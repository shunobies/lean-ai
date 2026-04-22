"""Multi-pass vision analysis of a UI screenshot.

Feeds a screenshot through a deterministic pipeline so a small local vision
model produces reliable structured output despite its size:

1. **Inventory pass** (``describe_image_structured`` with ``UIInventory``) —
   enumerates regions + components on a 3x3 grid.  Temperature pinned to 0.
2. **Text transcription pass** (``describe_image_structured`` with
   ``UITextTranscript``) — verbatim OCR-style extraction.  Done as a
   dedicated pass because label transcription from a holistic description
   is the #1 hallucination source for vision models.
3. **Color sampling** — pure Python via Pillow + NumPy k-means on a
   downsampled pixel array.  Never from the model; hex codes the model
   guesses are unreliable.
4. **Focused answer pass** (``describe_image`` with full system-prompt
   override) — synthesises the three prior sources into a direct answer
   to the caller's ``question``.

All four passes contribute to a single :class:`UIAnalysis` Pydantic
instance.  Failures in individual passes are captured as warnings rather
than aborting the whole flow, so the caller always gets a structured
result.

Optional dependencies (Pillow, numpy) are lazy-imported inside functions
so the module stays importable even when the ``ui-verification`` extras
group is not installed.  Call :func:`is_analysis_available` before using.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from lean_ai.config import settings
from lean_ai.llm.prompt_registry import registry
from lean_ai.llm.vision import describe_image, describe_image_structured

logger = logging.getLogger(__name__)

Confidence = Literal["high", "medium", "low"]


# ── Pydantic schemas ────────────────────────────────────────────────────


class UIRegion(BaseModel):
    """A distinct visual region of the UI (header, sidebar, footer, etc.)."""

    name: str
    grid_cell: str = ""  # e.g. "top-left", "center", or "top-left,top-center"
    notes: str = ""


class UIComponent(BaseModel):
    """A single UI component visible in the screenshot."""

    type: str  # button, input, link, card, nav, text, image, icon, checkbox, etc.
    location: str  # grid cell or region name
    label_text: str | None = None
    state: str = "visible"  # visible-only per prompt policy
    styling_notes: str = ""
    confidence: Confidence = "medium"


class UIInventory(BaseModel):
    regions: list[UIRegion] = Field(default_factory=list)
    components: list[UIComponent] = Field(default_factory=list)


class UITextLine(BaseModel):
    """A single line of visible text transcribed verbatim."""

    region: str  # which region this text belongs to
    verbatim: str  # exact text, or "[unreadable]"
    confidence: Confidence = "medium"


class UITextTranscript(BaseModel):
    lines: list[UITextLine] = Field(default_factory=list)


class UIAnalysis(BaseModel):
    """Merged result of all four analysis passes for a single screenshot."""

    inventory: UIInventory
    text: UITextTranscript
    colors: dict[str, Any] = Field(default_factory=dict)
    answer: str = ""
    warnings: list[str] = Field(default_factory=list)


# ── Availability check ─────────────────────────────────────────────────


def is_analysis_available() -> bool:
    """Check whether the analysis pipeline can run.

    Requires:
    - ``enable_ui_verification=True``
    - A configured vision model
    - Pillow and numpy importable (the ui-verification extras group)
    """
    if not settings.enable_ui_verification:
        return False
    if not settings.vision_model:
        return False
    try:
        import numpy  # noqa: F401
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def availability_reason() -> str | None:
    """Return a human-readable reason when analysis is unavailable, else None."""
    if not settings.enable_ui_verification:
        return "UI verification is disabled (enable_ui_verification=false)"
    if not settings.vision_model:
        return "No vision model configured (LEAN_AI_VISION_MODEL is empty)"
    try:
        import numpy  # noqa: F401
        import PIL  # noqa: F401
    except ImportError:
        return (
            "Pillow and numpy are not installed. "
            'Run `pip install "lean-ai[ui-verification]"`'
        )
    return None


# ── Helpers ─────────────────────────────────────────────────────────────


def _encode_image_base64(png_path: Path) -> str:
    """Read a PNG file and return its base64 encoding."""
    return base64.b64encode(png_path.read_bytes()).decode("ascii")


def sample_colors(png_path: Path, n_samples: int = 5) -> dict[str, Any]:
    """Extract a dominant-color palette from the screenshot.

    Uses Pillow + numpy k-means on a 100x100 downsampled copy.  Returns
    ``{"palette": [hex, ...], "background_guess": hex}``.  Palette is
    ranked by prevalence (most common first).  Background guess is the
    most frequent corner pixel (typically the dominant background color).
    """
    import numpy as np
    from PIL import Image

    img = Image.open(png_path).convert("RGB")

    # Background guess from corners (full-resolution so we don't lose the edge).
    w, h = img.size
    corners = [
        img.getpixel((0, 0)),
        img.getpixel((w - 1, 0)),
        img.getpixel((0, h - 1)),
        img.getpixel((w - 1, h - 1)),
    ]
    bg_rgb = Counter(corners).most_common(1)[0][0]
    background_hex = "#{:02X}{:02X}{:02X}".format(*bg_rgb)

    # Downsample for k-means speed.
    img.thumbnail((100, 100))
    pixels = np.array(img).reshape(-1, 3).astype(np.float32)

    k = max(1, min(n_samples, 8))

    # Deterministic init — reproducible output across runs on the same image.
    rng = np.random.default_rng(42)
    init_idx = rng.choice(len(pixels), k, replace=False)
    centroids = pixels[init_idx].copy()

    assignments = np.zeros(len(pixels), dtype=np.int32)
    for _ in range(10):
        distances = np.linalg.norm(
            pixels[:, np.newaxis, :] - centroids[np.newaxis, :, :], axis=2
        )
        assignments = np.argmin(distances, axis=1)
        new_centroids = centroids.copy()
        for i in range(k):
            mask = assignments == i
            if mask.any():
                new_centroids[i] = pixels[mask].mean(axis=0)
        if np.allclose(new_centroids, centroids, atol=1.0):
            break
        centroids = new_centroids

    counts = np.bincount(assignments, minlength=k)
    ranked = sorted(range(k), key=lambda i: -int(counts[i]))
    palette = [
        f"#{int(centroids[i][0]):02X}{int(centroids[i][1]):02X}{int(centroids[i][2]):02X}"
        for i in ranked
    ]

    return {"palette": palette, "background_guess": background_hex}


# ── Main entry point ────────────────────────────────────────────────────


async def analyze_screenshot(
    png_path: Path,
    question: str,
    *,
    vision_timeout: float | None = None,
) -> UIAnalysis:
    """Run the 4-pass analysis pipeline on a screenshot.

    Args:
        png_path: Absolute path to a PNG screenshot.
        question: The caller's focused question, used to constrain the
            final answer pass (e.g. "Does the login form have visible
            accessibility issues?").
        vision_timeout: Per-pass vision timeout override.  Defaults to
            ``settings.ui_verification_vision_timeout``.

    Returns:
        A ``UIAnalysis`` with the merged results.  Individual pass
        failures are collected into ``warnings`` rather than raised, so
        callers always get a structured result.
    """
    timeout = (
        vision_timeout
        if vision_timeout is not None
        else settings.ui_verification_vision_timeout
    )

    warnings: list[str] = []
    inventory = UIInventory()
    text = UITextTranscript()
    colors: dict[str, Any] = {}
    answer = ""

    # Gate on availability up-front so the caller sees a clean error.
    reason = availability_reason()
    if reason is not None:
        warnings.append(reason)
        return UIAnalysis(
            inventory=inventory, text=text, colors=colors,
            answer=f"Analysis unavailable: {reason}", warnings=warnings,
        )

    # Load image.
    try:
        img_b64 = _encode_image_base64(png_path)
    except Exception as e:
        warnings.append(f"image load failed: {e}")
        return UIAnalysis(
            inventory=inventory, text=text, colors=colors,
            answer=f"Failed to load screenshot at {png_path}: {e}",
            warnings=warnings,
        )

    # ── Pass 1 — inventory ────────────────────────────────────────────
    inv_result = await describe_image_structured(
        img_b64,
        UIInventory,
        system_prompt=registry.get("ui_verification.inventory_system"),
        user_prompt=registry.get("ui_verification.inventory_user"),
        timeout=timeout,
    )
    if inv_result.success and inv_result.parsed is not None:
        inventory = inv_result.parsed
    else:
        warnings.append(f"inventory pass failed: {inv_result.error}")
    warnings.extend(inv_result.warnings)

    # ── Pass 2 — text transcription ───────────────────────────────────
    text_result = await describe_image_structured(
        img_b64,
        UITextTranscript,
        system_prompt=registry.get("ui_verification.text_system"),
        user_prompt=registry.get("ui_verification.text_user"),
        timeout=timeout,
    )
    if text_result.success and text_result.parsed is not None:
        text = text_result.parsed
    else:
        warnings.append(f"text pass failed: {text_result.error}")
    warnings.extend(text_result.warnings)

    # ── Pass 3 — color sampling (pure Python, off the event loop) ─────
    try:
        colors = await asyncio.to_thread(
            sample_colors,
            png_path,
            settings.ui_verification_max_color_samples,
        )
    except ImportError:
        warnings.append(
            "color sampling skipped: install lean-ai[ui-verification] "
            "for Pillow and numpy"
        )
    except Exception as e:
        logger.exception("UI analysis: color sampling failed")
        warnings.append(f"color sampling failed: {e}")

    # ── Pass 4 — focused answer ──────────────────────────────────────
    answer_user = registry.format(
        "ui_verification.answer_user",
        question=question,
        inventory_json=inventory.model_dump_json(indent=2),
        text_json=text.model_dump_json(indent=2),
        colors=str(colors),
    )
    answer_result = await describe_image(
        img_b64,
        system_prompt=registry.get("ui_verification.answer_system"),
        user_prompt=answer_user,
        timeout=timeout,
    )
    if answer_result.success:
        answer = answer_result.description
    else:
        answer = f"(focused answer pass failed: {answer_result.error})"
        warnings.append(f"answer pass failed: {answer_result.error}")

    return UIAnalysis(
        inventory=inventory,
        text=text,
        colors=colors,
        answer=answer,
        warnings=warnings,
    )

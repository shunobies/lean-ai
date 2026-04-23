"""On-demand vision model for describing images.

Uses a dedicated Ollama vision-language model (e.g. qwen3-vl) to convert
base64-encoded images into rich text descriptions.  The descriptions are
injected into the user's message so text-only models can understand visual
content.

Two entry points:

- :func:`describe_image` — free-form prose description.  Used by the chat
  endpoint to inject image context into the user message.
- :func:`describe_image_structured` — JSON-schema-constrained extraction.
  Used by the UI verification pipeline to pull layout, components, and text
  into Pydantic models.  Temperature is pinned to 0 and Ollama's ``format``
  parameter enforces the schema so small vision models don't drift.

Disabled when ``LEAN_AI_VISION_MODEL`` is empty (the default).
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Generic, TypeVar

import ollama as ollama_lib
from pydantic import BaseModel, ValidationError

from lean_ai.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_VISION_SYSTEM_PROMPT = """\
Describe this image in detail for a software developer who cannot see it.

Focus on:
- UI layout, component structure, visual hierarchy, and styling
- All visible text content, labels, headings, and button text
- Error messages, warnings, notifications — capture the exact text
- Code snippets or terminal output — transcribe the visible text verbatim
- Colors, icons, spacing, and visual state (disabled, selected, hover, etc.)
- If this is a UI mockup or wireframe, describe the layout and components
- If this is an architecture diagram, describe nodes, connections, and labels

Be thorough but concise. Prioritise actionable information.\
"""


@dataclass
class VisionResult:
    """Result of a single image description."""

    success: bool
    description: str = ""
    error: str | None = None


def is_vision_available() -> bool:
    """Check if a vision model is configured."""
    return bool(settings.vision_model)


async def describe_image(
    image_base64: str,
    *,
    prompt: str | None = None,
    filename: str | None = None,
    system_prompt: str | None = None,
    user_prompt: str | None = None,
    timeout: float | None = None,
    max_tokens: int | None = None,
) -> VisionResult:
    """Describe a single base64-encoded image using the vision model.

    Args:
        image_base64: Base64-encoded image data (no ``data:`` URL prefix).
        prompt: Optional context appended to the default system prompt (free-form flow).
        filename: Optional original filename for context.
        system_prompt: Full system prompt override.  When provided, replaces the
            default prompt entirely (used by the UI verification focused-answer
            pass, which builds its own capability-first prompt).
        user_prompt: Full user message override.  When provided, replaces the
            default "Describe this image." message.
        timeout: Override ``settings.vision_timeout``.
        max_tokens: Override ``settings.vision_max_tokens``.

    Returns:
        VisionResult with the text description or error.
    """
    if not settings.vision_model:
        return VisionResult(success=False, error="No vision model configured")

    if system_prompt is None:
        effective_system = _VISION_SYSTEM_PROMPT
        if prompt:
            effective_system += f"\n\nAdditional context from the user: {prompt}"
    else:
        effective_system = system_prompt

    if user_prompt is not None:
        user_content = user_prompt
    else:
        user_content = "Describe this image."
        if filename:
            user_content = f"Describe this image (filename: {filename})."

    effective_timeout = timeout if timeout is not None else settings.vision_timeout
    effective_max_tokens = max_tokens if max_tokens is not None else settings.vision_max_tokens

    messages = [
        {"role": "system", "content": effective_system},
        {"role": "user", "content": user_content, "images": [image_base64]},
    ]

    client = ollama_lib.AsyncClient(host=settings.effective_vision_url)

    async def _call_vision() -> str:
        resp = await asyncio.wait_for(
            client.chat(
                model=settings.vision_model,
                messages=messages,
                options={
                    "num_predict": effective_max_tokens,
                    "temperature": 0.3,
                },
            ),
            timeout=effective_timeout,
        )
        return resp["message"]["content"]

    try:
        text = await _call_vision()

        # Empty response (common on cold starts) — retry once.
        if not text.strip():
            logger.warning("Vision: empty response, retrying once")
            text = await _call_vision()
            if not text.strip():
                logger.warning("Vision: still empty after retry")
                return VisionResult(
                    success=False,
                    error="Vision model returned empty description",
                )

        logger.info(
            "Vision: described image (%d chars) using %s",
            len(text),
            settings.vision_model,
        )
        return VisionResult(success=True, description=text)

    except asyncio.TimeoutError:
        logger.warning("Vision: timeout after %.0fs", effective_timeout)
        return VisionResult(
            success=False,
            error=f"Vision model timed out after {effective_timeout:.0f}s",
        )
    except ConnectionError:
        logger.warning(
            "Vision: cannot reach Ollama at %s",
            settings.effective_vision_url,
        )
        return VisionResult(
            success=False,
            error=f"Cannot reach Ollama at {settings.effective_vision_url}",
        )
    except Exception as e:
        logger.exception("Vision: description failed")
        return VisionResult(success=False, error=str(e))


async def describe_images(
    images: list[dict],
    *,
    prompt: str | None = None,
) -> list[VisionResult]:
    """Describe multiple images in parallel.

    Args:
        images: List of dicts with ``data`` (base64 string) and optional
                ``filename`` keys.
        prompt: Optional context prompt applied to all images.

    Returns:
        List of VisionResult, one per input image, in the same order.
    """
    if not images or not settings.vision_model:
        return []

    tasks = [
        describe_image(
            img["data"],
            prompt=prompt,
            filename=img.get("filename"),
        )
        for img in images
    ]
    return list(await asyncio.gather(*tasks))


@dataclass
class StructuredVisionResult(Generic[T]):
    """Result of a single structured (schema-constrained) image analysis."""

    success: bool
    parsed: T | None = None
    raw: str = ""
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


async def describe_image_structured(
    image_base64: str,
    schema: type[T],
    *,
    system_prompt: str,
    user_prompt: str,
    timeout: float | None = None,
    max_tokens: int | None = None,
) -> StructuredVisionResult[T]:
    """Run a schema-constrained pass over a single image.

    Uses Ollama's ``format`` parameter to enforce the Pydantic model's JSON
    schema on the output.  Temperature is pinned to 0 for deterministic
    extraction.  Retries once on empty/invalid responses (common on small
    vision models after a cold load).

    Args:
        image_base64: Base64-encoded image data (no ``data:`` URL prefix).
        schema: Pydantic model class that defines the extraction schema.
        system_prompt: Full system prompt — caller controls framing.
            Use capability-first language; do NOT assign the model a persona.
        user_prompt: User message accompanying the image.
        timeout: Override ``settings.vision_timeout``.  Pass the UI
            verification vision timeout for long structured passes.
        max_tokens: Override ``settings.vision_max_tokens``.

    Returns:
        StructuredVisionResult with the parsed Pydantic instance, or an error.
        ``raw`` holds the model's raw JSON text for debugging.
    """
    if not settings.vision_model:
        return StructuredVisionResult(success=False, error="No vision model configured")

    effective_timeout = timeout if timeout is not None else settings.vision_timeout
    effective_max_tokens = max_tokens if max_tokens is not None else settings.vision_max_tokens

    # Inline the schema into the system prompt alongside Ollama's format=
    # constraint.  format= enforces valid JSON at decode time, but the model
    # never sees the schema shape without this — small vision models then
    # produce technically-valid JSON with the nested types flattened or
    # fields mis-filled.  Gives the model structural context + safety.
    schema_dict = schema.model_json_schema()
    schema_json_text = json.dumps(schema_dict, indent=2)
    system_with_schema = (
        f"{system_prompt}\n\n"
        "Respond with a JSON object that matches this schema exactly. "
        "Populate every required field; use empty lists / empty strings for "
        "optional fields you cannot determine.\n\n"
        "```json\n"
        f"{schema_json_text}\n"
        "```"
    )

    messages = [
        {"role": "system", "content": system_with_schema},
        {"role": "user", "content": user_prompt, "images": [image_base64]},
    ]

    client = ollama_lib.AsyncClient(host=settings.effective_vision_url)

    async def _call() -> str:
        resp = await asyncio.wait_for(
            client.chat(
                model=settings.vision_model,
                messages=messages,
                format=schema_dict,
                options={
                    "num_predict": effective_max_tokens,
                    "temperature": 0.0,
                },
            ),
            timeout=effective_timeout,
        )
        return resp["message"]["content"]

    def _try_parse(text: str) -> tuple[T | None, str | None]:
        """Return (parsed, error). Error is None on success."""
        if not text.strip():
            return None, "empty response"
        try:
            return schema.model_validate_json(text), None
        except ValidationError as e:
            return None, f"schema validation failed: {e.errors()[0].get('msg', 'unknown')}"
        except json.JSONDecodeError as e:
            return None, f"invalid JSON: {e.msg}"

    warnings: list[str] = []
    try:
        raw = await _call()
        parsed, err = _try_parse(raw)
        if parsed is None:
            warnings.append(f"first attempt failed: {err}, retrying")
            logger.warning("Vision (structured): %s, retrying once", err)
            raw = await _call()
            parsed, err = _try_parse(raw)
            if parsed is None:
                return StructuredVisionResult(
                    success=False,
                    raw=raw,
                    error=err,
                    warnings=warnings,
                )

        logger.info(
            "Vision (structured): extracted %s using %s",
            schema.__name__,
            settings.vision_model,
        )
        return StructuredVisionResult(
            success=True,
            parsed=parsed,
            raw=raw,
            warnings=warnings,
        )

    except asyncio.TimeoutError:
        logger.warning("Vision (structured): timeout after %.0fs", effective_timeout)
        return StructuredVisionResult(
            success=False,
            error=f"Vision model timed out after {effective_timeout:.0f}s",
            warnings=warnings,
        )
    except ConnectionError:
        logger.warning(
            "Vision (structured): cannot reach Ollama at %s",
            settings.effective_vision_url,
        )
        return StructuredVisionResult(
            success=False,
            error=f"Cannot reach Ollama at {settings.effective_vision_url}",
            warnings=warnings,
        )
    except Exception as e:
        logger.exception("Vision (structured): extraction failed")
        return StructuredVisionResult(success=False, error=str(e), warnings=warnings)


def format_image_descriptions(results: list[VisionResult]) -> str:
    """Format vision results into a text block for injection into the user message.

    Returns empty string if no successful descriptions.
    """
    parts: list[str] = []
    for i, result in enumerate(results, 1):
        label = f"Image {i}" if len(results) > 1 else "Attached Image"
        if result.success:
            parts.append(
                f"[{label} Description]\n"
                f"{result.description}\n"
                f"[End {label} Description]"
            )
        else:
            parts.append(f"[{label}: could not be described — {result.error}]")

    return "\n\n".join(parts)

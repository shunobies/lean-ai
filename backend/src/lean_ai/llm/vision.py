"""On-demand vision model for describing images.

Uses a dedicated Ollama vision-language model (e.g. qwen3-vl) to convert
base64-encoded images into rich text descriptions.  The descriptions are
injected into the user's message so text-only models can understand visual
content.

Disabled when ``LEAN_AI_VISION_MODEL`` is empty (the default).
"""

import asyncio
import logging
from dataclasses import dataclass

import ollama as ollama_lib

from lean_ai.config import settings

logger = logging.getLogger(__name__)

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
) -> VisionResult:
    """Describe a single base64-encoded image using the vision model.

    Args:
        image_base64: Base64-encoded image data (no ``data:`` URL prefix).
        prompt: Optional context from the user's message for focused description.
        filename: Optional original filename for context.

    Returns:
        VisionResult with the text description or error.
    """
    if not settings.vision_model:
        return VisionResult(success=False, error="No vision model configured")

    system_prompt = _VISION_SYSTEM_PROMPT
    if prompt:
        system_prompt += f"\n\nAdditional context from the user: {prompt}"

    user_content = "Describe this image."
    if filename:
        user_content = f"Describe this image (filename: {filename})."

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content, "images": [image_base64]},
    ]

    client = ollama_lib.AsyncClient(host=settings.effective_vision_url)

    try:
        response = await asyncio.wait_for(
            client.chat(
                model=settings.vision_model,
                messages=messages,
                options={
                    "num_predict": settings.vision_max_tokens,
                    "temperature": 0.3,
                },
            ),
            timeout=settings.vision_timeout,
        )
        text = response["message"]["content"]
        logger.info(
            "Vision: described image (%d chars) using %s",
            len(text),
            settings.vision_model,
        )
        return VisionResult(success=True, description=text)

    except asyncio.TimeoutError:
        logger.warning("Vision: timeout after %.0fs", settings.vision_timeout)
        return VisionResult(success=False, error="Vision model timed out")
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

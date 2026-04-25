"""Per-provider image / audio content block attachment.

When a role is flagged ``supports_image`` or ``supports_audio`` and the user
sends media, dispatch attaches the media directly to the chat call's messages
in that provider's native shape — no round-trip through a separate vision
model, no prose-describer, no model swap on VRAM-constrained hosts.

Each provider's SDK expects a different message shape for media:

- **Ollama** — top-level ``"images": [base64, ...]`` field on the message
  dict.  No audio support in common Ollama models — raises ``CapabilityError``.
- **OpenAI / Lean AI Serve** — content array with ``image_url`` /
  ``input_audio`` blocks.  Both API-compatible.
- **Anthropic** — content array with ``image`` / ``source`` blocks.  No
  audio input support — raises ``CapabilityError``.
- **Gemini** — generic content array with ``{"type": "image" | "audio",
  "data": b64, "mime_type": ...}``.  The Gemini provider's ``_build_contents``
  recognises these and emits ``Part.from_bytes`` internally.

Entry points (both return a deep-enough copy of ``messages`` — callers may
mutate freely):

    attach_image(messages, image_b64, mime_type, *, provider) -> list[dict]
    attach_audio(messages, audio_b64, mime_type, *, provider) -> list[dict]

Raising ``CapabilityError`` signals dispatch to fall back (``vision_model``
for images, faster-whisper for audio).  Callers catch it and pivot.
"""

from __future__ import annotations

from typing import Any

from lean_ai.llm.base import CapabilityError

__all__ = ["CapabilityError", "attach_audio", "attach_image"]


_IMAGE_PROVIDERS = frozenset({"ollama", "openai", "serve", "anthropic", "gemini"})
_AUDIO_PROVIDERS = frozenset({"openai", "serve", "gemini"})


def _deep_copy_messages(messages: list[dict]) -> list[dict]:
    """Return a copy of ``messages`` where the last user message and its
    content list are new containers.  Callers may mutate freely."""
    out: list[dict] = []
    last_user_idx = _last_user_index(messages)
    for i, msg in enumerate(messages):
        copy = dict(msg)
        if i == last_user_idx:
            content = copy.get("content")
            if isinstance(content, list):
                copy["content"] = [dict(b) if isinstance(b, dict) else b for b in content]
        out.append(copy)
    return out


def _last_user_index(messages: list[dict]) -> int:
    """Return the index of the last user message, or -1 if none."""
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            return i
    return -1


def _ensure_content_list(msg: dict) -> list[dict]:
    """Normalize the message's ``content`` to a list of content blocks.

    If ``content`` is a non-empty string, wraps it in a single text block.
    If it's already a list, returns it in place.  Sets ``msg['content']``
    to the list so mutations stick.
    """
    content = msg.get("content", "")
    if isinstance(content, list):
        return content
    if isinstance(content, str) and content:
        new: list[dict] = [{"type": "text", "text": content}]
    else:
        new = []
    msg["content"] = new
    return new


def _openai_audio_format(mime_type: str) -> str:
    """Map a mime type to OpenAI's ``input_audio.format`` enum."""
    m = mime_type.lower()
    if "wav" in m or "x-wav" in m or "wave" in m:
        return "wav"
    if "mp3" in m or "mpeg" in m:
        return "mp3"
    # OpenAI currently accepts "wav" or "mp3"; default to wav for uncompressed PCM.
    return "wav"


# ── Image ──────────────────────────────────────────────────────────────


def attach_image(
    messages: list[dict],
    image_b64: str,
    mime_type: str,
    *,
    provider: str,
) -> list[dict]:
    """Return a copy of ``messages`` with ``image_b64`` attached to the
    last user message in ``provider``'s native shape.

    Args:
        messages: Chat message list.  Not mutated.
        image_b64: Image bytes, base64-encoded (no data-URL prefix).
        mime_type: e.g. ``"image/png"``, ``"image/jpeg"``.
        provider: One of ``"ollama"``, ``"openai"``, ``"serve"``,
            ``"anthropic"``, ``"gemini"``.

    Raises:
        CapabilityError: Provider is unknown or doesn't support image input.
    """
    if provider not in _IMAGE_PROVIDERS:
        raise CapabilityError(f"Provider {provider!r} does not support image input")

    new_messages = _deep_copy_messages(messages)
    idx = _last_user_index(new_messages)
    if idx < 0:
        # No user message yet — synthesize one.
        new_messages.append({"role": "user", "content": ""})
        idx = len(new_messages) - 1
    msg = new_messages[idx]

    if provider == "ollama":
        images = msg.setdefault("images", [])
        if not isinstance(images, list):
            images = [images]
            msg["images"] = images
        images.append(image_b64)
        return new_messages

    content = _ensure_content_list(msg)
    if provider in ("openai", "serve"):
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{image_b64}"},
            }
        )
    elif provider == "anthropic":
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime_type,
                    "data": image_b64,
                },
            }
        )
    elif provider == "gemini":
        # Generic block; Gemini's _build_contents translates to Part.from_bytes.
        content.append(
            {
                "type": "image",
                "data": image_b64,
                "mime_type": mime_type,
            }
        )
    return new_messages


# ── Audio ──────────────────────────────────────────────────────────────


def attach_audio(
    messages: list[dict],
    audio_b64: str,
    mime_type: str,
    *,
    provider: str,
) -> list[dict]:
    """Return a copy of ``messages`` with ``audio_b64`` attached to the
    last user message.

    Args:
        messages: Chat message list.  Not mutated.
        audio_b64: Audio bytes, base64-encoded.
        mime_type: e.g. ``"audio/wav"``, ``"audio/mpeg"``.
        provider: One of ``"openai"``, ``"serve"``, ``"gemini"``.

    Raises:
        CapabilityError: Provider doesn't support audio input (covers Ollama,
            Anthropic, and any unknown provider).
    """
    if provider not in _AUDIO_PROVIDERS:
        reason = {
            "ollama": "Common Ollama models do not accept audio input",
            "anthropic": "Anthropic does not accept audio input yet",
        }.get(provider, f"Provider {provider!r} does not support audio input")
        raise CapabilityError(reason)

    new_messages = _deep_copy_messages(messages)
    idx = _last_user_index(new_messages)
    if idx < 0:
        new_messages.append({"role": "user", "content": ""})
        idx = len(new_messages) - 1
    msg = new_messages[idx]

    content = _ensure_content_list(msg)
    if provider in ("openai", "serve"):
        content.append(
            {
                "type": "input_audio",
                "input_audio": {
                    "data": audio_b64,
                    "format": _openai_audio_format(mime_type),
                },
            }
        )
    elif provider == "gemini":
        content.append(
            {
                "type": "audio",
                "data": audio_b64,
                "mime_type": mime_type,
            }
        )
    return new_messages


# Re-export the typing constant so callers can import from one place.
SUPPORTED_IMAGE_PROVIDERS: tuple[str, ...] = tuple(sorted(_IMAGE_PROVIDERS))
SUPPORTED_AUDIO_PROVIDERS: tuple[str, ...] = tuple(sorted(_AUDIO_PROVIDERS))


_UNUSED: Any = None  # keep typing import referenced

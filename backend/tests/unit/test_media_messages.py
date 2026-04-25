"""Tests for llm/media_messages.py per-provider content-block builders."""

from __future__ import annotations

import pytest

from lean_ai.llm.media_messages import (
    SUPPORTED_AUDIO_PROVIDERS,
    SUPPORTED_IMAGE_PROVIDERS,
    CapabilityError,
    attach_audio,
    attach_image,
)


def _text_only_messages() -> list[dict]:
    return [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Describe this."},
    ]


# ── Image: per-provider shape ──────────────────────────────────────────


def test_attach_image_ollama_uses_top_level_images_field():
    msgs = _text_only_messages()
    out = attach_image(msgs, "AAAA", "image/png", provider="ollama")
    assert out[-1]["images"] == ["AAAA"]
    # Content is untouched (Ollama doesn't use content blocks for images)
    assert out[-1]["content"] == "Describe this."


def test_attach_image_openai_builds_image_url_block():
    msgs = _text_only_messages()
    out = attach_image(msgs, "AAAA", "image/png", provider="openai")
    content = out[-1]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "Describe this."}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == "data:image/png;base64,AAAA"


def test_attach_image_serve_uses_openai_shape():
    """Serve is OpenAI-compatible — same content-block format."""
    msgs = _text_only_messages()
    out = attach_image(msgs, "AAAA", "image/jpeg", provider="serve")
    content = out[-1]["content"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,AAAA"


def test_attach_image_anthropic_builds_source_base64_block():
    msgs = _text_only_messages()
    out = attach_image(msgs, "AAAA", "image/png", provider="anthropic")
    content = out[-1]["content"]
    assert content[1] == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": "AAAA",
        },
    }


def test_attach_image_gemini_uses_generic_type_keyed_block():
    """Gemini's _build_contents translates these to Part.from_bytes."""
    msgs = _text_only_messages()
    out = attach_image(msgs, "AAAA", "image/webp", provider="gemini")
    content = out[-1]["content"]
    assert content[1] == {
        "type": "image",
        "data": "AAAA",
        "mime_type": "image/webp",
    }


def test_attach_image_unknown_provider_raises():
    with pytest.raises(CapabilityError, match="does not support image"):
        attach_image(_text_only_messages(), "AAAA", "image/png", provider="zzz")


# ── Audio: per-provider shape + refusal ────────────────────────────────


def test_attach_audio_openai_infers_wav_format():
    msgs = _text_only_messages()
    out = attach_audio(msgs, "ZZZ", "audio/wav", provider="openai")
    content = out[-1]["content"]
    assert content[1] == {
        "type": "input_audio",
        "input_audio": {"data": "ZZZ", "format": "wav"},
    }


def test_attach_audio_openai_infers_mp3_format():
    out = attach_audio(_text_only_messages(), "ZZZ", "audio/mpeg", provider="openai")
    assert out[-1]["content"][1]["input_audio"]["format"] == "mp3"


def test_attach_audio_serve_uses_openai_shape():
    out = attach_audio(_text_only_messages(), "ZZZ", "audio/wav", provider="serve")
    assert out[-1]["content"][1]["type"] == "input_audio"


def test_attach_audio_gemini_uses_generic_shape():
    out = attach_audio(_text_only_messages(), "ZZZ", "audio/wav", provider="gemini")
    assert out[-1]["content"][1] == {
        "type": "audio",
        "data": "ZZZ",
        "mime_type": "audio/wav",
    }


def test_attach_audio_ollama_raises_capability_error():
    with pytest.raises(CapabilityError, match="Ollama"):
        attach_audio(_text_only_messages(), "ZZZ", "audio/wav", provider="ollama")


def test_attach_audio_anthropic_raises_capability_error():
    with pytest.raises(CapabilityError, match="Anthropic"):
        attach_audio(_text_only_messages(), "ZZZ", "audio/wav", provider="anthropic")


def test_attach_audio_unknown_provider_raises():
    with pytest.raises(CapabilityError):
        attach_audio(_text_only_messages(), "ZZZ", "audio/wav", provider="zzz")


# ── Immutability + edge cases ──────────────────────────────────────────


def test_attach_image_does_not_mutate_input_messages():
    original = [{"role": "user", "content": "hi"}]
    attach_image(original, "AAAA", "image/png", provider="openai")
    assert original == [{"role": "user", "content": "hi"}]


def test_attach_image_does_not_mutate_input_ollama():
    original = [{"role": "user", "content": "hi"}]
    attach_image(original, "AAAA", "image/png", provider="ollama")
    assert "images" not in original[0]
    assert original[0]["content"] == "hi"


def test_attach_image_appends_user_message_when_none_exists():
    """An empty list or assistant-only history gets a synthesised user message."""
    out = attach_image([], "AAAA", "image/png", provider="openai")
    assert len(out) == 1
    assert out[0]["role"] == "user"
    assert out[0]["content"][0] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,AAAA"},
    }


def test_attach_image_targets_last_user_when_assistant_follows():
    """Images should attach to the user message, not an assistant response."""
    msgs = [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "second question"},
    ]
    out = attach_image(msgs, "AAAA", "image/png", provider="openai")
    # The second (last) user message should carry the image
    assert isinstance(out[2]["content"], list)
    assert out[2]["content"][0]["text"] == "second question"
    assert out[2]["content"][1]["type"] == "image_url"
    # The first user message is unchanged
    assert out[0]["content"] == "first question"


def test_supported_provider_constants_are_exported():
    assert "ollama" in SUPPORTED_IMAGE_PROVIDERS
    assert "anthropic" in SUPPORTED_IMAGE_PROVIDERS
    # Ollama and Anthropic are NOT in audio
    assert "ollama" not in SUPPORTED_AUDIO_PROVIDERS
    assert "anthropic" not in SUPPORTED_AUDIO_PROVIDERS
    assert "openai" in SUPPORTED_AUDIO_PROVIDERS
    assert "gemini" in SUPPORTED_AUDIO_PROVIDERS


def test_multiple_images_accumulate_in_order():
    msgs = _text_only_messages()
    out = attach_image(msgs, "AAA", "image/png", provider="ollama")
    out = attach_image(out, "BBB", "image/png", provider="ollama")
    assert out[-1]["images"] == ["AAA", "BBB"]


def test_empty_content_string_produces_image_only_block_list():
    """A blank user content shouldn't leave a stray empty text block."""
    msgs = [{"role": "user", "content": ""}]
    out = attach_image(msgs, "AAAA", "image/png", provider="openai")
    assert out[0]["content"] == [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]

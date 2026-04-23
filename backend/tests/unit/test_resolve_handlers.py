"""Tests for routers/dependencies.py resolve_image_handler / resolve_audio_handler."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lean_ai.config import settings


def _reset_flags():
    for role in ("primary", "expert", "request", "worker", "inline"):
        setattr(settings, f"supports_image_{role}", False)
        setattr(settings, f"supports_audio_{role}", False)


@pytest.fixture(autouse=True)
def _isolate_settings():
    """Reset all capability flags + vision_model between tests."""
    saved = {
        f"supports_{kind}_{role}": getattr(settings, f"supports_{kind}_{role}")
        for kind in ("image", "audio")
        for role in ("primary", "expert", "request", "worker", "inline")
    }
    saved["vision_model"] = settings.vision_model
    _reset_flags()
    settings.vision_model = ""
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(settings, k, v)


# ── resolve_image_handler ──────────────────────────────────────────────


def test_image_handler_none_when_nothing_configured():
    from lean_ai.routers.dependencies import resolve_image_handler

    assert resolve_image_handler("chat") is None
    assert resolve_image_handler("workflow") is None


def test_image_handler_describe_when_only_vision_model_set():
    from lean_ai.routers.dependencies import resolve_image_handler

    settings.vision_model = "qwen3-vl:8b"
    mode_chat, _client = resolve_image_handler("chat")
    mode_wf, _client = resolve_image_handler("workflow")
    assert mode_chat == "describe"
    assert mode_wf == "describe"


def test_image_handler_inline_on_primary_flag_chat():
    from lean_ai.routers.dependencies import llm_client, resolve_image_handler

    settings.supports_image_primary = True
    result = resolve_image_handler("chat")
    assert result == ("inline", llm_client)


def test_image_handler_inline_on_primary_flag_workflow():
    from lean_ai.routers.dependencies import llm_client, resolve_image_handler

    settings.supports_image_primary = True
    result = resolve_image_handler("workflow")
    assert result == ("inline", llm_client)


def test_image_handler_workflow_ignores_request_flag():
    """workflow flow only considers primary — request images don't reach planning."""
    from lean_ai.routers.dependencies import resolve_image_handler

    settings.supports_image_request = True
    settings.vision_model = ""
    # request flag alone, workflow mode → no inline; no vision_model → None
    assert resolve_image_handler("workflow") is None


def test_image_handler_prefers_request_over_primary_for_chat():
    """When both flagged, chat uses request (matches _get_chat_client logic)."""
    from lean_ai.routers.dependencies import (
        llm_client,
        request_llm_client,
        resolve_image_handler,
    )

    if request_llm_client is None:
        pytest.skip("request_llm_client not configured in this test env")

    settings.supports_image_primary = True
    settings.supports_image_request = True
    mode, client = resolve_image_handler("chat")
    assert mode == "inline"
    # request wins over primary
    assert client is request_llm_client
    assert client is not llm_client


def test_image_handler_inline_preempts_vision_model():
    """If a role is flagged, it wins even when vision_model is set."""
    from lean_ai.routers.dependencies import resolve_image_handler

    settings.supports_image_primary = True
    settings.vision_model = "qwen3-vl:8b"
    mode, _ = resolve_image_handler("chat")
    assert mode == "inline"  # NOT describe


# ── resolve_audio_handler ──────────────────────────────────────────────


def test_audio_handler_none_when_nothing_configured():
    from lean_ai.routers.dependencies import resolve_audio_handler

    assert resolve_audio_handler() is None


def test_audio_handler_primary_wins():
    from lean_ai.routers.dependencies import llm_client, resolve_audio_handler

    settings.supports_audio_primary = True
    assert resolve_audio_handler() is llm_client


def test_audio_handler_priority_primary_over_request():
    """Priority is primary → request → worker → expert → inline."""
    from lean_ai.routers.dependencies import (
        llm_client,
        request_llm_client,
        resolve_audio_handler,
    )

    if request_llm_client is None:
        pytest.skip("request_llm_client not configured")

    settings.supports_audio_primary = True
    settings.supports_audio_request = True
    assert resolve_audio_handler() is llm_client  # primary first


def test_audio_handler_request_when_primary_unflagged():
    from lean_ai.routers.dependencies import request_llm_client, resolve_audio_handler

    if request_llm_client is None:
        pytest.skip("request_llm_client not configured")

    settings.supports_audio_request = True
    assert resolve_audio_handler() is request_llm_client


def test_audio_handler_returns_none_if_flagged_client_not_configured():
    """A flagged role whose client wasn't created returns None → Whisper."""
    from lean_ai.routers.dependencies import resolve_audio_handler

    # Flag a role that isn't configured by patching its client to None.
    with patch("lean_ai.routers.dependencies.expert_llm_client", None):
        settings.supports_audio_expert = True
        assert resolve_audio_handler() is None

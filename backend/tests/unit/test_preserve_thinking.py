"""Tests for preserve_thinking flow — facade retention + trim helper."""

from __future__ import annotations

from lean_ai.llm.facade import _trim_old_thinking

# ── _trim_old_thinking sliding window ─────────────────────────────────


def test_trim_keeps_last_3_assistant_thinking_blocks():
    msgs = [
        {"role": "assistant", "content": "a1", "thinking": "t1"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a2", "thinking": "t2"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a3", "thinking": "t3"},
        {"role": "assistant", "content": "a4", "thinking": "t4"},
        {"role": "assistant", "content": "a5", "thinking": "t5"},
    ]
    _trim_old_thinking(msgs, keep_recent=3)

    # First two assistant turns lose thinking, last three keep it
    assert "thinking" not in msgs[0]
    assert "thinking" not in msgs[2]
    assert msgs[4]["thinking"] == "t3"
    assert msgs[5]["thinking"] == "t4"
    assert msgs[6]["thinking"] == "t5"


def test_trim_keep_zero_strips_all_thinking():
    msgs = [
        {"role": "assistant", "content": "a1", "thinking": "t1"},
        {"role": "assistant", "content": "a2", "thinking": "t2"},
    ]
    _trim_old_thinking(msgs, keep_recent=0)
    for m in msgs:
        assert "thinking" not in m


def test_trim_no_op_when_no_assistant_thinking():
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},  # no thinking key
    ]
    before = [dict(m) for m in msgs]
    _trim_old_thinking(msgs, keep_recent=3)
    assert msgs == before


def test_trim_ignores_user_messages_even_with_thinking_field():
    """Trim should only touch assistant messages."""
    msgs = [
        # Malformed: user with a thinking key (shouldn't happen but be robust)
        {"role": "user", "content": "u", "thinking": "weird"},
        {"role": "assistant", "content": "a1", "thinking": "t1"},
        {"role": "assistant", "content": "a2", "thinking": "t2"},
    ]
    _trim_old_thinking(msgs, keep_recent=1)
    # User's "thinking" field (invalid shape) left alone
    assert msgs[0]["thinking"] == "weird"
    # Only the most recent assistant keeps thinking
    assert "thinking" not in msgs[1]
    assert msgs[2]["thinking"] == "t2"


def test_trim_keep_recent_larger_than_count_keeps_everything():
    msgs = [
        {"role": "assistant", "content": "a1", "thinking": "t1"},
        {"role": "assistant", "content": "a2", "thinking": "t2"},
    ]
    _trim_old_thinking(msgs, keep_recent=5)
    # Both kept — fewer than keep_recent exist
    assert msgs[0]["thinking"] == "t1"
    assert msgs[1]["thinking"] == "t2"


# ── OpenAIProvider._extra_body for Serve preserve_thinking ─────────────
#
# Guarded because openai SDK isn't always installed.


def _openai_provider_or_skip(**kwargs):
    """Instantiate OpenAIProvider; skip the test when the openai SDK isn't
    installed (the module imports lazily inside __init__)."""
    import pytest

    from lean_ai.llm.provider_openai import OpenAIProvider
    try:
        return OpenAIProvider(**kwargs)
    except ModuleNotFoundError:
        pytest.skip("openai SDK not installed")


def test_openai_extra_body_empty_when_off():
    p = _openai_provider_or_skip(api_key="dummy", preserve_thinking=False)
    assert p._extra_body() == {}


def test_openai_extra_body_populated_when_on():
    p = _openai_provider_or_skip(api_key="dummy", preserve_thinking=True)
    assert p._extra_body() == {
        "extra_body": {"chat_template_kwargs": {"preserve_thinking": True}},
    }


# ── Config fallback semantics ──────────────────────────────────────────


def test_effective_expert_min_p_falls_back_to_primary():
    from lean_ai.config import settings

    saved_primary = settings.ollama_min_p
    saved_expert = settings.ollama_expert_min_p
    try:
        settings.ollama_min_p = 0.05
        settings.ollama_expert_min_p = None
        assert settings.effective_expert_min_p == 0.05

        settings.ollama_expert_min_p = 0.1
        assert settings.effective_expert_min_p == 0.1
    finally:
        settings.ollama_min_p = saved_primary
        settings.ollama_expert_min_p = saved_expert


def test_effective_expert_presence_penalty_falls_back_to_primary():
    from lean_ai.config import settings

    saved_primary = settings.ollama_presence_penalty
    saved_expert = settings.ollama_expert_presence_penalty
    try:
        settings.ollama_presence_penalty = 1.5
        settings.ollama_expert_presence_penalty = None
        assert settings.effective_expert_presence_penalty == 1.5

        settings.ollama_expert_presence_penalty = 1.0
        assert settings.effective_expert_presence_penalty == 1.0
    finally:
        settings.ollama_presence_penalty = saved_primary
        settings.ollama_expert_presence_penalty = saved_expert


def test_effective_min_p_returns_none_when_neither_set():
    from lean_ai.config import settings

    saved_primary = settings.ollama_min_p
    saved_expert = settings.ollama_expert_min_p
    try:
        settings.ollama_min_p = None
        settings.ollama_expert_min_p = None
        assert settings.effective_expert_min_p is None
    finally:
        settings.ollama_min_p = saved_primary
        settings.ollama_expert_min_p = saved_expert

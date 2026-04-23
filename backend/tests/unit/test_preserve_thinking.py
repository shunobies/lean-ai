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


# ── Folded <think> blocks (Ollama path) ──────────────────────────────


def test_trim_strips_folded_think_blocks_from_old_turns():
    """Ollama-style folded thinking (<think>...</think> at start of content)
    should also be trimmed from older assistant turns."""
    msgs = [
        {"role": "assistant", "content": "<think>\nt1\n</think>\n\na1"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "<think>\nt2\n</think>\n\na2"},
        {"role": "assistant", "content": "<think>\nt3\n</think>\n\na3"},
        {"role": "assistant", "content": "<think>\nt4\n</think>\n\na4"},
        {"role": "assistant", "content": "<think>\nt5\n</think>\n\na5"},
    ]
    _trim_old_thinking(msgs, keep_recent=3)
    # Older two have their think blocks stripped — only the raw content left
    assert msgs[0]["content"] == "a1"
    assert msgs[2]["content"] == "a2"
    # Most recent three keep theirs
    assert msgs[3]["content"].startswith("<think>")
    assert msgs[4]["content"].startswith("<think>")
    assert msgs[5]["content"].startswith("<think>")


def test_trim_mixed_folded_and_field_delivery():
    """Mixed fleets — some turns use the separate `thinking` field, others
    have folded `<think>` blocks.  Trim handles both uniformly."""
    msgs = [
        {"role": "assistant", "content": "a1", "thinking": "t1"},
        {"role": "assistant", "content": "<think>\nt2\n</think>\n\na2"},
        {"role": "assistant", "content": "a3", "thinking": "t3"},
    ]
    _trim_old_thinking(msgs, keep_recent=1)

    # Only the most recent turn keeps thinking
    assert "thinking" not in msgs[0]
    assert msgs[1]["content"] == "a2"  # folded block gone
    assert msgs[2]["thinking"] == "t3"  # kept


# ── End-to-end: fold happens in the chat_with_tools loop ─────────────

import pytest  # noqa: E402

from lean_ai.llm.base import LLMMetrics, LLMProvider  # noqa: E402
from lean_ai.llm.facade import LLMClient  # noqa: E402


class _ProviderFake(LLMProvider):
    """Minimal provider double with configurable ``provider_name`` and
    ``_preserve_thinking`` so we can exercise the fold branch directly."""

    def __init__(self, *, name: str, preserve_thinking: bool, responses: list):
        self._name = name
        self._preserve_thinking = preserve_thinking
        self._responses = list(responses)
        self._context_window_val = 8192
        self._max_tokens_val = 512

    @property
    def model_name(self) -> str:
        return "fake"

    @property
    def provider_name(self) -> str:
        return self._name

    @property
    def context_window(self) -> int:
        return self._context_window_val

    @property
    def max_tokens(self) -> int:
        return self._max_tokens_val

    async def chat_raw(self, messages, temperature=None, max_tokens=None, **kwargs):
        return "", LLMMetrics()

    async def chat_structured(self, messages, schema, temperature=None, max_tokens=None, **kwargs):
        raise NotImplementedError

    async def chat_with_tools_single(
        self, messages, tools, max_tokens=None, *, stream_callback=None, thinking_callback=None,
    ):
        resp = self._responses.pop(0) if self._responses else ("done", [], LLMMetrics())
        return resp

    async def chat_stream(self, messages, temperature=None, max_tokens=None, **kwargs):
        yield ""

    async def check_health(self):
        return True


async def _noop_executor(name: str, args: dict) -> str:
    return "ok"


@pytest.mark.asyncio
async def test_ollama_provider_folds_thinking_into_content():
    """Ollama's compiled renderer can't read a separate thinking field, so
    the facade folds thinking into content as <think>...</think>\\n\\n..."""
    m = LLMMetrics()
    m.thinking = "this is my reasoning"
    provider = _ProviderFake(
        name="ollama",
        preserve_thinking=True,
        responses=[
            ("here is the answer", [], m),
        ],
    )
    client = LLMClient(provider=provider)
    messages: list[dict] = [{"role": "user", "content": "hi"}]

    await client.chat_with_tools(
        messages=messages,
        tools=[],
        tool_executor_fn=_noop_executor,
        text_only_exit_count=1,
    )

    # Assistant message appended with thinking folded into content
    assistant_msgs = [m for m in messages if m["role"] == "assistant"]
    assert assistant_msgs, "no assistant message appended"
    content = assistant_msgs[-1]["content"]
    assert content.startswith("<think>\nthis is my reasoning\n</think>\n\n"), content
    assert content.endswith("here is the answer")
    # Crucially — no separate "thinking" field (the whole point of the fold)
    assert "thinking" not in assistant_msgs[-1]


@pytest.mark.asyncio
async def test_non_ollama_provider_uses_thinking_field_not_fold():
    """Serve / OpenAI / Anthropic / Gemini get thinking as a separate field —
    vLLM's Jinja template reads it via chat_template_kwargs, others ignore."""
    m = LLMMetrics()
    m.thinking = "this is my reasoning"
    provider = _ProviderFake(
        name="openai",
        preserve_thinking=True,
        responses=[
            ("here is the answer", [], m),
        ],
    )
    client = LLMClient(provider=provider)
    messages: list[dict] = [{"role": "user", "content": "hi"}]

    await client.chat_with_tools(
        messages=messages,
        tools=[],
        tool_executor_fn=_noop_executor,
        text_only_exit_count=1,
    )

    assistant = [m for m in messages if m["role"] == "assistant"][-1]
    # Content is plain (NOT folded)
    assert not assistant["content"].startswith("<think>"), assistant["content"]
    assert assistant["content"] == "here is the answer"
    # Thinking lives in its own field
    assert assistant["thinking"] == "this is my reasoning"


@pytest.mark.asyncio
async def test_preserve_thinking_off_yields_neither_field_nor_fold():
    """Default behavior (flag off): the appended assistant message has
    neither a thinking field nor a folded <think> block."""
    m = LLMMetrics()
    m.thinking = "this should be dropped"
    provider = _ProviderFake(
        name="ollama",
        preserve_thinking=False,
        responses=[
            ("answer", [], m),
        ],
    )
    client = LLMClient(provider=provider)
    messages: list[dict] = [{"role": "user", "content": "hi"}]

    await client.chat_with_tools(
        messages=messages,
        tools=[],
        tool_executor_fn=_noop_executor,
        text_only_exit_count=1,
    )

    assistant = [m for m in messages if m["role"] == "assistant"][-1]
    assert assistant["content"] == "answer"
    assert not assistant["content"].startswith("<think>")
    assert "thinking" not in assistant


def test_trim_ignores_think_blocks_not_at_content_start():
    """A `<think>` token in the MIDDLE of content (e.g. quoting something) is
    not a folded thinking block — leave it alone."""
    msgs = [
        {
            "role": "assistant",
            "content": "Here is some code with <think> in it:\n```\nprint('<think>')\n```",
        },
        {"role": "assistant", "content": "<think>\nreal\n</think>\n\nreal content"},
    ]
    _trim_old_thinking(msgs, keep_recent=0)

    # First message is unchanged — not a folded block
    assert "<think>" in msgs[0]["content"]
    # Second had a real fold — stripped
    assert msgs[1]["content"] == "real content"


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

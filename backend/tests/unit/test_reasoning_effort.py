"""Tests for reasoning_effort — mapping helpers, Ollama interrupt, cloud
native parameter forwarding, and facade interrupt loop."""

from __future__ import annotations

import pytest

from lean_ai.config import (
    reasoning_effort_to_anthropic_budget,
    reasoning_effort_to_gemini_budget,
    reasoning_effort_to_ollama_limit,
    reasoning_effort_to_openai_param,
    settings,
)

# ── Mapping helpers ──────────────────────────────────────────────────


def test_ollama_limit_mapping():
    assert reasoning_effort_to_ollama_limit("") is None
    assert reasoning_effort_to_ollama_limit("max") is None
    assert reasoning_effort_to_ollama_limit("low") == 768
    assert reasoning_effort_to_ollama_limit("medium") == 3072
    assert reasoning_effort_to_ollama_limit("high") == 8192
    # Unknown value → None (safe default)
    assert reasoning_effort_to_ollama_limit("bogus") is None


def test_openai_param_mapping():
    assert reasoning_effort_to_openai_param("") is None
    assert reasoning_effort_to_openai_param("max") is None  # omit to let model default
    assert reasoning_effort_to_openai_param("low") == "low"
    assert reasoning_effort_to_openai_param("medium") == "medium"
    assert reasoning_effort_to_openai_param("high") == "high"


def test_anthropic_budget_mapping():
    assert reasoning_effort_to_anthropic_budget("") is None
    assert reasoning_effort_to_anthropic_budget("max") is None
    # Anthropic's minimum budget_tokens is 1024
    assert reasoning_effort_to_anthropic_budget("low") == 1024
    assert reasoning_effort_to_anthropic_budget("medium") == 4096
    assert reasoning_effort_to_anthropic_budget("high") == 16384


def test_gemini_budget_mapping():
    # -1 = dynamic; Gemini decides
    assert reasoning_effort_to_gemini_budget("") == -1
    assert reasoning_effort_to_gemini_budget("max") == -1
    assert reasoning_effort_to_gemini_budget("low") == 1024
    assert reasoning_effort_to_gemini_budget("medium") == 4096
    assert reasoning_effort_to_gemini_budget("high") == 16384


# ── Per-role values are independent ─────────────────────────────────


def test_effective_expert_reasoning_effort_does_not_fallback_to_primary():
    saved_p = settings.reasoning_effort_primary
    saved_e = settings.reasoning_effort_expert
    try:
        settings.reasoning_effort_primary = "high"
        settings.reasoning_effort_expert = ""
        assert settings.effective_expert_reasoning_effort == ""

        settings.reasoning_effort_expert = "low"
        assert settings.effective_expert_reasoning_effort == "low"
    finally:
        settings.reasoning_effort_primary = saved_p
        settings.reasoning_effort_expert = saved_e


def test_effective_request_reasoning_effort_does_not_fallback_to_primary():
    saved_p = settings.reasoning_effort_primary
    saved_r = settings.reasoning_effort_request
    try:
        settings.reasoning_effort_primary = "medium"
        settings.reasoning_effort_request = ""
        assert settings.effective_request_reasoning_effort == ""

        settings.reasoning_effort_primary = ""
        settings.reasoning_effort_request = ""
        assert settings.effective_request_reasoning_effort == ""
    finally:
        settings.reasoning_effort_primary = saved_p
        settings.reasoning_effort_request = saved_r


def test_effective_worker_reasoning_effort_does_not_fallback_to_primary():
    saved_p = settings.reasoning_effort_primary
    saved_w = settings.reasoning_effort_worker
    try:
        settings.reasoning_effort_primary = "low"
        settings.reasoning_effort_worker = ""
        assert settings.effective_worker_reasoning_effort == ""
    finally:
        settings.reasoning_effort_primary = saved_p
        settings.reasoning_effort_worker = saved_w


# ── Ollama client-side budget detection ─────────────────────────────


def _provider(*, reasoning_effort: str = "", max_thinking_override: int | None = None):
    """Construct an OllamaProvider with explicit params.  Optionally
    monkey-patch settings.max_thinking_tokens for a single test."""
    from lean_ai.llm.client import OllamaProvider

    if max_thinking_override is not None:
        settings.max_thinking_tokens = max_thinking_override

    return OllamaProvider(
        ollama_url="http://localhost:11434",
        model="test",
        max_tokens=1024,
        context_window=8192,
        temperature=0.7,
        top_p=0.8,
        top_k=20,
        repeat_penalty=1.05,
        reasoning_effort=reasoning_effort,
    )


def test_budget_not_exceeded_when_effort_off_and_under_safety_rail():
    p = _provider(reasoning_effort="")
    # 10k chars ≈ 2500 tokens — well under default 32768 safety rail
    assert not p._thinking_budget_exceeded(10_000)


def test_budget_exceeded_on_low_effort_at_soft_limit():
    p = _provider(reasoning_effort="low")
    # low limit = 768 tokens; 768*4 = 3072 chars
    assert not p._thinking_budget_exceeded(3000)
    assert p._thinking_budget_exceeded(3072)
    assert p._thinking_budget_exceeded(3500)


def test_budget_exceeded_on_medium_effort():
    p = _provider(reasoning_effort="medium")
    # medium = 3072 tokens = 12288 chars
    assert not p._thinking_budget_exceeded(12000)
    assert p._thinking_budget_exceeded(12288)


def test_budget_exceeded_on_high_effort():
    p = _provider(reasoning_effort="high")
    # high = 8192 tokens = 32768 chars
    assert not p._thinking_budget_exceeded(32000)
    assert p._thinking_budget_exceeded(32768)


def test_safety_rail_fires_even_on_max_effort():
    """Max effort has no soft limit, but the universal safety rail
    (max_thinking_tokens) must still fire."""
    saved = settings.max_thinking_tokens
    try:
        p = _provider(reasoning_effort="max", max_thinking_override=1024)
        # Safety rail = 1024 tokens = 4096 chars
        assert not p._thinking_budget_exceeded(4000)
        assert p._thinking_budget_exceeded(4096)
    finally:
        settings.max_thinking_tokens = saved


def test_safety_rail_fires_even_on_off_effort():
    saved = settings.max_thinking_tokens
    try:
        p = _provider(reasoning_effort="", max_thinking_override=2048)
        assert p._thinking_budget_exceeded(2048 * 4)
    finally:
        settings.max_thinking_tokens = saved


# ── OpenAI _extra_body shape ────────────────────────────────────────


def _openai_or_skip(**kwargs):
    from lean_ai.llm.provider_openai import OpenAIProvider

    try:
        return OpenAIProvider(**kwargs)
    except ModuleNotFoundError:
        pytest.skip("openai SDK not installed")


def test_openai_extra_body_reasoning_effort_set():
    p = _openai_or_skip(api_key="dummy", reasoning_effort="medium")
    eb = p._extra_body()
    assert eb == {"extra_body": {"reasoning_effort": "medium"}}


def test_openai_extra_body_reasoning_off_omitted():
    p = _openai_or_skip(api_key="dummy", reasoning_effort="")
    assert p._extra_body() == {}


def test_openai_extra_body_reasoning_max_omitted():
    """Max maps to None (no cap) — OpenAI param is omitted."""
    p = _openai_or_skip(api_key="dummy", reasoning_effort="max")
    assert p._extra_body() == {}


def test_openai_extra_body_merges_with_preserve_thinking():
    """Both knobs set → single extra_body dict with both keys."""
    p = _openai_or_skip(
        api_key="dummy",
        reasoning_effort="high",
        preserve_thinking=True,
    )
    eb = p._extra_body()
    assert eb == {
        "extra_body": {
            "chat_template_kwargs": {"preserve_thinking": True},
            "reasoning_effort": "high",
        }
    }


# ── Facade budget-interrupt loop ────────────────────────────────────

from lean_ai.llm.base import LLMMetrics, LLMProvider  # noqa: E402
from lean_ai.llm.facade import LLMClient  # noqa: E402


class _BudgetFake(LLMProvider):
    """Provider double that emits ``thinking_budget_exceeded=True`` on the
    first N turns, then a clean response.  Used to exercise the
    chat_with_tools interrupt handler."""

    def __init__(self, *, interrupt_count: int, provider_name: str = "ollama"):
        self._name = provider_name
        self._interrupt_count = interrupt_count
        self._turn = 0
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
        self,
        messages,
        tools,
        max_tokens=None,
        *,
        stream_callback=None,
        thinking_callback=None,
    ):
        self._turn += 1
        m = LLMMetrics()
        m.thinking = "partial reasoning"
        if self._turn <= self._interrupt_count:
            m.thinking_budget_exceeded = True
            m.thinking_token_count = 3000
            return ("partial content", [], m)
        # Clean final turn
        return ("final answer", [], m)

    async def chat_stream(self, messages, temperature=None, max_tokens=None, **kwargs):
        yield ""

    async def check_health(self):
        return True


async def _noop_executor(name: str, args: dict) -> str:
    return "ok"


@pytest.mark.asyncio
async def test_facade_injects_interrupt_nudge_on_budget_exceeded():
    """One interrupt turn, then a clean final turn — the loop should inject
    the nudge as a user message and converge."""
    provider = _BudgetFake(interrupt_count=1)
    client = LLMClient(provider=provider)
    messages: list[dict] = [{"role": "user", "content": "hard question"}]

    _executed, _explanation = await client.chat_with_tools(
        messages=messages,
        tools=[],
        tool_executor_fn=_noop_executor,
        text_only_exit_count=1,
    )

    # The interrupt nudge should appear as a user message between turns
    user_nudges = [
        m
        for m in messages
        if m.get("role") == "user" and "reasoning" in str(m.get("content", "")).lower()
    ]
    assert user_nudges, f"expected budget nudge, got {messages}"


@pytest.mark.asyncio
async def test_facade_exits_after_two_consecutive_interrupts():
    """Two back-to-back budget-exceeded turns → loop exits cleanly rather
    than infinitely retrying."""
    provider = _BudgetFake(interrupt_count=10)  # always exceed
    client = LLMClient(provider=provider)
    messages: list[dict] = [{"role": "user", "content": "hard question"}]

    _executed, _explanation = await client.chat_with_tools(
        messages=messages,
        tools=[],
        tool_executor_fn=_noop_executor,
        text_only_exit_count=10,
    )

    # The provider should have been called at most max_budget_interrupts + 1 times
    # (3 turns: interrupt, interrupt, exit), not looped forever
    assert provider._turn <= 3, f"looped too many times: {provider._turn}"


@pytest.mark.asyncio
async def test_facade_resets_interrupt_counter_on_clean_turn():
    """If a turn doesn't exceed budget, consecutive_budget_interrupts resets
    to zero so a later interrupt starts a fresh count."""
    provider = _BudgetFake(interrupt_count=1)  # one interrupt, one clean
    client = LLMClient(provider=provider)
    messages: list[dict] = [{"role": "user", "content": "hi"}]

    await client.chat_with_tools(
        messages=messages,
        tools=[],
        tool_executor_fn=_noop_executor,
        text_only_exit_count=1,
    )
    # Didn't exit early — we reached the clean turn
    assert provider._turn == 2


@pytest.mark.asyncio
async def test_facade_calls_on_budget_interrupt_callback():
    provider = _BudgetFake(interrupt_count=1)
    client = LLMClient(provider=provider)
    messages: list[dict] = [{"role": "user", "content": "hi"}]

    received: list[int] = []

    async def cb(token_count: int) -> None:
        received.append(token_count)

    await client.chat_with_tools(
        messages=messages,
        tools=[],
        tool_executor_fn=_noop_executor,
        text_only_exit_count=1,
        on_budget_interrupt=cb,
    )

    assert received == [3000], f"expected one callback with the token count, got {received}"

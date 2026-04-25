"""Tests for OllamaProvider._build_options with optional min_p / presence_penalty.

The design contract: blank / None → omit from the options dict so models that
don't support the parameter don't trip on an unexpected value.  A literal 0
is a valid explicit value and must be preserved.
"""

from __future__ import annotations

from lean_ai.llm.client import OllamaProvider


def _provider(*, min_p=None, presence_penalty=None, preserve_thinking=False):
    """Factory that bypasses settings defaults for deterministic testing."""
    return OllamaProvider(
        ollama_url="http://localhost:11434",
        model="test",
        max_tokens=1024,
        context_window=8192,
        temperature=0.7,
        top_p=0.8,
        top_k=20,
        repeat_penalty=1.05,
        min_p=min_p,
        presence_penalty=presence_penalty,
        enable_thinking=False,
        preserve_thinking=preserve_thinking,
    )


# ── Option-dict assembly ───────────────────────────────────────────────


def test_build_options_omits_min_p_when_none():
    opts = _provider().build_options_for_test()
    assert "min_p" not in opts


def test_build_options_omits_presence_penalty_when_none():
    opts = _provider().build_options_for_test()
    assert "presence_penalty" not in opts


def test_build_options_includes_min_p_when_set():
    opts = _provider(min_p=0.05).build_options_for_test()
    assert opts["min_p"] == 0.05


def test_build_options_includes_presence_penalty_when_set():
    opts = _provider(presence_penalty=1.5).build_options_for_test()
    assert opts["presence_penalty"] == 1.5


def test_build_options_preserves_zero_min_p():
    """0 is an explicit 'disabled' sentinel — must NOT be dropped."""
    opts = _provider(min_p=0).build_options_for_test()
    assert opts["min_p"] == 0


def test_build_options_preserves_zero_presence_penalty():
    """0 is an explicit 'no penalty' value — must NOT be dropped."""
    opts = _provider(presence_penalty=0).build_options_for_test()
    assert opts["presence_penalty"] == 0


def test_build_options_carries_standard_keys():
    """Baseline regression — sanity check the non-optional keys."""
    opts = _provider().build_options_for_test()
    assert opts["temperature"] == 0.7
    assert opts["top_p"] == 0.8
    assert opts["top_k"] == 20
    assert opts["repeat_penalty"] == 1.05
    assert opts["num_ctx"] == 8192


def test_build_options_both_params_set():
    opts = _provider(min_p=0.1, presence_penalty=0.5).build_options_for_test()
    assert opts["min_p"] == 0.1
    assert opts["presence_penalty"] == 0.5


# ── chat_template_kwargs for preserve_thinking ─────────────────────────


def test_chat_template_kwargs_always_empty_on_ollama():
    """Ollama's compiled renderer doesn't honor chat_template_kwargs —
    preserve_thinking is handled client-side via content folding in
    facade._trim_old_thinking / the chat_with_tools loop.  The helper
    exists for call-site splat parity but must return an empty dict in
    both states."""
    assert _provider(preserve_thinking=False)._build_chat_template_kwargs() == {}
    assert _provider(preserve_thinking=True)._build_chat_template_kwargs() == {}


# OllamaProvider._build_options is private; expose via a test hook so we
# don't have to reach into the underscore.  Mini monkeypatch — once.


def _build_options_hook(self, *, temperature=None, max_tokens=None):
    return self._build_options(temperature=temperature, max_tokens=max_tokens)


OllamaProvider.build_options_for_test = _build_options_hook  # type: ignore[attr-defined]

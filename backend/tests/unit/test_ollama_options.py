"""Tests for OllamaProvider._build_options with optional min_p / presence_penalty.

The design contract: blank / None → omit from the options dict so models that
don't support the parameter don't trip on an unexpected value.  A literal 0
is a valid explicit value and must be preserved.
"""

from __future__ import annotations

from lean_ai.config import settings
from lean_ai.llm.client import OllamaProvider
from lean_ai.routers.client_factory import RoleConfig, create_role_client


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


def _provider_without_sampling():
    """Factory with sampling fields explicitly blank."""
    return OllamaProvider(
        ollama_url="http://localhost:11434",
        model="test",
        max_tokens=1024,
        context_window=8192,
        temperature=None,
        top_p=None,
        top_k=None,
        repeat_penalty=None,
        min_p=None,
        presence_penalty=None,
        enable_thinking=False,
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


def test_build_options_omits_blank_sampling_keys():
    """Blank sampling values let Ollama/Modelfile defaults apply."""
    opts = _provider_without_sampling().build_options_for_test()
    assert "temperature" not in opts
    assert "top_p" not in opts
    assert "top_k" not in opts
    assert "repeat_penalty" not in opts
    assert opts["num_ctx"] == 8192


def test_build_options_both_params_set():
    opts = _provider(min_p=0.1, presence_penalty=0.5).build_options_for_test()
    assert opts["min_p"] == 0.1
    assert opts["presence_penalty"] == 0.5


def test_role_client_blank_sampling_does_not_inherit_primary_settings():
    saved = (
        settings.ollama_temperature,
        settings.ollama_top_p,
        settings.ollama_top_k,
        settings.ollama_repeat_penalty,
        settings.ollama_min_p,
        settings.ollama_presence_penalty,
    )
    try:
        settings.ollama_temperature = 0.7
        settings.ollama_top_p = 0.8
        settings.ollama_top_k = 20
        settings.ollama_repeat_penalty = 1.05
        settings.ollama_min_p = 0.05
        settings.ollama_presence_penalty = 1.5

        client = create_role_client(
            RoleConfig(
                role_name="Request",
                provider_setting="ollama",
                enable_thinking=False,
                ollama_model="role-model",
                ollama_max_tokens=1024,
                ollama_context_window=8192,
                ollama_temperature=None,
                ollama_top_p=None,
                ollama_top_k=None,
                ollama_repeat_penalty=None,
                ollama_min_p=None,
                ollama_presence_penalty=None,
            )
        )

        assert client is not None
        opts = client._provider.build_options_for_test()  # type: ignore[attr-defined]
        for key in (
            "temperature",
            "top_p",
            "top_k",
            "repeat_penalty",
            "min_p",
            "presence_penalty",
        ):
            assert key not in opts
    finally:
        (
            settings.ollama_temperature,
            settings.ollama_top_p,
            settings.ollama_top_k,
            settings.ollama_repeat_penalty,
            settings.ollama_min_p,
            settings.ollama_presence_penalty,
        ) = saved


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

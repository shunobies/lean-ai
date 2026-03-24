"""Tests for context-window shorthand expansion in config."""

from lean_ai.config import Settings, _expand_ctx


class TestExpandCtx:
    """Unit tests for the _expand_ctx helper."""

    def test_shorthand_int(self):
        assert _expand_ctx(128) == 131072

    def test_shorthand_string(self):
        assert _expand_ctx("128") == 131072

    def test_k_suffix_lowercase(self):
        assert _expand_ctx("128k") == 131072

    def test_k_suffix_uppercase(self):
        assert _expand_ctx("128K") == 131072

    def test_k_suffix_with_spaces(self):
        assert _expand_ctx("  128k  ") == 131072

    def test_large_value_passthrough(self):
        """Values > 10000 are used as-is (backwards compatibility)."""
        assert _expand_ctx(131072) == 131072

    def test_large_string_passthrough(self):
        assert _expand_ctx("131072") == 131072

    def test_small_values(self):
        assert _expand_ctx(4) == 4096
        assert _expand_ctx(8) == 8192
        assert _expand_ctx(32) == 32768
        assert _expand_ctx(64) == 65536

    def test_boundary_10000(self):
        """10000 is the upper boundary — still treated as shorthand."""
        assert _expand_ctx(10000) == 10000 * 1024

    def test_boundary_10001(self):
        """10001 is above the boundary — kept as-is."""
        assert _expand_ctx(10001) == 10001

    def test_k_suffix_fractional(self):
        """Support fractional k values like 1.5k."""
        assert _expand_ctx("1.5k") == 1536

    def test_256k(self):
        assert _expand_ctx(256) == 262144
        assert _expand_ctx("256k") == 262144


class TestSettingsShorthand:
    """Integration tests: shorthand works through the Settings model."""

    def test_ollama_shorthand(self):
        s = Settings(ollama_context_window=128)
        assert s.ollama_context_window == 131072

    def test_openai_shorthand(self):
        s = Settings(openai_context_window=128)
        assert s.openai_context_window == 131072

    def test_anthropic_shorthand(self):
        s = Settings(anthropic_context_window=200)
        assert s.anthropic_context_window == 204800

    def test_inline_shorthand(self):
        s = Settings(inline_context_window=16)
        assert s.inline_context_window == 16384

    def test_full_value_passthrough(self):
        """Existing full values still work unchanged."""
        s = Settings(ollama_context_window=131072)
        assert s.ollama_context_window == 131072

    def test_derived_limits_from_shorthand(self):
        """max_tokens should derive correctly from expanded context window."""
        s = Settings(ollama_context_window=128)
        assert s.ollama_context_window == 131072
        assert s.ollama_max_tokens == 131072 // 4

    def test_defaults_unchanged(self):
        """Default values should be correct without any shorthand."""
        s = Settings()
        assert s.ollama_context_window == 131072
        assert s.openai_context_window == 128000
        assert s.anthropic_context_window == 200000


class TestExpertConfig:
    """Tests for expert model configuration."""

    def test_expert_defaults_empty(self):
        """When expert model is not set, expert fields are empty/None."""
        s = Settings()
        assert s.ollama_model_expert == ""
        assert s.ollama_expert_max_tokens is None
        assert s.ollama_expert_context_window is None

    def test_expert_context_window_shorthand(self):
        """Expert context window accepts shorthand notation."""
        s = Settings(ollama_model_expert="qwen3:72b", ollama_expert_context_window=64)
        assert s.ollama_expert_context_window == 65536

    def test_expert_max_tokens_derived(self):
        """Expert max_tokens derives from expert context window."""
        s = Settings(ollama_model_expert="qwen3:72b", ollama_expert_context_window=64)
        assert s.ollama_expert_max_tokens == 65536 // 4

    def test_expert_max_tokens_fallback(self):
        """Expert context_window falls back to standard when not explicitly set."""
        s = Settings(ollama_model_expert="qwen3:72b", ollama_context_window=128)
        assert s.ollama_expert_context_window == 131072
        assert s.ollama_expert_max_tokens == 131072 // 4

    def test_expert_temperature_fallback(self):
        """Expert temperature falls back to standard when not set."""
        s = Settings(ollama_temperature=0.5)
        assert s.effective_expert_temperature == 0.5

    def test_expert_temperature_override(self):
        """Expert temperature overrides when explicitly set."""
        s = Settings(ollama_temperature=0.5, ollama_expert_temperature=0.3)
        assert s.effective_expert_temperature == 0.3

    def test_expert_top_p_fallback(self):
        """Expert top_p falls back to standard when not set."""
        s = Settings(ollama_top_p=0.9)
        assert s.effective_expert_top_p == 0.9

    def test_expert_top_p_override(self):
        """Expert top_p overrides when explicitly set."""
        s = Settings(ollama_top_p=0.9, ollama_expert_top_p=0.7)
        assert s.effective_expert_top_p == 0.7

    def test_expert_top_k_fallback(self):
        """Expert top_k falls back to standard when not set."""
        s = Settings(ollama_top_k=30)
        assert s.effective_expert_top_k == 30

    def test_expert_top_k_override(self):
        """Expert top_k overrides when explicitly set."""
        s = Settings(ollama_top_k=30, ollama_expert_top_k=15)
        assert s.effective_expert_top_k == 15

    def test_expert_repeat_penalty_fallback(self):
        """Expert repeat_penalty falls back to standard when not set."""
        s = Settings(ollama_repeat_penalty=1.1)
        assert s.effective_expert_repeat_penalty == 1.1

    def test_expert_repeat_penalty_override(self):
        """Expert repeat_penalty overrides when explicitly set."""
        s = Settings(ollama_repeat_penalty=1.1, ollama_expert_repeat_penalty=1.0)
        assert s.effective_expert_repeat_penalty == 1.0

    def test_expert_not_derived_when_empty(self):
        """When expert model is empty, derived fields stay None."""
        s = Settings(ollama_model_expert="")
        assert s.ollama_expert_max_tokens is None
        assert s.ollama_expert_context_window is None


class TestRequestConfig:
    """Tests for request model configuration."""

    def test_request_defaults_empty(self):
        s = Settings()
        assert s.ollama_model_request == ""
        assert s.ollama_request_max_tokens is None
        assert s.ollama_request_context_window is None

    def test_request_context_window_shorthand(self):
        s = Settings(ollama_model_request="qwen3.5:27b", ollama_request_context_window=64)
        assert s.ollama_request_context_window == 65536

    def test_request_max_tokens_derived(self):
        s = Settings(ollama_model_request="qwen3.5:27b", ollama_request_context_window=64)
        assert s.ollama_request_max_tokens == 65536 // 4

    def test_request_max_tokens_fallback(self):
        s = Settings(ollama_model_request="qwen3.5:27b", ollama_context_window=128)
        assert s.ollama_request_context_window == 131072
        assert s.ollama_request_max_tokens == 131072 // 4

    def test_request_temperature_fallback(self):
        s = Settings(ollama_temperature=0.5)
        assert s.effective_request_temperature == 0.5

    def test_request_temperature_override(self):
        s = Settings(ollama_temperature=0.5, ollama_request_temperature=0.3)
        assert s.effective_request_temperature == 0.3

    def test_request_top_p_fallback(self):
        s = Settings(ollama_top_p=0.9)
        assert s.effective_request_top_p == 0.9

    def test_request_top_p_override(self):
        s = Settings(ollama_top_p=0.9, ollama_request_top_p=0.7)
        assert s.effective_request_top_p == 0.7

    def test_request_top_k_fallback(self):
        s = Settings(ollama_top_k=30)
        assert s.effective_request_top_k == 30

    def test_request_top_k_override(self):
        s = Settings(ollama_top_k=30, ollama_request_top_k=15)
        assert s.effective_request_top_k == 15

    def test_request_repeat_penalty_fallback(self):
        s = Settings(ollama_repeat_penalty=1.1)
        assert s.effective_request_repeat_penalty == 1.1

    def test_request_repeat_penalty_override(self):
        s = Settings(ollama_repeat_penalty=1.1, ollama_request_repeat_penalty=1.0)
        assert s.effective_request_repeat_penalty == 1.0

    def test_request_not_derived_when_empty(self):
        s = Settings(ollama_model_request="")
        assert s.ollama_request_max_tokens is None
        assert s.ollama_request_context_window is None


class TestThinkingConfig:
    """Tests for per-model enable_thinking configuration."""

    def test_thinking_defaults(self):
        s = Settings()
        assert s.enable_thinking is True
        assert s.enable_thinking_expert is True
        assert s.enable_thinking_request is True

    def test_thinking_expert_independent(self):
        """Expert thinking is independent from primary."""
        s = Settings(enable_thinking=False, enable_thinking_expert=True)
        assert s.enable_thinking is False
        assert s.enable_thinking_expert is True

    def test_thinking_request_independent(self):
        """Request thinking is independent from primary."""
        s = Settings(enable_thinking=True, enable_thinking_request=False)
        assert s.enable_thinking is True
        assert s.enable_thinking_request is False

    def test_thinking_all_disabled(self):
        s = Settings(
            enable_thinking=False,
            enable_thinking_expert=False,
            enable_thinking_request=False,
        )
        assert s.enable_thinking is False
        assert s.enable_thinking_expert is False
        assert s.enable_thinking_request is False

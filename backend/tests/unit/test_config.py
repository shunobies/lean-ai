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

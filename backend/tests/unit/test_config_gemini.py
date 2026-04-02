"""Tests for Gemini provider configuration settings."""

from lean_ai.config import Settings


class TestGeminiConfig:
    """Gemini-specific configuration tests."""

    def test_gemini_context_window_shorthand(self):
        s = Settings(gemini_context_window=1024)
        assert s.gemini_context_window == 1048576

    def test_gemini_max_tokens_derived(self):
        s = Settings(gemini_context_window=1048576)
        assert s.gemini_max_tokens == 1048576 // 4

    def test_gemini_max_tokens_explicit(self):
        s = Settings(gemini_context_window=1048576, gemini_max_tokens=100000)
        assert s.gemini_max_tokens == 100000

    def test_gemini_active_context_window(self):
        s = Settings(llm_provider="gemini", gemini_context_window=1048576)
        assert s._active_context_window == 1048576

    def test_gemini_expert_context_window(self):
        s = Settings(expert_llm_provider="gemini", gemini_context_window=1048576)
        assert s.effective_expert_context_window == 1048576

    def test_gemini_expert_max_tokens(self):
        s = Settings(expert_llm_provider="gemini", gemini_context_window=1048576)
        assert s.effective_expert_max_tokens == 1048576 // 4

    def test_gemini_request_max_tokens(self):
        s = Settings(request_llm_provider="gemini", gemini_context_window=1048576)
        assert s.effective_request_max_tokens == 1048576 // 4

    def test_gemini_defaults(self):
        s = Settings()
        assert s.gemini_model == "gemini-2.5-flash"
        assert s.gemini_temperature == 0.7
        assert s.gemini_api_key == ""
        assert s.gemini_expert_model == ""
        assert s.gemini_request_model == ""

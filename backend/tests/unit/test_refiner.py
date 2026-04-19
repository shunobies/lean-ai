"""Tests for PromptRefiner — local LLM prompt refinement and privacy stripping."""

from unittest.mock import patch

import pytest

from lean_ai.llm.base import LLMMetrics
from lean_ai.llm.refiner import PromptRefiner


class FakeOllamaProvider:
    """Minimal fake Ollama provider for testing the refiner."""

    def __init__(self, response: str = "refined text"):
        self.response = response
        self.call_count = 0
        self.last_messages: list[dict] = []

    async def chat_raw(self, messages, temperature=None, max_tokens=None):
        self.call_count += 1
        self.last_messages = messages
        return self.response, LLMMetrics()


# ── Core behaviour ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refiner_inactive_when_no_ollama():
    """PromptRefiner(None) is not active and all methods pass through."""
    refiner = PromptRefiner(ollama_provider=None)
    assert not refiner.is_active

    result = await refiner.refine_chat_message("hello world")
    assert result.refined == "hello world"
    assert not result.was_refined

    result = await refiner.refine_task("add auth", repo_root="/tmp")
    assert result.refined == "add auth"
    assert not result.was_refined

    text, redactions = await refiner.strip_privacy("secret data")
    assert text == "secret data"
    assert redactions == []


@pytest.mark.asyncio
async def test_refine_chat_message_basic():
    """Refined text comes through with was_refined=True."""
    provider = FakeOllamaProvider(
        response="1. Add authentication endpoint\n2. Use JWT tokens",
    )
    refiner = PromptRefiner(
        ollama_provider=provider,
        enable_reference=False,
        enable_privacy=False,
    )
    result = await refiner.refine_chat_message("add auth")
    assert result.was_refined
    assert "authentication" in result.refined
    assert result.original == "add auth"
    assert result.duration_ms > 0


@pytest.mark.asyncio
async def test_refine_task_basic():
    """Task refinement works with REFINER_TASK_PROMPT."""
    provider = FakeOllamaProvider(
        response=(
            "1. Add OAuth2 JWT authentication to FastAPI endpoints\n"
            "2. Create auth middleware\n"
            "3. Add login/logout routes"
        ),
    )
    refiner = PromptRefiner(
        ollama_provider=provider,
        enable_reference=False,
        enable_privacy=False,
    )
    result = await refiner.refine_task("add auth", repo_root="/tmp")
    assert result.was_refined
    assert "OAuth2" in result.refined


# ── Reference library integration ──────────────────────────────────


@pytest.mark.asyncio
async def test_refine_chat_message_with_reference():
    """When reference library returns results, they appear in the prompt."""
    provider = FakeOllamaProvider(
        response="Add JWT auth using the internal OAuth2 gateway",
    )
    refiner = PromptRefiner(
        ollama_provider=provider,
        enable_reference=True,
        enable_privacy=False,
        reference_chunks=3,
    )

    mock_chunks = [
        {
            "doc_title": "Auth Guide",
            "section": "OAuth2",
            "content": "Use the OAuth2 gateway at auth.internal.com",
        },
    ]

    with (
        patch(
            "lean_ai.reference.indexer.is_reference_available",
            return_value=True,
        ),
        patch(
            "lean_ai.reference.indexer.search_reference",
            return_value=mock_chunks,
        ),
    ):
        result = await refiner.refine_chat_message(
            "add auth", repo_root="/tmp/project",
        )

    assert result.was_refined
    assert result.reference_context  # non-empty
    assert "Auth Guide" in result.reference_context
    # Verify the reference section was included in the LLM prompt
    prompt_sent = provider.last_messages[0]["content"]
    assert "REFERENCE MATERIAL" in prompt_sent


@pytest.mark.asyncio
async def test_refine_chat_message_no_reference():
    """When reference library is unavailable, refinement still works."""
    provider = FakeOllamaProvider(response="Add authentication to the API")
    refiner = PromptRefiner(
        ollama_provider=provider,
        enable_reference=True,
        enable_privacy=False,
    )

    with patch(
        "lean_ai.reference.indexer.is_reference_available",
        return_value=False,
    ):
        result = await refiner.refine_chat_message(
            "add auth", repo_root="/tmp/project",
        )

    assert result.was_refined
    assert result.reference_context == ""
    # Verify no REFERENCE MATERIAL section in prompt
    prompt_sent = provider.last_messages[0]["content"]
    assert "REFERENCE MATERIAL" not in prompt_sent


# ── Privacy stripping ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_strip_privacy_parses_redactions():
    """Privacy strip correctly parses the ---REDACTIONS--- separator."""
    provider = FakeOllamaProvider(
        response=(
            "Connect to <DB_CONNECTION> with token <REDACTED_KEY>\n"
            "---REDACTIONS---\n"
            "- Replaced database URL with <DB_CONNECTION>\n"
            "- Replaced API token with <REDACTED_KEY>"
        ),
    )
    refiner = PromptRefiner(
        ollama_provider=provider,
        enable_privacy=True,
    )
    text, redactions = await refiner.strip_privacy(
        "Connect to postgres://user:pass@db.internal:5432/prod with token sk-abc123",
    )
    assert "<DB_CONNECTION>" in text
    assert "<REDACTED_KEY>" in text
    assert len(redactions) == 2


@pytest.mark.asyncio
async def test_strip_privacy_no_separator():
    """When response has no ---REDACTIONS---, return the response as-is."""
    original = "This text has no sensitive data in it at all"
    provider = FakeOllamaProvider(response=original)
    refiner = PromptRefiner(
        ollama_provider=provider,
        enable_privacy=True,
    )
    text, redactions = await refiner.strip_privacy(original)
    assert text == original
    assert redactions == []


@pytest.mark.asyncio
async def test_strip_privacy_none_redactions():
    """When LLM reports 'None' in redactions, list is empty."""
    provider = FakeOllamaProvider(
        response="Clean text here\n---REDACTIONS---\n- None",
    )
    refiner = PromptRefiner(
        ollama_provider=provider,
        enable_privacy=True,
    )
    text, redactions = await refiner.strip_privacy("Clean text here")
    assert text == "Clean text here"
    assert redactions == []


# ── Safety guards ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refiner_length_guard():
    """If refined text is <50% of original, keep original."""
    long_original = "This is a detailed, well-structured request " * 10
    provider = FakeOllamaProvider(response="Short")
    refiner = PromptRefiner(
        ollama_provider=provider,
        enable_reference=False,
        enable_privacy=False,
    )
    result = await refiner.refine_chat_message(long_original)
    assert not result.was_refined
    assert result.refined == long_original


@pytest.mark.asyncio
async def test_strip_privacy_over_strip_guard():
    """If privacy strip removes >40% of text, keep original."""
    original = "This is a long text with lots of important content " * 5
    # Return a very short sanitized version
    provider = FakeOllamaProvider(
        response="Short\n---REDACTIONS---\n- Removed everything",
    )
    refiner = PromptRefiner(
        ollama_provider=provider,
        enable_privacy=True,
    )
    text, redactions = await refiner.strip_privacy(original)
    assert text == original
    assert redactions == []


@pytest.mark.asyncio
async def test_refiner_timeout_returns_original():
    """On timeout, original text passes through."""
    provider = FakeOllamaProvider()

    async def slow_chat_raw(*args, **kwargs):
        import asyncio
        await asyncio.sleep(10)
        return "refined", LLMMetrics()

    provider.chat_raw = slow_chat_raw

    refiner = PromptRefiner(
        ollama_provider=provider,
        enable_reference=False,
        enable_privacy=False,
        timeout=0.1,
    )
    result = await refiner.refine_chat_message("hello")
    assert result.refined == "hello"
    assert not result.was_refined
    assert result.error == "timeout"


@pytest.mark.asyncio
async def test_refiner_exception_returns_original():
    """On provider exception, original text passes through."""
    provider = FakeOllamaProvider()

    async def failing_chat_raw(*args, **kwargs):
        raise ConnectionError("Ollama is down")

    provider.chat_raw = failing_chat_raw

    refiner = PromptRefiner(
        ollama_provider=provider,
        enable_reference=False,
        enable_privacy=False,
    )
    result = await refiner.refine_chat_message("hello")
    assert result.refined == "hello"
    assert not result.was_refined
    assert "Ollama is down" in result.error


# ── Privacy disabled ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_strip_privacy_disabled():
    """When enable_privacy=False, strip_privacy is a no-op."""
    provider = FakeOllamaProvider()
    refiner = PromptRefiner(
        ollama_provider=provider,
        enable_privacy=False,
    )
    text, redactions = await refiner.strip_privacy("secret key: sk-abc123")
    assert text == "secret key: sk-abc123"
    assert redactions == []
    assert provider.call_count == 0  # No LLM call made

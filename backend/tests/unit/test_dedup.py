"""Tests for shared LLM-based section deduplication."""

from unittest.mock import AsyncMock

import pytest

from lean_ai.context.dedup import deduplicate_sections_llm


class TestDeduplicateSectionsLlm:
    @pytest.mark.asyncio
    async def test_removes_llm_identified_duplicates(self):
        text = (
            "## Architecture\n"
            "MVC pattern with controllers.\n\n"
            "## Data Flow\n"
            "Request flows through middleware.\n\n"
            "## Architecture Overview\n"
            "The system uses MVC architecture."
        )
        mock_llm = AsyncMock()
        mock_llm.chat_raw = AsyncMock(return_value="3")

        result = await deduplicate_sections_llm(text, mock_llm)
        assert "## Architecture\n" in result
        assert "## Data Flow\n" in result
        assert "## Architecture Overview" not in result

    @pytest.mark.asyncio
    async def test_none_response_preserves_all(self):
        text = (
            "## Section A\n"
            "Content A\n\n"
            "## Section B\n"
            "Content B"
        )
        mock_llm = AsyncMock()
        mock_llm.chat_raw = AsyncMock(return_value="NONE")

        result = await deduplicate_sections_llm(text, mock_llm)
        assert result == text

    @pytest.mark.asyncio
    async def test_llm_failure_returns_unchanged(self):
        text = "## Section\nContent"
        mock_llm = AsyncMock()
        mock_llm.chat_raw = AsyncMock(side_effect=Exception("LLM down"))

        result = await deduplicate_sections_llm(text, mock_llm)
        assert result == text

    @pytest.mark.asyncio
    async def test_single_section_skips_llm(self):
        """Document with fewer than 2 sections should skip LLM call."""
        text = "## Only Section\nContent here."
        mock_llm = AsyncMock()
        mock_llm.chat_raw = AsyncMock(return_value="NONE")

        result = await deduplicate_sections_llm(text, mock_llm)
        assert result == text
        mock_llm.chat_raw.assert_not_called()

    @pytest.mark.asyncio
    async def test_custom_log_prefix(self):
        """Ensure log_prefix parameter is accepted without error."""
        text = "## A\nContent A\n\n## B\nContent B"
        mock_llm = AsyncMock()
        mock_llm.chat_raw = AsyncMock(return_value="NONE")

        result = await deduplicate_sections_llm(
            text, mock_llm, log_prefix="Project context",
        )
        assert result == text

    @pytest.mark.asyncio
    async def test_no_valid_numbers_returns_unchanged(self):
        """LLM response with no parseable numbers returns original."""
        text = (
            "## Section A\n"
            "Content A\n\n"
            "## Section B\n"
            "Content B"
        )
        mock_llm = AsyncMock()
        mock_llm.chat_raw = AsyncMock(return_value="looks fine to me!")

        result = await deduplicate_sections_llm(text, mock_llm)
        assert result == text

    @pytest.mark.asyncio
    async def test_collapses_blank_lines(self):
        """After removing a section, triple+ blank lines are collapsed."""
        text = (
            "## Keep\n"
            "Content\n\n\n\n"
            "## Remove\n"
            "Duplicate\n\n\n\n"
            "## Also Keep\n"
            "More content"
        )
        mock_llm = AsyncMock()
        mock_llm.chat_raw = AsyncMock(return_value="2")

        result = await deduplicate_sections_llm(text, mock_llm)
        assert "## Remove" not in result
        assert "## Keep" in result
        assert "## Also Keep" in result
        # No runs of 4+ newlines (3+ blank lines)
        assert "\n\n\n\n" not in result

"""Tests for expansion merge, heading extraction, and LLM concurrency.

Pure unit tests — no LLM, no network calls required.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from lean_ai.context.content import extract_section_headings
from lean_ai.context.expansion import _merge_additions_into_doc
from lean_ai.llm.base import LLMMetrics
from lean_ai.llm.facade import LLMClient

_DUMMY_METRICS = LLMMetrics(
    tokens_per_second=0.0, completion_tokens=0, prompt_tokens=0,
)


# ---------------------------------------------------------------------------
# extract_section_headings
# ---------------------------------------------------------------------------


class TestExtractSectionHeadings:
    def test_basic(self):
        doc = "# Title\n## Foo\nsome text\n## Bar\nmore text\n"
        assert extract_section_headings(doc) == ["## Foo", "## Bar"]

    def test_empty(self):
        assert extract_section_headings("") == []

    def test_no_headings(self):
        assert extract_section_headings("just plain text\nno headings\n") == []

    def test_ignores_subheadings(self):
        doc = "## Top\n### Sub\ntext\n## Another\n"
        assert extract_section_headings(doc) == ["## Top", "## Another"]

    def test_strips_whitespace(self):
        doc = "  ## Indented  \n## Normal\n"
        assert extract_section_headings(doc) == ["## Indented", "## Normal"]


# ---------------------------------------------------------------------------
# _merge_additions_into_doc
# ---------------------------------------------------------------------------


BASE_DOC = """\
# Project Context

## Architecture Overview
Main app entry point.

## Module Map
- `src/` — source code

## Key Abstractions
- `App`: main class
"""


class TestMergeAdditionsIntoDoc:
    def test_single_addition(self):
        additions = [
            "## Key Abstractions\n- `Router`: handles routes\n",
        ]
        result = _merge_additions_into_doc(BASE_DOC, additions)
        assert "- `App`: main class" in result
        assert "- `Router`: handles routes" in result

    def test_multiple_additions_same_heading(self):
        additions = [
            "## Module Map\n- `lib/` — utilities\n",
            "## Module Map\n- `tests/` — test suite\n",
        ]
        result = _merge_additions_into_doc(BASE_DOC, additions)
        assert "- `src/` — source code" in result
        assert "- `lib/` — utilities" in result
        assert "- `tests/` — test suite" in result

    def test_additions_to_different_headings(self):
        additions = [
            "## Module Map\n- `lib/` — utilities\n"
            "## Key Abstractions\n- `Helper`: utility class\n",
        ]
        result = _merge_additions_into_doc(BASE_DOC, additions)
        assert "- `lib/` — utilities" in result
        assert "- `Helper`: utility class" in result

    def test_unknown_heading_skipped(self):
        additions = [
            "## Nonexistent Section\n- some content\n",
        ]
        result = _merge_additions_into_doc(BASE_DOC, additions)
        # Unknown heading content should not appear in merged doc.
        assert "Nonexistent Section" not in result
        # Base doc unchanged.
        assert "- `App`: main class" in result

    def test_empty_additions(self):
        result = _merge_additions_into_doc(BASE_DOC, [])
        assert result == BASE_DOC

    def test_empty_additions_text(self):
        result = _merge_additions_into_doc(BASE_DOC, ["", "  \n\n  "])
        assert result == BASE_DOC

    def test_preserves_base_structure(self):
        additions = [
            "## Architecture Overview\nExtra architecture note.\n",
        ]
        result = _merge_additions_into_doc(BASE_DOC, additions)
        # Original content preserved.
        assert "Main app entry point." in result
        # Addition present.
        assert "Extra architecture note." in result
        # Heading order preserved.
        arch_pos = result.index("## Architecture Overview")
        mod_pos = result.index("## Module Map")
        key_pos = result.index("## Key Abstractions")
        assert arch_pos < mod_pos < key_pos


# ---------------------------------------------------------------------------
# LLMClient.chat_raw semaphore
# ---------------------------------------------------------------------------


class TestChatRawSemaphore:
    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        """chat_raw respects the concurrency semaphore."""
        max_concurrent = 0
        current_concurrent = 0

        async def mock_chat_raw(messages, temperature=None, max_tokens=None):
            nonlocal max_concurrent, current_concurrent
            current_concurrent += 1
            if current_concurrent > max_concurrent:
                max_concurrent = current_concurrent
            await asyncio.sleep(0.05)  # Simulate LLM latency
            current_concurrent -= 1
            return ("result", _DUMMY_METRICS)

        mock_provider = MagicMock()
        mock_provider.chat_raw = AsyncMock(side_effect=mock_chat_raw)

        semaphore = asyncio.Semaphore(2)
        client = LLMClient(provider=mock_provider, concurrency_semaphore=semaphore)

        # Fire 5 concurrent calls — semaphore should limit to 2 at a time.
        tasks = [client.chat_raw([{"role": "user", "content": f"msg{i}"}]) for i in range(5)]
        await asyncio.gather(*tasks)

        assert max_concurrent <= 2

    @pytest.mark.asyncio
    async def test_no_semaphore_allows_full_concurrency(self):
        """Without semaphore, all calls run concurrently."""
        max_concurrent = 0
        current_concurrent = 0

        async def mock_chat_raw(messages, temperature=None, max_tokens=None):
            nonlocal max_concurrent, current_concurrent
            current_concurrent += 1
            if current_concurrent > max_concurrent:
                max_concurrent = current_concurrent
            await asyncio.sleep(0.05)
            current_concurrent -= 1
            return ("result", _DUMMY_METRICS)

        mock_provider = MagicMock()
        mock_provider.chat_raw = AsyncMock(side_effect=mock_chat_raw)

        client = LLMClient(provider=mock_provider)  # No semaphore

        tasks = [client.chat_raw([{"role": "user", "content": f"msg{i}"}]) for i in range(5)]
        await asyncio.gather(*tasks)

        assert max_concurrent == 5

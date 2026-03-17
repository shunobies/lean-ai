"""Tests for parallel expansion pipeline — merge, heading extraction, and orchestration.

Pure unit tests — no LLM, no network calls required.
"""

from unittest.mock import AsyncMock, patch

import pytest

from lean_ai.context.content import (
    build_parallel_expansion_prompt,
    extract_section_headings,
)
from lean_ai.context.generation import (
    _expand_project_context,
    _expand_project_context_sequential,
    _merge_additions_into_doc,
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
# build_parallel_expansion_prompt
# ---------------------------------------------------------------------------


class TestBuildParallelExpansionPrompt:
    def test_contains_headings_and_files(self):
        headings = ["## Module Map", "## Key Abstractions"]
        files = "--- src/foo.py ---\ndef bar(): pass\n"
        result = build_parallel_expansion_prompt(headings, files)
        assert "## Module Map" in result
        assert "## Key Abstractions" in result
        assert "src/foo.py" in result

    def test_does_not_contain_full_doc(self):
        """The parallel prompt should only have headings, not the full doc."""
        headings = ["## Module Map"]
        files = "--- src/foo.py ---\ncontent\n"
        result = build_parallel_expansion_prompt(headings, files)
        assert "EXISTING DOCUMENT HEADINGS" in result
        assert "EXISTING DOCUMENT ===" not in result


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
# _expand_project_context_sequential (basic sanity)
# ---------------------------------------------------------------------------


class TestExpandSequential:
    @pytest.mark.asyncio
    async def test_returns_updated_doc(self):
        """Sequential expansion returns updated doc when LLM output is valid."""
        base = "## Module Map\n- original\n"
        updated = "## Module Map\n- original\n- added by llm\n"

        mock_client = AsyncMock()
        mock_client.chat_raw = AsyncMock(return_value=updated)

        result = await _expand_project_context_sequential(
            base, mock_client, ["batch1"], max_out=4096,
        )
        assert "added by llm" in result

    @pytest.mark.asyncio
    async def test_keeps_base_on_short_output(self):
        """Sequential expansion keeps base when LLM returns too-short output."""
        base = "## Module Map\n" + ("x" * 200) + "\n"
        short = "tiny"

        mock_client = AsyncMock()
        mock_client.chat_raw = AsyncMock(return_value=short)

        result = await _expand_project_context_sequential(
            base, mock_client, ["batch1"], max_out=4096,
        )
        # Should keep base since "tiny" is < 70% of base length.
        assert result == base


# ---------------------------------------------------------------------------
# _expand_project_context (parallel mode integration)
# ---------------------------------------------------------------------------


class TestExpandParallel:
    @pytest.mark.asyncio
    async def test_parallel_mode_merges_results(self):
        """Parallel expansion merges additions from multiple batches."""
        base = (
            "# Project Context\n\n"
            "## Module Map\n- `src/` — source\n\n"
            "## Key Abstractions\n- `App`: main class\n"
        )

        call_count = 0

        async def mock_chat_raw(messages, max_tokens=None):
            nonlocal call_count
            call_count += 1
            # Expansion batches return additions-only.
            return (
                "## Key Abstractions\n"
                f"- `Worker{call_count}`: processes tasks\n"
            )

        mock_client = AsyncMock()
        mock_client.chat_raw = AsyncMock(side_effect=mock_chat_raw)

        # settings and list_repo_tree are local imports inside the function,
        # so patch at source rather than on the generation module.
        with (
            patch("lean_ai.config.settings") as mock_settings,
            patch("lean_ai.indexer.tree.list_repo_tree") as mock_tree,
            patch("lean_ai.context.generation.extract_metadata_cached") as mock_meta,
            patch("lean_ai.context.generation._collect_priority_file_contents") as mock_priority,
            patch("lean_ai.context.generation._collect_all_ranked_candidates") as mock_candidates,
            patch("lean_ai.context.generation._batch_file_contents") as mock_batch,
        ):
            mock_settings.expansion_concurrency = 3

            # Minimal stubs for the tree/metadata pipeline.
            mock_tree.return_value = []
            mock_meta.return_value = type("M", (), {"fan_in": {}})()
            mock_priority.return_value = ("", set())
            mock_candidates.return_value = [
                ("file1.py", "content1"),
                ("file2.py", "content2"),
            ]
            mock_batch.return_value = ["batch1_content", "batch2_content"]

            result = await _expand_project_context(
                base, "/fake/repo", mock_client,
                caps={}, max_out=4096, context_window=131072,
            )

        # Both batch additions should be merged in.
        assert "Worker" in result
        # Original content preserved.
        assert "- `App`: main class" in result

    @pytest.mark.asyncio
    async def test_sequential_fallback(self):
        """expansion_concurrency=0 falls back to sequential."""
        base = "## Module Map\n- original\n"
        updated = "## Module Map\n- original\n- sequential addition\n"

        mock_client = AsyncMock()
        mock_client.chat_raw = AsyncMock(return_value=updated)

        with (
            patch("lean_ai.config.settings") as mock_settings,
            patch("lean_ai.indexer.tree.list_repo_tree") as mock_tree,
            patch("lean_ai.context.generation.extract_metadata_cached") as mock_meta,
            patch("lean_ai.context.generation._collect_priority_file_contents") as mock_priority,
            patch("lean_ai.context.generation._collect_all_ranked_candidates") as mock_candidates,
            patch("lean_ai.context.generation._batch_file_contents") as mock_batch,
        ):
            mock_settings.expansion_concurrency = 0
            mock_tree.return_value = []
            mock_meta.return_value = type("M", (), {"fan_in": {}})()
            mock_priority.return_value = ("", set())
            mock_candidates.return_value = [("file1.py", "content1")]
            mock_batch.return_value = ["batch1_content"]

            result = await _expand_project_context(
                base, "/fake/repo", mock_client,
                caps={}, max_out=4096, context_window=131072,
            )

        assert "sequential addition" in result

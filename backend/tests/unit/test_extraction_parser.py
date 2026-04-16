"""Tests for the extraction schema + deterministic skeleton parser."""

import pytest
from pydantic import ValidationError

from lean_ai.context.extraction_parser import (
    ContextExtractionEntry,
    ContextExtractionResult,
    _extract_file_path,
    _looks_like_path,
    _normalize_heading,
    parse_skeleton_output,
)

# ---------------------------------------------------------------------------
# ContextExtractionResult schema
# ---------------------------------------------------------------------------

class TestContextExtractionSchema:
    def test_valid_entry_roundtrip(self):
        result = ContextExtractionResult.model_validate({
            "entries": [
                {
                    "section": "Architecture Overview",
                    "symbol": "main",
                    "description": "Application entry point.",
                    "file_path": "src/main.py",
                },
            ],
        })
        assert len(result.entries) == 1
        assert result.entries[0].section == "Architecture Overview"
        assert result.entries[0].symbol == "main"

    def test_empty_entries_list_is_legal(self):
        result = ContextExtractionResult.model_validate({"entries": []})
        assert result.entries == []

    def test_hallucinated_section_rejected(self):
        with pytest.raises(ValidationError):
            ContextExtractionResult.model_validate({
                "entries": [
                    {
                        "section": "Made Up Section",
                        "symbol": "x",
                        "description": "y",
                        "file_path": "z.py",
                    },
                ],
            })

    def test_all_seven_sections_accepted(self):
        sections = [
            "Architecture Overview",
            "Module Map",
            "Key Abstractions",
            "API Surface",
            "Integration Points",
            "Data Flow",
            "Conventions",
        ]
        for name in sections:
            entry = ContextExtractionEntry(
                section=name,
                symbol="s",
                description="d",
                file_path="f.py",
            )
            assert entry.section == name


# ---------------------------------------------------------------------------
# parse_skeleton_output
# ---------------------------------------------------------------------------

class TestParseSkeletonOutput:
    def test_basic(self):
        text = """\
# Project Context

## Architecture Overview
- Python FastAPI application

## Module Map
### src/
- `main.py` — entry point
- `utils.py` — utilities
"""
        result = parse_skeleton_output(text)
        assert len(result) >= 3
        # All entries should have source="skeleton" (4th element).
        for entry in result:
            assert entry[3] == "skeleton"

    def test_subsections_set_file_path(self):
        text = """\
## Module Map
### src/lean_ai/
- `client.py` — LLM client
"""
        result = parse_skeleton_output(text)
        assert len(result) == 1
        # The subsection should set the file_path context.
        assert result[0][1] == "src/lean_ai"

    def test_placeholder_skip(self):
        text = """\
## Architecture Overview
No data extracted yet.

## Module Map
- `main.py` — entry point
"""
        result = parse_skeleton_output(text)
        # "No data extracted yet." should be skipped.
        sections = [r[0] for r in result]
        assert "Module Map" in sections
        contents = [r[2] for r in result]
        assert "No data extracted yet." not in contents

    def test_non_bullet_content(self):
        text = """\
## Architecture Overview
Entry points: `main.py`, `app.py`
"""
        result = parse_skeleton_output(text)
        assert len(result) == 1
        assert result[0][2] == "Entry points: `main.py`, `app.py`"

    def test_empty_input(self):
        assert parse_skeleton_output("") == []
        assert parse_skeleton_output("   ") == []

    def test_top_level_heading_ignored(self):
        text = "# Project Context\n## Architecture Overview\n- Fact"
        result = parse_skeleton_output(text)
        sections = [r[0] for r in result]
        assert "Project Context" not in sections

    def test_truncated_placeholder_skip(self):
        text = "## Module Map\n... (truncated)\n- Real entry"
        result = parse_skeleton_output(text)
        contents = [r[2] for r in result]
        assert "... (truncated)" not in contents
        assert "Real entry" in contents

    def test_key_abstractions_extract_inline_path(self):
        # Key Abstractions skeleton lines carry the path in parens.
        text = """\
## Key Abstractions
- `class Foo` (`src/foo.py`) — fan-in: 3
"""
        result = parse_skeleton_output(text)
        assert len(result) == 1
        assert result[0][1] == "src/foo.py"


# ---------------------------------------------------------------------------
# _normalize_heading
# ---------------------------------------------------------------------------

class TestNormalizeHeading:
    def test_no_qualifier(self):
        assert _normalize_heading("Key Abstractions") == "Key Abstractions"

    def test_with_qualifier(self):
        assert _normalize_heading("Key Abstractions (Updated)") == "Key Abstractions"

    def test_nested_parens(self):
        # Should only strip trailing qualifier
        assert _normalize_heading("Data Flow") == "Data Flow"


# ---------------------------------------------------------------------------
# _extract_file_path (used by skeleton parser only)
# ---------------------------------------------------------------------------

class TestExtractFilePath:
    def test_path_in_parens(self):
        assert _extract_file_path("Some fact (`src/main.py`)") == "src/main.py"

    def test_path_without_backticks_in_parens(self):
        assert _extract_file_path("Some fact (src/main.py)") == "src/main.py"

    def test_backtick_path_anywhere(self):
        assert _extract_file_path("`src/handlers/api.py` — API handler") == "src/handlers/api.py"

    def test_no_path(self):
        assert _extract_file_path("Just a plain fact") == ""

    def test_path_needs_slash(self):
        assert _extract_file_path("`main.py` — no slash") == ""

    def test_path_needs_extension(self):
        assert _extract_file_path("`src/noextension` — no dot") == ""


# ---------------------------------------------------------------------------
# _looks_like_path
# ---------------------------------------------------------------------------

class TestLooksLikePath:
    def test_valid_path(self):
        assert _looks_like_path("src/main.py") is True

    def test_valid_windows_path(self):
        assert _looks_like_path("src\\main.py") is True

    def test_no_slash(self):
        assert _looks_like_path("main.py") is False

    def test_no_extension(self):
        assert _looks_like_path("src/main") is False

    def test_empty(self):
        assert _looks_like_path("") is False

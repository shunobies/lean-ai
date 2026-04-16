"""Tests for the extraction output parser (extraction_parser.py)."""


from lean_ai.context.extraction_parser import (
    _extract_file_path,
    _looks_like_path,
    _normalize_heading,
    parse_extraction_output,
    parse_skeleton_output,
)

# ---------------------------------------------------------------------------
# parse_extraction_output
# ---------------------------------------------------------------------------

class TestParseExtractionOutput:
    def test_basic(self):
        text = """\
## Architecture Overview
- `main()` — application entry point (`src/main.py`)

## Module Map
- `utils.py` — helper functions (`src/utils.py`)
"""
        result = parse_extraction_output(text)
        assert len(result) == 2
        assert result[0] == (
            "Architecture Overview",
            "src/main.py",
            "`main()` — application entry point (`src/main.py`)",
        )
        assert result[1][0] == "Module Map"

    def test_with_file_path_in_parens(self):
        text = "## Architecture Overview\n- Entry point (`src/app.py`)"
        result = parse_extraction_output(text)
        assert len(result) == 1
        assert result[0][1] == "src/app.py"

    def test_backtick_path(self):
        text = "## Module Map\n- `src/handlers/api.py` — API handler"
        result = parse_extraction_output(text)
        assert len(result) == 1
        assert result[0][1] == "src/handlers/api.py"

    def test_fallback_file_path(self):
        text = "## Architecture Overview\n- Simple hello world app"
        result = parse_extraction_output(text, fallback_file_path="src/main.py")
        assert len(result) == 1
        assert result[0][1] == "src/main.py"

    def test_unknown_section_ignored(self):
        text = """\
## Architecture Overview
- Fact 1 (`src/a.py`)

## Some Random Heading
- This should be ignored

## Module Map
- Fact 2 (`src/b.py`)
"""
        result = parse_extraction_output(text)
        assert len(result) == 2
        sections = {r[0] for r in result}
        assert "Some Random Heading" not in sections

    def test_heading_normalization(self):
        text = "## Key Abstractions (Updated)\n- `Foo` class (`src/foo.py`)"
        result = parse_extraction_output(text)
        assert len(result) == 1
        assert result[0][0] == "Key Abstractions"

    def test_empty_input(self):
        assert parse_extraction_output("") == []
        assert parse_extraction_output("   ") == []

    def test_none_input(self):
        # Should handle None gracefully.
        assert parse_extraction_output(None) == []

    def test_non_bullet_lines_ignored(self):
        text = """\
## Architecture Overview
This is a paragraph (not a bullet).
- Actual bullet entry (`src/main.py`)
Another paragraph.
"""
        result = parse_extraction_output(text)
        assert len(result) == 1
        assert "Actual bullet" in result[0][2]

    def test_all_valid_sections(self):
        sections = [
            "Architecture Overview",
            "Module Map",
            "Key Abstractions",
            "API Surface",
            "Integration Points",
            "Data Flow",
            "Conventions",
        ]
        lines = []
        for s in sections:
            lines.append(f"## {s}")
            lines.append(f"- Fact for {s} (`src/{s.lower().replace(' ', '_')}.py`)")
        text = "\n".join(lines)
        result = parse_extraction_output(text)
        assert len(result) == 7
        result_sections = [r[0] for r in result]
        assert result_sections == sections

    def test_empty_bullet_ignored(self):
        text = "## Architecture Overview\n- \n- Real entry (`src/a.py`)"
        result = parse_extraction_output(text)
        assert len(result) == 1

    def test_no_heading_before_bullets(self):
        text = "- Orphan bullet without heading"
        result = parse_extraction_output(text)
        assert len(result) == 0


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
# _extract_file_path
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

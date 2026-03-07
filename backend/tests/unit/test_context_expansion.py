"""Tests for additive context expansion — section merging and prompt building.

Pure unit tests — no LLM, no network calls required.
"""

from lean_ai.context.content import (
    _extract_covered_names,
    _extract_section_headings,
    build_additive_expansion_prompt,
)
from lean_ai.context.generation import _deduplicate_subsections, _merge_additions

# ---------------------------------------------------------------------------
# _merge_additions
# ---------------------------------------------------------------------------

BASE_DOC = """\
# Project Context

## Architecture Overview
This project uses FastAPI.

## Module Map
### src/api/
- Handles REST endpoints

## Key Abstractions
### src/models.py
- `User` — user model

## Data Flow
1. Request enters via FastAPI router

## Integration Points
- `api/router.py` imports `models.py`

## API Surface
- `GET /health` → `health_check()`
"""


class TestMergeAdditions:
    def test_adds_to_matching_section(self):
        additions = """\
## Module Map
### src/utils/
- Helper utilities for string processing

## Key Abstractions
### src/utils/strings.py
- `slugify()` — converts text to URL-safe slug
"""
        result = _merge_additions(BASE_DOC, additions)
        # Module Map section should now contain both original and new content
        assert "src/api/" in result
        assert "src/utils/" in result
        assert "slugify()" in result

    def test_preserves_all_original_content(self):
        additions = """\
## Key Abstractions
### src/new_file.py
- `NewClass` — does something
"""
        result = _merge_additions(BASE_DOC, additions)
        # All original content still present
        assert "FastAPI" in result
        assert "`User` — user model" in result
        assert "health_check()" in result
        assert "Request enters via FastAPI router" in result

    def test_discards_unmatched_sections(self):
        additions = """\
## Nonexistent Section
Some content that has no matching section.

## Key Abstractions
### src/real.py
- `RealClass` — this should be added
"""
        result = _merge_additions(BASE_DOC, additions)
        assert "Nonexistent Section" not in result
        assert "RealClass" in result

    def test_empty_additions(self):
        result = _merge_additions(BASE_DOC, "")
        assert result == BASE_DOC

    def test_whitespace_only_additions(self):
        result = _merge_additions(BASE_DOC, "   \n\n  ")
        assert result == BASE_DOC

    def test_additions_without_headings(self):
        result = _merge_additions(BASE_DOC, "Some random text without any headings")
        assert result == BASE_DOC

    def test_multiple_additions_to_same_section(self):
        additions = """\
## Key Abstractions
### src/a.py
- `ClassA` — first addition

## Key Abstractions
### src/b.py
- `ClassB` — second addition
"""
        result = _merge_additions(BASE_DOC, additions)
        assert "ClassA" in result
        assert "ClassB" in result

    def test_addition_to_last_section(self):
        additions = """\
## API Surface
- `POST /users` → `create_user()` in `router.py`
"""
        result = _merge_additions(BASE_DOC, additions)
        assert "POST /users" in result
        assert "GET /health" in result

    def test_heading_normalization(self):
        additions = """\
## Key Abstractions (Updated)
### src/extra.py
- `ExtraClass` — added via normalized heading
"""
        result = _merge_additions(BASE_DOC, additions)
        assert "ExtraClass" in result

    def test_section_ordering_preserved(self):
        additions = """\
## Module Map
### src/new_module/
- New module entry

## API Surface
- `DELETE /users/{id}` → `delete_user()`
"""
        result = _merge_additions(BASE_DOC, additions)
        # Check that Module Map still comes before Key Abstractions
        map_pos = result.find("## Module Map")
        key_pos = result.find("## Key Abstractions")
        api_pos = result.find("## API Surface")
        assert map_pos < key_pos < api_pos


# ---------------------------------------------------------------------------
# _extract_section_headings
# ---------------------------------------------------------------------------


class TestExtractSectionHeadings:
    def test_extracts_headings(self):
        headings = _extract_section_headings(BASE_DOC)
        assert "## Architecture Overview" in headings
        assert "## Module Map" in headings
        assert "## Key Abstractions" in headings
        assert "## API Surface" in headings

    def test_ignores_h3(self):
        headings = _extract_section_headings(BASE_DOC)
        for h in headings:
            assert not h.startswith("### ")

    def test_empty_doc(self):
        assert _extract_section_headings("") == []


# ---------------------------------------------------------------------------
# _extract_covered_names
# ---------------------------------------------------------------------------


class TestExtractCoveredNames:
    def test_extracts_backtick_names(self):
        doc = "Uses `User` model and `slugify()` function."
        covered = _extract_covered_names(doc)
        assert "User" in covered
        assert "slugify()" in covered

    def test_ignores_paths(self):
        doc = "File at `/src/main.py` and class `Foo`."
        covered = _extract_covered_names(doc)
        assert "Foo" in covered
        assert "/src/main.py" not in covered

    def test_deduplicates(self):
        doc = "`Foo` is used by `Bar` and also `Foo` again."
        covered = _extract_covered_names(doc)
        assert covered.count("Foo") == 1

    def test_empty_doc(self):
        assert _extract_covered_names("") == ""


# ---------------------------------------------------------------------------
# build_additive_expansion_prompt
# ---------------------------------------------------------------------------


class TestBuildAdditiveExpansionPrompt:
    def test_includes_section_headings(self):
        prompt = build_additive_expansion_prompt(BASE_DOC, "some file content")
        assert "## Architecture Overview" in prompt
        assert "## Module Map" in prompt

    def test_includes_covered_names(self):
        prompt = build_additive_expansion_prompt(BASE_DOC, "some file content")
        assert "User" in prompt
        assert "health_check()" in prompt

    def test_includes_file_batch(self):
        batch = "--- src/new.py ---\n```\nclass NewClass: pass\n```"
        prompt = build_additive_expansion_prompt(BASE_DOC, batch)
        assert "NewClass" in prompt


# ---------------------------------------------------------------------------
# _extract_covered_names — ### heading extraction
# ---------------------------------------------------------------------------


class TestExtractCoveredNamesHeadings:
    def test_extracts_h3_headings(self):
        doc = "## Module Map\n### app/Controllers/\n- Handles requests\n"
        covered = _extract_covered_names(doc)
        assert "app/Controllers/" in covered

    def test_h3_deduplicated_with_backtick(self):
        doc = "### src/models.py\n- `User` model\n### src/models.py\n- duplicate\n"
        covered = _extract_covered_names(doc)
        assert covered.count("src/models.py") == 1

    def test_h3_with_backtick_heading(self):
        doc = "## Key Abstractions\n### src/router.py\n- `Router` class\n"
        covered = _extract_covered_names(doc)
        # Both the backtick name and the h3 heading path should be present
        assert "Router" in covered
        assert "src/router.py" in covered


# ---------------------------------------------------------------------------
# _deduplicate_subsections
# ---------------------------------------------------------------------------


class TestDeduplicateSubsections:
    def test_removes_duplicate_h3_within_section(self):
        doc = (
            "## Module Map\n"
            "### app/Controllers/\n"
            "- Handles requests\n"
            "\n"
            "### app/Models/\n"
            "- Data models\n"
            "\n"
            "### app/Controllers/\n"
            "- Duplicate entry\n"
        )
        result = _deduplicate_subsections(doc)
        assert result.count("### app/Controllers/") == 1
        assert "Handles requests" in result
        assert "Duplicate entry" not in result
        assert "### app/Models/" in result

    def test_allows_same_h3_in_different_sections(self):
        doc = (
            "## Module Map\n"
            "### app/Controllers/\n"
            "- Overview\n"
            "\n"
            "## Key Abstractions\n"
            "### app/Controllers/\n"
            "- Detailed description\n"
        )
        result = _deduplicate_subsections(doc)
        assert result.count("### app/Controllers/") == 2

    def test_preserves_doc_without_duplicates(self):
        doc = (
            "## Module Map\n"
            "### src/api/\n"
            "- API handlers\n"
            "\n"
            "### src/models/\n"
            "- Models\n"
        )
        result = _deduplicate_subsections(doc)
        assert "### src/api/" in result
        assert "### src/models/" in result

    def test_empty_doc(self):
        assert _deduplicate_subsections("") == ""

    def test_no_subsections(self):
        doc = "## Module Map\nSome content without subsections\n"
        result = _deduplicate_subsections(doc)
        assert result == doc

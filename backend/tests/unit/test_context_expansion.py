"""Tests for additive context expansion — prompt building and subsection dedup.

Pure unit tests — no LLM, no network calls required.
"""

from lean_ai.context.content import build_additive_expansion_prompt
from lean_ai.context.generation import _deduplicate_subsections

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


# ---------------------------------------------------------------------------
# build_additive_expansion_prompt
# ---------------------------------------------------------------------------


class TestBuildAdditiveExpansionPrompt:
    def test_includes_full_existing_document(self):
        prompt = build_additive_expansion_prompt(BASE_DOC, "some file content")
        # The full document content should be present, not just headings
        assert "This project uses FastAPI." in prompt
        assert "`User` — user model" in prompt
        assert "Request enters via FastAPI router" in prompt

    def test_includes_file_batch(self):
        batch = "--- src/new.py ---\n```\nclass NewClass: pass\n```"
        prompt = build_additive_expansion_prompt(BASE_DOC, batch)
        assert "NewClass" in prompt

    def test_has_existing_document_section(self):
        prompt = build_additive_expansion_prompt(BASE_DOC, "file content")
        assert "=== EXISTING DOCUMENT ===" in prompt

    def test_has_source_files_section(self):
        prompt = build_additive_expansion_prompt(BASE_DOC, "file content")
        assert "=== SOURCE FILES" in prompt


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

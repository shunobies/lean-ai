"""Integration tests for the context generation pipeline (generation.py).

Tests the full generate_project_context() function with mocked LLM clients
and temporary directories.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from lean_ai.context.context_db import get_context_db, query_entries
from lean_ai.context.extraction_parser import parse_extraction_output, parse_skeleton_output
from lean_ai.context.generation import (
    _condense,
    _extract_single_file,
    _phase1_parallel,
    _phase1_sequential,
    generate_project_context,
    update_project_context,
    write_project_context,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def repo_root(tmp_path):
    """Create a minimal repo structure for testing."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "__init__.py").write_text("")
    (src / "main.py").write_text(
        'from src.utils import helper\n\n'
        'def main():\n'
        '    """Entry point."""\n'
        '    print(helper())\n'
    )
    (src / "utils.py").write_text(
        'def helper():\n'
        '    """Return a greeting."""\n'
        '    return "hello"\n'
    )
    return str(tmp_path)


@pytest.fixture
def mock_llm_client():
    """Create a mock LLM client that returns valid extraction output."""
    client = AsyncMock()
    client.chat_raw = AsyncMock(side_effect=_mock_chat_raw)
    return client


def _mock_chat_raw(messages, max_tokens=None, thinking_callback=None, **kwargs):
    """Return extraction-formatted responses based on the input."""
    user_msg = messages[-1]["content"] if messages else ""

    if "main.py" in user_msg:
        return (
            "## Architecture Overview\n"
            "- `main()` — application entry point (`src/main.py`)\n\n"
            "## Module Map\n"
            "- `main.py` — entry point that calls helper (`src/main.py`)\n\n"
            "## Integration Points\n"
            "- Imports `helper` from `src/utils` (`src/main.py`)\n"
        )
    if "utils.py" in user_msg:
        return (
            "## Module Map\n"
            "- `utils.py` — provides `helper()` function (`src/utils.py`)\n\n"
            "## Key Abstractions\n"
            "- `helper()` — returns a greeting string (`src/utils.py`)\n"
        )
    # Default/condensation response
    return "## Architecture Overview\n- Simple Python app"


# ---------------------------------------------------------------------------
# _extract_single_file
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_single_file():
    client = AsyncMock()
    client.chat_raw = AsyncMock(return_value="## Architecture Overview\n- Fact 1")
    result = await _extract_single_file("src/main.py", "def main(): pass", client, 4096)
    assert "Architecture Overview" in result
    assert "Fact 1" in result


@pytest.mark.asyncio
async def test_extract_single_file_empty_response():
    client = AsyncMock()
    client.chat_raw = AsyncMock(return_value="")
    result = await _extract_single_file("src/main.py", "def main(): pass", client, 4096)
    assert result == ""


# ---------------------------------------------------------------------------
# _phase1_sequential
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_phase1_sequential(repo_root, mock_llm_client):
    candidates = [
        ("src/main.py", "def main(): pass"),
        ("src/utils.py", "def helper(): return 42"),
    ]
    db = await get_context_db(repo_root)
    try:
        await _phase1_sequential(
            candidates, mock_llm_client, 4096, db,
        )
        rows = await query_entries(db, limit=100)
        assert len(rows) > 0
        # Should have entries from both files.
        file_paths = {r["file_path"] for r in rows}
        assert any("main" in fp for fp in file_paths)
        assert any("utils" in fp for fp in file_paths)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_phase1_sequential_llm_failure(repo_root):
    """LLM failure for one file should not stop processing other files."""
    call_count = 0

    async def flaky_chat_raw(messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("LLM unavailable")
        return "## Module Map\n- `utils.py` — utilities (`src/utils.py`)"

    client = AsyncMock()
    client.chat_raw = AsyncMock(side_effect=flaky_chat_raw)

    candidates = [
        ("src/main.py", "def main(): pass"),
        ("src/utils.py", "def helper(): return 42"),
    ]
    db = await get_context_db(repo_root)
    try:
        await _phase1_sequential(candidates, client, 4096, db)
        # Should have entries from the second file only.
        rows = await query_entries(db, limit=100)
        assert len(rows) > 0
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# _phase1_parallel
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_phase1_parallel(repo_root, mock_llm_client):
    candidates = [
        ("src/main.py", "def main(): pass"),
        ("src/utils.py", "def helper(): return 42"),
    ]
    await _phase1_parallel(
        candidates, mock_llm_client, 4096, repo_root,
    )
    # Verify entries were written by parallel connections.
    db = await get_context_db(repo_root)
    try:
        rows = await query_entries(db, limit=100)
        assert len(rows) > 0
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# generate_project_context (full pipeline)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_project_context_full_pipeline(repo_root, mock_llm_client):
    result = await generate_project_context(repo_root, mock_llm_client)

    # Should return non-empty markdown.
    assert len(result) > 0
    assert "# Project Context" in result

    # DB should have been created.
    db_path = Path(repo_root) / ".lean_ai" / "context.db"
    assert db_path.is_file()

    # Markdown file should have been written.
    md_path = Path(repo_root) / ".lean_ai" / "project_context.md"
    assert md_path.is_file()
    assert len(md_path.read_text()) > 0


@pytest.mark.asyncio
async def test_generate_project_context_no_source_files(tmp_path):
    """Empty repo should produce skeleton-only output."""
    repo_root = str(tmp_path)
    client = AsyncMock()
    client.chat_raw = AsyncMock(return_value="")

    result = await generate_project_context(repo_root, client)

    assert len(result) > 0
    assert "# Project Context" in result


@pytest.mark.asyncio
async def test_generate_project_context_llm_failure(repo_root):
    """Complete LLM failure should still return skeleton content."""
    client = AsyncMock()
    client.chat_raw = AsyncMock(side_effect=RuntimeError("LLM offline"))

    result = await generate_project_context(repo_root, client)

    # Should have skeleton content from Phase 0.
    assert len(result) > 0
    assert "# Project Context" in result


@pytest.mark.asyncio
async def test_generate_project_context_progress_callback(repo_root, mock_llm_client):
    events = []

    async def progress_cb(event):
        events.append(event)

    await generate_project_context(
        repo_root, mock_llm_client,
        progress_callback=progress_cb,
    )

    # Should have at least skeleton and export progress events.
    phases = [e.get("phase") for e in events]
    assert "skeleton" in phases
    assert "export" in phases


@pytest.mark.asyncio
async def test_generate_project_context_worker_not_used_for_extraction(repo_root, mock_llm_client):
    """Worker model is too small for extraction — should NOT be used."""
    worker = AsyncMock()
    worker.chat_raw = AsyncMock(return_value="## Module Map\n- Worker extracted (`src/main.py`)")

    await generate_project_context(
        repo_root, mock_llm_client,
        worker_client=worker,
    )

    # Worker should NOT be called for extraction (too small).
    # Primary (mock_llm_client) should be used instead.
    assert not worker.chat_raw.called
    assert mock_llm_client.chat_raw.called


@pytest.mark.asyncio
async def test_generate_project_context_request_client_used_for_extraction(repo_root):
    """Request client should be preferred for extraction when available."""
    primary = AsyncMock()
    primary.chat_raw = AsyncMock(return_value="## Module Map\n- Primary (`src/main.py`)")
    request = AsyncMock()
    request.chat_raw = AsyncMock(return_value="## Module Map\n- Request (`src/main.py`)")

    await generate_project_context(
        repo_root, primary,
        request_client=request,
    )

    # Request client should be used for extraction.
    assert request.chat_raw.called


@pytest.mark.asyncio
async def test_generate_project_context_db_entries(repo_root, mock_llm_client):
    """Verify DB has entries from both skeleton and LLM extraction."""
    await generate_project_context(repo_root, mock_llm_client)

    db = await get_context_db(repo_root)
    try:
        rows = await query_entries(db, limit=1000)
        sources = {r["source"] for r in rows}
        # Should have both skeleton and LLM entries.
        assert "skeleton" in sources
        assert "llm" in sources
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# update_project_context
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_project_context_incremental(repo_root, mock_llm_client):
    # First, generate initial context.
    await generate_project_context(repo_root, mock_llm_client)

    # Modify a file.
    (Path(repo_root) / "src" / "utils.py").write_text(
        'def helper():\n    return "updated"\n\ndef new_func():\n    pass\n'
    )

    # Update incrementally.
    path = await update_project_context(
        repo_root, ["src/utils.py"], mock_llm_client,
    )
    assert path is not None

    # Verify the context file exists and has content.
    md_path = Path(repo_root) / ".lean_ai" / "project_context.md"
    assert md_path.is_file()
    assert len(md_path.read_text()) > 0


@pytest.mark.asyncio
async def test_update_project_context_no_existing_context(tmp_path):
    """Should return None when no existing context file exists."""
    client = AsyncMock()
    result = await update_project_context(str(tmp_path), ["foo.py"], client)
    assert result is None


# ---------------------------------------------------------------------------
# _condense
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_condense_skip_short_document():
    """Documents under target word count should skip condensation."""
    doc = "Short document with a few words."
    client = AsyncMock()

    result = await _condense(doc, client, max_tokens=4096, context_window=131072)

    # Should return original without calling LLM.
    assert result == doc
    assert not client.chat_raw.called


@pytest.mark.asyncio
async def test_condense_bad_ratio_keeps_original():
    """Condensation with bad output ratio should keep original."""
    doc = " ".join(["word"] * 10000)  # Large document
    client = AsyncMock()
    client.chat_raw = AsyncMock(return_value="tiny")  # Too short

    result = await _condense(doc, client, max_tokens=4096, context_window=131072)

    assert result == doc  # Should keep original


# ---------------------------------------------------------------------------
# write_project_context
# ---------------------------------------------------------------------------

def test_write_project_context(tmp_path):
    content = "# Project Context\nTest content"
    path = write_project_context(str(tmp_path), content)

    assert Path(path).is_file()
    assert Path(path).read_text() == content


# ---------------------------------------------------------------------------
# parse_extraction_output integration with DB
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extraction_to_db_roundtrip(repo_root):
    """Verify extraction → parse → DB insert → export roundtrip."""
    raw = """\
## Architecture Overview
- `main()` — entry point (`src/main.py`)

## Module Map
- `utils.py` — helpers (`src/utils.py`)
"""
    parsed = parse_extraction_output(raw, fallback_file_path="src/main.py")
    assert len(parsed) == 2

    from lean_ai.context.context_db import export_to_markdown, upsert_entries_batch
    db = await get_context_db(repo_root)
    try:
        entries = [(s, fp, c, "llm") for s, fp, c in parsed]
        count = await upsert_entries_batch(db, entries)
        assert count == 2

        md = await export_to_markdown(db)
        assert "entry point" in md
        assert "helpers" in md
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_skeleton_to_db_roundtrip(repo_root):
    """Verify skeleton → parse → DB insert → export roundtrip."""
    skeleton = """\
# Project Context

## Architecture Overview
- Python application

## Module Map
### src/
- `main.py` — entry point
"""
    parsed = parse_skeleton_output(skeleton)
    assert len(parsed) >= 2

    from lean_ai.context.context_db import export_to_markdown, upsert_entries_batch
    db = await get_context_db(repo_root)
    try:
        count = await upsert_entries_batch(db, parsed)
        assert count >= 2

        md = await export_to_markdown(db)
        assert "Python application" in md
        assert "entry point" in md
    finally:
        await db.close()

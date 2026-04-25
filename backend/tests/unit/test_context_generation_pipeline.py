"""Integration tests for the context generation pipeline (generation.py).

Tests the full generate_project_context() function with mocked LLM clients
and temporary directories.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from lean_ai.context.context_db import get_context_db, query_entries
from lean_ai.context.extraction_parser import (
    ContextExtractionEntry,
    ContextExtractionResult,
    parse_skeleton_output,
)
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
        'from src.utils import helper\n\ndef main():\n    """Entry point."""\n    print(helper())\n'
    )
    (src / "utils.py").write_text(
        'def helper():\n    """Return a greeting."""\n    return "hello"\n'
    )
    return str(tmp_path)


def _mock_chat_structured(messages, schema=None, max_tokens=None, thinking_callback=None, **kwargs):
    """Return schema-valid ``ContextExtractionResult`` based on the source file."""
    user_msg = messages[-1]["content"] if messages else ""

    if "main.py" in user_msg:
        return ContextExtractionResult(
            entries=[
                ContextExtractionEntry(
                    section="Architecture Overview",
                    symbol="main()",
                    description="application entry point",
                    file_path="src/main.py",
                ),
                ContextExtractionEntry(
                    section="Module Map",
                    symbol="main.py",
                    description="entry point that calls helper",
                    file_path="src/main.py",
                ),
                ContextExtractionEntry(
                    section="Integration Points",
                    symbol="helper",
                    description="imports helper from src/utils",
                    file_path="src/main.py",
                ),
            ],
        )
    if "utils.py" in user_msg:
        return ContextExtractionResult(
            entries=[
                ContextExtractionEntry(
                    section="Module Map",
                    symbol="utils.py",
                    description="provides helper() function",
                    file_path="src/utils.py",
                ),
                ContextExtractionEntry(
                    section="Key Abstractions",
                    symbol="helper()",
                    description="returns a greeting string",
                    file_path="src/utils.py",
                ),
            ],
        )
    return ContextExtractionResult(entries=[])


def _mock_chat_raw_condense(messages, max_tokens=None, thinking_callback=None, **kwargs):
    """Condensation output kept as markdown (``chat_raw`` path)."""
    return "## Architecture Overview\n- Simple Python app"


@pytest.fixture
def mock_llm_client():
    """Create a mock LLM client with structured extraction + raw condensation."""
    client = AsyncMock()
    client.chat_structured = AsyncMock(side_effect=_mock_chat_structured)
    client.chat_raw = AsyncMock(side_effect=_mock_chat_raw_condense)
    return client


# ---------------------------------------------------------------------------
# _extract_single_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_single_file():
    client = AsyncMock()
    client.chat_structured = AsyncMock(
        return_value=ContextExtractionResult(
            entries=[
                ContextExtractionEntry(
                    section="Architecture Overview",
                    symbol="main",
                    description="Fact 1",
                    file_path="src/main.py",
                ),
            ],
        )
    )
    result = await _extract_single_file("src/main.py", "def main(): pass", client, 4096)
    assert len(result) == 1
    section, file_path, content, source = result[0]
    assert section == "Architecture Overview"
    assert file_path == "src/main.py"
    assert "main" in content
    assert "Fact 1" in content
    assert source == "llm"


@pytest.mark.asyncio
async def test_extract_single_file_empty_response():
    """Empty entries list from the LLM should produce no tuples."""
    client = AsyncMock()
    client.chat_structured = AsyncMock(return_value=ContextExtractionResult(entries=[]))
    result = await _extract_single_file("src/main.py", "def main(): pass", client, 4096)
    assert result == []


@pytest.mark.asyncio
async def test_extract_single_file_fallback_file_path():
    """Entry with empty file_path should fall back to the source file path."""
    client = AsyncMock()
    client.chat_structured = AsyncMock(
        return_value=ContextExtractionResult(
            entries=[
                ContextExtractionEntry(
                    section="Module Map",
                    symbol="thing",
                    description="does stuff",
                    file_path="",
                ),
            ],
        )
    )
    result = await _extract_single_file("src/real.py", "", client, 4096)
    assert len(result) == 1
    assert result[0][1] == "src/real.py"
    assert "src/real.py" in result[0][2]


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
            candidates,
            mock_llm_client,
            4096,
            db,
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

    async def flaky_chat_structured(messages, schema=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("LLM unavailable")
        return ContextExtractionResult(
            entries=[
                ContextExtractionEntry(
                    section="Module Map",
                    symbol="utils.py",
                    description="utilities",
                    file_path="src/utils.py",
                ),
            ]
        )

    client = AsyncMock()
    client.chat_structured = AsyncMock(side_effect=flaky_chat_structured)

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
        candidates,
        mock_llm_client,
        4096,
        repo_root,
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
    client.chat_structured = AsyncMock(return_value=ContextExtractionResult(entries=[]))
    client.chat_raw = AsyncMock(return_value="")

    result = await generate_project_context(repo_root, client)

    assert len(result) > 0
    assert "# Project Context" in result


@pytest.mark.asyncio
async def test_generate_project_context_llm_failure(repo_root):
    """Complete LLM failure should still return skeleton content."""
    client = AsyncMock()
    client.chat_structured = AsyncMock(side_effect=RuntimeError("LLM offline"))
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
        repo_root,
        mock_llm_client,
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
    worker.chat_structured = AsyncMock(
        return_value=ContextExtractionResult(
            entries=[
                ContextExtractionEntry(
                    section="Module Map",
                    symbol="worker",
                    description="worker extracted",
                    file_path="src/main.py",
                ),
            ],
        )
    )

    await generate_project_context(
        repo_root,
        mock_llm_client,
        worker_client=worker,
    )

    # Worker should NOT be called for extraction (too small).
    # Primary (mock_llm_client) should be used instead.
    assert not worker.chat_structured.called
    assert mock_llm_client.chat_structured.called


@pytest.mark.asyncio
async def test_generate_project_context_request_client_preferred_for_extraction(repo_root):
    """Request client should be preferred for extraction when available, primary is fallback."""
    primary = AsyncMock()
    primary.chat_structured = AsyncMock(
        return_value=ContextExtractionResult(
            entries=[
                ContextExtractionEntry(
                    section="Module Map",
                    symbol="primary",
                    description="p",
                    file_path="src/main.py",
                ),
            ],
        )
    )
    primary.chat_raw = AsyncMock(return_value="## Architecture Overview\n- summary")
    request = AsyncMock()
    request.chat_structured = AsyncMock(
        return_value=ContextExtractionResult(
            entries=[
                ContextExtractionEntry(
                    section="Module Map",
                    symbol="request",
                    description="r",
                    file_path="src/main.py",
                ),
            ],
        )
    )
    request.chat_raw = AsyncMock(return_value="## Architecture Overview\n- summary")

    await generate_project_context(
        repo_root,
        primary,
        request_client=request,
    )

    # Request should be used for extraction when available.
    assert request.chat_structured.called


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
        repo_root,
        ["src/utils.py"],
        mock_llm_client,
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
# Structured extraction + DB roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extraction_to_db_roundtrip(repo_root):
    """Verify structured extraction → tuple conversion → DB insert → export."""
    client = AsyncMock()
    client.chat_structured = AsyncMock(
        return_value=ContextExtractionResult(
            entries=[
                ContextExtractionEntry(
                    section="Architecture Overview",
                    symbol="main()",
                    description="entry point",
                    file_path="src/main.py",
                ),
                ContextExtractionEntry(
                    section="Module Map",
                    symbol="utils.py",
                    description="helpers",
                    file_path="src/utils.py",
                ),
            ],
        )
    )

    tuples = await _extract_single_file("src/main.py", "code", client, 4096)
    assert len(tuples) == 2

    from lean_ai.context.context_db import export_to_markdown, upsert_entries_batch

    db = await get_context_db(repo_root)
    try:
        count = await upsert_entries_batch(db, tuples)
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

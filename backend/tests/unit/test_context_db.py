"""Tests for the SQLite-backed context database (context_db.py)."""

import asyncio
from pathlib import Path

import pytest

from lean_ai.context.context_db import (
    clear_all,
    delete_entries_for_file,
    export_to_markdown,
    get_context_db,
    get_existing_hashes,
    get_stats,
    query_entries,
    upsert_entries_batch,
)


@pytest.fixture
def repo_root(tmp_path):
    return str(tmp_path)


@pytest.fixture
async def db(repo_root):
    """Open a fresh context DB for each test."""
    conn = await get_context_db(repo_root)
    yield conn
    await conn.close()


# ---------------------------------------------------------------------------
# get_context_db
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_context_db_creates_file(repo_root):
    db = await get_context_db(repo_root)
    try:
        db_path = Path(repo_root) / ".lean_ai" / "context.db"
        assert db_path.is_file()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_get_context_db_wal_mode(repo_root):
    db = await get_context_db(repo_root)
    try:
        cursor = await db.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
        assert row[0] == "wal"
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# upsert_entries_batch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_entries_batch_inserts(db):
    entries = [
        ("Architecture Overview", "src/main.py", "Entry point", "llm"),
        ("Module Map", "src/utils.py", "Utility functions", "llm"),
    ]
    count = await upsert_entries_batch(db, entries)
    assert count == 2

    rows = await query_entries(db)
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_upsert_entries_batch_dedup(db):
    entries = [
        ("Architecture Overview", "src/main.py", "Entry point", "llm"),
    ]
    count1 = await upsert_entries_batch(db, entries)
    assert count1 == 1

    # Insert exact same entry again — should be skipped.
    count2 = await upsert_entries_batch(db, entries)
    assert count2 == 0

    rows = await query_entries(db)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_upsert_entries_batch_empty(db):
    count = await upsert_entries_batch(db, [])
    assert count == 0


@pytest.mark.asyncio
async def test_upsert_entries_batch_different_content_same_section(db):
    """Same section+file but different content should insert both."""
    entries = [
        ("Architecture Overview", "src/main.py", "Entry point", "llm"),
        ("Architecture Overview", "src/main.py", "FastAPI app", "llm"),
    ]
    count = await upsert_entries_batch(db, entries)
    assert count == 2


# ---------------------------------------------------------------------------
# delete_entries_for_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_entries_for_file(db):
    entries = [
        ("Architecture Overview", "src/main.py", "Entry point", "llm"),
        ("Module Map", "src/utils.py", "Utility functions", "llm"),
    ]
    await upsert_entries_batch(db, entries)

    await delete_entries_for_file(db, "src/main.py")

    rows = await query_entries(db)
    assert len(rows) == 1
    assert rows[0]["file_path"] == "src/utils.py"


@pytest.mark.asyncio
async def test_delete_entries_for_file_with_source(db):
    entries = [
        ("Architecture Overview", "src/main.py", "From skeleton", "skeleton"),
        ("Architecture Overview", "src/main.py", "From LLM", "llm"),
    ]
    await upsert_entries_batch(db, entries)

    await delete_entries_for_file(db, "src/main.py", source="llm")

    rows = await query_entries(db)
    assert len(rows) == 1
    assert rows[0]["source"] == "skeleton"


# ---------------------------------------------------------------------------
# clear_all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_all(db):
    entries = [
        ("Architecture Overview", "src/main.py", "Entry point", "llm"),
        ("Module Map", "src/utils.py", "Utility functions", "llm"),
    ]
    await upsert_entries_batch(db, entries)
    assert len(await query_entries(db)) == 2

    await clear_all(db)
    assert len(await query_entries(db)) == 0


# ---------------------------------------------------------------------------
# query_entries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_entries_no_filter(db):
    entries = [
        ("Architecture Overview", "src/main.py", "Entry point", "llm"),
        ("Module Map", "src/utils.py", "Utility functions", "llm"),
        ("Conventions", "src/main.py", "Snake case", "llm"),
    ]
    await upsert_entries_batch(db, entries)

    rows = await query_entries(db)
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_query_entries_section_filter(db):
    entries = [
        ("Architecture Overview", "src/main.py", "Entry point", "llm"),
        ("Module Map", "src/utils.py", "Utility functions", "llm"),
    ]
    await upsert_entries_batch(db, entries)

    rows = await query_entries(db, section="Architecture Overview")
    assert len(rows) == 1
    assert rows[0]["section"] == "Architecture Overview"


@pytest.mark.asyncio
async def test_query_entries_file_path_filter(db):
    entries = [
        ("Architecture Overview", "src/main.py", "Entry point", "llm"),
        ("Module Map", "src/utils.py", "Utility functions", "llm"),
    ]
    await upsert_entries_batch(db, entries)

    rows = await query_entries(db, file_path="main")
    assert len(rows) == 1
    assert "main" in rows[0]["file_path"]


@pytest.mark.asyncio
async def test_query_entries_keyword_filter(db):
    entries = [
        ("Architecture Overview", "src/main.py", "Entry point", "llm"),
        ("Module Map", "src/utils.py", "Utility functions", "llm"),
    ]
    await upsert_entries_batch(db, entries)

    rows = await query_entries(db, keyword="Entry")
    assert len(rows) == 1
    assert "Entry" in rows[0]["content"]


@pytest.mark.asyncio
async def test_query_entries_limit(db):
    entries = [("Architecture Overview", f"src/f{i}.py", f"Fact {i}", "llm") for i in range(10)]
    await upsert_entries_batch(db, entries)

    rows = await query_entries(db, limit=3)
    assert len(rows) == 3


# ---------------------------------------------------------------------------
# get_existing_hashes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_existing_hashes_uses_latest_llm_hash_only(db):
    await upsert_entries_batch(
        db,
        [
            ("Architecture Overview", "src/main.py", "Skeleton fact", "skeleton"),
            ("Architecture Overview", "src/main.py", "Old fact", "llm", "oldhash"),
        ],
    )
    await asyncio.sleep(0.01)
    await upsert_entries_batch(
        db,
        [
            ("Module Map", "src/main.py", "New fact", "llm", "newhash"),
        ],
    )

    hashes = await get_existing_hashes(db)

    assert hashes == {"src/main.py": "newhash"}


# ---------------------------------------------------------------------------
# export_to_markdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_to_markdown(db):
    entries = [
        ("Architecture Overview", "src/main.py", "Entry point", "llm"),
        ("Module Map", "src/utils.py", "Utility functions", "llm"),
    ]
    await upsert_entries_batch(db, entries)

    md = await export_to_markdown(db)
    assert "# Project Context" in md
    assert "## Architecture Overview" in md
    assert "## Module Map" in md
    assert "- Entry point" in md
    assert "- Utility functions" in md


@pytest.mark.asyncio
async def test_export_to_markdown_empty(db):
    md = await export_to_markdown(db)
    assert "# Project Context" in md
    assert "No data extracted yet." in md


@pytest.mark.asyncio
async def test_export_to_markdown_dedup(db):
    """Exact duplicate content in the same section should appear only once."""
    entries = [
        ("Architecture Overview", "src/main.py", "Entry point", "llm"),
        ("Architecture Overview", "src/other.py", "Entry point", "llm"),
    ]
    # UNIQUE constraint means same content+section+path is deduped at insert.
    # But same content from different paths is allowed.
    await upsert_entries_batch(db, entries)

    md = await export_to_markdown(db)
    # export_to_markdown does inline dedup on content, so "Entry point"
    # should appear only once.
    assert md.count("- Entry point") == 1


@pytest.mark.asyncio
async def test_export_to_markdown_preserves_bullet_prefix(db):
    entries = [
        ("Architecture Overview", "src/main.py", "- Already has bullet", "llm"),
        ("Architecture Overview", "src/utils.py", "No bullet prefix", "llm"),
    ]
    await upsert_entries_batch(db, entries)

    md = await export_to_markdown(db)
    # "- Already has bullet" should NOT get double bullet "- - Already..."
    assert "- - Already" not in md
    assert "- Already has bullet" in md
    assert "- No bullet prefix" in md


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_stats(db):
    entries = [
        ("Architecture Overview", "src/main.py", "Entry point", "llm"),
        ("Architecture Overview", "src/utils.py", "Another fact", "llm"),
        ("Module Map", "src/utils.py", "Utility functions", "llm"),
    ]
    await upsert_entries_batch(db, entries)

    stats = await get_stats(db)
    assert stats["total_entries"] == 3
    assert stats["unique_files"] == 2
    assert stats["sections"]["Architecture Overview"] == 2
    assert stats["sections"]["Module Map"] == 1


# ---------------------------------------------------------------------------
# WAL mode concurrent writes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wal_concurrent_writes(repo_root):
    """Multiple connections can write concurrently via WAL mode."""
    # Create schema once before concurrent writers to avoid DDL contention.
    init_db = await get_context_db(repo_root)
    await init_db.close()

    async def write_entries(connection_id):
        db = await get_context_db(repo_root)
        try:
            entries = [
                (
                    "Architecture Overview",
                    f"conn{connection_id}/f{i}.py",
                    f"Fact {i} from connection {connection_id}",
                    "llm",
                )
                for i in range(5)
            ]
            await upsert_entries_batch(db, entries)
        finally:
            await db.close()

    # Run multiple writers concurrently.
    await asyncio.gather(
        write_entries(1),
        write_entries(2),
        write_entries(3),
    )

    # Verify all entries are visible from a single connection.
    db = await get_context_db(repo_root)
    try:
        rows = await query_entries(db, limit=100)
        assert len(rows) == 15  # 3 connections * 5 entries
    finally:
        await db.close()

"""SQLite persistence for project context entries.

Per-workspace database at ``.lean_ai/context.db``, separate from the main
``lean_ai.db``.  Uses WAL mode for parallel-safe writes during file-by-file
extraction.
"""

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from lean_ai.sqlite_compat import SQLITE_ROW_FACTORY, SQLiteConnection
from lean_ai.sqlite_compat import connect as connect_sqlite

logger = logging.getLogger(__name__)

_SECTION_ORDER = [
    "Architecture Overview",
    "Module Map",
    "Key Abstractions",
    "API Surface",
    "Integration Points",
    "Data Flow",
    "Conventions",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS context_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section TEXT NOT NULL,
    file_path TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'llm',
    content_hash TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(section, file_path, content)
);
CREATE INDEX IF NOT EXISTS idx_context_entries_section ON context_entries(section);
CREATE INDEX IF NOT EXISTS idx_context_entries_file_path ON context_entries(file_path);
"""


def _db_path(repo_root: str) -> Path:
    p = Path(repo_root) / ".lean_ai"
    p.mkdir(parents=True, exist_ok=True)
    return p / "context.db"


async def _ensure_columns(db: aiosqlite.Connection) -> None:
    """Add any missing columns to the context_entries table.

    Idempotent: silently ignores errors when a column already exists.
    """
    try:
        await db.execute(
            "ALTER TABLE context_entries ADD COLUMN content_hash TEXT"
        )
    except Exception:
        pass  # Column already exists


async def _unique_index_columns(db: aiosqlite.Connection) -> list[list[str]]:
    """Return column names for every unique index on context_entries."""
    cursor = await db.execute("PRAGMA index_list(context_entries)")
    indexes = await cursor.fetchall()
    unique_columns: list[list[str]] = []
    for row in indexes:
        if not row["unique"]:
            continue
        index_name = str(row["name"]).replace("'", "''")
        index_cursor = await db.execute(f"PRAGMA index_info('{index_name}')")
        columns = [info["name"] for info in await index_cursor.fetchall()]
        unique_columns.append(columns)
    return unique_columns


async def _ensure_unique_constraint(db: aiosqlite.Connection) -> None:
    """Migrate old DBs that only allowed one entry per section/file."""
    unique_columns = await _unique_index_columns(db)
    has_current_unique = ["section", "file_path", "content"] in unique_columns
    has_old_unique = ["section", "file_path"] in unique_columns
    if has_current_unique or not has_old_unique:
        return

    await db.execute("ALTER TABLE context_entries RENAME TO context_entries_old")
    await db.executescript(_SCHEMA)
    await db.execute(
        "INSERT OR IGNORE INTO context_entries "
        "(id, section, file_path, content, source, content_hash, updated_at) "
        "SELECT id, section, file_path, content, source, content_hash, updated_at "
        "FROM context_entries_old"
    )
    await db.execute("DROP TABLE context_entries_old")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_context_entries_section "
        "ON context_entries(section)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_context_entries_file_path "
        "ON context_entries(file_path)"
    )
    await db.commit()


async def get_context_db(repo_root: str) -> SQLiteConnection:
    """Open (or create) the per-workspace context database."""
    db = await connect_sqlite(str(_db_path(repo_root)))
    db.row_factory = SQLITE_ROW_FACTORY
    await db.execute("PRAGMA journal_mode = WAL")
    await db.execute("PRAGMA busy_timeout = 5000")
    await db.executescript(_SCHEMA)
    await _ensure_columns(db)
    await _ensure_unique_constraint(db)
    await db.commit()
    return db


def _entry_content_hash(section: str, file_path: str, content: str, source: str) -> str:
    """Derive a stable fallback hash for legacy 4-field entries."""
    raw = f"{section}\0{file_path}\0{content}\0{source}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_entry(
    entry: tuple[str, ...],
) -> tuple[str, str, str, str, str]:
    """Accept legacy 4-tuples and current 5-tuples for context entries."""
    if len(entry) == 5:
        section, file_path, content, source, content_hash = entry
        return section, file_path, content, source, content_hash
    if len(entry) == 4:
        section, file_path, content, source = entry
        return (
            section,
            file_path,
            content,
            source,
            _entry_content_hash(section, file_path, content, source),
        )
    raise ValueError("context entries must contain 4 or 5 fields")


async def upsert_entries_batch(
    db: aiosqlite.Connection,
    entries: list[tuple[str, str, str, str] | tuple[str, str, str, str, str]],
) -> int:
    """Batch insert context entries.

    Accepts current ``(section, file_path, content, source, content_hash)``
    tuples and legacy 4-tuples. Exact duplicate facts are ignored; distinct
    facts from the same file and section are preserved.
    """
    if not entries:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    before = db.total_changes
    normalized = [_normalize_entry(tuple(entry)) for entry in entries]
    await db.executemany(
        "INSERT OR IGNORE INTO context_entries "
        "(section, file_path, content, source, content_hash, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(s, fp, c, src, ch, now) for s, fp, c, src, ch in normalized],
    )
    await db.commit()
    return db.total_changes - before


async def delete_entries_for_file(
    db: aiosqlite.Connection,
    file_path: str,
    source: str | None = None,
) -> int:
    """Delete all entries for a file.  Optionally filter by source."""
    if source:
        await db.execute(
            "DELETE FROM context_entries WHERE file_path = ? AND source = ?",
            (file_path, source),
        )
    else:
        await db.execute(
            "DELETE FROM context_entries WHERE file_path = ?",
            (file_path,),
        )
    await db.commit()
    return db.total_changes


async def clear_all(db: aiosqlite.Connection) -> None:
    """Delete all entries (full regeneration)."""
    await db.execute("DELETE FROM context_entries")
    await db.commit()


async def get_existing_hashes(
    db: aiosqlite.Connection,
) -> dict[str, str]:
    """Return file_path to content_hash for existing entries with hashes.

    Testable seam: returns a pure dict that can be mocked in tests to
    simulate cached vs uncached states.
    """
    cursor = await db.execute(
        "SELECT file_path, content_hash FROM context_entries "
        "WHERE source = 'llm' AND content_hash IS NOT NULL "
        "ORDER BY updated_at DESC"
    )
    rows = await cursor.fetchall()
    hashes: dict[str, str] = {}
    for row in rows:
        file_path = row[0]
        if file_path not in hashes:
            hashes[file_path] = row[1]
    return hashes


async def query_entries(
    db: aiosqlite.Connection,
    *,
    section: str | None = None,
    file_path: str | None = None,
    keyword: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Query context entries with optional filters.

    All filters are optional; when none are provided returns all entries
    (up to *limit*).
    """
    conditions: list[str] = []
    values: list[str | int] = []

    if section:
        conditions.append("section = ?")
        values.append(section)
    if file_path:
        conditions.append("file_path LIKE ?")
        values.append(f"%{file_path}%")
    if keyword:
        conditions.append("content LIKE ?")
        values.append(f"%{keyword}%")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    cursor = await db.execute(
        f"SELECT section, file_path, content, source "
        f"FROM context_entries {where} "
        f"ORDER BY section, file_path LIMIT ?",
        [*values, limit],
    )
    return [dict(r) for r in await cursor.fetchall()]


async def export_to_markdown(db: aiosqlite.Connection) -> str:
    """Export all entries to a Markdown document grouped by section."""
    sections: list[str] = ["# Project Context\n"]

    for section_name in _SECTION_ORDER:
        cursor = await db.execute(
            "SELECT content FROM context_entries WHERE section = ? ORDER BY source, file_path",
            (section_name,),
        )
        rows = await cursor.fetchall()

        if not rows:
            sections.append(f"## {section_name}\n\nNo data extracted yet.")
            continue

        lines = [f"## {section_name}\n"]
        seen: set[str] = set()
        for row in rows:
            text = row["content"]
            # Inline dedup: skip exact duplicate lines.
            if text in seen:
                continue
            seen.add(text)
            # Ensure bullet prefix.
            if not text.startswith("- "):
                lines.append(f"- {text}")
            else:
                lines.append(text)
        sections.append("\n".join(lines))

    return "\n\n".join(sections) + "\n"


async def get_stats(db: aiosqlite.Connection) -> dict:
    """Return summary statistics for the context DB."""
    cursor = await db.execute(
        "SELECT section, COUNT(*) as cnt FROM context_entries GROUP BY section",
    )
    section_counts = {row["section"]: row["cnt"] for row in await cursor.fetchall()}
    cursor2 = await db.execute(
        "SELECT COUNT(DISTINCT file_path) as files FROM context_entries",
    )
    row = await cursor2.fetchone()
    return {
        "total_entries": sum(section_counts.values()),
        "unique_files": row["files"] if row else 0,
        "sections": section_counts,
    }

"""SQLite persistence for project context entries.

Per-workspace database at ``.lean_ai/context.db``, separate from the main
``lean_ai.db``.  Uses WAL mode for parallel-safe writes during file-by-file
extraction.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

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

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS context_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section TEXT NOT NULL,
    file_path TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'llm',
    updated_at TEXT NOT NULL,
    UNIQUE(section, file_path, content)
);

CREATE INDEX IF NOT EXISTS idx_context_section
    ON context_entries(section);
CREATE INDEX IF NOT EXISTS idx_context_file
    ON context_entries(file_path);
"""


def _db_path(repo_root: str) -> Path:
    p = Path(repo_root) / ".lean_ai"
    p.mkdir(parents=True, exist_ok=True)
    return p / "context.db"


async def get_context_db(repo_root: str) -> aiosqlite.Connection:
    """Open (or create) the per-workspace context database."""
    db = await aiosqlite.connect(str(_db_path(repo_root)))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode = WAL")
    await db.execute("PRAGMA busy_timeout = 5000")
    await db.executescript(_SCHEMA)
    return db


async def upsert_entries_batch(
    db: aiosqlite.Connection,
    entries: list[tuple[str, str, str, str]],
) -> int:
    """Batch insert entries: ``[(section, file_path, content, source), ...]``.

    Uses ``INSERT OR IGNORE`` — exact duplicates (same section + file_path +
    content) are silently skipped.  Returns count of new entries inserted.
    """
    if not entries:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    before = db.total_changes
    await db.executemany(
        "INSERT OR IGNORE INTO context_entries "
        "(section, file_path, content, source, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [(s, fp, c, src, now) for s, fp, c, src in entries],
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
            "SELECT content FROM context_entries "
            "WHERE section = ? ORDER BY source, file_path",
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
        "SELECT section, COUNT(*) as cnt "
        "FROM context_entries GROUP BY section",
    )
    section_counts = {
        row["section"]: row["cnt"] for row in await cursor.fetchall()
    }
    cursor2 = await db.execute(
        "SELECT COUNT(DISTINCT file_path) as files FROM context_entries",
    )
    row = await cursor2.fetchone()
    return {
        "total_entries": sum(section_counts.values()),
        "unique_files": row["files"] if row else 0,
        "sections": section_counts,
    }

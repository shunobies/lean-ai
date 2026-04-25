"""Global notes persistence via aiosqlite.

Cross-project note storage at ~/.lean_ai/notes/notes.db.
Notes are categorized by project and can have associated TODOs.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

_NOTES_DIR = Path.home() / ".lean_ai" / "notes"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    project TEXT,
    tags TEXT,
    source_workspace TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS note_todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


def _db_path() -> Path:
    """Global notes database path."""
    _NOTES_DIR.mkdir(parents=True, exist_ok=True)
    return _NOTES_DIR / "notes.db"


async def get_notes_db() -> aiosqlite.Connection:
    """Open (or create) the global notes database."""
    db = await aiosqlite.connect(str(_db_path()))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode = WAL")
    await db.execute("PRAGMA busy_timeout = 5000")
    await db.execute("PRAGMA foreign_keys = ON")
    await db.executescript(_SCHEMA)
    return db


# ── Note CRUD ──


def _format_note(row: dict) -> dict:
    """Format a note row for API responses."""
    tags = row.get("tags")
    if tags and isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except (json.JSONDecodeError, TypeError):
            tags = []
    return {
        "id": row["id"],
        "content": row["content"],
        "project": row.get("project"),
        "tags": tags or [],
        "source_workspace": row.get("source_workspace"),
        "created_at": row.get("created_at", ""),
        "updated_at": row.get("updated_at", ""),
    }


async def create_note(
    db: aiosqlite.Connection,
    content: str,
    source_workspace: str | None = None,
) -> dict:
    """Create a new note. Returns the formatted note dict."""
    note_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO notes (id, content, source_workspace, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (note_id, content, source_workspace, now, now),
    )
    await db.commit()
    return {
        "id": note_id,
        "content": content,
        "project": None,
        "tags": [],
        "source_workspace": source_workspace,
        "created_at": now,
        "updated_at": now,
    }


async def update_note(
    db: aiosqlite.Connection,
    note_id: str,
    *,
    content: str | None = None,
    project: str | None = ...,  # type: ignore[assignment]
    tags: list[str] | None = ...,  # type: ignore[assignment]
) -> dict | None:
    """Update note fields. Returns updated note or None if not found."""
    parts: list[str] = []
    values: list = []
    now = datetime.now(timezone.utc).isoformat()

    if content is not None:
        parts.append("content = ?")
        values.append(content)
    if project is not ...:
        parts.append("project = ?")
        values.append(project)
    if tags is not ...:
        parts.append("tags = ?")
        values.append(json.dumps(tags) if tags is not None else None)

    if not parts:
        return await get_note(db, note_id)

    parts.append("updated_at = ?")
    values.append(now)
    values.append(note_id)

    await db.execute(f"UPDATE notes SET {', '.join(parts)} WHERE id = ?", values)
    await db.commit()
    return await get_note(db, note_id)


async def delete_note(db: aiosqlite.Connection, note_id: str) -> bool:
    """Delete a note and its TODOs. Returns True if found."""
    cursor = await db.execute("SELECT id FROM notes WHERE id = ?", (note_id,))
    if not await cursor.fetchone():
        return False
    await db.execute("DELETE FROM note_todos WHERE note_id = ?", (note_id,))
    await db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    await db.commit()
    return True


async def get_note(db: aiosqlite.Connection, note_id: str) -> dict | None:
    """Fetch a single note with its TODOs."""
    cursor = await db.execute("SELECT * FROM notes WHERE id = ?", (note_id,))
    row = await cursor.fetchone()
    if not row:
        return None
    note = _format_note(dict(row))
    note["todos"] = await list_todos(db, note_id)
    return note


async def list_notes(
    db: aiosqlite.Connection,
    project: str | None = None,
) -> list[dict]:
    """List notes, optionally filtered by project. Newest first."""
    if project:
        cursor = await db.execute(
            "SELECT * FROM notes WHERE project = ? ORDER BY updated_at DESC",
            (project,),
        )
    else:
        cursor = await db.execute("SELECT * FROM notes ORDER BY updated_at DESC")
    rows = await cursor.fetchall()
    results = []
    for r in rows:
        note = _format_note(dict(r))
        note["todos"] = await list_todos(db, note["id"])
        results.append(note)
    return results


async def list_projects(db: aiosqlite.Connection) -> list[str]:
    """List distinct project categories."""
    cursor = await db.execute(
        "SELECT DISTINCT project FROM notes WHERE project IS NOT NULL ORDER BY project"
    )
    rows = await cursor.fetchall()
    return [row[0] for row in rows]


# ── TODO CRUD ──


async def create_todo(
    db: aiosqlite.Connection,
    note_id: str,
    description: str,
) -> dict:
    """Add a TODO to a note. Returns the todo dict."""
    now = datetime.now(timezone.utc).isoformat()
    cursor = await db.execute(
        "INSERT INTO note_todos (note_id, description, created_at) VALUES (?, ?, ?)",
        (note_id, description, now),
    )
    await db.commit()
    return {
        "id": cursor.lastrowid,
        "note_id": note_id,
        "description": description,
        "completed": False,
        "created_at": now,
    }


async def update_todo(
    db: aiosqlite.Connection,
    todo_id: int,
    *,
    description: str | None = None,
    completed: bool | None = None,
) -> dict | None:
    """Update a TODO item. Returns updated todo or None."""
    parts: list[str] = []
    values: list = []

    if description is not None:
        parts.append("description = ?")
        values.append(description)
    if completed is not None:
        parts.append("completed = ?")
        values.append(int(completed))

    if not parts:
        return await get_todo(db, todo_id)

    values.append(todo_id)
    await db.execute(f"UPDATE note_todos SET {', '.join(parts)} WHERE id = ?", values)
    await db.commit()
    return await get_todo(db, todo_id)


async def delete_todo(db: aiosqlite.Connection, todo_id: int) -> bool:
    """Delete a TODO. Returns True if found."""
    cursor = await db.execute("SELECT id FROM note_todos WHERE id = ?", (todo_id,))
    if not await cursor.fetchone():
        return False
    await db.execute("DELETE FROM note_todos WHERE id = ?", (todo_id,))
    await db.commit()
    return True


async def get_todo(db: aiosqlite.Connection, todo_id: int) -> dict | None:
    """Fetch a single TODO."""
    cursor = await db.execute("SELECT * FROM note_todos WHERE id = ?", (todo_id,))
    row = await cursor.fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "note_id": row["note_id"],
        "description": row["description"],
        "completed": bool(row["completed"]),
        "created_at": row["created_at"],
    }


async def list_todos(
    db: aiosqlite.Connection,
    note_id: str,
) -> list[dict]:
    """List all TODOs for a note."""
    cursor = await db.execute(
        "SELECT * FROM note_todos WHERE note_id = ? ORDER BY id ASC",
        (note_id,),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": row["id"],
            "note_id": row["note_id"],
            "description": row["description"],
            "completed": bool(row["completed"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


async def list_todos_by_project(
    db: aiosqlite.Connection,
    project: str | None = None,
    source_workspace: str | None = None,
    pending_only: bool = True,
) -> list[dict]:
    """List TODOs across notes, filtered by project or workspace.

    Returns todos enriched with their parent note's content snippet.
    """
    conditions = []
    values: list = []

    if project:
        conditions.append("n.project = ?")
        values.append(project)
    if source_workspace:
        conditions.append("n.source_workspace = ?")
        values.append(source_workspace)
    if pending_only:
        conditions.append("t.completed = 0")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    cursor = await db.execute(
        f"SELECT t.*, n.content AS note_content, n.project AS note_project "
        f"FROM note_todos t JOIN notes n ON t.note_id = n.id "
        f"{where} ORDER BY t.created_at DESC",
        values,
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": row["id"],
            "note_id": row["note_id"],
            "description": row["description"],
            "completed": bool(row["completed"]),
            "created_at": row["created_at"],
            "note_content": row["note_content"][:100],
            "note_project": row["note_project"],
        }
        for row in rows
    ]

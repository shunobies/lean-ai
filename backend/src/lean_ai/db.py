"""Minimal SQLite persistence via aiosqlite.

Two tables: sessions and tool_logs. No ORM — raw SQL for simplicity.
Database file lives at .lean_ai/lean_ai.db relative to the repo root.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    repo_root TEXT NOT NULL,
    task TEXT NOT NULL,
    plan TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    completed_at TEXT,
    branch_name TEXT,
    base_branch TEXT,
    stashed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tool_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    tool_name TEXT NOT NULL,
    parameters TEXT,
    result TEXT,
    success INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
"""


def _db_path(repo_root: str) -> Path:
    """Database lives inside .lean_ai/ in the target repo."""
    p = Path(repo_root) / ".lean_ai"
    p.mkdir(parents=True, exist_ok=True)
    return p / "lean_ai.db"


async def get_db(repo_root: str) -> aiosqlite.Connection:
    """Open (or create) the database and ensure schema exists."""
    db = await aiosqlite.connect(str(_db_path(repo_root)))
    db.row_factory = aiosqlite.Row
    await db.executescript(_SCHEMA)
    await _ensure_columns(db)
    return db


async def _ensure_columns(db: aiosqlite.Connection) -> None:
    """Add columns that may be missing from older databases."""
    new_columns = [
        ("sessions", "branch_name", "TEXT"),
        ("sessions", "base_branch", "TEXT"),
        ("sessions", "stashed", "INTEGER NOT NULL DEFAULT 0"),
    ]
    for table, col, col_type in new_columns:
        try:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
            await db.commit()
        except Exception:
            pass  # Column already exists


# ── Session helpers ──


async def create_session(db: aiosqlite.Connection, repo_root: str, task: str) -> str:
    """Create a new session, return its ID."""
    session_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO sessions (id, repo_root, task, status, created_at) "
        "VALUES (?, ?, ?, 'active', ?)",
        (session_id, repo_root, task, now),
    )
    await db.commit()
    return session_id


async def update_session(
    db: aiosqlite.Connection,
    session_id: str,
    *,
    plan: str | None = None,
    status: str | None = None,
    branch_name: str | None = None,
    base_branch: str | None = None,
    stashed: bool | None = None,
) -> None:
    """Update session fields."""
    parts: list[str] = []
    values: list[str] = []
    if plan is not None:
        parts.append("plan = ?")
        values.append(plan)
    if status is not None:
        parts.append("status = ?")
        values.append(status)
        if status in ("completed", "failed"):
            parts.append("completed_at = ?")
            values.append(datetime.now(timezone.utc).isoformat())
    if branch_name is not None:
        parts.append("branch_name = ?")
        values.append(branch_name)
    if base_branch is not None:
        parts.append("base_branch = ?")
        values.append(base_branch)
    if stashed is not None:
        parts.append("stashed = ?")
        values.append(int(stashed))
    if not parts:
        return
    values.append(session_id)
    await db.execute(f"UPDATE sessions SET {', '.join(parts)} WHERE id = ?", values)
    await db.commit()


def _format_session(row: dict) -> dict:
    """Map database row to the SessionSummary shape the frontend expects."""
    task = row.get("task", "")
    return {
        "session_id": row["id"],
        "title": task[:80] if task else None,
        "session_status": row.get("status", "active"),
        "workflow_stage": "completed" if row.get("status") == "completed" else "active",
        "task_track": None,
        "base_branch": row.get("base_branch"),
        "plan_branch": row.get("branch_name"),
        "merge_commit_sha": None,
        "created_at": row.get("created_at", ""),
        "updated_at": row.get("completed_at") or row.get("created_at", ""),
    }


async def get_session(db: aiosqlite.Connection, session_id: str) -> dict | None:
    """Fetch a single session as a dict."""
    cursor = await db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
    row = await cursor.fetchone()
    return _format_session(dict(row)) if row else None


async def list_sessions(db: aiosqlite.Connection) -> list[dict]:
    """List all sessions, newest first."""
    cursor = await db.execute("SELECT * FROM sessions ORDER BY created_at DESC")
    rows = await cursor.fetchall()
    return [_format_session(dict(r)) for r in rows]


# ── Tool log helpers ──


async def log_tool_call(
    db: aiosqlite.Connection,
    session_id: str,
    tool_name: str,
    parameters: dict,
    result: str,
    success: bool,
) -> None:
    """Record a tool invocation."""
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO tool_logs (session_id, tool_name, parameters, result, success, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, tool_name, json.dumps(parameters), result, int(success), now),
    )
    await db.commit()

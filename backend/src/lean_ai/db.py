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

CREATE TABLE IF NOT EXISTS conversation_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_name TEXT,
    tool_args TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_commits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    commit_sha TEXT NOT NULL,
    message TEXT,
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
        ("sessions", "merge_commit_sha", "TEXT"),
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
    merge_commit_sha: str | None = None,
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
        if status in ("merged", "abandoned"):
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
    if merge_commit_sha is not None:
        parts.append("merge_commit_sha = ?")
        values.append(merge_commit_sha)
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
        "merge_commit_sha": row.get("merge_commit_sha"),
        "created_at": row.get("created_at", ""),
        "updated_at": row.get("completed_at") or row.get("created_at", ""),
    }


async def get_session(db: aiosqlite.Connection, session_id: str) -> dict | None:
    """Fetch a single session as a dict (formatted for frontend)."""
    cursor = await db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
    row = await cursor.fetchone()
    return _format_session(dict(row)) if row else None


async def get_session_raw(db: aiosqlite.Connection, session_id: str) -> dict | None:
    """Fetch a single session as a raw dict (no field renaming).

    Use this for backend-internal operations (merge, abandon) that need
    the actual DB column names rather than the frontend-formatted shape.
    """
    cursor = await db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None


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


# ── Conversation log helpers ──


async def log_conversation_entry(
    db: aiosqlite.Connection,
    session_id: str,
    role: str,
    content: str,
    tool_name: str | None = None,
    tool_args: str | None = None,
) -> None:
    """Record a conversation entry (assistant thinking, tool call, or tool result)."""
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO conversation_logs "
        "(session_id, role, content, tool_name, tool_args, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, role, content, tool_name, tool_args, now),
    )
    await db.commit()


async def get_conversation_log(
    db: aiosqlite.Connection,
    session_id: str,
) -> list[dict]:
    """Retrieve the full conversation log for a session, oldest first."""
    cursor = await db.execute(
        "SELECT role, content, tool_name, tool_args, created_at "
        "FROM conversation_logs WHERE session_id = ? ORDER BY id ASC",
        (session_id,),
    )
    rows = await cursor.fetchall()
    return [
        {
            "role": row[0],
            "content": row[1],
            "tool_name": row[2],
            "tool_args": row[3],
            "created_at": row[4],
        }
        for row in rows
    ]


# ── Commit tracking ──


async def log_commit(
    db: aiosqlite.Connection,
    session_id: str,
    commit_sha: str,
    message: str = "",
) -> None:
    """Record a commit SHA associated with a session."""
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO session_commits (session_id, commit_sha, message, created_at) "
        "VALUES (?, ?, ?, ?)",
        (session_id, commit_sha, message, now),
    )
    await db.commit()


async def get_commits_for_session(
    db: aiosqlite.Connection,
    session_id: str,
) -> list[dict]:
    """List all commits associated with a session."""
    cursor = await db.execute(
        "SELECT commit_sha, message, created_at FROM session_commits "
        "WHERE session_id = ? ORDER BY id ASC",
        (session_id,),
    )
    rows = await cursor.fetchall()
    return [
        {"commit_sha": row[0], "message": row[1], "created_at": row[2]}
        for row in rows
    ]


async def find_session_by_commit(
    db: aiosqlite.Connection,
    sha_prefix: str,
) -> dict | None:
    """Find the session that produced a commit (prefix match)."""
    cursor = await db.execute(
        "SELECT s.* FROM sessions s "
        "JOIN session_commits sc ON s.id = sc.session_id "
        "WHERE sc.commit_sha LIKE ? LIMIT 1",
        (f"{sha_prefix}%",),
    )
    row = await cursor.fetchone()
    return _format_session(dict(row)) if row else None


# ── Session search ──


async def search_sessions(
    db: aiosqlite.Connection,
    query: str = "",
    commit_sha: str = "",
) -> list[dict]:
    """Search sessions by task text, plan content, conversation, or commit SHA."""
    seen: set[str] = set()
    results: list[dict] = []

    if commit_sha:
        cursor = await db.execute(
            "SELECT s.* FROM sessions s "
            "JOIN session_commits sc ON s.id = sc.session_id "
            "WHERE sc.commit_sha LIKE ? ORDER BY s.created_at DESC LIMIT 10",
            (f"{commit_sha}%",),
        )
        for row in await cursor.fetchall():
            d = dict(row)
            if d["id"] not in seen:
                seen.add(d["id"])
                results.append(_format_session(d))

    if query:
        q = f"%{query}%"
        # Search task and plan fields
        cursor = await db.execute(
            "SELECT * FROM sessions WHERE task LIKE ? OR plan LIKE ? "
            "ORDER BY created_at DESC LIMIT 20",
            (q, q),
        )
        for row in await cursor.fetchall():
            d = dict(row)
            if d["id"] not in seen:
                seen.add(d["id"])
                results.append(_format_session(d))

        # Search conversation logs
        cursor = await db.execute(
            "SELECT DISTINCT session_id FROM conversation_logs "
            "WHERE content LIKE ? LIMIT 20",
            (q,),
        )
        conv_session_ids = [row[0] for row in await cursor.fetchall()]
        for sid in conv_session_ids:
            if sid not in seen:
                cursor2 = await db.execute(
                    "SELECT * FROM sessions WHERE id = ?", (sid,),
                )
                row = await cursor2.fetchone()
                if row:
                    seen.add(sid)
                    results.append(_format_session(dict(row)))

    return results


# ── Session deletion ──


async def delete_session(db: aiosqlite.Connection, session_id: str) -> bool:
    """Delete a session and all associated data. Returns True if found."""
    cursor = await db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,))
    if not await cursor.fetchone():
        return False
    await db.execute("DELETE FROM session_commits WHERE session_id = ?", (session_id,))
    await db.execute("DELETE FROM conversation_logs WHERE session_id = ?", (session_id,))
    await db.execute("DELETE FROM tool_logs WHERE session_id = ?", (session_id,))
    await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    await db.commit()
    return True

"""Integration persistence — task links and sync state.

Global database at ~/.lean_ai/integrations/integrations.db.
Tracks which external tasks are linked to Lean AI sessions,
and sync status/history.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

_INTEGRATIONS_DIR = Path.home() / ".lean_ai" / "integrations"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_links (
    id TEXT PRIMARY KEY,
    integration_name TEXT NOT NULL,
    external_id TEXT NOT NULL,
    session_id TEXT,
    workspace TEXT,
    title TEXT,
    status TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(integration_name, external_id)
);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_link_id TEXT NOT NULL REFERENCES task_links(id),
    direction TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT,
    success INTEGER NOT NULL DEFAULT 1,
    error_message TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS integration_config (
    integration_name TEXT PRIMARY KEY,
    config TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _db_path() -> Path:
    """Global integrations database path."""
    _INTEGRATIONS_DIR.mkdir(parents=True, exist_ok=True)
    return _INTEGRATIONS_DIR / "integrations.db"


async def get_integrations_db() -> aiosqlite.Connection:
    """Open (or create) the global integrations database."""
    db = await aiosqlite.connect(str(_db_path()))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode = WAL")
    await db.execute("PRAGMA busy_timeout = 5000")
    await db.execute("PRAGMA foreign_keys = ON")
    await db.executescript(_SCHEMA)
    return db


# ── Task link CRUD ──


async def link_task(
    db: aiosqlite.Connection,
    integration_name: str,
    external_id: str,
    session_id: str | None = None,
    workspace: str | None = None,
    title: str | None = None,
    status: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Link an external task to a session or workspace."""
    link_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    meta_json = json.dumps(metadata) if metadata else None
    await db.execute(
        "INSERT OR REPLACE INTO task_links "
        "(id, integration_name, external_id, session_id, workspace, title, status, metadata, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            link_id,
            integration_name,
            external_id,
            session_id,
            workspace,
            title,
            status,
            meta_json,
            now,
            now,
        ),
    )
    await db.commit()
    return {
        "id": link_id,
        "integration_name": integration_name,
        "external_id": external_id,
        "session_id": session_id,
        "workspace": workspace,
        "title": title,
        "status": status,
        "created_at": now,
        "updated_at": now,
    }


async def unlink_task(db: aiosqlite.Connection, link_id: str) -> bool:
    """Remove a task link. Returns True if found."""
    cursor = await db.execute("SELECT id FROM task_links WHERE id = ?", (link_id,))
    if not await cursor.fetchone():
        return False
    await db.execute("DELETE FROM sync_log WHERE task_link_id = ?", (link_id,))
    await db.execute("DELETE FROM task_links WHERE id = ?", (link_id,))
    await db.commit()
    return True


async def get_linked_tasks(
    db: aiosqlite.Connection,
    workspace: str | None = None,
    session_id: str | None = None,
    integration_name: str | None = None,
) -> list[dict]:
    """List linked tasks, optionally filtered."""
    conditions = []
    values: list = []
    if workspace:
        conditions.append("workspace = ?")
        values.append(workspace)
    if session_id:
        conditions.append("session_id = ?")
        values.append(session_id)
    if integration_name:
        conditions.append("integration_name = ?")
        values.append(integration_name)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    cursor = await db.execute(
        f"SELECT * FROM task_links {where} ORDER BY updated_at DESC",
        values,
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def update_task_link(
    db: aiosqlite.Connection,
    link_id: str,
    *,
    session_id: str | None = ...,  # type: ignore[assignment]
    status: str | None = ...,  # type: ignore[assignment]
    title: str | None = ...,  # type: ignore[assignment]
) -> dict | None:
    """Update a task link. Returns updated link or None."""
    parts: list[str] = []
    values: list = []
    now = datetime.now(timezone.utc).isoformat()

    if session_id is not ...:
        parts.append("session_id = ?")
        values.append(session_id)
    if status is not ...:
        parts.append("status = ?")
        values.append(status)
    if title is not ...:
        parts.append("title = ?")
        values.append(title)

    if not parts:
        cursor = await db.execute("SELECT * FROM task_links WHERE id = ?", (link_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    parts.append("updated_at = ?")
    values.append(now)
    values.append(link_id)

    await db.execute(
        f"UPDATE task_links SET {', '.join(parts)} WHERE id = ?",
        values,
    )
    await db.commit()
    cursor = await db.execute("SELECT * FROM task_links WHERE id = ?", (link_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None


# ── Sync log ──


async def log_sync_event(
    db: aiosqlite.Connection,
    task_link_id: str,
    direction: str,
    event_type: str,
    payload: dict | None = None,
    success: bool = True,
    error_message: str | None = None,
) -> int:
    """Log a sync event. Returns the log ID."""
    now = datetime.now(timezone.utc).isoformat()
    payload_json = json.dumps(payload) if payload else None
    cursor = await db.execute(
        "INSERT INTO sync_log "
        "(task_link_id, direction, event_type, payload, success, error_message, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (task_link_id, direction, event_type, payload_json, int(success), error_message, now),
    )
    await db.commit()
    return cursor.lastrowid or 0


# ── Integration config ──


async def save_integration_config(
    db: aiosqlite.Connection,
    integration_name: str,
    config: dict,
    enabled: bool = True,
) -> dict:
    """Save or update integration configuration."""
    now = datetime.now(timezone.utc).isoformat()
    config_json = json.dumps(config)
    await db.execute(
        "INSERT OR REPLACE INTO integration_config "
        "(integration_name, config, enabled, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (integration_name, config_json, int(enabled), now, now),
    )
    await db.commit()
    return {
        "integration_name": integration_name,
        "config": config,
        "enabled": enabled,
        "updated_at": now,
    }


async def get_integration_config(
    db: aiosqlite.Connection,
    integration_name: str,
) -> dict | None:
    """Fetch integration configuration."""
    cursor = await db.execute(
        "SELECT * FROM integration_config WHERE integration_name = ?",
        (integration_name,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    result = dict(row)
    try:
        result["config"] = json.loads(result["config"])
    except (json.JSONDecodeError, TypeError):
        result["config"] = {}
    result["enabled"] = bool(result["enabled"])
    return result

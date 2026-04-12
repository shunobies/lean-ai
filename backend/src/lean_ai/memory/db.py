"""CRUD operations for session memories.

Memories are stored in the per-workspace SQLite DB (session_memories table)
alongside sessions, tool_logs, and conversation_logs.
"""

import json
import logging
import uuid
from datetime import datetime, timezone

import aiosqlite

logger = logging.getLogger(__name__)


async def create_memory(
    db: aiosqlite.Connection,
    session_id: str,
    category: str,
    content: str,
    tags: list[str] | None = None,
    source_task: str | None = None,
) -> dict:
    """Create a session memory. Returns the formatted memory dict."""
    memory_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO session_memories "
        "(id, session_id, category, content, tags, source_task, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            memory_id,
            session_id,
            category,
            content,
            json.dumps(tags) if tags else None,
            source_task,
            now,
        ),
    )
    await db.commit()
    return {
        "id": memory_id,
        "session_id": session_id,
        "category": category,
        "content": content,
        "tags": tags or [],
        "source_task": source_task,
        "created_at": now,
    }


async def list_memories(
    db: aiosqlite.Connection,
    category: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """List memories, optionally filtered by category. Newest first."""
    if category:
        cursor = await db.execute(
            "SELECT * FROM session_memories WHERE category = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (category, limit),
        )
    else:
        cursor = await db.execute(
            "SELECT * FROM session_memories ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
    rows = await cursor.fetchall()
    return [_format_memory(dict(r)) for r in rows]


async def get_memories_for_session(
    db: aiosqlite.Connection,
    session_id: str,
) -> list[dict]:
    """Get all memories extracted from a specific session."""
    cursor = await db.execute(
        "SELECT * FROM session_memories WHERE session_id = ? "
        "ORDER BY created_at ASC",
        (session_id,),
    )
    rows = await cursor.fetchall()
    return [_format_memory(dict(r)) for r in rows]


async def delete_memory(db: aiosqlite.Connection, memory_id: str) -> bool:
    """Delete a memory. Returns True if found."""
    cursor = await db.execute(
        "SELECT id FROM session_memories WHERE id = ?", (memory_id,)
    )
    if not await cursor.fetchone():
        return False
    await db.execute("DELETE FROM session_memories WHERE id = ?", (memory_id,))
    await db.commit()
    return True


def _format_memory(row: dict) -> dict:
    """Format a memory row for API/internal use."""
    tags = row.get("tags")
    if tags and isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except (json.JSONDecodeError, TypeError):
            tags = []
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "category": row["category"],
        "content": row["content"],
        "tags": tags or [],
        "source_task": row.get("source_task"),
        "created_at": row.get("created_at", ""),
    }

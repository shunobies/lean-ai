"""CRUD operations for session memories.

Memories are stored in the per-workspace SQLite DB (session_memories table)
alongside sessions, tool_logs, and conversation_logs.
"""

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

import aiosqlite

logger = logging.getLogger(__name__)


_VALID_STATUSES = {
    "auto",
    "user_confirmed",
    "user_rejected",
    "superseded",
    "high_confidence_auto",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_for_similarity(content: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for equality-ish
    similarity matching used by auto-promotion. Keeps alphanumerics and spaces."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", content.lower())).strip()


async def create_memory(
    db: aiosqlite.Connection,
    session_id: str,
    category: str,
    content: str,
    tags: list[str] | None = None,
    source_task: str | None = None,
    *,
    curation_status: str = "auto",
    confidence: float = 0.5,
    source_phase: str | None = None,
    model_name: str | None = None,
    expires_at: str | None = None,
) -> dict:
    """Create a session memory. Returns the formatted memory dict."""
    memory_id = uuid.uuid4().hex[:12]
    now = _now()
    await db.execute(
        "INSERT INTO session_memories "
        "(id, session_id, category, content, tags, source_task, created_at, "
        " curation_status, confidence, expires_at, source_phase, model_name, "
        " seen_count, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
        (
            memory_id,
            session_id,
            category,
            content,
            json.dumps(tags) if tags else None,
            source_task,
            now,
            curation_status,
            confidence,
            expires_at,
            source_phase,
            model_name,
            now,
        ),
    )
    await db.commit()
    return _format_memory({
        "id": memory_id,
        "session_id": session_id,
        "category": category,
        "content": content,
        "tags": json.dumps(tags) if tags else None,
        "source_task": source_task,
        "created_at": now,
        "curation_status": curation_status,
        "confidence": confidence,
        "expires_at": expires_at,
        "source_phase": source_phase,
        "model_name": model_name,
        "seen_count": 1,
        "last_seen_at": now,
    })


async def list_memories(
    db: aiosqlite.Connection,
    category: str | None = None,
    limit: int = 50,
    *,
    curation_status: str | list[str] | None = None,
    include_expired: bool = False,
) -> list[dict]:
    """List memories, newest first. Optional filters by category and status."""
    clauses: list[str] = []
    params: list = []
    if category:
        clauses.append("category = ?")
        params.append(category)
    if curation_status:
        statuses = (
            [curation_status]
            if isinstance(curation_status, str)
            else list(curation_status)
        )
        placeholders = ",".join("?" for _ in statuses)
        clauses.append(f"curation_status IN ({placeholders})")
        params.extend(statuses)
    if not include_expired:
        clauses.append("(expires_at IS NULL OR expires_at > ?)")
        params.append(_now())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    cursor = await db.execute(
        f"SELECT * FROM session_memories {where} "
        f"ORDER BY created_at DESC LIMIT ?",
        tuple(params),
    )
    rows = await cursor.fetchall()
    return [_format_memory(dict(r)) for r in rows]


async def get_memory(
    db: aiosqlite.Connection, memory_id: str
) -> dict | None:
    """Fetch a single memory by id."""
    cursor = await db.execute(
        "SELECT * FROM session_memories WHERE id = ?", (memory_id,),
    )
    row = await cursor.fetchone()
    return _format_memory(dict(row)) if row else None


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


async def update_curation_status(
    db: aiosqlite.Connection,
    memory_id: str,
    status: str,
    *,
    confidence: float | None = None,
) -> bool:
    """Set curation_status (and optionally confidence). Returns True if found."""
    if status not in _VALID_STATUSES:
        raise ValueError(f"Invalid curation_status: {status}")
    cursor = await db.execute(
        "SELECT id FROM session_memories WHERE id = ?", (memory_id,),
    )
    if not await cursor.fetchone():
        return False
    if confidence is not None:
        await db.execute(
            "UPDATE session_memories SET curation_status = ?, confidence = ? "
            "WHERE id = ?",
            (status, confidence, memory_id),
        )
    else:
        await db.execute(
            "UPDATE session_memories SET curation_status = ? WHERE id = ?",
            (status, memory_id),
        )
    await db.commit()
    return True


async def find_similar_memory(
    db: aiosqlite.Connection,
    content: str,
    *,
    category: str | None = None,
) -> dict | None:
    """Find a memory with equivalent normalized content (for auto-promotion).

    Returns the first match or None. Scans candidates by category to keep the
    search bounded; equality is normalized (case, punctuation, whitespace).
    """
    target = _normalize_for_similarity(content)
    if not target:
        return None
    if category:
        cursor = await db.execute(
            "SELECT * FROM session_memories WHERE category = ? "
            "ORDER BY created_at DESC LIMIT 200",
            (category,),
        )
    else:
        cursor = await db.execute(
            "SELECT * FROM session_memories ORDER BY created_at DESC LIMIT 200"
        )
    rows = await cursor.fetchall()
    for row in rows:
        if _normalize_for_similarity(row["content"]) == target:
            return _format_memory(dict(row))
    return None


async def bump_seen_count(
    db: aiosqlite.Connection,
    memory_id: str,
    *,
    promote_threshold: int | None = None,
) -> dict | None:
    """Increment seen_count, refresh last_seen_at. Optionally auto-promote
    auto→high_confidence_auto when threshold reached. Returns updated row."""
    existing = await get_memory(db, memory_id)
    if not existing:
        return None
    new_count = (existing.get("seen_count") or 1) + 1
    now = _now()
    should_promote = (
        promote_threshold is not None
        and existing.get("curation_status") == "auto"
        and new_count >= promote_threshold
    )
    if should_promote:
        await db.execute(
            "UPDATE session_memories SET seen_count = ?, last_seen_at = ?, "
            "curation_status = 'high_confidence_auto', confidence = ? "
            "WHERE id = ?",
            (new_count, now, min(0.85, (existing.get("confidence") or 0.5) + 0.2),
             memory_id),
        )
    else:
        await db.execute(
            "UPDATE session_memories SET seen_count = ?, last_seen_at = ? "
            "WHERE id = ?",
            (new_count, now, memory_id),
        )
    await db.commit()
    return await get_memory(db, memory_id)


async def set_expiry_from_ttl(
    db: aiosqlite.Connection,
    memory_id: str,
    ttl_days: int,
) -> bool:
    """Set expires_at to created_at + ttl_days."""
    existing = await get_memory(db, memory_id)
    if not existing:
        return False
    created = datetime.fromisoformat(existing["created_at"])
    expires = (created + timedelta(days=ttl_days)).isoformat()
    await db.execute(
        "UPDATE session_memories SET expires_at = ? WHERE id = ?",
        (expires, memory_id),
    )
    await db.commit()
    return True


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
        "curation_status": row.get("curation_status", "auto"),
        "confidence": row.get("confidence", 0.5),
        "expires_at": row.get("expires_at"),
        "source_phase": row.get("source_phase"),
        "model_name": row.get("model_name"),
        "seen_count": row.get("seen_count", 1),
        "last_seen_at": row.get("last_seen_at"),
    }

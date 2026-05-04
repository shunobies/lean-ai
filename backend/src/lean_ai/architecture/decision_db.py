"""Helpers for durable architecture decisions stored in the workspace DB."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import aiosqlite


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [str(tag) for tag in value if str(tag).strip()]


def _encode_tags(tags: list[str] | None) -> str | None:
    if not tags:
        return None
    cleaned = [str(tag).strip() for tag in tags if str(tag).strip()]
    return json.dumps(cleaned) if cleaned else None


def _format_decision(row: aiosqlite.Row | dict | None) -> dict | None:
    if row is None:
        return None
    data = dict(row)
    data["tags"] = _parse_tags(data.get("tags"))
    return data


async def create_architecture_decision(
    db: aiosqlite.Connection,
    *,
    title: str,
    summary: str,
    rationale: str,
    status: str = "active",
    tags: list[str] | None = None,
    source_session_id: str | None = None,
    source_memory_id: str | None = None,
    source_plan_decision_ref: str | None = None,
) -> dict:
    """Create and return a durable architecture decision."""
    decision_id = uuid.uuid4().hex[:12]
    now = _now()
    await db.execute(
        """
        INSERT INTO architecture_decisions (
            id,
            title,
            summary,
            rationale,
            status,
            tags,
            source_session_id,
            source_memory_id,
            source_plan_decision_ref,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            decision_id,
            title,
            summary,
            rationale,
            status,
            _encode_tags(tags),
            source_session_id,
            source_memory_id,
            source_plan_decision_ref,
            now,
            now,
        ),
    )
    await db.commit()
    return {
        "id": decision_id,
        "title": title,
        "summary": summary,
        "rationale": rationale,
        "status": status,
        "tags": tags or [],
        "source_session_id": source_session_id,
        "source_memory_id": source_memory_id,
        "source_plan_decision_ref": source_plan_decision_ref,
        "created_at": now,
        "updated_at": now,
    }


async def get_architecture_decision(
    db: aiosqlite.Connection,
    decision_id: str,
) -> dict | None:
    """Fetch one architecture decision by id."""
    cursor = await db.execute(
        "SELECT * FROM architecture_decisions WHERE id = ?",
        (decision_id,),
    )
    row = await cursor.fetchone()
    return _format_decision(row)


async def list_architecture_decisions(
    db: aiosqlite.Connection,
    *,
    status: str | None = "active",
    query: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """List or search architecture decisions, newest first."""
    clauses: list[str] = []
    values: list[str | int] = []

    if status:
        clauses.append("status = ?")
        values.append(status)
    if query:
        like = f"%{query}%"
        clauses.append("(title LIKE ? OR summary LIKE ? OR rationale LIKE ? OR tags LIKE ?)")
        values.extend([like, like, like, like])

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    values.append(max(1, min(limit, 100)))
    cursor = await db.execute(
        f"""
        SELECT *
        FROM architecture_decisions
        {where_sql}
        ORDER BY updated_at DESC, created_at DESC
        LIMIT ?
        """,
        values,
    )
    rows = await cursor.fetchall()
    return [_format_decision(row) for row in rows if row is not None]


async def update_architecture_decision_status(
    db: aiosqlite.Connection,
    decision_id: str,
    *,
    status: str,
) -> dict | None:
    """Update a decision's status and return the fresh row."""
    now = _now()
    await db.execute(
        "UPDATE architecture_decisions SET status = ?, updated_at = ? WHERE id = ?",
        (status, now, decision_id),
    )
    await db.commit()
    return await get_architecture_decision(db, decision_id)


def render_architecture_decision_for_llm(decision: dict | None) -> str:
    """Format one decision as compact text for chat tools."""
    if not decision:
        return "Architecture decision not found."
    lines = [
        f"Decision: {decision['title']} [{decision.get('status', 'active')}]",
        f"ID: {decision['id']}",
        f"Summary: {decision['summary']}",
        f"Rationale: {decision['rationale']}",
    ]
    tags = decision.get("tags") or []
    if tags:
        lines.append(f"Tags: {', '.join(tags)}")
    if decision.get("source_session_id"):
        lines.append(f"Source session: {decision['source_session_id']}")
    if decision.get("source_memory_id"):
        lines.append(f"Source memory: {decision['source_memory_id']}")
    if decision.get("source_plan_decision_ref"):
        lines.append(f"Plan decision ref: {decision['source_plan_decision_ref']}")
    return "\n".join(lines)


def render_architecture_decisions_for_llm(
    decisions: list[dict],
    *,
    query: str | None = None,
) -> str:
    """Format many decisions as compact text for chat tools."""
    if not decisions:
        if query:
            return f"No architecture decisions found matching '{query}'."
        return "No architecture decisions recorded yet."

    header = (
        f"Architecture decisions matching '{query}' ({len(decisions)} results):"
        if query
        else f"Architecture decisions ({len(decisions)} results):"
    )
    lines = [header]
    for decision in decisions:
        line = (
            f"  [{decision['id']}] {decision['title']} "
            f"— {decision.get('status', 'active')}: {decision['summary']}"
        )
        tags = decision.get("tags") or []
        if tags:
            line += f" (tags: {', '.join(tags)})"
        if decision.get("source_session_id"):
            line += f" [session {decision['source_session_id']}]"
        lines.append(line)
    return "\n".join(lines)

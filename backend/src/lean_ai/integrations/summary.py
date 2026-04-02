"""Build session summaries from DB data for pushing to external services."""

from __future__ import annotations

import json
import logging
from datetime import datetime

from lean_ai.integrations.base import SessionSummary

logger = logging.getLogger(__name__)


async def build_session_summary(
    repo_root: str, session_id: str,
) -> SessionSummary | None:
    """Build a SessionSummary from existing session + tool_log + commit data."""
    from lean_ai.db import get_db

    db = await get_db(repo_root)
    try:
        # Fetch session
        cursor = await db.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,),
        )
        session = await cursor.fetchone()
        if not session:
            return None
        session = dict(session)

        # Fetch commits
        cursor = await db.execute(
            "SELECT commit_sha, message FROM session_commits WHERE session_id = ?",
            (session_id,),
        )
        commit_rows = await cursor.fetchall()
        commits = [
            {"sha": row["commit_sha"], "message": row.get("message", "")}
            for row in commit_rows
        ]

        # Calculate duration
        created = session.get("created_at", "")
        completed = session.get("completed_at", "")
        duration = 0.0
        if created and completed:
            try:
                t1 = datetime.fromisoformat(created)
                t2 = datetime.fromisoformat(completed)
                duration = (t2 - t1).total_seconds()
            except ValueError:
                pass

        # Count tool calls
        cursor = await db.execute(
            "SELECT COUNT(*) FROM tool_logs WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        tool_count = row[0] if row else 0

        # Extract files changed from tool logs
        cursor = await db.execute(
            "SELECT DISTINCT parameters FROM tool_logs "
            "WHERE session_id = ? AND tool_name IN ('create_file', 'edit_file')",
            (session_id,),
        )
        files: set[str] = set()
        for row in await cursor.fetchall():
            try:
                params = json.loads(row[0]) if row[0] else {}
                if "path" in params:
                    files.add(params["path"])
            except (json.JSONDecodeError, TypeError):
                pass

        return SessionSummary(
            session_id=session_id,
            task_description=session.get("task", ""),
            status=session.get("status", ""),
            branch_name=session.get("branch_name", ""),
            files_changed=sorted(files),
            commits=commits,
            duration_seconds=duration,
            tool_calls_count=tool_count,
            created_at=created,
            completed_at=completed,
        )
    finally:
        await db.close()

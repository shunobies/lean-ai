"""Build session summaries from DB data for pushing to external services."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from lean_ai.config import settings
from lean_ai.integrations.base import ModelUsage, SessionSummary

logger = logging.getLogger(__name__)

_LOCAL_PROVIDERS = {"ollama", "serve"}


def _is_local_provider(provider: str) -> bool:
    return provider.lower() in _LOCAL_PROVIDERS


def _extract_models_from_payload(payload: dict | None) -> list[ModelUsage]:
    if not payload:
        return []
    models: list[ModelUsage] = []
    for role in ("primary", "expert", "request", "worker"):
        provider = str(payload.get(f"{role}_provider") or "").strip().lower()
        model = str(payload.get(f"{role}_model") or "").strip()
        if not provider or not model:
            continue
        models.append(
            ModelUsage(
                role=role,
                provider=provider,
                model=model,
                is_local=_is_local_provider(provider),
            )
        )
    return models


def _runtime_model_usage() -> list[ModelUsage]:
    try:
        from lean_ai.routers.dependencies import (
            expert_llm_client,
            llm_client,
            request_llm_client,
            worker_llm_client,
        )
    except Exception:
        return []

    clients = {
        "primary": llm_client,
        "expert": expert_llm_client,
        "request": request_llm_client,
        "worker": worker_llm_client,
    }
    models: list[ModelUsage] = []
    for role, client in clients.items():
        if client is None:
            continue
        provider = str(getattr(client, "provider_name", "") or "").strip().lower()
        model = str(getattr(client, "model_name", "") or "").strip()
        if not provider or not model:
            continue
        models.append(
            ModelUsage(
                role=role,
                provider=provider,
                model=model,
                is_local=_is_local_provider(provider),
            )
        )
    return models


async def _load_session_start_payload(repo_root: str, session_id: str) -> dict | None:
    if not settings.enable_training_capture:
        return None
    repo_path = Path(repo_root)
    if not repo_path.exists():
        return None

    from lean_ai.training.db import get_training_db

    db = await get_training_db(repo_root)
    try:
        cursor = await db.execute(
            "SELECT payload FROM workflow_events "
            "WHERE session_id = ? AND event_type = 'session_start' "
            "ORDER BY id DESC LIMIT 1",
            (session_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        raw_payload = row[0] if not isinstance(row, dict) else row.get("payload")
        if not raw_payload:
            return None
        return json.loads(raw_payload)
    except Exception:
        logger.debug("Could not load session_start payload for %s", session_id, exc_info=True)
        return None
    finally:
        await db.close()


async def build_session_summary(
    repo_root: str,
    session_id: str,
) -> SessionSummary | None:
    """Build a SessionSummary from existing session + tool_log + commit data."""
    from lean_ai.db import get_db

    db = await get_db(repo_root)
    try:
        # Fetch session
        cursor = await db.execute(
            "SELECT * FROM sessions WHERE id = ?",
            (session_id,),
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
            {"sha": row["commit_sha"], "message": row.get("message", "")} for row in commit_rows
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

        session_start = await _load_session_start_payload(repo_root, session_id)
        models_used = _extract_models_from_payload(session_start)
        if not models_used:
            models_used = _runtime_model_usage()

        return SessionSummary(
            session_id=session_id,
            task_description=session.get("task", ""),
            status=session.get("status", ""),
            workflow_mode=(session_start or {}).get("mode", ""),
            branch_name=session.get("branch_name", ""),
            files_changed=sorted(files),
            commits=commits,
            models_used=models_used,
            duration_seconds=duration,
            tool_calls_count=tool_count,
            created_at=created,
            completed_at=completed,
        )
    finally:
        await db.close()

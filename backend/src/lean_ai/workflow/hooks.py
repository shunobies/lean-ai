"""Fire-and-forget hooks for the workflow pipeline.

Most fire as background tasks after plan execution completes. The
phase-specific hooks (``on_plan_decision``, ``on_validation_attempt_complete``)
fire mid-workflow to capture failure/success signals that would otherwise
be thrown away.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from fastapi import WebSocket

from lean_ai.config import settings
from lean_ai.llm.plan_schema import ExecutionPlan, plan_to_markdown

if TYPE_CHECKING:
    from lean_ai.llm.facade import LLMClient

logger = logging.getLogger(__name__)


def _make_memory_notifier(ws: WebSocket | None) -> Callable[[dict], Awaitable[None]] | None:
    """Build an ``on_memory_created`` callback that fires a `memory_suggested`
    WS message so the extension can surface the memory inline for confirmation.

    Returns None when ws is None (extraction runs but no notification).
    """
    if ws is None:
        return None
    from lean_ai.workflow.ws_messages import fire_memory_suggested

    async def _notify(memory: dict) -> None:
        # Only surface auto memories — confirmed/rejected don't need a chip
        if memory.get("curation_status") != "auto":
            return
        try:
            fire_memory_suggested(ws, memory=memory)
        except Exception:
            logger.debug("memory_suggested fire failed (non-fatal)", exc_info=True)

    return _notify


async def auto_push_integration(repo_root: str, session_id: str) -> None:
    """Push session summary to any linked external tasks (best-effort)."""
    try:
        from lean_ai.integrations.db import (
            get_integrations_db,
            get_linked_tasks,
        )
        from lean_ai.integrations.registry import get_integration
        from lean_ai.integrations.summary import build_session_summary

        db = await get_integrations_db()
        try:
            links = await get_linked_tasks(db, session_id=session_id)
        finally:
            await db.close()

        if not links:
            return

        summary = await build_session_summary(repo_root, session_id)
        if not summary:
            return

        for link in links:
            integration = get_integration(link["integration_name"])
            if integration:
                try:
                    await integration.push_session_summary(
                        link["external_id"], summary,
                    )
                    logger.info(
                        "Auto-pushed session %s to %s/%s",
                        session_id,
                        link["integration_name"],
                        link["external_id"],
                    )
                except Exception:
                    logger.debug(
                        "Auto-push failed for %s/%s",
                        link["integration_name"],
                        link["external_id"],
                        exc_info=True,
                    )
    except Exception:
        logger.debug(
            "Auto-push integration failed (non-fatal)", exc_info=True,
        )


async def auto_extract_session_memories(
    repo_root: str,
    session_id: str,
    task: str,
    plan: ExecutionPlan,
    llm_client: "LLMClient",
    files_modified: list[str],
    validation_results: dict,
) -> None:
    """Extract cross-session memories from a completed session (best-effort)."""
    try:
        from lean_ai.db import get_db
        from lean_ai.memory.extractor import (
            build_session_summary_for_extraction,
            schedule_extraction,
        )
        from lean_ai.routers.dependencies import worker_llm_client

        # Use worker model if available, fall back to primary
        extractor_llm = worker_llm_client or llm_client

        # Gather tool call stats and search findings from DB
        db = await get_db(repo_root)
        try:
            cursor = await db.execute(
                "SELECT tool_name, COUNT(*) as cnt, "
                "SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failures "
                "FROM tool_logs WHERE session_id = ? GROUP BY tool_name",
                (session_id,),
            )
            rows = await cursor.fetchall()
            tool_stats = {row[0]: row[1] for row in rows}
            failed_tools = [row[0] for row in rows if row[2] > 0]

            # Gather search/fetch results for extraction enrichment
            search_cursor = await db.execute(
                "SELECT tool_name, content FROM conversation_logs "
                "WHERE session_id = ? AND role = 'tool_result' "
                "AND tool_name IN ('search_internet', 'fetch_url') "
                "ORDER BY id ASC",
                (session_id,),
            )
            search_rows = await search_cursor.fetchall()
            search_findings: list[tuple[str, str]] = [
                (row[0], row[1]) for row in search_rows
            ]
        finally:
            await db.close()

        # Build summary for extraction
        plan_text = plan_to_markdown(plan) if plan else None
        validation_passed = all(
            r.get("success", True) for r in validation_results.values()
        ) if validation_results else True

        session_summary = build_session_summary_for_extraction(
            task=task,
            plan_text=plan_text,
            tool_stats=tool_stats,
            failed_tools=failed_tools,
            validation_passed=validation_passed,
            files_modified=files_modified,
            search_findings=search_findings,
        )

        schedule_extraction(
            extractor_llm, repo_root, session_id, session_summary, task,
        )
    except Exception:
        logger.debug(
            "Session memory extraction failed (non-fatal)", exc_info=True,
        )


async def on_plan_decision(
    repo_root: str,
    session_id: str,
    llm_client: "LLMClient",
    *,
    task: str,
    plan_before: str,
    feedback: str,
    plan_after: str | None,
    decision: str,
    ws: WebSocket | None = None,
) -> None:
    """Hook: user approved or rejected a plan.

    - decision="approved" after a prior rejection → extract `rejection` memory
      using the (plan_before, feedback, plan_after) triple. This is the
      strongest signal — user saw a bad plan, gave feedback, a revised plan
      satisfied them.
    - decision="rejected" (intermediate) → capture (plan_before, feedback)
      and defer extraction until the eventual approval. No-op here.
    - decision="approved" without prior rejection → no memory extraction.

    Fire-and-forget; exceptions logged and suppressed.
    """
    if not getattr(settings, "enable_session_memory", True):
        return
    if decision != "approved" or plan_before is None or not feedback:
        return
    try:
        from lean_ai.memory.extractor import schedule_plan_rejection_extraction
        from lean_ai.routers.dependencies import worker_llm_client

        extractor_llm = worker_llm_client or llm_client
        schedule_plan_rejection_extraction(
            extractor_llm,
            repo_root,
            session_id,
            task=task,
            plan_before=plan_before,
            feedback=feedback,
            plan_after=plan_after,
            on_memory_created=_make_memory_notifier(ws),
        )
        logger.info(
            "Queued plan-rejection memory extraction for session %s", session_id,
        )
    except Exception:
        logger.debug(
            "Plan-rejection memory hook failed (non-fatal)", exc_info=True,
        )


async def on_validation_attempt_complete(
    repo_root: str,
    session_id: str,
    llm_client: "LLMClient",
    *,
    task: str,
    attempt_num: int,
    failing_commands: list[str],
    error_output: str,
    diagnosis: str,
    fix_tool_calls: list[dict] | None,
    succeeded: bool,
    ws: WebSocket | None = None,
) -> None:
    """Hook: one validation fix-loop attempt completed.

    On success, extract a `fix_pattern` memory capturing (error → root-cause
    → fix). On failure, no memory (the next attempt may still succeed; we
    extract only when we have a known-good fix to learn from).
    """
    if not getattr(settings, "enable_session_memory", True):
        return
    if not succeeded:
        return
    try:
        from lean_ai.memory.extractor import schedule_fix_success_extraction
        from lean_ai.routers.dependencies import worker_llm_client

        extractor_llm = worker_llm_client or llm_client
        schedule_fix_success_extraction(
            extractor_llm,
            repo_root,
            session_id,
            task=task,
            failing_command=", ".join(failing_commands) or "(unknown)",
            error_output=error_output,
            diagnosis=diagnosis,
            fix_tool_calls=fix_tool_calls,
            on_memory_created=_make_memory_notifier(ws),
        )
        logger.info(
            "Queued fix-pattern memory extraction for session %s (attempt %d)",
            session_id, attempt_num,
        )
    except Exception:
        logger.debug(
            "Fix-pattern memory hook failed (non-fatal)", exc_info=True,
        )


def fire_plan_decision_hook(
    *,
    repo_root: str,
    session_id: str,
    llm_client: "LLMClient",
    task: str,
    plan_before: str,
    feedback: str,
    plan_after: str | None,
    decision: str,
    ws: WebSocket | None,
) -> None:
    """Fire-and-forget wrapper for on_plan_decision."""
    from lean_ai.workflow.callbacks import log_task_exception

    t = asyncio.create_task(on_plan_decision(
        repo_root, session_id, llm_client,
        task=task, plan_before=plan_before, feedback=feedback,
        plan_after=plan_after, decision=decision, ws=ws,
    ))
    t.add_done_callback(log_task_exception)


def fire_validation_attempt_hook(
    *,
    repo_root: str,
    session_id: str,
    llm_client: "LLMClient",
    task: str,
    attempt_num: int,
    failing_commands: list[str],
    error_output: str,
    diagnosis: str,
    fix_tool_calls: list[dict] | None,
    succeeded: bool,
    ws: WebSocket | None,
) -> None:
    """Fire-and-forget wrapper for on_validation_attempt_complete."""
    from lean_ai.workflow.callbacks import log_task_exception

    t = asyncio.create_task(on_validation_attempt_complete(
        repo_root, session_id, llm_client,
        task=task, attempt_num=attempt_num,
        failing_commands=failing_commands, error_output=error_output,
        diagnosis=diagnosis, fix_tool_calls=fix_tool_calls,
        succeeded=succeeded, ws=ws,
    ))
    t.add_done_callback(log_task_exception)

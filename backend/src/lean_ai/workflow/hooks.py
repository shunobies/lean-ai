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

from lean_ai.config import settings
from lean_ai.workflow.ws_protocol import WorkflowSession
from lean_ai.llm.plan_schema import ExecutionPlan, plan_to_markdown

if TYPE_CHECKING:
    from lean_ai.llm.facade import LLMClient

logger = logging.getLogger(__name__)


def _make_memory_notifier(session: WorkflowSession | None) -> Callable[[dict], Awaitable[None]] | None:
    """Build an ``on_memory_created`` callback that fires a `memory_suggested`
    WS message so the extension can surface the memory inline for confirmation.

    Returns None when ws is None (extraction runs but no notification).
    """
    if session is None:
        return None
    from lean_ai.workflow.ws_messages import fire_memory_suggested

    async def _notify(memory: dict) -> None:
        # Only surface auto memories — confirmed/rejected don't need a chip
        if memory.get("curation_status") != "auto":
            return
        try:
            fire_memory_suggested(session, memory=memory)
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
                        link["external_id"],
                        summary,
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
            "Auto-push integration failed (non-fatal)",
            exc_info=True,
        )


async def auto_extract_session_memories(
    repo_root: str,
    session_id: str,
    task: str,
    plan: ExecutionPlan,
    llm_client: "LLMClient",
    files_modified: list[str],
    validation_results: dict,
    ws: WorkflowSession | None = None,
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
            search_findings: list[tuple[str, str]] = [(row[0], row[1]) for row in search_rows]
        finally:
            await db.close()

        # Build summary for extraction
        plan_text = plan_to_markdown(plan) if plan else None
        validation_passed = (
            all(r.get("success", True) for r in validation_results.values())
            if validation_results
            else True
        )

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
            extractor_llm,
            repo_root,
            session_id,
            session_summary,
            task,
            on_memory_created=_make_memory_notifier(ws),
        )
    except Exception:
        logger.debug(
            "Session memory extraction failed (non-fatal)",
            exc_info=True,
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
    revision_count: int = 0,
    ws: WorkflowSession | None = None,
) -> None:
    """Hook: user approved or rejected a plan.

    Two jobs:

    1. **Training archive** (always): persist the decision row so
       exports can build DPO pairs from (rejected → approved) pairs.
    2. **Curated memory** (approval after prior rejection only): extract
       a ``rejection`` memory from the (plan_before, feedback, plan_after)
       triple so future planning avoids the same mistake.

    Fire-and-forget; exceptions logged and suppressed.
    """
    # 1. Training archive
    try:
        from lean_ai.training.capture import capture_plan_decision

        await capture_plan_decision(
            repo_root,
            session_id=session_id,
            revision_count=revision_count,
            task=task,
            plan_before=plan_before,
            plan_after=plan_after,
            feedback=feedback,
            decision=decision,
        )
    except Exception:
        logger.debug(
            "Plan-decision training capture failed (non-fatal)",
            exc_info=True,
        )

    # 2. Curated memory extraction — only on approval after rejection
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
            "Queued plan-rejection memory extraction for session %s",
            session_id,
        )
    except Exception:
        logger.debug(
            "Plan-rejection memory hook failed (non-fatal)",
            exc_info=True,
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
    failures_before: dict | None = None,
    failures_after: dict | None = None,
    ws: WorkflowSession | None = None,
) -> None:
    """Hook: one validation fix-loop attempt completed.

    1. **Training archive**: persist every attempt (both failed and
       succeeded) so exports can build DPO pairs from failure→success
       runs on the same error.
    2. **Curated memory**: on success only, extract a ``fix_pattern``
       memory capturing (error → root-cause → fix).
    """
    # 1. Training archive — every attempt, pass/fail alike.
    # Detect regression failures from the error output so exports can
    # weight fixes that correctly addressed regressions.
    regression_failure = False
    try:
        from lean_ai.tools.regression_guard import (
            extract_regression_paths_from_text,
        )

        regression_failure = bool(extract_regression_paths_from_text(error_output or ""))
    except Exception:
        regression_failure = False

    try:
        from lean_ai.training.capture import capture_validation_attempt

        await capture_validation_attempt(
            repo_root,
            session_id=session_id,
            attempt_num=attempt_num,
            failures_before=failures_before,
            diagnosis=diagnosis,
            fix_tool_calls=fix_tool_calls,
            failures_after=failures_after,
            succeeded=succeeded,
            regression_failure=regression_failure,
        )
    except Exception:
        logger.debug(
            "Validation-attempt training capture failed (non-fatal)",
            exc_info=True,
        )

    # 2. Curated memory — only on success
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
            session_id,
            attempt_num,
        )
    except Exception:
        logger.debug(
            "Fix-pattern memory hook failed (non-fatal)",
            exc_info=True,
        )


# ── Workflow event hooks (loop, context refresh, cancellation, etc.) ──


async def on_workflow_event(
    repo_root: str,
    session_id: str,
    *,
    event_type: str,
    payload: dict | None = None,
    trace_uuid: str | None = None,
) -> None:
    """Persist a workflow event to the training archive.

    Covers: ``loop_detected``, ``context_refresh``, ``reminder_injected``,
    ``claim_unverified``, ``cancellation``,
    ``execution_complete``, ``session_start``.
    """
    try:
        from lean_ai.training.capture import capture_workflow_event

        await capture_workflow_event(
            repo_root,
            session_id=session_id,
            event_type=event_type,
            payload=payload,
            trace_uuid=trace_uuid,
        )
    except Exception:
        logger.debug(
            "Workflow event capture failed (non-fatal, type=%s)",
            event_type,
            exc_info=True,
        )


def fire_workflow_event(
    *,
    repo_root: str,
    session_id: str,
    event_type: str,
    payload: dict | None = None,
    trace_uuid: str | None = None,
) -> None:
    """Fire-and-forget wrapper for on_workflow_event.

    Safe to call from sync code inside ``chat_with_tools`` / workflow
    hot paths — schedules on the running event loop and never blocks.
    """
    import asyncio

    from lean_ai.workflow.callbacks import log_task_exception

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    t = loop.create_task(
        on_workflow_event(
            repo_root,
            session_id,
            event_type=event_type,
            payload=payload,
            trace_uuid=trace_uuid,
        )
    )
    t.add_done_callback(log_task_exception)


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
    revision_count: int = 0,
    ws: WorkflowSession | None,
) -> None:
    """Fire-and-forget wrapper for on_plan_decision."""
    from lean_ai.workflow.callbacks import log_task_exception

    t = asyncio.create_task(
        on_plan_decision(
            repo_root,
            session_id,
            llm_client,
            task=task,
            plan_before=plan_before,
            feedback=feedback,
            plan_after=plan_after,
            decision=decision,
            revision_count=revision_count,
            ws=ws,
        )
    )
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
    failures_before: dict | None = None,
    failures_after: dict | None = None,
    ws: WorkflowSession | None,
) -> None:
    """Fire-and-forget wrapper for on_validation_attempt_complete."""
    from lean_ai.workflow.callbacks import log_task_exception

    t = asyncio.create_task(
        on_validation_attempt_complete(
            repo_root,
            session_id,
            llm_client,
            task=task,
            attempt_num=attempt_num,
            failing_commands=failing_commands,
            error_output=error_output,
            diagnosis=diagnosis,
            fix_tool_calls=fix_tool_calls,
            succeeded=succeeded,
            failures_before=failures_before,
            failures_after=failures_after,
            ws=ws,
        )
    )
    t.add_done_callback(log_task_exception)

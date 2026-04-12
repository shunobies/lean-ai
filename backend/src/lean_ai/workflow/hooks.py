"""Fire-and-forget post-completion hooks for the workflow pipeline.

These run as background tasks after plan execution completes.
"""

import logging
from typing import TYPE_CHECKING

from lean_ai.llm.plan_schema import ExecutionPlan, plan_to_markdown

if TYPE_CHECKING:
    from lean_ai.llm.facade import LLMClient

logger = logging.getLogger(__name__)


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

        # Gather tool call stats from DB
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
        )

        schedule_extraction(
            extractor_llm, repo_root, session_id, session_summary, task,
        )
    except Exception:
        logger.debug(
            "Session memory extraction failed (non-fatal)", exc_info=True,
        )

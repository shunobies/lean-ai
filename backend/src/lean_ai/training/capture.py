"""High-level capture functions called from workflow hooks.

Each function:

1. Runs :func:`scrub_payload` to strip PII/secrets.
2. Opens the workspace training DB.
3. Inserts the appropriate row(s) and audit entries.
4. Closes the DB.

Callers fire these via ``asyncio.create_task(...)`` so the hot path is
never blocked. The :func:`safe_capture` wrapper handles fail-closed
behaviour — if scrubbing raises and ``settings.scrubbing_strict`` is
True, the trace is dropped with a warning.
"""

from __future__ import annotations

import logging
from typing import Any

from lean_ai.config import settings
from lean_ai.training.db import (
    get_training_db,
    insert_plan_decision,
    insert_training_trace,
    insert_validation_attempt,
    insert_workflow_event,
    new_trace_uuid,
)
from lean_ai.training.scrubber import (
    ScrubberError,
    persist_audit_rows,
    scrub_payload,
)

logger = logging.getLogger(__name__)


def _is_enabled() -> bool:
    return bool(getattr(settings, "enable_training_capture", True))


async def _scrub_and_write(
    repo_root: str,
    *,
    writer,  # async fn(db, *, collect_audit) → row_id / trace_uuid
    audit_source_table: str,
    audit_source_id_factory,  # callable(payload_or_row_id) → str
    payload: dict,
) -> Any:
    """Common scaffolding: scrub → open DB → write → persist audit."""
    audit_rows: list[tuple[str, str, str]] = []

    def _collect(pattern: str, replacement: str, preview: str) -> None:
        audit_rows.append((pattern, replacement, preview))

    try:
        scrubbed = scrub_payload(payload, _collect)
    except ScrubberError:
        if settings.scrubbing_strict:
            logger.warning(
                "Scrubber failed on %s; dropping trace (fail-closed)",
                audit_source_table,
            )
            return None
        # Lenient: keep going, but mark scrubbed=False in the writer call
        scrubbed = payload

    db = await get_training_db(repo_root)
    try:
        result = await writer(db, scrubbed=scrubbed, audit_rows=audit_rows)
        if audit_rows:
            source_id = audit_source_id_factory(result)
            await persist_audit_rows(
                db,
                source_table=audit_source_table,
                source_id=source_id,
                rows=audit_rows,
            )
        return result
    finally:
        await db.close()


async def capture_turn(
    repo_root: str,
    *,
    session_id: str,
    phase: str,
    model_name: str,
    provider: str,
    messages: list,
    assistant_output: dict,
    outcome: str | None = None,
    pair_id: str | None = None,
    preference: int | None = None,
    pair_kind: str | None = None,
    tokens_prompt: int | None = None,
    tokens_completion: int | None = None,
    latency_ms: int | None = None,
) -> str | None:
    """Persist one LLM turn to ``training_traces``.

    Returns the ``trace_uuid`` or ``None`` if capture was dropped.
    """
    if not _is_enabled():
        return None

    payload = {
        "messages": messages,
        "assistant_output": assistant_output,
    }
    trace_uuid = new_trace_uuid()

    async def _write(db, *, scrubbed, audit_rows):
        await insert_training_trace(
            db,
            trace_uuid=trace_uuid,
            session_id=session_id,
            phase=phase,
            model_name=model_name,
            provider=provider,
            messages=scrubbed["messages"],
            assistant_output=scrubbed["assistant_output"],
            outcome=outcome,
            pair_id=pair_id,
            preference=preference,
            pair_kind=pair_kind,
            tokens_prompt=tokens_prompt,
            tokens_completion=tokens_completion,
            latency_ms=latency_ms,
            scrubbed=True,
        )
        return trace_uuid

    try:
        return await _scrub_and_write(
            repo_root,
            writer=_write,
            audit_source_table="training_traces",
            audit_source_id_factory=lambda _u: trace_uuid,
            payload=payload,
        )
    except Exception:
        logger.debug("capture_turn failed (non-fatal)", exc_info=True)
        return None


async def capture_plan_decision(
    repo_root: str,
    *,
    session_id: str,
    revision_count: int,
    task: str,
    plan_before: str | dict | None,
    plan_after: str | dict | None,
    feedback: str | None,
    decision: str,
    trace_uuid: str | None = None,
    pair_trace_uuid: str | None = None,
) -> int | None:
    """Persist a plan approve/reject/cancel decision."""
    if not _is_enabled():
        return None

    payload = {
        "task": task,
        "plan_before": plan_before,
        "plan_after": plan_after,
        "feedback": feedback,
    }

    async def _write(db, *, scrubbed, audit_rows):
        return await insert_plan_decision(
            db,
            session_id=session_id,
            revision_count=revision_count,
            task=scrubbed["task"],
            plan_before=scrubbed["plan_before"],
            plan_after=scrubbed["plan_after"],
            feedback=scrubbed["feedback"],
            decision=decision,
            trace_uuid=trace_uuid,
            pair_trace_uuid=pair_trace_uuid,
        )

    try:
        return await _scrub_and_write(
            repo_root,
            writer=_write,
            audit_source_table="plan_decisions",
            audit_source_id_factory=lambda row_id: f"pd-{row_id}",
            payload=payload,
        )
    except Exception:
        logger.debug("capture_plan_decision failed (non-fatal)", exc_info=True)
        return None


async def capture_validation_attempt(
    repo_root: str,
    *,
    session_id: str,
    attempt_num: int,
    failures_before: dict | None,
    diagnosis: str | None,
    fix_tool_calls: list | None,
    failures_after: dict | None,
    succeeded: bool,
    trace_uuid: str | None = None,
) -> int | None:
    """Persist a fix-loop attempt row."""
    if not _is_enabled():
        return None

    payload = {
        "failures_before": failures_before,
        "diagnosis": diagnosis,
        "fix_tool_calls": fix_tool_calls,
        "failures_after": failures_after,
    }

    async def _write(db, *, scrubbed, audit_rows):
        return await insert_validation_attempt(
            db,
            session_id=session_id,
            attempt_num=attempt_num,
            failures_before=scrubbed["failures_before"],
            diagnosis=scrubbed["diagnosis"],
            fix_tool_calls=scrubbed["fix_tool_calls"],
            failures_after=scrubbed["failures_after"],
            succeeded=succeeded,
            trace_uuid=trace_uuid,
        )

    try:
        return await _scrub_and_write(
            repo_root,
            writer=_write,
            audit_source_table="validation_attempts",
            audit_source_id_factory=lambda row_id: f"va-{row_id}",
            payload=payload,
        )
    except Exception:
        logger.debug(
            "capture_validation_attempt failed (non-fatal)", exc_info=True,
        )
        return None


async def capture_workflow_event(
    repo_root: str,
    *,
    session_id: str,
    event_type: str,
    payload: dict | None,
) -> int | None:
    """Persist a workflow event (loop / refresh / reminder / cancel / claim)."""
    if not _is_enabled():
        return None

    body = payload or {}

    async def _write(db, *, scrubbed, audit_rows):
        return await insert_workflow_event(
            db,
            session_id=session_id,
            event_type=event_type,
            payload=scrubbed,
        )

    try:
        return await _scrub_and_write(
            repo_root,
            writer=_write,
            audit_source_table="workflow_events",
            audit_source_id_factory=lambda row_id: f"we-{row_id}",
            payload=body,
        )
    except Exception:
        logger.debug(
            "capture_workflow_event failed (non-fatal)", exc_info=True,
        )
        return None

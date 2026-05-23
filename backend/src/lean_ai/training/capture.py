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
    insert_clarification,
    insert_diff_decision,
    insert_feedback,
    insert_phase2_synthesis,
    insert_plan_decision,
    insert_tool_compression,
    insert_tool_execution,
    insert_training_trace,
    insert_validation_attempt,
    insert_workflow_event,
    new_trace_uuid,
)
from lean_ai.training.span_context import trace_span
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
    role: str | None = None,
    turn_index: int | None = None,
    span_uuid: str | None = None,
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
            role=role,
            turn_index=turn_index,
        )
        if span_uuid is not None:
            await db.execute(
                "UPDATE training_traces SET span_uuid = ? WHERE trace_uuid = ?",
                (span_uuid, trace_uuid),
            )
            await db.commit()
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


# Cap stored tool-result previews to keep archive size bounded. Full
# outputs spill to `.lean_ai/tool_output/` anyway.
_TOOL_PREVIEW_CHARS = 4000


async def capture_tool_execution(
    repo_root: str,
    *,
    session_id: str,
    tool_name: str,
    arguments: dict | None,
    result: str,
    success: bool,
    latency_ms: int | None = None,
    phase: str | None = None,
    turn_index: int | None = None,
    trace_uuid: str | None = None,
    pair_id: str | None = None,
    preference: int | None = None,
    span_uuid: str | None = None,
) -> int | None:
    """Persist one tool invocation to ``tool_executions``.

    The result is truncated to ``_TOOL_PREVIEW_CHARS`` for the archive;
    full output is already spilled to disk by the workflow.
    """
    if not _is_enabled():
        return None

    preview = result[:_TOOL_PREVIEW_CHARS] if result else None
    payload = {"arguments": arguments, "result_preview": preview}

    async def _write(db, *, scrubbed, audit_rows):
        return await insert_tool_execution(
            db,
            session_id=session_id,
            trace_uuid=trace_uuid,
            phase=phase,
            turn_index=turn_index,
            tool_name=tool_name,
            arguments=scrubbed["arguments"],
            result_preview=scrubbed["result_preview"],
            result_length=len(result) if result else 0,
            success=success,
            latency_ms=latency_ms,
            pair_id=pair_id,
            preference=preference,
        )

    try:
        return await _scrub_and_write(
            repo_root,
            writer=_write,
            audit_source_table="tool_executions",
            audit_source_id_factory=lambda row_id: f"te-{row_id}",
            payload=payload,
        )
    except Exception:
        logger.debug(
            "capture_tool_execution failed (non-fatal)",
            exc_info=True,
        )
        return None


async def capture_tool_compression(
    repo_root: str,
    *,
    session_id: str,
    tool_name: str,
    raw_output: str,
    compressed_output: str,
    worker_model: str | None = None,
    worker_provider: str | None = None,
    phase: str | None = None,
    followup_progress: int | None = None,
) -> int | None:
    """Persist a worker compression pair to ``tool_compressions``.

    Emitted whenever the worker compression path runs — no-op today
    because the feature is off by default, but the capture is live so
    activating compression produces distillation data immediately.
    """
    if not _is_enabled():
        return None

    payload = {"raw_output": raw_output, "compressed_output": compressed_output}

    async def _write(db, *, scrubbed, audit_rows):
        return await insert_tool_compression(
            db,
            session_id=session_id,
            phase=phase,
            tool_name=tool_name,
            raw_output=scrubbed["raw_output"],
            compressed_output=scrubbed["compressed_output"],
            worker_model=worker_model,
            worker_provider=worker_provider,
            followup_progress=followup_progress,
        )

    try:
        return await _scrub_and_write(
            repo_root,
            writer=_write,
            audit_source_table="tool_compressions",
            audit_source_id_factory=lambda row_id: f"tc-{row_id}",
            payload=payload,
        )
    except Exception:
        logger.debug(
            "capture_tool_compression failed (non-fatal)",
            exc_info=True,
        )
        return None


async def capture_clarification(
    repo_root: str,
    *,
    session_id: str,
    question: str,
    answer: str | None,
    outcome: str,
    task: str | None = None,
    phase: str = "planning.phase1",
    trace_uuid: str | None = None,
) -> int | None:
    """Persist a Phase 1 clarification Q/A pair."""
    if not _is_enabled():
        return None
    payload = {"task": task, "question": question, "answer": answer}

    async def _write(db, *, scrubbed, audit_rows):
        return await insert_clarification(
            db,
            session_id=session_id,
            phase=phase,
            task=scrubbed["task"],
            question=scrubbed["question"],
            answer=scrubbed["answer"],
            outcome=outcome,
            trace_uuid=trace_uuid,
        )

    try:
        return await _scrub_and_write(
            repo_root,
            writer=_write,
            audit_source_table="clarifications",
            audit_source_id_factory=lambda row_id: f"cl-{row_id}",
            payload=payload,
        )
    except Exception:
        logger.debug(
            "capture_clarification failed (non-fatal)",
            exc_info=True,
        )
        return None


async def capture_phase2_synthesis(
    repo_root: str,
    *,
    session_id: str,
    task: str | None,
    scope: str | None,
    observations: list | None,
    scratchpad: str | None,
    journal: str | None,
    exploration_output: str | None,
    file_summary: dict | None,
    trace_uuid: str | None = None,
) -> int | None:
    """Persist Phase 2 (raw evidence → structured synthesis) pair."""
    if not _is_enabled():
        return None
    payload = {
        "task": task,
        "scope": scope,
        "observations": observations,
        "scratchpad": scratchpad,
        "journal": journal,
        "exploration_output": exploration_output,
        "file_summary": file_summary,
    }

    async def _write(db, *, scrubbed, audit_rows):
        return await insert_phase2_synthesis(
            db,
            session_id=session_id,
            task=scrubbed["task"],
            scope=scrubbed["scope"],
            observations=scrubbed["observations"],
            scratchpad=scrubbed["scratchpad"],
            journal=scrubbed["journal"],
            exploration_output=scrubbed["exploration_output"],
            file_summary=scrubbed["file_summary"],
            trace_uuid=trace_uuid,
        )

    try:
        return await _scrub_and_write(
            repo_root,
            writer=_write,
            audit_source_table="phase2_syntheses",
            audit_source_id_factory=lambda row_id: f"p2s-{row_id}",
            payload=payload,
        )
    except Exception:
        logger.debug(
            "capture_phase2_synthesis failed (non-fatal)",
            exc_info=True,
        )
        return None


async def capture_diff_decision(
    repo_root: str,
    *,
    session_id: str,
    file_path: str,
    accepted: bool,
    diff_hash: str | None = None,
    note: str | None = None,
    trace_uuid: str | None = None,
) -> int | None:
    """Persist a user's accept/reject decision on a proposed diff."""
    if not _is_enabled():
        return None
    payload = {"file_path": file_path, "note": note}

    async def _write(db, *, scrubbed, audit_rows):
        return await insert_diff_decision(
            db,
            session_id=session_id,
            file_path=scrubbed["file_path"],
            accepted=accepted,
            diff_hash=diff_hash,
            note=scrubbed["note"],
            trace_uuid=trace_uuid,
        )

    try:
        return await _scrub_and_write(
            repo_root,
            writer=_write,
            audit_source_table="diff_decisions",
            audit_source_id_factory=lambda row_id: f"dd-{row_id}",
            payload=payload,
        )
    except Exception:
        logger.debug(
            "capture_diff_decision failed (non-fatal)",
            exc_info=True,
        )
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
    regression_failure: bool = False,
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
            regression_failure=regression_failure,
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
            "capture_validation_attempt failed (non-fatal)",
            exc_info=True,
        )
        return None


async def capture_workflow_event(
    repo_root: str,
    *,
    session_id: str,
    event_type: str,
    payload: dict | None,
    trace_uuid: str | None = None,
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
            trace_uuid=trace_uuid,
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
            "capture_workflow_event failed (non-fatal)",
            exc_info=True,
        )
        return None


async def capture_feedback(
    repo_root: str,
    *,
    session_id: str,
    thumbs_up: bool | None = None,
    rating: int | None = None,
    comment: str | None = None,
    tags: list[str] | None = None,
    trace_span_uuid: str | None = None,
) -> int | None:
    """Persist user feedback to ``session_feedback``.

    Returns the feedback row id or ``None`` if capture was dropped.
    """
    if not _is_enabled():
        return None

    payload: dict[str, Any] = {
        "comment": comment,
        "tags": tags,
    }

    async def _write(db, *, scrubbed, audit_rows):
        return await insert_feedback(
            db,
            session_id=session_id,
            thumbs_up=thumbs_up,
            rating=rating,
            comment=scrubbed.get("comment"),
            tags=scrubbed.get("tags"),
            trace_span_uuid=trace_span_uuid,
        )

    try:
        return await _scrub_and_write(
            repo_root,
            writer=_write,
            audit_source_table="session_feedback",
            audit_source_id_factory=lambda row_id: f"sf-{row_id}",
            payload=payload,
        )
    except Exception:
        logger.debug(
            "capture_feedback failed (non-fatal)",
            exc_info=True,
        )
        return None


async def capture_span(
    span_type: str,
    span_name: str,
    session_id: str,
    parent_span: Any = None,
    metadata: dict | None = None,
):
    """Convenience wrapper around the ``trace_span`` context manager.

    Provides a fire-and-forget safe async context manager for creating
    timing spans that record LLM calls, tool invocations, and workflow
    phases into the ``trace_spans`` table.  Exceptions inside span
    bodies are caught, logged, and mark spans as failed without
    propagating to the caller.

    Args:
        span_type: Category of the operation (e.g. 'llm_call', 'tool_call').
        span_name: Descriptive name for the span.
        session_id: The session identifier.
        parent_span: Optional parent TraceSpan for hierarchical nesting.
        metadata: Optional dict of arbitrary metadata (stored as JSON).
    """
    async with trace_span(
        span_type=span_type,
        span_name=span_name,
        session_id=session_id,
        parent_span=parent_span,
        metadata=metadata,
    ) as span:
        yield span

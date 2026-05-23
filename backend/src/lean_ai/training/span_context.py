"""Async context manager for hierarchical trace spans.

Provides a fire-and-forget safe API for creating timing spans that
record LLM calls, tool invocations, and workflow phases into the
``trace_spans`` table.  Exceptions inside span bodies are caught,
logged at ERROR level, and mark the span as ``failed`` without
propagating to the caller — observability failures never break
business logic.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

from lean_ai.training.db import (
    get_training_db,
    insert_trace_span,
    update_trace_span_end,
)

logger = logging.getLogger(__name__)


@dataclass
class TraceSpan:
    """Represents a single timing span in the observability trace tree.

    Attributes:
        span_uuid: Unique identifier for this span.
        parent_span_uuid: UUID of the parent span, or None for root spans.
        session_id: The session this span belongs to.
        span_type: Category such as 'llm_call', 'tool_call', 'phase'.
        span_name: Human-readable label for the span.
        start_time: ISO-8601 timestamp when the span started.
        end_time: ISO-8601 timestamp when the span ended, or None while active.
        status: Current status — 'ok', 'failed', or None while active.
        metadata_json: Optional JSON string with arbitrary metadata.
    """

    span_uuid: str
    parent_span_uuid: Optional[str] = None
    session_id: str = ""
    span_type: str = ""
    span_name: str = ""
    start_time: str = ""
    end_time: Optional[str] = None
    status: Optional[str] = None
    metadata_json: Optional[str] = None


@asynccontextmanager
async def trace_span(
    span_type: str,
    span_name: str,
    session_id: str,
    parent_span: Optional[TraceSpan] = None,
    metadata: Optional[dict] = None,
) -> AsyncGenerator[TraceSpan, None]:
    """Async context manager that creates a trace span and records timing.

    On entry, inserts a row into ``trace_spans`` with status=None.
    On normal exit, updates end_time and sets status='ok'.
    On exception, logs at ERROR level, updates end_time and sets
    status='failed', and does NOT re-raise — the exception is
    swallowed so observability never breaks business logic.

    The outermost try/except around the DB calls ensures that even
    database write failures do not propagate — observability is
    fire-and-forget safe.

    Args:
        span_type: Category of the operation (e.g. 'llm_call', 'tool_call').
        span_name: Descriptive name for the span.
        session_id: The session identifier.
        parent_span: Optional parent TraceSpan for hierarchical nesting.
        metadata: Optional dict of arbitrary metadata (stored as JSON).

    Yields:
        A TraceSpan instance with start_time populated.
    """
    span_uuid = uuid.uuid4().hex
    parent_span_uuid = parent_span.span_uuid if parent_span is not None else None
    start = datetime.now(timezone.utc)
    start_iso = start.isoformat()

    span = TraceSpan(
        span_uuid=span_uuid,
        parent_span_uuid=parent_span_uuid,
        session_id=session_id,
        span_type=span_type,
        span_name=span_name,
        start_time=start_iso,
        metadata_json=None,
    )

    db = None
    try:
        db = await get_training_db(session_id)
        await insert_trace_span(
            db,
            span_uuid=span_uuid,
            session_id=session_id,
            span_type=span_type,
            span_name=span_name,
            start_time=start_iso,
            parent_span_uuid=parent_span_uuid,
            status=None,
            metadata=metadata,
        )
    except Exception:
        logger.error(
            "Failed to insert trace span %s (non-fatal)",
            span_uuid,
            exc_info=True,
        )

    try:
        yield span
    except Exception as exc:
        span.status = "failed"
        end_iso = datetime.now(timezone.utc).isoformat()
        span.end_time = end_iso
        logger.error(
            "Trace span %s (%s) failed: %s",
            span_uuid,
            span_name,
            exc,
            exc_info=True,
        )
        try:
            if db is not None:
                await update_trace_span_end(
                    db,
                    span_uuid=span_uuid,
                    end_time=end_iso,
                    status="failed",
                )
        except Exception:
            logger.error(
                "Failed to update trace span %s end (non-fatal)",
                span_uuid,
                exc_info=True,
            )
    else:
        end_iso = datetime.now(timezone.utc).isoformat()
        span.end_time = end_iso
        span.status = "ok"
        try:
            if db is not None:
                await update_trace_span_end(
                    db,
                    span_uuid=span_uuid,
                    end_time=end_iso,
                    status="ok",
                )
        except Exception:
            logger.error(
                "Failed to update trace span %s end (non-fatal)",
                span_uuid,
                exc_info=True,
            )
    finally:
        try:
            if db is not None:
                await db.close()
        except Exception:
            logger.error(
                "Failed to close training DB (non-fatal)",
                exc_info=True,
            )

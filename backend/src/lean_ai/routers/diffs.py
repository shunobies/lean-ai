"""Diff accept/reject decision endpoint.

The extension POSTs here when the user clicks accept/reject on a
proposed file change. The decision is persisted to the training
archive's ``diff_decisions`` table so exports can build preference
pairs (accepted vs rejected) per file edit.

See ``docs/training-ingestion.md`` for the full shape consumed by
``lean_ai_serve``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from lean_ai.training.capture import capture_diff_decision

logger = logging.getLogger(__name__)

diffs_router = APIRouter(prefix="/diffs", tags=["diffs"])


class DiffDecisionRequest(BaseModel):
    repo_root: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    file_path: str = Field(min_length=1)
    accepted: bool
    diff_hash: str | None = Field(
        default=None,
        description=(
            "Optional sha256(diff)[:16] emitted alongside the WS ``diff`` "
            "message. When supplied, lets exports pair this decision with "
            "the exact diff the model proposed."
        ),
    )
    note: str | None = Field(default=None, max_length=2000)
    trace_uuid: str | None = None


@diffs_router.post("/decision")
async def post_diff_decision(request: DiffDecisionRequest) -> dict:
    """Record a user accept/reject on a diff.

    Returns the archive row id on success. Safe to call when training
    capture is disabled — the capture helper returns ``None`` and this
    endpoint returns ``{"stored": False}``.
    """
    try:
        row_id = await capture_diff_decision(
            request.repo_root,
            session_id=request.session_id,
            file_path=request.file_path,
            accepted=request.accepted,
            diff_hash=request.diff_hash,
            note=request.note,
            trace_uuid=request.trace_uuid,
        )
    except Exception as exc:
        logger.exception("capture_diff_decision failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if row_id is None:
        return {"stored": False, "reason": "training capture disabled"}
    return {"stored": True, "id": row_id}

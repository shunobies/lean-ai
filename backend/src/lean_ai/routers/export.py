"""Authenticated export endpoints for the training archive.

An external coordinator (e.g. lean-ai-serve) calls these to pull data
from every registered workspace, merge across users, and produce a
training dataset.  All endpoints are gated behind
``LEAN_AI_EXPORT_API_KEY`` — when the setting is empty the router
returns 503 Service Unavailable, so export is disabled by default
even though local capture (``LEAN_AI_ENABLE_TRAINING_CAPTURE``) is on.

Streaming pattern: every large-result endpoint uses
``StreamingResponse(application/x-ndjson)`` with an async generator
over a SQLite cursor so memory stays flat regardless of archive size.
"""

from __future__ import annotations

import hmac
import json
import logging
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from lean_ai.config import settings
from lean_ai.db import get_db
from lean_ai.memory.db import list_memories
from lean_ai.training.db import get_training_db, manifest_counts
from lean_ai.training.export_formats import (
    FORMATS,
    anonymize_row,
    workspace_id,
)
from lean_ai.training.memory_anonymizer import (
    anonymize_memories,
    build_symbol_table,
)

logger = logging.getLogger(__name__)

export_router = APIRouter(prefix="/export", tags=["export"])


# ── Auth ────────────────────────────────────────────────────


async def require_export_key(
    authorization: str | None = Header(default=None),
) -> None:
    """FastAPI dependency: validate ``Authorization: Bearer <key>``.

    - 503 if no export key is configured (export feature disabled)
    - 401 if the header is missing or doesn't match
    """
    if not settings.export_api_key:
        raise HTTPException(
            status_code=503,
            detail="Export API disabled (set LEAN_AI_EXPORT_API_KEY to enable)",
        )
    expected = f"Bearer {settings.export_api_key}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Invalid export API key")


# ── Manifest (cached 60s per-workspace) ─────────────────────


_manifest_cache: dict[str, tuple[float, dict]] = {}
_MANIFEST_TTL_SECONDS = 60.0


@export_router.get("/workspace-id")
async def get_workspace_id_endpoint(
    repo_root: str,
    authorization: str | None = Header(default=None),
):
    """Return the stable ``workspace_id`` for this workspace."""
    await require_export_key(authorization)
    return {"workspace_id": workspace_id(repo_root)}


@export_router.get("/manifest")
async def get_manifest_endpoint(
    repo_root: str,
    authorization: str | None = Header(default=None),
):
    """Return per-workspace trace/event counts.  Cached 60s."""
    await require_export_key(authorization)
    cached = _manifest_cache.get(repo_root)
    now = time.monotonic()
    if cached and (now - cached[0]) < _MANIFEST_TTL_SECONDS:
        return cached[1]

    db = await get_training_db(repo_root)
    try:
        manifest = await manifest_counts(db)
    finally:
        await db.close()

    # Add memory counts from the workspace DB
    memory_counts = {"total": 0, "by_status": {}}
    try:
        main_db = await get_db(repo_root)
        try:
            cursor = await main_db.execute(
                "SELECT curation_status, COUNT(*) FROM session_memories GROUP BY curation_status"
            )
            rows = await cursor.fetchall()
            by_status = {r[0]: r[1] for r in rows}
            memory_counts = {
                "total": sum(by_status.values()),
                "by_status": by_status,
            }
        finally:
            await main_db.close()
    except Exception:
        logger.debug("memory manifest failed (non-fatal)", exc_info=True)

    manifest["memories"] = memory_counts
    manifest["workspace_id"] = workspace_id(repo_root)

    _manifest_cache[repo_root] = (now, manifest)
    return manifest


# ── Streaming endpoints ─────────────────────────────────────


def _cursor_predicate(cursor: str | None) -> tuple[str, list]:
    """Build a ``WHERE id > ?`` clause from a cursor string."""
    if cursor:
        try:
            return "id > ?", [int(cursor)]
        except ValueError:
            pass
    return "1=1", []


async def _iter_training_rows(
    repo_root: str,
    *,
    model: str | None,
    phase: str | None,
    outcome: str | None,
    since: str | None,
    cursor: str | None,
    limit: int,
) -> AsyncIterator[dict]:
    """Async iterator over raw training_traces rows matching filters."""
    clauses: list[str] = []
    params: list = []
    cursor_clause, cursor_params = _cursor_predicate(cursor)
    clauses.append(cursor_clause)
    params.extend(cursor_params)
    if model:
        clauses.append("model_name = ?")
        params.append(model)
    if phase:
        clauses.append("phase = ?")
        params.append(phase)
    if outcome:
        clauses.append("outcome = ?")
        params.append(outcome)
    if since:
        clauses.append("created_at >= ?")
        params.append(since)
    params.append(limit)

    where = " AND ".join(clauses) if clauses else "1=1"
    sql = f"SELECT * FROM training_traces WHERE {where} ORDER BY id ASC LIMIT ?"

    db = await get_training_db(repo_root)
    try:
        cursor_obj = await db.execute(sql, tuple(params))
        rows = await cursor_obj.fetchall()
        for row in rows:
            yield dict(row)
    finally:
        await db.close()


@export_router.get("/traces")
async def export_traces_endpoint(
    repo_root: str,
    format: str = Query(default="raw"),
    model: str | None = None,
    phase: str | None = None,
    outcome: str | None = None,
    since: str | None = None,
    cursor: str | None = None,
    limit: int = 1000,
    authorization: str | None = Header(default=None),
):
    """Stream training_traces rows as JSONL in the requested format."""
    await require_export_key(authorization)
    if format not in FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown format '{format}'. Supported: {sorted(FORMATS.keys())}",
        )
    if limit <= 0 or limit > 10000:
        raise HTTPException(
            status_code=400,
            detail="limit must be between 1 and 10000",
        )
    converter = FORMATS[format]

    async def _stream() -> AsyncIterator[bytes]:
        rows: list[dict] = []
        async for row in _iter_training_rows(
            repo_root,
            model=model,
            phase=phase,
            outcome=outcome,
            since=since,
            cursor=cursor,
            limit=limit,
        ):
            rows.append(row)
        # Converters are sync generators — materialize rows then feed in.
        # (Rows fit in memory by construction: limit <= 10000.)
        for line in converter(rows, repo_root):
            yield line.encode("utf-8")

    return StreamingResponse(_stream(), media_type="application/x-ndjson")


@export_router.get("/memories")
async def export_memories_endpoint(
    repo_root: str,
    curation_status: str | None = "user_confirmed,high_confidence_auto",
    category: str | None = None,
    limit: int = 500,
    authorization: str | None = Header(default=None),
):
    """Stream curated memories as JSONL with workspace anonymization."""
    await require_export_key(authorization)
    if limit <= 0 or limit > 5000:
        raise HTTPException(
            status_code=400,
            detail="limit must be between 1 and 5000",
        )
    statuses: list[str] | None = None
    if curation_status:
        statuses = [s.strip() for s in curation_status.split(",") if s.strip()]

    main_db = await get_db(repo_root)
    try:
        symbol_table = await build_symbol_table(main_db, repo_root=repo_root)
        raw_memories = await list_memories(
            main_db,
            category=category,
            limit=limit,
            curation_status=statuses,
            include_expired=False,
        )
    finally:
        await main_db.close()

    anonymized = list(anonymize_memories(raw_memories, table=symbol_table))
    ws_id = workspace_id(repo_root)

    def _stream() -> AsyncIterator[bytes]:
        async def _gen():
            for row in anonymized:
                out = {k: v for k, v in row.items() if k != "session_id"}
                out["workspace_id"] = ws_id
                yield (json.dumps(out, ensure_ascii=False) + "\n").encode("utf-8")

        return _gen()

    return StreamingResponse(_stream(), media_type="application/x-ndjson")


@export_router.get("/events")
async def export_events_endpoint(
    repo_root: str,
    event_type: str | None = None,
    since: str | None = None,
    cursor: str | None = None,
    limit: int = 1000,
    authorization: str | None = Header(default=None),
):
    """Stream workflow_events rows as JSONL (anonymized)."""
    await require_export_key(authorization)
    if limit <= 0 or limit > 10000:
        raise HTTPException(
            status_code=400,
            detail="limit must be between 1 and 10000",
        )

    clauses: list[str] = []
    params: list = []
    cursor_clause, cursor_params = _cursor_predicate(cursor)
    clauses.append(cursor_clause)
    params.extend(cursor_params)
    if event_type:
        clauses.append("event_type = ?")
        params.append(event_type)
    if since:
        clauses.append("created_at >= ?")
        params.append(since)
    params.append(limit)
    where = " AND ".join(clauses)

    async def _stream() -> AsyncIterator[bytes]:
        db = await get_training_db(repo_root)
        try:
            cursor_obj = await db.execute(
                f"SELECT * FROM workflow_events WHERE {where} ORDER BY id ASC LIMIT ?",
                tuple(params),
            )
            rows = await cursor_obj.fetchall()
            for row in rows:
                anonymized = anonymize_row(dict(row), repo_root)
                yield (json.dumps(anonymized, ensure_ascii=False) + "\n").encode(
                    "utf-8",
                )
        finally:
            await db.close()

    return StreamingResponse(_stream(), media_type="application/x-ndjson")


# ── New-table exports (Tier S.3, S.4, A.6, A.7, A.8) ──────────


async def _stream_anonymized_table(
    repo_root: str,
    *,
    table: str,
    extra_clauses: list[str],
    extra_params: list,
    cursor: str | None,
    limit: int,
) -> AsyncIterator[bytes]:
    """Shared helper: stream a training.db table as anonymized JSONL."""
    clauses: list[str] = []
    params: list = []
    cursor_clause, cursor_params = _cursor_predicate(cursor)
    clauses.append(cursor_clause)
    params.extend(cursor_params)
    clauses.extend(extra_clauses)
    params.extend(extra_params)
    params.append(limit)
    where = " AND ".join(clauses)

    db = await get_training_db(repo_root)
    try:
        cursor_obj = await db.execute(
            f"SELECT * FROM {table} WHERE {where} ORDER BY id ASC LIMIT ?",
            tuple(params),
        )
        rows = await cursor_obj.fetchall()
        for row in rows:
            anonymized = anonymize_row(dict(row), repo_root)
            yield (json.dumps(anonymized, ensure_ascii=False) + "\n").encode("utf-8")
    finally:
        await db.close()


@export_router.get("/tool-executions")
async def export_tool_executions_endpoint(
    repo_root: str,
    tool_name: str | None = None,
    phase: str | None = None,
    success: int | None = None,
    format: str = Query(default="raw"),
    since: str | None = None,
    cursor: str | None = None,
    limit: int = 1000,
    authorization: str | None = Header(default=None),
):
    """Stream tool_executions rows as JSONL.

    Supported formats:

    - ``raw`` — one JSON object per row, anonymized.
    - ``dpo_pairs`` — pair up a failed (``success=0``) call with the
      next successful (``success=1``) call on the same session + tool
      name, emitting ``{"prompt_hint": tool_name, "chosen": {arguments,
      result_preview}, "rejected": {arguments, result_preview},
      ...}``. Useful for training the model to call tools with
      argument shapes that actually work.
    """
    await require_export_key(authorization)
    if format not in ("raw", "dpo_pairs"):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown format '{format}'. Supported: raw, dpo_pairs",
        )
    if limit <= 0 or limit > 10000:
        raise HTTPException(
            status_code=400,
            detail="limit must be between 1 and 10000",
        )
    extra_clauses: list[str] = []
    extra_params: list = []
    if tool_name:
        extra_clauses.append("tool_name = ?")
        extra_params.append(tool_name)
    if phase:
        extra_clauses.append("phase = ?")
        extra_params.append(phase)
    if success is not None:
        extra_clauses.append("success = ?")
        extra_params.append(1 if success else 0)
    if since:
        extra_clauses.append("created_at >= ?")
        extra_params.append(since)

    if format == "raw":
        return StreamingResponse(
            _stream_anonymized_table(
                repo_root,
                table="tool_executions",
                extra_clauses=extra_clauses,
                extra_params=extra_params,
                cursor=cursor,
                limit=limit,
            ),
            media_type="application/x-ndjson",
        )

    # dpo_pairs: group rows by (session_id, tool_name) and emit
    # (failure, following-success) pairs. This is a heuristic pairing —
    # the trainer downstream can apply stricter joins if needed.
    async def _stream_pairs() -> AsyncIterator[bytes]:
        from lean_ai.training.export_formats import anonymize_row

        db = await get_training_db(repo_root)
        try:
            clauses = list(extra_clauses)
            params = list(extra_params)
            where = " AND ".join(clauses) if clauses else "1=1"
            cursor_obj = await db.execute(
                f"SELECT * FROM tool_executions WHERE {where} "
                f"ORDER BY session_id, tool_name, id ASC",
                tuple(params),
            )
            rows = await cursor_obj.fetchall()
        finally:
            await db.close()

        emitted = 0
        current_key: tuple | None = None
        pending_failure: dict | None = None
        for raw in rows:
            row = dict(raw)
            key = (row.get("session_id"), row.get("tool_name"))
            if key != current_key:
                current_key = key
                pending_failure = None
            if not row.get("success") and pending_failure is None:
                pending_failure = row
                continue
            if row.get("success") and pending_failure is not None:
                failed = pending_failure
                pending_failure = None
                pair = {
                    "prompt_hint": row.get("tool_name"),
                    "session_id": row.get("session_id"),
                    "phase": row.get("phase"),
                    "rejected": {
                        "arguments": failed.get("arguments"),
                        "result_preview": failed.get("result_preview"),
                    },
                    "chosen": {
                        "arguments": row.get("arguments"),
                        "result_preview": row.get("result_preview"),
                    },
                }
                pair = anonymize_row(pair, repo_root)
                yield (json.dumps(pair, ensure_ascii=False) + "\n").encode("utf-8")
                emitted += 1
                if emitted >= limit:
                    return

    return StreamingResponse(
        _stream_pairs(),
        media_type="application/x-ndjson",
    )


@export_router.get("/tool-compressions")
async def export_tool_compressions_endpoint(
    repo_root: str,
    tool_name: str | None = None,
    since: str | None = None,
    cursor: str | None = None,
    limit: int = 1000,
    authorization: str | None = Header(default=None),
):
    """Stream tool_compressions rows as JSONL.

    Worker-model distillation pairs: raw tool output → summary. Only
    populated when the off-by-default worker compression feature is
    active. Use these to fine-tune a smaller summarization model.
    """
    await require_export_key(authorization)
    if limit <= 0 or limit > 5000:
        raise HTTPException(
            status_code=400,
            detail="limit must be between 1 and 5000",
        )
    extra_clauses: list[str] = []
    extra_params: list = []
    if tool_name:
        extra_clauses.append("tool_name = ?")
        extra_params.append(tool_name)
    if since:
        extra_clauses.append("created_at >= ?")
        extra_params.append(since)

    return StreamingResponse(
        _stream_anonymized_table(
            repo_root,
            table="tool_compressions",
            extra_clauses=extra_clauses,
            extra_params=extra_params,
            cursor=cursor,
            limit=limit,
        ),
        media_type="application/x-ndjson",
    )


@export_router.get("/clarifications")
async def export_clarifications_endpoint(
    repo_root: str,
    outcome: str | None = None,
    since: str | None = None,
    cursor: str | None = None,
    limit: int = 1000,
    authorization: str | None = Header(default=None),
):
    """Stream Phase 1 clarification Q/A pairs as JSONL."""
    await require_export_key(authorization)
    if limit <= 0 or limit > 5000:
        raise HTTPException(
            status_code=400,
            detail="limit must be between 1 and 5000",
        )
    extra_clauses: list[str] = []
    extra_params: list = []
    if outcome:
        extra_clauses.append("outcome = ?")
        extra_params.append(outcome)
    if since:
        extra_clauses.append("created_at >= ?")
        extra_params.append(since)

    return StreamingResponse(
        _stream_anonymized_table(
            repo_root,
            table="clarifications",
            extra_clauses=extra_clauses,
            extra_params=extra_params,
            cursor=cursor,
            limit=limit,
        ),
        media_type="application/x-ndjson",
    )


@export_router.get("/phase2-syntheses")
async def export_phase2_syntheses_endpoint(
    repo_root: str,
    since: str | None = None,
    cursor: str | None = None,
    limit: int = 500,
    authorization: str | None = Header(default=None),
):
    """Stream Phase 2 exploration → FileSummary synthesis pairs.

    These are the highest-value supervised training data for teaching
    a model to produce structured file summaries from raw observations.
    Payloads can be large (observations + scratchpad + journal + full
    FileSummary), so the default limit is lower.
    """
    await require_export_key(authorization)
    if limit <= 0 or limit > 2000:
        raise HTTPException(
            status_code=400,
            detail="limit must be between 1 and 2000",
        )
    extra_clauses: list[str] = []
    extra_params: list = []
    if since:
        extra_clauses.append("created_at >= ?")
        extra_params.append(since)

    return StreamingResponse(
        _stream_anonymized_table(
            repo_root,
            table="phase2_syntheses",
            extra_clauses=extra_clauses,
            extra_params=extra_params,
            cursor=cursor,
            limit=limit,
        ),
        media_type="application/x-ndjson",
    )


@export_router.get("/diff-decisions")
async def export_diff_decisions_endpoint(
    repo_root: str,
    accepted: int | None = None,
    since: str | None = None,
    cursor: str | None = None,
    limit: int = 1000,
    authorization: str | None = Header(default=None),
):
    """Stream user accept/reject decisions on diffs."""
    await require_export_key(authorization)
    if limit <= 0 or limit > 10000:
        raise HTTPException(
            status_code=400,
            detail="limit must be between 1 and 10000",
        )
    extra_clauses: list[str] = []
    extra_params: list = []
    if accepted is not None:
        extra_clauses.append("accepted = ?")
        extra_params.append(1 if accepted else 0)
    if since:
        extra_clauses.append("created_at >= ?")
        extra_params.append(since)

    return StreamingResponse(
        _stream_anonymized_table(
            repo_root,
            table="diff_decisions",
            extra_clauses=extra_clauses,
            extra_params=extra_params,
            cursor=cursor,
            limit=limit,
        ),
        media_type="application/x-ndjson",
    )


def clear_manifest_cache() -> None:
    """Test helper: reset the manifest cache."""
    _manifest_cache.clear()

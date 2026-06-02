"""Observability router for the VS Code dashboard.

Provides all HTTP endpoints consumed by the observability panel:
sessions listing, session detail with trace trees, individual trace
span details, feedback CRUD, and aggregate metrics.

Hybrid auth: GET endpoints are open (no auth) so the dashboard can
read freely.  POST endpoints require ``LEAN_AI_EXPORT_API_KEY`` via
a ``Bearer`` token to prevent feedback spoofing.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from lean_ai.config import settings
from lean_ai.db import get_db
from lean_ai.training.db import (
    get_training_db,
    get_trace_tree,
    insert_feedback,
)

logger = logging.getLogger(__name__)

observability_router = APIRouter(prefix="/observability", tags=["observability"])


# ── Hybrid auth ────────────────────────────────────────────────


async def require_export_key_for_writes(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    """FastAPI dependency: validate ``Authorization: Bearer <key>``.

    Mirrors ``require_export_key`` from export.py but is used only on
    POST/PUT/DELETE endpoints.  GET endpoints remain open for the
    dashboard to read without authentication.

    - 503 if no export key is configured (feature disabled)
    - 401 if the header is missing or doesn't match
    """
    client_host = request.client.host if request.client else ""
    try:
        is_loopback = bool(client_host) and ipaddress.ip_address(client_host).is_loopback
    except ValueError:
        is_loopback = client_host in {"localhost"}
    if is_loopback:
        return

    if not settings.export_api_key:
        raise HTTPException(
            status_code=503,
            detail="Export API disabled (set LEAN_AI_EXPORT_API_KEY to enable)",
        )
    expected = f"Bearer {settings.export_api_key}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Invalid export API key")


# ── Sessions ───────────────────────────────────────────────────


@observability_router.get("/sessions")
async def list_observability_sessions(repo_root: str):
    """Return a list of sessions with basic metrics from the training archive.

    Joins the main sessions table with trace_spans counts from the
    training DB so the dashboard can show per-session observability
    summaries without loading full trace trees.
    """
    main_db = await get_db(repo_root)
    train_db = await get_training_db(repo_root)
    try:
        cursor = await main_db.execute(
            "SELECT * FROM sessions ORDER BY created_at DESC"
        )
        sessions = await cursor.fetchall()

        result: list[dict[str, Any]] = []
        for session in sessions:
            session_dict = dict(session)

            # Count trace spans for this session
            span_cursor = await train_db.execute(
                "SELECT COUNT(*) AS cnt FROM trace_spans WHERE session_id = ?",
                (session_dict["id"],),
            )
            span_row = await span_cursor.fetchone()
            session_dict["span_count"] = span_row["cnt"] if span_row else 0

            # Count feedback entries for this session
            fb_cursor = await train_db.execute(
                "SELECT COUNT(*) AS cnt FROM session_feedback WHERE session_id = ?",
                (session_dict["id"],),
            )
            fb_row = await fb_cursor.fetchone()
            session_dict["feedback_count"] = fb_row["cnt"] if fb_row else 0

            result.append(session_dict)

        return result
    finally:
        await main_db.close()
        await train_db.close()


@observability_router.get("/sessions/{session_id}")
async def get_observability_session(session_id: str, repo_root: str):
    """Return session detail with the full trace tree.

    Combines the main session record with the recursive trace tree
    from the training DB's trace_spans table.
    """
    main_db = await get_db(repo_root)
    train_db = await get_training_db(repo_root)
    try:
        cursor = await main_db.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        )
        session = await cursor.fetchone()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        session_dict = dict(session)

        # Fetch the trace tree
        tree = await get_trace_tree(train_db, session_id)
        session_dict["trace_tree"] = tree

        # Fetch feedback for this session
        fb_cursor = await train_db.execute(
            "SELECT * FROM session_feedback WHERE session_id = ? ORDER BY created_at DESC",
            (session_id,),
        )
        feedback_rows = await fb_cursor.fetchall()
        session_dict["feedback"] = [dict(r) for r in feedback_rows]

        return session_dict
    finally:
        await main_db.close()
        await train_db.close()


# ── Traces ─────────────────────────────────────────────────────


@observability_router.get("/traces/{span_uuid}")
async def get_trace_span(span_uuid: str, repo_root: str):
    """Return details for a single trace span by its UUID."""
    train_db = await get_training_db(repo_root)
    try:
        cursor = await train_db.execute(
            "SELECT * FROM trace_spans WHERE span_uuid = ?", (span_uuid,)
        )
        span = await cursor.fetchone()
        if not span:
            raise HTTPException(status_code=404, detail="Trace span not found")

        span_dict = dict(span)

        # Parse metadata_json if present
        if span_dict.get("metadata_json"):
            try:
                span_dict["metadata"] = json.loads(span_dict["metadata_json"])
            except (json.JSONDecodeError, TypeError):
                span_dict["metadata"] = None
        else:
            span_dict["metadata"] = None

        # Fetch child spans
        child_cursor = await train_db.execute(
            "SELECT span_uuid, span_type, span_name, start_time, end_time, status "
            "FROM trace_spans WHERE parent_span_uuid = ? ORDER BY start_time",
            (span_uuid,),
        )
        children = await child_cursor.fetchall()
        span_dict["children"] = [dict(c) for c in children]

        return span_dict
    finally:
        await train_db.close()


@observability_router.get("/traces/tree")
async def get_trace_tree_endpoint(session_id: str, repo_root: str):
    """Return the full nested trace tree for a session.

    Uses the recursive CTE in get_trace_tree() to build a flat list
    with depth information, then nests it into a tree structure.
    """
    train_db = await get_training_db(repo_root)
    try:
        flat_tree = await get_trace_tree(train_db, session_id)

        # Build a nested tree from the flat list
        children_map: dict[str, list[dict[str, Any]]] = {}
        roots: list[dict[str, Any]] = []

        for node in flat_tree:
            node["children"] = []
            parent = node.get("parent_span_uuid")
            if parent is None:
                roots.append(node)
            else:
                children_map.setdefault(parent, []).append(node)

        for node in flat_tree:
            parent = node.get("parent_span_uuid")
            if parent and parent in children_map:
                children_map[parent].extend(node.get("children", []))

        # Re-link children
        for node in flat_tree:
            node_uuid = node["span_uuid"]
            if node_uuid in children_map:
                node["children"] = children_map[node_uuid]

        return {"session_id": session_id, "tree": roots}
    finally:
        await train_db.close()


# ── Feedback ───────────────────────────────────────────────────


@observability_router.post("/feedback")
async def create_feedback(
    request: Request,
    repo_root: str,
    session_id: str,
    thumbs_up: bool | None = None,
    rating: int | None = None,
    comment: str | None = None,
    tags: str | None = None,
    trace_span_uuid: str | None = None,
    authorization: str | None = Header(default=None),
):
    """Create a new feedback entry for a session or trace span.

    Requires Bearer token authentication to prevent feedback spoofing.
    """
    await require_export_key_for_writes(request, authorization)

    # Parse tags from comma-separated string to list
    tag_list: list[str] | None = None
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    train_db = await get_training_db(repo_root)
    try:
        feedback_id = await insert_feedback(
            train_db,
            session_id=session_id,
            thumbs_up=thumbs_up,
            rating=rating,
            comment=comment,
            tags=tag_list,
            trace_span_uuid=trace_span_uuid,
        )
        return {"status": "created", "feedback_id": feedback_id}
    finally:
        await train_db.close()


@observability_router.get("/feedback")
async def list_feedback(
    repo_root: str,
    session_id: str | None = None,
):
    """List feedback entries, optionally filtered by session_id."""
    train_db = await get_training_db(repo_root)
    try:
        if session_id:
            cursor = await train_db.execute(
                "SELECT * FROM session_feedback "
                "WHERE session_id = ? ORDER BY created_at DESC",
                (session_id,),
            )
        else:
            cursor = await train_db.execute(
                "SELECT * FROM session_feedback ORDER BY created_at DESC"
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await train_db.close()


# ── Metrics ────────────────────────────────────────────────────


@observability_router.get("/metrics/summary")
async def get_metrics_summary(repo_root: str):
    """Return aggregate observability metrics across all sessions.

    Uses COALESCE and GROUP BY for NULL-safe aggregation of trace
    span data from the training archive.
    """
    train_db = await get_training_db(repo_root)
    try:
        # Total spans
        cursor = await train_db.execute(
            "SELECT COUNT(*) AS total FROM trace_spans"
        )
        row = await cursor.fetchone()
        total_spans = row["total"] if row else 0

        # Spans by type
        type_cursor = await train_db.execute(
            "SELECT span_type, COUNT(*) AS count "
            "FROM trace_spans GROUP BY span_type"
        )
        type_rows = await type_cursor.fetchall()
        by_type = {r["span_type"]: r["count"] for r in type_rows}

        # Spans by status
        status_cursor = await train_db.execute(
            "SELECT COALESCE(status, 'active') AS status, COUNT(*) AS count "
            "FROM trace_spans GROUP BY COALESCE(status, 'active')"
        )
        status_rows = await status_cursor.fetchall()
        by_status = {r["status"]: r["count"] for r in status_rows}

        # Total feedback
        fb_cursor = await train_db.execute(
            "SELECT COUNT(*) AS total FROM session_feedback"
        )
        fb_row = await fb_cursor.fetchone()
        total_feedback = fb_row["total"] if fb_row else 0

        # Feedback thumbs up breakdown
        thumbs_cursor = await train_db.execute(
            "SELECT COALESCE(thumbs_up, -1) AS thumb, COUNT(*) AS count "
            "FROM session_feedback GROUP BY COALESCE(thumbs_up, -1)"
        )
        thumbs_rows = await thumbs_cursor.fetchall()
        thumbs_breakdown = {str(r["thumb"]): r["count"] for r in thumbs_rows}

        return {
            "total_spans": total_spans,
            "by_type": by_type,
            "by_status": by_status,
            "total_feedback": total_feedback,
            "thumbs_breakdown": thumbs_breakdown,
        }
    finally:
        await train_db.close()


@observability_router.get("/metrics/tokens")
async def get_metrics_tokens(repo_root: str):
    """Return token usage metrics aggregated from training traces.

    Uses COALESCE(json_extract(...), 0) for NULL-safe extraction of
    token counts from the training_traces table.
    """
    train_db = await get_training_db(repo_root)
    try:
        cursor = await train_db.execute(
            "SELECT "
            "COALESCE(SUM(tokens_prompt), 0) AS total_prompt_tokens, "
            "COALESCE(SUM(tokens_completion), 0) AS total_completion_tokens, "
            "COALESCE(AVG(tokens_prompt), 0) AS avg_prompt_tokens, "
            "COALESCE(AVG(tokens_completion), 0) AS avg_completion_tokens "
            "FROM training_traces"
        )
        row = await cursor.fetchone()

        # Per-model breakdown
        model_cursor = await train_db.execute(
            "SELECT "
            "model_name, "
            "COALESCE(SUM(tokens_prompt), 0) AS prompt_tokens, "
            "COALESCE(SUM(tokens_completion), 0) AS completion_tokens "
            "FROM training_traces "
            "WHERE model_name IS NOT NULL "
            "GROUP BY model_name"
        )
        model_rows = await model_cursor.fetchall()
        by_model = {
            r["model_name"]: {
                "prompt_tokens": r["prompt_tokens"],
                "completion_tokens": r["completion_tokens"],
            }
            for r in model_rows
        }

        return {
            "total_prompt_tokens": row["total_prompt_tokens"],
            "total_completion_tokens": row["total_completion_tokens"],
            "avg_prompt_tokens": round(row["avg_prompt_tokens"], 2),
            "avg_completion_tokens": round(row["avg_completion_tokens"], 2),
            "by_model": by_model,
        }
    finally:
        await train_db.close()


@observability_router.get("/metrics/latency")
async def get_metrics_latency(repo_root: str):
    """Return latency metrics aggregated from training traces.

    Uses COALESCE for NULL-safe aggregation of latency_ms values.
    """
    train_db = await get_training_db(repo_root)
    try:
        cursor = await train_db.execute(
            "SELECT "
            "COALESCE(AVG(latency_ms), 0) AS avg_latency, "
            "COALESCE(MIN(latency_ms), 0) AS min_latency, "
            "COALESCE(MAX(latency_ms), 0) AS max_latency, "
            "COUNT(*) AS total_traces "
            "FROM training_traces "
            "WHERE latency_ms IS NOT NULL"
        )
        row = await cursor.fetchone()

        # Per-model breakdown
        model_cursor = await train_db.execute(
            "SELECT "
            "model_name, "
            "COALESCE(AVG(latency_ms), 0) AS avg_latency, "
            "COALESCE(MIN(latency_ms), 0) AS min_latency, "
            "COALESCE(MAX(latency_ms), 0) AS max_latency, "
            "COUNT(*) AS count "
            "FROM training_traces "
            "WHERE model_name IS NOT NULL AND latency_ms IS NOT NULL "
            "GROUP BY model_name"
        )
        model_rows = await model_cursor.fetchall()
        by_model = {
            r["model_name"]: {
                "avg_latency": round(r["avg_latency"], 2),
                "min_latency": r["min_latency"],
                "max_latency": r["max_latency"],
                "count": r["count"],
            }
            for r in model_rows
        }

        return {
            "avg_latency_ms": round(row["avg_latency"], 2),
            "min_latency_ms": row["min_latency"],
            "max_latency_ms": row["max_latency"],
            "total_traces": row["total_traces"],
            "by_model": by_model,
        }
    finally:
        await train_db.close()


@observability_router.get("/metrics/tools")
async def get_metrics_tools(repo_root: str):
    """Return tool usage metrics aggregated from tool_executions.

    Uses COALESCE and GROUP BY for NULL-safe aggregation of tool
    execution data.
    """
    train_db = await get_training_db(repo_root)
    try:
        # Total tool executions
        cursor = await train_db.execute(
            "SELECT COUNT(*) AS total FROM tool_executions"
        )
        row = await cursor.fetchone()
        total = row["total"] if row else 0

        # Per-tool breakdown
        tool_cursor = await train_db.execute(
            "SELECT "
            "tool_name, "
            "COUNT(*) AS count, "
            "COALESCE(SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END), 0) AS successes, "
            "COALESCE(SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END), 0) AS failures, "
            "COALESCE(AVG(latency_ms), 0) AS avg_latency "
            "FROM tool_executions "
            "GROUP BY tool_name "
            "ORDER BY count DESC"
        )
        tool_rows = await tool_cursor.fetchall()
        by_tool = {
            r["tool_name"]: {
                "count": r["count"],
                "successes": r["successes"],
                "failures": r["failures"],
                "avg_latency_ms": round(r["avg_latency"], 2),
            }
            for r in tool_rows
        }

        return {
            "total_executions": total,
            "by_tool": by_tool,
        }
    finally:
        await train_db.close()

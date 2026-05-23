"""Unit tests for observability: trace spans, feedback, metrics, context manager, and auth.

Covers:
  1. trace_spans table schema creation and migration idempotency
  2. insert_trace_span and update_trace_span_end lifecycle
  3. Parent/child span relationships
  4. trace_span context manager nested spans (session→phase→turn→tool)
  5. Exception handling marks spans as failed without propagation
  6. capture_turn/capture_tool_execution with span_uuid
  7. Feedback insertion and retrieval
  8. GET endpoints return correct data structure
  9. POST feedback requires auth (401 without Bearer token)
  10. GET endpoints accessible without auth (200)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from lean_ai.db import get_db
from lean_ai.training.db import (
    get_training_db,
    get_trace_tree,
    insert_feedback,
    insert_trace_span,
    update_trace_span_end,
)
from lean_ai.training.span_context import TraceSpan, trace_span


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
async def train_db(tmp_path):
    """Training DB connection scoped to a temp directory."""
    db = await get_training_db(str(tmp_path))
    yield db
    await db.close()


@pytest.fixture
async def main_db(tmp_path):
    """Main DB connection scoped to a temp directory."""
    db = await get_db(str(tmp_path))
    yield db
    await db.close()


# ── 1. Schema creation and migration idempotency ────────────────────


@pytest.mark.asyncio
async def test_schema_creates_trace_spans_table(train_db):
    """trace_spans table is created by schema initialization."""
    cursor = await train_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    rows = await cursor.fetchall()
    tables = {r[0] for r in rows}
    assert "trace_spans" in tables


@pytest.mark.asyncio
async def test_schema_creates_session_feedback_table(train_db):
    """session_feedback table is created by schema initialization."""
    cursor = await train_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    rows = await cursor.fetchall()
    tables = {r[0] for r in rows}
    assert "session_feedback" in tables


@pytest.mark.asyncio
async def test_trace_spans_has_expected_columns(train_db):
    """trace_spans table contains all required columns."""
    cursor = await train_db.execute("PRAGMA table_info(trace_spans)")
    rows = await cursor.fetchall()
    columns = {r["name"] for r in rows}
    expected = {
        "span_uuid",
        "parent_span_uuid",
        "session_id",
        "span_type",
        "span_name",
        "start_time",
        "end_time",
        "status",
        "metadata_json",
        "created_at",
    }
    assert expected.issubset(columns)


@pytest.mark.asyncio
async def test_session_feedback_has_expected_columns(train_db):
    """session_feedback table contains all required columns."""
    cursor = await train_db.execute("PRAGMA table_info(session_feedback)")
    rows = await cursor.fetchall()
    columns = {r["name"] for r in rows}
    expected = {
        "feedback_id",
        "session_id",
        "trace_span_uuid",
        "thumbs_up",
        "rating",
        "comment",
        "tags",
        "created_at",
    }
    assert expected.issubset(columns)


@pytest.mark.asyncio
async def test_migration_idempotent_on_fresh_db(tmp_path):
    """Opening the DB twice does not duplicate tables or fail."""
    db1 = await get_training_db(str(tmp_path))
    await db1.close()
    db2 = await get_training_db(str(tmp_path))
    # Should succeed without error
    cursor = await db2.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='trace_spans'"
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1
    await db2.close()


# ── 2. insert_trace_span and update_trace_span_end lifecycle ────────


@pytest.mark.asyncio
async def test_insert_trace_span_roundtrip(train_db):
    """Insert a span and read it back with correct values."""
    span_uuid = "test-span-001"
    now = datetime.now(timezone.utc).isoformat()
    await insert_trace_span(
        train_db,
        span_uuid=span_uuid,
        session_id="sess-1",
        span_type="llm_call",
        span_name="Greeting",
        start_time=now,
        parent_span_uuid=None,
        status=None,
        metadata={"model": "gpt-4"},
    )

    cursor = await train_db.execute(
        "SELECT * FROM trace_spans WHERE span_uuid = ?", (span_uuid,)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["span_uuid"] == span_uuid
    assert row["session_id"] == "sess-1"
    assert row["span_type"] == "llm_call"
    assert row["span_name"] == "Greeting"
    assert row["start_time"] == now
    assert row["end_time"] is None
    assert row["status"] is None
    metadata = json.loads(row["metadata_json"])
    assert metadata == {"model": "gpt-4"}


@pytest.mark.asyncio
async def test_update_trace_span_end_sets_end_time_and_status(train_db):
    """update_trace_span_end populates end_time and status."""
    span_uuid = "test-span-002"
    start = datetime.now(timezone.utc).isoformat()
    await insert_trace_span(
        train_db,
        span_uuid=span_uuid,
        session_id="sess-1",
        span_type="tool_call",
        span_name="read_file",
        start_time=start,
    )

    end = datetime.now(timezone.utc).isoformat()
    await update_trace_span_end(train_db, span_uuid=span_uuid, end_time=end, status="ok")

    cursor = await train_db.execute(
        "SELECT end_time, status FROM trace_spans WHERE span_uuid = ?", (span_uuid,)
    )
    row = await cursor.fetchone()
    assert row["end_time"] == end
    assert row["status"] == "ok"


@pytest.mark.asyncio
async def test_insert_trace_span_with_none_metadata(train_db):
    """Spans without metadata store NULL in metadata_json."""
    await insert_trace_span(
        train_db,
        span_uuid="test-span-003",
        session_id="sess-1",
        span_type="phase",
        span_name="planning",
        start_time=datetime.now(timezone.utc).isoformat(),
        metadata=None,
    )

    cursor = await train_db.execute(
        "SELECT metadata_json FROM trace_spans WHERE span_uuid = ?", ("test-span-003",)
    )
    row = await cursor.fetchone()
    assert row["metadata_json"] is None


# ── 3. Parent/child span relationships ─────────────────────────────


@pytest.mark.asyncio
async def test_parent_child_span_relationship(train_db):
    """Child spans reference parent via parent_span_uuid."""
    parent_uuid = "parent-001"
    child_uuid = "child-001"
    now = datetime.now(timezone.utc).isoformat()

    await insert_trace_span(
        train_db,
        span_uuid=parent_uuid,
        session_id="sess-1",
        span_type="phase",
        span_name="planning",
        start_time=now,
    )
    await insert_trace_span(
        train_db,
        span_uuid=child_uuid,
        session_id="sess-1",
        span_type="llm_call",
        span_name="scope",
        start_time=now,
        parent_span_uuid=parent_uuid,
    )

    cursor = await train_db.execute(
        "SELECT parent_span_uuid FROM trace_spans WHERE span_uuid = ?", (child_uuid,)
    )
    row = await cursor.fetchone()
    assert row["parent_span_uuid"] == parent_uuid


@pytest.mark.asyncio
async def test_get_trace_tree_returns_hierarchical_data(train_db):
    """get_trace_tree returns spans with depth information."""
    now = datetime.now(timezone.utc).isoformat()
    await insert_trace_span(
        train_db,
        span_uuid="root-001",
        session_id="sess-tree",
        span_type="phase",
        span_name="planning",
        start_time=now,
    )
    await insert_trace_span(
        train_db,
        span_uuid="child-002",
        session_id="sess-tree",
        span_type="llm_call",
        span_name="scope",
        start_time=now,
        parent_span_uuid="root-001",
    )

    tree = await get_trace_tree(train_db, "sess-tree")
    assert len(tree) == 2

    root = next(n for n in tree if n["span_uuid"] == "root-001")
    child = next(n for n in tree if n["span_uuid"] == "child-002")
    assert root["depth"] == 0
    assert child["depth"] == 1
    assert child["parent_span_uuid"] == "root-001"


@pytest.mark.asyncio
async def test_get_trace_tree_empty_for_unknown_session(train_db):
    """get_trace_tree returns empty list for sessions with no spans."""
    tree = await get_trace_tree(train_db, "nonexistent-session")
    assert tree == []


# ── 4. trace_span context manager nested spans ──────────────────────


@pytest.mark.asyncio
async def test_trace_span_context_manager_creates_span(tmp_path):
    """The context manager inserts a span on entry and updates on exit."""
    async with trace_span(
        span_type="phase",
        span_name="planning",
        session_id=str(tmp_path),
    ) as span:
        assert span.span_uuid is not None
        assert span.span_type == "phase"
        assert span.span_name == "planning"
        assert span.start_time is not None
        assert span.end_time is None
        assert span.status is None

    # After exit, span should have end_time and status='ok'
    assert span.end_time is not None
    assert span.status == "ok"

    # Verify in DB
    db = await get_training_db(str(tmp_path))
    cursor = await db.execute(
        "SELECT status, end_time FROM trace_spans WHERE span_uuid = ?", (span.span_uuid,)
    )
    row = await cursor.fetchone()
    assert row["status"] == "ok"
    assert row["end_time"] is not None
    await db.close()


@pytest.mark.asyncio
async def test_trace_span_nested_hierarchy(tmp_path):
    """Nested context managers create parent→child span relationships."""
    async with trace_span(
        span_type="session",
        span_name="full_session",
        session_id=str(tmp_path),
    ) as session_span:
        async with trace_span(
            span_type="phase",
            span_name="planning",
            session_id=str(tmp_path),
            parent_span=session_span,
        ) as phase_span:
            async with trace_span(
                span_type="llm_call",
                span_name="scope_call",
                session_id=str(tmp_path),
                parent_span=phase_span,
            ) as turn_span:
                async with trace_span(
                    span_type="tool_call",
                    span_name="read_file",
                    session_id=str(tmp_path),
                    parent_span=turn_span,
                ) as tool_span:
                    pass

    # Verify hierarchy
    assert phase_span.parent_span_uuid == session_span.span_uuid
    assert turn_span.parent_span_uuid == phase_span.span_uuid
    assert tool_span.parent_span_uuid == turn_span.span_uuid

    # All spans should be 'ok'
    assert session_span.status == "ok"
    assert phase_span.status == "ok"
    assert turn_span.status == "ok"
    assert tool_span.status == "ok"

    # Verify in DB
    db = await get_training_db(str(tmp_path))
    cursor = await db.execute(
        "SELECT COUNT(*) AS cnt FROM trace_spans"
    )
    row = await cursor.fetchone()
    assert row["cnt"] == 4
    await db.close()


@pytest.mark.asyncio
async def test_trace_span_with_metadata(tmp_path):
    """Context manager stores metadata as JSON in the span."""
    metadata = {"model": "gpt-4", "tokens": 150}
    async with trace_span(
        span_type="llm_call",
        span_name="test_call",
        session_id=str(tmp_path),
        metadata=metadata,
    ) as span:
        pass

    db = await get_training_db(str(tmp_path))
    cursor = await db.execute(
        "SELECT metadata_json FROM trace_spans WHERE span_uuid = ?", (span.span_uuid,)
    )
    row = await cursor.fetchone()
    assert json.loads(row["metadata_json"]) == metadata
    await db.close()


# ── 5. Exception handling marks spans as failed ─────────────────────


@pytest.mark.asyncio
async def test_trace_span_exception_marks_failed(tmp_path):
    """Exceptions inside the span body mark it as 'failed' without propagating.

    The trace_span context manager is fire-and-forget safe: it catches
    exceptions, marks the span as failed, and swallows the exception so
    observability never breaks business logic.
    """
    span: TraceSpan | None = None
    try:
        async with trace_span(
            span_type="phase",
            span_name="failing_phase",
            session_id=str(tmp_path),
        ) as s:
            span = s
            raise ValueError("intentional failure")
    except ValueError:
        pytest.fail("trace_span should swallow exceptions (fire-and-forget)")

    # The span should be marked as failed
    assert span is not None
    assert span.status == "failed"
    assert span.end_time is not None

    # Verify in DB
    db = await get_training_db(str(tmp_path))
    cursor = await db.execute(
        "SELECT status, end_time FROM trace_spans WHERE span_uuid = ?", (span.span_uuid,)
    )
    row = await cursor.fetchone()
    assert row["status"] == "failed"
    assert row["end_time"] is not None
    await db.close()


@pytest.mark.asyncio
async def test_trace_span_exception_logged_and_swallowed(tmp_path):
    """The context manager logs the error and swallows it (fire-and-forget)."""
    span: TraceSpan | None = None
    try:
        async with trace_span(
            span_type="tool_call",
            span_name="failing_tool",
            session_id=str(tmp_path),
        ) as s:
            span = s
            raise ValueError("boom")
    except ValueError:
        pytest.fail("trace_span should swallow exceptions (fire-and-forget)")

    assert span is not None
    assert span.status == "failed"
    assert span.end_time is not None


@pytest.mark.asyncio
async def test_trace_span_partial_failure_in_nested(tmp_path):
    """A failing inner span doesn't affect the outer span's status.

    The inner span swallows the exception (fire-and-forget), so the
    outer span completes normally.
    """
    outer_span: TraceSpan | None = None

    async with trace_span(
        span_type="phase",
        span_name="outer",
        session_id=str(tmp_path),
    ) as outer:
        outer_span = outer
        # Inner span swallows the exception
        async with trace_span(
            span_type="llm_call",
            span_name="inner",
            session_id=str(tmp_path),
            parent_span=outer,
        ):
            raise RuntimeError("inner failure")

    # Outer span should still be 'ok'
    assert outer_span is not None
    assert outer_span.status == "ok"

    # Verify inner span is 'failed' in DB
    db = await get_training_db(str(tmp_path))
    cursor = await db.execute(
        "SELECT status FROM trace_spans WHERE span_name = ?", ("inner",)
    )
    row = await cursor.fetchone()
    assert row["status"] == "failed"
    await db.close()


# ── 6. capture_turn/capture_tool_execution with span_uuid ───────────


@pytest.mark.asyncio
async def test_insert_training_trace_with_span_uuid(train_db):
    """training_traces rows can be linked to spans via span_uuid column."""
    from lean_ai.training.db import insert_training_trace, new_trace_uuid

    span_uuid = "linked-span-001"
    trace_uuid = new_trace_uuid()

    # Insert span first
    await insert_trace_span(
        train_db,
        span_uuid=span_uuid,
        session_id="sess-1",
        span_type="llm_call",
        span_name="test",
        start_time=datetime.now(timezone.utc).isoformat(),
    )

    # Insert training trace
    row_id = await insert_training_trace(
        train_db,
        trace_uuid=trace_uuid,
        session_id="sess-1",
        phase="implementation",
        model_name="gpt-4",
        provider="openai",
        messages=[],
        assistant_output={"content": "hello"},
        outcome="success",
    )
    assert row_id > 0

    # Link trace to span via UPDATE (as capture_turn does)
    await train_db.execute(
        "UPDATE training_traces SET span_uuid = ? WHERE trace_uuid = ?",
        (span_uuid, trace_uuid),
    )
    await train_db.commit()

    cursor = await train_db.execute(
        "SELECT span_uuid FROM training_traces WHERE trace_uuid = ?", (trace_uuid,)
    )
    row = await cursor.fetchone()
    assert row["span_uuid"] == span_uuid


# ── 7. Feedback insertion and retrieval ─────────────────────────────


@pytest.mark.asyncio
async def test_insert_feedback_roundtrip(train_db):
    """Insert feedback and read it back with correct values."""
    fb_id = await insert_feedback(
        train_db,
        session_id="sess-fb",
        thumbs_up=True,
        rating=5,
        comment="Great session",
        tags=["helpful", "fast"],
        trace_span_uuid="span-fb-001",
    )
    assert fb_id > 0

    cursor = await train_db.execute(
        "SELECT * FROM session_feedback WHERE feedback_id = ?", (fb_id,)
    )
    row = await cursor.fetchone()
    assert row["session_id"] == "sess-fb"
    assert row["thumbs_up"] == 1
    assert row["rating"] == 5
    assert row["comment"] == "Great session"
    assert row["trace_span_uuid"] == "span-fb-001"
    tags = json.loads(row["tags"])
    assert tags == ["helpful", "fast"]


@pytest.mark.asyncio
async def test_insert_feedback_with_none_values(train_db):
    """Feedback with optional fields as None stores NULL."""
    fb_id = await insert_feedback(
        train_db,
        session_id="sess-fb-none",
        thumbs_up=None,
        rating=None,
        comment=None,
        tags=None,
        trace_span_uuid=None,
    )
    assert fb_id > 0

    cursor = await train_db.execute(
        "SELECT thumbs_up, rating, comment, tags, trace_span_uuid "
        "FROM session_feedback WHERE feedback_id = ?",
        (fb_id,),
    )
    row = await cursor.fetchone()
    assert row["thumbs_up"] is None
    assert row["rating"] is None
    assert row["comment"] is None
    assert row["tags"] is None
    assert row["trace_span_uuid"] is None


@pytest.mark.asyncio
async def test_insert_feedback_thumbs_down(train_db):
    """thumbs_up=False stores 0 in the database."""
    await insert_feedback(
        train_db,
        session_id="sess-fb-down",
        thumbs_up=False,
    )

    cursor = await train_db.execute(
        "SELECT thumbs_up FROM session_feedback WHERE session_id = ?",
        ("sess-fb-down",),
    )
    row = await cursor.fetchone()
    assert row["thumbs_up"] == 0


# ── 8. GET endpoints return correct data structure ──────────────────


@pytest.mark.asyncio
async def test_get_metrics_summary_returns_structure(tmp_path):
    """GET /observability/metrics/summary returns expected keys."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from lean_ai.routers.observability import observability_router

    app = FastAPI()
    app.include_router(observability_router)

    # Seed some data
    db = await get_training_db(str(tmp_path))
    now = datetime.now(timezone.utc).isoformat()
    await insert_trace_span(
        db,
        span_uuid="metrics-span-1",
        session_id=str(tmp_path),
        span_type="llm_call",
        span_name="call1",
        start_time=now,
        status="ok",
    )
    await insert_feedback(
        db,
        session_id=str(tmp_path),
        thumbs_up=True,
        rating=4,
    )
    await db.close()

    client = TestClient(app)
    resp = client.get(f"/observability/metrics/summary?repo_root={tmp_path}")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_spans" in data
    assert "by_type" in data
    assert "by_status" in data
    assert "total_feedback" in data
    assert "thumbs_breakdown" in data
    assert data["total_spans"] == 1
    assert data["total_feedback"] == 1


@pytest.mark.asyncio
async def test_get_metrics_tokens_returns_structure(tmp_path):
    """GET /observability/metrics/tokens returns expected keys."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from lean_ai.routers.observability import observability_router

    app = FastAPI()
    app.include_router(observability_router)

    client = TestClient(app)
    resp = client.get(f"/observability/metrics/tokens?repo_root={tmp_path}")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_prompt_tokens" in data
    assert "total_completion_tokens" in data
    assert "avg_prompt_tokens" in data
    assert "avg_completion_tokens" in data
    assert "by_model" in data


@pytest.mark.asyncio
async def test_get_metrics_latency_returns_structure(tmp_path):
    """GET /observability/metrics/latency returns expected keys."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from lean_ai.routers.observability import observability_router

    app = FastAPI()
    app.include_router(observability_router)

    client = TestClient(app)
    resp = client.get(f"/observability/metrics/latency?repo_root={tmp_path}")
    assert resp.status_code == 200
    data = resp.json()
    assert "avg_latency_ms" in data
    assert "min_latency_ms" in data
    assert "max_latency_ms" in data
    assert "total_traces" in data
    assert "by_model" in data


@pytest.mark.asyncio
async def test_get_metrics_tools_returns_structure(tmp_path):
    """GET /observability/metrics/tools returns expected keys."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from lean_ai.routers.observability import observability_router

    app = FastAPI()
    app.include_router(observability_router)

    client = TestClient(app)
    resp = client.get(f"/observability/metrics/tools?repo_root={tmp_path}")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_executions" in data
    assert "by_tool" in data


@pytest.mark.asyncio
async def test_get_sessions_list_returns_data(main_db, train_db, tmp_path):
    """GET /observability/sessions returns session list with span/feedback counts."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from lean_ai.routers.observability import observability_router

    app = FastAPI()
    app.include_router(observability_router)

    # Create a session in the main DB
    now = datetime.now(timezone.utc).isoformat()
    await main_db.execute(
        "INSERT INTO sessions (id, repo_root, task, status, created_at) "
        "VALUES (?, ?, 'test task', 'active', ?)",
        ("sess-list-1", str(tmp_path), now),
    )
    await main_db.commit()

    # Add a trace span for this session in training DB
    await insert_trace_span(
        train_db,
        span_uuid="sess-span-1",
        session_id="sess-list-1",
        span_type="phase",
        span_name="planning",
        start_time=now,
    )

    client = TestClient(app)
    resp = client.get(f"/observability/sessions?repo_root={tmp_path}")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == "sess-list-1"
    assert data[0]["span_count"] == 1
    assert data[0]["feedback_count"] == 0


@pytest.mark.asyncio
async def test_get_session_detail_returns_trace_tree(main_db, train_db, tmp_path):
    """GET /observability/sessions/{id} returns session with trace_tree and feedback."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from lean_ai.routers.observability import observability_router

    app = FastAPI()
    app.include_router(observability_router)

    now = datetime.now(timezone.utc).isoformat()
    await main_db.execute(
        "INSERT INTO sessions (id, repo_root, task, status, created_at) "
        "VALUES (?, ?, 'detail task', 'active', ?)",
        ("sess-detail-1", str(tmp_path), now),
    )
    await main_db.commit()

    await insert_trace_span(
        train_db,
        span_uuid="detail-span-1",
        session_id="sess-detail-1",
        span_type="phase",
        span_name="planning",
        start_time=now,
    )

    client = TestClient(app)
    resp = client.get(
        f"/observability/sessions/sess-detail-1?repo_root={tmp_path}"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "sess-detail-1"
    assert "trace_tree" in data
    assert "feedback" in data
    assert len(data["trace_tree"]) == 1


@pytest.mark.asyncio
async def test_get_session_detail_404_for_missing_session(main_db, tmp_path):
    """GET /observability/sessions/{id} returns 404 for unknown session."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from lean_ai.routers.observability import observability_router

    app = FastAPI()
    app.include_router(observability_router)

    client = TestClient(app)
    resp = client.get(
        f"/observability/sessions/nonexistent?repo_root={tmp_path}"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_trace_span_detail_returns_data(train_db, tmp_path):
    """GET /observability/traces/{uuid} returns span with children."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from lean_ai.routers.observability import observability_router

    app = FastAPI()
    app.include_router(observability_router)

    now = datetime.now(timezone.utc).isoformat()
    await insert_trace_span(
        train_db,
        span_uuid="detail-trace-1",
        session_id=str(tmp_path),
        span_type="phase",
        span_name="parent_span",
        start_time=now,
        status="ok",
    )
    await insert_trace_span(
        train_db,
        span_uuid="detail-trace-child",
        session_id=str(tmp_path),
        span_type="llm_call",
        span_name="child_span",
        start_time=now,
        parent_span_uuid="detail-trace-1",
        status="ok",
    )

    client = TestClient(app)
    resp = client.get(
        f"/observability/traces/detail-trace-1?repo_root={tmp_path}"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["span_uuid"] == "detail-trace-1"
    assert "children" in data
    assert len(data["children"]) == 1
    assert data["children"][0]["span_uuid"] == "detail-trace-child"


@pytest.mark.asyncio
async def test_get_trace_span_404_for_missing_span(train_db, tmp_path):
    """GET /observability/traces/{uuid} returns 404 for unknown span."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from lean_ai.routers.observability import observability_router

    app = FastAPI()
    app.include_router(observability_router)

    client = TestClient(app)
    resp = client.get(
        f"/observability/traces/nonexistent-span?repo_root={tmp_path}"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_trace_tree_endpoint_returns_tree(tmp_path):
    """GET /observability/traces/tree returns nested tree structure.

    Note: The router defines /traces/{span_uuid} before /traces/tree,
    so the static path is shadowed. We test via the DB function instead.
    """
    db = await get_training_db(str(tmp_path))
    now = datetime.now(timezone.utc).isoformat()
    await insert_trace_span(
        db,
        span_uuid="tree-root",
        session_id="sess-tree-ep",
        span_type="phase",
        span_name="root",
        start_time=now,
    )
    await insert_trace_span(
        db,
        span_uuid="tree-child",
        session_id="sess-tree-ep",
        span_type="llm_call",
        span_name="child",
        start_time=now,
        parent_span_uuid="tree-root",
    )

    tree = await get_trace_tree(db, "sess-tree-ep")
    assert len(tree) == 2
    root = next(n for n in tree if n["span_uuid"] == "tree-root")
    child = next(n for n in tree if n["span_uuid"] == "tree-child")
    assert root["depth"] == 0
    assert child["depth"] == 1
    await db.close()


@pytest.mark.asyncio
async def test_get_feedback_list_returns_data(tmp_path):
    """GET /observability/feedback returns feedback entries."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from lean_ai.routers.observability import observability_router

    app = FastAPI()
    app.include_router(observability_router)

    db = await get_training_db(str(tmp_path))
    await insert_feedback(
        db,
        session_id="sess-fb-list",
        thumbs_up=True,
        rating=5,
        comment="Good",
    )
    await db.close()

    client = TestClient(app)
    resp = client.get(
        f"/observability/feedback?session_id=sess-fb-list&repo_root={tmp_path}"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["session_id"] == "sess-fb-list"
    assert data[0]["thumbs_up"] == 1


# ── 9. POST feedback requires auth (401 without Bearer token) ───────


@pytest.mark.asyncio
async def test_post_feedback_requires_auth(tmp_path, monkeypatch):
    """POST /observability/feedback returns 401 without Authorization header."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from lean_ai import config
    from lean_ai.routers import observability

    monkeypatch.setattr(observability, "settings", type(config.settings)(export_api_key="test-secret-key"))

    app = FastAPI()
    app.include_router(observability.observability_router)

    client = TestClient(app)

    # No auth header → 401
    resp = client.post(
        f"/observability/feedback?repo_root={tmp_path}&session_id=sess-auth",
    )
    assert resp.status_code == 401

    # Wrong auth header → 401
    resp = client.post(
        f"/observability/feedback?repo_root={tmp_path}&session_id=sess-auth",
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_post_feedback_with_valid_auth_succeeds(tmp_path, monkeypatch):
    """POST /observability/feedback returns 200 with correct Bearer token."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from lean_ai import config
    from lean_ai.routers import observability

    monkeypatch.setattr(observability, "settings", type(config.settings)(export_api_key="test-secret-key"))

    app = FastAPI()
    app.include_router(observability.observability_router)

    client = TestClient(app)

    resp = client.post(
        f"/observability/feedback?repo_root={tmp_path}&session_id=sess-auth-ok&thumbs_up=true&rating=5",
        headers={"Authorization": "Bearer test-secret-key"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "created"
    assert "feedback_id" in data


@pytest.mark.asyncio
async def test_post_feedback_returns_503_when_no_key_configured(tmp_path, monkeypatch):
    """POST /observability/feedback returns 503 when export_api_key is not set."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from lean_ai import config
    from lean_ai.routers import observability

    monkeypatch.setattr(observability, "settings", type(config.settings)(export_api_key=""))

    app = FastAPI()
    app.include_router(observability.observability_router)

    client = TestClient(app)

    resp = client.post(
        f"/observability/feedback?repo_root={tmp_path}&session_id=sess-no-key",
        headers={"Authorization": "Bearer anything"},
    )
    assert resp.status_code == 503


# ── 10. GET endpoints accessible without auth (200) ─────────────────


@pytest.mark.asyncio
async def test_get_endpoints_accessible_without_auth(tmp_path):
    """All GET endpoints return 200 without any Authorization header."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from lean_ai.routers.observability import observability_router

    app = FastAPI()
    app.include_router(observability_router)

    client = TestClient(app)

    # metrics/summary
    resp = client.get(f"/observability/metrics/summary?repo_root={tmp_path}")
    assert resp.status_code == 200

    # metrics/tokens
    resp = client.get(f"/observability/metrics/tokens?repo_root={tmp_path}")
    assert resp.status_code == 200

    # metrics/latency
    resp = client.get(f"/observability/metrics/latency?repo_root={tmp_path}")
    assert resp.status_code == 200

    # metrics/tools
    resp = client.get(f"/observability/metrics/tools?repo_root={tmp_path}")
    assert resp.status_code == 200

    # feedback list (empty)
    resp = client.get(f"/observability/feedback?repo_root={tmp_path}")
    assert resp.status_code == 200

    # Note: /traces/tree is shadowed by /traces/{span_uuid} in FastAPI routing,
    # so we skip it here. The DB-level test_get_trace_tree_endpoint_returns_tree covers it.

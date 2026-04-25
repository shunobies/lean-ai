"""Tests for the training archive schema and insert helpers."""

from datetime import datetime, timedelta, timezone

import pytest

from lean_ai.training.db import (
    get_training_db,
    insert_plan_decision,
    insert_redaction_audit,
    insert_training_trace,
    insert_validation_attempt,
    insert_workflow_event,
    manifest_counts,
    new_trace_uuid,
    prune_expired,
)


@pytest.fixture
async def db(tmp_path):
    conn = await get_training_db(str(tmp_path))
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_schema_creates_expected_tables(db):
    cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    rows = await cursor.fetchall()
    tables = {r[0] for r in rows}
    for expected in (
        "training_traces",
        "plan_decisions",
        "validation_attempts",
        "workflow_events",
        "redaction_audit",
    ):
        assert expected in tables, f"{expected} missing"


@pytest.mark.asyncio
async def test_insert_training_trace_roundtrip(db):
    trace_uuid = new_trace_uuid()
    row_id = await insert_training_trace(
        db,
        trace_uuid=trace_uuid,
        session_id="sess1",
        phase="implementation",
        model_name="qwen3-coder:30b",
        provider="ollama",
        messages=[{"role": "user", "content": "hi"}],
        assistant_output={"content": "hello", "thinking": "", "tool_calls": []},
        outcome="success",
        pair_id="pair-1",
        preference=1,
        pair_kind="plan_revision",
        tokens_prompt=10,
        tokens_completion=20,
        latency_ms=125,
        scrubbed=True,
    )
    assert row_id > 0

    cursor = await db.execute(
        "SELECT trace_uuid, phase, scrubbed, preference FROM training_traces WHERE id = ?",
        (row_id,),
    )
    row = await cursor.fetchone()
    assert row["trace_uuid"] == trace_uuid
    assert row["phase"] == "implementation"
    assert row["scrubbed"] == 1
    assert row["preference"] == 1


@pytest.mark.asyncio
async def test_insert_plan_decision_and_validation_attempt(db):
    pd = await insert_plan_decision(
        db,
        session_id="sess1",
        revision_count=1,
        task="do X",
        plan_before={"steps": [1]},
        plan_after={"steps": [1, 2]},
        feedback="add error handling",
        decision="approved",
        trace_uuid="t-before",
        pair_trace_uuid="t-after",
    )
    va = await insert_validation_attempt(
        db,
        session_id="sess1",
        attempt_num=1,
        failures_before={"test": "boom"},
        diagnosis="PYTHONPATH",
        fix_tool_calls=[{"tool": "edit_file"}],
        failures_after={},
        succeeded=True,
        trace_uuid="t-va",
    )
    assert pd > 0 and va > 0


@pytest.mark.asyncio
async def test_insert_workflow_event_and_redaction_audit(db):
    evt = await insert_workflow_event(
        db,
        session_id="s",
        event_type="loop_detected",
        payload={"tool_name": "grep_files", "count": 3},
    )
    red = await insert_redaction_audit(
        db,
        source_table="training_traces",
        source_id="t-1",
        pattern_name="openai_key",
        replacement="<REDACTED:openai-key>",
        match_preview="abc123def456",
    )
    assert evt > 0 and red > 0


@pytest.mark.asyncio
async def test_manifest_counts_aggregates(db):
    await insert_training_trace(
        db,
        trace_uuid=new_trace_uuid(),
        session_id="s1",
        phase="implementation",
        model_name="m1",
        provider="ollama",
        messages=[],
        assistant_output={},
        outcome="success",
    )
    await insert_training_trace(
        db,
        trace_uuid=new_trace_uuid(),
        session_id="s1",
        phase="phase3",
        model_name="m2",
        provider="ollama",
        messages=[],
        assistant_output={},
        outcome="rejected",
    )
    await insert_plan_decision(
        db,
        session_id="s1",
        revision_count=0,
        task="t",
        plan_before=None,
        plan_after=None,
        feedback=None,
        decision="approved",
    )

    manifest = await manifest_counts(db)
    assert manifest["total_traces"] == 2
    assert manifest["by_model"] == {"m1": 1, "m2": 1}
    assert manifest["by_phase"] == {"implementation": 1, "phase3": 1}
    assert manifest["by_outcome"] == {"success": 1, "rejected": 1}
    assert manifest["plan_decisions"] == 1


@pytest.mark.asyncio
async def test_prune_expired_removes_old_rows(db):
    # Fresh row
    await insert_training_trace(
        db,
        trace_uuid=new_trace_uuid(),
        session_id="s",
        phase="p",
        model_name="m",
        provider="ollama",
        messages=[],
        assistant_output={},
    )
    # Stale row — backdate via direct UPDATE
    old_id = await insert_training_trace(
        db,
        trace_uuid=new_trace_uuid(),
        session_id="s",
        phase="p",
        model_name="m",
        provider="ollama",
        messages=[],
        assistant_output={},
    )
    past = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    await db.execute(
        "UPDATE training_traces SET created_at = ? WHERE id = ?",
        (past, old_id),
    )
    await db.commit()

    counts = await prune_expired(db, retention_days=365)
    assert counts["training_traces"] == 1

    cursor = await db.execute("SELECT COUNT(*) FROM training_traces")
    (remaining,) = await cursor.fetchone()
    assert remaining == 1


@pytest.mark.asyncio
async def test_prune_zero_days_is_noop(db):
    await insert_training_trace(
        db,
        trace_uuid=new_trace_uuid(),
        session_id="s",
        phase="p",
        model_name="m",
        provider="ollama",
        messages=[],
        assistant_output={},
    )
    counts = await prune_expired(db, retention_days=0)
    assert counts == {}

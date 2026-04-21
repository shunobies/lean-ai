"""Tests for high-level capture helpers."""

import pytest

from lean_ai.training.capture import (
    capture_plan_decision,
    capture_turn,
    capture_validation_attempt,
    capture_workflow_event,
)
from lean_ai.training.db import get_training_db


@pytest.mark.asyncio
async def test_capture_turn_writes_scrubbed_row(tmp_path):
    root = str(tmp_path)
    trace_uuid = await capture_turn(
        root,
        session_id="sess1",
        phase="implementation",
        model_name="qwen3-coder:30b",
        provider="ollama",
        messages=[
            {"role": "user", "content": "here is sk-proj-abcdefghijklmnop1234567890 please"},
        ],
        assistant_output={"content": "ok", "thinking": "", "tool_calls": []},
        outcome="success",
    )
    assert trace_uuid

    db = await get_training_db(root)
    try:
        cursor = await db.execute(
            "SELECT messages, scrubbed FROM training_traces WHERE trace_uuid = ?",
            (trace_uuid,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["scrubbed"] == 1
        assert "sk-proj-abcdefg" not in row["messages"]
        assert "<REDACTED:openai-key>" in row["messages"]

        cursor = await db.execute(
            "SELECT COUNT(*) FROM redaction_audit WHERE source_id = ?",
            (trace_uuid,),
        )
        (count,) = await cursor.fetchone()
        assert count >= 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_capture_plan_decision_roundtrip(tmp_path):
    root = str(tmp_path)
    row_id = await capture_plan_decision(
        root,
        session_id="sess1",
        revision_count=1,
        task="implement feature X",
        plan_before={"steps": ["a", "b"]},
        plan_after={"steps": ["a", "b", "c"]},
        feedback="please add error handling for sk-proj-abcdefghijkl1234567890",
        decision="approved",
    )
    assert row_id

    db = await get_training_db(root)
    try:
        cursor = await db.execute(
            "SELECT feedback, decision FROM plan_decisions WHERE id = ?",
            (row_id,),
        )
        row = await cursor.fetchone()
        assert row["decision"] == "approved"
        assert "sk-proj-" not in row["feedback"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_capture_validation_attempt_roundtrip(tmp_path):
    root = str(tmp_path)
    row_id = await capture_validation_attempt(
        root,
        session_id="sess1",
        attempt_num=1,
        failures_before={"test": "ModuleNotFoundError"},
        diagnosis="PYTHONPATH missing",
        fix_tool_calls=[{"tool": "edit_file", "args": {"path": "setup.py"}}],
        failures_after={},
        succeeded=True,
    )
    assert row_id

    db = await get_training_db(root)
    try:
        cursor = await db.execute(
            "SELECT succeeded, diagnosis FROM validation_attempts WHERE id = ?",
            (row_id,),
        )
        row = await cursor.fetchone()
        assert row["succeeded"] == 1
        assert row["diagnosis"] == "PYTHONPATH missing"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_capture_workflow_event_roundtrip(tmp_path):
    root = str(tmp_path)
    row_id = await capture_workflow_event(
        root,
        session_id="sess1",
        event_type="loop_detected",
        payload={"tool_name": "grep_files", "count": 3},
    )
    assert row_id

    db = await get_training_db(root)
    try:
        cursor = await db.execute(
            "SELECT event_type, payload FROM workflow_events WHERE id = ?",
            (row_id,),
        )
        row = await cursor.fetchone()
        assert row["event_type"] == "loop_detected"
        assert "grep_files" in row["payload"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_capture_disabled_returns_none(tmp_path, monkeypatch):
    from lean_ai.config import settings as cfg

    monkeypatch.setattr(cfg, "enable_training_capture", False)
    result = await capture_turn(
        str(tmp_path),
        session_id="s", phase="p", model_name="m", provider="ollama",
        messages=[], assistant_output={},
    )
    assert result is None

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


# ── Tier S/A new capture helpers ────────────────────────────────


@pytest.mark.asyncio
async def test_capture_turn_records_role_and_turn_index(tmp_path):
    from lean_ai.training.capture import capture_turn as _cap

    root = str(tmp_path)
    trace_uuid = await _cap(
        root,
        session_id="s1", phase="planning.phase1",
        role="primary", turn_index=3,
        model_name="qwen3-coder:30b", provider="ollama",
        messages=[{"role": "user", "content": "hi"}],
        assistant_output={"content": "hello", "tool_calls": []},
    )
    assert trace_uuid

    db = await get_training_db(root)
    try:
        cursor = await db.execute(
            "SELECT role, turn_index, phase FROM training_traces "
            "WHERE trace_uuid = ?",
            (trace_uuid,),
        )
        row = await cursor.fetchone()
        assert row["role"] == "primary"
        assert row["turn_index"] == 3
        assert row["phase"] == "planning.phase1"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_capture_tool_execution(tmp_path):
    from lean_ai.training.capture import capture_tool_execution

    root = str(tmp_path)
    row_id = await capture_tool_execution(
        root,
        session_id="s1",
        tool_name="read_file",
        arguments={"path": "foo.py"},
        result="file contents...",
        success=True,
        latency_ms=42,
        phase="implementation",
        trace_uuid="trace-1",
    )
    assert row_id
    db = await get_training_db(root)
    try:
        cursor = await db.execute(
            "SELECT tool_name, success, latency_ms, trace_uuid "
            "FROM tool_executions WHERE id = ?",
            (row_id,),
        )
        row = await cursor.fetchone()
        assert row["tool_name"] == "read_file"
        assert row["success"] == 1
        assert row["latency_ms"] == 42
        assert row["trace_uuid"] == "trace-1"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_capture_tool_compression_records_ratio(tmp_path):
    from lean_ai.training.capture import capture_tool_compression

    root = str(tmp_path)
    raw = "x" * 1000
    compressed = "summary"
    row_id = await capture_tool_compression(
        root,
        session_id="s1",
        phase="planning.phase2",
        tool_name="read_file",
        raw_output=raw,
        compressed_output=compressed,
        worker_model="qwen2.5-coder:7b",
        worker_provider="ollama",
    )
    assert row_id
    db = await get_training_db(root)
    try:
        cursor = await db.execute(
            "SELECT compression_ratio, raw_length, compressed_length "
            "FROM tool_compressions WHERE id = ?",
            (row_id,),
        )
        row = await cursor.fetchone()
        assert row["raw_length"] == 1000
        assert row["compressed_length"] == len(compressed)
        assert 0 < row["compression_ratio"] < 0.1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_capture_clarification_outcome(tmp_path):
    from lean_ai.training.capture import capture_clarification

    root = str(tmp_path)
    row_id = await capture_clarification(
        root,
        session_id="s1",
        question="which database engine?",
        answer="postgres",
        outcome="answered",
        task="wire up persistence",
    )
    assert row_id
    db = await get_training_db(root)
    try:
        cursor = await db.execute(
            "SELECT question, answer, outcome FROM clarifications "
            "WHERE id = ?",
            (row_id,),
        )
        row = await cursor.fetchone()
        assert row["question"].startswith("which database")
        assert row["answer"] == "postgres"
        assert row["outcome"] == "answered"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_capture_phase2_synthesis(tmp_path):
    from lean_ai.training.capture import capture_phase2_synthesis

    root = str(tmp_path)
    row_id = await capture_phase2_synthesis(
        root,
        session_id="s1",
        task="add audit log",
        scope="problem\n-\n...",
        observations=[{"file_path": "a.py", "role": "modify", "reason": "x"}],
        scratchpad="notes",
        journal="journal",
        exploration_output="prose output",
        file_summary={"files_to_modify": ["a.py"]},
    )
    assert row_id
    db = await get_training_db(root)
    try:
        cursor = await db.execute(
            "SELECT task, file_summary FROM phase2_syntheses WHERE id = ?",
            (row_id,),
        )
        row = await cursor.fetchone()
        assert row["task"] == "add audit log"
        assert "files_to_modify" in row["file_summary"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_capture_diff_decision_roundtrip(tmp_path):
    from lean_ai.training.capture import capture_diff_decision

    root = str(tmp_path)
    row_id = await capture_diff_decision(
        root,
        session_id="s1",
        file_path="src/foo.py",
        accepted=False,
        diff_hash="abc123",
        note="introduces regression",
    )
    assert row_id
    db = await get_training_db(root)
    try:
        cursor = await db.execute(
            "SELECT file_path, accepted, diff_hash FROM diff_decisions "
            "WHERE id = ?",
            (row_id,),
        )
        row = await cursor.fetchone()
        assert row["file_path"] == "src/foo.py"
        assert row["accepted"] == 0
        assert row["diff_hash"] == "abc123"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_capture_workflow_event_with_trace_uuid(tmp_path):
    root = str(tmp_path)
    row_id = await capture_workflow_event(
        root,
        session_id="s1",
        event_type="loop_detected",
        payload={"tool_name": "read_file", "count": 3},
        trace_uuid="trace-xyz",
    )
    assert row_id
    db = await get_training_db(root)
    try:
        cursor = await db.execute(
            "SELECT event_type, trace_uuid FROM workflow_events WHERE id = ?",
            (row_id,),
        )
        row = await cursor.fetchone()
        assert row["event_type"] == "loop_detected"
        assert row["trace_uuid"] == "trace-xyz"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_manifest_includes_new_tables(tmp_path):
    from lean_ai.training.capture import (
        capture_clarification,
        capture_diff_decision,
        capture_tool_compression,
        capture_tool_execution,
    )
    from lean_ai.training.db import manifest_counts

    root = str(tmp_path)
    await capture_tool_execution(
        root, session_id="s", tool_name="read_file",
        arguments={}, result="ok", success=True, latency_ms=1,
    )
    await capture_tool_compression(
        root, session_id="s", phase="planning.phase2",
        tool_name="read_file", raw_output="x" * 500,
        compressed_output="y", worker_model="w", worker_provider="ollama",
    )
    await capture_clarification(
        root, session_id="s", question="q", answer="a",
        outcome="answered",
    )
    await capture_diff_decision(
        root, session_id="s", file_path="a.py", accepted=True,
    )

    db = await get_training_db(root)
    try:
        manifest = await manifest_counts(db)
    finally:
        await db.close()

    assert manifest["tool_executions"] == 1
    assert manifest["tool_compressions"] == 1
    assert manifest["clarifications"] == 1
    assert manifest["diff_decisions"] == 1

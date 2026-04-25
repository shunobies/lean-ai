"""Integration tests: workflow hooks produce training archive rows.

These tests don't drive the full workflow (which requires an LLM).
Instead they invoke the hooks directly with realistic payloads and
verify that the expected rows appear in ``training.db``.
"""

import pytest

from lean_ai.training.db import get_training_db
from lean_ai.workflow.hooks import (
    on_plan_decision,
    on_validation_attempt_complete,
    on_workflow_event,
)


class _NullLLM:
    """Stand-in for the LLM client — never actually invoked here."""

    model_name = "test-model"


@pytest.mark.asyncio
async def test_plan_decision_hook_writes_training_archive(tmp_path):
    root = str(tmp_path)
    await on_plan_decision(
        root,
        "sess-1",
        _NullLLM(),
        task="implement feature X",
        plan_before='{"steps": [1]}',
        feedback="add error handling",
        plan_after='{"steps": [1, 2]}',
        decision="approved",
        revision_count=1,
    )

    db = await get_training_db(root)
    try:
        cursor = await db.execute(
            "SELECT decision, revision_count, feedback FROM plan_decisions",
        )
        rows = await cursor.fetchall()
        assert len(rows) == 1
        assert rows[0]["decision"] == "approved"
        assert rows[0]["revision_count"] == 1
        assert rows[0]["feedback"] == "add error handling"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_plan_decision_first_time_approval_logs_archive(tmp_path):
    """First-try approval (no rejection) should still hit the archive."""
    root = str(tmp_path)
    await on_plan_decision(
        root,
        "sess-1",
        _NullLLM(),
        task="implement Y",
        plan_before="",
        feedback="",
        plan_after='{"steps": [1]}',
        decision="approved",
        revision_count=0,
    )

    db = await get_training_db(root)
    try:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM plan_decisions WHERE decision = 'approved'",
        )
        (count,) = await cursor.fetchone()
        assert count == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_validation_attempt_hook_records_both_outcomes(tmp_path):
    root = str(tmp_path)
    # First: a failed attempt
    await on_validation_attempt_complete(
        root,
        "sess-1",
        _NullLLM(),
        task="implement Y",
        attempt_num=1,
        failing_commands=["test"],
        error_output="ModuleNotFoundError: foo",
        diagnosis="PYTHONPATH missing",
        fix_tool_calls=[{"tool": "edit_file"}],
        succeeded=False,
        failures_before={"test": "ModuleNotFoundError"},
        failures_after={"test": "ModuleNotFoundError"},
    )
    # Then a successful attempt
    await on_validation_attempt_complete(
        root,
        "sess-1",
        _NullLLM(),
        task="implement Y",
        attempt_num=2,
        failing_commands=["test"],
        error_output="ModuleNotFoundError: foo",
        diagnosis="set PYTHONPATH=src",
        fix_tool_calls=[{"tool": "run_command"}],
        succeeded=True,
        failures_before={"test": "ModuleNotFoundError"},
        failures_after={},
    )

    db = await get_training_db(root)
    try:
        cursor = await db.execute(
            "SELECT attempt_num, succeeded FROM validation_attempts ORDER BY attempt_num"
        )
        rows = await cursor.fetchall()
        assert len(rows) == 2
        assert rows[0]["succeeded"] == 0
        assert rows[1]["succeeded"] == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_workflow_event_cancellation_captured(tmp_path):
    root = str(tmp_path)
    await on_workflow_event(
        root,
        "sess-1",
        event_type="cancellation",
        payload={"task": "do X", "mode": "plan"},
    )

    db = await get_training_db(root)
    try:
        cursor = await db.execute("SELECT event_type, payload FROM workflow_events")
        rows = await cursor.fetchall()
        assert len(rows) == 1
        assert rows[0]["event_type"] == "cancellation"
        assert "do X" in rows[0]["payload"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_scrubber_applies_to_plan_feedback(tmp_path):
    root = str(tmp_path)
    await on_plan_decision(
        root,
        "sess-1",
        _NullLLM(),
        task="do X with api",
        plan_before='{"old": true}',
        feedback="use this key sk-proj-abcdefghij1234567890zzzz to call api",
        plan_after='{"new": true}',
        decision="approved",
        revision_count=1,
    )

    db = await get_training_db(root)
    try:
        cursor = await db.execute("SELECT feedback FROM plan_decisions")
        row = await cursor.fetchone()
        assert row is not None
        assert "sk-proj-abcdefghij" not in row["feedback"]

        cursor = await db.execute(
            "SELECT pattern_name FROM redaction_audit",
        )
        audit_rows = await cursor.fetchall()
        names = {r[0] for r in audit_rows}
        assert "openai_key" in names
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_training_capture_disabled_skips_rows(tmp_path, monkeypatch):
    from lean_ai.config import settings as cfg

    monkeypatch.setattr(cfg, "enable_training_capture", False)

    root = str(tmp_path)
    await on_plan_decision(
        root,
        "s",
        _NullLLM(),
        task="t",
        plan_before="",
        feedback="",
        plan_after="x",
        decision="approved",
        revision_count=0,
    )
    await on_workflow_event(
        root,
        "s",
        event_type="cancellation",
        payload=None,
    )

    # With capture disabled, no rows should be written. But because
    # plan_decision also runs memory extraction (which opens the
    # *memory* DB), the training.db file may or may not exist — only
    # verify that if it does exist, it's empty.
    try:
        db = await get_training_db(root)
    except Exception:
        return
    try:
        cursor = await db.execute("SELECT COUNT(*) FROM plan_decisions")
        (pd,) = await cursor.fetchone()
        cursor = await db.execute("SELECT COUNT(*) FROM workflow_events")
        (we,) = await cursor.fetchone()
        assert pd == 0
        assert we == 0
    finally:
        await db.close()

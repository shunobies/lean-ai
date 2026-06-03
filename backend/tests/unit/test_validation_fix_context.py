"""Tests for post-validation fix-loop context."""

from __future__ import annotations

import pytest

from lean_ai.config import settings
from lean_ai.workflow import validation as workflow_validation
from lean_ai.workflow.validation import _run_validation_fix_loop


class CapturingClient:
    model_name = "primary"

    def __init__(self) -> None:
        self.messages = None

    async def chat_with_tools(self, **kwargs):
        self.messages = kwargs["messages"]
        return [], "fixed"


class DummyStateManager:
    session_id = "sess-validation"

    async def get_state_async(self):
        return self.get_cached_state()

    def get_cached_state(self):
        return type(
            "State",
            (),
            {
                "journal_entries": ["changed planner validation"],
                "scratchpad_content": "working on full-suite failure",
            },
        )()


async def _noop_executor(_name: str, _args: dict) -> str:
    return "OK"


@pytest.mark.asyncio
async def test_validation_fix_prompt_includes_phase4_plan_context(monkeypatch, tmp_path):
    client = CapturingClient()
    calls = 0

    async def _fake_post_validation(repo_root, ws):
        nonlocal calls
        calls += 1
        return {"test": {"success": True, "output": "ok", "full_output": "ok"}}

    monkeypatch.setattr(settings, "post_validation_max_retries", 1)
    monkeypatch.setattr(settings, "implementation_max_tokens", 1024)
    monkeypatch.setattr(workflow_validation, "load_condensed_context", lambda _repo_root: "")
    monkeypatch.setattr(workflow_validation, "make_tool_executor", lambda *a, **k: _noop_executor)
    monkeypatch.setattr(workflow_validation, "_run_post_validation", _fake_post_validation)

    result = await _run_validation_fix_loop(
        repo_root=str(tmp_path),
        ws=None,
        llm_client=client,
        context="repo context",
        validation_results={
            "test": {
                "success": False,
                "output": "failed",
                "full_output": "pytest failed",
            }
        },
        state_manager=DummyStateManager(),
        task="Implement planner changes",
        plan_context="PLAN MARKDOWN:\n## Scope\nPlanner change\n\nPLAN JSON:\n{}",
    )

    assert result["test"]["success"] is True
    assert calls == 1
    user_text = client.messages[1]["content"]
    assert "APPROVED PHASE 4 PLAN CONTEXT" in user_text
    assert "PLAN MARKDOWN" in user_text
    assert "pytest failed" in user_text
    assert any("[SESSION JOURNAL]" in msg["content"] for msg in client.messages)

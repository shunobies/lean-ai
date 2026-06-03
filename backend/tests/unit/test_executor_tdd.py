"""Tests for active TDD execution behavior."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from lean_ai.config import settings
from lean_ai.llm.plan_schema import ExecutionPlan, PlanStep
from lean_ai.workflow import executor as workflow_executor
from lean_ai.workflow import tool_executor as workflow_tool_executor
from lean_ai.workflow.executor import _run_tdd_execution, execute_plan


class FakeSession:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, data: dict[str, object]) -> None:
        self.sent.append(data)

    def send_nowait(self, data: dict[str, object]) -> None:
        self.sent.append(data)

    def is_connected(self) -> bool:
        return True


class DummyClient:
    def __init__(self, name: str) -> None:
        self.model_name = name

    async def chat_with_tools(self, **kwargs):
        raise AssertionError("chat_with_tools should not run in this test")


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        scope="Implement auth callback.",
        tdd_mode=True,
        steps=[
            PlanStep(
                step_number=2,
                tool="edit_file",
                file_path="src/auth.py",
                instruction="Implement the auth callback.",
                reason="Land the implementation after tests are written.",
            )
        ],
        tdd_test_steps=[
            PlanStep(
                step_number=1,
                tool="create_file",
                file_path="tests/test_auth.py",
                instruction="Write tests for src/auth.py callback behavior.",
                reason="Pin the intended behavior first.",
            )
        ],
        affected_files=["src/auth.py", "tests/test_auth.py"],
        test_strategy="Run pytest.",
    )


def _empty_plan() -> ExecutionPlan:
    return ExecutionPlan(
        scope="No-op plan.",
        steps=[],
        affected_files=[],
        test_strategy="Run pytest.",
    )


async def _noop_executor(_name: str, _args: dict) -> str:
    return "OK"


async def _noop_async(*_args, **_kwargs):
    return None


async def _noop_retention_pass(*_args, **_kwargs) -> dict:
    return {}


@asynccontextmanager
async def _noop_trace_span(*_args, **_kwargs):
    yield None


def _noop_factory(*_args, **_kwargs):
    return _noop_executor


def _const_tdd_test_prompt(*_args, **_kwargs) -> str:
    return "tdd-test-system"


def _const_tdd_impl_prompt(*_args, **_kwargs) -> str:
    return "tdd-impl-system"


def _const_step_prompt(*_args, **_kwargs) -> str:
    return "system"


def _noop_side_effect(*_args, **_kwargs) -> None:
    return None


@pytest.fixture(autouse=True)
def _disable_unit_test_side_effects(monkeypatch):
    monkeypatch.setattr(workflow_executor, "ensure_primary_role_tuning", _noop_async)
    monkeypatch.setattr(workflow_executor, "ensure_expert_role_tuning", _noop_async)
    monkeypatch.setattr(workflow_executor, "trace_span", _noop_trace_span)
    monkeypatch.setattr(
        "lean_ai.training.maintenance.run_retention_pass",
        _noop_retention_pass,
    )
    monkeypatch.setattr(
        "lean_ai.workflow.hooks.fire_workflow_event",
        lambda **_kwargs: None,
    )


@pytest.mark.asyncio
async def test_run_tdd_execution_falls_back_to_primary_client_for_test_writing(
    monkeypatch,
    tmp_path,
):
    ws = FakeSession()
    primary = DummyClient("primary")
    seen_clients: list[DummyClient] = []

    async def _run_step(step, client, tools, executor, sys_prompt, label_prefix="", telemetry=None):
        seen_clients.append(client)
        return True

    monkeypatch.setattr(workflow_executor, "make_tool_executor", _noop_factory)
    monkeypatch.setattr(workflow_executor, "load_execution_context", lambda _repo_root: "")
    monkeypatch.setattr(
        workflow_executor,
        "build_tdd_test_writing_prompt",
        _const_tdd_test_prompt,
    )
    monkeypatch.setattr(
        workflow_executor,
        "build_tdd_step_system_prompt",
        _const_tdd_impl_prompt,
    )

    ok, metrics = await _run_tdd_execution(
        plan=_plan(),
        repo_root=str(tmp_path),
        ws=ws,
        llm_client=primary,
        expert_llm_client=None,
        session_id="sess-1",
        dispatcher=None,
        cb=None,
        step_artifacts={},
        run_step=_run_step,
    )

    assert ok is True
    assert seen_clients[0] is primary
    assert metrics["test_writer_role"] == "primary_fallback"
    assert any(msg.get("stage") == "tdd_test_writing" for msg in ws.sent)


@pytest.mark.asyncio
async def test_execute_plan_activates_tdd_path_and_reports_metrics(monkeypatch, tmp_path):
    ws = FakeSession()
    primary = DummyClient("primary")
    called: list[bool] = []

    async def _fake_tdd_execution(**kwargs):
        called.append(True)
        return True, {
            "planned_test_steps": 1,
            "implementation_steps": 1,
            "implementation_steps_with_test_checks": 1,
            "steps_missing_test_checks": 0,
            "red_green_retries": 2,
            "test_writer_role": "primary_fallback",
        }

    monkeypatch.setattr(workflow_executor, "_run_tdd_execution", _fake_tdd_execution)
    monkeypatch.setattr(workflow_executor, "make_tool_executor", _noop_factory)
    monkeypatch.setattr(workflow_executor, "load_execution_context", lambda _repo_root: "")
    monkeypatch.setattr(workflow_executor, "build_step_system_prompt", _const_step_prompt)
    monkeypatch.setattr(workflow_executor, "read_journal", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        workflow_executor,
        "invalidate_metadata_cache_for_paths",
        _noop_side_effect,
    )
    monkeypatch.setattr(settings, "enable_strict_test_contract", True)
    monkeypatch.setattr(settings, "enable_post_validation", False)
    monkeypatch.setattr(settings, "enable_project_context", False)
    monkeypatch.setattr(settings, "enable_integrations", False)
    monkeypatch.setattr(settings, "enable_session_memory", False)

    await execute_plan(
        plan=_plan(),
        task="Implement auth callback",
        repo_root=str(tmp_path),
        ws=ws,
        llm_client=primary,
        context="repo context",
        branch_name="",
        session_id="sess-2",
        expert_llm_client=None,
    )

    assert called == [True]
    checklist = next(msg for msg in ws.sent if msg.get("type") == "execution_checklist")
    assert checklist["steps"][0]["description"].startswith("[TEST] ")
    complete = next(msg for msg in ws.sent if msg.get("type") == "complete")
    assert "TDD Metrics:" in complete["summary"]
    assert "red-green retries: 2" in complete["summary"]


@pytest.mark.asyncio
async def test_execute_plan_runs_post_validation_even_without_file_edits(
    monkeypatch,
    tmp_path,
):
    ws = FakeSession()
    primary = DummyClient("primary")
    called: list[bool] = []

    async def _fake_post_validation(repo_root, ws_arg):
        called.append(True)
        return {"test": {"success": True, "output": "ok", "full_output": "ok"}}

    monkeypatch.setattr(workflow_executor, "make_tool_executor", _noop_factory)
    monkeypatch.setattr(workflow_executor, "load_execution_context", lambda _repo_root: "")
    monkeypatch.setattr(workflow_executor, "build_step_system_prompt", _const_step_prompt)
    monkeypatch.setattr(workflow_executor, "read_journal", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(workflow_executor, "_run_post_validation", _fake_post_validation)
    monkeypatch.setattr(
        workflow_executor,
        "invalidate_metadata_cache_for_paths",
        _noop_side_effect,
    )
    monkeypatch.setattr(settings, "enable_post_validation", True)
    monkeypatch.setattr(settings, "enable_project_context", False)
    monkeypatch.setattr(settings, "enable_integrations", False)
    monkeypatch.setattr(settings, "enable_session_memory", False)

    await execute_plan(
        plan=_empty_plan(),
        task="Run validation",
        repo_root=str(tmp_path),
        ws=ws,
        llm_client=primary,
        context="repo context",
        branch_name="",
        session_id="sess-4",
    )

    assert called == [True]


@pytest.mark.asyncio
async def test_execute_plan_validation_fix_gets_state_manager_and_plan_context(
    monkeypatch,
    tmp_path,
):
    ws = FakeSession()
    primary = DummyClient("primary")
    captured: dict[str, object] = {}

    class DummyStateManager:
        session_id = "sess-5"

        async def get_state_async(self):
            return type(
                "State",
                (),
                {"journal_entries": [], "scratchpad_content": ""},
            )()

        def get_cached_state(self):
            return type(
                "State",
                (),
                {"journal_entries": [], "scratchpad_content": ""},
            )()

        def save(self):
            return None

        async def save_checkpoint_async(self, **kwargs):
            return "checkpoint"

    async def _fake_post_validation(repo_root, ws_arg):
        return {"test": {"success": False, "output": "boom", "full_output": "boom"}}

    async def _fake_fix_loop(*args, **kwargs):
        captured["state_manager"] = args[5]
        captured["plan_context"] = kwargs.get("plan_context")
        return {"test": {"success": True, "output": "ok", "full_output": "ok"}}

    monkeypatch.setattr(workflow_executor, "make_tool_executor", _noop_factory)
    monkeypatch.setattr(workflow_executor, "load_execution_context", lambda _repo_root: "")
    monkeypatch.setattr(workflow_executor, "build_step_system_prompt", _const_step_prompt)
    monkeypatch.setattr(workflow_executor, "read_journal", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(workflow_executor, "_run_post_validation", _fake_post_validation)
    monkeypatch.setattr(workflow_executor, "_run_validation_fix_loop", _fake_fix_loop)
    monkeypatch.setattr(
        workflow_executor,
        "invalidate_metadata_cache_for_paths",
        _noop_side_effect,
    )
    monkeypatch.setattr(settings, "enable_post_validation", True)
    monkeypatch.setattr(settings, "post_validation_max_retries", 1)
    monkeypatch.setattr(settings, "enable_project_context", False)
    monkeypatch.setattr(settings, "enable_integrations", False)
    monkeypatch.setattr(settings, "enable_session_memory", False)

    manager = DummyStateManager()
    await execute_plan(
        plan=_empty_plan(),
        task="Run validation",
        repo_root=str(tmp_path),
        ws=ws,
        llm_client=primary,
        context="repo context",
        branch_name="",
        session_id="sess-5",
        state_manager=manager,
    )

    assert captured["state_manager"] is manager
    assert "PLAN MARKDOWN" in str(captured["plan_context"])
    assert "PLAN JSON" in str(captured["plan_context"])


@pytest.mark.asyncio
async def test_make_tool_executor_blocks_test_file_edits_in_tdd_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(workflow_tool_executor, "append_event", _noop_side_effect)
    monkeypatch.setattr(
        workflow_tool_executor,
        "_fire_tool_execution_capture",
        _noop_side_effect,
    )

    executor = workflow_tool_executor.make_tool_executor(
        str(tmp_path),
        ws=None,
        session_id="sess-3",
        tdd_protect_tests=True,
    )
    result = await executor(
        "edit_file",
        {"path": "tests/test_auth.py", "search": "old", "replace": "new"},
    )

    assert "cannot modify test files during the implementation phase" in result

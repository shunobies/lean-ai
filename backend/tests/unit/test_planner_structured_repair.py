"""Tests for planner-side structured-output repair retries."""

from __future__ import annotations

import pytest

from lean_ai.config import settings
from lean_ai.llm.base import StructuredOutputError, validate_structured_output
from lean_ai.llm.plan_schema import (
    ChangeDesign,
    DesignAndRisks,
    ExecutionPlan,
    FileObservation,
    FileSummary,
    PlanStep,
    ScopeDocument,
    VerificationPlan,
)
from lean_ai.llm.planner import _run_phase5_verification, create_plan
from lean_ai.llm.planner_helpers import _chat_structured_with_repair, _revise_plan


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.messages.append(payload)


class FakeExpert:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.model_name = "expert-test-model"

    async def chat_structured(
        self,
        messages,
        schema,
        temperature=None,
        max_tokens=None,
        *,
        retry_on_validation_error=True,
        **kwargs,
    ):
        self.calls.append(
            {
                "messages": list(messages),
                "schema": schema,
                "retry_on_validation_error": retry_on_validation_error,
                "kwargs": dict(kwargs),
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakePlannerClient:
    def __init__(self, outputs: list[tuple[list, str]]) -> None:
        self.outputs = list(outputs)
        self.model_name = "planner-test-model"

    async def chat_with_tools(self, *args, **kwargs):
        return self.outputs.pop(0)


def _make_structured_error(schema, raw_json: str) -> StructuredOutputError:
    try:
        validate_structured_output(raw_json, schema)
    except StructuredOutputError as exc:
        return exc
    raise AssertionError("Expected structured validation to fail")


def _implementation_step() -> PlanStep:
    return PlanStep(
        step_number=1,
        tool="edit_file",
        file_path="src/app.py",
        instruction="Update the handler implementation.",
        reason="Implement the requested behavior.",
        context="",
    )


def _execution_plan() -> ExecutionPlan:
    return ExecutionPlan(
        scope="Update the app handler.",
        steps=[_implementation_step()],
        affected_files=["src/app.py"],
        test_strategy="Run pytest.",
    )


def _verification_plan() -> VerificationPlan:
    return VerificationPlan(
        steps=[
            PlanStep(
                step_number=1,
                tool="create_file",
                file_path="tests/test_app.py",
                instruction="Add a regression test for the updated handler.",
                reason="Verify the new behavior.",
                context="",
            ),
            PlanStep(
                step_number=2,
                tool="run_tests",
                file_path="",
                instruction="Run pytest -q",
                reason="Confirm the suite passes.",
                context="",
            ),
        ]
    )


@pytest.mark.asyncio
async def test_phase4_execution_plan_repair_retries_once():
    error = _make_structured_error(ExecutionPlan, '{"scope": }')
    expert = FakeExpert([error, _execution_plan()])
    ws = FakeWebSocket()

    result = await _chat_structured_with_repair(
        messages=[
            {"role": "system", "content": "assemble plan"},
            {"role": "user", "content": "build an execution plan"},
        ],
        schema=ExecutionPlan,
        expert=expert,
        max_tokens=4000,
        artifact_label="structured plan",
        ws=ws,
        phase=4,
    )

    assert result == _execution_plan()
    assert len(expert.calls) == 2
    assert expert.calls[0]["retry_on_validation_error"] is False
    assert expert.calls[1]["retry_on_validation_error"] is False
    repair_message = expert.calls[1]["messages"][-1]["content"]
    assert "Exact JSON error:" in repair_message
    assert "line 1 column" in repair_message
    assert "Previous invalid JSON" in repair_message
    assert ws.messages[-1]["summary"] == "Repairing malformed ExecutionPlan JSON..."


@pytest.mark.asyncio
async def test_revise_plan_uses_execution_plan_repair_flow():
    error = _make_structured_error(ExecutionPlan, '{"scope": }')
    expert = FakeExpert([error, _execution_plan()])
    ws = FakeWebSocket()

    result = await _revise_plan(
        task="Fix the handler",
        revision_context="PREVIOUS PLAN: {}",
        llm_client=expert,
        context="repo context",
        ws=ws,
        expert_llm_client=expert,
    )

    assert result == _execution_plan()
    assert len(expert.calls) == 2
    assert "Revise the plan based on the user's feedback" in expert.calls[0]["messages"][1]["content"]
    assert "ExecutionPlan" in expert.calls[1]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_phase5_verification_plan_repair_appends_steps(tmp_path):
    saved_tdd = settings.enable_tdd
    settings.enable_tdd = False
    try:
        error = _make_structured_error(VerificationPlan, '{"steps": }')
        expert = FakeExpert([error, _verification_plan()])
        ws = FakeWebSocket()
        plan = _execution_plan()

        elapsed = await _run_phase5_verification(
            plan=plan,
            task="Add tests",
            file_summary="",
            file_summary_obj=FileSummary(),
            design_and_risks_obj=DesignAndRisks(),
            test_command="pytest -q",
            expert=expert,
            plan_assembly_max_tokens=4000,
            ws=ws,
            repo_root=str(tmp_path),
            session_id="s1",
            on_thinking=None,
            on_metrics=None,
            on_metrics_reset=None,
        )

        assert elapsed >= 0
        assert len(expert.calls) == 2
        assert expert.calls[0]["retry_on_validation_error"] is False
        assert "VerificationPlan" in expert.calls[1]["messages"][-1]["content"]
        assert "tests/test_app.py" in plan.affected_files
        assert any(step.file_path == "tests/test_app.py" for step in plan.steps)
        assert ws.messages[-2]["summary"] == "Repairing malformed VerificationPlan JSON..."
    finally:
        settings.enable_tdd = saved_tdd


@pytest.mark.asyncio
async def test_structured_repair_failure_raises_user_safe_error():
    first = _make_structured_error(ExecutionPlan, '{"scope": }')
    second = _make_structured_error(ExecutionPlan, '{"scope": }')
    expert = FakeExpert([first, second])

    with pytest.raises(RuntimeError) as excinfo:
        await _chat_structured_with_repair(
            messages=[
                {"role": "system", "content": "assemble plan"},
                {"role": "user", "content": "build an execution plan"},
            ],
            schema=ExecutionPlan,
            expert=expert,
            max_tokens=4000,
            artifact_label="structured plan",
        )

    assert "malformed ExecutionPlan JSON twice" in str(excinfo.value)
    assert "validation error for ExecutionPlan" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_revise_plan_falls_back_to_previous_plan_when_repair_crashes(monkeypatch):
    async def _boom(**kwargs):
        raise RuntimeError("repair loop crashed")

    monkeypatch.setattr("lean_ai.llm.planner_helpers._chat_structured_with_repair", _boom)

    previous = _execution_plan()
    result = await _revise_plan(
        task="Fix the handler",
        revision_context="PREVIOUS PLAN: {}",
        llm_client=FakeExpert([]),
        context="repo context",
        previous_plan=previous,
    )

    assert result.steps == previous.steps
    assert result.affected_files == previous.affected_files
    assert any("automatic plan revision failed" in warning for warning in result.plan_validation_warnings)


@pytest.mark.asyncio
async def test_create_plan_returns_fallback_plan_when_phase4_aborts(monkeypatch):
    async def _fake_scope(**kwargs):
        scope = ScopeDocument(
            problem="Update the handler.",
            deliverables=["Handler change"],
            in_scope=["Handler logic"],
            out_of_scope=[],
            downstream_consumers=[],
            assumptions=[],
            success_criteria=[],
            risks=[],
        )
        return scope, "PROBLEM / PURPOSE:\nUpdate the handler.\n", True

    async def _fake_phase2(**kwargs):
        return (
            FileSummary(
                files_to_modify=[
                    FileObservation(
                        file_path="src/app.py",
                        role="modify",
                        reason="Handler implementation lives here.",
                        relevant_sections="10-40",
                        key_snippets=["def handler():\n    pass"],
                    )
                ]
            ),
            "FILES TO MODIFY:\n1. src/app.py — Handler implementation lives here.\n",
            0.01,
        )

    async def _fake_design(**kwargs):
        return DesignAndRisks(
            change_designs=[
                ChangeDesign(
                    file_path="src/app.py",
                    decisions="Update the handler logic without changing the public API.",
                )
            ]
        )

    async def _boom(**kwargs):
        raise RuntimeError("phase 4 assembly exploded")

    async def _no_memory(*args, **kwargs):
        return ""

    monkeypatch.setattr("lean_ai.llm.planner._retrieve_session_memories", _no_memory)
    monkeypatch.setattr("lean_ai.llm.planner._synthesize_scope", _fake_scope)
    monkeypatch.setattr("lean_ai.llm.planner.run_phase2_exploration", _fake_phase2)
    monkeypatch.setattr("lean_ai.llm.planner._synthesize_design_and_risks", _fake_design)
    monkeypatch.setattr("lean_ai.llm.planner._chat_structured_with_repair", _boom)

    client = FakePlannerClient(
        outputs=[
            ([], "phase 1 prose"),
            ([], "phase 3 prose"),
        ]
    )

    plan = await create_plan(
        task="Fix the handler",
        repo_root=".",
        llm_client=client,
        context="repo context",
        ws=None,
    )

    assert isinstance(plan, ExecutionPlan)
    assert any("planning fallback:" in warning for warning in plan.plan_validation_warnings)
    assert "src/app.py" in plan.affected_files
    assert any(step.file_path == "src/app.py" for step in plan.steps)

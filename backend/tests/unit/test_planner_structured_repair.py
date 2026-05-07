"""Tests for planner-side structured-output repair retries."""

from __future__ import annotations

import pytest

from lean_ai.config import settings
from lean_ai.llm.base import StructuredOutputError, validate_structured_output
from lean_ai.llm.plan_schema import DesignAndRisks, ExecutionPlan, FileSummary, PlanStep, VerificationPlan
from lean_ai.llm.planner import _run_phase5_verification
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

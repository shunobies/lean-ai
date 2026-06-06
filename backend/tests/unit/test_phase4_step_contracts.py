"""Tests for Phase 4 job-contract plan steps."""

from lean_ai.llm.plan_schema import (
    CoreFunctionalityTag,
    ExecutionPlan,
    FileObservation,
    FileSummary,
    PlanStep,
    StepChangeTarget,
    StepSuccessCheck,
)
from lean_ai.llm.planner import (
    _check_core_functionality_success_checked,
    _check_full_suite_command_available,
    _check_success_checks_cover_affected_files,
    _check_tdd_required_for_executable_files,
    _check_tdd_test_contract_cover_affected_files,
)
from lean_ai.workflow.executor import _step_scope_error


def test_plan_step_accepts_job_contract_without_legacy_instruction():
    step = PlanStep(
        step_number=1,
        job="Update the auth callback flow.",
        inputs=[{"source": "src/auth.py", "details": "callback handler and token parser"}],
        may_change=[
            StepChangeTarget(path="src/auth.py", change="Adjust callback validation only.")
        ],
        must_not_change=["Public token format"],
        allowed_tools=["read_file", "grep_files", "edit_file", "run_tests"],
        output_shape="Callback rejects missing state and keeps existing token response shape.",
        success_checks=[
            StepSuccessCheck(
                description="Auth callback tests pass.",
                tool="run_tests",
                command="pytest tests/test_auth.py -q",
                expected="Command exits successfully.",
            )
        ],
        blocked_protocol="If the callback handler is absent, report the missing file.",
        reason="The task changes callback validation.",
    )

    assert step.instruction == "Update the auth callback flow."
    assert step.file_path == "src/auth.py"
    assert "task_complete" in step.allowed_tools
    assert step.tool == "edit_file"


def test_step_scope_rejects_tool_outside_allowed_contract():
    """Tool-name blocking is removed; allowed_tools is advisory metadata during execution.

    Using a tool not listed in allowed_tools should not produce a scope error,
    as long as it is not a file-write tool targeting a path outside may_change.
    """
    step = PlanStep(
        step_number=1,
        job="Update src/app.py only.",
        may_change=[StepChangeTarget(path="src/app.py", change="Small behavior edit.")],
        allowed_tools=["read_file", "edit_file", "task_complete"],
        output_shape="src/app.py contains the updated behavior.",
        blocked_protocol="Report blocker.",
    )

    error = _step_scope_error(step, "run_command", {"command": "touch src/other.py"})

    assert error is None


def test_success_check_coverage_uses_phase4_contracts():
    plan = ExecutionPlan(
        scope="Update auth.",
        steps=[
            PlanStep(
                step_number=1,
                job="Update auth callback and tests.",
                may_change=[
                    StepChangeTarget(path="src/auth.py", change="Edit callback validation."),
                    StepChangeTarget(path="tests/test_auth.py", change="Cover callback state."),
                ],
                allowed_tools=["read_file", "edit_file", "run_tests", "task_complete"],
                output_shape="src/auth.py rejects missing state; tests cover src/auth.py.",
                success_checks=[
                    StepSuccessCheck(
                        description="tests/test_auth.py covers src/auth.py callback state.",
                        tool="run_tests",
                        command="pytest tests/test_auth.py -q",
                    )
                ],
                blocked_protocol="Report blocker.",
            )
        ],
        affected_files=["src/auth.py"],
        test_strategy="Run pytest.",
    )
    summary = FileSummary(
        files_to_modify=[
            FileObservation(file_path="src/auth.py", role="modify", reason="behavior")
        ]
    )

    assert _check_success_checks_cover_affected_files(plan, summary) == ([], False)


def test_core_functionality_requires_regression_success_check():
    plan = ExecutionPlan(
        scope="Update auth.",
        steps=[
            PlanStep(
                step_number=1,
                job="Update auth callback and regression coverage.",
                may_change=[
                    StepChangeTarget(path="src/auth.py", change="Edit callback validation."),
                    StepChangeTarget(
                        path="tests/regression/regression_auth_test.py",
                        change="Add regression coverage for AuthCallback.",
                    ),
                ],
                allowed_tools=["read_file", "edit_file", "run_tests", "task_complete"],
                output_shape="Regression test covers AuthCallback in src/auth.py.",
                success_checks=[
                    StepSuccessCheck(
                        description=(
                            "REGRESSION: AuthCallback in src/auth.py rejects "
                            "missing state."
                        ),
                        tool="run_tests",
                        command="pytest tests/regression/regression_auth_test.py -q",
                    )
                ],
                blocked_protocol="Report blocker.",
            )
        ],
        affected_files=["src/auth.py", "tests/regression/regression_auth_test.py"],
        test_strategy="Run pytest.",
        core_functionality=[
            CoreFunctionalityTag(
                entity="AuthCallback",
                file_path="src/auth.py",
                reason="Public callback route.",
                source_signal="public_api",
                confidence="high",
            )
        ],
    )

    assert _check_core_functionality_success_checked(plan) == ([], False)


def test_tdd_contract_accepts_authored_test_steps_for_affected_files():
    plan = ExecutionPlan(
        scope="Update auth.",
        tdd_mode=True,
        steps=[
            PlanStep(
                step_number=2,
                job="Implement auth callback behavior.",
                may_change=[
                    StepChangeTarget(
                        path="src/auth.py",
                        change="Edit callback validation.",
                    )
                ],
                allowed_tools=["read_file", "edit_file", "task_complete"],
                output_shape="src/auth.py rejects missing state.",
                success_checks=[
                    StepSuccessCheck(
                        description="Auth tests pass.",
                        tool="run_tests",
                        command="pytest tests/test_auth.py -q",
                    )
                ],
                blocked_protocol="Report blocker.",
            )
        ],
        tdd_test_steps=[
            PlanStep(
                step_number=1,
                tool="create_file",
                file_path="tests/test_auth.py",
                instruction="Add tests covering src/auth.py callback state validation.",
                reason="Pin the intended callback behavior before implementation.",
            )
        ],
        affected_files=["src/auth.py", "tests/test_auth.py"],
        test_strategy="Run pytest.",
    )
    summary = FileSummary(
        files_to_modify=[
            FileObservation(file_path="src/auth.py", role="modify", reason="behavior")
        ]
    )

    assert _check_tdd_test_contract_cover_affected_files(plan, summary) == ([], False)


def test_tdd_contract_rejects_structural_check_without_targeted_tests():
    plan = ExecutionPlan(
        scope="Update auth.",
        tdd_mode=True,
        steps=[
            PlanStep(
                step_number=2,
                job="Implement auth callback behavior.",
                may_change=[StepChangeTarget(path="src/auth.py", change="Edit callback.")],
                allowed_tools=["edit_file", "run_command"],
                output_shape="src/auth.py rejects missing state.",
                success_checks=[
                    StepSuccessCheck(
                        description="Inspect the auth AST.",
                        tool="run_command",
                        command="python scripts/check_ast.py src/auth.py",
                    )
                ],
                blocked_protocol="Report blocker.",
            )
        ],
        tdd_test_steps=[
            PlanStep(
                step_number=1,
                tool="create_file",
                file_path="tests/test_auth.py",
                instruction="Add tests covering src/auth.py callback behavior.",
                reason="Pin behavior.",
            )
        ],
        affected_files=["src/auth.py", "tests/test_auth.py"],
        test_strategy="Run pytest.",
    )
    summary = FileSummary(
        files_to_modify=[FileObservation(file_path="src/auth.py", role="modify", reason="behavior")]
    )

    warnings, blocking = _check_tdd_test_contract_cover_affected_files(plan, summary)

    assert blocking is True
    assert "run_tests check naming that test" in warnings[0]


def test_strict_contract_blocks_executable_plan_without_tdd_steps(monkeypatch):
    from lean_ai.config import settings

    monkeypatch.setattr(settings, "enable_strict_test_contract", True)
    plan = ExecutionPlan(
        scope="Update auth.",
        tdd_mode=False,
        steps=[
            PlanStep(
                step_number=1,
                job="Implement auth callback behavior.",
                may_change=[
                    StepChangeTarget(path="src/auth.py", change="Edit callback validation.")
                ],
                allowed_tools=["read_file", "edit_file", "run_tests", "task_complete"],
                output_shape="src/auth.py rejects missing state.",
                blocked_protocol="Report blocker.",
            )
        ],
        affected_files=["src/auth.py"],
        test_strategy="Run pytest.",
    )
    summary = FileSummary(
        files_to_modify=[FileObservation(file_path="src/auth.py", role="modify", reason="behavior")]
    )

    warnings, blocking = _check_tdd_required_for_executable_files(plan, summary)

    assert blocking is True
    assert "strict TDD contract requires" in warnings[0]


def test_pytest_success_check_does_not_count_as_test_setup(monkeypatch):
    from lean_ai.config import settings

    monkeypatch.setattr(settings, "enable_strict_test_contract", True)
    plan = ExecutionPlan(
        scope="Update auth.",
        tdd_mode=False,
        steps=[
            PlanStep(
                step_number=1,
                job="Implement auth callback behavior.",
                may_change=[
                    StepChangeTarget(path="src/auth.py", change="Edit callback validation.")
                ],
                allowed_tools=["read_file", "edit_file", "run_tests", "task_complete"],
                output_shape="src/auth.py rejects missing state.",
                success_checks=[
                    StepSuccessCheck(
                        description="Run auth tests.",
                        tool="run_tests",
                        command="pytest tests/test_auth.py -q",
                    )
                ],
                blocked_protocol="Report blocker.",
            )
        ],
        affected_files=["src/auth.py"],
        test_strategy="Run pytest.",
    )
    summary = FileSummary(
        files_to_modify=[FileObservation(file_path="src/auth.py", role="modify", reason="behavior")]
    )

    warnings, blocking = _check_tdd_required_for_executable_files(plan, summary)

    assert blocking is True
    assert "strict TDD contract requires" in warnings[0]


def test_strict_contract_allows_plan_that_adds_test_setup(monkeypatch):
    from lean_ai.config import settings

    monkeypatch.setattr(settings, "enable_strict_test_contract", True)
    plan = ExecutionPlan(
        scope="Set up tests and update auth.",
        tdd_mode=False,
        steps=[
            PlanStep(
                step_number=1,
                job="Create pytest testing infrastructure and record the test command.",
                may_change=[
                    StepChangeTarget(path=".lean_ai/commands.json", change="Record pytest command."),
                    StepChangeTarget(path="pyproject.toml", change="Add pytest config."),
                ],
                allowed_tools=["edit_file", "create_file", "run_command", "task_complete"],
                output_shape="Testing infrastructure exists and records pytest.",
                blocked_protocol="Report blocker.",
            )
        ],
        affected_files=["src/auth.py", ".lean_ai/commands.json", "pyproject.toml"],
        test_strategy="Run pytest once configured.",
    )
    summary = FileSummary(
        files_to_modify=[FileObservation(file_path="src/auth.py", role="modify", reason="behavior")]
    )

    assert _check_tdd_required_for_executable_files(plan, summary) == ([], False)
    assert _check_full_suite_command_available(plan, summary, test_command="") == ([], False)


def test_strict_contract_requires_full_suite_command(monkeypatch):
    from lean_ai.config import settings

    monkeypatch.setattr(settings, "enable_strict_test_contract", True)
    plan = ExecutionPlan(
        scope="Update auth.",
        tdd_mode=True,
        steps=[
            PlanStep(
                step_number=2,
                job="Implement auth callback behavior.",
                may_change=[
                    StepChangeTarget(path="src/auth.py", change="Edit callback validation.")
                ],
                allowed_tools=["read_file", "edit_file", "task_complete"],
                output_shape="src/auth.py rejects missing state.",
                blocked_protocol="Report blocker.",
            )
        ],
        tdd_test_steps=[
            PlanStep(
                step_number=1,
                tool="create_file",
                file_path="tests/test_auth.py",
                instruction="Add tests for src/auth.py callback validation.",
                reason="Pin behavior.",
            )
        ],
        affected_files=["src/auth.py", "tests/test_auth.py"],
        test_strategy="Run pytest.",
    )
    summary = FileSummary(
        files_to_modify=[FileObservation(file_path="src/auth.py", role="modify", reason="behavior")]
    )

    warnings, blocking = _check_full_suite_command_available(plan, summary, test_command="")

    assert blocking is True
    assert "project test command" in warnings[0]


def test_core_functionality_can_be_satisfied_by_tdd_regression_test_step():
    plan = ExecutionPlan(
        scope="Update auth.",
        tdd_mode=True,
        steps=[
            PlanStep(
                step_number=2,
                job="Implement auth callback behavior.",
                may_change=[
                    StepChangeTarget(
                        path="src/auth.py",
                        change="Edit callback validation.",
                    )
                ],
                allowed_tools=["read_file", "edit_file", "task_complete"],
                output_shape="src/auth.py rejects missing state.",
                blocked_protocol="Report blocker.",
            )
        ],
        tdd_test_steps=[
            PlanStep(
                step_number=1,
                tool="create_file",
                file_path="tests/regression/regression_auth_test.py",
                instruction="Add regression coverage for AuthCallback in src/auth.py.",
                reason="REGRESSION: AuthCallback must reject missing state.",
            )
        ],
        affected_files=["src/auth.py", "tests/regression/regression_auth_test.py"],
        test_strategy="Run pytest.",
        core_functionality=[
            CoreFunctionalityTag(
                entity="AuthCallback",
                file_path="src/auth.py",
                reason="Public callback route.",
                source_signal="public_api",
                confidence="high",
            )
        ],
    )

    assert _check_core_functionality_success_checked(plan) == ([], False)

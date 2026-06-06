"""Unit tests for implementation-step execution guards."""

from pathlib import Path

from lean_ai.llm.base import ToolCall
from lean_ai.llm.plan_schema import PlanStep, StepSuccessCheck
from lean_ai.llm.prompts import STEP_EXECUTION_SYSTEM_PROMPT
from lean_ai.workflow.executor import (
    _append_incomplete_entry,
    _clear_incomplete_file,
    _collect_tdd_test_files,
    _diff_repo_state,
    _step_completion_error,
    _step_scope_error,
)


def _step(
    tool: str,
    *,
    file_path: str = "",
    instruction: str = "do the thing",
) -> PlanStep:
    return PlanStep(
        step_number=1,
        tool=tool,
        file_path=file_path,
        instruction=instruction,
        reason="",
    )


def test_step_scope_rejects_wrong_file_write_path():
    step = _step("edit_file", file_path="src/app.py")

    error = _step_scope_error(
        step,
        "edit_file",
        {"path": "src/other.py", "search": "x", "replace": "y"},
    )

    assert error is not None
    assert "src/app.py" in error
    assert "src/other.py" in error


def test_step_scope_rejects_direct_file_write_in_non_write_step():
    """Tool-name blocking is removed; create_file on a may_change path is allowed even in a non-write step."""
    step = PlanStep(
        step_number=1,
        job="Run tests for the module.",
        may_change=[{"path": "tests/test_new.py", "change": "Create test file."}],
        allowed_tools=["run_tests"],
        output_shape="Tests pass.",
        blocked_protocol="Report blocker.",
    )

    error = _step_scope_error(
        step,
        "create_file",
        {"path": "tests/test_new.py", "content": "pass"},
    )

    assert error is None


def test_step_scope_enforces_may_change_path_boundary():
    """Path-based may_change boundaries still block writes outside the declared scope."""
    step = PlanStep(
        step_number=1,
        job="Update src/app.py only.",
        may_change=[{"path": "src/app.py", "change": "Small behavior edit."}],
        allowed_tools=["edit_file"],
        output_shape="src/app.py contains the updated behavior.",
        blocked_protocol="Report blocker.",
    )

    # Writing to a path NOT in may_change should still error
    error = _step_scope_error(
        step,
        "edit_file",
        {"path": "src/other.py", "search": "x", "replace": "y"},
    )
    assert error is not None
    assert "src/app.py" in error
    assert "src/other.py" in error

    # Writing to a path that IS in may_change should be allowed
    error_ok = _step_scope_error(
        step,
        "edit_file",
        {"path": "src/app.py", "search": "x", "replace": "y"},
    )
    assert error_ok is None

    # create_file to an out-of-scope path should also error
    error_create = _step_scope_error(
        step,
        "create_file",
        {"path": "src/new_module.py", "content": "pass"},
    )
    assert error_create is not None
    assert "src/app.py" in error_create


def test_step_scope_allows_default_read_only_helper_when_not_listed_explicitly():
    step = PlanStep(
        step_number=1,
        job="Update src/app.py only.",
        may_change=[{"path": "src/app.py", "change": "Small behavior edit."}],
        allowed_tools=["edit_file", "run_tests"],
        output_shape="src/app.py contains the updated behavior.",
        blocked_protocol="Report blocker.",
    )

    error = _step_scope_error(step, "grep_files", {"pattern": "app"})

    assert error is None


def test_step_completion_requires_task_complete():
    step = _step("edit_file", file_path="src/app.py")
    calls = [ToolCall(tool_name="edit_file", parameters={"path": "src/app.py"})]

    error = _step_completion_error(
        step,
        task_complete_seen=False,
        successful_calls=calls,
        attempted_calls=calls,
    )

    assert error is not None
    assert "task_complete" in error


def test_step_completion_accepts_same_path_fallback_mutation():
    step = _step("create_file", file_path="src/app.py")
    calls = [ToolCall(tool_name="edit_file", parameters={"path": "src/app.py"})]

    error = _step_completion_error(
        step,
        task_complete_seen=True,
        successful_calls=calls,
        attempted_calls=calls,
    )

    assert error is None


def test_collect_tdd_test_files_includes_edited_test_files():
    steps = [
        _step("create_file", file_path="tests/test_feature.py"),
        _step("edit_file", file_path="tests/conftest.py"),
        _step("edit_file", file_path="src/app.py"),
        _step("edit_file", file_path="tests/conftest.py"),
    ]

    review_files = _collect_tdd_test_files(steps)

    assert review_files == ["tests/test_feature.py", "tests/conftest.py"]


def test_step_completion_rejects_failed_attempted_success_check():
    step = PlanStep(
        step_number=1,
        job="Update src/app.py.",
        may_change=[{"path": "src/app.py", "change": "Change behavior."}],
        allowed_tools=["edit_file", "run_tests"],
        output_shape="Behavior is updated.",
        success_checks=[
            StepSuccessCheck(
                description="Targeted tests pass.",
                tool="run_tests",
                command="test-runner tests/app.test",
            )
        ],
        blocked_protocol="Report blocker.",
    )
    write = ToolCall(tool_name="edit_file", parameters={"path": "src/app.py"})
    failed_test = ToolCall(
        tool_name="run_tests",
        parameters={"command": "test-runner tests/app.test"},
    )

    error = _step_completion_error(
        step,
        task_complete_seen=True,
        successful_calls=[write],
        attempted_calls=[write, failed_test],
    )

    assert error is not None
    assert "did not run required success check" in error


def test_diff_repo_state_reports_added_changed_and_deleted_paths():
    before = {
        "src/a.py": (10, 100),
        "src/b.py": (20, 200),
    }
    after = {
        "src/a.py": (10, 101),
        "src/c.py": (5, 50),
    }

    changed = _diff_repo_state(before, after)

    assert changed == ["src/a.py", "src/b.py", "src/c.py"]


def test_clear_incomplete_file_removes_stale_file(tmp_path):
    _append_incomplete_entry(
        str(tmp_path),
        step_label="Step 1",
        detail="Something failed",
    )
    incomplete = Path(tmp_path) / ".lean_ai" / "incomplete.md"
    assert incomplete.exists()

    _clear_incomplete_file(str(tmp_path))

    assert not incomplete.exists()


def test_step_execution_prompt_includes_scratchpad_and_journal_policy():
    assert "SCRATCHPAD (volatile)" in STEP_EXECUTION_SYSTEM_PROMPT
    assert "JOURNAL (permanent)" in STEP_EXECUTION_SYSTEM_PROMPT

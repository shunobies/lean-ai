"""Tests for Layer 2 Phase 5 coverage validator.

The validator warns when an executable affected_file has no matching
test step. Warnings are appended to ``plan.plan_validation_warnings``
via the call site in ``_run_phase5_verification``. This suite tests the
pure helper directly.
"""

from __future__ import annotations

from lean_ai.llm.plan_schema import (
    ExecutionPlan,
    FileObservation,
    FileSummary,
    PlanStep,
    VerificationPlan,
)
from lean_ai.llm.planner import (
    _check_affected_files_covered,
    _has_executable_extension,
)


def _plan(*, affected: list[str]) -> ExecutionPlan:
    return ExecutionPlan(
        scope="test scope",
        steps=[],
        affected_files=list(affected),
        test_strategy="n/a",
    )


def _verif(steps: list[PlanStep]) -> VerificationPlan:
    return VerificationPlan(steps=steps)


def _test_step(*, file_path: str, instruction: str = "") -> PlanStep:
    return PlanStep(
        step_number=1,
        tool="create_file",
        file_path=file_path,
        instruction=instruction or "create test",
        reason="reason",
    )


def test_executable_extension_recognizes_common_languages() -> None:
    for p in ("foo.py", "a/b/c.ts", "mod.go", "handler.rs", "App.java"):
        assert _has_executable_extension(p), p
    for p in ("README.md", "config.yaml", "lock.json", "data.csv"):
        assert not _has_executable_extension(p), p


def test_no_warning_when_every_executable_file_is_covered() -> None:
    plan = _plan(affected=["src/foo.py", "src/bar.ts"])
    fs = FileSummary(
        files_to_create=[
            FileObservation(
                file_path="src/foo.py",
                role="create",
                reason="new",
            )
        ],
        files_to_modify=[
            FileObservation(
                file_path="src/bar.ts",
                role="modify",
                reason="upd",
            )
        ],
    )
    verif = _verif(
        [
            _test_step(file_path="tests/test_foo.py"),
            _test_step(file_path="tests/bar.spec.ts", instruction="tests for src/bar.ts"),
        ]
    )

    warnings = _check_affected_files_covered(verif, plan, fs)
    assert warnings == []


def test_warning_when_executable_file_has_no_test() -> None:
    plan = _plan(affected=["src/foo.py", "src/bar.ts"])
    fs = FileSummary(
        files_to_create=[
            FileObservation(file_path="src/foo.py", role="create", reason="new"),
            FileObservation(file_path="src/bar.ts", role="create", reason="new"),
        ],
    )
    # Only foo has a test step — bar.ts is uncovered.
    verif = _verif([_test_step(file_path="tests/test_foo.py")])

    warnings = _check_affected_files_covered(verif, plan, fs)
    assert any("src/bar.ts" in w for w in warnings), warnings
    assert all("src/foo.py" not in w for w in warnings), warnings


def test_doc_and_config_files_are_ignored() -> None:
    plan = _plan(affected=["README.md", "config/prod.yaml", "src/foo.py"])
    fs = FileSummary(
        files_to_modify=[
            FileObservation(file_path="README.md", role="modify", reason="docs"),
            FileObservation(file_path="config/prod.yaml", role="modify", reason="cfg"),
            FileObservation(file_path="src/foo.py", role="modify", reason="code"),
        ],
    )
    # Only the python file needs a test — and it has one.
    verif = _verif([_test_step(file_path="tests/test_foo.py")])

    warnings = _check_affected_files_covered(verif, plan, fs)
    assert warnings == []


def test_filename_match_in_instruction_counts_as_coverage() -> None:
    plan = _plan(affected=["src/services/foo.py"])
    fs = FileSummary(
        files_to_create=[
            FileObservation(
                file_path="src/services/foo.py",
                role="create",
                reason="new",
            )
        ],
    )
    # Test step references the filename in its instruction, not path.
    verif = _verif(
        [
            _test_step(
                file_path="tests/test_services.py",
                instruction="cover the behavior of foo.py's public API",
            )
        ]
    )

    warnings = _check_affected_files_covered(verif, plan, fs)
    assert warnings == []


def test_falls_back_to_affected_files_when_file_summary_is_none() -> None:
    plan = _plan(affected=["src/foo.py", "README.md"])
    verif = _verif([_test_step(file_path="tests/test_foo.py")])

    warnings = _check_affected_files_covered(verif, plan, file_summary=None)
    # README is filtered by extension; foo.py is covered.
    assert warnings == []


def test_run_tests_step_does_not_count_as_coverage() -> None:
    plan = _plan(affected=["src/foo.py"])
    fs = FileSummary(
        files_to_create=[
            FileObservation(
                file_path="src/foo.py",
                role="create",
                reason="new",
            )
        ],
    )
    # The only step is run_tests — not a create_file step, so
    # coverage is absent.
    verif = _verif(
        [
            PlanStep(
                step_number=1,
                tool="run_tests",
                file_path="",
                instruction="pytest tests/ -q",
                reason="execute test suite",
            )
        ]
    )

    warnings = _check_affected_files_covered(verif, plan, fs)
    assert any("src/foo.py" in w for w in warnings)

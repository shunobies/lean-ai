"""Tests for Layer 9 core-functionality regression-coverage validator.

Validates that Phase 5's ``_check_core_functionality_covered`` warns
when a tagged core entity has no matching regression test step. Also
verifies the confidence-gating policy and the schema additions.
"""

from __future__ import annotations

from unittest.mock import patch

from lean_ai.llm.plan_schema import (
    CoreFunctionalityTag,
    DesignAndRisks,
    ExecutionPlan,
    PlanStep,
    VerificationPlan,
)
from lean_ai.llm.planner import _check_core_functionality_covered


def _plan(
    *,
    core: list[CoreFunctionalityTag],
    affected: list[str] | None = None,
) -> ExecutionPlan:
    return ExecutionPlan(
        scope="s",
        steps=[],
        affected_files=affected or [],
        test_strategy="n/a",
        core_functionality=core,
    )


def _step(
    *,
    file_path: str,
    instruction: str = "",
    output_shape: str = "",
    reason: str = "",
) -> PlanStep:
    return PlanStep(
        step_number=1,
        tool="create_file",
        file_path=file_path,
        instruction=instruction or "create test",
        output_shape=output_shape,
        reason=reason,
    )


def test_schema_accepts_tags_and_defaults_empty() -> None:
    dar = DesignAndRisks()
    assert dar.core_functionality == []
    plan = ExecutionPlan(
        scope="s",
        steps=[],
        affected_files=[],
        test_strategy="",
    )
    assert plan.core_functionality == []


def test_tag_round_trip_preserves_fields() -> None:
    tag = CoreFunctionalityTag(
        entity="login",
        file_path="src/auth.py",
        reason="Primary deliverable — users can't access the app without it.",
        source_signal="phase1_deliverable",
        confidence="high",
    )
    dar = DesignAndRisks(core_functionality=[tag])
    dumped = dar.model_dump_json()
    restored = DesignAndRisks.model_validate_json(dumped)
    assert restored.core_functionality[0].entity == "login"
    assert restored.core_functionality[0].source_signal == "phase1_deliverable"


def test_no_warnings_when_no_tags() -> None:
    plan = _plan(core=[])
    verif = VerificationPlan(steps=[])
    assert _check_core_functionality_covered(verif, plan) == []


def test_warns_when_core_entity_has_no_regression_step() -> None:
    plan = _plan(
        core=[
            CoreFunctionalityTag(
                entity="login",
                file_path="src/auth.py",
                reason="primary deliverable",
                source_signal="phase1_deliverable",
                confidence="high",
            ),
        ]
    )
    # Only a regular test step, NOT in regression convention.
    verif = VerificationPlan(
        steps=[
            _step(
                file_path="tests/test_auth.py",
                instruction="tests for login function in src/auth.py",
            )
        ]
    )

    warnings = _check_core_functionality_covered(verif, plan)
    assert len(warnings) == 1
    assert "login" in warnings[0]
    assert "src/auth.py" in warnings[0]


def test_silent_when_entity_has_matching_regression_step_by_entity_name() -> None:
    plan = _plan(
        core=[
            CoreFunctionalityTag(
                entity="login",
                file_path="src/auth.py",
                reason="primary deliverable",
                source_signal="phase1_deliverable",
                confidence="high",
            ),
        ]
    )
    verif = VerificationPlan(
        steps=[
            _step(
                file_path="tests/regression/regression_auth_test.py",
                instruction="regression test for login — must not be removed",
            )
        ]
    )

    assert _check_core_functionality_covered(verif, plan) == []


def test_silent_when_entity_has_matching_regression_step_by_path() -> None:
    plan = _plan(
        core=[
            CoreFunctionalityTag(
                entity="do_thing",
                file_path="src/services/thing.py",
                reason="public API",
                source_signal="public_api",
                confidence="medium",
            ),
        ]
    )
    # No entity name, but the file path is mentioned.
    verif = VerificationPlan(
        steps=[
            _step(
                file_path="tests/regression/regression_thing_test.py",
                output_shape="Regression test covers the contract of src/services/thing.py.",
            )
        ]
    )

    assert _check_core_functionality_covered(verif, plan) == []


def test_regular_test_file_does_not_count_as_regression_coverage() -> None:
    plan = _plan(
        core=[
            CoreFunctionalityTag(
                entity="login",
                file_path="src/auth.py",
                reason="primary deliverable",
                source_signal="phase1_deliverable",
                confidence="high",
            ),
        ]
    )
    # Step IS about login, but the test file is NOT regression convention.
    verif = VerificationPlan(
        steps=[
            _step(
                file_path="tests/test_auth.py",
                instruction="tests login happy path",
            )
        ]
    )

    warnings = _check_core_functionality_covered(verif, plan)
    assert warnings, "regular tests should not satisfy a core tag"


def test_low_confidence_tag_is_not_enforced_at_default_threshold() -> None:
    # Default settings.core_functionality_min_confidence == "medium".
    plan = _plan(
        core=[
            CoreFunctionalityTag(
                entity="hidden",
                file_path="src/maybe.py",
                reason="not sure",
                source_signal="public_api",
                confidence="low",
            ),
        ]
    )
    verif = VerificationPlan(steps=[])  # No tests at all.
    assert _check_core_functionality_covered(verif, plan) == []


def test_min_confidence_low_enforces_all_tags() -> None:
    plan = _plan(
        core=[
            CoreFunctionalityTag(
                entity="hidden",
                file_path="src/maybe.py",
                reason="not sure",
                source_signal="public_api",
                confidence="low",
            ),
        ]
    )
    verif = VerificationPlan(steps=[])

    with patch(
        "lean_ai.llm.planner.settings.core_functionality_min_confidence",
        "low",
    ):
        warnings = _check_core_functionality_covered(verif, plan)
    assert warnings, "low threshold should enforce low-confidence tags"


def test_min_confidence_high_ignores_medium_tags() -> None:
    plan = _plan(
        core=[
            CoreFunctionalityTag(
                entity="middling",
                file_path="src/middle.py",
                reason="public_api",
                source_signal="public_api",
                confidence="medium",
            ),
        ]
    )
    verif = VerificationPlan(steps=[])

    with patch(
        "lean_ai.llm.planner.settings.core_functionality_min_confidence",
        "high",
    ):
        warnings = _check_core_functionality_covered(verif, plan)
    assert warnings == [], "high threshold should ignore medium-confidence tags"

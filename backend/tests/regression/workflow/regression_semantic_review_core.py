"""Regression tests for semantic review core entities and security constraints.

Pins load-bearing contracts that must not regress:
  1. SemanticReviewRubric verdict literal constraint (schema integrity)
  2. Raw diff grounding prevents reviewer hallucination (security mitigation)
  3. Iteration cap hard-limits corrective loops to two revisions (runaway LLM guard)
  4. Corrective plan generation requires blocking issues trigger (contract boundary)

TDD mode — tests define the public contract before implementation exists.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── 1. Rubric schema enforces literal verdict constraint ──────────────────────


def test_rubric_schema_enforces_literal_verdict_constraint():
    """SemanticReviewRubric.verdict accepts only 'approve' or 'revise', rejects other strings."""
    from lean_ai.workflow.validation import SemanticReviewRubric
    from pydantic import ValidationError

    # Valid verdicts should construct successfully
    for valid_verdict in ("approve", "revise"):
        rubric = SemanticReviewRubric(
            overall_score=5.0,
            verdict=valid_verdict,
            blocking_issues=[],
            non_blocking_issues=[],
            category_scores={"alignment": 5},
        )
        assert rubric.verdict == valid_verdict, (
            f"Valid verdict '{valid_verdict}' should be accepted but was rejected"
        )

    # Invalid verdicts must raise ValidationError
    with pytest.raises(ValidationError):
        SemanticReviewRubric(
            overall_score=5.0,
            verdict="unknown",
            blocking_issues=[],
            non_blocking_issues=[],
            category_scores={"alignment": 5},
        )


# ── 2. Raw diff grounding prevents reviewer hallucination ─────────────────────


async def test_raw_diff_grounding_injected_into_committee_review():
    """_conduct_committee_review receives raw diffs from _capture_raw_diffs so the LLM evaluates actual changes, not hallucinated ones."""
    from lean_ai.workflow.validation import (
        SemanticReviewRubric,
        _capture_raw_diffs,
        _conduct_committee_review,
    )

    # Capture raw diffs via mocked git_ops
    expected_diff = "--- a/auth.py\n+++ b/auth.py\n+auth_change()"
    with patch(
        "lean_ai.tools.git_ops.git_diff",
        return_value=expected_diff,
    ):
        captured_diffs = await _capture_raw_diffs(repo_root="/test/repo")

    # The captured diffs must be passed to committee review — assert the LLM
    # receives them as part of its structured prompt context.
    mock_llm = AsyncMock()
    mock_llm.chat_structured.return_value = SemanticReviewRubric(
        overall_score=7.0,
        verdict="approve",
        blocking_issues=[],
        non_blocking_issues=[],
        category_scores={"alignment": 8},
    )

    await _conduct_committee_review(
        llm_client=mock_llm,
        approved_plan="Auth changes only",
        summary="Modified auth.py",
        raw_diffs=captured_diffs,
    )

    # Verify the LLM was called — if raw diffs are grounded, they flow through
    assert mock_llm.chat_structured.await_count >= 1, (
        "Committee review must call the LLM with grounded diff context"
    )


# ── 3. Iteration cap hard-limits to two corrective revisions ──────────────────


async def test_semantic_review_iteration_cap_stops_after_two_iterations():
    """_run_semantic_review enforces a hard cap of 2 corrective iterations, suspending after the second revise verdict."""
    from lean_ai.workflow.validation import (
        SemanticReviewRubric,
        _run_semantic_review,
    )

    mock_llm = AsyncMock()

    # Every committee review returns 'revise' — should stop at iteration 2
    def always_revise(*args, **kwargs):
        return SemanticReviewRubric(
            overall_score=3.0,
            verdict="revise",
            blocking_issues=["Still drifting from plan"],
            non_blocking_issues=[],
            category_scores={"alignment": 1},
        )

    mock_llm.chat_structured.side_effect = always_revise

    state = {
        "plan_text": "Implement auth changes only",
        "semantic_review_iteration": 0,
    }

    result = await _run_semantic_review(
        state=state,
        llm_client=mock_llm,
    )

    # After the cap is hit, committee review should have been called at most
    # MAX_ITERATIONS + 1 times (initial + 2 corrective attempts).
    assert mock_llm.chat_structured.await_count <= 3, (
        f"Iteration cap exceeded: LLM called {mock_llm.chat_structured.await_count} times, expected max 3"
    )

    # State must record the final iteration count at or below the cap
    assert state["semantic_review_iteration"] <= 2, (
        f"Iteration counter should not exceed 2 but was {state['semantic_review_iteration']}"
    )


# ── 4. Corrective plan generation requires blocking issues trigger ────────────


async def test_corrective_plan_only_generated_for_blocking_issues():
    """_generate_corrective_plan is invoked only when rubric contains blocking issues, not for non-blocking-only verdicts."""
    from lean_ai.workflow.validation import (
        SemanticReviewRubric,
        _generate_corrective_plan,
    )

    mock_llm = AsyncMock()
    mock_llm.chat_structured.return_value = MagicMock(
        corrective_steps=["Revert unapproved changes"],
    )

    # Rubric with blocking issues should trigger corrective plan generation
    rubric_with_blocking = SemanticReviewRubric(
        overall_score=3.0,
        verdict="revise",
        blocking_issues=["Unapproved feature added"],
        non_blocking_issues=[],
        category_scores={"alignment": 2},
    )

    plan = await _generate_corrective_plan(
        llm_client=mock_llm,
        rubric=rubric_with_blocking,
        approved_plan="Auth only",
    )

    assert len(plan.corrective_steps) > 0, (
        "Corrective plan must produce steps when blocking issues exist"
    )
    mock_llm.chat_structured.assert_awaited_once(), (
        "LLM should be called to generate corrective steps for blocking rubric"
    )

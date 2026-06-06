"""Unit tests for semantic review gate logic.

Covers:
  1. SemanticReviewRubric schema validation (field types and constraints)
  2. Drift detection triggering revise verdict via committee review
  3. Non-blocking approval path when no blocking issues exist
  4. Raw diff capture utility returning unified patch text
  5. Corrective plan generation from rubric feedback

TDD mode — tests define the public contract before implementation exists.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── 1. SemanticReviewRubric schema validation ────────────────────────────────


async def test_rubric_schema_validates_fields_and_rejects_missing():
    """SemanticReviewRubric requires overall_score, verdict, blocking_issues, non_blocking_issues, category_scores and rejects missing fields."""
    from lean_ai.workflow.validation import SemanticReviewRubric
    from pydantic import ValidationError

    # Valid construction with all required fields
    rubric = SemanticReviewRubric(
        overall_score=7.5,
        verdict="approve",
        blocking_issues=[],
        non_blocking_issues=["minor style note"],
        category_scores={"alignment": 8, "scope_adherence": 9},
    )
    assert rubric.overall_score == 7.5
    assert rubric.verdict == "approve"
    assert len(rubric.blocking_issues) == 0
    assert len(rubric.non_blocking_issues) == 1
    assert "alignment" in rubric.category_scores

    # Missing fields raises ValidationError
    with pytest.raises(ValidationError):
        SemanticReviewRubric()  # type: ignore[call-arg]


# ── 2. Drift detection triggers revise verdict ───────────────────────────────


async def test_committee_review_returns_revise_on_drift():
    """_conduct_committee_review returns 'revise' verdict when diffs deviate from approved plan."""
    from lean_ai.workflow.validation import _conduct_committee_review

    mock_llm = AsyncMock()
    # Simulate LLM returning a rubric with drift detected (blocking issues)
    from lean_ai.workflow.validation import SemanticReviewRubric

    mock_llm.chat_structured.return_value = SemanticReviewRubric(
        overall_score=3.0,
        verdict="revise",
        blocking_issues=["Added unapproved feature X not in plan"],
        non_blocking_issues=[],
        category_scores={"alignment": 2, "scope_adherence": 1},
    )

    approved_plan = "Implement auth module changes only"
    summary = "Modified auth.py and added new payments.py file"
    raw_diffs = "--- a/payments.py\n+++ b/payments.py\n+new payment code"

    result = await _conduct_committee_review(
        llm_client=mock_llm,
        approved_plan=approved_plan,
        summary=summary,
        raw_diffs=raw_diffs,
    )

    assert result.verdict == "revise", (
        f"Expected 'revise' verdict on drift but got '{result.verdict}'"
    )
    assert len(result.blocking_issues) > 0, "Drift should produce blocking issues"


# ── 3. Non-blocking approval path ────────────────────────────────────────────


async def test_committee_review_approves_with_non_blocking_issues():
    """_conduct_committee_review returns 'approve' verdict when only non-blocking issues exist."""
    from lean_ai.workflow.validation import _conduct_committee_review, SemanticReviewRubric

    mock_llm = AsyncMock()
    mock_llm.chat_structured.return_value = SemanticReviewRubric(
        overall_score=8.0,
        verdict="approve",
        blocking_issues=[],
        non_blocking_issues=["Consider renaming variable x to user_name"],
        category_scores={"alignment": 9, "scope_adherence": 8},
    )

    approved_plan = "Refactor auth module"
    summary = "Renamed functions in auth.py per plan"
    raw_diffs = "--- a/auth.py\n+++ b/auth.py\n-renamed function"

    result = await _conduct_committee_review(
        llm_client=mock_llm,
        approved_plan=approved_plan,
        summary=summary,
        raw_diffs=raw_diffs,
    )

    assert result.verdict == "approve", (
        f"Expected 'approve' verdict with only non-blocking issues but got '{result.verdict}'"
    )
    assert len(result.blocking_issues) == 0, "Non-blocking path must have no blocking issues"


# ── 4. Raw diff capture utility ──────────────────────────────────────────────


async def test_capture_raw_diffs_returns_unified_patch():
    """_capture_raw_diffs returns unified git patch text for staged changes."""
    from lean_ai.workflow.validation import _capture_raw_diffs

    expected_diff = (
        "--- a/example.py\n"
        "+++ b/example.py\n"
        "@@ -1,3 +1,4 @@\n"
        "+new_line()\n"
    )

    with patch(
        "lean_ai.tools.git_ops.git_diff",
        return_value=expected_diff,
    ):
        diffs = await _capture_raw_diffs(repo_root="/fake/repo")

    assert "--- a/example.py" in diffs, (
        f"Diffs should contain unified patch markers but got: {diffs[:80]}"
    )


# ── 5. Corrective plan generation logic ──────────────────────────────────────


async def test_generate_corrective_plan_produces_fix_instructions():
    """_generate_corrective_plan returns actionable fix steps based on rubric blocking issues."""
    from lean_ai.workflow.validation import (
        SemanticReviewRubric,
        _generate_corrective_plan,
    )

    mock_llm = AsyncMock()
    mock_llm.chat_structured.return_value = MagicMock(
        corrective_steps=[
            "Remove unapproved payments.py",
            "Revert changes outside auth scope",
        ],
    )

    rubric = SemanticReviewRubric(
        overall_score=3.0,
        verdict="revise",
        blocking_issues=["Added unapproved feature X"],
        non_blocking_issues=[],
        category_scores={"alignment": 2},
    )
    approved_plan = "Auth module changes only"

    plan = await _generate_corrective_plan(
        llm_client=mock_llm,
        rubric=rubric,
        approved_plan=approved_plan,
    )

    assert len(plan.corrective_steps) > 0, (
        "Corrective plan should contain fix steps but got none"
    )
    mock_llm.chat_structured.assert_awaited_once()

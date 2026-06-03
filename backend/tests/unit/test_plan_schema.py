"""Tests for PlanStep validation."""

import logging

import pytest
from pydantic import ValidationError

from lean_ai.llm.plan_schema import (
    DEFAULT_ALLOWED_READ_ONLY_STEP_TOOLS,
    AssumptionStatus,
    ExecutionPlan,
    FileObservation,
    FileSummary,
    MissingItem,
    PlanStep,
    ScopeAssumption,
    ScopeDocument,
    TestingInventory as InventoryModel,
    VerifiedReference,
    plan_to_markdown,
)


def _step(**overrides) -> PlanStep:
    defaults = {
        "step_number": 1,
        "tool": "edit_file",
        "file_path": "src/main.py",
        "instruction": "Update the main function",
    }
    defaults.update(overrides)
    return PlanStep(**defaults)


class TestInstructionValidation:
    def test_empty_instruction_rejected(self):
        with pytest.raises(ValidationError, match="instruction must not be empty"):
            _step(instruction="")

    def test_whitespace_only_rejected(self):
        with pytest.raises(ValidationError, match="instruction must not be empty"):
            _step(instruction="   ")

    def test_valid_instruction_accepted(self):
        step = _step(instruction="Add error handling")
        assert step.instruction == "Add error handling"


class TestFilePathWarning:
    def test_edit_file_without_path_warns(self, caplog):
        with caplog.at_level(logging.WARNING):
            _step(tool="edit_file", file_path="")
        assert "should have a file_path" in caplog.text

    def test_create_file_without_path_warns(self, caplog):
        with caplog.at_level(logging.WARNING):
            _step(tool="create_file", file_path="")
        assert "should have a file_path" in caplog.text

    def test_run_command_without_path_no_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            _step(tool="run_command", file_path="")
        assert "should have a file_path" not in caplog.text

    def test_edit_file_with_path_no_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            _step(tool="edit_file", file_path="src/main.py")
        assert "should have a file_path" not in caplog.text


def test_plan_step_adds_default_read_only_helpers_to_explicit_allowed_tools():
    step = _step(allowed_tools=["edit_file", "run_tests"])

    assert step.allowed_tools[:2] == ["edit_file", "run_tests"]
    assert step.allowed_tools[-1] == "task_complete"
    for tool_name in DEFAULT_ALLOWED_READ_ONLY_STEP_TOOLS:
        assert tool_name in step.allowed_tools


def test_plan_to_markdown_renders_separate_tdd_and_implementation_sections():
    plan = ExecutionPlan(
        scope="Implement auth callback.",
        tdd_mode=True,
        steps=[_step(step_number=2, file_path="src/auth.py", instruction="Implement callback")],
        tdd_test_steps=[
            _step(
                step_number=1,
                tool="create_file",
                file_path="tests/test_auth.py",
                instruction="Write auth callback tests",
            )
        ],
        affected_files=["src/auth.py", "tests/test_auth.py"],
        test_strategy="Run pytest.",
    )

    rendered = plan_to_markdown(plan)

    assert "## TEST PHASE (Expert Model)" in rendered
    assert "## IMPLEMENTATION PHASE (Primary Model)" in rendered
    assert "tests/test_auth.py" in rendered
    assert "src/auth.py" in rendered


def test_scope_document_to_markdown():
    """Verify ScopeDocument.to_markdown() produces the 8-section markdown shape
    with correct headers, bulleted lists, assumption formatting, and
    '(none identified)' placeholders for empty sections."""

    scope = ScopeDocument(
        problem="Add authentication to the API so users can log in securely.",
        deliverables=["Users can log in via POST /auth/login", "JWT token returned on success"],
        in_scope=["src/auth/login.py", "src/models/user.py"],
        out_of_scope=["OAuth2 social login", "Two-factor authentication"],
        downstream_consumers=["src/middleware/auth.py", "tests/test_auth.py"],
        assumptions=[
            ScopeAssumption(
                assumption="PyJWT is already installed",
                verify_hint="grep PyJWT in pyproject.toml",
            ),
            ScopeAssumption(
                assumption="User model has a hashed_password field",
                verify_hint="read src/models/user.py",
            ),
        ],
        success_criteria=["POST /auth/login returns 200 with valid credentials", "Invalid credentials return 401"],
        risks=["Existing sessions may be invalidated"],
    )

    rendered = scope.to_markdown()

    # Verify all 8 section headers are present
    assert "PROBLEM / PURPOSE:" in rendered
    assert "DELIVERABLES:" in rendered
    assert "IN SCOPE:" in rendered
    assert "OUT OF SCOPE:" in rendered
    assert "DOWNSTREAM CONSUMERS:" in rendered
    assert "ASSUMPTIONS (with verification hints):" in rendered
    assert "SUCCESS CRITERIA:" in rendered
    assert "RISKS:" in rendered

    # Verify problem text is rendered
    assert "Add authentication to the API" in rendered

    # Verify bulleted list items
    assert "- Users can log in via POST /auth/login" in rendered
    assert "- JWT token returned on success" in rendered
    assert "- src/auth/login.py" in rendered
    assert "- src/models/user.py" in rendered
    assert "- OAuth2 social login" in rendered
    assert "- Two-factor authentication" in rendered
    assert "- src/middleware/auth.py" in rendered
    assert "- tests/test_auth.py" in rendered

    # Verify assumption special format
    assert "- Assumption: PyJWT is already installed — verify: grep PyJWT in pyproject.toml" in rendered
    assert "- Assumption: User model has a hashed_password field — verify: read src/models/user.py" in rendered

    # Verify success criteria
    assert "- POST /auth/login returns 200 with valid credentials" in rendered
    assert "- Invalid credentials return 401" in rendered

    # Verify risks
    assert "- Existing sessions may be invalidated" in rendered

    # Verify trailing newline
    assert rendered.endswith("\n")

    # Verify empty sections produce '(none identified)' placeholder
    empty_scope = ScopeDocument(problem="A minimal scope.")
    empty_rendered = empty_scope.to_markdown()
    # Count placeholder occurrences — all 7 list sections should have it
    assert empty_rendered.count("- (none identified)") == 7


def test_file_summary_to_markdown():
    """Verify FileSummary.to_markdown() produces the 8-section markdown shape
    with nested object rendering, correct field values, and
    '(none identified)' placeholders for empty sections."""

    summary = FileSummary(
        files_to_modify=[
            FileObservation(
                file_path="src/auth/login.py",
                role="modify",
                reason="Add login endpoint logic",
                relevant_sections="Lines 10-45: route handler",
            ),
        ],
        files_to_create=[
            FileObservation(
                file_path="src/auth/token.py",
                role="create",
                reason="JWT token generation utilities",
            ),
        ],
        files_read_for_context=[
            FileObservation(
                file_path="src/models/user.py",
                role="reference",
                reason="Check user model fields",
            ),
        ],
        missing_infrastructure=[
            MissingItem(name="Password hashing utility", reason="No bcrypt wrapper found", blocking=True),
            MissingItem(name="Token blacklist", reason="Session revocation not implemented", blocking=False),
        ],
        verified_references=[
            VerifiedReference(
                dependency="PyJWT",
                docs_url="https://pyjwt.readthedocs.io",
                version="2.8.0",
            ),
        ],
        assumptions_resolved=[
            AssumptionStatus(
                assumption="PyJWT is installed",
                status="confirmed",
                evidence="Found pyjwt==2.8.0 in pyproject.toml",
            ),
            AssumptionStatus(
                assumption="User model has hashed_password",
                status="falsified",
                evidence="User model only has password field, not hashed",
            ),
        ],
        testing_inventory=InventoryModel(
            test_framework="pytest",
            test_directory="tests/",
            test_file_pattern="test_*.py",
            strategy_summary="Use pytest with fixtures for DB setup.",
        ),
        notes="Remember to update the API docs.",
    )

    rendered = summary.to_markdown()

    # Verify all 8 section headers are present
    assert "FILES TO MODIFY:" in rendered
    assert "FILES TO CREATE:" in rendered
    assert "FILES READ FOR CONTEXT:" in rendered
    assert "MISSING INFRASTRUCTURE:" in rendered
    assert "VERIFIED REFERENCES:" in rendered
    assert "ASSUMPTIONS RESOLVED:" in rendered
    assert "TESTING INVENTORY:" in rendered
    assert "NOTES:" in rendered

    # Verify nested FileObservation rendering (files_to_modify)
    assert "- **src/auth/login.py** (modify)" in rendered
    assert "  - Reason: Add login endpoint logic" in rendered
    assert "  - Relevant sections: Lines 10-45: route handler" in rendered

    # Verify nested FileObservation rendering (files_to_create) — no relevant_sections
    assert "- **src/auth/token.py** (create)" in rendered
    assert "  - Reason: JWT token generation utilities" in rendered

    # Verify nested FileObservation rendering (files_read_for_context)
    assert "- **src/models/user.py** (reference)" in rendered
    assert "  - Reason: Check user model fields" in rendered

    # Verify nested MissingItem rendering with blocking flag
    assert "- **Password hashing utility** (BLOCKING)" in rendered
    assert "  - Reason: No bcrypt wrapper found" in rendered
    assert "- **Token blacklist** (non-blocking)" in rendered
    assert "  - Reason: Session revocation not implemented" in rendered

    # Verify nested VerifiedReference rendering
    assert "- **PyJWT**" in rendered
    assert "  - Docs: https://pyjwt.readthedocs.io" in rendered
    assert "  - Version: 2.8.0" in rendered

    # Verify nested AssumptionStatus rendering
    assert "- **PyJWT is installed** — status: confirmed" in rendered
    assert "  - Evidence: Found pyjwt==2.8.0 in pyproject.toml" in rendered
    assert "- **User model has hashed_password** — status: falsified" in rendered
    assert "  - Evidence: User model only has password field, not hashed" in rendered

    # Verify TestingInventory rendering
    assert "- Framework: pytest" in rendered
    assert "- Test directory: tests/" in rendered
    assert "- File pattern: test_*.py" in rendered
    assert "- Strategy: Use pytest with fixtures for DB setup." in rendered

    # Verify notes section
    assert "Remember to update the API docs." in rendered

    # Verify trailing newline
    assert rendered.endswith("\n")

    # Verify empty sections produce '(none identified)' placeholder
    empty_summary = FileSummary()
    empty_rendered = empty_summary.to_markdown()
    # All 8 sections should have the placeholder when empty
    assert empty_rendered.count("- (none identified)") == 8

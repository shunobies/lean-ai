"""Tests for PlanStep validation."""

import logging

import pytest
from pydantic import ValidationError

from lean_ai.llm.plan_schema import (
    DEFAULT_ALLOWED_READ_ONLY_STEP_TOOLS,
    PlanStep,
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

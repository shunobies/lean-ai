"""Tests for human-readable tool descriptions."""

from lean_ai.tools.descriptions import humanize_tool_call


def test_humanize_run_command_handles_none_command():
    assert humanize_tool_call("run_command", {"command": None}) == "Running: ..."


def test_humanize_fallback_handles_missing_values():
    assert humanize_tool_call("unknown_tool", {"path": None}) == "unknown_tool"

"""Tests for request-mode specific tool exposure."""

from lean_ai.llm.tool_definitions import build_implementation_tools, build_request_tools


def test_request_tools_include_request_clarification() -> None:
    """Request mode must expose the pause-capable clarification tool."""
    plain_names = {tool["function"]["name"] for tool in build_implementation_tools()}
    request_names = {tool["function"]["name"] for tool in build_request_tools()}

    assert "request_clarification" not in plain_names
    assert "request_clarification" in request_names
    assert plain_names <= request_names

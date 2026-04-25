"""Tests for TDD dispute wiring in Phase C (implementation).

Prior to this change `request_test_change` was only routed in Phase B
(review). During Phase C the primary had no escape hatch for a
genuinely flawed test — `request_test_change` returned an error and the
only recourse was cancelling the session. These tests lock in the fixed
behavior: Phase C's executor now wires the same expert-evaluated
dispute mechanism Phase B uses, and the TDD implementation tool set
includes `request_test_change`.
"""

from unittest.mock import AsyncMock

import pytest

from lean_ai.llm.tool_definitions import (
    build_implementation_tools,
    build_tdd_implementation_tools,
)
from lean_ai.workflow.tool_executor import make_tool_executor


class _FakeWS:
    """Minimal WebSocket stub — ws_send is never called on this path."""

    async def send_json(self, *a, **kw):
        return None


@pytest.mark.asyncio
async def test_tdd_impl_tools_include_request_test_change():
    """TDD mode must expose request_test_change in every phase that
    write-protects tests; otherwise the guard has no escape hatch."""
    tdd_tools = build_tdd_implementation_tools()
    plain_tools = build_implementation_tools()

    plain_names = {t["function"]["name"] for t in plain_tools}
    tdd_names = {t["function"]["name"] for t in tdd_tools}

    assert "request_test_change" not in plain_names
    assert "request_test_change" in tdd_names
    # Every plain tool must still be present — TDD is an addition, not a swap.
    assert plain_names <= tdd_names


@pytest.mark.asyncio
async def test_phase_c_dispute_routes_to_callback(tmp_path):
    """With tdd_protect_tests=True AND on_test_dispute set (Phase C
    wiring), request_test_change calls MUST invoke the callback."""
    dispute_callback = AsyncMock(
        return_value="ACCEPTED: test edited to match new contract",
    )
    executor = make_tool_executor(
        str(tmp_path),
        _FakeWS(),
        session_id="s1",
        tdd_protect_tests=True,
        on_test_dispute=dispute_callback,
    )

    result = await executor(
        "request_test_change",
        {
            "test_file": "tests/test_auth.py",
            "test_function": "test_login_rejects_empty_password",
            "reason": (
                "test asserts password.strip() != '' but the contract says "
                "empty passwords raise ValueError rather than returning False"
            ),
        },
    )

    assert dispute_callback.call_count == 1
    assert "ACCEPTED" in result


@pytest.mark.asyncio
async def test_request_test_change_without_callback_returns_error(tmp_path):
    """Fallback behavior: if no dispute callback is wired (e.g. during a
    non-TDD validation fix), the tool must return an explanatory error
    rather than crashing."""
    executor = make_tool_executor(
        str(tmp_path),
        _FakeWS(),
        session_id="s1",
        tdd_protect_tests=True,
        on_test_dispute=None,
    )

    result = await executor(
        "request_test_change",
        {
            "test_file": "tests/test_auth.py",
            "test_function": "test_x",
            "reason": "something",
        },
    )
    assert "ERROR" in result
    assert "not available" in result


@pytest.mark.asyncio
async def test_phase_c_still_blocks_direct_test_edits(tmp_path):
    """Dispute wiring must NOT weaken the write-protection guard. Even
    with the dispute callback present, direct edit_file on a test path
    must still be rejected — disputes are the ONLY permitted path."""
    (tmp_path / "tests").mkdir()
    test_file = tmp_path / "tests" / "test_auth.py"
    test_file.write_text("def test_x():\n    assert True\n")

    executor = make_tool_executor(
        str(tmp_path),
        _FakeWS(),
        session_id="s1",
        tdd_protect_tests=True,
        on_test_dispute=AsyncMock(return_value="unused"),
    )

    result = await executor(
        "edit_file",
        {
            "path": "tests/test_auth.py",
            "search": "assert True",
            "replace": "assert False",
        },
    )
    assert "ERROR" in result
    assert "TDD" in result or "test files" in result.lower()
    # File on disk must be untouched.
    assert test_file.read_text() == "def test_x():\n    assert True\n"

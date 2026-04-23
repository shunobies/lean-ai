"""Tests for typed WebSocket message contracts."""

import asyncio

import pytest

from lean_ai.workflow.ws_messages import (
    BranchCreatedMessage,
    ClientMessageType,
    DiffMessage,
    ErrorMessage,
    ServerMessageType,
    StageChangeMessage,
    ToolApprovalRequiredMessage,
    fire_assistant_content,
    fire_tool_progress,
    send_diff,
    send_error,
    send_stage_change,
    send_stage_status,
    send_test_result,
    send_tool_approval_required,
)


class FakeWebSocket:
    """Minimal stub that records sent messages."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


# ── TypedDict shape tests ───────────────────────────────────────


class TestMessageShapes:
    def test_stage_change_shape(self):
        msg: StageChangeMessage = {"type": "stage_change", "stage": "planning"}
        assert msg["type"] == "stage_change"
        assert msg["stage"] == "planning"

    def test_error_message_shape(self):
        msg: ErrorMessage = {"type": "error", "message": "boom"}
        assert msg["type"] == "error"

    def test_diff_message_shape(self):
        msg: DiffMessage = {"type": "diff", "file": "a.py", "diff": "+line"}
        assert msg["file"] == "a.py"

    def test_branch_created_shape(self):
        msg: BranchCreatedMessage = {
            "type": "branch_created",
            "branch_name": "lean-ai/abc",
            "base_branch": "main",
        }
        assert msg["branch_name"] == "lean-ai/abc"

    def test_tool_approval_required_shape(self):
        msg: ToolApprovalRequiredMessage = {
            "type": "tool_approval_required",
            "tool": "run_command",
            "command": "rm foo",
            "reason": "destructive",
        }
        assert msg["tool"] == "run_command"


# ── Typed send helper tests ─────────────────────────────────────


class TestTypedSendHelpers:
    @pytest.mark.asyncio
    async def test_send_stage_change(self):
        ws = FakeWebSocket()
        await send_stage_change(ws, stage="planning")
        assert ws.sent[-1] == {"type": "stage_change", "stage": "planning"}

    @pytest.mark.asyncio
    async def test_send_stage_status(self):
        ws = FakeWebSocket()
        await send_stage_status(
            ws, stage="PLANNING", status="running",
            summary="Phase 1", model="qwen3", phase=1,
        )
        msg = ws.sent[-1]
        assert msg["type"] == "stage_status"
        assert msg["stage"] == "PLANNING"
        assert msg["model"] == "qwen3"
        assert msg["phase"] == 1

    @pytest.mark.asyncio
    async def test_send_stage_status_minimal(self):
        ws = FakeWebSocket()
        await send_stage_status(
            ws, stage="EXEC", status="done", summary="Done",
        )
        msg = ws.sent[-1]
        assert "model" not in msg
        assert "phase" not in msg

    @pytest.mark.asyncio
    async def test_send_error(self):
        ws = FakeWebSocket()
        await send_error(ws, message="something broke")
        assert ws.sent[-1] == {"type": "error", "message": "something broke"}

    @pytest.mark.asyncio
    async def test_send_diff(self):
        ws = FakeWebSocket()
        await send_diff(ws, file="a.py", diff="+new line")
        # Shape now includes diff_hash — the extension echoes it back on
        # accept/reject so training captures pair decisions to diffs.
        msg = ws.sent[-1]
        assert msg["type"] == "diff"
        assert msg["file"] == "a.py"
        assert msg["diff"] == "+new line"
        assert isinstance(msg["diff_hash"], str) and len(msg["diff_hash"]) == 16

    @pytest.mark.asyncio
    async def test_send_test_result(self):
        ws = FakeWebSocket()
        await send_test_result(ws, passed=True, output="OK", command="pytest")
        msg = ws.sent[-1]
        assert msg["type"] == "test_result"
        assert msg["passed"] is True
        assert msg["command"] == "pytest"

    @pytest.mark.asyncio
    async def test_send_tool_approval_required(self):
        ws = FakeWebSocket()
        await send_tool_approval_required(
            ws, tool="run_command", command="rm foo", reason="destructive",
        )
        msg = ws.sent[-1]
        assert msg["type"] == "tool_approval_required"
        assert msg["tool"] == "run_command"


class TestFireAndForgetHelpers:
    @pytest.mark.asyncio
    async def test_fire_tool_progress(self):
        ws = FakeWebSocket()
        fire_tool_progress(
            ws, tool="read_file", status="running",
            description="Reading file.py",
        )
        # fire-and-forget uses create_task — give the event loop a tick
        await asyncio.sleep(0.01)
        assert len(ws.sent) == 1
        assert ws.sent[0]["type"] == "tool_progress"
        assert ws.sent[0]["tool"] == "read_file"

    @pytest.mark.asyncio
    async def test_fire_assistant_content(self):
        ws = FakeWebSocket()
        fire_assistant_content(ws, content="hello", streaming=True)
        await asyncio.sleep(0.01)
        assert ws.sent[0]["type"] == "assistant_content"
        assert ws.sent[0]["streaming"] is True

    @pytest.mark.asyncio
    async def test_fire_assistant_content_no_optional_flags(self):
        ws = FakeWebSocket()
        fire_assistant_content(ws, content="hello")
        await asyncio.sleep(0.01)
        assert "streaming" not in ws.sent[0]
        assert "done" not in ws.sent[0]


# ── Type literal coverage ───────────────────────────────────────


class TestTypeLiteralCoverage:
    def test_server_message_type_is_literal(self):
        """Sanity check that the type alias resolves."""
        # ServerMessageType is a Literal union — we can check its args
        from typing import get_args
        args = get_args(ServerMessageType)
        assert "stage_change" in args
        assert "complete" in args
        assert "error" in args
        assert len(args) >= 15  # we have 22+ types

    def test_client_message_type_is_literal(self):
        from typing import get_args
        args = get_args(ClientMessageType)
        assert "user_message" in args
        assert "cancel" in args
        assert "approve_tool" in args
        assert len(args) >= 6

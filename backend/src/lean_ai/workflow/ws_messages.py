"""Typed WebSocket message contracts.

Defines ``TypedDict`` shapes for every server→client and client→server
message.  Use the ``Literal`` type unions as the single source of truth
for the protocol.

Usage — typed send helpers::

    from lean_ai.workflow.ws_messages import send_stage_change, send_error

    await send_stage_change(ws, stage="planning")
    await send_error(ws, message="something broke")

Usage — building a payload manually::

    msg: StageStatusMessage = {
        "type": "stage_status",
        "stage": "PLANNING",
        "status": "running",
        "summary": "Phase 1 — scope analysis",
    }
    await ws_send(ws, msg["type"], {k: v for k, v in msg.items() if k != "type"})
"""

from __future__ import annotations

from typing import Literal, TypedDict

from fastapi import WebSocket

from lean_ai.workflow.ws_handler import ws_send, ws_send_nowait

# ── Server → Client messages ────────────────────────────────────


class StageChangeMessage(TypedDict):
    type: Literal["stage_change"]
    stage: str


class StageStatusMessage(TypedDict, total=False):
    type: Literal["stage_status"]  # pyright: ignore[reportGeneralTypeIssues]
    stage: str
    status: str
    summary: str
    model: str
    phase: int


# Make required keys explicit
class _StageStatusRequired(TypedDict):
    type: Literal["stage_status"]
    stage: str
    status: str
    summary: str


class ApprovalRequiredMessage(TypedDict, total=False):
    type: Literal["approval_required"]  # pyright: ignore[reportGeneralTypeIssues]
    plan: str
    plan_object: dict


class _ApprovalRequiredBase(TypedDict):
    type: Literal["approval_required"]
    plan: str


class ToolApprovalRequiredMessage(TypedDict):
    type: Literal["tool_approval_required"]
    tool: str
    command: str
    reason: str


class ClarificationNeededMessage(TypedDict):
    type: Literal["clarification_needed"]
    questions: str


class PlanRejectedMessage(TypedDict):
    type: Literal["plan_rejected"]
    message: str


class PlanRevisionMessage(TypedDict, total=False):
    type: Literal["plan_revision"]  # pyright: ignore[reportGeneralTypeIssues]
    plan: str
    plan_object: dict


class ToolProgressMessage(TypedDict, total=False):
    type: Literal["tool_progress"]  # pyright: ignore[reportGeneralTypeIssues]
    tool: str
    status: str
    description: str
    output: str


class _ToolProgressRequired(TypedDict):
    type: Literal["tool_progress"]
    tool: str
    status: str
    description: str


class DiffMessage(TypedDict):
    type: Literal["diff"]
    file: str
    diff: str


class TestResultMessage(TypedDict, total=False):
    type: Literal["test_result"]  # pyright: ignore[reportGeneralTypeIssues]
    passed: bool
    output: str
    command: str


class _TestResultRequired(TypedDict):
    type: Literal["test_result"]
    passed: bool
    output: str


class ErrorMessage(TypedDict):
    type: Literal["error"]
    message: str


class CompleteMessage(TypedDict, total=False):
    type: Literal["complete"]  # pyright: ignore[reportGeneralTypeIssues]
    commit_message: str
    files_changed: list[str]
    affected_files: list[str]


class CancelledMessage(TypedDict):
    type: Literal["cancelled"]


class CheckpointMessage(TypedDict, total=False):
    type: Literal["checkpoint"]  # pyright: ignore[reportGeneralTypeIssues]
    step: int
    total: int
    tool: str
    file_path: str
    description: str


class ExecutionChecklistMessage(TypedDict, total=False):
    type: Literal["execution_checklist"]  # pyright: ignore[reportGeneralTypeIssues]
    steps: list[dict]


class ContextRefreshedMessage(TypedDict, total=False):
    type: Literal["context_refreshed"]  # pyright: ignore[reportGeneralTypeIssues]
    dropped_messages: int
    reason: str


class AssistantContentMessage(TypedDict, total=False):
    type: Literal["assistant_content"]  # pyright: ignore[reportGeneralTypeIssues]
    content: str
    done: bool
    streaming: bool


class ThinkingContentMessage(TypedDict, total=False):
    type: Literal["thinking_content"]  # pyright: ignore[reportGeneralTypeIssues]
    content: str
    streaming: bool


class MetricsUpdateMessage(TypedDict, total=False):
    type: Literal["metrics_update"]  # pyright: ignore[reportGeneralTypeIssues]
    prompt_tokens: int
    context_window: int
    context_pct: int


class BranchCreatedMessage(TypedDict):
    type: Literal["branch_created"]
    branch_name: str
    base_branch: str


class PongMessage(TypedDict):
    type: Literal["pong"]


class VisionDescriptionMessage(TypedDict):
    type: Literal["vision_description"]
    descriptions: str


class RefinerStatusMessage(TypedDict, total=False):
    type: Literal["refiner_status"]  # pyright: ignore[reportGeneralTypeIssues]
    status: str
    original: str
    refined: str
    changes: str


# ── Server message type union ───────────────────────────────────

ServerMessageType = Literal[
    "stage_change",
    "stage_status",
    "approval_required",
    "tool_approval_required",
    "clarification_needed",
    "plan_rejected",
    "plan_revision",
    "tool_progress",
    "diff",
    "test_result",
    "error",
    "complete",
    "cancelled",
    "checkpoint",
    "execution_checklist",
    "context_refreshed",
    "assistant_content",
    "thinking_content",
    "metrics_update",
    "branch_created",
    "pong",
    "vision_description",
    "refiner_status",
]

# ── Client → Server messages ────────────────────────────────────


class UserMessage(TypedDict, total=False):
    type: Literal["user_message"]  # pyright: ignore[reportGeneralTypeIssues]
    content: str


class CancelMessage(TypedDict):
    type: Literal["cancel"]


class ApproveMessage(TypedDict):
    type: Literal["approve"]


class ApproveToolMessage(TypedDict):
    type: Literal["approve_tool"]


class DenyToolMessage(TypedDict):
    type: Literal["deny_tool"]


class PingMessage(TypedDict):
    type: Literal["ping"]


class ResumeMessage(TypedDict, total=False):
    type: Literal["resume"]  # pyright: ignore[reportGeneralTypeIssues]
    session_id: str
    repo_root: str


ClientMessageType = Literal[
    "user_message",
    "cancel",
    "approve",
    "approve_tool",
    "deny_tool",
    "ping",
    "resume",
]

# ── Typed send helpers ──────────────────────────────────────────
#
# Thin wrappers that construct the correct payload and delegate to
# the existing ws_send / ws_send_nowait.  Use these at call sites
# for compile-time type safety without changing the wire format.


async def send_stage_change(ws: WebSocket, *, stage: str) -> None:
    await ws_send(ws, "stage_change", {"stage": stage})


async def send_stage_status(
    ws: WebSocket,
    *,
    stage: str,
    status: str,
    summary: str,
    model: str | None = None,
    phase: int | None = None,
) -> None:
    data: dict = {"stage": stage, "status": status, "summary": summary}
    if model is not None:
        data["model"] = model
    if phase is not None:
        data["phase"] = phase
    await ws_send(ws, "stage_status", data)


async def send_error(ws: WebSocket, *, message: str) -> None:
    await ws_send(ws, "error", {"message": message})


async def send_diff(ws: WebSocket, *, file: str, diff: str) -> None:
    await ws_send(ws, "diff", {"file": file, "diff": diff})


async def send_test_result(
    ws: WebSocket, *, passed: bool, output: str, command: str | None = None,
) -> None:
    data: dict = {"passed": passed, "output": output}
    if command is not None:
        data["command"] = command
    await ws_send(ws, "test_result", data)


async def send_tool_approval_required(
    ws: WebSocket, *, tool: str, command: str, reason: str,
) -> None:
    await ws_send(ws, "tool_approval_required", {
        "tool": tool, "command": command, "reason": reason,
    })


async def send_checkpoint(
    ws: WebSocket, *, step: int, total: int, tool: str,
    file_path: str = "", description: str = "",
) -> None:
    data: dict = {
        "step": step, "total": total, "tool": tool,
    }
    if file_path:
        data["file_path"] = file_path
    if description:
        data["description"] = description
    await ws_send(ws, "checkpoint", data)


async def send_complete(ws: WebSocket, **data: object) -> None:
    await ws_send(ws, "complete", dict(data))


def fire_tool_progress(
    ws: WebSocket, *, tool: str, status: str, description: str,
    output: str | None = None,
) -> None:
    """Fire-and-forget tool progress (non-blocking)."""
    data: dict = {"tool": tool, "status": status, "description": description}
    if output is not None:
        data["output"] = output
    ws_send_nowait(ws, "tool_progress", data)


def fire_assistant_content(
    ws: WebSocket, *, content: str, done: bool = False,
    streaming: bool = False,
) -> None:
    """Fire-and-forget assistant content (non-blocking)."""
    data: dict = {"content": content}
    if done:
        data["done"] = True
    if streaming:
        data["streaming"] = True
    ws_send_nowait(ws, "assistant_content", data)


def fire_thinking_content(
    ws: WebSocket, *, content: str, streaming: bool = False,
) -> None:
    """Fire-and-forget thinking content (non-blocking)."""
    data: dict = {"content": content}
    if streaming:
        data["streaming"] = True
    ws_send_nowait(ws, "thinking_content", data)


def fire_context_refreshed(
    ws: WebSocket, *, dropped_messages: int, reason: str = "",
) -> None:
    """Fire-and-forget context refresh notification (non-blocking)."""
    data: dict = {"dropped_messages": dropped_messages}
    if reason:
        data["reason"] = reason
    ws_send_nowait(ws, "context_refreshed", data)


def fire_metrics_update(
    ws: WebSocket, *, prompt_tokens: int, context_window: int,
    context_pct: int,
) -> None:
    """Fire-and-forget metrics update (non-blocking)."""
    ws_send_nowait(ws, "metrics_update", {
        "prompt_tokens": prompt_tokens,
        "context_window": context_window,
        "context_pct": context_pct,
    })

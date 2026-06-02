"""Typed WebSocket message send helpers.

Thin wrappers that construct the correct payload and delegate to
``ws_handler.ws_send`` / ``ws_handler.ws_send_nowait``.  All TypedDict
message contracts and type aliases are imported from ``ws_protocol``.

Usage::

    from lean_ai.workflow.ws_messages import send_stage_change, send_error

    await send_stage_change(session, stage="planning")
    await send_error(session, message="something broke")
"""

from __future__ import annotations

from lean_ai.workflow.ws_handler import ws_send, ws_send_nowait
from lean_ai.workflow.ws_protocol import (
    ApprovalRequiredMessage,
    ApproveMessage,
    ApproveToolMessage,
    AssistantContentMessage,
    BranchCreatedMessage,
    CancelMessage,
    CancelledMessage,
    TestResultMessage,
    ExecutionCheckpointMessage,
    ClarificationNeededMessage,
    ClientMessageType,
    CompleteMessage,
    ConfirmMemoryMessage,
    ContextRefreshedMessage,
    DiffMessage,
    DenyToolMessage,
    ErrorMessage,
    ExecutionChecklistMessage,
    MemorySuggestedMessage,
    MetricsResetMessage,
    MetricsUpdateMessage,
    PongMessage,
    PingMessage,
    PlanRejectedMessage,
    PlanRevisionMessage,
    RefinerStatusMessage,
    RejectMessage,
    RejectMemoryMessage,
    ResumeMessage,
    SaveMemoryManualMessage,
    ServerMessageType,
    StageChangeMessage,
    StageStatusMessage,
    ThinkingContentMessage,
    ToolApprovalRequiredMessage,
    ToolProgressMessage,
    UserMessage,
    VisionDescriptionMessage,
    WorkflowSession,
)

# ── Typed send helpers ──────────────────────────────────────────
#
# Thin wrappers that construct the correct payload and delegate to
# the existing ws_send / ws_send_nowait.  Use these at call sites
# for compile-time type safety without changing the wire format.


async def send_stage_change(session: WorkflowSession, *, stage: str) -> None:
    await ws_send(session, "stage_change", {"stage": stage})


async def send_stage_status(
    session: WorkflowSession,
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
    await ws_send(session, "stage_status", data)


async def send_error(session: WorkflowSession, *, message: str) -> None:
    await ws_send(session, "error", {"message": message})


async def send_diff(session: WorkflowSession, *, file: str, diff: str) -> None:
    import hashlib

    diff_hash = hashlib.sha256((diff or "").encode("utf-8")).hexdigest()[:16]
    await ws_send(
        session,
        "diff",
        {
            "file": file,
            "diff": diff,
            "diff_hash": diff_hash,
        },
    )


async def send_test_result(
    session: WorkflowSession,
    *,
    passed: bool,
    output: str,
    command: str | None = None,
) -> None:
    data: dict = {"passed": passed, "output": output}
    if command is not None:
        data["command"] = command
    await ws_send(session, "test_result", data)


async def send_tool_approval_required(
    session: WorkflowSession,
    *,
    tool: str,
    command: str,
    reason: str,
) -> None:
    await ws_send(
        session,
        "tool_approval_required",
        {
            "tool": tool,
            "command": command,
            "reason": reason,
        },
    )


async def send_execution_checkpoint(
    session: WorkflowSession,
    *,
    step: int,
    total: int,
    tool: str,
    file_path: str = "",
    description: str = "",
    status: str = "running",
) -> None:
    data: dict = {
        "step": step,
        "total": total,
        "tool": tool,
        "status": status,
    }
    if file_path:
        data["file_path"] = file_path
    if description:
        data["description"] = description
    await ws_send(session, "execution_checkpoint", data)


async def send_complete(session: WorkflowSession, **data: object) -> None:
    await ws_send(session, "complete", dict(data))


def fire_tool_progress(
    session: WorkflowSession,
    *,
    tool: str,
    status: str,
    description: str,
    output: str | None = None,
) -> None:
    """Fire-and-forget tool progress (non-blocking)."""
    data: dict = {"tool": tool, "status": status, "description": description}
    if output is not None:
        data["output"] = output
    ws_send_nowait(session, "tool_progress", data)


def fire_assistant_content(
    session: WorkflowSession,
    *,
    content: str,
    done: bool = False,
    streaming: bool = False,
) -> None:
    """Fire-and-forget assistant content (non-blocking)."""
    data: dict = {"content": content}
    if done:
        data["done"] = True
    if streaming:
        data["streaming"] = True
    ws_send_nowait(session, "assistant_content", data)


def fire_thinking_content(
    session: WorkflowSession,
    *,
    content: str = "",
    streaming: bool = False,
    truncated: bool = False,
) -> None:
    """Fire-and-forget thinking content (non-blocking).

    When ``truncated`` is True, the provider's thinking stream was aborted
    because accumulated tokens crossed the configured budget — the
    extension renders a warning chip in the thinking panel.
    """
    data: dict = {"content": content}
    if streaming:
        data["streaming"] = True
    if truncated:
        data["truncated"] = True
    ws_send_nowait(session, "thinking_content", data)


def fire_context_refreshed(
    session: WorkflowSession,
    *,
    dropped_messages: int,
    reason: str = "",
) -> None:
    """Fire-and-forget context refresh notification (non-blocking)."""
    data: dict = {"dropped_messages": dropped_messages}
    if reason:
        data["reason"] = reason
    ws_send_nowait(session, "context_refreshed", data)


def fire_metrics_update(
    session: WorkflowSession,
    *,
    prompt_tokens: int,
    context_window: int,
    context_percent: int,
) -> None:
    """Fire-and-forget metrics update (non-blocking)."""
    ws_send_nowait(
        session,
        "metrics_update",
        {
            "prompt_tokens": prompt_tokens,
            "context_window": context_window,
            "context_percent": context_percent,
        },
    )


def fire_metrics_reset(session: WorkflowSession) -> None:
    """Fire-and-forget metrics reset (non-blocking)."""
    ws_send_nowait(session, "metrics_reset", {})


def fire_memory_suggested(session: WorkflowSession, *, memory: dict) -> None:
    """Fire-and-forget memory-suggestion chip (non-blocking).

    Sent when an auto-extracted memory is eligible for user confirmation.
    The extension surfaces it as an inline chip in the chat stream.
    """
    data: dict = {
        "memory_id": memory.get("id", ""),
        "category": memory.get("category", ""),
        "content": memory.get("content", ""),
    }
    phase = memory.get("source_phase")
    if phase:
        data["source_phase"] = phase
    tags = memory.get("tags")
    if tags:
        data["tags"] = tags
    ws_send_nowait(session, "memory_suggested", data)

"""Workflow WebSocket protocol abstractions.

Defines the ``WorkflowSession`` Protocol (send-only interface) and all
``TypedDict`` message contracts used on the wire.  This module has zero
FastAPI / asyncio dependencies so it can be imported in isolation.
"""

from __future__ import annotations

from typing import Literal, Protocol, TypedDict

# ── WorkflowSession protocol ──────────────────────────────────────


class WorkflowSessionClosedError(Exception):
    """Raised when the workflow session closes while awaiting user input."""


class WorkflowSession(Protocol):
    """Send-only WebSocket session interface.

    Implementations wrap a FastAPI ``WebSocket`` (or any other async
    transport) and expose a small, framework-agnostic API.
    """

    async def send(self, data: dict[str, object]) -> None: ...

    def send_nowait(self, data: dict[str, object]) -> None: ...

    def is_connected(self) -> bool: ...


# ── Server → Client messages ──────────────────────────────────────


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
    user_summary: str
    plan_validation_warnings: list[str]


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


class DiffMessage(TypedDict, total=False):
    type: Literal["diff"]  # pyright: ignore[reportGeneralTypeIssues]
    file: str
    diff: str
    diff_hash: str  # sha256(diff)[:16] — used to pair accept/reject decisions


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
    # When True, the provider's thinking stream was aborted because
    # accumulated tokens crossed the reasoning_effort soft limit or the
    # max_thinking_tokens safety rail.  The extension renders a warning
    # chip inline in the collapsible thinking panel.
    truncated: bool


class MetricsUpdateMessage(TypedDict, total=False):
    type: Literal["metrics_update"]  # pyright: ignore[reportGeneralTypeIssues]
    prompt_tokens: int
    context_window: int
    context_percent: int


class MetricsResetMessage(TypedDict):
    type: Literal["metrics_reset"]


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


class MemorySuggestedMessage(TypedDict, total=False):
    type: Literal["memory_suggested"]  # pyright: ignore[reportGeneralTypeIssues]
    memory_id: str
    category: str
    content: str
    source_phase: str
    tags: list[str]


# ── Server message type union ─────────────────────────────────────

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
    "metrics_reset",
    "branch_created",
    "pong",
    "vision_description",
    "refiner_status",
    "memory_suggested",
]

# ── Client → Server messages ──────────────────────────────────────


class UserMessage(TypedDict, total=False):
    type: Literal["user_message"]  # pyright: ignore[reportGeneralTypeIssues]
    content: str
    text: str
    repo_root: str
    workspace_path: str


class RejectMessage(TypedDict, total=False):
    type: Literal["reject"]  # pyright: ignore[reportGeneralTypeIssues]
    feedback: str
    content: str
    text: str


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


class ConfirmMemoryMessage(TypedDict):
    type: Literal["confirm_memory"]
    memory_id: str


class RejectMemoryMessage(TypedDict):
    type: Literal["reject_memory"]
    memory_id: str


class SaveMemoryManualMessage(TypedDict, total=False):
    type: Literal["save_memory_manual"]  # pyright: ignore[reportGeneralTypeIssues]
    category: str
    content: str
    tags: list[str]


ClientMessageType = Literal[
    "user_message",
    "reject",
    "cancel",
    "approve",
    "approve_tool",
    "deny_tool",
    "ping",
    "resume",
    "confirm_memory",
    "reject_memory",
    "save_memory_manual",
]

"""Abstract base class for external service integrations.

Each integration implements pull/push methods for two-way sync
with external task tracking services (Jira, ServiceNow, etc.).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class TaskStatus(str, Enum):
    """Normalized task status across all integrations."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"


class TaskPriority(str, Enum):
    """Normalized priority levels."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class ExternalTask:
    """Normalized representation of an external task/ticket."""

    external_id: str
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.OPEN
    priority: TaskPriority = TaskPriority.MEDIUM
    assignee: str = ""
    labels: list[str] = field(default_factory=list)
    url: str = ""
    source: str = ""
    raw_data: dict = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class SessionSummary:
    """Summary of a Lean AI session for pushing to external services."""

    session_id: str
    task_description: str
    status: str  # "completed", "merged", "abandoned"
    branch_name: str = ""
    files_changed: list[str] = field(default_factory=list)
    commits: list[dict] = field(default_factory=list)
    duration_seconds: float = 0.0
    tool_calls_count: int = 0
    created_at: str = ""
    completed_at: str = ""


@dataclass
class WebhookEvent:
    """Incoming webhook event from an external service."""

    event_type: str
    external_id: str
    source: str
    payload: dict = field(default_factory=dict)


class IntegrationProvider(ABC):
    """Abstract base class for external service integrations.

    Implementations provide two-way sync with task tracking services.
    The integration registry manages lifecycle and configuration.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this integration (e.g. 'jira', 'servicenow')."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name (e.g. 'Jira', 'ServiceNow')."""

    @abstractmethod
    async def check_health(self) -> bool:
        """Verify connectivity to the external service."""

    # -- Pull operations --

    @abstractmethod
    async def list_tasks(
        self,
        project: str | None = None,
        status: TaskStatus | None = None,
        assignee: str | None = None,
        limit: int = 50,
    ) -> list[ExternalTask]:
        """Fetch tasks from the external service."""

    @abstractmethod
    async def get_task(self, external_id: str) -> ExternalTask | None:
        """Fetch a single task by its external ID."""

    # -- Push operations --

    @abstractmethod
    async def push_session_summary(
        self,
        external_id: str,
        summary: SessionSummary,
    ) -> bool:
        """Push a session summary (work log, comment) to the linked external task."""

    @abstractmethod
    async def update_task_status(
        self,
        external_id: str,
        status: TaskStatus,
    ) -> bool:
        """Update the status of an external task."""

    # -- Webhook handling (optional) --

    async def handle_webhook(self, event: WebhookEvent) -> dict:
        """Process an incoming webhook event. Override if the service supports webhooks."""
        return {"status": "not_implemented"}

    # -- Search (optional) --

    async def search_tasks(self, query: str, limit: int = 20) -> list[ExternalTask]:
        """Search tasks by text query. Default: return empty list."""
        return []

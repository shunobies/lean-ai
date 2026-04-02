"""ServiceNow integration provider.

Connects to the ServiceNow Table API for two-way task sync:
pull incidents/tasks, push session summaries as work notes,
update states, and handle webhooks.

Requires: instance_url, username, password.
Auth: Basic Auth (username:password).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

import httpx

from lean_ai.integrations.base import (
    ExternalTask,
    IntegrationProvider,
    SessionSummary,
    TaskPriority,
    TaskStatus,
    WebhookEvent,
)
from lean_ai.integrations.registry import register_integration

logger = logging.getLogger(__name__)

# ── State / priority mappings ─────────────────────────────────────────────────
# ServiceNow uses numeric state and priority values.

_STATE_MAP: dict[str, TaskStatus] = {
    "1": TaskStatus.OPEN,          # New
    "2": TaskStatus.IN_PROGRESS,   # In Progress
    "3": TaskStatus.BLOCKED,       # On Hold
    "6": TaskStatus.DONE,          # Resolved
    "7": TaskStatus.DONE,          # Closed
}

_PRIORITY_MAP: dict[str, TaskPriority] = {
    "1": TaskPriority.CRITICAL,
    "2": TaskPriority.HIGH,
    "3": TaskPriority.MEDIUM,
    "4": TaskPriority.LOW,
    "5": TaskPriority.LOW,
}

# Reverse: TaskStatus → ServiceNow state value
_REVERSE_STATE: dict[TaskStatus, str] = {
    TaskStatus.OPEN: "1",
    TaskStatus.IN_PROGRESS: "2",
    TaskStatus.BLOCKED: "3",
    TaskStatus.DONE: "6",
}

# ServiceNow sys_id pattern: 32-char hex
_SYS_ID_RE = re.compile(r"^[a-f0-9]{32}$", re.IGNORECASE)


def _map_state(raw: str) -> TaskStatus:
    """Map a ServiceNow state value to our normalized TaskStatus."""
    return _STATE_MAP.get(str(raw).strip(), TaskStatus.OPEN)


def _map_priority(raw: str) -> TaskPriority:
    """Map a ServiceNow priority value to our normalized TaskPriority."""
    return _PRIORITY_MAP.get(str(raw).strip(), TaskPriority.MEDIUM)


@register_integration
class ServiceNowProvider(IntegrationProvider):
    """ServiceNow integration via Table API."""

    INTEGRATION_NAME = "servicenow"
    DISPLAY_NAME = "ServiceNow"

    def __init__(
        self,
        instance_url: str,
        username: str,
        password: str,
        table: str = "incident",
    ) -> None:
        self._base_url = instance_url.rstrip("/")
        self._username = username
        self._password = password
        self._table = table

    @property
    def name(self) -> str:
        return "servicenow"

    @property
    def display_name(self) -> str:
        return "ServiceNow"

    # ── HTTP helper ───────────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs,
    ) -> httpx.Response | None:
        """Central HTTP helper with Basic Auth and 30s timeout."""
        url = f"{self._base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.request(
                    method,
                    url,
                    auth=httpx.BasicAuth(self._username, self._password),
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    **kwargs,
                )
                return resp
        except Exception as exc:
            logger.warning("ServiceNow request failed: %s %s — %s", method, path, exc)
            return None

    # ── Parsing helpers ───────────────────────────────────────────────────────

    def _parse_record(self, data: dict) -> ExternalTask:
        """Map a ServiceNow table record to ExternalTask."""
        # ServiceNow fields may be plain strings or {display_value, value} dicts
        def _val(field) -> str:
            if isinstance(field, dict):
                return field.get("display_value", field.get("value", ""))
            return str(field) if field else ""

        sys_id = _val(data.get("sys_id", ""))
        number = _val(data.get("number", ""))
        external_id = number or sys_id

        state = _val(data.get("state", ""))
        priority = _val(data.get("priority", ""))

        assignee = ""
        assigned_to = data.get("assigned_to", "")
        if isinstance(assigned_to, dict):
            assignee = assigned_to.get("display_value", "")
        elif assigned_to:
            assignee = str(assigned_to)

        created_at = None
        created_raw = _val(data.get("sys_created_on", ""))
        if created_raw:
            try:
                created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        updated_at = None
        updated_raw = _val(data.get("sys_updated_on", ""))
        if updated_raw:
            try:
                updated_at = datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        return ExternalTask(
            external_id=external_id,
            title=_val(data.get("short_description", "")),
            description=_val(data.get("description", "")),
            status=_map_state(state),
            priority=_map_priority(priority),
            assignee=assignee,
            labels=[],
            url=f"{self._base_url}/nav_to.do?uri={self._table}.do?sys_id={sys_id}",
            source="servicenow",
            raw_data=data,
            created_at=created_at,
            updated_at=updated_at,
        )

    async def _resolve_sys_id(self, external_id: str) -> str | None:
        """Resolve an external ID (INC number or sys_id) to a sys_id.

        If external_id is already a 32-char hex string, return it directly.
        Otherwise, query by number field.
        """
        if _SYS_ID_RE.match(external_id):
            return external_id
        # Query by number
        resp = await self._request(
            "GET",
            f"/api/now/table/{self._table}",
            params={
                "sysparm_query": f"number={external_id}",
                "sysparm_limit": "1",
                "sysparm_fields": "sys_id",
            },
        )
        if resp is None or resp.status_code != 200:
            return None
        results = resp.json().get("result", [])
        if not results:
            return None
        return results[0].get("sys_id")

    # ── Health ────────────────────────────────────────────────────────────────

    async def check_health(self) -> bool:
        """Verify connectivity by fetching one record from the table."""
        resp = await self._request(
            "GET",
            f"/api/now/table/{self._table}",
            params={"sysparm_limit": "1"},
        )
        if resp is None:
            return False
        return resp.status_code == 200

    # ── Pull: list / get / search ─────────────────────────────────────────────

    async def list_tasks(
        self,
        project: str | None = None,
        status: TaskStatus | None = None,
        assignee: str | None = None,
        limit: int = 50,
    ) -> list[ExternalTask]:
        """Query records using ServiceNow encoded query syntax."""
        clauses: list[str] = []
        if project:
            clauses.append(f"category={project}")
        if status:
            state_val = _REVERSE_STATE.get(status)
            if state_val:
                clauses.append(f"state={state_val}")
        if assignee:
            clauses.append(f"assigned_to.name={assignee}")

        query = "^".join(clauses) if clauses else "ORDERBYDESCsys_updated_on"
        if clauses:
            query += "^ORDERBYDESCsys_updated_on"

        resp = await self._request(
            "GET",
            f"/api/now/table/{self._table}",
            params={
                "sysparm_query": query,
                "sysparm_limit": str(limit),
            },
        )
        if resp is None or resp.status_code != 200:
            return []
        results = resp.json().get("result", [])
        return [self._parse_record(r) for r in results]

    async def get_task(self, external_id: str) -> ExternalTask | None:
        """Fetch a single record by number or sys_id."""
        sys_id = await self._resolve_sys_id(external_id)
        if not sys_id:
            return None
        resp = await self._request(
            "GET", f"/api/now/table/{self._table}/{sys_id}",
        )
        if resp is None or resp.status_code != 200:
            return None
        result = resp.json().get("result")
        if not result:
            return None
        return self._parse_record(result)

    async def search_tasks(self, query: str, limit: int = 20) -> list[ExternalTask]:
        """Search by short_description or description containing the query."""
        encoded_query = (
            f"short_descriptionLIKE{query}"
            f"^ORdescriptionLIKE{query}"
            f"^ORDERBYDESCsys_updated_on"
        )
        resp = await self._request(
            "GET",
            f"/api/now/table/{self._table}",
            params={
                "sysparm_query": encoded_query,
                "sysparm_limit": str(limit),
            },
        )
        if resp is None or resp.status_code != 200:
            return []
        results = resp.json().get("result", [])
        return [self._parse_record(r) for r in results]

    # ── Push: work note ───────────────────────────────────────────────────────

    async def push_session_summary(
        self,
        external_id: str,
        summary: SessionSummary,
    ) -> bool:
        """Push session summary as a work_notes field update."""
        sys_id = await self._resolve_sys_id(external_id)
        if not sys_id:
            logger.warning("Could not resolve ServiceNow ID: %s", external_id)
            return False

        lines = [
            f"[Lean AI Session: {summary.session_id}]",
            f"Task: {summary.task_description}",
            f"Status: {summary.status}",
        ]
        if summary.branch_name:
            lines.append(f"Branch: {summary.branch_name}")
        if summary.files_changed:
            lines.append(f"Files changed: {', '.join(summary.files_changed[:10])}")
        if summary.commits:
            shas = [c.get("commit_sha", "")[:8] for c in summary.commits[:5]]
            lines.append(f"Commits: {', '.join(shas)}")
        if summary.duration_seconds > 0:
            mins = summary.duration_seconds / 60
            lines.append(f"Duration: {mins:.1f} minutes")
        if summary.tool_calls_count:
            lines.append(f"Tool calls: {summary.tool_calls_count}")

        work_note = "\n".join(lines)

        resp = await self._request(
            "PUT",
            f"/api/now/table/{self._table}/{sys_id}",
            json={"work_notes": work_note},
        )
        if resp is None or resp.status_code != 200:
            logger.warning("Failed to push ServiceNow work note for %s", external_id)
            return False
        return True

    # ── Push: status update ───────────────────────────────────────────────────

    async def update_task_status(
        self,
        external_id: str,
        status: TaskStatus,
    ) -> bool:
        """Update the state field on a ServiceNow record."""
        sys_id = await self._resolve_sys_id(external_id)
        if not sys_id:
            return False

        state_val = _REVERSE_STATE.get(status)
        if state_val is None:
            logger.warning("No ServiceNow state mapping for %s", status.value)
            return False

        resp = await self._request(
            "PUT",
            f"/api/now/table/{self._table}/{sys_id}",
            json={"state": state_val},
        )
        return resp is not None and resp.status_code == 200

    # ── Webhooks ──────────────────────────────────────────────────────────────

    async def handle_webhook(self, event: WebhookEvent) -> dict:
        """Parse ServiceNow webhook payload and return normalized event info."""
        payload = event.payload
        return {
            "status": "processed",
            "event_type": event.event_type,
            "record_id": event.external_id,
            "table": payload.get("table", self._table),
            "changes": payload.get("changes", {}),
        }

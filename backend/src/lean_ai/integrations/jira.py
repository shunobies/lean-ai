"""Jira Cloud integration provider.

Connects to Jira Cloud REST API v3 for two-way task sync:
pull issues, push session summaries as comments + worklogs,
update statuses via transitions, and handle webhooks.

Requires: base_url, email, api_token (Jira API token).
Auth: Basic Auth (base64 of email:token).
"""

from __future__ import annotations

import base64
import logging
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

# ── Status / priority mappings ────────────────────────────────────────────────

_STATUS_MAP: dict[str, TaskStatus] = {
    "to do": TaskStatus.OPEN,
    "open": TaskStatus.OPEN,
    "new": TaskStatus.OPEN,
    "backlog": TaskStatus.OPEN,
    "in progress": TaskStatus.IN_PROGRESS,
    "in review": TaskStatus.IN_PROGRESS,
    "done": TaskStatus.DONE,
    "closed": TaskStatus.DONE,
    "resolved": TaskStatus.DONE,
    "blocked": TaskStatus.BLOCKED,
}

_PRIORITY_MAP: dict[str, TaskPriority] = {
    "highest": TaskPriority.CRITICAL,
    "critical": TaskPriority.CRITICAL,
    "high": TaskPriority.HIGH,
    "medium": TaskPriority.MEDIUM,
    "low": TaskPriority.LOW,
    "lowest": TaskPriority.LOW,
}

# Reverse: TaskStatus → Jira target status name fragments for transition matching
_REVERSE_STATUS: dict[TaskStatus, set[str]] = {
    TaskStatus.OPEN: {"to do", "open", "new", "backlog", "reopen"},
    TaskStatus.IN_PROGRESS: {"in progress", "start progress", "in review"},
    TaskStatus.DONE: {"done", "close", "resolve"},
    TaskStatus.BLOCKED: {"blocked"},
}


def _map_status(raw: str) -> TaskStatus:
    """Map a Jira status name to our normalized TaskStatus."""
    return _STATUS_MAP.get(raw.lower().strip(), TaskStatus.OPEN)


def _map_priority(raw: str) -> TaskPriority:
    """Map a Jira priority name to our normalized TaskPriority."""
    return _PRIORITY_MAP.get(raw.lower().strip(), TaskPriority.MEDIUM)


def _extract_adf_text(node: dict) -> str:
    """Recursively extract plain text from Atlassian Document Format (ADF)."""
    if not isinstance(node, dict):
        return ""
    parts: list[str] = []
    if node.get("type") == "text":
        parts.append(node.get("text", ""))
    for child in node.get("content", []):
        parts.append(_extract_adf_text(child))
    return "".join(parts)


@register_integration
class JiraProvider(IntegrationProvider):
    """Jira Cloud integration via REST API v3."""

    INTEGRATION_NAME = "jira"
    DISPLAY_NAME = "Jira Cloud"

    def __init__(self, base_url: str, email: str, api_token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._email = email
        self._api_token = api_token
        creds = base64.b64encode(f"{email}:{api_token}".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @property
    def name(self) -> str:
        return "jira"

    @property
    def display_name(self) -> str:
        return "Jira Cloud"

    # ── HTTP helper ───────────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs,
    ) -> httpx.Response | None:
        """Central HTTP helper with 30s timeout. Returns None on error."""
        url = f"{self._base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.request(
                    method, url, headers=self._headers, **kwargs,
                )
                return resp
        except Exception as exc:
            logger.warning("Jira request failed: %s %s — %s", method, path, exc)
            return None

    # ── Parsing helpers ───────────────────────────────────────────────────────

    def _parse_issue(self, data: dict) -> ExternalTask:
        """Map Jira issue JSON to ExternalTask."""
        fields = data.get("fields", {})
        status_raw = ""
        if fields.get("status"):
            status_raw = fields["status"].get("name", "")
        priority_raw = ""
        if fields.get("priority"):
            priority_raw = fields["priority"].get("name", "")
        assignee = ""
        if fields.get("assignee"):
            assignee = fields["assignee"].get("displayName", "")
        labels = fields.get("labels", [])
        description = ""
        if fields.get("description"):
            description = _extract_adf_text(fields["description"])

        created_at = None
        if fields.get("created"):
            try:
                created_at = datetime.fromisoformat(
                    fields["created"].replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass

        updated_at = None
        if fields.get("updated"):
            try:
                updated_at = datetime.fromisoformat(
                    fields["updated"].replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass

        return ExternalTask(
            external_id=data.get("key", ""),
            title=fields.get("summary", ""),
            description=description,
            status=_map_status(status_raw),
            priority=_map_priority(priority_raw),
            assignee=assignee,
            labels=labels,
            url=f"{self._base_url}/browse/{data.get('key', '')}",
            source="jira",
            raw_data=data,
            created_at=created_at,
            updated_at=updated_at,
        )

    # ── Health ────────────────────────────────────────────────────────────────

    async def check_health(self) -> bool:
        """Verify connectivity via GET /rest/api/3/myself."""
        resp = await self._request("GET", "/rest/api/3/myself")
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
        """Build JQL and fetch issues via /rest/api/3/search."""
        clauses: list[str] = []
        if project:
            clauses.append(f'project = "{project}"')
        if status:
            # Map our status to Jira status names
            names = _REVERSE_STATUS.get(status, set())
            if names:
                quoted = ", ".join(f'"{n}"' for n in sorted(names))
                clauses.append(f"status IN ({quoted})")
        if assignee:
            clauses.append(f'assignee = "{assignee}"')
        jql = " AND ".join(clauses) if clauses else "ORDER BY updated DESC"
        if clauses:
            jql += " ORDER BY updated DESC"

        resp = await self._request(
            "GET",
            "/rest/api/3/search",
            params={"jql": jql, "maxResults": limit},
        )
        if resp is None or resp.status_code != 200:
            return []
        data = resp.json()
        return [self._parse_issue(issue) for issue in data.get("issues", [])]

    async def get_task(self, external_id: str) -> ExternalTask | None:
        """Fetch a single issue by key (e.g. PROJ-123)."""
        resp = await self._request("GET", f"/rest/api/3/issue/{external_id}")
        if resp is None or resp.status_code != 200:
            return None
        return self._parse_issue(resp.json())

    async def search_tasks(self, query: str, limit: int = 20) -> list[ExternalTask]:
        """Search issues by text using JQL text search."""
        jql = f'text ~ "{query}" ORDER BY updated DESC'
        resp = await self._request(
            "GET",
            "/rest/api/3/search",
            params={"jql": jql, "maxResults": limit},
        )
        if resp is None or resp.status_code != 200:
            return []
        data = resp.json()
        return [self._parse_issue(issue) for issue in data.get("issues", [])]

    # ── Push: comment + worklog ───────────────────────────────────────────────

    async def push_session_summary(
        self,
        external_id: str,
        summary: SessionSummary,
    ) -> bool:
        """Post a comment (ADF) and optional worklog to the issue."""
        # Build ADF comment body
        lines = [
            f"Lean AI Session: {summary.session_id}",
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

        # ADF document with a single paragraph per line
        adf_content = []
        for line in lines:
            adf_content.append({
                "type": "paragraph",
                "content": [{"type": "text", "text": line}],
            })

        comment_body = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": adf_content,
            },
        }

        resp = await self._request(
            "POST",
            f"/rest/api/3/issue/{external_id}/comment",
            json=comment_body,
        )
        if resp is None or resp.status_code not in (200, 201):
            logger.warning("Failed to post Jira comment for %s", external_id)
            return False

        # Post worklog if duration > 0
        if summary.duration_seconds > 0:
            seconds = int(summary.duration_seconds)
            if seconds > 0:
                worklog_body = {
                    "timeSpentSeconds": seconds,
                    "comment": {
                        "type": "doc",
                        "version": 1,
                        "content": [{
                            "type": "paragraph",
                            "content": [{
                                "type": "text",
                                "text": f"Lean AI session {summary.session_id}",
                            }],
                        }],
                    },
                }
                wl_resp = await self._request(
                    "POST",
                    f"/rest/api/3/issue/{external_id}/worklog",
                    json=worklog_body,
                )
                if wl_resp is None or wl_resp.status_code not in (200, 201):
                    logger.warning("Failed to post Jira worklog for %s", external_id)
                    # Comment succeeded — still return True

        return True

    # ── Push: status update via transitions ───────────────────────────────────

    async def update_task_status(
        self,
        external_id: str,
        status: TaskStatus,
    ) -> bool:
        """Update issue status by finding and executing a matching transition."""
        # Get available transitions
        resp = await self._request(
            "GET", f"/rest/api/3/issue/{external_id}/transitions",
        )
        if resp is None or resp.status_code != 200:
            return False

        transitions = resp.json().get("transitions", [])
        target_names = _REVERSE_STATUS.get(status, set())

        # Find a transition whose target status name matches
        transition_id = None
        for t in transitions:
            t_status = t.get("to", {}).get("name", "").lower().strip()
            if t_status in target_names:
                transition_id = t["id"]
                break
            # Also try matching the transition name itself
            t_name = t.get("name", "").lower().strip()
            if t_name in target_names:
                transition_id = t["id"]
                break

        if transition_id is None:
            logger.warning(
                "No matching Jira transition for %s → %s (available: %s)",
                external_id, status.value,
                [t.get("name") for t in transitions],
            )
            return False

        resp = await self._request(
            "POST",
            f"/rest/api/3/issue/{external_id}/transitions",
            json={"transition": {"id": transition_id}},
        )
        return resp is not None and resp.status_code in (200, 204)

    # ── Webhooks ──────────────────────────────────────────────────────────────

    async def handle_webhook(self, event: WebhookEvent) -> dict:
        """Parse Jira webhook payload and return normalized event info."""
        payload = event.payload
        issue = payload.get("issue", {})
        key = issue.get("key", event.external_id)
        changelog = payload.get("changelog", {})

        return {
            "status": "processed",
            "event_type": event.event_type,
            "issue_key": key,
            "changes": changelog.get("items", []),
        }

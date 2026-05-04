"""GitHub integration provider.

Connects to the GitHub REST API for two-way issue/PR sync:
fetch issues, search pull requests/issues, push Lean AI session
summaries as comments, and close/reopen linked tasks.

Requires: repo_full_name (owner/repo), api_token (PAT or fine-grained token).
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
)
from lean_ai.integrations.formatting import build_markdown_session_summary
from lean_ai.integrations.registry import register_integration

logger = logging.getLogger(__name__)

_REPO_ID_RE = re.compile(r"^[^/\s]+/[^/\s]+$")


def _map_status(raw: str) -> TaskStatus:
    return TaskStatus.DONE if raw.lower().strip() == "closed" else TaskStatus.OPEN


def _map_priority(labels: list[dict] | None) -> TaskPriority:
    for label in labels or []:
        name = str(label.get("name", "")).lower().strip()
        if "critical" in name:
            return TaskPriority.CRITICAL
        if "high" in name:
            return TaskPriority.HIGH
        if "low" in name:
            return TaskPriority.LOW
    return TaskPriority.MEDIUM


def _parse_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


@register_integration
class GitHubProvider(IntegrationProvider):
    """GitHub issue/PR integration via the REST API."""

    INTEGRATION_NAME = "github"
    DISPLAY_NAME = "GitHub"

    def __init__(self, repo_full_name: str, api_token: str) -> None:
        self._repo_full_name = repo_full_name.strip()
        self._api_token = api_token
        self._headers = {
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "lean-ai",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    @property
    def name(self) -> str:
        return "github"

    @property
    def display_name(self) -> str:
        return "GitHub"

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response | None:
        url = f"https://api.github.com{path}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                return await client.request(method, url, headers=self._headers, **kwargs)
        except Exception as exc:
            logger.warning("GitHub request failed: %s %s — %s", method, path, exc)
            return None

    def _repo_and_number(self, external_id: str) -> tuple[str, int] | None:
        value = external_id.strip()
        if not value:
            return None

        if "#" in value:
            repo, number = value.rsplit("#", 1)
            repo = repo.strip() or self._repo_full_name
            number = number.strip()
        else:
            repo = self._repo_full_name
            number = value.lstrip("#").strip()

        if not _REPO_ID_RE.match(repo) or not number.isdigit():
            return None
        return repo, int(number)

    def _parse_issue(self, data: dict) -> ExternalTask:
        html_url = str(data.get("html_url", "") or "")
        repo = self._repo_full_name
        if "/repos/" in str(data.get("repository_url", "")):
            repo = str(data["repository_url"]).split("/repos/", 1)[1]
        elif "github.com/" in html_url:
            parts = html_url.split("github.com/", 1)[1].split("/")
            if len(parts) >= 2:
                repo = f"{parts[0]}/{parts[1]}"

        assignee = ""
        if isinstance(data.get("assignee"), dict):
            assignee = str(data["assignee"].get("login", "") or "")

        return ExternalTask(
            external_id=f"{repo}#{data.get('number', '')}",
            title=str(data.get("title", "") or ""),
            description=str(data.get("body", "") or ""),
            status=_map_status(str(data.get("state", "") or "")),
            priority=_map_priority(data.get("labels")),
            assignee=assignee,
            labels=[str(label.get("name", "") or "") for label in data.get("labels", [])],
            url=html_url,
            source="github",
            raw_data=data,
            created_at=_parse_datetime(data.get("created_at")),
            updated_at=_parse_datetime(data.get("updated_at")),
        )

    async def check_health(self) -> bool:
        resp = await self._request("GET", f"/repos/{self._repo_full_name}")
        return resp is not None and resp.status_code == 200

    async def list_tasks(
        self,
        project: str | None = None,
        status: TaskStatus | None = None,
        assignee: str | None = None,
        limit: int = 50,
    ) -> list[ExternalTask]:
        repo = project or self._repo_full_name
        state = "all"
        if status == TaskStatus.DONE:
            state = "closed"
        elif status in {TaskStatus.OPEN, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED}:
            state = "open"

        params = {"state": state, "per_page": min(limit, 100)}
        if assignee:
            params["assignee"] = assignee

        resp = await self._request("GET", f"/repos/{repo}/issues", params=params)
        if resp is None or resp.status_code != 200:
            return []
        return [self._parse_issue(item) for item in resp.json()]

    async def get_task(self, external_id: str) -> ExternalTask | None:
        parsed = self._repo_and_number(external_id)
        if not parsed:
            return None
        repo, number = parsed
        resp = await self._request("GET", f"/repos/{repo}/issues/{number}")
        if resp is None or resp.status_code != 200:
            return None
        return self._parse_issue(resp.json())

    async def push_session_summary(
        self,
        external_id: str,
        summary: SessionSummary,
    ) -> bool:
        parsed = self._repo_and_number(external_id)
        if not parsed:
            logger.warning("Invalid GitHub external_id: %s", external_id)
            return False
        repo, number = parsed
        resp = await self._request(
            "POST",
            f"/repos/{repo}/issues/{number}/comments",
            json={"body": build_markdown_session_summary(summary)},
        )
        if resp is None or resp.status_code not in (200, 201):
            logger.warning("Failed to post GitHub comment for %s", external_id)
            return False
        return True

    async def update_task_status(
        self,
        external_id: str,
        status: TaskStatus,
    ) -> bool:
        parsed = self._repo_and_number(external_id)
        if not parsed:
            return False
        repo, number = parsed
        target_state = "closed" if status == TaskStatus.DONE else "open"
        resp = await self._request(
            "PATCH",
            f"/repos/{repo}/issues/{number}",
            json={"state": target_state},
        )
        return resp is not None and resp.status_code == 200

    async def search_tasks(self, query: str, limit: int = 20) -> list[ExternalTask]:
        q = f"repo:{self._repo_full_name} {query}".strip()
        resp = await self._request(
            "GET",
            "/search/issues",
            params={"q": q, "per_page": min(limit, 100)},
        )
        if resp is None or resp.status_code != 200:
            return []
        return [self._parse_issue(item) for item in resp.json().get("items", [])]

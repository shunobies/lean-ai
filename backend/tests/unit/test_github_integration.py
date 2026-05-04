"""Tests for GitHubProvider — parsing, search/list/get, comments, and status updates."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lean_ai.integrations.base import ModelUsage, SessionSummary, TaskPriority, TaskStatus
from lean_ai.integrations.github import GitHubProvider, _map_priority, _map_status


def _make_provider(repo_full_name="owner/repo", api_token="tok"):
    return GitHubProvider(repo_full_name=repo_full_name, api_token=api_token)


def _make_issue_json(
    *,
    repo="owner/repo",
    number=12,
    title="Fix login bug",
    state="open",
    labels=None,
    assignee="alice",
):
    return {
        "number": number,
        "title": title,
        "body": "Issue body",
        "state": state,
        "labels": labels or [{"name": "priority:high"}],
        "assignee": {"login": assignee} if assignee else None,
        "html_url": f"https://github.com/{repo}/issues/{number}",
        "repository_url": f"https://api.github.com/repos/{repo}",
        "created_at": "2024-01-15T10:30:00Z",
        "updated_at": "2024-01-16T14:20:00Z",
    }


def _mock_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


class TestStatusMapping:
    def test_open_defaults(self):
        assert _map_status("open") == TaskStatus.OPEN
        assert _map_status("anything-else") == TaskStatus.OPEN

    def test_closed_maps_to_done(self):
        assert _map_status("closed") == TaskStatus.DONE


class TestPriorityMapping:
    def test_critical(self):
        assert _map_priority([{"name": "priority:critical"}]) == TaskPriority.CRITICAL

    def test_high(self):
        assert _map_priority([{"name": "high-priority"}]) == TaskPriority.HIGH

    def test_low(self):
        assert _map_priority([{"name": "low"}]) == TaskPriority.LOW

    def test_unknown_defaults_to_medium(self):
        assert _map_priority([{"name": "triage"}]) == TaskPriority.MEDIUM


class TestParsing:
    def test_parse_issue(self):
        provider = _make_provider()
        task = provider._parse_issue(_make_issue_json())
        assert task.external_id == "owner/repo#12"
        assert task.title == "Fix login bug"
        assert task.status == TaskStatus.OPEN
        assert task.priority == TaskPriority.HIGH
        assert task.assignee == "alice"
        assert task.source == "github"

    def test_repo_and_number_accepts_plain_number(self):
        provider = _make_provider("owner/repo")
        assert provider._repo_and_number("42") == ("owner/repo", 42)

    def test_repo_and_number_accepts_explicit_repo(self):
        provider = _make_provider("owner/repo")
        assert provider._repo_and_number("other/repo#42") == ("other/repo", 42)

    def test_repo_and_number_rejects_invalid_values(self):
        provider = _make_provider()
        assert provider._repo_and_number("abc") is None
        assert provider._repo_and_number("owner/repo#abc") is None


class TestHttpOperations:
    @pytest.mark.asyncio
    async def test_health_success(self):
        provider = _make_provider()
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _mock_response(200)
            assert await provider.check_health() is True

    @pytest.mark.asyncio
    async def test_list_tasks(self):
        provider = _make_provider()
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _mock_response(200, [_make_issue_json(number=1)])
            tasks = await provider.list_tasks(limit=10)
            assert len(tasks) == 1
            assert tasks[0].external_id == "owner/repo#1"

    @pytest.mark.asyncio
    async def test_get_task(self):
        provider = _make_provider()
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _mock_response(200, _make_issue_json(number=7))
            task = await provider.get_task("7")
            assert task is not None
            assert task.external_id == "owner/repo#7"

    @pytest.mark.asyncio
    async def test_search_tasks(self):
        provider = _make_provider()
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _mock_response(200, {"items": [_make_issue_json(number=9)]})
            tasks = await provider.search_tasks("login", limit=5)
            assert len(tasks) == 1
            assert tasks[0].external_id == "owner/repo#9"


class TestPushSummary:
    @pytest.mark.asyncio
    async def test_push_summary_posts_comment(self):
        provider = _make_provider()
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _mock_response(201, {"id": 1})
            summary = SessionSummary(
                session_id="sess-1",
                task_description="Fix login",
                status="completed",
                workflow_mode="plan",
                branch_name="fix/login",
                files_changed=["auth.py"],
                commits=[{"sha": "abc12345"}],
                duration_seconds=1800,
                tool_calls_count=15,
            )
            result = await provider.push_session_summary("12", summary)
            assert result is True
            _, path = mock_req.await_args.args[:2]
            assert path == "/repos/owner/repo/issues/12/comments"
            body = mock_req.await_args.kwargs["json"]["body"]
            assert "### Lean AI Session" in body
            assert "### Models Used" not in body

    @pytest.mark.asyncio
    async def test_push_summary_includes_models_used(self):
        provider = _make_provider()
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _mock_response(201, {"id": 1})
            summary = SessionSummary(
                session_id="sess-1",
                task_description="Fix login",
                status="completed",
                models_used=[
                    ModelUsage(
                        role="primary",
                        provider="ollama",
                        model="qwen3-coder:30b",
                        is_local=True,
                    )
                ],
            )
            result = await provider.push_session_summary("12", summary)
            assert result is True
            body = mock_req.await_args.kwargs["json"]["body"]
            assert "### Models Used" in body
            assert "qwen3-coder:30b" in body

    @pytest.mark.asyncio
    async def test_push_summary_invalid_external_id(self):
        provider = _make_provider()
        summary = SessionSummary("s", "t", "completed")
        result = await provider.push_session_summary("not-a-number", summary)
        assert result is False


class TestUpdateStatus:
    @pytest.mark.asyncio
    async def test_close_issue_for_done(self):
        provider = _make_provider()
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _mock_response(200, _make_issue_json(state="closed"))
            result = await provider.update_task_status("12", TaskStatus.DONE)
            assert result is True
            assert mock_req.await_args.kwargs["json"] == {"state": "closed"}

    @pytest.mark.asyncio
    async def test_reopen_issue_for_non_done(self):
        provider = _make_provider()
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _mock_response(200, _make_issue_json(state="open"))
            result = await provider.update_task_status("12", TaskStatus.IN_PROGRESS)
            assert result is True
            assert mock_req.await_args.kwargs["json"] == {"state": "open"}

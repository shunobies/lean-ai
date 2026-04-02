"""Tests for JiraProvider — status/priority mapping, parsing, HTTP operations."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lean_ai.integrations.base import (
    ExternalTask,
    SessionSummary,
    TaskPriority,
    TaskStatus,
    WebhookEvent,
)
from lean_ai.integrations.jira import (
    JiraProvider,
    _extract_adf_text,
    _map_priority,
    _map_status,
)

# ── Helpers ──


def _make_provider(base_url="https://test.atlassian.net", email="a@b.com", api_token="tok"):
    """Create a JiraProvider instance for testing."""
    return JiraProvider(base_url=base_url, email=email, api_token=api_token)


def _make_issue_json(
    key="PROJ-1",
    summary="Fix login bug",
    status_name="In Progress",
    priority_name="High",
    assignee_name="Alice",
    labels=None,
    description=None,
):
    """Build realistic Jira issue JSON."""
    issue = {
        "key": key,
        "fields": {
            "summary": summary,
            "status": {"name": status_name},
            "priority": {"name": priority_name},
            "assignee": {"displayName": assignee_name} if assignee_name else None,
            "labels": labels or [],
            "description": description,
            "created": "2024-01-15T10:30:00.000+0000",
            "updated": "2024-01-16T14:20:00.000+0000",
        },
    }
    return issue


def _mock_response(status_code=200, json_data=None):
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


# ── Status / priority mapping tests ──


class TestStatusMapping:

    def test_open_variants(self):
        assert _map_status("To Do") == TaskStatus.OPEN
        assert _map_status("Open") == TaskStatus.OPEN
        assert _map_status("New") == TaskStatus.OPEN
        assert _map_status("Backlog") == TaskStatus.OPEN

    def test_in_progress_variants(self):
        assert _map_status("In Progress") == TaskStatus.IN_PROGRESS
        assert _map_status("In Review") == TaskStatus.IN_PROGRESS

    def test_done_variants(self):
        assert _map_status("Done") == TaskStatus.DONE
        assert _map_status("Closed") == TaskStatus.DONE
        assert _map_status("Resolved") == TaskStatus.DONE

    def test_blocked(self):
        assert _map_status("Blocked") == TaskStatus.BLOCKED

    def test_unknown_defaults_to_open(self):
        assert _map_status("Custom Status") == TaskStatus.OPEN

    def test_case_insensitive(self):
        assert _map_status("TO DO") == TaskStatus.OPEN
        assert _map_status("in progress") == TaskStatus.IN_PROGRESS
        assert _map_status("DONE") == TaskStatus.DONE


class TestPriorityMapping:

    def test_critical(self):
        assert _map_priority("Highest") == TaskPriority.CRITICAL
        assert _map_priority("Critical") == TaskPriority.CRITICAL

    def test_high(self):
        assert _map_priority("High") == TaskPriority.HIGH

    def test_medium(self):
        assert _map_priority("Medium") == TaskPriority.MEDIUM

    def test_low(self):
        assert _map_priority("Low") == TaskPriority.LOW
        assert _map_priority("Lowest") == TaskPriority.LOW

    def test_unknown_defaults_to_medium(self):
        assert _map_priority("Custom Priority") == TaskPriority.MEDIUM


# ── ADF text extraction ──


class TestAdfExtraction:

    def test_simple_text(self):
        node = {"type": "text", "text": "Hello"}
        assert _extract_adf_text(node) == "Hello"

    def test_nested_paragraphs(self):
        doc = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "First line"},
                    ],
                },
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "Second line"},
                    ],
                },
            ],
        }
        assert "First line" in _extract_adf_text(doc)
        assert "Second line" in _extract_adf_text(doc)

    def test_non_dict_returns_empty(self):
        assert _extract_adf_text("not a dict") == ""
        assert _extract_adf_text(None) == ""

    def test_empty_dict(self):
        assert _extract_adf_text({}) == ""


# ── Parse issue ──


class TestParseIssue:

    def test_basic_fields(self):
        provider = _make_provider()
        issue = _make_issue_json()
        task = provider._parse_issue(issue)

        assert isinstance(task, ExternalTask)
        assert task.external_id == "PROJ-1"
        assert task.title == "Fix login bug"
        assert task.status == TaskStatus.IN_PROGRESS
        assert task.priority == TaskPriority.HIGH
        assert task.assignee == "Alice"
        assert task.source == "jira"
        assert "PROJ-1" in task.url

    def test_no_assignee(self):
        provider = _make_provider()
        issue = _make_issue_json(assignee_name=None)
        task = provider._parse_issue(issue)
        assert task.assignee == ""

    def test_labels(self):
        provider = _make_provider()
        issue = _make_issue_json(labels=["bug", "urgent"])
        task = provider._parse_issue(issue)
        assert task.labels == ["bug", "urgent"]

    def test_adf_description(self):
        provider = _make_provider()
        adf_desc = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{"type": "text", "text": "Bug description here"}],
            }],
        }
        issue = _make_issue_json(description=adf_desc)
        task = provider._parse_issue(issue)
        assert "Bug description here" in task.description

    def test_timestamps(self):
        provider = _make_provider()
        issue = _make_issue_json()
        task = provider._parse_issue(issue)
        assert task.created_at is not None
        assert task.updated_at is not None


# ── Properties ──


class TestProperties:

    def test_name(self):
        provider = _make_provider()
        assert provider.name == "jira"

    def test_display_name(self):
        provider = _make_provider()
        assert provider.display_name == "Jira Cloud"


# ── Health check ──


class TestHealthCheck:

    @pytest.mark.asyncio
    async def test_health_ok(self):
        provider = _make_provider()
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _mock_response(200)
            assert await provider.check_health() is True
            mock_req.assert_called_once_with("GET", "/rest/api/3/myself")

    @pytest.mark.asyncio
    async def test_health_401(self):
        provider = _make_provider()
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _mock_response(401)
            assert await provider.check_health() is False

    @pytest.mark.asyncio
    async def test_health_network_error(self):
        provider = _make_provider()
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = None
            assert await provider.check_health() is False


# ── List tasks ──


class TestListTasks:

    @pytest.mark.asyncio
    async def test_list_tasks_default(self):
        provider = _make_provider()
        resp = _mock_response(200, {"issues": [_make_issue_json()]})
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = resp
            tasks = await provider.list_tasks()
            assert len(tasks) == 1
            assert tasks[0].external_id == "PROJ-1"

    @pytest.mark.asyncio
    async def test_list_tasks_with_project(self):
        provider = _make_provider()
        resp = _mock_response(200, {"issues": []})
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = resp
            await provider.list_tasks(project="MYPROJ")
            call_kwargs = mock_req.call_args
            params = call_kwargs.kwargs.get("params", {})
            assert 'project = "MYPROJ"' in params["jql"]

    @pytest.mark.asyncio
    async def test_list_tasks_with_status(self):
        provider = _make_provider()
        resp = _mock_response(200, {"issues": []})
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = resp
            await provider.list_tasks(status=TaskStatus.DONE)
            call_kwargs = mock_req.call_args
            params = call_kwargs.kwargs.get("params", {})
            assert "status IN" in params["jql"]

    @pytest.mark.asyncio
    async def test_list_tasks_error(self):
        provider = _make_provider()
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = None
            tasks = await provider.list_tasks()
            assert tasks == []


# ── Get task ──


class TestGetTask:

    @pytest.mark.asyncio
    async def test_get_task_found(self):
        provider = _make_provider()
        resp = _mock_response(200, _make_issue_json(key="PROJ-42"))
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = resp
            task = await provider.get_task("PROJ-42")
            assert task is not None
            assert task.external_id == "PROJ-42"

    @pytest.mark.asyncio
    async def test_get_task_not_found(self):
        provider = _make_provider()
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _mock_response(404)
            task = await provider.get_task("PROJ-999")
            assert task is None


# ── Search tasks ──


class TestSearchTasks:

    @pytest.mark.asyncio
    async def test_search_tasks(self):
        provider = _make_provider()
        resp = _mock_response(200, {"issues": [_make_issue_json()]})
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = resp
            tasks = await provider.search_tasks("login bug")
            assert len(tasks) == 1
            call_kwargs = mock_req.call_args
            params = call_kwargs.kwargs.get("params", {})
            assert "login bug" in params["jql"]

    @pytest.mark.asyncio
    async def test_search_tasks_error(self):
        provider = _make_provider()
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = None
            tasks = await provider.search_tasks("query")
            assert tasks == []


# ── Push session summary ─���


class TestPushSummary:

    @pytest.mark.asyncio
    async def test_push_summary_comment_and_worklog(self):
        provider = _make_provider()
        comment_resp = _mock_response(201)
        worklog_resp = _mock_response(201)

        call_count = 0

        async def mock_request(method, path, **kwargs):
            nonlocal call_count
            call_count += 1
            if "worklog" in path:
                return worklog_resp
            return comment_resp

        with patch.object(provider, "_request", side_effect=mock_request):
            summary = SessionSummary(
                session_id="sess-1",
                task_description="Fix login",
                status="completed",
                branch_name="fix/login",
                files_changed=["auth.py"],
                commits=[{"commit_sha": "abc12345"}],
                duration_seconds=1800,
                tool_calls_count=15,
            )
            result = await provider.push_session_summary("PROJ-1", summary)
            assert result is True
            assert call_count == 2  # comment + worklog

    @pytest.mark.asyncio
    async def test_push_summary_no_duration(self):
        """No worklog posted when duration is 0."""
        provider = _make_provider()
        call_count = 0

        async def mock_request(method, path, **kwargs):
            nonlocal call_count
            call_count += 1
            return _mock_response(201)

        with patch.object(provider, "_request", side_effect=mock_request):
            summary = SessionSummary(
                session_id="sess-1",
                task_description="Quick fix",
                status="completed",
                duration_seconds=0,
            )
            result = await provider.push_session_summary("PROJ-1", summary)
            assert result is True
            assert call_count == 1  # comment only

    @pytest.mark.asyncio
    async def test_push_summary_comment_fails(self):
        provider = _make_provider()
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _mock_response(500)
            summary = SessionSummary(
                session_id="s-1", task_description="test", status="completed",
            )
            result = await provider.push_session_summary("PROJ-1", summary)
            assert result is False


# ── Update task status ──


class TestUpdateStatus:

    @pytest.mark.asyncio
    async def test_update_status_success(self):
        provider = _make_provider()
        transitions_resp = _mock_response(200, {
            "transitions": [
                {"id": "31", "name": "Done", "to": {"name": "Done"}},
                {"id": "21", "name": "In Progress", "to": {"name": "In Progress"}},
            ],
        })
        post_resp = _mock_response(204)

        call_count = 0

        async def mock_request(method, path, **kwargs):
            nonlocal call_count
            call_count += 1
            if method == "GET":
                return transitions_resp
            return post_resp

        with patch.object(provider, "_request", side_effect=mock_request):
            result = await provider.update_task_status("PROJ-1", TaskStatus.DONE)
            assert result is True
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_update_status_no_matching_transition(self):
        provider = _make_provider()
        transitions_resp = _mock_response(200, {
            "transitions": [
                {"id": "21", "name": "Start", "to": {"name": "In Progress"}},
            ],
        })
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = transitions_resp
            result = await provider.update_task_status("PROJ-1", TaskStatus.DONE)
            assert result is False

    @pytest.mark.asyncio
    async def test_update_status_get_transitions_fails(self):
        provider = _make_provider()
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = None
            result = await provider.update_task_status("PROJ-1", TaskStatus.DONE)
            assert result is False


# ── Webhook handling ──


class TestWebhook:

    @pytest.mark.asyncio
    async def test_handle_webhook(self):
        provider = _make_provider()
        event = WebhookEvent(
            "issue_updated",
            "PROJ-1",
            "jira",
            {
                "issue": {"key": "PROJ-1"},
                "changelog": {"items": [{"field": "status", "toString": "Done"}]},
            },
        )
        result = await provider.handle_webhook(event)
        assert result["status"] == "processed"
        assert result["issue_key"] == "PROJ-1"
        assert len(result["changes"]) == 1

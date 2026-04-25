"""Tests for ServiceNowProvider — state/priority mapping, parsing, HTTP operations."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lean_ai.integrations.base import (
    ExternalTask,
    SessionSummary,
    TaskPriority,
    TaskStatus,
    WebhookEvent,
)
from lean_ai.integrations.servicenow import (
    _SYS_ID_RE,
    ServiceNowProvider,
    _map_priority,
    _map_state,
)

# ── Helpers ──


def _make_provider(
    instance_url="https://test.service-now.com",
    username="admin",
    password="pass",
    table="incident",
):
    """Create a ServiceNowProvider instance for testing."""
    return ServiceNowProvider(
        instance_url=instance_url,
        username=username,
        password=password,
        table=table,
    )


def _make_record(
    sys_id="abc12345def67890abc12345def67890",
    number="INC0012345",
    short_description="Server down",
    state="1",
    priority="2",
    assigned_to="Jane Doe",
):
    """Build realistic ServiceNow record JSON."""
    return {
        "sys_id": sys_id,
        "number": number,
        "short_description": short_description,
        "description": "Full description of the incident",
        "state": state,
        "priority": priority,
        "assigned_to": {"display_value": assigned_to} if assigned_to else "",
        "sys_created_on": "2024-01-15 10:30:00",
        "sys_updated_on": "2024-01-16 14:20:00",
    }


def _mock_response(status_code=200, json_data=None):
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


# ── State / priority mapping tests ──


class TestStateMapping:
    def test_new(self):
        assert _map_state("1") == TaskStatus.OPEN

    def test_in_progress(self):
        assert _map_state("2") == TaskStatus.IN_PROGRESS

    def test_on_hold(self):
        assert _map_state("3") == TaskStatus.BLOCKED

    def test_resolved(self):
        assert _map_state("6") == TaskStatus.DONE

    def test_closed(self):
        assert _map_state("7") == TaskStatus.DONE

    def test_unknown_defaults_to_open(self):
        assert _map_state("99") == TaskStatus.OPEN

    def test_string_handling(self):
        assert _map_state(1) == TaskStatus.OPEN
        assert _map_state(" 2 ") == TaskStatus.IN_PROGRESS


class TestPriorityMapping:
    def test_critical(self):
        assert _map_priority("1") == TaskPriority.CRITICAL

    def test_high(self):
        assert _map_priority("2") == TaskPriority.HIGH

    def test_medium(self):
        assert _map_priority("3") == TaskPriority.MEDIUM

    def test_low(self):
        assert _map_priority("4") == TaskPriority.LOW
        assert _map_priority("5") == TaskPriority.LOW

    def test_unknown_defaults_to_medium(self):
        assert _map_priority("99") == TaskPriority.MEDIUM


# ── Sys ID regex ──


class TestSysIdRegex:
    def test_valid_sys_id(self):
        assert _SYS_ID_RE.match("abc12345def67890abc12345def67890")

    def test_invalid_sys_id_short(self):
        assert _SYS_ID_RE.match("abc123") is None

    def test_inc_number(self):
        assert _SYS_ID_RE.match("INC0012345") is None


# ── Parse record ──


class TestParseRecord:
    def test_basic_fields(self):
        provider = _make_provider()
        record = _make_record()
        task = provider._parse_record(record)

        assert isinstance(task, ExternalTask)
        assert task.external_id == "INC0012345"
        assert task.title == "Server down"
        assert task.status == TaskStatus.OPEN
        assert task.priority == TaskPriority.HIGH
        assert task.assignee == "Jane Doe"
        assert task.source == "servicenow"

    def test_no_assignee(self):
        provider = _make_provider()
        record = _make_record(assigned_to=None)
        task = provider._parse_record(record)
        assert task.assignee == ""

    def test_description(self):
        provider = _make_provider()
        record = _make_record()
        task = provider._parse_record(record)
        assert "Full description" in task.description

    def test_sys_id_fallback(self):
        """When number is empty, use sys_id as external_id."""
        provider = _make_provider()
        record = _make_record(number="")
        task = provider._parse_record(record)
        assert task.external_id == "abc12345def67890abc12345def67890"

    def test_display_value_dict(self):
        """Handle {display_value, value} dict fields."""
        provider = _make_provider()
        record = _make_record()
        record["short_description"] = {"display_value": "Dict title", "value": "dict_title"}
        task = provider._parse_record(record)
        assert task.title == "Dict title"


# ── Properties ──


class TestProperties:
    def test_name(self):
        provider = _make_provider()
        assert provider.name == "servicenow"

    def test_display_name(self):
        provider = _make_provider()
        assert provider.display_name == "ServiceNow"


# ── Resolve sys_id ──


class TestResolveSysId:
    @pytest.mark.asyncio
    async def test_already_sys_id(self):
        provider = _make_provider()
        result = await provider._resolve_sys_id("abc12345def67890abc12345def67890")
        assert result == "abc12345def67890abc12345def67890"

    @pytest.mark.asyncio
    async def test_resolve_by_number(self):
        provider = _make_provider()
        resp = _mock_response(
            200,
            {
                "result": [{"sys_id": "abc12345def67890abc12345def67890"}],
            },
        )
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = resp
            result = await provider._resolve_sys_id("INC0012345")
            assert result == "abc12345def67890abc12345def67890"

    @pytest.mark.asyncio
    async def test_resolve_not_found(self):
        provider = _make_provider()
        resp = _mock_response(200, {"result": []})
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = resp
            result = await provider._resolve_sys_id("INC9999999")
            assert result is None

    @pytest.mark.asyncio
    async def test_resolve_request_fails(self):
        provider = _make_provider()
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = None
            result = await provider._resolve_sys_id("INC0012345")
            assert result is None


# ── Health check ──


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_ok(self):
        provider = _make_provider()
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _mock_response(200)
            assert await provider.check_health() is True

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
        resp = _mock_response(200, {"result": [_make_record()]})
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = resp
            tasks = await provider.list_tasks()
            assert len(tasks) == 1
            assert tasks[0].external_id == "INC0012345"

    @pytest.mark.asyncio
    async def test_list_tasks_with_status(self):
        provider = _make_provider()
        resp = _mock_response(200, {"result": []})
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = resp
            await provider.list_tasks(status=TaskStatus.IN_PROGRESS)
            call_kwargs = mock_req.call_args
            params = call_kwargs.kwargs.get("params", {})
            assert "state=2" in params["sysparm_query"]

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
    async def test_get_task_by_number(self):
        provider = _make_provider()
        resolve_resp = _mock_response(
            200,
            {
                "result": [{"sys_id": "abc12345def67890abc12345def67890"}],
            },
        )
        record_resp = _mock_response(200, {"result": _make_record()})

        call_count = 0

        async def mock_request(method, path, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return resolve_resp
            return record_resp

        with patch.object(provider, "_request", side_effect=mock_request):
            task = await provider.get_task("INC0012345")
            assert task is not None
            assert task.external_id == "INC0012345"

    @pytest.mark.asyncio
    async def test_get_task_not_found(self):
        provider = _make_provider()
        resolve_resp = _mock_response(200, {"result": []})
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = resolve_resp
            task = await provider.get_task("INC9999999")
            assert task is None

    @pytest.mark.asyncio
    async def test_get_task_by_sys_id(self):
        """sys_id skips the resolve step."""
        provider = _make_provider()
        resp = _mock_response(200, {"result": _make_record()})
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = resp
            task = await provider.get_task("abc12345def67890abc12345def67890")
            assert task is not None
            # Only 1 call (no resolve step)
            assert mock_req.call_count == 1


# ── Search tasks ──


class TestSearchTasks:
    @pytest.mark.asyncio
    async def test_search_tasks(self):
        provider = _make_provider()
        resp = _mock_response(200, {"result": [_make_record()]})
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = resp
            tasks = await provider.search_tasks("server down")
            assert len(tasks) == 1
            call_kwargs = mock_req.call_args
            params = call_kwargs.kwargs.get("params", {})
            assert "server down" in params["sysparm_query"]

    @pytest.mark.asyncio
    async def test_search_tasks_error(self):
        provider = _make_provider()
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = None
            tasks = await provider.search_tasks("query")
            assert tasks == []


# ── Push session summary ──


class TestPushSummary:
    @pytest.mark.asyncio
    async def test_push_summary_success(self):
        provider = _make_provider()
        # First call: resolve sys_id (since INC number), second: PUT work_notes
        resolve_resp = _mock_response(
            200,
            {
                "result": [{"sys_id": "abc12345def67890abc12345def67890"}],
            },
        )
        put_resp = _mock_response(200)

        call_count = 0

        async def mock_request(method, path, **kwargs):
            nonlocal call_count
            call_count += 1
            if method == "GET":
                return resolve_resp
            return put_resp

        with patch.object(provider, "_request", side_effect=mock_request):
            summary = SessionSummary(
                session_id="sess-1",
                task_description="Fix server issue",
                status="completed",
                branch_name="fix/server",
                files_changed=["server.py"],
                commits=[{"commit_sha": "abc12345"}],
                duration_seconds=3600,
                tool_calls_count=20,
            )
            result = await provider.push_session_summary("INC0012345", summary)
            assert result is True

    @pytest.mark.asyncio
    async def test_push_summary_resolve_fails(self):
        provider = _make_provider()
        with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _mock_response(200, {"result": []})
            summary = SessionSummary(
                session_id="s-1",
                task_description="test",
                status="completed",
            )
            result = await provider.push_session_summary("INC9999999", summary)
            assert result is False

    @pytest.mark.asyncio
    async def test_push_summary_put_fails(self):
        provider = _make_provider()
        resolve_resp = _mock_response(
            200,
            {
                "result": [{"sys_id": "abc12345def67890abc12345def67890"}],
            },
        )
        put_resp = _mock_response(500)

        call_count = 0

        async def mock_request(method, path, **kwargs):
            nonlocal call_count
            call_count += 1
            if method == "GET":
                return resolve_resp
            return put_resp

        with patch.object(provider, "_request", side_effect=mock_request):
            summary = SessionSummary(
                session_id="s-1",
                task_description="test",
                status="completed",
            )
            result = await provider.push_session_summary("INC0012345", summary)
            assert result is False


# ── Update task status ──


class TestUpdateStatus:
    @pytest.mark.asyncio
    async def test_update_status_success(self):
        provider = _make_provider()
        put_resp = _mock_response(200)

        with patch.object(provider, "_resolve_sys_id", new_callable=AsyncMock) as mock_resolve:
            mock_resolve.return_value = "abc12345def67890abc12345def67890"
            with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
                mock_req.return_value = put_resp
                result = await provider.update_task_status("INC0012345", TaskStatus.DONE)
                assert result is True
                # Check that state=6 was sent
                call_kwargs = mock_req.call_args
                assert call_kwargs.kwargs["json"]["state"] == "6"

    @pytest.mark.asyncio
    async def test_update_status_resolve_fails(self):
        provider = _make_provider()
        with patch.object(provider, "_resolve_sys_id", new_callable=AsyncMock) as mock_resolve:
            mock_resolve.return_value = None
            result = await provider.update_task_status("INC9999999", TaskStatus.DONE)
            assert result is False

    @pytest.mark.asyncio
    async def test_update_status_all_states(self):
        """Verify all status→state mappings."""
        provider = _make_provider()
        expected = {
            TaskStatus.OPEN: "1",
            TaskStatus.IN_PROGRESS: "2",
            TaskStatus.BLOCKED: "3",
            TaskStatus.DONE: "6",
        }
        for task_status, state_val in expected.items():
            with patch.object(provider, "_resolve_sys_id", new_callable=AsyncMock) as mock_resolve:
                mock_resolve.return_value = "abc12345def67890abc12345def67890"
                with patch.object(provider, "_request", new_callable=AsyncMock) as mock_req:
                    mock_req.return_value = _mock_response(200)
                    await provider.update_task_status("INC001", task_status)
                    call_kwargs = mock_req.call_args
                    assert call_kwargs.kwargs["json"]["state"] == state_val


# ── Webhook handling ──


class TestWebhook:
    @pytest.mark.asyncio
    async def test_handle_webhook(self):
        provider = _make_provider()
        event = WebhookEvent(
            "record_updated",
            "INC0012345",
            "servicenow",
            {"table": "incident", "changes": {"state": "6"}},
        )
        result = await provider.handle_webhook(event)
        assert result["status"] == "processed"
        assert result["record_id"] == "INC0012345"
        assert result["table"] == "incident"

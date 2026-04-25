"""Tests for the integration framework — registry, DB, summary builder."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lean_ai.integrations.base import (
    ExternalTask,
    IntegrationProvider,
    SessionSummary,
    TaskPriority,
    TaskStatus,
    WebhookEvent,
)
from lean_ai.integrations.registry import (
    _ACTIVE,
    _REGISTRY,
    deactivate_integration,
    get_integration,
    init_integration,
    list_active_integrations,
    list_integrations,
    register_integration,
    shutdown_integrations,
)

# ── Fake integration for testing ──


class FakeIntegration(IntegrationProvider):
    INTEGRATION_NAME = "fake"
    DISPLAY_NAME = "Fake Service"

    def __init__(self, healthy=True):
        self._healthy = healthy

    @property
    def name(self) -> str:
        return "fake"

    @property
    def display_name(self) -> str:
        return "Fake Service"

    async def check_health(self) -> bool:
        return self._healthy

    async def list_tasks(self, project=None, status=None, assignee=None, limit=50):
        return [
            ExternalTask(
                external_id="FAKE-1",
                title="Test task",
                status=TaskStatus.OPEN,
                priority=TaskPriority.MEDIUM,
            ),
        ]

    async def get_task(self, external_id):
        if external_id == "FAKE-1":
            return ExternalTask(external_id="FAKE-1", title="Test task")
        return None

    async def push_session_summary(self, external_id, summary):
        return True

    async def update_task_status(self, external_id, status):
        return True

    async def search_tasks(self, query, limit=20):
        return [
            ExternalTask(external_id="FAKE-2", title=f"Search: {query}"),
        ]

    async def handle_webhook(self, event):
        return {"status": "processed", "event_type": event.event_type}


# ── Registry tests ──


class TestRegistry:
    def setup_method(self):
        """Clear registry state before each test."""
        _REGISTRY.clear()
        _ACTIVE.clear()

    def test_register_integration(self):
        register_integration(FakeIntegration)
        assert "fake" in _REGISTRY
        assert _REGISTRY["fake"] is FakeIntegration

    def test_register_without_name_raises(self):
        class BadIntegration(FakeIntegration):
            INTEGRATION_NAME = None

        with pytest.raises(ValueError, match="INTEGRATION_NAME"):
            register_integration(BadIntegration)

    def test_list_integrations_empty(self):
        assert list_integrations() == []

    def test_list_integrations_registered(self):
        register_integration(FakeIntegration)
        result = list_integrations()
        assert len(result) == 1
        assert result[0]["name"] == "fake"
        assert result[0]["display_name"] == "Fake Service"
        assert result[0]["active"] is False

    def test_get_integration_not_active(self):
        assert get_integration("fake") is None

    @pytest.mark.asyncio
    async def test_init_integration_success(self):
        register_integration(FakeIntegration)
        provider = await init_integration("fake", healthy=True)
        assert provider is not None
        assert provider.name == "fake"
        assert get_integration("fake") is provider

    @pytest.mark.asyncio
    async def test_init_integration_health_fail(self):
        register_integration(FakeIntegration)
        provider = await init_integration("fake", healthy=False)
        assert provider is None
        assert get_integration("fake") is None

    @pytest.mark.asyncio
    async def test_init_unknown_integration(self):
        result = await init_integration("nonexistent")
        assert result is None

    def test_list_active_integrations(self):
        instance = FakeIntegration()
        _ACTIVE["fake"] = instance
        actives = list_active_integrations()
        assert len(actives) == 1
        assert actives[0] is instance

    def test_deactivate_integration(self):
        _ACTIVE["fake"] = FakeIntegration()
        assert deactivate_integration("fake") is True
        assert get_integration("fake") is None
        assert deactivate_integration("fake") is False

    @pytest.mark.asyncio
    async def test_shutdown_integrations(self):
        _ACTIVE["fake"] = FakeIntegration()
        await shutdown_integrations()
        assert len(_ACTIVE) == 0

    def test_list_integrations_shows_active(self):
        register_integration(FakeIntegration)
        _ACTIVE["fake"] = FakeIntegration()
        result = list_integrations()
        assert result[0]["active"] is True


# ── ABC contract tests ──


class TestABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            IntegrationProvider()

    def test_webhook_default(self):
        """Default handle_webhook returns not_implemented."""
        provider = FakeIntegration()
        import asyncio

        result = asyncio.run(
            IntegrationProvider.handle_webhook(provider, WebhookEvent("test", "1", "src"))
        )
        assert result == {"status": "not_implemented"}

    def test_search_default(self):
        """Default search_tasks returns empty list."""
        import asyncio

        result = asyncio.run(IntegrationProvider.search_tasks(MagicMock(), "query"))
        assert result == []


# ── Data class tests ──


class TestDataClasses:
    def test_external_task_defaults(self):
        task = ExternalTask(external_id="T-1", title="Test")
        assert task.status == TaskStatus.OPEN
        assert task.priority == TaskPriority.MEDIUM
        assert task.labels == []
        assert task.raw_data == {}

    def test_session_summary(self):
        summary = SessionSummary(
            session_id="sess-1",
            task_description="Fix bug",
            status="completed",
            files_changed=["a.py"],
            duration_seconds=120.5,
            tool_calls_count=10,
        )
        assert summary.session_id == "sess-1"
        assert summary.files_changed == ["a.py"]
        assert summary.duration_seconds == 120.5

    def test_webhook_event(self):
        event = WebhookEvent("updated", "TASK-1", "jira", {"key": "value"})
        assert event.event_type == "updated"
        assert event.payload == {"key": "value"}

    def test_task_status_values(self):
        assert TaskStatus.OPEN.value == "open"
        assert TaskStatus.IN_PROGRESS.value == "in_progress"
        assert TaskStatus.DONE.value == "done"
        assert TaskStatus.BLOCKED.value == "blocked"


# ── Database tests ──


class TestDatabase:
    @pytest.fixture
    async def db(self, tmp_path):
        """Create an in-memory-like temp database."""
        import aiosqlite

        from lean_ai.integrations.db import _SCHEMA

        db_path = tmp_path / "test_integrations.db"
        db = await aiosqlite.connect(str(db_path))
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        await db.executescript(_SCHEMA)
        yield db
        await db.close()

    @pytest.mark.asyncio
    async def test_link_task(self, db):
        from lean_ai.integrations.db import link_task

        result = await link_task(
            db,
            "jira",
            "PROJ-123",
            session_id="sess-1",
            workspace="/home/user/project",
            title="Fix login bug",
            status="open",
        )
        assert result["integration_name"] == "jira"
        assert result["external_id"] == "PROJ-123"
        assert result["session_id"] == "sess-1"
        assert result["title"] == "Fix login bug"
        assert "id" in result
        assert "created_at" in result

    @pytest.mark.asyncio
    async def test_unlink_task(self, db):
        from lean_ai.integrations.db import link_task, unlink_task

        result = await link_task(db, "jira", "PROJ-123")
        link_id = result["id"]
        assert await unlink_task(db, link_id) is True
        assert await unlink_task(db, link_id) is False

    @pytest.mark.asyncio
    async def test_get_linked_tasks_all(self, db):
        from lean_ai.integrations.db import get_linked_tasks, link_task

        await link_task(db, "jira", "J-1", workspace="/proj")
        await link_task(db, "servicenow", "SN-1", workspace="/proj")

        tasks = await get_linked_tasks(db)
        assert len(tasks) == 2

    @pytest.mark.asyncio
    async def test_get_linked_tasks_filtered(self, db):
        from lean_ai.integrations.db import get_linked_tasks, link_task

        await link_task(db, "jira", "J-1", workspace="/proj")
        await link_task(db, "servicenow", "SN-1", workspace="/other")

        tasks = await get_linked_tasks(db, workspace="/proj")
        assert len(tasks) == 1
        assert tasks[0]["integration_name"] == "jira"

    @pytest.mark.asyncio
    async def test_get_linked_tasks_by_integration(self, db):
        from lean_ai.integrations.db import get_linked_tasks, link_task

        await link_task(db, "jira", "J-1")
        await link_task(db, "jira", "J-2")
        await link_task(db, "servicenow", "SN-1")

        tasks = await get_linked_tasks(db, integration_name="jira")
        assert len(tasks) == 2

    @pytest.mark.asyncio
    async def test_update_task_link(self, db):
        from lean_ai.integrations.db import link_task, update_task_link

        result = await link_task(db, "jira", "J-1", status="open")
        link_id = result["id"]

        updated = await update_task_link(db, link_id, status="done")
        assert updated is not None
        assert updated["status"] == "done"

    @pytest.mark.asyncio
    async def test_update_task_link_not_found(self, db):
        from lean_ai.integrations.db import update_task_link

        result = await update_task_link(db, "nonexistent", status="done")
        assert result is None

    @pytest.mark.asyncio
    async def test_log_sync_event(self, db):
        from lean_ai.integrations.db import link_task, log_sync_event

        result = await link_task(db, "jira", "J-1")
        link_id = result["id"]

        log_id = await log_sync_event(
            db,
            link_id,
            "push",
            "session_summary",
            payload={"key": "value"},
            success=True,
        )
        assert log_id > 0

    @pytest.mark.asyncio
    async def test_save_and_get_integration_config(self, db):
        from lean_ai.integrations.db import get_integration_config, save_integration_config

        await save_integration_config(
            db,
            "jira",
            config={"url": "https://jira.example.com", "token": "xxx"},
            enabled=True,
        )
        result = await get_integration_config(db, "jira")
        assert result is not None
        assert result["config"]["url"] == "https://jira.example.com"
        assert result["enabled"] is True

    @pytest.mark.asyncio
    async def test_get_integration_config_not_found(self, db):
        from lean_ai.integrations.db import get_integration_config

        result = await get_integration_config(db, "nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_link_task_upsert(self, db):
        """Linking same integration+external_id replaces existing link."""
        from lean_ai.integrations.db import get_linked_tasks, link_task

        await link_task(db, "jira", "J-1", title="First")
        await link_task(db, "jira", "J-1", title="Second")

        tasks = await get_linked_tasks(db)
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Second"


# ── Summary builder tests ──


class TestSummaryBuilder:
    @pytest.mark.asyncio
    async def test_build_session_summary(self):
        """Test summary builder with mocked DB."""
        from lean_ai.integrations.summary import build_session_summary

        mock_db = AsyncMock()

        # Use real dicts wrapped to support both dict() and [] access like aiosqlite.Row
        class FakeRow(dict):
            """Dict that also supports integer-index access like aiosqlite.Row."""

            def __getitem__(self, key):
                if isinstance(key, int):
                    return list(self.values())[key]
                return dict.__getitem__(self, key)

        session_data = FakeRow(
            {
                "task": "Fix the login bug",
                "status": "completed",
                "branch_name": "fix/login",
                "created_at": "2024-01-01T10:00:00",
                "completed_at": "2024-01-01T10:30:00",
            }
        )

        commit_data = FakeRow({"commit_sha": "abc123", "message": "fix: login"})
        tool_count_data = FakeRow({"count": 15})
        file_data = FakeRow({"parameters": '{"path": "src/auth.py"}'})

        # Set up cursor chain
        cursor_session = AsyncMock()
        cursor_session.fetchone.return_value = session_data

        cursor_commits = AsyncMock()
        cursor_commits.fetchall.return_value = [commit_data]

        cursor_tool_count = AsyncMock()
        cursor_tool_count.fetchone.return_value = tool_count_data

        cursor_files = AsyncMock()
        cursor_files.fetchall.return_value = [file_data]

        mock_db.execute = AsyncMock(
            side_effect=[
                cursor_session,
                cursor_commits,
                cursor_tool_count,
                cursor_files,
            ]
        )
        mock_db.close = AsyncMock()

        with patch("lean_ai.db.get_db", return_value=mock_db):
            summary = await build_session_summary("/repo", "sess-1")

        assert summary is not None
        assert summary.session_id == "sess-1"
        assert summary.task_description == "Fix the login bug"
        assert summary.status == "completed"
        assert summary.branch_name == "fix/login"
        assert summary.duration_seconds == 1800.0  # 30 minutes
        assert summary.tool_calls_count == 15
        assert "src/auth.py" in summary.files_changed
        assert len(summary.commits) == 1

    @pytest.mark.asyncio
    async def test_build_session_summary_not_found(self):
        """Returns None when session doesn't exist."""
        from lean_ai.integrations.summary import build_session_summary

        mock_db = AsyncMock()
        cursor = AsyncMock()
        cursor.fetchone.return_value = None
        mock_db.execute = AsyncMock(return_value=cursor)
        mock_db.close = AsyncMock()

        with patch("lean_ai.db.get_db", return_value=mock_db):
            summary = await build_session_summary("/repo", "nonexistent")

        assert summary is None


# ── FakeIntegration behavior tests ──


class TestFakeIntegrationBehavior:
    @pytest.mark.asyncio
    async def test_list_tasks(self):
        provider = FakeIntegration()
        tasks = await provider.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].external_id == "FAKE-1"
        assert tasks[0].status == TaskStatus.OPEN

    @pytest.mark.asyncio
    async def test_get_task_found(self):
        provider = FakeIntegration()
        task = await provider.get_task("FAKE-1")
        assert task is not None
        assert task.title == "Test task"

    @pytest.mark.asyncio
    async def test_get_task_not_found(self):
        provider = FakeIntegration()
        task = await provider.get_task("NONEXISTENT")
        assert task is None

    @pytest.mark.asyncio
    async def test_push_session_summary(self):
        provider = FakeIntegration()
        summary = SessionSummary(
            session_id="s-1",
            task_description="test",
            status="completed",
        )
        assert await provider.push_session_summary("FAKE-1", summary) is True

    @pytest.mark.asyncio
    async def test_search_tasks(self):
        provider = FakeIntegration()
        results = await provider.search_tasks("login bug")
        assert len(results) == 1
        assert "login bug" in results[0].title

    @pytest.mark.asyncio
    async def test_handle_webhook(self):
        provider = FakeIntegration()
        event = WebhookEvent("task_updated", "FAKE-1", "fake", {"new_status": "done"})
        result = await provider.handle_webhook(event)
        assert result["status"] == "processed"

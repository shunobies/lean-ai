"""Tests for chat-mode session history tools."""

import pytest

from lean_ai.db import get_db
from lean_ai.memory.session_tools import (
    get_recent_activity_context,
    get_session_summary,
    list_recent_sessions,
    search_session_history,
    search_workspace_memories,
)


@pytest.fixture
async def db(tmp_path):
    """Open a temporary workspace database and seed session data."""
    conn = await get_db(str(tmp_path))

    # Seed two sessions (DB column is 'id', not 'session_id')
    await conn.execute(
        "INSERT INTO sessions (id, repo_root, task, status, "
        "branch_name, created_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "sess_001", str(tmp_path), "Add login feature",
            "completed", "feat/login",
            "2025-06-01T10:00:00", "2025-06-01T11:00:00",
        ),
    )
    await conn.execute(
        "INSERT INTO sessions (id, repo_root, task, status, "
        "branch_name, created_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "sess_002", str(tmp_path), "Fix database migration",
            "failed", "fix/db-migration",
            "2025-06-02T09:00:00", "2025-06-02T09:30:00",
        ),
    )

    # Seed tool logs for sess_001
    await conn.execute(
        "INSERT INTO tool_logs (session_id, tool_name, parameters, success, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("sess_001", "edit_file", '{"path": "src/auth.py"}', 1, "2025-06-01T10:10:00"),
    )
    await conn.execute(
        "INSERT INTO tool_logs (session_id, tool_name, parameters, success, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("sess_001", "create_file", '{"path": "src/login.py"}', 1, "2025-06-01T10:15:00"),
    )
    await conn.execute(
        "INSERT INTO tool_logs (session_id, tool_name, parameters, success, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("sess_001", "run_tests", '{}', 0, "2025-06-01T10:20:00"),
    )

    await conn.commit()
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_list_recent_sessions(db, tmp_path):
    result = await list_recent_sessions(str(tmp_path), limit=5)
    assert "sess_001" in result
    assert "sess_002" in result
    assert "Add login feature" in result
    assert "Fix database migration" in result


@pytest.mark.asyncio
async def test_list_recent_sessions_with_limit(db, tmp_path):
    result = await list_recent_sessions(str(tmp_path), limit=1)
    # Should show only one session (most recent first)
    assert "sess_002" in result
    # sess_001 may or may not be in the header count but shouldn't be listed
    lines = result.strip().split("\n")
    session_lines = [ln for ln in lines if ln.startswith("  [")]
    assert len(session_lines) == 1


@pytest.mark.asyncio
async def test_list_recent_sessions_empty(tmp_path):
    result = await list_recent_sessions(str(tmp_path))
    assert "No previous sessions" in result


@pytest.mark.asyncio
async def test_get_session_summary_metadata(db, tmp_path):
    result = await get_session_summary(str(tmp_path), "sess_001")
    assert "sess_001" in result
    assert "Add login feature" in result
    assert "completed" in result
    assert "feat/login" in result
    # Tool stats
    assert "edit_file" in result
    assert "create_file" in result
    # Files modified
    assert "src/auth.py" in result
    assert "src/login.py" in result


@pytest.mark.asyncio
async def test_get_session_summary_not_found(db, tmp_path):
    result = await get_session_summary(str(tmp_path), "nonexistent")
    assert "not found" in result


@pytest.mark.asyncio
async def test_search_session_history(db, tmp_path):
    result = await search_session_history(str(tmp_path), "login")
    assert "sess_001" in result
    assert "Add login feature" in result


@pytest.mark.asyncio
async def test_search_session_history_no_results(db, tmp_path):
    result = await search_session_history(str(tmp_path), "zzz_nonexistent")
    assert "No sessions found" in result


def test_search_workspace_memories_empty(tmp_path):
    result = search_workspace_memories(str(tmp_path), "testing")
    assert "No memories found" in result


@pytest.mark.asyncio
async def test_get_recent_activity_context(db, tmp_path):
    result = await get_recent_activity_context(str(tmp_path), "login feature")
    # Should include at least one session line
    assert "login" in result.lower() or "migration" in result.lower()


@pytest.mark.asyncio
async def test_get_recent_activity_context_empty(tmp_path):
    result = await get_recent_activity_context(str(tmp_path), "anything")
    assert result == ""


@pytest.mark.asyncio
async def test_get_recent_activity_context_budget_cap(db, tmp_path):
    result = await get_recent_activity_context(str(tmp_path), "test")
    # Should be within the 800 char budget
    assert len(result) <= 800

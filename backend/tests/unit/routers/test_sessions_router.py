from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from lean_ai.main import app
from lean_ai.workflow.state import StateManager, WorkflowState


def _create_session(client: TestClient, repo_root: str) -> str:
    resp = client.post(
        "/api/sessions",
        json={"repo_root": repo_root, "task": "checkpoint test"},
    )
    assert resp.status_code == 200
    return resp.json()["session_id"]


def test_list_checkpoints_returns_normalized_history_shape(tmp_path):
    client = TestClient(app)
    session_id = _create_session(client, str(tmp_path))

    manager = StateManager(session_id)
    state = WorkflowState.from_scratch(session_id)
    manager._state = state
    manager.save()
    manager.save_checkpoint(
        state=state,
        phase="Phase 3: Design",
        summary="Captured design checkpoint",
    )

    resp = client.get(
        f"/api/sessions/{session_id}/checkpoints",
        params={"repo_root": str(tmp_path)},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["label"] == "Phase 3: Design"
    assert data[0]["phase"] == "Phase 3: Design"
    assert data[0]["summary"] == "Captured design checkpoint"
    assert data[0]["created_at"]
    assert data[0]["status"] == "active"
    assert data[0]["is_head"] is True


def test_restore_checkpoint_sets_restored_checkpoint_metadata(tmp_path):
    client = TestClient(app)
    session_id = _create_session(client, str(tmp_path))

    manager = StateManager(session_id)
    original = WorkflowState.from_scratch(session_id)
    original.add_journal_entry("before restore")
    manager._state = original
    manager.save()
    checkpoint_id = manager.save_checkpoint(
        state=original,
        phase="Phase 2: Explore",
        summary="Checkpoint before mutation",
    )

    mutated = WorkflowState.from_scratch(session_id)
    mutated.add_journal_entry("after restore")
    manager._state = mutated
    manager.save()

    resp = client.post(
        f"/api/sessions/{session_id}/restore",
        json={"checkpoint_id": checkpoint_id, "repo_root": str(tmp_path)},
    )
    assert resp.status_code == 200
    assert resp.json()["checkpoint_id"] == checkpoint_id

    restored = StateManager(session_id).load()
    assert restored.journal_entries == ["before restore"]
    assert restored.session_metadata["restored_checkpoint_id"] == checkpoint_id


def test_resume_session_awaits_async_state_load(tmp_path):
    client = TestClient(app)
    session_id = _create_session(client, str(tmp_path))
    state = WorkflowState.from_scratch(session_id)
    state.scratchpad_content = "cached work"

    manager = MagicMock()
    manager.get_state.side_effect = RuntimeError("sync state load should not be used")
    manager.get_state_async = AsyncMock(return_value=state)

    with patch("lean_ai.routers.sessions.StateManager", return_value=manager):
        resp = client.post(
            f"/api/sessions/{session_id}/resume",
            json={"repo_root": str(tmp_path)},
        )

    assert resp.status_code == 200
    assert resp.json()["scratchpad_exists"] is True
    manager.get_state_async.assert_awaited_once()
    manager.get_state.assert_not_called()

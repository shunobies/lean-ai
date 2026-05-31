"""Unit tests for branching detection hook in pipeline.

Verifies that _detect_branching_and_request_feedback correctly identifies
checkpoint divergence when a user restores from a checkpoint and the
workflow continues on a different path. Tests cover:

  1. No feedback when no checkpoint was restored (restored_checkpoint_id is None)
  2. No feedback when parent_id matches restored checkpoint (linear continuation)
  3. Feedback triggered when parent_id differs from restored checkpoint (divergence)
  4. insert_feedback called with correct trace_span_uuid and branching metadata
  5. Feedback persisted in session_feedback table with correct linkage
  6. Exception handling — DB errors are caught and logged, not propagated
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lean_ai.training.db import get_training_db, insert_feedback
from lean_ai.workflow.pipeline import _detect_branching_and_request_feedback
from lean_ai.workflow.state import WorkflowState


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
async def train_db(tmp_path):
    """Training DB connection scoped to a temp directory."""
    db = await get_training_db(str(tmp_path))
    yield db
    await db.close()


def _make_state_manager(
    session_id: str = "test-session",
    restored_checkpoint_id: str | None = None,
) -> MagicMock:
    """Build a mock StateManager with configurable restored_checkpoint_id.

    The mock exposes get_state() returning a WorkflowState whose
    session_metadata contains the restored_checkpoint_id key.
    """
    state = WorkflowState.from_scratch(session_id)
    if restored_checkpoint_id is not None:
        state.session_metadata["restored_checkpoint_id"] = restored_checkpoint_id

    manager = MagicMock()
    manager.session_id = session_id
    manager.get_state.return_value = state
    return manager


# ── TestBranchingDetection ──────────────────────────────────────────────


class TestBranchingDetection:
    """Tests for _detect_branching_and_request_feedback branching detection."""

    async def test_no_feedback_when_no_checkpoint_restored(self, tmp_path):
        """When restored_checkpoint_id is None, no feedback is inserted."""
        manager = _make_state_manager(restored_checkpoint_id=None)

        with patch(
            "lean_ai.training.db.get_training_db", new_callable=AsyncMock
        ) as mock_get_db:
            await _detect_branching_and_request_feedback(
                state_manager=manager,
                new_checkpoint_id="new-cp-1",
                new_parent_id="some-parent",
                session_span_uuid="span-uuid-1",
                repo_root=str(tmp_path),
            )

        # get_training_db should never be called — early return
        mock_get_db.assert_not_called()

    async def test_no_feedback_when_parent_matches_restored(self, tmp_path):
        """When new_parent_id equals restored_checkpoint_id, no divergence — no feedback."""
        restored_id = "restored-cp-abc"
        manager = _make_state_manager(restored_checkpoint_id=restored_id)

        with patch(
            "lean_ai.training.db.get_training_db", new_callable=AsyncMock
        ) as mock_get_db:
            await _detect_branching_and_request_feedback(
                state_manager=manager,
                new_checkpoint_id="new-cp-2",
                new_parent_id=restored_id,
                session_span_uuid="span-uuid-2",
                repo_root=str(tmp_path),
            )

        # No divergence detected, so no DB call
        mock_get_db.assert_not_called()

    async def test_feedback_triggered_on_parent_id_mismatch(self, tmp_path):
        """When new_parent_id differs from restored_checkpoint_id, feedback is inserted."""
        restored_id = "restored-cp-abc"
        divergent_parent = "different-parent-xyz"
        manager = _make_state_manager(restored_checkpoint_id=restored_id)

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.close = AsyncMock()

        with patch(
            "lean_ai.training.db.get_training_db", new_callable=AsyncMock
        ) as mock_get_db:
            mock_get_db.return_value = mock_db
            await _detect_branching_and_request_feedback(
                state_manager=manager,
                new_checkpoint_id="new-cp-3",
                new_parent_id=divergent_parent,
                session_span_uuid="span-uuid-3",
                repo_root=str(tmp_path),
            )

        # get_training_db was called to open the training DB
        mock_get_db.assert_called_once_with(str(tmp_path))
        # insert_feedback was called (via db.execute)
        assert mock_db.execute.called

    async def test_insert_feedback_called_with_correct_trace_span_uuid(
        self, tmp_path
    ):
        """Verify insert_feedback receives the session_span_uuid as trace_span_uuid."""
        restored_id = "restored-cp-abc"
        manager = _make_state_manager(restored_checkpoint_id=restored_id)
        span_uuid = "trace-span-uuid-42"

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.close = AsyncMock()

        with patch(
            "lean_ai.training.db.get_training_db", new_callable=AsyncMock
        ) as mock_get_db:
            mock_get_db.return_value = mock_db
            await _detect_branching_and_request_feedback(
                state_manager=manager,
                new_checkpoint_id="new-cp-4",
                new_parent_id="divergent-parent",
                session_span_uuid=span_uuid,
                repo_root=str(tmp_path),
            )

        # The first positional argument to execute is the SQL, the rest are params.
        # insert_feedback calls db.execute with:
        #   (session_id, trace_span_uuid, thumbs_up, rating, comment, tags, created_at)
        call_args = mock_db.execute.call_args
        params = call_args[0][1]  # Second element of args tuple is the params tuple
        assert params[1] == span_uuid, "trace_span_uuid should be second param"

    async def test_insert_feedback_called_with_correct_session_id(self, tmp_path):
        """Verify insert_feedback receives the correct session_id."""
        restored_id = "restored-cp-abc"
        session_id = "my-test-session"
        manager = _make_state_manager(session_id=session_id, restored_checkpoint_id=restored_id)

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.close = AsyncMock()

        with patch(
            "lean_ai.training.db.get_training_db", new_callable=AsyncMock
        ) as mock_get_db:
            mock_get_db.return_value = mock_db
            await _detect_branching_and_request_feedback(
                state_manager=manager,
                new_checkpoint_id="new-cp-5",
                new_parent_id="divergent-parent",
                session_span_uuid="span-uuid-5",
                repo_root=str(tmp_path),
            )

        call_args = mock_db.execute.call_args
        params = call_args[0][1]
        assert params[0] == session_id, "session_id should be first param"

    async def test_insert_feedback_called_with_thumbs_down(self, tmp_path):
        """Verify insert_feedback is called with thumbs_up=False (stored as 0)."""
        restored_id = "restored-cp-abc"
        manager = _make_state_manager(restored_checkpoint_id=restored_id)

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.close = AsyncMock()

        with patch(
            "lean_ai.training.db.get_training_db", new_callable=AsyncMock
        ) as mock_get_db:
            mock_get_db.return_value = mock_db
            await _detect_branching_and_request_feedback(
                state_manager=manager,
                new_checkpoint_id="new-cp-6",
                new_parent_id="divergent-parent",
                session_span_uuid="span-uuid-6",
                repo_root=str(tmp_path),
            )

        call_args = mock_db.execute.call_args
        params = call_args[0][1]
        # thumbs_up is the third positional param (index 2)
        assert params[2] == 0, "thumbs_up should be 0 (False)"

    async def test_insert_feedback_contains_branching_comment(self, tmp_path):
        """Verify the feedback comment describes the branching event."""
        restored_id = "restored-cp-abc"
        divergent_parent = "divergent-parent-xyz"
        new_cp = "new-cp-7"
        manager = _make_state_manager(restored_checkpoint_id=restored_id)

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.close = AsyncMock()

        with patch(
            "lean_ai.training.db.get_training_db", new_callable=AsyncMock
        ) as mock_get_db:
            mock_get_db.return_value = mock_db
            await _detect_branching_and_request_feedback(
                state_manager=manager,
                new_checkpoint_id=new_cp,
                new_parent_id=divergent_parent,
                session_span_uuid="span-uuid-7",
                repo_root=str(tmp_path),
            )

        call_args = mock_db.execute.call_args
        params = call_args[0][1]
        comment = params[4]  # comment is the fifth positional param
        assert "Checkpoint branching detected" in comment
        assert restored_id in comment
        assert divergent_parent in comment
        assert new_cp in comment

    async def test_insert_feedback_contains_branching_tags(self, tmp_path):
        """Verify feedback includes branching-detected and checkpoint-divergence tags."""
        restored_id = "restored-cp-abc"
        manager = _make_state_manager(restored_checkpoint_id=restored_id)

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.close = AsyncMock()

        with patch(
            "lean_ai.training.db.get_training_db", new_callable=AsyncMock
        ) as mock_get_db:
            mock_get_db.return_value = mock_db
            await _detect_branching_and_request_feedback(
                state_manager=manager,
                new_checkpoint_id="new-cp-8",
                new_parent_id="divergent-parent",
                session_span_uuid="span-uuid-8",
                repo_root=str(tmp_path),
            )

        import json

        call_args = mock_db.execute.call_args
        params = call_args[0][1]
        tags_json = params[5]  # tags is the sixth positional param
        tags = json.loads(tags_json)
        assert "branching-detected" in tags
        assert "checkpoint-divergence" in tags

    async def test_feedback_stored_in_session_feedback_table(self, tmp_path):
        """End-to-end: feedback is actually stored in session_feedback table."""
        restored_id = "restored-cp-abc"
        session_id = "e2e-session"
        span_uuid = "e2e-span-uuid"
        manager = _make_state_manager(session_id=session_id, restored_checkpoint_id=restored_id)

        await _detect_branching_and_request_feedback(
            state_manager=manager,
            new_checkpoint_id="new-cp-e2e",
            new_parent_id="divergent-parent",
            session_span_uuid=span_uuid,
            repo_root=str(tmp_path),
        )

        # Query the session_feedback table to verify the row was inserted
        db = await get_training_db(str(tmp_path))
        try:
            cursor = await db.execute(
                "SELECT session_id, trace_span_uuid, thumbs_up, comment, tags "
                "FROM session_feedback WHERE session_id = ?",
                (session_id,),
            )
            row = await cursor.fetchone()
            assert row is not None, "Feedback row should exist in session_feedback"
            assert row["session_id"] == session_id
            assert row["trace_span_uuid"] == span_uuid
            assert row["thumbs_up"] == 0
            assert "Checkpoint branching detected" in row["comment"]
        finally:
            await db.close()

    async def test_feedback_linked_to_divergence_point_via_trace_span(self, tmp_path):
        """Verify feedback row's trace_span_uuid links to the divergence trace span."""
        restored_id = "restored-cp-abc"
        session_id = "linked-session"
        span_uuid = "divergence-point-span"
        manager = _make_state_manager(session_id=session_id, restored_checkpoint_id=restored_id)

        await _detect_branching_and_request_feedback(
            state_manager=manager,
            new_checkpoint_id="new-cp-linked",
            new_parent_id="divergent-parent",
            session_span_uuid=span_uuid,
            repo_root=str(tmp_path),
        )

        db = await get_training_db(str(tmp_path))
        try:
            cursor = await db.execute(
                "SELECT trace_span_uuid FROM session_feedback WHERE trace_span_uuid = ?",
                (span_uuid,),
            )
            row = await cursor.fetchone()
            assert row is not None, "Feedback should be linkable via trace_span_uuid"
            assert row["trace_span_uuid"] == span_uuid
        finally:
            await db.close()

    async def test_db_exception_is_caught_and_logged(self, tmp_path):
        """When insert_feedback raises, the exception is caught and not propagated."""
        restored_id = "restored-cp-abc"
        manager = _make_state_manager(restored_checkpoint_id=restored_id)

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=RuntimeError("DB connection lost"))
        mock_db.close = AsyncMock()

        with patch(
            "lean_ai.training.db.get_training_db", new_callable=AsyncMock
        ) as mock_get_db:
            mock_get_db.return_value = mock_db
            # Should not raise — exceptions are caught
            await _detect_branching_and_request_feedback(
                state_manager=manager,
                new_checkpoint_id="new-cp-err",
                new_parent_id="divergent-parent",
                session_span_uuid="span-uuid-err",
                repo_root=str(tmp_path),
            )

        # DB was opened but the error was swallowed
        mock_get_db.assert_called_once()
        mock_db.close.assert_called_once()

    async def test_get_training_db_exception_is_caught(self, tmp_path):
        """When get_training_db itself raises, the exception is caught."""
        restored_id = "restored-cp-abc"
        manager = _make_state_manager(restored_checkpoint_id=restored_id)

        with patch(
            "lean_ai.training.db.get_training_db",
            new_callable=AsyncMock,
            side_effect=FileNotFoundError("training.db not found"),
        ):
            # Should not raise
            await _detect_branching_and_request_feedback(
                state_manager=manager,
                new_checkpoint_id="new-cp-err2",
                new_parent_id="divergent-parent",
                session_span_uuid="span-uuid-err2",
                repo_root=str(tmp_path),
            )

    async def test_new_parent_id_none_triggers_divergence(self, tmp_path):
        """When new_parent_id is None but restored_checkpoint_id is set, divergence detected."""
        restored_id = "restored-cp-abc"
        manager = _make_state_manager(restored_checkpoint_id=restored_id)

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.close = AsyncMock()

        with patch(
            "lean_ai.training.db.get_training_db", new_callable=AsyncMock
        ) as mock_get_db:
            mock_get_db.return_value = mock_db
            await _detect_branching_and_request_feedback(
                state_manager=manager,
                new_checkpoint_id="new-cp-none",
                new_parent_id=None,
                session_span_uuid="span-uuid-none",
                repo_root=str(tmp_path),
            )

        # None != restored_id, so divergence is detected
        mock_get_db.assert_called_once()
        assert mock_db.execute.called

    async def test_checkpoint_restore_then_linear_continuation_no_feedback(self, tmp_path):
        """Simulate checkpoint restore followed by linear (non-divergent) continuation."""
        restored_id = "checkpoint-alpha"
        manager = _make_state_manager(restored_checkpoint_id=restored_id)

        with patch(
            "lean_ai.training.db.get_training_db", new_callable=AsyncMock
        ) as mock_get_db:
            # new_parent_id equals restored_id — linear continuation
            await _detect_branching_and_request_feedback(
                state_manager=manager,
                new_checkpoint_id="checkpoint-beta",
                new_parent_id=restored_id,
                session_span_uuid="span-linear",
                repo_root=str(tmp_path),
            )

        mock_get_db.assert_not_called()

    async def test_checkpoint_restore_then_divergent_path_triggers_feedback(self, tmp_path):
        """Simulate checkpoint restore followed by divergent path — feedback triggered."""
        restored_id = "checkpoint-alpha"
        divergent_parent = "checkpoint-gamma"  # Different from restored
        manager = _make_state_manager(restored_checkpoint_id=restored_id)

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.close = AsyncMock()

        with patch(
            "lean_ai.training.db.get_training_db", new_callable=AsyncMock
        ) as mock_get_db:
            mock_get_db.return_value = mock_db
            await _detect_branching_and_request_feedback(
                state_manager=manager,
                new_checkpoint_id="checkpoint-delta",
                new_parent_id=divergent_parent,
                session_span_uuid="span-divergent",
                repo_root=str(tmp_path),
            )

        mock_get_db.assert_called_once()
        assert mock_db.execute.called

        # Verify the comment references the divergence point
        call_args = mock_db.execute.call_args
        params = call_args[0][1]
        comment = params[4]
        assert restored_id in comment
        assert divergent_parent in comment

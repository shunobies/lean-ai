"""Integration tests for prompt_version_id propagation through capture_turn.

Covers:
  1. capture_turn propagates prompt_version_id to insert_training_trace
  2. insert_training_trace accepts prompt_version_id as a parameter
  3. prompt_version_id is persisted in the training_traces table

These tests verify the version propagation chain so that traces can be
linked back to the specific prompt version used during the turn.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from lean_ai.training.capture import capture_turn
from lean_ai.training.db import (
    get_training_db,
    insert_training_trace,
)


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
async def train_db(tmp_path):
    """Training DB connection scoped to a temp directory."""
    db = await get_training_db(str(tmp_path))
    yield db
    await db.close()


# ── 1. capture_turn propagates version_id ──────────────────────────


@pytest.mark.asyncio
async def test_capture_turn_propagates_version_id(tmp_path) -> None:
    """capture_turn must forward prompt_version_id to insert_training_trace
    so that the trace is linked to the prompt version used during the turn."""

    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]
    assistant_output = {"content": "Hi there!"}

    with patch(
        "lean_ai.training.capture.insert_training_trace",
        new_callable=AsyncMock,
    ) as mock_insert:
        mock_insert.return_value = 1

        await capture_turn(
            repo_root=str(tmp_path),
            session_id="version-test-session",
            phase="planning",
            model_name="gpt-4",
            provider="openai",
            messages=messages,
            assistant_output=assistant_output,
            outcome="success",
            prompt_version_id=42,
        )

        # Verify insert_training_trace was called with prompt_version_id
        mock_insert.assert_called_once()
        call_kwargs = mock_insert.call_args[1]
        assert call_kwargs["prompt_version_id"] == 42


# ── 2. insert_training_trace accepts version_id ────────────────────


@pytest.mark.asyncio
async def test_insert_training_trace_accepts_version_id(train_db) -> None:
    """insert_training_trace must accept prompt_version_id as a keyword
    argument without raising an error."""

    messages = [
        {"role": "user", "content": "Test question"},
        {"role": "assistant", "content": "Test answer"},
    ]
    assistant_output = {"content": "Test answer"}

    # Should not raise — prompt_version_id is an accepted parameter
    row_id = await insert_training_trace(
        train_db,
        trace_uuid="version-trace-001",
        session_id="version-test-session",
        phase="planning",
        model_name="gpt-4",
        provider="openai",
        messages=messages,
        assistant_output=assistant_output,
        outcome="success",
        prompt_version_id=42,
    )

    assert row_id >= 1


# ── 3. version_id persisted in training_traces ────────────────────


@pytest.mark.asyncio
async def test_version_id_persisted_in_training_traces(train_db) -> None:
    """The prompt_version_id value passed to insert_training_trace must be
    stored in the training_traces row and retrievable via SQL."""

    messages = [
        {"role": "user", "content": "Persist test"},
        {"role": "assistant", "content": "Persisted"},
    ]
    assistant_output = {"content": "Persisted"}

    await insert_training_trace(
        train_db,
        trace_uuid="persist-trace-001",
        session_id="persist-test-session",
        phase="planning",
        model_name="gpt-4",
        provider="openai",
        messages=messages,
        assistant_output=assistant_output,
        outcome="success",
        prompt_version_id=42,
    )

    # Query the database to verify prompt_version_id was persisted
    cursor = await train_db.execute(
        "SELECT prompt_version_id FROM training_traces WHERE trace_uuid = ?",
        ("persist-trace-001",),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["prompt_version_id"] == 42

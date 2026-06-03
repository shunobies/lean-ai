"""Unit tests for WorkflowState and StateManager (Phase 3: Unified State Object).

Covers:
  1. WorkflowState model construction and validation
  2. Conversation turn bounds enforcement (turn count and char limits)
  3. Journal entry appending
  4. Current plan storage
  5. Pydantic extra-forbid validation
  6. StateManager save/load with legacy migration
  7. StateManager lazy loading and refresh
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from lean_ai.workflow.state import StateManager, WorkflowState


# ── WorkflowState Tests ─────────────────────────────────────────────────────


class FakeAsyncDb:
    def __init__(self) -> None:
        self.close_count = 0

    async def close(self) -> None:
        self.close_count += 1


def test_from_scratch_creates_empty_state():
    """Verify WorkflowState.from_scratch returns a state with empty lists,
    empty strings, None plan, and valid timestamps."""
    state = WorkflowState.from_scratch("sess-1")
    assert state.session_id == "sess-1"
    assert state.current_phase == ""
    assert state.conversation_history == []
    assert state.scratchpad_content == ""
    assert state.journal_entries == []
    assert state.observations == []
    assert state.current_plan is None
    assert state.session_metadata == {}
    assert state.created_at is not None
    assert state.updated_at is not None


def test_from_scratch_timestamps_are_equal():
    """Verify created_at equals updated_at on a fresh state."""
    state = WorkflowState.from_scratch("sess-1")
    assert state.created_at == state.updated_at


def test_add_conversation_turn_appends():
    """Verify add_conversation_turn appends to conversation_history
    and updates updated_at."""
    state = WorkflowState.from_scratch("sess-1")
    original_updated = state.updated_at
    turn = {"role": "user", "content": "hi"}
    state.add_conversation_turn(turn)
    assert state.conversation_history == [turn]
    assert state.updated_at != original_updated


def test_add_conversation_turn_enforces_max_turns():
    """Add 25 turns (exceeds MAX_CONVERSATION_TURNS=20),
    verify only the most recent 20 remain (oldest trimmed)."""
    state = WorkflowState.from_scratch("sess-1")
    for i in range(25):
        state.add_conversation_turn({"role": "user", "content": f"turn-{i}"})
    assert len(state.conversation_history) == 20
    # Verify oldest turns were trimmed (turns 0-4 removed, 5-24 remain)
    contents = [t["content"] for t in state.conversation_history]
    assert contents == [f"turn-{i}" for i in range(5, 25)]


def test_add_conversation_turn_enforces_max_chars():
    """Add turns whose combined JSON size exceeds MAX_CONVERSATION_CHARS=50_000,
    verify oldest turns are trimmed until under the limit."""
    state = WorkflowState.from_scratch("sess-1")
    # Each turn with ~2000 chars, need ~25 turns to exceed 50_000
    large_content = "x" * 2000
    for i in range(25):
        state.add_conversation_turn({"role": "user", "content": f"turn-{i}:" + large_content})
    # Verify we're under the char limit
    total_chars = sum(
        len(json.dumps(turn, ensure_ascii=False))
        for turn in state.conversation_history
    )
    assert total_chars <= 50_000
    # Verify some turns were trimmed
    assert len(state.conversation_history) < 25


def test_enforce_bounds_trims_by_turns_first():
    """Verify turn-count trimming happens before char-count trimming
    (check the order in _enforce_conversation_bounds)."""
    state = WorkflowState.from_scratch("sess-1")
    # Add turns that will exceed both limits
    large_content = "x" * 3000
    for i in range(25):
        state.add_conversation_turn({"role": "user", "content": f"turn-{i}:" + large_content})
    # After turn-count trimming, we should have at most 20 turns
    assert len(state.conversation_history) <= 20
    # Then char-count trimming may reduce further
    total_chars = sum(
        len(json.dumps(turn, ensure_ascii=False))
        for turn in state.conversation_history
    )
    assert total_chars <= 50_000


def test_add_journal_entry_appends():
    """Verify add_journal_entry appends to journal_entries
    and updates updated_at."""
    state = WorkflowState.from_scratch("sess-1")
    original_updated = state.updated_at
    state.add_journal_entry("entry-1")
    assert state.journal_entries == ["entry-1"]
    assert state.updated_at != original_updated


def test_set_current_plan_stores_dict():
    """Verify set_current_plan stores the dict in current_plan
    and updates updated_at."""
    state = WorkflowState.from_scratch("sess-1")
    original_updated = state.updated_at
    plan = {"steps": [{"action": "test"}]}
    state.set_current_plan(plan)
    assert state.current_plan == plan
    assert state.updated_at != original_updated


def test_model_extra_forbid_rejects_unknown_fields():
    """Verify WorkflowState rejects unknown fields with ValidationError."""
    with pytest.raises(ValidationError):
        WorkflowState(**{"session_id": "x", "bogus_field": 1})


def test_model_dump_serializes_correctly():
    """Verify state.model_dump() produces a dict with all expected keys
    that can round-trip through WorkflowState(**dumped)."""
    state = WorkflowState.from_scratch("sess-1")
    state.add_conversation_turn({"role": "user", "content": "test"})
    dumped = state.model_dump()
    # Verify all expected keys are present
    expected_keys = {
        "session_id",
        "current_phase",
        "conversation_history",
        "scratchpad_content",
        "journal_entries",
        "observations",
        "current_plan",
        "session_metadata",
        "created_at",
        "updated_at",
    }
    assert set(dumped.keys()) == expected_keys
    # Verify round-trip
    state2 = WorkflowState(**dumped)
    assert state2.session_id == state.session_id
    assert state2.conversation_history == state.conversation_history


def test_default_values():
    """Verify a freshly constructed state has correct default values."""
    state = WorkflowState.from_scratch("sess-1")
    assert state.current_phase == ""
    assert state.conversation_history == []
    assert state.scratchpad_content == ""
    assert state.journal_entries == []
    assert state.observations == []
    assert state.current_plan is None
    assert state.session_metadata == {}


# ── StateManager Tests ─────────────────────────────────────────────────────


def test_save_creates_consolidated_file(tmp_path, monkeypatch):
    """Create state via from_scratch, call manager.save(),
    verify .lean_ai/state/{session_id}.json exists and contains valid JSON."""
    monkeypatch.chdir(tmp_path)
    manager = StateManager("sess-1")
    state = WorkflowState.from_scratch("sess-1")
    manager._state = state
    manager.save()
    state_file = tmp_path / ".lean_ai" / "state" / "sess-1.json"
    assert state_file.is_file()
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["session_id"] == "sess-1"


def test_save_updates_updated_at(tmp_path, monkeypatch):
    """Save, sleep briefly, modify state, save again, verify updated_at changed."""
    monkeypatch.chdir(tmp_path)
    manager = StateManager("sess-1")
    state = WorkflowState.from_scratch("sess-1")
    manager._state = state
    manager.save()
    first_updated = state.updated_at
    time.sleep(0.01)
    state.add_journal_entry("entry")
    manager.save()
    assert state.updated_at != first_updated


def test_load_from_consolidated_file(tmp_path, monkeypatch):
    """Pre-create a valid consolidated JSON file, call manager.load(),
    verify it returns a WorkflowState with correct values."""
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".lean_ai" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "sess-1.json"
    test_state = WorkflowState.from_scratch("sess-1")
    test_state.add_journal_entry("test-entry")
    state_file.write_text(
        json.dumps(test_state.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    manager = StateManager("sess-1")
    loaded = manager.load()
    assert loaded.session_id == "sess-1"
    assert loaded.journal_entries == ["test-entry"]


def test_load_fallback_to_legacy_on_missing_consolidated(tmp_path, monkeypatch):
    """Do NOT create the consolidated file. Create legacy scratchpad/journal/observations
    files. Call manager.load(), verify it returns state populated from legacy sources."""
    monkeypatch.chdir(tmp_path)
    # Create legacy scratchpad
    scratchpad_dir = tmp_path / ".lean_ai" / "scratchpads"
    scratchpad_dir.mkdir(parents=True, exist_ok=True)
    scratchpad_file = scratchpad_dir / "sess-1.md"
    scratchpad_file.write_text("scratchpad content", encoding="utf-8")
    # Create legacy journal
    journal_dir = tmp_path / ".lean_ai" / "journals"
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal_file = journal_dir / "sess-1.md"
    journal_file.write_text("- [12:00] journal entry", encoding="utf-8")
    # Create legacy observations
    obs_dir = tmp_path / ".lean_ai" / "observations"
    obs_dir.mkdir(parents=True, exist_ok=True)
    obs_file = obs_dir / "sess-1.json"
    obs_file.write_text('[{"key": "value"}]', encoding="utf-8")
    manager = StateManager("sess-1")
    loaded = manager.load()
    assert loaded.scratchpad_content == "scratchpad content"
    assert loaded.journal_entries == ["- [12:00] journal entry"]
    assert loaded.observations == [{"key": "value"}]


def test_load_fallback_to_legacy_on_corrupt_consolidated(tmp_path, monkeypatch):
    """Create a consolidated file with invalid JSON. Call manager.load(),
    verify it falls back to legacy sources without raising."""
    monkeypatch.chdir(tmp_path)
    # Create corrupt consolidated file
    state_dir = tmp_path / ".lean_ai" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "sess-1.json"
    state_file.write_text("not valid json", encoding="utf-8")
    # Create legacy scratchpad
    scratchpad_dir = tmp_path / ".lean_ai" / "scratchpads"
    scratchpad_dir.mkdir(parents=True, exist_ok=True)
    scratchpad_file = scratchpad_dir / "sess-1.md"
    scratchpad_file.write_text("legacy content", encoding="utf-8")
    manager = StateManager("sess-1")
    loaded = manager.load()
    assert loaded.scratchpad_content == "legacy content"


def test_load_legacy_scratchpad(tmp_path, monkeypatch):
    """Create only a legacy scratchpad file at .lean_ai/scratchpads/{session_id}.md.
    Verify load() populates scratchpad_content."""
    monkeypatch.chdir(tmp_path)
    scratchpad_dir = tmp_path / ".lean_ai" / "scratchpads"
    scratchpad_dir.mkdir(parents=True, exist_ok=True)
    scratchpad_file = scratchpad_dir / "sess-1.md"
    scratchpad_file.write_text("scratchpad content", encoding="utf-8")
    manager = StateManager("sess-1")
    loaded = manager.load()
    assert loaded.scratchpad_content == "scratchpad content"


def test_load_legacy_journal(tmp_path, monkeypatch):
    """Create only a legacy journal file at .lean_ai/journals/{session_id}.md
    with timestamped lines like '- [12:00] did thing'.
    Verify load() populates journal_entries."""
    monkeypatch.chdir(tmp_path)
    journal_dir = tmp_path / ".lean_ai" / "journals"
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal_file = journal_dir / "sess-1.md"
    journal_file.write_text("- [12:00] did thing\n- [12:01] did another thing", encoding="utf-8")
    manager = StateManager("sess-1")
    loaded = manager.load()
    assert loaded.journal_entries == ["- [12:00] did thing", "- [12:01] did another thing"]


def test_load_legacy_observations(tmp_path, monkeypatch):
    """Create only a legacy observations file at .lean_ai/observations/{session_id}.json
    with a JSON array. Verify load() populates observations."""
    monkeypatch.chdir(tmp_path)
    obs_dir = tmp_path / ".lean_ai" / "observations"
    obs_dir.mkdir(parents=True, exist_ok=True)
    obs_file = obs_dir / "sess-1.json"
    obs_file.write_text('[{"key": "value"}, {"key2": "value2"}]', encoding="utf-8")
    manager = StateManager("sess-1")
    loaded = manager.load()
    assert loaded.observations == [{"key": "value"}, {"key2": "value2"}]


def test_load_legacy_conversation_from_db(tmp_path, monkeypatch):
    """Mock the lean_ai.db.get_db and lean_ai.db.get_conversation_log imports
    inside _load_legacy. Verify conversation history is populated from the mock return value."""
    monkeypatch.chdir(tmp_path)
    mock_conv = [{"role": "user", "content": "test"}]
    db = FakeAsyncDb()
    with patch("lean_ai.db.get_db", return_value=db):
        with patch("lean_ai.db.get_conversation_log", return_value=mock_conv):
            manager = StateManager("sess-1")
            loaded = manager.load()
            assert loaded.conversation_history == mock_conv
            assert db.close_count == 1


@pytest.mark.asyncio
async def test_load_async_legacy_conversation_from_db(tmp_path, monkeypatch):
    """Verify load_async() populates conversation history from the DB path."""
    monkeypatch.chdir(tmp_path)
    mock_conv = [{"role": "user", "content": "async test"}]
    db = FakeAsyncDb()
    with patch("lean_ai.db.get_db", return_value=db):
        with patch("lean_ai.db.get_conversation_log", return_value=mock_conv):
            manager = StateManager("sess-1")
            loaded = await manager.load_async()
            assert loaded.conversation_history == mock_conv
            assert db.close_count == 1


def test_load_legacy_empty_when_no_sources(tmp_path, monkeypatch):
    """Do not create any legacy files and do not create a consolidated file.
    Verify load() returns an empty state (from from_scratch)."""
    monkeypatch.chdir(tmp_path)
    manager = StateManager("sess-1")
    loaded = manager.load()
    assert loaded.session_id == "sess-1"
    assert loaded.scratchpad_content == ""
    assert loaded.journal_entries == []
    assert loaded.observations == []
    assert loaded.conversation_history == []


def test_save_removes_legacy_files(tmp_path, monkeypatch):
    """Create legacy scratchpad/journal/observations files AND a consolidated state.
    Call save(), verify the legacy files are deleted after the consolidated file is written."""
    monkeypatch.chdir(tmp_path)
    # Create legacy files
    scratchpad_dir = tmp_path / ".lean_ai" / "scratchpads"
    scratchpad_dir.mkdir(parents=True, exist_ok=True)
    scratchpad_file = scratchpad_dir / "sess-1.md"
    scratchpad_file.write_text("scratchpad", encoding="utf-8")
    journal_dir = tmp_path / ".lean_ai" / "journals"
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal_file = journal_dir / "sess-1.md"
    journal_file.write_text("journal", encoding="utf-8")
    obs_dir = tmp_path / ".lean_ai" / "observations"
    obs_dir.mkdir(parents=True, exist_ok=True)
    obs_file = obs_dir / "sess-1.json"
    obs_file.write_text("[]", encoding="utf-8")
    manager = StateManager("sess-1")
    state = WorkflowState.from_scratch("sess-1")
    manager._state = state
    manager.save()
    assert not scratchpad_file.is_file()
    assert not journal_file.is_file()
    assert not obs_file.is_file()


def test_save_raises_runtime_error_when_no_state(tmp_path, monkeypatch):
    """Create a StateManager and call save() without calling load() first.
    Verify it raises RuntimeError."""
    monkeypatch.chdir(tmp_path)
    manager = StateManager("sess-1")
    with pytest.raises(RuntimeError, match="No state to save"):
        manager.save()


def test_get_state_lazy_loads(tmp_path, monkeypatch):
    """Create a consolidated file, call manager.get_state(),
    verify it loads and caches the state. Call get_state() again,
    verify it returns the cached instance."""
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".lean_ai" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "sess-1.json"
    test_state = WorkflowState.from_scratch("sess-1")
    test_state.add_journal_entry("test-entry")
    state_file.write_text(
        json.dumps(test_state.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    manager = StateManager("sess-1")
    state1 = manager.get_state()
    state2 = manager.get_state()
    assert state1 is state2
    assert state1.journal_entries == ["test-entry"]


def test_refresh_state_reload_from_disk(tmp_path, monkeypatch):
    """Load state, modify the JSON file on disk, call manager.refresh_state(),
    verify the returned state reflects the on-disk changes."""
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / ".lean_ai" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "sess-1.json"
    test_state = WorkflowState.from_scratch("sess-1")
    test_state.add_journal_entry("original")
    state_file.write_text(
        json.dumps(test_state.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    manager = StateManager("sess-1")
    loaded = manager.load()
    assert loaded.journal_entries == ["original"]
    # Modify on disk
    test_state.journal_entries = ["modified"]
    state_file.write_text(
        json.dumps(test_state.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    refreshed = manager.refresh_state()
    assert refreshed.journal_entries == ["modified"]


def test_state_manager_creates_state_directory(tmp_path, monkeypatch):
    """Verify that save() creates .lean_ai/state/ directory if it doesn't exist."""
    monkeypatch.chdir(tmp_path)
    manager = StateManager("sess-1")
    state = WorkflowState.from_scratch("sess-1")
    manager._state = state
    manager.save()
    state_dir = tmp_path / ".lean_ai" / "state"
    assert state_dir.is_dir()


@pytest.mark.asyncio
async def test_save_checkpoint_async_persists_and_loads_from_db(tmp_path, monkeypatch):
    """Verify save_checkpoint_async writes a checkpoint retrievable by async helpers."""
    monkeypatch.chdir(tmp_path)
    manager = StateManager("sess-1")
    state = WorkflowState.from_scratch("sess-1")
    checkpoint_id = await manager.save_checkpoint_async(
        state=state,
        phase="Phase 1",
        summary="Async checkpoint",
    )

    loaded = await manager.get_checkpoint_async(checkpoint_id)
    checkpoints = await manager.list_checkpoints_async("sess-1")

    assert loaded.session_id == "sess-1"
    assert len(checkpoints) == 1
    assert checkpoints[0]["id"] == checkpoint_id
    assert checkpoints[0]["is_head"] is True


@pytest.mark.asyncio
async def test_get_checkpoint_async_reads_json_cache_first(tmp_path, monkeypatch):
    """Verify get_checkpoint_async prefers the JSON cache when present."""
    monkeypatch.chdir(tmp_path)
    manager = StateManager("sess-1")
    checkpoint_id = "checkpoint123"
    cp_dir = tmp_path / ".lean_ai" / "checkpoints" / "sess-1"
    cp_dir.mkdir(parents=True, exist_ok=True)
    cached_state = WorkflowState.from_scratch("sess-1")
    cached_state.add_journal_entry("from cache")
    (cp_dir / f"{checkpoint_id}.json").write_text(
        json.dumps(cached_state.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    loaded = await manager.get_checkpoint_async(checkpoint_id)

    assert loaded.journal_entries == ["from cache"]


@pytest.mark.asyncio
async def test_list_checkpoints_async_returns_head_and_status(tmp_path, monkeypatch):
    """Verify list_checkpoints_async normalizes rows and head status."""
    monkeypatch.chdir(tmp_path)
    manager = StateManager("sess-1")
    state = WorkflowState.from_scratch("sess-1")

    first_id = await manager.save_checkpoint_async(
        state=state,
        phase="Phase 1",
        summary="First",
    )
    second_id = await manager.save_checkpoint_async(
        state=state,
        phase="Phase 2",
        summary="Second",
        parent_id=first_id,
    )

    checkpoints = await manager.list_checkpoints_async("sess-1")

    assert [cp["id"] for cp in checkpoints] == [first_id, second_id]
    assert checkpoints[0]["status"] == "completed"
    assert checkpoints[0]["is_head"] is False
    assert checkpoints[1]["status"] == "active"
    assert checkpoints[1]["is_head"] is True


@pytest.mark.asyncio
async def test_load_raises_clear_error_when_called_in_async_context(tmp_path, monkeypatch):
    """Verify sync load fails fast inside an active event loop."""
    monkeypatch.chdir(tmp_path)
    manager = StateManager("sess-1")

    with pytest.raises(RuntimeError, match="load_async\\(\\) must be awaited"):
        manager.load()


@pytest.mark.asyncio
async def test_save_checkpoint_raises_clear_error_when_called_in_async_context(
    tmp_path,
    monkeypatch,
):
    """Verify sync checkpoint save fails fast inside an active event loop."""
    monkeypatch.chdir(tmp_path)
    manager = StateManager("sess-1")
    state = WorkflowState.from_scratch("sess-1")

    with pytest.raises(
        RuntimeError,
        match="save_checkpoint_async\\(\\) must be awaited",
    ):
        manager.save_checkpoint(
            state=state,
            phase="Phase 1",
            summary="sync wrapper should fail in async context",
        )

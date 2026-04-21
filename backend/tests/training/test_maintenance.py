"""Tests for Phase D maintenance tasks: retention + auto-promotion."""

import pytest

from lean_ai.db import get_db
from lean_ai.memory.db import (
    create_memory,
    get_memory,
    list_memories,
    update_curation_status,
)
from lean_ai.training.capture import capture_turn
from lean_ai.training.db import get_training_db
from lean_ai.training.maintenance import (
    auto_promote_memory,
    bulk_invalidate_by_model,
    reset_throttle_for_tests,
    run_retention_pass,
    supersede_user_rejected,
)


@pytest.fixture(autouse=True)
def _reset_throttle():
    reset_throttle_for_tests()
    yield
    reset_throttle_for_tests()


# ── Auto-promotion ──


@pytest.mark.asyncio
async def test_auto_promote_returns_none_for_fresh_content(tmp_path):
    db = await get_db(str(tmp_path))
    try:
        result = await auto_promote_memory(
            db, category="pattern", content="never-before-seen lesson",
        )
        assert result is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_auto_promote_bumps_existing_memory(tmp_path):
    db = await get_db(str(tmp_path))
    try:
        first = await create_memory(
            db, session_id="s1", category="pattern",
            content="use pytest-asyncio for async tests",
        )
        result = await auto_promote_memory(
            db, category="pattern",
            content="Use pytest-asyncio for async tests!",  # normalized = same
        )
        assert result is not None
        assert result["id"] == first["id"]
        assert result["seen_count"] == 2
        # Not yet at threshold, still `auto`
        assert result["curation_status"] == "auto"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_auto_promote_upgrades_at_threshold(tmp_path, monkeypatch):
    from lean_ai.config import settings as cfg

    monkeypatch.setattr(cfg, "memory_autopromote_threshold", 3)

    db = await get_db(str(tmp_path))
    try:
        content = "use pytest-asyncio for async tests"
        await create_memory(db, "s1", "pattern", content)
        # Second sighting — bumps to 2
        await auto_promote_memory(db, category="pattern", content=content)
        # Third sighting — should promote to high_confidence_auto
        result = await auto_promote_memory(
            db, category="pattern", content=content,
        )
        assert result["seen_count"] == 3
        assert result["curation_status"] == "high_confidence_auto"
        assert result["confidence"] > 0.5
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_supersede_skips_user_rejected(tmp_path):
    db = await get_db(str(tmp_path))
    try:
        mem = await create_memory(
            db, "s1", "pattern", "this lesson was rejected",
        )
        await update_curation_status(db, mem["id"], "user_rejected")

        should_skip = await supersede_user_rejected(
            db, category="pattern", content="this lesson was rejected",
        )
        assert should_skip is True
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_supersede_allows_unrelated_content(tmp_path):
    db = await get_db(str(tmp_path))
    try:
        should_skip = await supersede_user_rejected(
            db, category="pattern", content="something totally new",
        )
        assert should_skip is False
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_bulk_invalidate_by_model(tmp_path):
    db = await get_db(str(tmp_path))
    try:
        await create_memory(
            db, "s1", "pattern", "memory from model A",
            model_name="bad-model-v1", curation_status="auto",
        )
        await create_memory(
            db, "s1", "pattern", "memory from model B",
            model_name="good-model", curation_status="high_confidence_auto",
        )

        count = await bulk_invalidate_by_model(db, model_name="bad-model-v1")
        assert count == 1

        remaining = await list_memories(
            db, curation_status="superseded", include_expired=True,
        )
        assert len(remaining) == 1
        assert remaining[0]["model_name"] == "bad-model-v1"

        good = await list_memories(
            db, curation_status="high_confidence_auto",
        )
        assert len(good) == 1
    finally:
        await db.close()


# ── Retention pruning ──


@pytest.mark.asyncio
async def test_run_retention_pass_throttled(tmp_path):
    from datetime import datetime, timedelta, timezone

    # Seed a stale row
    trace_uuid = await capture_turn(
        str(tmp_path),
        session_id="s1", phase="p", model_name="m", provider="ollama",
        messages=[], assistant_output={},
    )
    db = await get_training_db(str(tmp_path))
    try:
        past = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        await db.execute(
            "UPDATE training_traces SET created_at = ? WHERE trace_uuid = ?",
            (past, trace_uuid),
        )
        await db.commit()
    finally:
        await db.close()

    # First call — should prune
    counts = await run_retention_pass(str(tmp_path))
    assert counts.get("training_traces", 0) == 1

    # Second call within an hour — should be throttled
    counts_again = await run_retention_pass(str(tmp_path))
    assert counts_again == {}


@pytest.mark.asyncio
async def test_retention_force_overrides_throttle(tmp_path):
    # Run once to set the throttle timestamp
    await run_retention_pass(str(tmp_path))
    # Force another immediate run — should actually open the DB
    counts = await run_retention_pass(str(tmp_path), force=True)
    # Empty archive → all counts zero, but dict returned (not throttled)
    assert isinstance(counts, dict)
    assert counts.get("training_traces", 0) == 0


@pytest.mark.asyncio
async def test_retention_disabled_when_capture_disabled(tmp_path, monkeypatch):
    from lean_ai.config import settings as cfg

    monkeypatch.setattr(cfg, "enable_training_capture", False)
    counts = await run_retention_pass(str(tmp_path), force=True)
    assert counts == {}


# ── Extractor integration: dedupe and promote ──


@pytest.mark.asyncio
async def test_extractor_dedupes_via_auto_promote(tmp_path, monkeypatch):
    """Extraction path should bump seen_count rather than inserting twice."""
    from lean_ai.config import settings as cfg

    monkeypatch.setattr(cfg, "memory_autopromote_threshold", 2)

    db = await get_db(str(tmp_path))
    try:
        # Seed one memory
        original = await create_memory(
            db, "s1", "gotcha", "same lesson",
        )
        # Simulate another extraction round via auto_promote
        second = await auto_promote_memory(
            db, category="gotcha", content="Same lesson!!",  # different case
        )
        assert second["id"] == original["id"]
        # seen_count bumped AND promoted (threshold 2)
        assert second["seen_count"] == 2
        assert second["curation_status"] == "high_confidence_auto"

        # Rows count stays at 1 — no duplicate insert
        all_rows = await list_memories(
            db, include_expired=True,
            curation_status=["auto", "high_confidence_auto"],
        )
        assert len(all_rows) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_extractor_skips_rejected_duplicates(tmp_path):
    db = await get_db(str(tmp_path))
    try:
        mem = await create_memory(db, "s1", "gotcha", "bad lesson")
        await update_curation_status(db, mem["id"], "user_rejected")

        # Should report "skip" for near-identical content
        should_skip = await supersede_user_rejected(
            db, category="gotcha", content="Bad lesson.",
        )
        assert should_skip is True

        # The original rejected memory should not be clobbered
        current = await get_memory(db, mem["id"])
        assert current["curation_status"] == "user_rejected"
    finally:
        await db.close()

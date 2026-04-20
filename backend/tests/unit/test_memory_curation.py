"""Tests for the Phase-A curation extensions to memory CRUD."""

from datetime import datetime, timedelta, timezone

import pytest

from lean_ai.db import get_db
from lean_ai.memory.db import (
    bump_seen_count,
    create_memory,
    find_similar_memory,
    get_memory,
    list_memories,
    set_expiry_from_ttl,
    update_curation_status,
)


@pytest.fixture
async def db(tmp_path):
    """Open a temporary workspace database."""
    conn = await get_db(str(tmp_path))
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_defaults_on_create(db):
    mem = await create_memory(
        db, session_id="s1", category="pattern", content="default defaults",
    )
    assert mem["curation_status"] == "auto"
    assert mem["confidence"] == pytest.approx(0.5)
    assert mem["seen_count"] == 1
    assert mem["expires_at"] is None
    assert mem["source_phase"] is None
    assert mem["model_name"] is None
    assert mem["last_seen_at"]


@pytest.mark.asyncio
async def test_phase_specific_fields_preserved(db):
    mem = await create_memory(
        db,
        session_id="s1",
        category="rejection",
        content="plan was too vague",
        curation_status="auto",
        source_phase="plan_rejection",
        model_name="qwen3-coder:30b",
    )
    reloaded = await get_memory(db, mem["id"])
    assert reloaded["source_phase"] == "plan_rejection"
    assert reloaded["model_name"] == "qwen3-coder:30b"


@pytest.mark.asyncio
async def test_list_filters_by_curation_status(db):
    await create_memory(db, "s1", "pattern", "auto one")
    b = await create_memory(db, "s1", "pattern", "confirmed one")
    await update_curation_status(db, b["id"], "user_confirmed", confidence=0.9)
    c = await create_memory(db, "s1", "pattern", "rejected one")
    await update_curation_status(db, c["id"], "user_rejected", confidence=0.0)

    autos = await list_memories(db, curation_status="auto")
    assert len(autos) == 1 and autos[0]["content"] == "auto one"

    confirmed = await list_memories(db, curation_status="user_confirmed")
    assert len(confirmed) == 1 and confirmed[0]["content"] == "confirmed one"

    multi = await list_memories(
        db, curation_status=["user_confirmed", "user_rejected"],
    )
    assert {m["content"] for m in multi} == {"confirmed one", "rejected one"}


@pytest.mark.asyncio
async def test_list_excludes_expired_by_default(db):
    fresh = await create_memory(db, "s1", "pattern", "still fresh")
    stale = await create_memory(db, "s1", "pattern", "stale")
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    # Use internal SQL to set past expiry (API helper uses created_at+ttl)
    await db.execute(
        "UPDATE session_memories SET expires_at = ? WHERE id = ?",
        (past, stale["id"]),
    )
    await db.commit()

    visible = await list_memories(db)
    assert [m["id"] for m in visible] == [fresh["id"]]

    with_expired = await list_memories(db, include_expired=True)
    assert len(with_expired) == 2


@pytest.mark.asyncio
async def test_update_curation_status_roundtrip(db):
    mem = await create_memory(db, "s1", "pattern", "promote me")
    ok = await update_curation_status(
        db, mem["id"], "user_confirmed", confidence=0.9,
    )
    assert ok is True

    reloaded = await get_memory(db, mem["id"])
    assert reloaded["curation_status"] == "user_confirmed"
    assert reloaded["confidence"] == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_update_curation_status_rejects_invalid(db):
    mem = await create_memory(db, "s1", "pattern", "valid only")
    with pytest.raises(ValueError):
        await update_curation_status(db, mem["id"], "banana")


@pytest.mark.asyncio
async def test_update_nonexistent_returns_false(db):
    assert await update_curation_status(db, "missing", "user_confirmed") is False


@pytest.mark.asyncio
async def test_find_similar_matches_normalized_content(db):
    first = await create_memory(
        db, "s1", "gotcha", "When pytest fails, check PYTHONPATH.",
    )
    # Same content, different capitalization + punctuation
    match = await find_similar_memory(
        db, "when PYTEST FAILS check pythonpath",
    )
    assert match is not None and match["id"] == first["id"]


@pytest.mark.asyncio
async def test_find_similar_respects_category(db):
    await create_memory(db, "s1", "gotcha", "alpha bravo")
    await create_memory(db, "s1", "pattern", "alpha bravo")
    match = await find_similar_memory(db, "alpha bravo", category="pattern")
    assert match is not None and match["category"] == "pattern"


@pytest.mark.asyncio
async def test_bump_seen_count_without_threshold(db):
    mem = await create_memory(db, "s1", "pattern", "seen often")
    updated = await bump_seen_count(db, mem["id"])
    assert updated["seen_count"] == 2
    assert updated["curation_status"] == "auto"


@pytest.mark.asyncio
async def test_bump_seen_count_auto_promotes(db):
    mem = await create_memory(db, "s1", "pattern", "rising star")
    await bump_seen_count(db, mem["id"], promote_threshold=3)  # 2
    updated = await bump_seen_count(db, mem["id"], promote_threshold=3)  # 3
    assert updated["seen_count"] == 3
    assert updated["curation_status"] == "high_confidence_auto"
    assert updated["confidence"] > 0.5


@pytest.mark.asyncio
async def test_bump_preserves_confirmed_status(db):
    mem = await create_memory(db, "s1", "pattern", "already confirmed")
    await update_curation_status(db, mem["id"], "user_confirmed", confidence=0.9)
    updated = await bump_seen_count(db, mem["id"], promote_threshold=2)
    # Already confirmed — promotion branch should not overwrite
    assert updated["curation_status"] == "user_confirmed"
    assert updated["seen_count"] == 2


@pytest.mark.asyncio
async def test_set_expiry_from_ttl(db):
    mem = await create_memory(db, "s1", "pattern", "ttl me")
    ok = await set_expiry_from_ttl(db, mem["id"], ttl_days=7)
    assert ok is True
    reloaded = await get_memory(db, mem["id"])
    assert reloaded["expires_at"] is not None


@pytest.mark.asyncio
async def test_indexes_exist_after_ensure_columns(db):
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='session_memories'"
    )
    rows = await cursor.fetchall()
    names = {r[0] for r in rows}
    assert "idx_mem_status" in names
    assert "idx_mem_category" in names

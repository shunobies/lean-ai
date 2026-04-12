"""Tests for session memory CRUD operations."""

import pytest

from lean_ai.db import get_db
from lean_ai.memory.db import (
    create_memory,
    delete_memory,
    get_memories_for_session,
    list_memories,
)


@pytest.fixture
async def db(tmp_path):
    """Open a temporary workspace database."""
    conn = await get_db(str(tmp_path))
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_create_and_list(db):
    mem = await create_memory(
        db,
        session_id="sess_001",
        category="gotcha",
        content="pytest requires conftest.py at repo root",
        tags=["testing", "pytest"],
        source_task="Fix test discovery",
    )
    assert mem["id"]
    assert mem["category"] == "gotcha"
    assert mem["content"] == "pytest requires conftest.py at repo root"
    assert mem["tags"] == ["testing", "pytest"]
    assert mem["source_task"] == "Fix test discovery"

    all_mems = await list_memories(db)
    assert len(all_mems) == 1
    assert all_mems[0]["id"] == mem["id"]


@pytest.mark.asyncio
async def test_list_filtered_by_category(db):
    await create_memory(db, "s1", "gotcha", "gotcha item")
    await create_memory(db, "s1", "pattern", "pattern item")
    await create_memory(db, "s1", "gotcha", "another gotcha")

    gotchas = await list_memories(db, category="gotcha")
    assert len(gotchas) == 2
    assert all(m["category"] == "gotcha" for m in gotchas)

    patterns = await list_memories(db, category="pattern")
    assert len(patterns) == 1


@pytest.mark.asyncio
async def test_get_memories_for_session(db):
    await create_memory(db, "sess_a", "gotcha", "mem A1")
    await create_memory(db, "sess_a", "pattern", "mem A2")
    await create_memory(db, "sess_b", "build", "mem B1")

    mems_a = await get_memories_for_session(db, "sess_a")
    assert len(mems_a) == 2

    mems_b = await get_memories_for_session(db, "sess_b")
    assert len(mems_b) == 1
    assert mems_b[0]["content"] == "mem B1"


@pytest.mark.asyncio
async def test_delete_memory(db):
    mem = await create_memory(db, "s1", "gotcha", "to delete")
    assert await delete_memory(db, mem["id"]) is True

    all_mems = await list_memories(db)
    assert len(all_mems) == 0


@pytest.mark.asyncio
async def test_delete_nonexistent(db):
    assert await delete_memory(db, "nonexistent") is False


@pytest.mark.asyncio
async def test_tags_none(db):
    mem = await create_memory(db, "s1", "build", "no tags here")
    assert mem["tags"] == []

    all_mems = await list_memories(db)
    assert all_mems[0]["tags"] == []


@pytest.mark.asyncio
async def test_list_limit(db):
    for i in range(10):
        await create_memory(db, "s1", "pattern", f"memory {i}")

    limited = await list_memories(db, limit=3)
    assert len(limited) == 3

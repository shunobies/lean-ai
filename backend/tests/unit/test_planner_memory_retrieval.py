"""Tests for the Phase-A retrieval filters in planner_helpers."""

import pytest

from lean_ai.db import get_db
from lean_ai.llm.planner_helpers import (
    _retrieve_memories_for_phase,
    retrieve_design_memories,
    retrieve_fix_pattern_memories,
)
from lean_ai.memory.db import create_memory, update_curation_status
from lean_ai.memory.index import index_memory


async def _seed(db, repo_root, *, content, category, status, session="s1",
                tags=None):
    mem = await create_memory(
        db, session_id=session, category=category, content=content, tags=tags,
        curation_status=status,
    )
    index_memory(
        repo_root=repo_root, memory_id=mem["id"], content=content,
        category=category, tags=tags,
    )
    return mem


@pytest.mark.asyncio
async def test_retrieval_excludes_auto_memories(tmp_path):
    repo_root = str(tmp_path)
    db = await get_db(repo_root)
    try:
        await _seed(
            db, repo_root,
            content="pytest imports break when rootdir is wrong",
            category="gotcha", status="auto",
        )
        await _seed(
            db, repo_root,
            content="use pytest -s to see stdout in tests",
            category="gotcha", status="user_confirmed",
        )

        result = await _retrieve_memories_for_phase(
            repo_root, "pytest", phase_label="test",
        )
        assert "pytest -s" in result  # confirmed shows up
        assert "rootdir is wrong" not in result  # auto excluded
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_retrieval_filters_by_category(tmp_path):
    repo_root = str(tmp_path)
    db = await get_db(repo_root)
    try:
        await _seed(
            db, repo_root, content="gotcha content pytest",
            category="gotcha", status="user_confirmed",
        )
        await _seed(
            db, repo_root, content="architecture content pytest",
            category="architecture", status="user_confirmed",
        )

        result = await _retrieve_memories_for_phase(
            repo_root, "pytest", phase_label="test",
            categories=["gotcha"],
        )
        assert "gotcha content" in result
        assert "architecture content" not in result
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_design_memory_retrieval_uses_design_categories(tmp_path):
    repo_root = str(tmp_path)
    db = await get_db(repo_root)
    try:
        await _seed(
            db, repo_root,
            content="async loops must not block the event loop pytest",
            category="gotcha", status="user_confirmed",
        )
        await _seed(
            db, repo_root, content="build note pytest",
            category="build", status="user_confirmed",
        )

        result = await retrieve_design_memories(repo_root, "pytest")
        assert "async loops" in result
        assert "build note" not in result
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_fix_pattern_retrieval_uses_fix_categories(tmp_path):
    repo_root = str(tmp_path)
    db = await get_db(repo_root)
    try:
        await _seed(
            db, repo_root,
            content="when ruff reports F401 remove the unused import",
            category="fix_pattern", status="user_confirmed",
        )
        await _seed(
            db, repo_root, content="architecture: we use hex layering",
            category="architecture", status="user_confirmed",
        )

        result = await retrieve_fix_pattern_memories(repo_root, "ruff F401")
        assert "unused import" in result
        assert "hex layering" not in result
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_rejected_memories_excluded(tmp_path):
    repo_root = str(tmp_path)
    db = await get_db(repo_root)
    try:
        rejected = await _seed(
            db, repo_root, content="this is a bad memory pytest",
            category="gotcha", status="user_confirmed",
        )
        await update_curation_status(
            db, rejected["id"], "user_rejected", confidence=0.0,
        )

        result = await _retrieve_memories_for_phase(
            repo_root, "pytest", phase_label="test",
        )
        assert "bad memory" not in result
    finally:
        await db.close()

"""Tests for durable architecture decision storage."""

import pytest

from lean_ai.architecture.decision_db import (
    create_architecture_decision,
    get_architecture_decision,
    list_architecture_decisions,
    update_architecture_decision_status,
)
from lean_ai.db import get_db


@pytest.fixture
async def db(tmp_path):
    """Open a temporary workspace database."""
    conn = await get_db(str(tmp_path))
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_architecture_decision_roundtrip(db):
    decision = await create_architecture_decision(
        db,
        title="Keep extension chat-first",
        summary="Architecture review should stay in chat mode.",
        rationale="It reuses existing session and memory retrieval infrastructure.",
        tags=["extension", "chat"],
        source_session_id="abc123",
        source_memory_id="mem123",
        source_plan_decision_ref="plan:abc123:4",
    )

    reloaded = await get_architecture_decision(db, decision["id"])

    assert reloaded is not None
    assert reloaded["title"] == "Keep extension chat-first"
    assert reloaded["tags"] == ["extension", "chat"]
    assert reloaded["source_session_id"] == "abc123"
    assert reloaded["source_memory_id"] == "mem123"
    assert reloaded["source_plan_decision_ref"] == "plan:abc123:4"


@pytest.mark.asyncio
async def test_architecture_decision_search_and_status_filter(db):
    kept = await create_architecture_decision(
        db,
        title="Prefer decision registry over ADR files",
        summary="Store architecture decisions in the local DB.",
        rationale="Avoid repo clutter while preserving durable reasoning.",
        tags=["architecture", "memory"],
    )
    archived = await create_architecture_decision(
        db,
        title="Superseded experiment",
        summary="Old approach for testing.",
        rationale="Replaced by the decision registry.",
        tags=["archive"],
    )
    await update_architecture_decision_status(
        db,
        archived["id"],
        status="superseded",
    )

    active = await list_architecture_decisions(db, query="decision registry")
    superseded = await list_architecture_decisions(db, status="superseded")

    assert [row["id"] for row in active] == [kept["id"]]
    assert [row["id"] for row in superseded] == [archived["id"]]

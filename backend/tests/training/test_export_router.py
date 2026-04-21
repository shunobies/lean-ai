"""Tests for the /api/export router (auth, streaming, manifest caching)."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lean_ai.memory.db import create_memory
from lean_ai.routers.export import clear_manifest_cache, export_router
from lean_ai.training.capture import capture_plan_decision, capture_turn
from lean_ai.training.db import new_trace_uuid


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(export_router, prefix="/api")
    return application


@pytest.fixture
def client(app):
    clear_manifest_cache()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def api_key(monkeypatch):
    from lean_ai.config import settings as cfg

    monkeypatch.setattr(cfg, "export_api_key", "test-key-123")
    return "test-key-123"


@pytest.fixture
def auth_headers(api_key):
    return {"Authorization": f"Bearer {api_key}"}


# ── Auth ──


def test_export_disabled_without_key(client, tmp_path):
    resp = client.get(f"/api/export/manifest?repo_root={tmp_path}")
    assert resp.status_code == 503
    assert "disabled" in resp.json()["detail"].lower()


def test_missing_auth_header_rejected(client, tmp_path, api_key):
    resp = client.get(f"/api/export/manifest?repo_root={tmp_path}")
    assert resp.status_code == 401


def test_wrong_key_rejected(client, tmp_path, api_key):
    resp = client.get(
        f"/api/export/manifest?repo_root={tmp_path}",
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert resp.status_code == 401


# ── /workspace-id ──


def test_workspace_id_returns_16_chars(client, tmp_path, auth_headers):
    resp = client.get(
        f"/api/export/workspace-id?repo_root={tmp_path}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    ws_id = resp.json()["workspace_id"]
    assert len(ws_id) == 16


# ── /manifest ──


@pytest.mark.asyncio
async def test_manifest_counts_traces_and_memories(tmp_path):
    # Seed training archive
    await capture_turn(
        str(tmp_path),
        session_id="s1",
        phase="implementation",
        model_name="m-test",
        provider="ollama",
        messages=[{"role": "user", "content": "hi"}],
        assistant_output={"content": "hi", "thinking": "", "tool_calls": []},
        outcome="success",
    )
    # Seed workspace DB with a memory
    from lean_ai.db import get_db

    main_db = await get_db(str(tmp_path))
    try:
        await create_memory(
            main_db, session_id="s1", category="gotcha", content="test",
            curation_status="user_confirmed",
        )
    finally:
        await main_db.close()


def test_manifest_aggregates(client, tmp_path, auth_headers):
    import asyncio
    asyncio.run(test_manifest_counts_traces_and_memories(tmp_path))

    resp = client.get(
        f"/api/export/manifest?repo_root={tmp_path}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_traces"] == 1
    assert "m-test" in body["by_model"]
    assert body["memories"]["total"] == 1
    assert body["memories"]["by_status"]["user_confirmed"] == 1


# ── /traces ──


def _jsonl(resp) -> list[dict]:
    return [
        json.loads(line) for line in resp.text.splitlines() if line.strip()
    ]


@pytest.mark.asyncio
async def _seed_traces_with_pair(tmp_path):
    pair_id = "pair-1"
    await capture_turn(
        str(tmp_path),
        session_id="s1",
        phase="phase3",
        model_name="qwen3-coder:30b",
        provider="ollama",
        messages=[{"role": "user", "content": "design X"}],
        assistant_output={"content": "approach A (rejected)", "tool_calls": []},
        outcome="rejected",
        pair_id=pair_id,
        preference=-1,
        pair_kind="plan_revision",
    )
    await capture_turn(
        str(tmp_path),
        session_id="s1",
        phase="phase3",
        model_name="qwen3-coder:30b",
        provider="ollama",
        messages=[{"role": "user", "content": "design X"}],
        assistant_output={"content": "approach B (chosen)", "tool_calls": []},
        outcome="success",
        pair_id=pair_id,
        preference=1,
        pair_kind="plan_revision",
    )


def test_traces_raw_returns_both_rows(client, tmp_path, auth_headers):
    import asyncio
    asyncio.run(_seed_traces_with_pair(tmp_path))

    resp = client.get(
        f"/api/export/traces?repo_root={tmp_path}&format=raw",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    lines = _jsonl(resp)
    assert len(lines) == 2
    # Anonymization: session_id is hashed, not original
    assert all(row["session_id"] != "s1" for row in lines)
    # workspace_id added
    assert all("workspace_id" in row for row in lines)


def test_traces_sft_only_success(client, tmp_path, auth_headers):
    import asyncio
    asyncio.run(_seed_traces_with_pair(tmp_path))

    resp = client.get(
        f"/api/export/traces?repo_root={tmp_path}&format=sft",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    lines = _jsonl(resp)
    assert len(lines) == 1
    assert lines[0]["messages"][-1]["role"] == "assistant"
    assert "approach B" in lines[0]["messages"][-1]["content"]


def test_traces_dpo_pairs(client, tmp_path, auth_headers):
    import asyncio
    asyncio.run(_seed_traces_with_pair(tmp_path))

    resp = client.get(
        f"/api/export/traces?repo_root={tmp_path}&format=dpo",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    lines = _jsonl(resp)
    assert len(lines) == 1
    pair = lines[0]
    assert "approach B" in pair["chosen"]["content"]
    assert "approach A" in pair["rejected"]["content"]
    assert pair["pair_kind"] == "plan_revision"


def test_traces_rejects_unknown_format(client, tmp_path, auth_headers):
    resp = client.get(
        f"/api/export/traces?repo_root={tmp_path}&format=parquet",
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_traces_rejects_excessive_limit(client, tmp_path, auth_headers):
    resp = client.get(
        f"/api/export/traces?repo_root={tmp_path}&limit=99999",
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_traces_filter_by_model(client, tmp_path, auth_headers):
    import asyncio
    asyncio.run(_seed_traces_with_pair(tmp_path))

    resp = client.get(
        f"/api/export/traces?repo_root={tmp_path}&format=raw&model=other-model",
        headers=auth_headers,
    )
    lines = _jsonl(resp)
    assert lines == []


# ── /memories ──


@pytest.mark.asyncio
async def _seed_memory(tmp_path, *, content, status="user_confirmed"):
    from lean_ai.db import get_db

    db = await get_db(str(tmp_path))
    try:
        await create_memory(
            db, session_id="s1", category="gotcha", content=content,
            curation_status=status, tags=["test"],
        )
    finally:
        await db.close()


def test_memories_stream_anonymized(client, tmp_path, auth_headers):
    import asyncio
    asyncio.run(_seed_memory(
        tmp_path,
        content="Generic note about async tests — use pytest.mark.asyncio.",
    ))

    resp = client.get(
        f"/api/export/memories?repo_root={tmp_path}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    lines = _jsonl(resp)
    assert len(lines) == 1
    assert "workspace_id" in lines[0]
    assert "session_id" not in lines[0]
    assert "anonymization_ratio" in lines[0]


def test_memories_filters_auto_out_by_default(client, tmp_path, auth_headers):
    import asyncio
    asyncio.run(_seed_memory(tmp_path, content="auto memory", status="auto"))

    resp = client.get(
        f"/api/export/memories?repo_root={tmp_path}",
        headers=auth_headers,
    )
    lines = _jsonl(resp)
    assert lines == []


# ── /events ──


@pytest.mark.asyncio
async def _seed_event(tmp_path):
    from lean_ai.training.capture import capture_workflow_event

    await capture_workflow_event(
        str(tmp_path), session_id="s1",
        event_type="cancellation", payload={"task": "x"},
    )


def test_events_streamed(client, tmp_path, auth_headers):
    import asyncio
    asyncio.run(_seed_event(tmp_path))

    resp = client.get(
        f"/api/export/events?repo_root={tmp_path}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    lines = _jsonl(resp)
    assert len(lines) == 1
    assert lines[0]["event_type"] == "cancellation"
    assert lines[0]["session_id"] != "s1"  # hashed
    assert "workspace_id" in lines[0]


def test_events_filter_by_type(client, tmp_path, auth_headers):
    import asyncio
    asyncio.run(_seed_event(tmp_path))

    resp = client.get(
        f"/api/export/events?repo_root={tmp_path}&event_type=loop_detected",
        headers=auth_headers,
    )
    lines = _jsonl(resp)
    assert lines == []


# ── Plan decision hook → training archive → export ──


@pytest.mark.asyncio
async def _seed_plan_decision(tmp_path):
    await capture_plan_decision(
        str(tmp_path),
        session_id="s1",
        revision_count=1,
        task="implement X",
        plan_before={"a": 1},
        plan_after={"a": 2},
        feedback="add error handling",
        decision="approved",
        trace_uuid=new_trace_uuid(),
        pair_trace_uuid=new_trace_uuid(),
    )


def test_plan_decisions_included_in_manifest(client, tmp_path, auth_headers):
    import asyncio
    asyncio.run(_seed_plan_decision(tmp_path))

    resp = client.get(
        f"/api/export/manifest?repo_root={tmp_path}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["plan_decisions"] == 1

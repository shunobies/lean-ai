"""Tests for the /api/diffs decision endpoint."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lean_ai.routers.diffs import diffs_router


@pytest.fixture
def client(tmp_path):
    app = FastAPI()
    app.include_router(diffs_router, prefix="/api")
    with TestClient(app) as c:
        yield c


def test_post_diff_decision_writes_row(client, tmp_path):
    payload = {
        "repo_root": str(tmp_path),
        "session_id": "sess-abc",
        "file_path": "src/foo.py",
        "accepted": True,
        "diff_hash": "deadbeef12345678",
        "note": "looks correct",
    }
    resp = client.post("/api/diffs/decision", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["stored"] is True
    assert isinstance(body["id"], int) and body["id"] > 0


def test_post_diff_decision_requires_session(client, tmp_path):
    payload = {
        "repo_root": str(tmp_path),
        "session_id": "",  # empty — pydantic should reject
        "file_path": "x",
        "accepted": True,
    }
    resp = client.post("/api/diffs/decision", json=payload)
    assert resp.status_code == 422


def test_post_diff_decision_returns_stored_false_when_disabled(
    client, tmp_path, monkeypatch,
):
    from lean_ai.config import settings as cfg

    monkeypatch.setattr(cfg, "enable_training_capture", False)
    payload = {
        "repo_root": str(tmp_path),
        "session_id": "s1",
        "file_path": "a.py",
        "accepted": False,
    }
    resp = client.post("/api/diffs/decision", json=payload)
    assert resp.status_code == 200
    assert resp.json()["stored"] is False

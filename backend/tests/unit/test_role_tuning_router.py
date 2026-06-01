"""Tests for explicit role-tuning prewarm API behavior."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lean_ai.routers import role_tuning as router_mod
from lean_ai.routers.role_tuning import role_tuning_router


class FakeClient:
    def __init__(self, provider_name: str, model_name: str):
        self.provider_name = provider_name
        self.model_name = model_name


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(role_tuning_router, prefix="/api")
    with TestClient(app) as c:
        yield c


def test_prewarm_role_tuning_skips_current_profiles(client, tmp_path, monkeypatch):
    primary = FakeClient("ollama", "primary-model")
    request = FakeClient("ollama", "request-model")
    expert = FakeClient("ollama", "expert-model")

    monkeypatch.setattr(router_mod, "llm_client", primary)
    monkeypatch.setattr(router_mod, "request_llm_client", request)
    monkeypatch.setattr(router_mod, "expert_llm_client", expert)
    monkeypatch.setattr(
        router_mod,
        "load_role_tuning_profile",
        lambda repo_root, scope: SimpleNamespace(judge_warning=f"{scope.agent_role}-warning"),
    )
    monkeypatch.setattr(router_mod, "profile_is_current", lambda profile, scope, repo_root=None: True)

    ensure_calls: list[str] = []

    async def _record(*args, **kwargs):
        ensure_calls.append("called")
        return None

    monkeypatch.setattr(router_mod, "ensure_primary_role_tuning", _record)
    monkeypatch.setattr(router_mod, "ensure_request_role_tuning", _record)
    monkeypatch.setattr(router_mod, "ensure_expert_role_tuning", _record)

    resp = client.post("/api/role-tuning/prewarm", json={"repo_root": str(tmp_path)})
    assert resp.status_code == 200
    body = resp.json()

    assert [item["status"] for item in body["results"]] == ["skipped", "skipped", "skipped"]
    assert [item["role"] for item in body["results"]] == ["primary", "request", "expert"]
    assert ensure_calls == []


def test_prewarm_role_tuning_tunes_missing_profiles_and_reports_paths(client, tmp_path, monkeypatch):
    primary = FakeClient("ollama", "primary-model")
    request = FakeClient("openai", "request-model")
    expert = FakeClient("anthropic", "expert-model")

    monkeypatch.setattr(router_mod, "llm_client", primary)
    monkeypatch.setattr(router_mod, "request_llm_client", request)
    monkeypatch.setattr(router_mod, "expert_llm_client", expert)
    monkeypatch.setattr(router_mod, "load_role_tuning_profile", lambda repo_root, scope: None)
    monkeypatch.setattr(router_mod, "profile_is_current", lambda profile, scope, repo_root=None: False)

    calls: list[str] = []

    async def _ensure_primary(**kwargs):
        calls.append("primary")
        return router_mod.prompt_scope_for_role(primary, "primary")

    async def _ensure_request(**kwargs):
        calls.append("request")
        return router_mod.prompt_scope_for_role(request, "request")

    async def _ensure_expert(**kwargs):
        calls.append("expert")
        return router_mod.prompt_scope_for_role(expert, "expert")

    monkeypatch.setattr(router_mod, "ensure_primary_role_tuning", _ensure_primary)
    monkeypatch.setattr(router_mod, "ensure_request_role_tuning", _ensure_request)
    monkeypatch.setattr(router_mod, "ensure_expert_role_tuning", _ensure_expert)

    resp = client.post("/api/role-tuning/prewarm", json={"repo_root": str(tmp_path)})
    assert resp.status_code == 200
    body = resp.json()

    assert calls == ["primary", "request", "expert"]
    assert [item["status"] for item in body["results"]] == ["tuned", "tuned", "tuned"]
    assert body["results"][0]["profile_path"].endswith("primary--ollama-primary-model.json")
    assert body["results"][1]["profile_path"].endswith("request--openai-request-model.json")
    assert body["results"][2]["profile_path"].endswith("expert--anthropic-expert-model.json")
    assert all(item["prompts_path"].endswith(".lean_ai/prompts.yaml") for item in body["results"])


def test_prewarm_role_tuning_uses_primary_fallback_for_unconfigured_optional_roles(
    client,
    tmp_path,
    monkeypatch,
):
    primary = FakeClient("ollama", "shared-model")

    monkeypatch.setattr(router_mod, "llm_client", primary)
    monkeypatch.setattr(router_mod, "request_llm_client", None)
    monkeypatch.setattr(router_mod, "expert_llm_client", None)
    monkeypatch.setattr(router_mod, "load_role_tuning_profile", lambda repo_root, scope: None)
    monkeypatch.setattr(router_mod, "profile_is_current", lambda profile, scope, repo_root=None: False)

    captured: list[tuple[str, str]] = []

    async def _ensure_primary(**kwargs):
        captured.append(("primary", kwargs["assigned_client"].model_name))
        return router_mod.prompt_scope_for_role(kwargs["assigned_client"], "primary")

    async def _ensure_request(**kwargs):
        captured.append(("request", kwargs["assigned_client"].model_name))
        return router_mod.prompt_scope_for_role(kwargs["assigned_client"], "request")

    async def _ensure_expert(**kwargs):
        captured.append(("expert", kwargs["assigned_client"].model_name))
        return router_mod.prompt_scope_for_role(kwargs["assigned_client"], "expert")

    monkeypatch.setattr(router_mod, "ensure_primary_role_tuning", _ensure_primary)
    monkeypatch.setattr(router_mod, "ensure_request_role_tuning", _ensure_request)
    monkeypatch.setattr(router_mod, "ensure_expert_role_tuning", _ensure_expert)

    resp = client.post("/api/role-tuning/prewarm", json={"repo_root": str(tmp_path)})
    assert resp.status_code == 200

    assert captured == [
        ("primary", "shared-model"),
        ("request", "shared-model"),
        ("expert", "shared-model"),
    ]


def test_prewarm_role_tuning_treats_same_model_as_distinct_role_profiles(
    client,
    tmp_path,
    monkeypatch,
):
    primary = FakeClient("ollama", "shared-model")
    request = FakeClient("ollama", "shared-model")
    expert = FakeClient("ollama", "shared-model")

    monkeypatch.setattr(router_mod, "llm_client", primary)
    monkeypatch.setattr(router_mod, "request_llm_client", request)
    monkeypatch.setattr(router_mod, "expert_llm_client", expert)
    monkeypatch.setattr(router_mod, "load_role_tuning_profile", lambda repo_root, scope: None)
    monkeypatch.setattr(router_mod, "profile_is_current", lambda profile, scope, repo_root=None: False)
    async def _ensure_primary(**kwargs):
        return router_mod.prompt_scope_for_role(kwargs["assigned_client"], "primary")

    async def _ensure_request(**kwargs):
        return router_mod.prompt_scope_for_role(kwargs["assigned_client"], "request")

    async def _ensure_expert(**kwargs):
        return router_mod.prompt_scope_for_role(kwargs["assigned_client"], "expert")

    monkeypatch.setattr(router_mod, "ensure_primary_role_tuning", _ensure_primary)
    monkeypatch.setattr(router_mod, "ensure_request_role_tuning", _ensure_request)
    monkeypatch.setattr(router_mod, "ensure_expert_role_tuning", _ensure_expert)

    resp = client.post("/api/role-tuning/prewarm", json={"repo_root": str(tmp_path)})
    assert resp.status_code == 200
    body = resp.json()

    assert [(item["role"], item["model_id"]) for item in body["results"]] == [
        ("primary", "ollama:shared-model"),
        ("request", "ollama:shared-model"),
        ("expert", "ollama:shared-model"),
    ]
    assert body["results"][0]["profile_path"] != body["results"][1]["profile_path"]
    assert body["results"][1]["profile_path"] != body["results"][2]["profile_path"]

"""Tests for the prompts management router."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lean_ai.llm.prompt_registry import registry
from lean_ai.routers.prompts import prompts_router


def _broken_phase4_prompt() -> str:
    """Return a Phase 4 prompt with one literal JSON object unescaped."""
    text = registry.get("planning.assembly_user")
    return text.replace(
        '{{"source": "src/config/handlers.ext"',
        '{"source": "src/config/handlers.ext"',
        1,
    )


def test_put_prompts_rejects_invalid_format_syntax(tmp_path) -> None:
    app = FastAPI()
    app.include_router(prompts_router, prefix="/api")

    with TestClient(app) as client:
        resp = client.put(
            "/api/prompts",
            json={
                "repo_root": str(tmp_path),
                "overrides": {
                    "planning.assembly_user": _broken_phase4_prompt(),
                },
            },
        )

    assert resp.status_code == 422
    body = resp.json()["detail"]
    assert body["message"] == (
        "Validation failed — prompt contains missing placeholders "
        "or invalid format syntax"
    )
    assert "planning.assembly_user" in body["errors"]
    assert any(
        error.startswith("Invalid format syntax:")
        for error in body["errors"]["planning.assembly_user"]
    )
    assert not (tmp_path / ".lean_ai" / "prompts.yaml").exists()

from __future__ import annotations

import pytest


class NoLlmClient:
    provider_name = "ollama"
    model_name = "gemma4:31b"

    async def chat_raw(self, *args, **kwargs):  # pragma: no cover - fail-fast guard
        raise AssertionError("chat_raw must not run while building chat messages")

    async def chat_structured(self, *args, **kwargs):  # pragma: no cover - fail-fast guard
        raise AssertionError("chat_structured must not run while building chat messages")


@pytest.mark.asyncio
async def test_build_chat_messages_does_not_tune_roles_on_chat_start(tmp_path, monkeypatch):
    from lean_ai.llm import role_tuning
    from lean_ai.routers import chat as chat_mod
    from lean_ai.routers.models import ChatRequest, WorkspaceContext

    async def _fail_calibrate(*args, **kwargs):  # pragma: no cover - fail-fast guard
        raise AssertionError("calibrate_role must not run while building chat messages")

    async def _fail_runtime_eval(*args, **kwargs):  # pragma: no cover - fail-fast guard
        raise AssertionError(
            "evaluate_runtime_prompt_reliability must not run while building chat messages"
        )

    client = NoLlmClient()
    monkeypatch.setattr(chat_mod, "request_llm_client", client)
    monkeypatch.setattr(chat_mod, "llm_client", client)
    monkeypatch.setattr(chat_mod, "refiner", None)
    monkeypatch.setattr(chat_mod, "resolve_image_handler", lambda _role: None)
    monkeypatch.setattr(role_tuning, "calibrate_role", _fail_calibrate)
    monkeypatch.setattr(role_tuning, "evaluate_runtime_prompt_reliability", _fail_runtime_eval)

    messages, refiner_result, image_descriptions = await chat_mod._build_chat_messages(
        ChatRequest(
            message="hi",
            workspace=WorkspaceContext(
                workspace_name="tmp",
                workspace_root=str(tmp_path),
            ),
        )
    )

    assert refiner_result is None
    assert image_descriptions == ""
    assert messages[0]["role"] == "system"
    assert messages[-1] == {"role": "user", "content": "hi"}


def test_chat_prompt_ignores_unsafe_scoped_override(tmp_path):
    from lean_ai.llm.prompt_registry import PromptScope, ScopedPromptOverride, registry
    from lean_ai.routers.context_helpers import build_chat_system_prompt

    scope = PromptScope(model_id="ollama:gemma4:31b", agent_role="request")
    registry.load(str(tmp_path))
    registry.save_scoped_overrides(
        str(tmp_path),
        [
            ScopedPromptOverride(
                prompt_key="chat.system",
                model_id=scope.model_id,
                agent_role=scope.agent_role,
                text=(
                    "SCOPED_BAD_OVERRIDE\n"
                    "## Suggested Agent Prompt - Grill Me Protocol\n"
                    "AVAILABLE TOOLS: create_file, edit_file"
                ),
            )
        ],
    )

    prompt = build_chat_system_prompt(
        repo_root=str(tmp_path),
        prompt_scope=scope,
        max_turns=5,
    )

    assert "SCOPED_BAD_OVERRIDE" not in prompt
    assert "AVAILABLE TOOLS: create_file, edit_file" not in prompt

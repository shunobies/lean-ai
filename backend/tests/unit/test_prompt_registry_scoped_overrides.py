from __future__ import annotations

from lean_ai.llm.prompt_registry import (
    PromptEntry,
    PromptRegistry,
    PromptScope,
    ScopedPromptOverride,
)


def _make_registry() -> PromptRegistry:
    registry = PromptRegistry()
    registry.register(
        PromptEntry(
            key="chat.system",
            category="Chat",
            name="Chat",
            description="chat prompt",
            default_text="Chat turns={CHAT_MAX_TURNS}",
            template_vars=["CHAT_MAX_TURNS"],
        )
    )
    registry.register(
        PromptEntry(
            key="fix.request_system",
            category="Fix",
            name="Request",
            description="request prompt",
            default_text="{TOOL_POLICY}\n{QUALITY_RULES}\n{WEB_SEARCH_POLICY}\n{SCRATCHPAD_POLICY}\n{COMPLETION_CONTRACT}",
            template_vars=[
                "TOOL_POLICY",
                "QUALITY_RULES",
                "WEB_SEARCH_POLICY",
                "SCRATCHPAD_POLICY",
                "COMPLETION_CONTRACT",
            ],
        )
    )
    return registry


def test_scoped_override_wins_for_matching_model(tmp_path) -> None:
    registry = _make_registry()
    scope = PromptScope(model_id="ollama:qwen3", agent_role="request")

    registry.save_overrides(str(tmp_path), {"chat.system": "global {CHAT_MAX_TURNS}"})
    registry.save_scoped_overrides(
        str(tmp_path),
        [
            ScopedPromptOverride(
                prompt_key="chat.system",
                model_id=scope.model_id,
                agent_role=scope.agent_role,
                text="scoped {CHAT_MAX_TURNS}",
            )
        ],
    )

    reloaded = _make_registry()
    reloaded.load(str(tmp_path))

    assert reloaded.format_text("chat.system", CHAT_MAX_TURNS="5") == "global 5"
    assert reloaded.format_text("chat.system", scope=scope, CHAT_MAX_TURNS="5") == "scoped 5"


def test_non_matching_model_falls_back_to_global_override(tmp_path) -> None:
    registry = _make_registry()
    registry.save_overrides(str(tmp_path), {"chat.system": "global {CHAT_MAX_TURNS}"})
    registry.save_scoped_overrides(
        str(tmp_path),
        [
            ScopedPromptOverride(
                prompt_key="chat.system",
                model_id="ollama:qwen3",
                agent_role="request",
                text="scoped {CHAT_MAX_TURNS}",
            )
        ],
    )

    reloaded = _make_registry()
    reloaded.load(str(tmp_path))

    other_scope = PromptScope(model_id="openai:gpt-4o", agent_role="request")
    assert reloaded.format_text("chat.system", scope=other_scope, CHAT_MAX_TURNS="7") == "global 7"


def test_get_all_reports_global_and_scoped_override_metadata(tmp_path) -> None:
    registry = _make_registry()
    registry.save_overrides(str(tmp_path), {"chat.system": "global {CHAT_MAX_TURNS}"})
    registry.save_scoped_overrides(
        str(tmp_path),
        [
            ScopedPromptOverride(
                prompt_key="chat.system",
                model_id="ollama:qwen3",
                agent_role="request",
                text="scoped {CHAT_MAX_TURNS}",
            )
        ],
    )

    reloaded = _make_registry()
    reloaded.load(str(tmp_path))

    prompts = {item["key"]: item for item in reloaded.get_all()}
    assert prompts["chat.system"]["is_overridden"] is True
    assert prompts["chat.system"]["has_scoped_overrides"] is True
    assert prompts["chat.system"]["scoped_override_count"] == 1
    assert prompts["fix.request_system"]["is_overridden"] is False
    assert prompts["fix.request_system"]["has_scoped_overrides"] is False

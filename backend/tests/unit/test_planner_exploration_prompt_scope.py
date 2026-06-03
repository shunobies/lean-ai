from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from lean_ai.llm.plan_schema import FileSummary
from lean_ai.llm.planner_exploration import run_phase2_exploration
from lean_ai.llm.prompt_registry import PromptScope, ScopedPromptOverride, registry


class DummyExplorer:
    model_name = "dummy-model"
    provider = "test"

    def __init__(self) -> None:
        self.messages: list[dict] | None = None

    async def chat_with_tools(self, *, messages, **kwargs):
        self.messages = messages
        return [], "No file paths identified."


@pytest.mark.asyncio
async def test_phase2_serial_formats_scope_text_without_prompt_scope_collision(
    tmp_path,
    monkeypatch,
):
    prompt_scope = PromptScope(model_id="test:dummy-model", agent_role="primary")
    registry.load(str(tmp_path))
    registry.save_scoped_overrides(
        str(tmp_path),
        [
            ScopedPromptOverride(
                prompt_key="planning.exploration_system",
                model_id=prompt_scope.model_id,
                agent_role=prompt_scope.agent_role,
                text="TUNED EXPLORATION SYSTEM",
            )
        ],
    )

    captured: dict[str, list[dict]] = {}

    async def fake_serial(**kwargs):
        captured["messages"] = kwargs["phase2_messages"]
        return "serial exploration prose"

    async def fake_synthesis(**kwargs):
        return FileSummary(), "file identification markdown"

    monkeypatch.setattr("lean_ai.llm.planner_exploration.settings.num_parallel", 1)
    monkeypatch.setattr("lean_ai.llm.planner_exploration._run_serial_exploration", fake_serial)
    monkeypatch.setattr("lean_ai.llm.planner_exploration._synthesize_file_summary", fake_synthesis)

    await run_phase2_exploration(
        task="Build the feature",
        scope="Scope markdown handoff",
        context="Project context",
        repo_root=str(tmp_path),
        session_id="sess",
        explorer=DummyExplorer(),
        phase_max_tokens=1000,
        ws=None,
        dispatcher=None,
        state_manager=MagicMock(),
        prompt_scope=prompt_scope,
    )

    assert captured["messages"][0]["content"] == "TUNED EXPLORATION SYSTEM"
    user_content = captured["messages"][1]["content"]
    assert "TASK: Build the feature" in user_content
    assert "Scope markdown handoff" in user_content
    assert "Project context" in user_content


@pytest.mark.asyncio
async def test_phase2_parallel_scan_formats_scope_text_without_prompt_scope_collision(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("lean_ai.llm.planner_exploration.settings.num_parallel", 2)
    explorer = DummyExplorer()

    file_summary, file_identification, _ = await run_phase2_exploration(
        task="Build the feature",
        scope="Scope markdown handoff",
        context="Project context",
        repo_root=str(tmp_path),
        session_id="sess",
        explorer=explorer,
        phase_max_tokens=1000,
        ws=None,
        dispatcher=None,
        state_manager=MagicMock(
            get_state=MagicMock(
                return_value=SimpleNamespace(
                    observations=[],
                    scratchpad_content="",
                    journal_entries=[],
                )
            )
        ),
        prompt_scope=None,
    )

    assert file_summary is None
    assert file_identification == "No file paths identified."
    assert explorer.messages is not None
    scan_user_content = explorer.messages[1]["content"]
    assert "PHASE 2a" in scan_user_content
    assert "TASK: Build the feature" in scan_user_content
    assert "Scope markdown handoff" in scan_user_content

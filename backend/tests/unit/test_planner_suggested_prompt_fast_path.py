"""Tests for the Suggested Agent Prompt Phase 1 fast path."""

from __future__ import annotations

import pytest

from lean_ai.llm.plan_schema import ScopeDocument
from lean_ai.llm.planner import ScopePhase, _looks_like_final_suggested_agent_prompt


class FastPathClient:
    model_name = "planner-test-model"

    def __init__(self) -> None:
        self.chat_with_tools_called = False
        self.chat_structured_called = False

    async def chat_with_tools(self, *args, **kwargs):
        self.chat_with_tools_called = True
        raise AssertionError("fast path should skip Phase 1 tool loop")

    async def chat_structured(self, *args, **kwargs):
        self.chat_structured_called = True
        return ScopeDocument(
            problem="Implement the requested planner workflow change.",
            deliverables=["Planner skips redundant scope exploration for completed handoffs."],
            in_scope=["backend/src/lean_ai/llm/planner.py"],
            out_of_scope=[],
            downstream_consumers=["workflow planning"],
            assumptions=[],
            success_criteria=["Scope synthesis still returns a ScopeDocument."],
            risks=[],
        )


def _suggested_prompt() -> str:
    return """## Suggested Agent Prompt

### Goal
Implement the planner fast path.

### Requirements
- Update `backend/src/lean_ai/llm/planner.py`.

### References
- `backend/src/lean_ai/llm/planner.py`

### Success Criteria
- Phase 1 tool exploration is skipped only for final handoffs.

### User Decisions
- Keep Phase 2 exploration active.
"""


def test_detects_complete_suggested_agent_prompt() -> None:
    assert _looks_like_final_suggested_agent_prompt(_suggested_prompt()) is True


def test_rejects_unresolved_grill_me_prompt() -> None:
    task = _suggested_prompt() + "\n\n### Grill Me Question\nWhich scope should win?"

    assert _looks_like_final_suggested_agent_prompt(task) is False


@pytest.mark.asyncio
async def test_scope_phase_fast_path_skips_tool_loop() -> None:
    client = FastPathClient()

    scope = await ScopePhase().execute(
        task=_suggested_prompt(),
        llm_client=client,
        context="repo context",
        repo_root=".",
    )

    assert client.chat_with_tools_called is False
    assert client.chat_structured_called is True
    assert scope.in_scope == ["backend/src/lean_ai/llm/planner.py"]

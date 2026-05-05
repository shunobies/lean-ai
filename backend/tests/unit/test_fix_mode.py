"""Tests for fix-mode prompt handoff behaviour."""

import pytest

from lean_ai.workflow import fix_mode


class FakeWebSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


class RecordingClient:
    def __init__(self):
        self.calls: list[dict] = []
        self.model_name = "recording-model"

    async def chat_with_tools(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return [], "Investigation found the parser edge case."
        return [], "Implemented fix."


async def _noop_executor(_name: str, _args: dict) -> str:
    return "OK"


@pytest.mark.asyncio
async def test_fix_mode_implementation_starts_from_fresh_prompt_root(monkeypatch):
    client = RecordingClient()
    ws = FakeWebSocket()

    monkeypatch.setattr(fix_mode, "load_condensed_context", lambda _repo_root: "ctx")
    monkeypatch.setattr(
        fix_mode,
        "build_fix_investigation_prompt",
        lambda _ctx, test_command="": "investigation-system",
    )
    monkeypatch.setattr(
        fix_mode,
        "build_fix_system_prompt",
        lambda _ctx, test_command="", task="": "implementation-system",
    )
    monkeypatch.setattr(fix_mode, "make_tool_executor", lambda *args, **kwargs: _noop_executor)
    monkeypatch.setattr(fix_mode, "_effective_post_commands", lambda _repo_root: {})
    monkeypatch.setattr(fix_mode, "append_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(fix_mode, "summarize_recent_events", lambda *args, **kwargs: "")
    monkeypatch.setattr(fix_mode, "read_journal", lambda _repo_root, _session_id: "")
    monkeypatch.setattr(
        fix_mode.scratchpad,
        "read_scratchpad",
        lambda _repo_root, _session_id: "scratch state",
    )
    monkeypatch.setattr(fix_mode.settings, "enable_fix_investigation", True)

    await fix_mode._run_fix(
        task="Fix the parser bug",
        repo_root="/tmp/repo",
        ws=ws,
        llm_client=client,
        context="",
        branch_name="test-branch",
        base_branch="main",
        session_id="sess-123",
        expert_llm_client=client,
        mode="fix",
    )

    assert len(client.calls) == 2

    investigation_messages = client.calls[0]["messages"]
    implementation_messages = client.calls[1]["messages"]

    assert investigation_messages[0]["content"] == "investigation-system"
    assert implementation_messages[0]["content"] == "implementation-system"
    assert all(msg["role"] in {"system", "user"} for msg in implementation_messages)
    assert not any(msg["content"] == "investigation-system" for msg in implementation_messages)
    assert any(
        "[INVESTIGATION HANDOFF]" in msg["content"] for msg in implementation_messages
    )
    assert any(
        "[SCRATCHPAD FROM PREVIOUS EXECUTION" in msg["content"]
        for msg in implementation_messages
    )

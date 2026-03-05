"""Tests for LLMClient.chat_with_tools() task reminder injection."""

import pytest

from lean_ai.llm.client import LLMClient


class FakeOllamaClient:
    """Minimal fake Ollama client for testing chat_with_tools flow."""

    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.call_count = 0
        self.messages_at_each_call: list[list[dict]] = []

    async def chat(self, **kwargs):
        self.messages_at_each_call.append(list(kwargs.get("messages", [])))
        if self.call_count < len(self.responses):
            resp = self.responses[self.call_count]
        else:
            # Default: stop (no tool calls)
            resp = {"message": {"content": "Done.", "tool_calls": []}}
        self.call_count += 1
        return resp


def _make_tool_call_response(name: str, args: dict, content: str = "") -> dict:
    """Build a fake Ollama response containing a tool call."""
    return {
        "message": {
            "content": content,
            "tool_calls": [
                {"function": {"name": name, "arguments": args}}
            ],
        }
    }


def _make_text_response(content: str = "Done.") -> dict:
    """Build a fake Ollama response with text only (no tool calls)."""
    return {"message": {"content": content, "tool_calls": []}}


def _build_client(responses: list[dict]) -> tuple[LLMClient, FakeOllamaClient]:
    """Create an LLMClient backed by a FakeOllamaClient."""
    client = LLMClient.__new__(LLMClient)
    fake = FakeOllamaClient(responses)
    client._client = fake
    client._model = "test-model"
    client._max_tokens = 1024
    client._context_window = 4096
    client._temperature = 0.0
    return client, fake


async def _noop_executor(name: str, args: dict) -> str:
    return f"OK: {name}"


@pytest.mark.asyncio
async def test_reminder_injected_at_interval():
    """Reminder should be injected after every reminder_interval turns."""
    # 12 turns of tool calls, then stop
    responses = [
        _make_tool_call_response("edit_file", {"path": "f.py", "search": "a", "replace": "b"})
        for _ in range(12)
    ] + [_make_text_response()]

    client, fake = _build_client(responses)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do stuff"},
    ]

    await client.chat_with_tools(
        messages=messages,
        tools=[],
        tool_executor_fn=_noop_executor,
        max_turns=20,
        task_reminder="REMINDER: do stuff",
        reminder_interval=5,
    )

    # Count reminder messages in the final message list
    reminders = [m for m in messages if m.get("content") == "REMINDER: do stuff"]
    # Should fire at turn 5 and turn 10 (not at 15 since we stopped at 13)
    assert len(reminders) == 2


@pytest.mark.asyncio
async def test_reminder_not_injected_on_final_turn():
    """Reminder should not be injected if turn+1 == max_turns."""
    # Exactly 10 turns of tool calls — reminder_interval=10 but turn 10 is the last
    responses = [
        _make_tool_call_response("edit_file", {"path": "f.py", "search": "a", "replace": "b"})
        for _ in range(10)
    ]

    client, fake = _build_client(responses)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do stuff"},
    ]

    await client.chat_with_tools(
        messages=messages,
        tools=[],
        tool_executor_fn=_noop_executor,
        max_turns=10,
        task_reminder="REMINDER: do stuff",
        reminder_interval=10,
    )

    reminders = [m for m in messages if m.get("content") == "REMINDER: do stuff"]
    assert len(reminders) == 0


@pytest.mark.asyncio
async def test_no_reminder_when_none():
    """No reminder should be injected when task_reminder is None."""
    responses = [
        _make_tool_call_response("edit_file", {"path": "f.py", "search": "a", "replace": "b"})
        for _ in range(15)
    ] + [_make_text_response()]

    client, fake = _build_client(responses)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do stuff"},
    ]

    await client.chat_with_tools(
        messages=messages,
        tools=[],
        tool_executor_fn=_noop_executor,
        max_turns=20,
        task_reminder=None,
        reminder_interval=5,
    )

    # Only system, user, and tool messages — no reminders
    user_msgs = [m for m in messages if m["role"] == "user"]
    assert len(user_msgs) == 1  # Only the original


@pytest.mark.asyncio
async def test_reminder_interval_zero_disables():
    """reminder_interval=0 should disable reminders."""
    responses = [
        _make_tool_call_response("edit_file", {"path": "f.py", "search": "a", "replace": "b"})
        for _ in range(15)
    ] + [_make_text_response()]

    client, fake = _build_client(responses)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do stuff"},
    ]

    await client.chat_with_tools(
        messages=messages,
        tools=[],
        tool_executor_fn=_noop_executor,
        max_turns=20,
        task_reminder="REMINDER: do stuff",
        reminder_interval=0,
    )

    reminders = [m for m in messages if m.get("content") == "REMINDER: do stuff"]
    assert len(reminders) == 0


@pytest.mark.asyncio
async def test_reminder_is_user_role():
    """Reminder messages should have role=user."""
    responses = [
        _make_tool_call_response("edit_file", {"path": "f.py", "search": "a", "replace": "b"})
        for _ in range(6)
    ] + [_make_text_response()]

    client, fake = _build_client(responses)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do stuff"},
    ]

    await client.chat_with_tools(
        messages=messages,
        tools=[],
        tool_executor_fn=_noop_executor,
        max_turns=20,
        task_reminder="REMINDER: do stuff",
        reminder_interval=3,
    )

    reminders = [m for m in messages if m.get("content") == "REMINDER: do stuff"]
    assert len(reminders) == 2  # At turn 3 and turn 6
    for r in reminders:
        assert r["role"] == "user"


@pytest.mark.asyncio
async def test_callable_reminder_invoked_at_interval():
    """When task_reminder is a callable, it should be called at each injection."""
    call_count = 0

    def dynamic_reminder() -> str:
        nonlocal call_count
        call_count += 1
        return f"REMINDER #{call_count}"

    responses = [
        _make_tool_call_response(
            "edit_file", {"path": "f.py", "search": "a", "replace": "b"},
        )
        for _ in range(12)
    ] + [_make_text_response()]

    client, _fake = _build_client(responses)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do stuff"},
    ]

    await client.chat_with_tools(
        messages=messages,
        tools=[],
        tool_executor_fn=_noop_executor,
        max_turns=20,
        task_reminder=dynamic_reminder,
        reminder_interval=5,
    )

    # Should have been called at turn 5 and turn 10
    assert call_count == 2
    # Each call should produce a distinct, fresh value
    reminder_msgs = [
        m for m in messages
        if m["role"] == "user" and "REMINDER #" in m.get("content", "")
    ]
    assert len(reminder_msgs) == 2
    assert reminder_msgs[0]["content"] == "REMINDER #1"
    assert reminder_msgs[1]["content"] == "REMINDER #2"

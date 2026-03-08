"""Tests for LLMClient.chat_with_tools() — reminders, loop detection, compression, sanitization."""

import pytest

from lean_ai.llm.client import LLMClient, _sanitize_messages


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
            # Default: signal completion via task_complete
            resp = _make_task_complete_response("Default done.")
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


def _make_task_complete_response(summary: str = "Done.") -> dict:
    """Build a fake Ollama response with a task_complete tool call."""
    return {
        "message": {
            "content": "",
            "tool_calls": [
                {"function": {"name": "task_complete", "arguments": {"summary": summary}}}
            ],
        }
    }


def _build_client(responses: list[dict]) -> tuple[LLMClient, FakeOllamaClient]:
    """Create an LLMClient backed by a FakeOllamaClient."""
    client = LLMClient.__new__(LLMClient)
    fake = FakeOllamaClient(responses)
    client._client = fake
    client._model = "test-model"
    client._max_tokens = 1024
    client._context_window = 4096
    client._temperature = 0.0
    client._top_p = 0.8
    client._top_k = 20
    client._repeat_penalty = 1.05
    return client, fake


async def _noop_executor(name: str, args: dict) -> str:
    return f"OK: {name}"


@pytest.mark.asyncio
async def test_reminder_injected_at_interval():
    """Reminder should be injected after every reminder_interval turns."""
    # 12 turns of tool calls, then task_complete
    responses = [
        _make_tool_call_response("edit_file", {"path": "f.py", "search": "a", "replace": "b"})
        for _ in range(12)
    ] + [_make_task_complete_response()]

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
    ] + [_make_task_complete_response()]

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
        loop_detection_threshold=0,  # Disable to isolate reminder test
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
    ] + [_make_task_complete_response()]

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
    ] + [_make_task_complete_response()]

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
    ] + [_make_task_complete_response()]

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


# ── Loop detection tests ──


@pytest.mark.asyncio
async def test_loop_detection_triggers_at_threshold():
    """Warning injected after N consecutive identical tool calls."""
    # 5 identical calls, threshold=3 → warning after 3rd
    responses = [
        _make_tool_call_response("edit_file", {"path": "f.py", "search": "a", "replace": "b"})
        for _ in range(5)
    ] + [_make_task_complete_response()]

    client, _fake = _build_client(responses)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do stuff"},
    ]

    await client.chat_with_tools(
        messages=messages,
        tools=[],
        tool_executor_fn=_noop_executor,
        max_turns=10,
        loop_detection_threshold=3,
    )

    warnings = [
        m for m in messages
        if m["role"] == "user" and "identical arguments" in m.get("content", "")
    ]
    assert len(warnings) >= 1
    assert "edit_file" in warnings[0]["content"]


@pytest.mark.asyncio
async def test_loop_detection_resets_on_different_call():
    """Counter resets when a different tool call is seen."""
    responses = [
        # 2 identical
        _make_tool_call_response("edit_file", {"path": "a.py", "search": "x", "replace": "y"}),
        _make_tool_call_response("edit_file", {"path": "a.py", "search": "x", "replace": "y"}),
        # 1 different
        _make_tool_call_response("read_file", {"path": "b.py"}),
        # 2 identical (same as first)
        _make_tool_call_response("edit_file", {"path": "a.py", "search": "x", "replace": "y"}),
        _make_tool_call_response("edit_file", {"path": "a.py", "search": "x", "replace": "y"}),
        _make_task_complete_response(),
    ]

    client, _fake = _build_client(responses)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do stuff"},
    ]

    await client.chat_with_tools(
        messages=messages,
        tools=[],
        tool_executor_fn=_noop_executor,
        max_turns=10,
        loop_detection_threshold=3,
    )

    warnings = [
        m for m in messages
        if m["role"] == "user" and "identical arguments" in m.get("content", "")
    ]
    # Never hit 3 consecutive — no warning
    assert len(warnings) == 0


@pytest.mark.asyncio
async def test_loop_detection_threshold_zero_disables():
    """threshold=0 disables loop detection."""
    responses = [
        _make_tool_call_response("edit_file", {"path": "f.py", "search": "a", "replace": "b"})
        for _ in range(10)
    ] + [_make_task_complete_response()]

    client, _fake = _build_client(responses)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do stuff"},
    ]

    await client.chat_with_tools(
        messages=messages,
        tools=[],
        tool_executor_fn=_noop_executor,
        max_turns=15,
        loop_detection_threshold=0,
    )

    warnings = [
        m for m in messages
        if m["role"] == "user" and "identical arguments" in m.get("content", "")
    ]
    assert len(warnings) == 0


# ── task_complete tests ──


@pytest.mark.asyncio
async def test_task_complete_exits_loop():
    """Model calling task_complete should exit the loop and capture the summary."""
    responses = [
        _make_tool_call_response("read_file", {"path": "f.py"}),
        _make_tool_call_response("edit_file", {"path": "f.py", "search": "a", "replace": "b"}),
        _make_task_complete_response("Edited f.py: replaced a with b."),
    ]

    client, fake = _build_client(responses)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do stuff"},
    ]

    executed, explanation = await client.chat_with_tools(
        messages=messages,
        tools=[],
        tool_executor_fn=_noop_executor,
        max_turns=10,
    )

    # Should have executed 2 real tools (not task_complete)
    assert len(executed) == 2
    assert executed[0].tool_name == "read_file"
    assert executed[1].tool_name == "edit_file"
    # Summary should be captured in the explanation
    assert "Edited f.py: replaced a with b." in explanation
    # Loop should have exited on turn 3
    assert fake.call_count == 3


@pytest.mark.asyncio
async def test_task_complete_with_other_tools():
    """task_complete mixed with other tools: execute others, then exit."""
    # Response with edit_file AND task_complete in the same turn
    responses = [
        _make_tool_call_response("read_file", {"path": "f.py"}),
        {
            "message": {
                "content": "",
                "tool_calls": [
                    {"function": {"name": "edit_file", "arguments": {
                        "path": "f.py", "search": "a", "replace": "b",
                    }}},
                    {"function": {"name": "task_complete", "arguments": {
                        "summary": "All done.",
                    }}},
                ],
            }
        },
    ]

    client, fake = _build_client(responses)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do stuff"},
    ]

    executed, explanation = await client.chat_with_tools(
        messages=messages,
        tools=[],
        tool_executor_fn=_noop_executor,
        max_turns=10,
    )

    # Should have executed read_file and edit_file (not task_complete)
    assert len(executed) == 2
    assert executed[0].tool_name == "read_file"
    assert executed[1].tool_name == "edit_file"
    # Summary captured
    assert "All done." in explanation
    # Loop exited after turn 2
    assert fake.call_count == 2


@pytest.mark.asyncio
async def test_text_only_continues_loop():
    """Text-only responses should not exit the loop — model must call task_complete."""
    responses = [
        _make_tool_call_response("read_file", {"path": "f.py"}),
        _make_text_response("Let me think about this..."),
        _make_text_response("I'll make the change now."),
        _make_task_complete_response("Done thinking and working."),
    ]

    client, fake = _build_client(responses)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do stuff"},
    ]

    executed, explanation = await client.chat_with_tools(
        messages=messages,
        tools=[],
        tool_executor_fn=_noop_executor,
        max_turns=10,
    )

    # Loop should NOT have exited on the text-only turns
    assert fake.call_count == 4
    # Only read_file should have been executed (text-only turns have no tools)
    assert len(executed) == 1
    assert executed[0].tool_name == "read_file"
    # All text content should be in explanation
    assert "Let me think about this..." in explanation
    assert "I'll make the change now." in explanation
    assert "Done thinking and working." in explanation


@pytest.mark.asyncio
async def test_consecutive_text_only_safety_exit():
    """3+ consecutive text-only responses should trigger safety exit."""
    responses = [
        _make_tool_call_response("read_file", {"path": "f.py"}),
        _make_text_response("Thinking..."),
        _make_text_response("Still thinking..."),
        _make_text_response("Almost done thinking..."),
        # This should never be reached — loop exits after 3 text-only
        _make_tool_call_response("edit_file", {"path": "f.py", "search": "a", "replace": "b"}),
    ]

    client, fake = _build_client(responses)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do stuff"},
    ]

    executed, explanation = await client.chat_with_tools(
        messages=messages,
        tools=[],
        tool_executor_fn=_noop_executor,
        max_turns=10,
    )

    # Should have called LLM 4 times: 1 tool + 3 text-only
    assert fake.call_count == 4
    # Only read_file executed
    assert len(executed) == 1
    assert executed[0].tool_name == "read_file"


@pytest.mark.asyncio
async def test_nudge_removed():
    """Text-only on first turn should NOT inject a nudge — just continue."""
    responses = [
        _make_text_response("Let me explain what I'll do..."),
        _make_task_complete_response("Explained and done."),
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
    )

    # No nudge message should exist in messages
    nudge_msgs = [
        m for m in messages
        if m["role"] == "user" and "not complete" in m.get("content", "").lower()
    ]
    assert len(nudge_msgs) == 0
    # Loop continued past text-only turn and exited on task_complete
    assert fake.call_count == 2


# ── Sanitize messages tests ──


def test_sanitize_preserves_valid_messages():
    """Well-formed conversation passes through unchanged."""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do stuff"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "read_file", "arguments": {"path": "f.py"}}},
        ]},
        {"role": "tool", "content": "file contents"},
        {"role": "assistant", "content": "Done."},
    ]
    result = _sanitize_messages(msgs)
    assert len(result) == 5
    assert result[0]["role"] == "system"
    assert result[4]["content"] == "Done."


def test_sanitize_removes_orphaned_tool_calls():
    """Assistant with 2 tool_calls but only 1 result → trimmed to 1."""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "read_file", "arguments": {"path": "a.py"}}},
            {"function": {"name": "read_file", "arguments": {"path": "b.py"}}},
        ]},
        {"role": "tool", "content": "contents of a.py"},
        {"role": "user", "content": "next"},
    ]
    result = _sanitize_messages(msgs)
    # Assistant should have only 1 tool_call now
    assistant = result[1]
    assert len(assistant["tool_calls"]) == 1
    assert assistant["tool_calls"][0]["function"]["arguments"]["path"] == "a.py"


def test_sanitize_removes_assistant_with_no_tool_results():
    """Assistant with tool_calls but zero results → removed."""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "I'll read the file", "tool_calls": [
            {"function": {"name": "read_file", "arguments": {"path": "f.py"}}},
        ]},
        {"role": "user", "content": "next"},
    ]
    result = _sanitize_messages(msgs)
    assert len(result) == 2  # system + user
    assert result[0]["role"] == "system"
    assert result[1]["role"] == "user"


def test_sanitize_merges_consecutive_assistants():
    """Two adjacent assistant messages → merged into one."""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "First part."},
        {"role": "assistant", "content": "Second part."},
        {"role": "user", "content": "ok"},
    ]
    result = _sanitize_messages(msgs)
    assert len(result) == 3
    assert result[1]["role"] == "assistant"
    assert "First part." in result[1]["content"]
    assert "Second part." in result[1]["content"]


def test_sanitize_handles_empty_list():
    """Empty list returns empty list."""
    assert _sanitize_messages([]) == []


# ── Compression tests ──


@pytest.mark.asyncio
async def test_compression_triggers_at_threshold():
    """Messages exceeding threshold get compressed."""
    client, fake = _build_client([])
    # Small context window — total content ~860 chars ≈ 215 tokens.
    # Threshold of 0.7 * 200 = 140 tokens, so 215 > 140 triggers compression.
    client._context_window = 200

    # Override chat_raw to return a fake summary
    async def fake_chat_raw(messages, max_tokens=None):
        return "Summary: edited files a.py and b.py."

    client.chat_raw = fake_chat_raw

    tc = [{"function": {"name": "edit_file", "arguments": {"path": "a.py"}}}]
    messages = [
        {"role": "system", "content": "System prompt here"},
        {"role": "user", "content": "Task: " + "x" * 200},
        {"role": "assistant", "content": "", "tool_calls": tc},
        {"role": "tool", "content": "Result: " + "z" * 200},
        {"role": "user", "content": "Continue " + "c" * 200},
        {"role": "assistant", "content": "", "tool_calls": tc},
        {"role": "tool", "content": "Done: " + "v" * 200},
        {"role": "user", "content": "Recent message"},
    ]
    original_len = len(messages)

    await client._maybe_compress(messages, threshold=0.7, preserve=0.3)

    # System prompt should still be first
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "System prompt here"
    # Should have been compressed — fewer messages now
    assert len(messages) < original_len
    # Should contain the summary
    summary_msgs = [
        m for m in messages
        if "[Previous conversation summary]" in m.get("content", "")
    ]
    assert len(summary_msgs) == 1


@pytest.mark.asyncio
async def test_compression_preserves_system_prompt():
    """System prompt remains at index 0 after compression."""
    client, _fake = _build_client([])
    client._context_window = 200

    async def fake_chat_raw(messages, max_tokens=None):
        return "Compressed summary."

    client.chat_raw = fake_chat_raw

    messages = [
        {"role": "system", "content": "Important system prompt"},
        {"role": "user", "content": "x" * 300},
        {"role": "assistant", "content": "y" * 300},
        {"role": "user", "content": "recent"},
    ]

    await client._maybe_compress(messages, threshold=0.7, preserve=0.3)

    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "Important system prompt"


@pytest.mark.asyncio
async def test_compression_skips_below_threshold():
    """Messages below threshold are not compressed."""
    client, _fake = _build_client([])
    client._context_window = 100000  # Large window

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "short message"},
        {"role": "assistant", "content": "short reply"},
    ]
    original_len = len(messages)

    await client._maybe_compress(messages, threshold=0.7, preserve=0.3)

    assert len(messages) == original_len


@pytest.mark.asyncio
async def test_compression_failure_doesnt_break():
    """If chat_raw raises, compression is skipped gracefully."""
    client, _fake = _build_client([])
    client._context_window = 200

    async def failing_chat_raw(messages, max_tokens=None):
        raise ConnectionError("Ollama down")

    client.chat_raw = failing_chat_raw

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "x" * 300},
        {"role": "assistant", "content": "y" * 300},
        {"role": "user", "content": "recent"},
    ]
    original_len = len(messages)

    # Should not raise
    await client._maybe_compress(messages, threshold=0.7, preserve=0.3)

    # Messages unchanged
    assert len(messages) == original_len

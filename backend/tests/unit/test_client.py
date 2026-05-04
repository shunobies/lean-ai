"""Tests for LLMClient.chat_with_tools() — reminders, loop detection, compression, sanitization."""

import logging

import pytest

from lean_ai.llm.base import LLMMetrics, LLMProvider, ToolCallInfo
from lean_ai.llm.client import _sanitize_messages
from lean_ai.llm.facade import LLMClient


class FakeProvider(LLMProvider):
    """Minimal fake LLM provider for testing the chat_with_tools orchestration loop."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.call_count = 0
        self.messages_at_each_call: list[list[dict]] = []
        self._context_window_val = 4096
        self._max_tokens_val = 1024

    @property
    def model_name(self) -> str:
        return "test-model"

    @property
    def context_window(self) -> int:
        return self._context_window_val

    @property
    def max_tokens(self) -> int:
        return self._max_tokens_val

    async def chat_raw(self, messages, temperature=None, max_tokens=None):
        return "", LLMMetrics()

    async def chat_structured(self, messages, schema, temperature=None, max_tokens=None):
        raise NotImplementedError

    async def chat_with_tools_single(
        self, messages, tools, max_tokens=None, *, stream_callback=None, thinking_callback=None
    ):
        self.messages_at_each_call.append(list(messages))
        if self.call_count < len(self.responses):
            resp = self.responses[self.call_count]
        else:
            resp = _make_task_complete_response()
        self.call_count += 1
        return resp

    async def chat_stream(self, messages, temperature=None, max_tokens=None):
        yield ""

    async def check_health(self):
        return True


def _make_tool_call_response(
    name: str,
    args: dict,
    content: str = "",
) -> tuple[str, list[ToolCallInfo], LLMMetrics]:
    """Build a fake provider response containing a tool call."""
    return (
        content,
        [ToolCallInfo(name=name, arguments=args)],
        LLMMetrics(),
    )


def _make_text_response(
    content: str = "Done.",
    stop_reason: str | None = None,
) -> tuple[str, list[ToolCallInfo], LLMMetrics]:
    """Build a fake provider response with text only (no tool calls)."""
    return content, [], LLMMetrics(stop_reason=stop_reason)


def _make_task_complete_response(
    summary: str = "Done.",
) -> tuple[str, list[ToolCallInfo], LLMMetrics]:
    """Build a fake provider response with a task_complete tool call."""
    return (
        "",
        [ToolCallInfo(name="task_complete", arguments={"summary": summary})],
        LLMMetrics(),
    )


def _build_client(responses: list) -> tuple[LLMClient, FakeProvider]:
    """Create an LLMClient backed by a FakeProvider."""
    fake = FakeProvider(responses)
    client = LLMClient(provider=fake)
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
            "edit_file",
            {"path": "f.py", "search": "a", "replace": "b"},
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
        m for m in messages if m["role"] == "user" and "REMINDER #" in m.get("content", "")
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
        m for m in messages if m["role"] == "user" and "Loop detected" in m.get("content", "")
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
        m for m in messages if m["role"] == "user" and "Loop detected" in m.get("content", "")
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
        m for m in messages if m["role"] == "user" and "Loop detected" in m.get("content", "")
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
        (
            "",
            [
                ToolCallInfo(
                    name="edit_file",
                    arguments={
                        "path": "f.py",
                        "search": "a",
                        "replace": "b",
                    },
                ),
                ToolCallInfo(
                    name="task_complete",
                    arguments={
                        "summary": "All done.",
                    },
                ),
            ],
            LLMMetrics(),
        ),
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
        m
        for m in messages
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
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "read_file", "arguments": {"path": "f.py"}}},
            ],
        },
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
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "read_file", "arguments": {"path": "a.py"}}},
                {"function": {"name": "read_file", "arguments": {"path": "b.py"}}},
            ],
        },
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
        {
            "role": "assistant",
            "content": "I'll read the file",
            "tool_calls": [
                {"function": {"name": "read_file", "arguments": {"path": "f.py"}}},
            ],
        },
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


# ── Schema injection tests ──


def _dar_sample():
    """Build the DesignAndRisks schema lazily to keep imports cheap."""
    from lean_ai.llm.plan_schema import DesignAndRisks

    return DesignAndRisks


def test_inject_schema_appends_to_existing_system_message():
    """Existing system prompt is preserved; schema JSON appended after it."""
    from lean_ai.llm.client import _inject_schema_into_messages

    messages = [
        {"role": "system", "content": "Use your knowledge of architecture."},
        {"role": "user", "content": "task"},
    ]
    out = _inject_schema_into_messages(messages, _dar_sample())

    assert len(out) == 2
    assert out[0]["role"] == "system"
    # Original content is preserved
    assert "Use your knowledge of architecture." in out[0]["content"]
    # Schema framing is appended
    assert "JSON object that matches this schema" in out[0]["content"]
    # Schema itself is present with nested type names
    assert "naming_conventions" in out[0]["content"]
    assert "change_designs" in out[0]["content"]
    assert "critical_risks" in out[0]["content"]
    # User message is unchanged
    assert out[1] == {"role": "user", "content": "task"}


def test_inject_schema_prepends_when_no_system_message():
    """When no system message exists, one is inserted at position 0."""
    from lean_ai.llm.client import _inject_schema_into_messages

    messages = [{"role": "user", "content": "hey"}]
    out = _inject_schema_into_messages(messages, _dar_sample())

    assert len(out) == 2
    assert out[0]["role"] == "system"
    assert out[1]["role"] == "user"
    assert "Produce JSON matching" in out[0]["content"]


def test_inject_schema_only_touches_first_system_message():
    """If multiple system messages are present, only the first gets the schema."""
    from lean_ai.llm.client import _inject_schema_into_messages

    messages = [
        {"role": "system", "content": "first"},
        {"role": "user", "content": "q"},
        {"role": "system", "content": "second"},
    ]
    out = _inject_schema_into_messages(messages, _dar_sample())

    assert "```json" in out[0]["content"]
    assert out[2]["content"] == "second"


def test_inject_schema_does_not_mutate_original_messages():
    """Source messages and their dicts must remain unchanged."""
    from lean_ai.llm.client import _inject_schema_into_messages

    original_system = {"role": "system", "content": "original"}
    messages = [original_system, {"role": "user", "content": "q"}]

    _inject_schema_into_messages(messages, _dar_sample())

    assert original_system["content"] == "original"
    assert len(messages) == 2  # original list unchanged


def test_inject_schema_covers_every_phase_schema():
    """Smoke-test that every structured-call schema round-trips through
    the injector without error and produces a non-trivial payload."""
    from lean_ai.llm.client import _inject_schema_into_messages
    from lean_ai.llm.plan_schema import (
        DesignAndRisks,
        ExecutionPlan,
        FileSummary,
        ScopeDocument,
        VerificationPlan,
    )

    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "u"}]
    for schema in (
        ScopeDocument,
        FileSummary,
        DesignAndRisks,
        ExecutionPlan,
        VerificationPlan,
    ):
        out = _inject_schema_into_messages(msgs, schema)
        assert schema.__name__ in out[0]["content"], (
            f"{schema.__name__} missing from injected system prompt"
        )
        assert len(out[0]["content"]) > len("sys") + 100


# ── Context refresh tests ──


@pytest.mark.asyncio
async def test_context_refresh_triggers_at_threshold():
    """Messages exceeding threshold trigger context refresh via callback."""
    client, _fake = _build_client([])
    # Small context window — force via a custom provider
    _fake._context_window_val = 200

    refresh_called = False

    def on_refresh(msgs):
        nonlocal refresh_called
        refresh_called = True
        return [
            {"role": "system", "content": "Fresh system prompt"},
            {"role": "user", "content": "Original task"},
            {"role": "user", "content": "[CONTEXT REFRESHED]\nScratchpad"},
        ]

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

    result = await client._maybe_refresh_context(
        messages,
        threshold=0.7,
        on_context_refresh=on_refresh,
    )

    assert result is True
    assert refresh_called
    assert messages[0]["content"] == "Fresh system prompt"
    assert len(messages) == 3


@pytest.mark.asyncio
async def test_context_refresh_rebuilds_from_callback():
    """Callback return value completely replaces the message list."""
    client, _fake = _build_client([])
    _fake._context_window_val = 200

    def on_refresh(msgs):
        # Callback receives the old messages
        assert msgs[0]["content"] == "Old system prompt"
        return [
            {"role": "system", "content": "New system prompt"},
            {"role": "user", "content": "Task"},
        ]

    messages = [
        {"role": "system", "content": "Old system prompt"},
        {"role": "user", "content": "x" * 300},
        {"role": "assistant", "content": "y" * 300},
        {"role": "user", "content": "recent"},
    ]

    result = await client._maybe_refresh_context(
        messages,
        threshold=0.7,
        on_context_refresh=on_refresh,
    )

    assert result is True
    assert messages[0]["content"] == "New system prompt"
    assert messages[1]["content"] == "Task"
    assert len(messages) == 2


@pytest.mark.asyncio
async def test_context_refresh_skips_below_threshold():
    """Messages below threshold are not refreshed."""
    client, _fake = _build_client([])
    _fake._context_window_val = 100000  # Large window

    refresh_called = False

    def on_refresh(msgs):
        nonlocal refresh_called
        refresh_called = True
        return msgs

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "short message"},
        {"role": "assistant", "content": "short reply"},
    ]
    original_len = len(messages)

    result = await client._maybe_refresh_context(
        messages,
        threshold=0.7,
        on_context_refresh=on_refresh,
    )

    assert result is False
    assert not refresh_called
    assert len(messages) == original_len


@pytest.mark.asyncio
async def test_context_refresh_no_callback_is_noop():
    """Without a callback, refresh returns False and messages are unchanged."""
    client, _fake = _build_client([])
    _fake._context_window_val = 200

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "x" * 300},
        {"role": "assistant", "content": "y" * 300},
        {"role": "user", "content": "recent"},
    ]
    original_len = len(messages)

    result = await client._maybe_refresh_context(
        messages,
        threshold=0.7,
        on_context_refresh=None,
    )

    assert result is False
    assert len(messages) == original_len


@pytest.mark.asyncio
async def test_context_refresh_callback_exception_handled():
    """If the callback raises, refresh is skipped gracefully."""
    client, _fake = _build_client([])
    _fake._context_window_val = 200

    def on_refresh(msgs):
        raise RuntimeError("disk read failed")

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "x" * 300},
        {"role": "assistant", "content": "y" * 300},
        {"role": "user", "content": "recent"},
    ]
    original_len = len(messages)

    # Should not raise
    result = await client._maybe_refresh_context(
        messages,
        threshold=0.7,
        on_context_refresh=on_refresh,
    )

    assert result is False
    assert len(messages) == original_len


# ── Custom nudge tests ──


@pytest.mark.asyncio
async def test_custom_nudge_used_on_text_only():
    """When text_only_nudge is provided, it replaces the default nudge."""
    custom_nudge = "STOP. Call search_internet now."
    responses = [
        _make_text_response("Let me plan..."),
        _make_task_complete_response("Done."),
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
        text_only_nudge=custom_nudge,
    )

    nudge_msgs = [m for m in messages if m["role"] == "user" and m.get("content") == custom_nudge]
    assert len(nudge_msgs) == 1


@pytest.mark.asyncio
async def test_default_nudge_when_no_custom():
    """Without text_only_nudge, the default generic nudge is used."""
    responses = [
        _make_text_response("Let me plan..."),
        _make_task_complete_response("Done."),
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
    )

    nudge_msgs = [
        m for m in messages if m["role"] == "user" and "task_complete" in m.get("content", "")
    ]
    assert len(nudge_msgs) == 1


@pytest.mark.asyncio
async def test_text_only_nudge_logged(caplog):
    """Backend logs the injected text-only nudge so the harness action is visible."""
    responses = [
        _make_text_response("Let me plan..."),
        _make_task_complete_response("Done."),
    ]

    client, _fake = _build_client(responses)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do stuff"},
    ]

    caplog.set_level(logging.INFO, logger="lean_ai.llm.facade")

    await client.chat_with_tools(
        messages=messages,
        tools=[],
        tool_executor_fn=_noop_executor,
        max_turns=10,
    )

    assert "LLM harness message injected: kind=nudge key=nudge.text_only" in caplog.text


# ── Truncation handling tests ──


@pytest.mark.asyncio
async def test_truncated_response_not_counted_as_text_only():
    """Truncated responses should not count toward text-only exit limit."""
    responses = [
        # 3 truncated responses — would exit if counted as text-only
        _make_text_response("I'll help...", stop_reason="length"),
        _make_text_response("Let me...", stop_reason="length"),
        _make_text_response("Starting...", stop_reason="length"),
        # 4th turn: model calls a tool
        _make_tool_call_response("read_file", {"path": "f.py"}),
        _make_task_complete_response("Done."),
    ]

    client, fake = _build_client(responses)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do stuff"},
    ]

    executed, _explanation = await client.chat_with_tools(
        messages=messages,
        tools=[],
        tool_executor_fn=_noop_executor,
        max_turns=10,
    )

    # Should NOT have exited after 3 truncated responses
    assert fake.call_count == 5
    assert len(executed) == 1
    assert executed[0].tool_name == "read_file"


@pytest.mark.asyncio
async def test_truncated_response_injects_truncation_nudge():
    """Truncated response should inject truncation-specific nudge."""
    responses = [
        _make_text_response("I'll help...", stop_reason="length"),
        _make_task_complete_response("Done."),
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
    )

    truncation_nudges = [
        m for m in messages if m["role"] == "user" and "truncated" in m.get("content", "")
    ]
    assert len(truncation_nudges) == 1
    assert "ONLY the tool call" in truncation_nudges[0]["content"]


@pytest.mark.asyncio
async def test_normal_text_only_still_exits_at_limit():
    """Non-truncated text-only responses still exit after 3 (regression test)."""
    responses = [
        _make_text_response("Thinking..."),
        _make_text_response("Still thinking..."),
        _make_text_response("Almost done..."),
        # Should never reach this
        _make_task_complete_response("Done."),
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

    # Should exit after 3 text-only responses
    assert fake.call_count == 3


@pytest.mark.asyncio
async def test_consecutive_truncation_cap():
    """Repeated truncation should exit at the safety cap (5)."""
    responses = [
        _make_text_response(f"Truncated {i}...", stop_reason="length") for i in range(7)
    ] + [_make_task_complete_response("Done.")]

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
    )

    # Should exit after 5 truncated responses, not continue to 7
    assert fake.call_count == 5


@pytest.mark.asyncio
async def test_anthropic_max_tokens_treated_as_truncation():
    """Anthropic's 'max_tokens' stop_reason should be treated as truncation."""
    responses = [
        _make_text_response("Truncated...", stop_reason="max_tokens"),
        _make_tool_call_response("read_file", {"path": "f.py"}),
        _make_task_complete_response("Done."),
    ]

    client, fake = _build_client(responses)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do stuff"},
    ]

    executed, _explanation = await client.chat_with_tools(
        messages=messages,
        tools=[],
        tool_executor_fn=_noop_executor,
        max_turns=10,
    )

    # Should have continued past the truncated response
    assert fake.call_count == 3
    assert len(executed) == 1


@pytest.mark.asyncio
async def test_reasoning_budget_nudge_logged(caplog):
    """Backend logs reasoning-budget nudges so 'stop thinking and answer' is traceable."""
    responses = [
        (
            "Partial answer",
            [],
            LLMMetrics(
                thinking_budget_exceeded=True,
                thinking_token_count=321,
            ),
        ),
        _make_task_complete_response("Done."),
    ]

    client, _fake = _build_client(responses)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do stuff"},
    ]

    caplog.set_level(logging.WARNING, logger="lean_ai.llm.facade")

    await client.chat_with_tools(
        messages=messages,
        tools=[],
        tool_executor_fn=_noop_executor,
        max_turns=10,
    )

    assert (
        "LLM harness message injected: kind=nudge key=nudge.reasoning_budget_exceeded"
        in caplog.text
    )
    assert "thinking_tokens=321" in caplog.text


@pytest.mark.asyncio
async def test_task_complete_validator_rejection_logged(caplog):
    """Harness/LLM completion conflicts are logged when task_complete is rejected."""
    responses = [
        _make_task_complete_response("Done too early."),
        _make_task_complete_response("Done for real."),
    ]

    client, _fake = _build_client(responses)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do stuff"},
    ]

    caplog.set_level(logging.WARNING, logger="lean_ai.llm.facade")

    rejections = iter(["Need at least one file edit before completion.", None])

    def _validator():
        return next(rejections)

    await client.chat_with_tools(
        messages=messages,
        tools=[],
        tool_executor_fn=_noop_executor,
        max_turns=10,
        task_complete_validator=_validator,
    )

    assert "LLM tool result overridden: key=task_complete_validator_rejection" in caplog.text
    assert "Need at least one file edit before completion." in caplog.text

"""Tests for chat tool support: CHAT_TOOLS, tool executor, text_only_exit_count, streaming."""

import pytest

from lean_ai.llm.base import LLMMetrics, LLMProvider, ToolCallInfo
from lean_ai.llm.facade import LLMClient
from lean_ai.llm.tool_definitions import CHAT_TOOLS

# ── FakeProvider for testing ────────────────────────────────────────


class FakeProvider(LLMProvider):
    """Minimal fake LLM provider for chat tool tests."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.call_count = 0
        self.messages_at_each_call: list[list[dict]] = []
        self._context_window_val = 4096
        self._max_tokens_val = 1024
        self.stream_callbacks_received: list[bool] = []

    @property
    def model_name(self) -> str:
        return "test-model"

    @property
    def context_window(self) -> int:
        return self._context_window_val

    @property
    def max_tokens(self) -> int:
        return self._max_tokens_val

    async def chat_raw(self, messages, temperature=None, max_tokens=None, **kwargs):
        return "", LLMMetrics()

    async def chat_structured(self, messages, schema, temperature=None, max_tokens=None, **kwargs):
        raise NotImplementedError

    async def chat_with_tools_single(self, messages, tools, max_tokens=None, *,
                                     stream_callback=None, thinking_callback=None):
        self.messages_at_each_call.append(list(messages))
        self.stream_callbacks_received.append(stream_callback is not None)

        # Forward content via stream_callback if provided
        if self.call_count < len(self.responses):
            resp = self.responses[self.call_count]
        else:
            resp = ("done", [], LLMMetrics())
        self.call_count += 1

        content, tool_calls, metrics = resp
        if stream_callback and content:
            await stream_callback(content)

        return resp

    async def chat_stream(self, messages, temperature=None, max_tokens=None, **kwargs):
        yield ""

    async def check_health(self):
        return True


def _text_response(text: str) -> tuple[str, list, LLMMetrics]:
    return (text, [], LLMMetrics())


def _tool_response(name: str, args: dict, content: str = "") -> tuple[str, list, LLMMetrics]:
    return (content, [ToolCallInfo(name=name, arguments=args)], LLMMetrics())


# ── CHAT_TOOLS constant tests ──────────────────────────────────────


def test_chat_tools_contains_expected_tools():
    assert len(CHAT_TOOLS) == 8


def test_chat_tools_names():
    names = {t["function"]["name"] for t in CHAT_TOOLS}
    assert names == {
        "read_file", "list_directory", "directory_tree", "grep_files",
        "search_internet", "fetch_url",
        "save_note", "list_project_todos",
    }


def test_chat_tools_excludes_task_complete():
    names = {t["function"]["name"] for t in CHAT_TOOLS}
    assert "task_complete" not in names


def test_chat_tools_excludes_write_tools():
    names = {t["function"]["name"] for t in CHAT_TOOLS}
    assert "create_file" not in names
    assert "edit_file" not in names
    assert "run_command" not in names


# ── text_only_exit_count tests ──────────────────────────────────────


async def _noop_executor(name: str, args: dict) -> str:
    return "ok"


@pytest.mark.asyncio
async def test_text_only_exit_count_1_exits_immediately():
    """With text_only_exit_count=1, the loop exits on the first text-only response."""
    provider = FakeProvider([
        _text_response("Hello, here is my answer."),
    ])
    client = LLMClient(provider)

    _, explanation = await client.chat_with_tools(
        messages=[{"role": "user", "content": "test"}],
        tools=CHAT_TOOLS,
        tool_executor_fn=_noop_executor,
        text_only_exit_count=1,
    )

    assert provider.call_count == 1
    assert "Hello, here is my answer." in explanation


@pytest.mark.asyncio
async def test_text_only_exit_count_1_no_nudge():
    """With exit_count=1, no nudge message is injected."""
    provider = FakeProvider([
        _text_response("Answer"),
    ])
    client = LLMClient(provider)

    await client.chat_with_tools(
        messages=[{"role": "user", "content": "test"}],
        tools=CHAT_TOOLS,
        tool_executor_fn=_noop_executor,
        text_only_exit_count=1,
    )

    # Only one call was made, no nudge injected
    assert provider.call_count == 1
    # Messages should not contain any nudge
    assert len(provider.messages_at_each_call) == 1


@pytest.mark.asyncio
async def test_text_only_exit_count_default_nudges():
    """Default exit_count=3 injects nudges before exiting."""
    provider = FakeProvider([
        _text_response("first"),
        _text_response("second"),
        _text_response("third"),
    ])
    client = LLMClient(provider)

    await client.chat_with_tools(
        messages=[{"role": "user", "content": "test"}],
        tools=CHAT_TOOLS,
        tool_executor_fn=_noop_executor,
        # default text_only_exit_count=3
    )

    # Should have made 3 calls (first text → nudge → second text → nudge → third text → exit)
    assert provider.call_count == 3


@pytest.mark.asyncio
async def test_tools_then_text_exits_cleanly():
    """LLM calls a tool, gets result, then responds with text — exits on first text."""
    tool_results = {}

    async def executor(name: str, args: dict) -> str:
        tool_results[name] = args
        return "file content here"

    provider = FakeProvider([
        _tool_response("read_file", {"path": "src/main.py"}),
        _text_response("Based on main.py, here is the answer."),
    ])
    client = LLMClient(provider)

    executed, explanation = await client.chat_with_tools(
        messages=[{"role": "user", "content": "test"}],
        tools=CHAT_TOOLS,
        tool_executor_fn=executor,
        text_only_exit_count=1,
    )

    assert provider.call_count == 2
    assert len(executed) == 1
    assert executed[0].tool_name == "read_file"
    assert "Based on main.py" in explanation


# ── stream_content tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_content_passes_callback_to_provider():
    """When stream_content=True, stream_callback is passed to the provider."""
    provider = FakeProvider([
        _text_response("streamed answer"),
    ])
    client = LLMClient(provider)

    streamed_tokens = []

    async def on_content(token: str):
        streamed_tokens.append(token)

    await client.chat_with_tools(
        messages=[{"role": "user", "content": "test"}],
        tools=CHAT_TOOLS,
        tool_executor_fn=_noop_executor,
        text_only_exit_count=1,
        stream_content=True,
        on_content=on_content,
    )

    assert provider.stream_callbacks_received[0] is True
    assert "streamed answer" in "".join(streamed_tokens)


@pytest.mark.asyncio
async def test_stream_content_false_no_callback():
    """When stream_content=False, no stream_callback is passed to provider."""
    provider = FakeProvider([
        _text_response("bulk answer"),
    ])
    client = LLMClient(provider)

    content_received = []

    async def on_content(token: str):
        content_received.append(token)

    await client.chat_with_tools(
        messages=[{"role": "user", "content": "test"}],
        tools=CHAT_TOOLS,
        tool_executor_fn=_noop_executor,
        text_only_exit_count=1,
        stream_content=False,
        on_content=on_content,
    )

    assert provider.stream_callbacks_received[0] is False
    # Content should still arrive via bulk on_content
    assert "bulk answer" in "".join(content_received)


# ── Chat tool executor tests ───────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_executor_read_file(tmp_path):
    """Tool executor handles read_file correctly."""
    from lean_ai.routers.chat import _make_chat_tool_executor

    test_file = tmp_path / "hello.txt"
    test_file.write_text("hello world")

    executor = _make_chat_tool_executor(str(tmp_path))
    result = await executor("read_file", {"path": "hello.txt"})

    assert "hello world" in result


@pytest.mark.asyncio
async def test_chat_executor_list_directory(tmp_path):
    """Tool executor handles list_directory correctly."""
    from lean_ai.routers.chat import _make_chat_tool_executor

    (tmp_path / "file_a.py").touch()
    (tmp_path / "subdir").mkdir()

    executor = _make_chat_tool_executor(str(tmp_path))
    result = await executor("list_directory", {"path": ""})

    assert "file_a.py" in result
    assert "subdir" in result


@pytest.mark.asyncio
async def test_chat_executor_rejects_unknown_tool(tmp_path):
    """Tool executor rejects tools not in the read-only set."""
    from lean_ai.routers.chat import _make_chat_tool_executor

    executor = _make_chat_tool_executor(str(tmp_path))
    result = await executor("create_file", {"path": "x.py", "content": "hack"})

    assert "Unknown tool" in result


@pytest.mark.asyncio
async def test_chat_executor_grep_files(tmp_path):
    """Tool executor handles grep_files correctly."""
    from lean_ai.routers.chat import _make_chat_tool_executor

    (tmp_path / "code.py").write_text("def hello():\n    return 42\n")

    executor = _make_chat_tool_executor(str(tmp_path))
    result = await executor("grep_files", {"pattern": "hello"})

    assert "hello" in result

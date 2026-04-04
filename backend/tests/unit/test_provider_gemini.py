"""Tests for GeminiProvider — message conversion, tool handling, metrics, retry."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lean_ai.llm.base import ToolCallInfo
from lean_ai.llm.provider_gemini import (
    GeminiProvider,
    _convert_tools,
    _split_system,
)

# ── Helpers ──


def _make_types_module():
    """Create a mock google.genai.types module with the classes GeminiProvider uses."""
    types = MagicMock()

    class FakePart:
        def __init__(self, text=None, function_call=None):
            self.text = text
            self.function_call = function_call

        @staticmethod
        def from_function_response(name, response, id=None):
            return FakePart(text=f"[fn_response:{name}]")

    types.Part = FakePart

    class FakeContent:
        def __init__(self, role=None, parts=None):
            self.role = role
            self.parts = parts or []

    types.Content = FakeContent

    class FakeFunctionCall:
        def __init__(self, name=None, args=None, id=None):
            self.name = name
            self.args = args
            self.id = id

    types.FunctionCall = FakeFunctionCall

    class FakeTool:
        def __init__(self, function_declarations=None):
            self.function_declarations = function_declarations

    types.Tool = FakeTool

    class FakeGenConfig:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    types.GenerateContentConfig = FakeGenConfig

    class FakeAutoFuncConfig:
        def __init__(self, disable=False):
            self.disable = disable

    types.AutomaticFunctionCallingConfig = FakeAutoFuncConfig

    return types


def _make_provider(types_mod=None, **overrides):
    """Create a GeminiProvider with mocked SDK."""
    types_mod = types_mod or _make_types_module()

    with patch("lean_ai.llm.provider_gemini.genai", create=True), \
         patch.dict("sys.modules", {"google": MagicMock(), "google.genai": MagicMock()}):
        provider = object.__new__(GeminiProvider)
        provider._genai = MagicMock()
        provider._types = types_mod
        provider._client = MagicMock()
        provider._model = overrides.get("model", "gemini-2.5-flash")
        provider._max_tokens_val = overrides.get("max_tokens", 262144)
        provider._context_window_val = overrides.get("context_window", 1048576)
        provider._temperature = overrides.get("temperature", 0.7)
        provider._retry_max = overrides.get("retry_max", 0)
        provider._retry_base_delay = overrides.get("retry_base_delay", 0.01)
        provider._enable_thinking = overrides.get("enable_thinking", False)

    return provider


# ── _split_system tests ──


class TestSplitSystem:

    def test_extracts_system_messages(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
            {"role": "system", "content": "Be concise."},
            {"role": "assistant", "content": "Hello!"},
        ]
        system, filtered = _split_system(messages)
        assert system == "You are helpful.\n\nBe concise."
        assert len(filtered) == 2
        assert filtered[0]["role"] == "user"
        assert filtered[1]["role"] == "assistant"

    def test_no_system_messages(self):
        messages = [{"role": "user", "content": "Hi"}]
        system, filtered = _split_system(messages)
        assert system == ""
        assert len(filtered) == 1

    def test_empty_system_content_skipped(self):
        messages = [
            {"role": "system", "content": ""},
            {"role": "user", "content": "Hi"},
        ]
        system, filtered = _split_system(messages)
        assert system == ""
        assert len(filtered) == 1


# ── _convert_tools tests ──


class TestConvertTools:

    def test_openai_format_conversion(self):
        types = _make_types_module()
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                },
            }
        ]
        result = _convert_tools(tools, types)
        assert len(result) == 1
        decls = result[0].function_declarations
        assert len(decls) == 1
        assert decls[0]["name"] == "read_file"
        assert decls[0]["description"] == "Read a file"

    def test_multiple_tools(self):
        types = _make_types_module()
        tools = [
            {"type": "function", "function": {"name": "tool_a", "description": "A"}},
            {"type": "function", "function": {"name": "tool_b", "description": "B"}},
        ]
        result = _convert_tools(tools, types)
        decls = result[0].function_declarations
        assert len(decls) == 2
        assert {d["name"] for d in decls} == {"tool_a", "tool_b"}


# ── _build_contents tests ──


class TestBuildContents:

    def test_basic_text_messages(self):
        types = _make_types_module()
        provider = _make_provider(types_mod=types)
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        contents = provider._build_contents(messages)
        assert len(contents) == 2
        assert contents[0].role == "user"
        assert contents[0].parts[0].text == "Hello"
        assert contents[1].role == "model"
        assert contents[1].parts[0].text == "Hi there"

    def test_function_call_message(self):
        types = _make_types_module()
        provider = _make_provider(types_mod=types)
        messages = [{
            "role": "assistant",
            "content": "Let me read that.",
            "_gemini_function_calls": [
                {"name": "read_file", "args": {"path": "foo.py"}, "id": "fc1"},
            ],
        }]
        contents = provider._build_contents(messages)
        assert len(contents) == 1
        assert contents[0].role == "model"
        # First part is text, second is function call
        assert len(contents[0].parts) == 2
        assert contents[0].parts[0].text == "Let me read that."

    def test_function_response_message(self):
        types = _make_types_module()
        provider = _make_provider(types_mod=types)
        messages = [{
            "role": "user",
            "content": "file contents",
            "_gemini_function_response": {
                "name": "read_file",
                "response": {"result": "file contents"},
                "id": "fc1",
            },
        }]
        contents = provider._build_contents(messages)
        assert len(contents) == 1
        assert contents[0].role == "user"

    def test_anthropic_style_content_blocks(self):
        types = _make_types_module()
        provider = _make_provider(types_mod=types)
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Block 1"},
                {"type": "text", "text": "Block 2"},
            ],
        }]
        contents = provider._build_contents(messages)
        assert len(contents) == 1
        assert "Block 1" in contents[0].parts[0].text
        assert "Block 2" in contents[0].parts[0].text

    def test_empty_content_skipped(self):
        types = _make_types_module()
        provider = _make_provider(types_mod=types)
        messages = [{"role": "user", "content": ""}]
        contents = provider._build_contents(messages)
        assert len(contents) == 0


# ── Properties ──


class TestProviderProperties:

    def test_model_name(self):
        provider = _make_provider(model="gemini-2.5-pro")
        assert provider.model_name == "gemini-2.5-pro"

    def test_context_window(self):
        provider = _make_provider(context_window=2097152)
        assert provider.context_window == 2097152

    def test_max_tokens(self):
        provider = _make_provider(max_tokens=500000)
        assert provider.max_tokens == 500000


# ── Metrics extraction ──


class TestMetrics:

    def test_extract_metrics_with_usage(self):
        provider = _make_provider()
        response = MagicMock()
        response.usage_metadata.prompt_token_count = 100
        response.usage_metadata.candidates_token_count = 50
        metrics = provider._extract_metrics(response, stop_reason="STOP")
        assert metrics.prompt_tokens == 100
        assert metrics.completion_tokens == 50
        assert metrics.stop_reason == "STOP"

    def test_extract_metrics_no_usage(self):
        provider = _make_provider()
        response = MagicMock(spec=[])  # No usage_metadata attr
        metrics = provider._extract_metrics(response)
        assert metrics.prompt_tokens == 0
        assert metrics.completion_tokens == 0

    def test_get_finish_reason(self):
        provider = _make_provider()
        response = MagicMock()
        response.candidates = [MagicMock(finish_reason="STOP")]
        assert provider._get_finish_reason(response) == "STOP"

    def test_get_finish_reason_no_candidates(self):
        provider = _make_provider()
        response = MagicMock(candidates=[])
        assert provider._get_finish_reason(response) is None


# ── Message formatting (tool call/result) ──


class TestMessageFormatting:

    def test_format_tool_result_messages(self):
        provider = _make_provider()
        tc = ToolCallInfo(name="read_file", arguments={"path": "x"}, id="fc1")
        msgs = provider.format_tool_result_messages(tc, "file contents here")
        assert len(msgs) == 1
        msg = msgs[0]
        assert msg["role"] == "user"
        assert msg["content"] == "file contents here"
        fr = msg["_gemini_function_response"]
        assert fr["name"] == "read_file"
        assert fr["response"] == {"result": "file contents here"}
        assert fr["id"] == "fc1"

    def test_format_assistant_tool_message(self):
        provider = _make_provider()
        tcs = [
            ToolCallInfo(name="read_file", arguments={"path": "a.py"}, id="fc1"),
            ToolCallInfo(name="edit_file", arguments={"path": "a.py"}, id="fc2"),
        ]
        msg = provider.format_assistant_tool_message("I'll help", tcs)
        assert msg["role"] == "assistant"
        assert msg["content"] == "I'll help"
        fcs = msg["_gemini_function_calls"]
        assert len(fcs) == 2
        assert fcs[0]["name"] == "read_file"
        assert fcs[1]["name"] == "edit_file"


# ── Retry with backoff ──


class TestRetry:

    @pytest.mark.asyncio
    async def test_retry_on_transient_error(self):
        provider = _make_provider(retry_max=2, retry_base_delay=0.001)
        call_count = 0

        async def _flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("transient")
            return "success"

        result = await provider._retry_with_backoff(_flaky, label="test")
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_no_retry_on_non_transient(self):
        provider = _make_provider(retry_max=2, retry_base_delay=0.001)

        async def _bad():
            raise ValueError("bad input")

        with pytest.raises(ValueError, match="bad input"):
            await provider._retry_with_backoff(_bad, label="test")

    @pytest.mark.asyncio
    async def test_retry_exhausted(self):
        provider = _make_provider(retry_max=1, retry_base_delay=0.001)
        call_count = 0

        async def _always_fails():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("down")

        with pytest.raises(ConnectionError):
            await provider._retry_with_backoff(_always_fails, label="test")
        assert call_count == 2  # initial + 1 retry


# ── chat_raw ──


class TestChatRaw:

    @pytest.mark.asyncio
    async def test_chat_raw_non_streaming(self):
        types = _make_types_module()
        provider = _make_provider(types_mod=types, retry_max=0)

        response = MagicMock()
        response.text = "Hello from Gemini"
        response.usage_metadata.prompt_token_count = 10
        response.usage_metadata.candidates_token_count = 5
        response.candidates = [MagicMock(finish_reason="STOP")]

        provider._client.aio.models.generate_content = AsyncMock(return_value=response)

        text, metrics = await provider.chat_raw(
            [{"role": "user", "content": "Hi"}],
        )
        assert text == "Hello from Gemini"
        assert metrics.prompt_tokens == 10
        assert metrics.completion_tokens == 5

    @pytest.mark.asyncio
    async def test_chat_raw_with_system_prompt(self):
        types = _make_types_module()
        provider = _make_provider(types_mod=types, retry_max=0)

        response = MagicMock()
        response.text = "OK"
        response.usage_metadata.prompt_token_count = 5
        response.usage_metadata.candidates_token_count = 1
        response.candidates = [MagicMock(finish_reason="STOP")]

        provider._client.aio.models.generate_content = AsyncMock(return_value=response)

        text, metrics = await provider.chat_raw([
            {"role": "system", "content": "Be helpful"},
            {"role": "user", "content": "Hi"},
        ])
        assert text == "OK"
        # Verify generate_content was called (system prompt handled via config)
        provider._client.aio.models.generate_content.assert_called_once()


# ── chat_with_tools_single ──


class TestChatWithTools:

    @pytest.mark.asyncio
    async def test_parses_function_call(self):
        types = _make_types_module()
        provider = _make_provider(types_mod=types, retry_max=0)

        fc = types.FunctionCall(name="read_file", args={"path": "foo.py"}, id="fc1")
        part_fc = types.Part(function_call=fc)
        part_text = types.Part(text="Let me read that.")

        candidate = MagicMock()
        candidate.content.parts = [part_text, part_fc]

        response = MagicMock()
        response.candidates = [candidate]
        response.usage_metadata.prompt_token_count = 20
        response.usage_metadata.candidates_token_count = 10

        provider._client.aio.models.generate_content = AsyncMock(return_value=response)

        content, tool_calls, metrics = await provider.chat_with_tools_single(
            [{"role": "user", "content": "Read foo.py"}],
            [{"type": "function", "function": {"name": "read_file", "description": "Read"}}],
        )
        assert content == "Let me read that."
        assert len(tool_calls) == 1
        assert tool_calls[0].name == "read_file"
        assert tool_calls[0].arguments == {"path": "foo.py"}
        assert tool_calls[0].id == "fc1"

    @pytest.mark.asyncio
    async def test_text_only_response(self):
        types = _make_types_module()
        provider = _make_provider(types_mod=types, retry_max=0)

        part = types.Part(text="All done.")
        candidate = MagicMock()
        candidate.content.parts = [part]

        response = MagicMock()
        response.candidates = [candidate]
        response.usage_metadata.prompt_token_count = 5
        response.usage_metadata.candidates_token_count = 3

        provider._client.aio.models.generate_content = AsyncMock(return_value=response)

        content, tool_calls, metrics = await provider.chat_with_tools_single(
            [{"role": "user", "content": "Done?"}],
            [{"type": "function", "function": {"name": "task_complete", "description": "Done"}}],
        )
        assert content == "All done."
        assert len(tool_calls) == 0


# ── check_health ──


class TestCheckHealth:

    @pytest.mark.asyncio
    async def test_health_ok(self):
        types = _make_types_module()
        provider = _make_provider(types_mod=types)
        provider._client.aio.models.generate_content = AsyncMock(return_value=MagicMock())
        assert await provider.check_health() is True

    @pytest.mark.asyncio
    async def test_health_failure(self):
        types = _make_types_module()
        provider = _make_provider(types_mod=types)
        provider._client.aio.models.generate_content = AsyncMock(side_effect=Exception("down"))
        assert await provider.check_health() is False

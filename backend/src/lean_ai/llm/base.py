"""Abstract base class for LLM providers.

Each provider implements core chat methods. Provider-specific differences
(response parsing, error handling, structured output, streaming format)
are encapsulated inside each implementation.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

from pydantic import BaseModel

# Type alias for streaming callbacks used by chat_raw / chat_structured.
StreamCallback = Callable[[str], Awaitable[None]]


@dataclass
class ToolCall:
    """Record of an executed tool call (used by the orchestration loop)."""

    tool_name: str
    parameters: dict = field(default_factory=dict)
    description: str = ""


@dataclass
class ToolCallInfo:
    """Normalized tool call from a provider response.

    Carries the provider-specific ID needed for tool result messages
    (e.g. ``tool_use_id`` for Anthropic, ``tool_call.id`` for OpenAI).
    """

    name: str
    arguments: dict = field(default_factory=dict)
    id: str | None = None


@dataclass
class LLMMetrics:
    """Standardized metrics from an LLM response."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    tokens_per_second: float | None = None
    stop_reason: str | None = None
    thinking: str | None = None


class LLMProvider(ABC):
    """Abstract base class for LLM providers.

    Providers implement single-turn chat methods.  The multi-turn
    orchestration loop (tool calling, context refresh, loop detection)
    lives in ``LLMClient`` (facade.py).
    """

    @property
    @abstractmethod
    def model_name(self) -> str:
        """The model identifier used by this provider."""

    @property
    @abstractmethod
    def context_window(self) -> int:
        """Total context window size in tokens."""

    @property
    @abstractmethod
    def max_tokens(self) -> int:
        """Default max output tokens per response."""

    # ── Core chat methods ──

    @abstractmethod
    async def chat_raw(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        *,
        stream_callback: StreamCallback | None = None,
        thinking_callback: StreamCallback | None = None,
    ) -> tuple[str, LLMMetrics]:
        """Send a conversation and return (response_text, metrics).

        When *stream_callback* is provided, content tokens are forwarded
        to the callback as they arrive (the full text is still returned).
        When *thinking_callback* is provided, thinking/reasoning tokens
        are forwarded similarly.
        """

    @abstractmethod
    async def chat_structured(
        self,
        messages: list[dict],
        schema: type[BaseModel],
        temperature: float | None = None,
        max_tokens: int | None = None,
        *,
        thinking_callback: StreamCallback | None = None,
    ) -> tuple[BaseModel, LLMMetrics]:
        """Send a conversation and parse the response into a Pydantic model.

        When *thinking_callback* is provided, thinking/reasoning tokens
        are forwarded to the callback as they arrive.  Content tokens
        (JSON) are not streamed.
        """

    @abstractmethod
    async def chat_with_tools_single(
        self,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int | None = None,
        *,
        stream_callback: StreamCallback | None = None,
        thinking_callback: StreamCallback | None = None,
    ) -> tuple[str, list[ToolCallInfo], LLMMetrics]:
        """Single-turn LLM call that may return tool calls.

        Returns (content, tool_calls, metrics).  Tool definitions use
        the OpenAI-compatible format that the codebase already uses.

        When *stream_callback* is provided, content tokens are forwarded
        to the callback as they arrive (the full text is still returned).
        When *thinking_callback* is provided, thinking/reasoning tokens
        are forwarded similarly.
        """

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        *,
        thinking_callback: StreamCallback | None = None,
    ) -> AsyncIterator[str]:
        """Stream response tokens.

        When *thinking_callback* is provided, thinking/reasoning tokens
        are forwarded via the callback instead of being yielded.
        """
        # yield is needed to make this an async generator in the ABC
        yield ""  # pragma: no cover
        raise NotImplementedError  # pragma: no cover

    @abstractmethod
    async def check_health(self) -> bool:
        """Check if the provider is reachable and the model is available."""

    # ── Message formatting (provider-specific) ──

    def format_tool_result_messages(
        self, tool_call: ToolCallInfo, content: str,
    ) -> list[dict]:
        """Build message(s) representing a tool result.

        Ollama/OpenAI: ``[{"role": "tool", ...}]``
        Anthropic: ``[{"role": "user", "content": [{"type": "tool_result", ...}]}]``
        """
        return [{"role": "tool", "content": content}]

    def format_assistant_tool_message(
        self, content: str, tool_calls: list[ToolCallInfo],
    ) -> dict:
        """Build the assistant message that contains tool calls.

        Stored in conversation history so the provider can correlate
        tool results with their originating calls.
        """
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "function": {
                        "name": tc.name,
                        "arguments": dict(tc.arguments),
                    },
                }
                for tc in tool_calls
            ],
        }

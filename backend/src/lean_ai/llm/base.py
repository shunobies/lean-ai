"""Abstract base class for LLM providers.

Each provider implements core chat methods. Provider-specific differences
(response parsing, error handling, structured output, streaming format)
are encapsulated inside each implementation.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Type alias for streaming callbacks used by chat_raw / chat_structured.
StreamCallback = Callable[[str], Awaitable[None]]


class CapabilityError(Exception):
    """Raised when a provider is asked to handle media it cannot process.

    Examples: audio sent to Ollama or Anthropic, image sent to a model the
    selected provider does not accept.  Catching callers typically log the
    error and fall back to the dedicated path (``vision_model`` for image,
    faster-whisper for audio).
    """


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

    @classmethod
    def from_usage(
        cls,
        prompt: int = 0,
        completion: int = 0,
        *,
        stop_reason: str | None = None,
        tps: float | None = None,
    ) -> "LLMMetrics":
        """Construct from provider-extracted token counts."""
        return cls(
            prompt_tokens=prompt or 0,
            completion_tokens=completion or 0,
            tokens_per_second=tps,
            stop_reason=stop_reason,
        )


async def retry_with_backoff(
    coro_factory: Callable,
    *,
    retryable_exceptions: tuple[type[Exception], ...] = (),
    is_retryable: Callable[[Exception], bool] | None = None,
    max_retries: int = 3,
    base_delay: float = 1.0,
    label: str = "LLM call",
):
    """Retry an async callable with exponential backoff.

    Parameters
    ----------
    coro_factory:
        Zero-arg async callable to retry.
    retryable_exceptions:
        Exception types that should trigger a retry.
    is_retryable:
        Optional predicate for exceptions not covered by
        *retryable_exceptions* (e.g. checking status codes or
        exception name substrings).
    max_retries:
        Maximum number of retry attempts (0 = no retries).
    base_delay:
        Base delay in seconds (doubled each attempt).
    label:
        Human-readable label for log messages.
    """
    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except Exception as exc:
            should_retry = (
                isinstance(exc, retryable_exceptions)
                or (is_retryable is not None and is_retryable(exc))
            )
            if not should_retry or attempt >= max_retries:
                raise
            delay = base_delay * (2 ** attempt)
            logger.warning(
                "%s failed (attempt %d/%d), retrying in %.1fs: %s",
                label, attempt + 1, max_retries + 1, delay, exc,
            )
            await asyncio.sleep(delay)
    return None  # unreachable — loop always returns or raises


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
    def provider_name(self) -> str:
        """Kebab-case provider identifier used for media routing.

        One of ``"ollama"``, ``"openai"``, ``"anthropic"``, ``"gemini"``.
        (``"serve"`` reuses ``OpenAIProvider`` and reports ``"openai"`` —
        identical content-block shape for images and audio.)
        """
        return type(self).__name__.replace("Provider", "").lower()

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

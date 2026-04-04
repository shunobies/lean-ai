"""Anthropic LLM provider — wraps the Anthropic Python SDK."""

import json
import logging
from collections.abc import AsyncIterator

from pydantic import BaseModel, ValidationError

from lean_ai.config import settings
from lean_ai.llm.base import LLMMetrics, LLMProvider, ToolCallInfo, retry_with_backoff

logger = logging.getLogger(__name__)


def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """Extract system prompt from messages and return (system_str, non_system_messages).

    Anthropic requires system as a separate parameter, not in the messages list.
    """
    system_parts: list[str] = []
    filtered: list[dict] = []
    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content") or ""
            if content:
                system_parts.append(content)
        else:
            filtered.append(msg)
    return "\n\n".join(system_parts), filtered


def _convert_tools(tools: list[dict]) -> list[dict]:
    """Convert OpenAI-format tool definitions to Anthropic format.

    OpenAI: ``{"type": "function", "function": {"name": ..., ...}}``
    Anthropic: ``{"name": ..., "input_schema": ...}``
    """
    converted = []
    for tool in tools:
        fn = tool.get("function", tool)
        converted.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return converted


class AnthropicProvider(LLMProvider):
    """LLM provider backed by Anthropic's Claude API."""

    # Fraction of max_tokens allocated to thinking budget
    _THINKING_BUDGET_PERCENT = 0.8

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 50000,
        context_window: int = 200000,
        temperature: float = 0.7,
        retry_max: int | None = None,
        retry_base_delay: float | None = None,
        enable_thinking: bool = False,
    ):
        import anthropic as anthropic_lib
        self._anthropic = anthropic_lib

        self._client = anthropic_lib.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_tokens_val = max_tokens
        self._context_window_val = context_window
        self._temperature = temperature
        self._enable_thinking = enable_thinking
        self._retry_max = retry_max if retry_max is not None else settings.llm_retry_max
        self._retry_base_delay = (
            retry_base_delay if retry_base_delay is not None else settings.llm_retry_base_delay
        )

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def context_window(self) -> int:
        return self._context_window_val

    @property
    def max_tokens(self) -> int:
        return self._max_tokens_val

    async def _retry_with_backoff(self, coro_factory, label: str = "Anthropic call"):
        """Retry with exponential backoff for transient/rate-limit errors."""
        return await retry_with_backoff(
            coro_factory,
            retryable_exceptions=(
                self._anthropic.APIConnectionError,
                self._anthropic.InternalServerError,
                self._anthropic.RateLimitError,
            ),
            max_retries=self._retry_max,
            base_delay=self._retry_base_delay,
            label=label,
        )

    def _extract_metrics(
        self, response, *, stop_reason: str | None = None,
    ) -> LLMMetrics:
        """Extract metrics from an Anthropic response."""
        usage = getattr(response, "usage", None)
        if usage:
            return LLMMetrics.from_usage(
                getattr(usage, "input_tokens", 0),
                getattr(usage, "output_tokens", 0),
                stop_reason=stop_reason,
            )
        return LLMMetrics(stop_reason=stop_reason)

    def _apply_thinking_kwargs(self, kwargs: dict, tokens: int) -> None:
        """Add extended thinking parameters to API kwargs when enabled.

        Anthropic extended thinking requires temperature=1.0 and adds a
        ``thinking`` parameter with a budget.
        """
        if not self._enable_thinking:
            return
        budget = max(1024, int(tokens * self._THINKING_BUDGET_PERCENT))
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
        kwargs["temperature"] = 1.0

    @staticmethod
    def _extract_thinking_from_message(message) -> str | None:
        """Extract thinking text from content blocks of a final message."""
        parts: list[str] = []
        for block in getattr(message, "content", []):
            if getattr(block, "type", None) == "thinking":
                text = getattr(block, "thinking", "")
                if text:
                    parts.append(text)
        return "\n\n".join(parts) if parts else None

    async def chat_raw(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        *,
        stream_callback=None,
        thinking_callback=None,
    ) -> tuple[str, LLMMetrics]:
        temp = temperature if temperature is not None else self._temperature
        tokens = max_tokens if max_tokens is not None else self._max_tokens_val
        system_prompt, filtered_messages = _split_system(messages)

        logger.info(
            "Anthropic chat_raw: model=%s messages=%d temp=%.1f max_tokens=%d "
            "streaming=%s thinking=%s",
            self._model, len(filtered_messages), temp, tokens,
            bool(stream_callback or thinking_callback), self._enable_thinking,
        )

        kwargs: dict = {
            "model": self._model,
            "messages": filtered_messages,
            "temperature": temp,
            "max_tokens": tokens,
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        if self._enable_thinking and (stream_callback or thinking_callback):
            self._apply_thinking_kwargs(kwargs, tokens)
            return await self._chat_raw_streaming(
                kwargs, stream_callback, thinking_callback,
            )

        # Non-thinking streaming path (content only)
        async def _chat():
            chunks: list[str] = []
            async with self._client.messages.stream(**kwargs) as stream:
                async for chunk in stream.text_stream:
                    if chunk:
                        chunks.append(chunk)
                        if stream_callback:
                            await stream_callback(chunk)
                final_message = await stream.get_final_message()
            return "".join(chunks), final_message

        text, final_message = await self._retry_with_backoff(_chat, label="chat_raw")
        metrics = self._extract_metrics(
            final_message, stop_reason=getattr(final_message, "stop_reason", None),
        )

        logger.info("Anthropic chat_raw response (%d chars): %s", len(text), text[:200])
        return text, metrics

    async def _chat_raw_streaming(
        self,
        kwargs: dict,
        stream_callback,
        thinking_callback,
    ) -> tuple[str, LLMMetrics]:
        """Stream chat_raw with thinking support via full event iteration."""
        content_parts: list[str] = []
        thinking_parts: list[str] = []

        async def _chat():
            async with self._client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    etype = getattr(event, "type", "")
                    if etype == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        if delta is None:
                            continue
                        dtype = getattr(delta, "type", "")
                        if dtype == "thinking_delta":
                            text = getattr(delta, "thinking", "")
                            if text:
                                thinking_parts.append(text)
                                if thinking_callback:
                                    await thinking_callback(text)
                        elif dtype == "text_delta":
                            text = getattr(delta, "text", "")
                            if text:
                                content_parts.append(text)
                                if stream_callback:
                                    await stream_callback(text)
                return await stream.get_final_message()

        try:
            final_message = await self._retry_with_backoff(
                _chat, label="chat_raw(thinking)",
            )
        except self._anthropic.BadRequestError as exc:
            logger.warning(
                "Extended thinking not supported for %s, falling back: %s",
                self._model, exc,
            )
            # Remove thinking params and retry without
            kwargs.pop("thinking", None)
            kwargs["temperature"] = self._temperature

            async def _fallback():
                chunks: list[str] = []
                async with self._client.messages.stream(**kwargs) as stream:
                    async for chunk in stream.text_stream:
                        if chunk:
                            chunks.append(chunk)
                            if stream_callback:
                                await stream_callback(chunk)
                    final_msg = await stream.get_final_message()
                return "".join(chunks), final_msg

            text, final_message = await self._retry_with_backoff(
                _fallback, label="chat_raw(fallback)",
            )
            metrics = self._extract_metrics(
                final_message,
                stop_reason=getattr(final_message, "stop_reason", None),
            )
            logger.info(
                "Anthropic chat_raw response (%d chars, fallback): %s",
                len(text), text[:200],
            )
            return text, metrics

        text = "".join(content_parts)
        thinking = "\n".join(thinking_parts) or None
        metrics = self._extract_metrics(
            final_message,
            stop_reason=getattr(final_message, "stop_reason", None),
        )
        metrics.thinking = thinking

        logger.info(
            "Anthropic chat_raw response (%d chars, thinking=%d chars): %s",
            len(text), len(thinking or ""), text[:200],
        )
        return text, metrics

    async def chat_structured(
        self,
        messages: list[dict],
        schema: type[BaseModel],
        temperature: float | None = None,
        max_tokens: int | None = None,
        *,
        thinking_callback=None,
    ) -> tuple[BaseModel, LLMMetrics]:
        temp = temperature if temperature is not None else self._temperature
        tokens = max_tokens if max_tokens is not None else self._max_tokens_val
        system_prompt, filtered_messages = _split_system(messages)

        logger.info(
            "Anthropic chat_structured: schema=%s model=%s thinking=%s",
            schema.__name__, self._model, self._enable_thinking,
        )

        # Inject JSON schema instruction into system prompt
        schema_json = json.dumps(schema.model_json_schema(), indent=2)
        structured_instruction = (
            f"{system_prompt}\n\n"
            f"Respond with a JSON object that matches this schema exactly:\n"
            f"```json\n{schema_json}\n```\n"
            f"Output ONLY valid JSON, no other text."
        )

        kwargs: dict = {
            "model": self._model,
            "messages": filtered_messages,
            "temperature": temp,
            "max_tokens": tokens,
            "system": structured_instruction,
        }

        if self._enable_thinking and thinking_callback:
            self._apply_thinking_kwargs(kwargs, tokens)
            chat_fn = self._make_structured_thinking_chat(kwargs, thinking_callback)
        else:
            chat_fn = self._make_structured_chat(kwargs)

        last_error = None
        for attempt in range(2):
            raw, final_message = await self._retry_with_backoff(
                chat_fn, label=f"structured({schema.__name__})",
            )
            metrics = self._extract_metrics(
                final_message,
                stop_reason=getattr(final_message, "stop_reason", None),
            )

            # Strip markdown code fences if present
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                # Remove first line (```json) and last line (```)
                if lines[-1].strip() == "```":
                    lines = lines[1:-1]
                else:
                    lines = lines[1:]
                cleaned = "\n".join(lines)

            try:
                return schema.model_validate_json(cleaned), metrics
            except ValidationError as exc:
                last_error = exc
                if attempt == 0:
                    logger.warning(
                        "Schema validation failed for %s, retrying: %s",
                        schema.__name__, exc.errors(),
                    )
                    continue
                logger.error(
                    "Schema validation failed after retry for %s. Raw: %s",
                    schema.__name__, raw[:1000],
                )
                raise
        raise last_error  # type: ignore[misc]

    def _make_structured_chat(self, kwargs: dict):
        """Create a non-thinking structured chat coroutine factory."""
        async def _chat():
            chunks: list[str] = []
            async with self._client.messages.stream(**kwargs) as stream:
                async for chunk in stream.text_stream:
                    if chunk:
                        chunks.append(chunk)
                final_message = await stream.get_final_message()
            return "".join(chunks), final_message
        return _chat

    def _make_structured_thinking_chat(self, kwargs: dict, thinking_callback):
        """Create a thinking-enabled structured chat coroutine factory."""
        anthropic_lib = self._anthropic

        async def _chat():
            content_parts: list[str] = []
            try:
                async with self._client.messages.stream(**kwargs) as stream:
                    async for event in stream:
                        etype = getattr(event, "type", "")
                        if etype == "content_block_delta":
                            delta = getattr(event, "delta", None)
                            if delta is None:
                                continue
                            dtype = getattr(delta, "type", "")
                            if dtype == "thinking_delta":
                                text = getattr(delta, "thinking", "")
                                if text:
                                    await thinking_callback(text)
                            elif dtype == "text_delta":
                                text = getattr(delta, "text", "")
                                if text:
                                    content_parts.append(text)
                    final_message = await stream.get_final_message()
                return "".join(content_parts), final_message
            except anthropic_lib.BadRequestError:
                logger.warning(
                    "Extended thinking not supported for %s in structured mode, "
                    "falling back",
                    self._model,
                )
                kwargs.pop("thinking", None)
                kwargs["temperature"] = self._temperature
                chunks: list[str] = []
                async with self._client.messages.stream(**kwargs) as stream:
                    async for chunk in stream.text_stream:
                        if chunk:
                            chunks.append(chunk)
                    final_msg = await stream.get_final_message()
                return "".join(chunks), final_msg

        return _chat

    async def chat_with_tools_single(
        self,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int | None = None,
        *,
        stream_callback=None,
        thinking_callback=None,
    ) -> tuple[str, list[ToolCallInfo], LLMMetrics]:
        tokens = max_tokens or self._max_tokens_val
        system_prompt, filtered_messages = _split_system(messages)
        anthropic_tools = _convert_tools(tools)

        kwargs: dict = {
            "model": self._model,
            "messages": filtered_messages,
            "tools": anthropic_tools,
            "temperature": self._temperature,
            "max_tokens": tokens,
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        if not stream_callback and not thinking_callback:
            # Non-streaming path (unchanged)
            async def _chat():
                async with self._client.messages.stream(**kwargs) as stream:
                    return await stream.get_final_message()

            response = await self._retry_with_backoff(
                _chat, label="chat_with_tools_single",
            )
        else:
            # Streaming path — forward content tokens via callback
            streamed_parts: list[str] = []

            async def _chat_streaming():
                async with self._client.messages.stream(**kwargs) as stream:
                    async for event in stream:
                        if hasattr(event, "type") and event.type == "content_block_delta":
                            delta = getattr(event, "delta", None)
                            if delta and hasattr(delta, "text"):
                                streamed_parts.append(delta.text)
                                if stream_callback:
                                    await stream_callback(delta.text)
                    return await stream.get_final_message()

            response = await self._retry_with_backoff(
                _chat_streaming, label="chat_with_tools_single(stream)",
            )

        metrics = self._extract_metrics(
            response, stop_reason=getattr(response, "stop_reason", None),
        )

        # Parse response content blocks
        content_parts: list[str] = []
        tool_calls: list[ToolCallInfo] = []

        for block in response.content:
            if block.type == "text":
                content_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCallInfo(
                    name=block.name,
                    arguments=dict(block.input) if isinstance(block.input, dict) else {},
                    id=block.id,
                ))

        content = "\n".join(content_parts)
        return content, tool_calls, metrics

    async def chat_stream(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        *,
        thinking_callback=None,
    ) -> AsyncIterator[str]:
        temp = temperature if temperature is not None else self._temperature
        tokens = max_tokens if max_tokens is not None else self._max_tokens_val
        system_prompt, filtered_messages = _split_system(messages)

        kwargs: dict = {
            "model": self._model,
            "messages": filtered_messages,
            "temperature": temp,
            "max_tokens": tokens,
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        async def _stream():
            return self._client.messages.stream(**kwargs)

        stream_ctx = await self._retry_with_backoff(_stream, label="chat_stream")

        async with stream_ctx as stream:
            async for text in stream.text_stream:
                if text:
                    yield text

    async def check_health(self) -> bool:
        try:
            await self._client.messages.create(
                model=self._model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            return True
        except Exception:
            logger.exception("Anthropic health check failed")
            return False

    # ── Message formatting (Anthropic-specific) ──

    def format_tool_result_messages(
        self, tool_call: ToolCallInfo, content: str,
    ) -> list[dict]:
        """Anthropic requires tool results as user messages with tool_result blocks."""
        return [{
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call.id or "",
                    "content": content,
                }
            ],
        }]

    def format_assistant_tool_message(
        self, content: str, tool_calls: list[ToolCallInfo],
    ) -> dict:
        """Anthropic assistant messages contain content blocks, not a tool_calls array."""
        blocks: list[dict] = []
        if content:
            blocks.append({"type": "text", "text": content})
        for tc in tool_calls:
            blocks.append({
                "type": "tool_use",
                "id": tc.id or "",
                "name": tc.name,
                "input": tc.arguments,
            })
        return {"role": "assistant", "content": blocks}


def _extract_text(response) -> str:
    """Extract text content from an Anthropic response."""
    for block in response.content:
        if block.type == "text":
            return block.text
    return ""

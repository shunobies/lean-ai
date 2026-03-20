"""Anthropic LLM provider — wraps the Anthropic Python SDK."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from pydantic import BaseModel, ValidationError

from lean_ai.config import settings
from lean_ai.llm.base import LLMMetrics, LLMProvider, ToolCallInfo

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

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 50000,
        context_window: int = 200000,
        temperature: float = 0.7,
        retry_max: int | None = None,
        retry_base_delay: float | None = None,
    ):
        import anthropic as anthropic_lib
        self._anthropic = anthropic_lib

        self._client = anthropic_lib.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_tokens_val = max_tokens
        self._context_window_val = context_window
        self._temperature = temperature
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
        for attempt in range(self._retry_max + 1):
            try:
                return await coro_factory()
            except (
                self._anthropic.APIConnectionError,
                self._anthropic.InternalServerError,
            ) as exc:
                if attempt >= self._retry_max:
                    raise
                delay = self._retry_base_delay * (2**attempt)
                logger.warning(
                    "%s failed (attempt %d/%d), retrying in %.1fs: %s",
                    label, attempt + 1, self._retry_max + 1, delay, exc,
                )
                await asyncio.sleep(delay)
            except self._anthropic.RateLimitError as exc:
                if attempt >= self._retry_max:
                    raise
                delay = self._retry_base_delay * (2**attempt)
                logger.warning(
                    "%s rate limited (attempt %d/%d), retrying in %.1fs: %s",
                    label, attempt + 1, self._retry_max + 1, delay, exc,
                )
                await asyncio.sleep(delay)

    def _extract_metrics(
        self, response, *, stop_reason: str | None = None,
    ) -> LLMMetrics:
        """Extract metrics from an Anthropic response."""
        usage = getattr(response, "usage", None)
        if usage:
            return LLMMetrics(
                prompt_tokens=getattr(usage, "input_tokens", 0) or 0,
                completion_tokens=getattr(usage, "output_tokens", 0) or 0,
                stop_reason=stop_reason,
            )
        return LLMMetrics(stop_reason=stop_reason)

    async def chat_raw(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> tuple[str, LLMMetrics]:
        temp = temperature if temperature is not None else self._temperature
        tokens = max_tokens if max_tokens is not None else self._max_tokens_val
        system_prompt, filtered_messages = _split_system(messages)

        logger.info(
            "Anthropic chat_raw: model=%s messages=%d temp=%.1f max_tokens=%d",
            self._model, len(filtered_messages), temp, tokens,
        )

        kwargs: dict = {
            "model": self._model,
            "messages": filtered_messages,
            "temperature": temp,
            "max_tokens": tokens,
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        async def _chat():
            chunks: list[str] = []
            async with self._client.messages.stream(**kwargs) as stream:
                async for chunk in stream.text_stream:
                    if chunk:
                        chunks.append(chunk)
                final_message = await stream.get_final_message()
            return "".join(chunks), final_message

        text, final_message = await self._retry_with_backoff(_chat, label="chat_raw")
        metrics = self._extract_metrics(
            final_message, stop_reason=getattr(final_message, "stop_reason", None),
        )

        logger.info("Anthropic chat_raw response (%d chars): %s", len(text), text[:200])
        return text, metrics

    async def chat_structured(
        self,
        messages: list[dict],
        schema: type[BaseModel],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> tuple[BaseModel, LLMMetrics]:
        temp = temperature if temperature is not None else self._temperature
        tokens = max_tokens if max_tokens is not None else self._max_tokens_val
        system_prompt, filtered_messages = _split_system(messages)

        logger.info(
            "Anthropic chat_structured: schema=%s model=%s", schema.__name__, self._model,
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

        async def _chat():
            chunks: list[str] = []
            async with self._client.messages.stream(**kwargs) as stream:
                async for chunk in stream.text_stream:
                    if chunk:
                        chunks.append(chunk)
                final_message = await stream.get_final_message()
            return "".join(chunks), final_message

        last_error = None
        for attempt in range(2):
            raw, final_message = await self._retry_with_backoff(
                _chat, label=f"structured({schema.__name__})",
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

    async def chat_with_tools_single(
        self,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int | None = None,
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

        async def _chat():
            async with self._client.messages.stream(**kwargs) as stream:
                return await stream.get_final_message()

        response = await self._retry_with_backoff(
            _chat, label="chat_with_tools_single",
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

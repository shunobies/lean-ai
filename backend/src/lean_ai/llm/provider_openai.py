"""OpenAI LLM provider — wraps the OpenAI Python SDK."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from pydantic import BaseModel, ValidationError

from lean_ai.config import settings
from lean_ai.llm.base import LLMMetrics, LLMProvider, ToolCallInfo

logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    """LLM provider backed by OpenAI (or any OpenAI-compatible API).

    Supports GPT-4o, GPT-4 Turbo, and OpenAI-compatible endpoints
    (Together, Groq, vLLM, etc.) via ``base_url``.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        max_tokens: int = 32000,
        context_window: int = 128000,
        temperature: float = 0.7,
        base_url: str | None = None,
        retry_max: int | None = None,
        retry_base_delay: float | None = None,
    ):
        import openai as openai_lib
        self._openai = openai_lib

        client_kwargs: dict = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = openai_lib.AsyncOpenAI(**client_kwargs)
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

    async def _retry_with_backoff(self, coro_factory, label: str = "OpenAI call"):
        """Retry with exponential backoff for transient/rate-limit errors."""
        for attempt in range(self._retry_max + 1):
            try:
                return await coro_factory()
            except (
                self._openai.APIConnectionError,
                self._openai.InternalServerError,
            ) as exc:
                if attempt >= self._retry_max:
                    raise
                delay = self._retry_base_delay * (2**attempt)
                logger.warning(
                    "%s failed (attempt %d/%d), retrying in %.1fs: %s",
                    label, attempt + 1, self._retry_max + 1, delay, exc,
                )
                await asyncio.sleep(delay)
            except self._openai.RateLimitError as exc:
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
        """Extract metrics from an OpenAI response."""
        usage = getattr(response, "usage", None)
        if usage:
            return LLMMetrics(
                prompt_tokens=usage.prompt_tokens or 0,
                completion_tokens=usage.completion_tokens or 0,
                stop_reason=stop_reason,
            )
        return LLMMetrics(stop_reason=stop_reason)

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

        logger.info(
            "OpenAI chat_raw: model=%s messages=%d temp=%.1f max_tokens=%d streaming=%s",
            self._model, len(messages), temp, tokens, bool(stream_callback),
        )

        if stream_callback:
            return await self._chat_raw_streaming(
                messages, temp, tokens, stream_callback,
            )

        async def _chat():
            return await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=temp,
                max_tokens=tokens,
            )

        response = await self._retry_with_backoff(_chat, label="chat_raw")
        choice = response.choices[0]
        text = choice.message.content or ""
        metrics = self._extract_metrics(
            response, stop_reason=choice.finish_reason,
        )

        logger.info("OpenAI chat_raw response (%d chars): %s", len(text), text[:200])
        return text, metrics

    async def _chat_raw_streaming(
        self,
        messages: list[dict],
        temp: float,
        tokens: int,
        stream_callback,
    ) -> tuple[str, LLMMetrics]:
        """Stream chat_raw response, forwarding content tokens via callback."""
        async def _start_stream():
            return await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=temp,
                max_tokens=tokens,
                stream=True,
                stream_options={"include_usage": True},
            )

        stream = await self._retry_with_backoff(
            _start_stream, label="chat_raw(stream)",
        )

        content_parts: list[str] = []
        stop_reason: str | None = None
        usage = None

        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                token = chunk.choices[0].delta.content
                content_parts.append(token)
                await stream_callback(token)
            if chunk.choices and chunk.choices[0].finish_reason:
                stop_reason = chunk.choices[0].finish_reason
            if hasattr(chunk, "usage") and chunk.usage:
                usage = chunk.usage

        text = "".join(content_parts)
        metrics = LLMMetrics(
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            stop_reason=stop_reason,
        )

        logger.info(
            "OpenAI chat_raw response (%d chars, streamed): %s", len(text), text[:200],
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

        logger.info(
            "OpenAI chat_structured: schema=%s model=%s", schema.__name__, self._model,
        )

        json_schema = schema.model_json_schema()
        # Remove unsupported keys that Pydantic adds
        json_schema.pop("title", None)

        async def _chat():
            return await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=temp,
                max_tokens=tokens,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema.__name__,
                        "schema": json_schema,
                        "strict": True,
                    },
                },
            )

        last_error = None
        for attempt in range(2):
            response = await self._retry_with_backoff(
                _chat, label=f"structured({schema.__name__})",
            )
            choice = response.choices[0]
            raw = choice.message.content or ""
            metrics = self._extract_metrics(
                response, stop_reason=choice.finish_reason,
            )
            try:
                return schema.model_validate_json(raw), metrics
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

        async def _chat():
            return await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=tools,
                temperature=self._temperature,
                max_tokens=tokens,
            )

        response = await self._retry_with_backoff(
            _chat, label="chat_with_tools_single",
        )

        choice = response.choices[0]
        content = choice.message.content or ""
        raw_tool_calls = choice.message.tool_calls or []
        metrics = self._extract_metrics(
            response, stop_reason=choice.finish_reason,
        )

        tool_calls: list[ToolCallInfo] = []
        for tc in raw_tool_calls:
            try:
                arguments = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                arguments = {}
            tool_calls.append(ToolCallInfo(
                name=tc.function.name,
                arguments=arguments,
                id=tc.id,
            ))

        return content, tool_calls, metrics

    async def chat_stream(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        temp = temperature if temperature is not None else self._temperature
        tokens = max_tokens if max_tokens is not None else self._max_tokens_val

        async def _chat():
            return await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=temp,
                max_tokens=tokens,
                stream=True,
            )

        stream = await self._retry_with_backoff(_chat, label="chat_stream")

        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def check_health(self) -> bool:
        try:
            await self._client.models.retrieve(self._model)
            return True
        except Exception:
            logger.exception("OpenAI health check failed")
            return False

    # ── Message formatting ──

    def format_tool_result_messages(
        self, tool_call: ToolCallInfo, content: str,
    ) -> list[dict]:
        return [{
            "role": "tool",
            "tool_call_id": tool_call.id or "",
            "content": content,
        }]

    def format_assistant_tool_message(
        self, content: str, tool_calls: list[ToolCallInfo],
    ) -> dict:
        return {
            "role": "assistant",
            "content": content or None,
            "tool_calls": [
                {
                    "id": tc.id or f"call_{i}",
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for i, tc in enumerate(tool_calls)
            ],
        }

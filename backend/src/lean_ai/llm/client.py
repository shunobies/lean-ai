"""Ollama LLM provider — wraps the Ollama Python SDK."""

import asyncio
import logging
from collections.abc import AsyncIterator

import ollama as ollama_lib
from pydantic import BaseModel, ValidationError

from lean_ai.config import settings
from lean_ai.llm.base import LLMMetrics, LLMProvider, ToolCallInfo, retry_with_backoff

logger = logging.getLogger(__name__)

_TRANSIENT_ERRORS = (ConnectionError, TimeoutError, OSError)


def _sanitize_messages(messages: list[dict]) -> list[dict]:
    """Return a cleaned copy of messages with orphaned tool calls removed.

    Fixes two issues that can confuse the LLM:
    1. Assistant messages with tool_calls that lack corresponding tool results
       (e.g. from interrupted execution) — excess tool_calls are trimmed.
    2. Consecutive assistant messages — merged into one.
    """
    cleaned: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        tool_calls = msg.get("tool_calls")

        # Merge consecutive assistant messages
        if role == "assistant" and cleaned and cleaned[-1].get("role") == "assistant":
            prev = cleaned[-1]
            prev_content = prev.get("content") or ""
            new_content = msg.get("content") or ""
            merged = "\n\n".join(p for p in [prev_content, new_content] if p)
            prev["content"] = merged
            # If the new message also has tool_calls, adopt them
            if tool_calls:
                prev["tool_calls"] = list(tool_calls)
            continue

        cleaned.append(dict(msg))

    # Fix orphaned tool_calls: for each assistant with tool_calls, ensure
    # enough role="tool" messages follow before the next non-tool message.
    result: list[dict] = []
    i = 0
    while i < len(cleaned):
        msg = cleaned[i]
        tool_calls = msg.get("tool_calls")

        if msg.get("role") == "assistant" and tool_calls:
            # Count following tool-result messages
            following_tools = 0
            j = i + 1
            while j < len(cleaned) and cleaned[j].get("role") == "tool":
                following_tools += 1
                j += 1

            if following_tools == 0:
                # No tool results at all — drop the entire assistant message
                i += 1
                continue

            if following_tools < len(tool_calls):
                # Fewer results than calls — trim tool_calls to match
                trimmed = dict(msg)
                trimmed["tool_calls"] = list(tool_calls[:following_tools])
                result.append(trimmed)
            else:
                result.append(msg)
        else:
            result.append(msg)
        i += 1

    return result


class OllamaProvider(LLMProvider):
    """LLM provider backed by a local Ollama instance.

    Handles all Ollama-specific details: SDK calls, response parsing,
    options dict, retry logic, and Ollama-only features (FIM, embeddings).
    """

    def __init__(
        self,
        ollama_url: str | None = None,
        embed_ollama_url: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        context_window: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        repeat_penalty: float | None = None,
        enable_thinking: bool | None = None,
    ):
        effective_url = ollama_url or settings.ollama_url
        self._url = effective_url
        self._client = ollama_lib.AsyncClient(host=effective_url)
        self._model = model or settings.ollama_model
        self._max_tokens_val = max_tokens if max_tokens is not None else settings.ollama_max_tokens
        self._context_window_val = (
            context_window if context_window is not None else settings.ollama_context_window
        )
        self._temperature = (
            temperature if temperature is not None else settings.ollama_temperature
        )
        self._top_p = top_p if top_p is not None else settings.ollama_top_p
        self._top_k = top_k if top_k is not None else settings.ollama_top_k
        self._repeat_penalty = (
            repeat_penalty if repeat_penalty is not None
            else settings.ollama_repeat_penalty
        )
        self._enable_thinking = (
            enable_thinking if enable_thinking is not None
            else settings.enable_thinking
        )

        self._fim_supported = True

        effective_embed_url = embed_ollama_url or settings.effective_embedding_url
        if effective_embed_url != effective_url:
            self._embed_client = ollama_lib.AsyncClient(host=effective_embed_url)
        else:
            self._embed_client = self._client

    # ── LLMProvider interface ──

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def context_window(self) -> int:
        return self._context_window_val

    @property
    def max_tokens(self) -> int:
        return self._max_tokens_val

    def _build_options(
        self, *, temperature: float | None = None, max_tokens: int | None = None,
    ) -> dict:
        """Build the Ollama options dict."""
        return {
            "temperature": temperature if temperature is not None else self._temperature,
            "top_p": self._top_p,
            "top_k": self._top_k,
            "repeat_penalty": self._repeat_penalty,
            "num_predict": max_tokens if max_tokens is not None else self._max_tokens_val,
            "num_ctx": self._context_window_val,
        }

    def _extract_metrics(self, response: dict) -> LLMMetrics:
        """Extract standardized metrics from an Ollama response."""
        try:
            eval_count = response.get("eval_count", 0) or 0
            eval_duration = response.get("eval_duration", 0) or 0
            tps = (
                round(eval_count / (eval_duration / 1_000_000_000), 1)
                if eval_count and eval_duration and eval_duration > 0
                else None
            )
            return LLMMetrics.from_usage(
                response.get("prompt_eval_count", 0),
                eval_count,
                stop_reason=response.get("done_reason"),
                tps=tps,
            )
        except Exception:
            return LLMMetrics()

    async def _retry_with_backoff(self, coro_factory, label: str = "LLM call"):
        """Retry an async callable with exponential backoff for transient errors."""
        def _is_retryable(exc: Exception) -> bool:
            return (
                isinstance(exc, ollama_lib.ResponseError)
                and exc.status_code is not None
                and exc.status_code >= 500
            )

        return await retry_with_backoff(
            coro_factory,
            retryable_exceptions=_TRANSIENT_ERRORS,
            is_retryable=_is_retryable,
            max_retries=settings.llm_retry_max,
            base_delay=settings.llm_retry_base_delay,
            label=label,
        )

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
            "LLM chat_raw: model=%s messages=%d temp=%.1f max_tokens=%d streaming=%s",
            self._model, len(messages), temp, tokens,
            bool(stream_callback or thinking_callback),
        )

        if stream_callback or thinking_callback:
            return await self._chat_raw_streaming(
                messages, temp, tokens, stream_callback, thinking_callback,
            )

        async def _chat():
            return await self._client.chat(
                model=self._model,
                messages=messages,
                options=self._build_options(temperature=temp, max_tokens=tokens),
                think=self._enable_thinking,
            )

        response = await self._retry_with_backoff(_chat, label="chat_raw")
        text = response["message"]["content"]
        thinking = response["message"].get("thinking") or None
        metrics = self._extract_metrics(response)
        metrics.thinking = thinking

        logger.info("LLM chat_raw response (%d chars): %s", len(text), text[:200])
        return text, metrics

    async def _chat_raw_streaming(
        self,
        messages: list[dict],
        temp: float,
        tokens: int,
        stream_callback,
        thinking_callback,
    ) -> tuple[str, LLMMetrics]:
        """Stream chat_raw response, forwarding tokens via callbacks."""
        async def _start_stream():
            return await self._client.chat(
                model=self._model,
                messages=messages,
                stream=True,
                options=self._build_options(temperature=temp, max_tokens=tokens),
                think=self._enable_thinking,
            )

        stream = await self._retry_with_backoff(_start_stream, label="chat_raw(stream)")

        content_parts: list[str] = []
        thinking_parts: list[str] = []
        last_chunk: dict = {}

        async for chunk in stream:
            msg = chunk.get("message") or {}
            thinking_token = msg.get("thinking") or ""
            content_token = msg.get("content") or ""

            if thinking_token:
                thinking_parts.append(thinking_token)
                if thinking_callback:
                    await thinking_callback(thinking_token)

            if content_token:
                content_parts.append(content_token)
                if stream_callback:
                    await stream_callback(content_token)

            if chunk.get("done"):
                last_chunk = chunk

        text = "".join(content_parts)
        thinking = "".join(thinking_parts) or None
        metrics = self._extract_metrics(last_chunk) if last_chunk else LLMMetrics()
        metrics.thinking = thinking

        logger.info("LLM chat_raw response (%d chars, streamed): %s", len(text), text[:200])
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
            "LLM chat_structured: schema=%s model=%s streaming=%s",
            schema.__name__, self._model, bool(thinking_callback),
        )

        last_error = None
        for attempt in range(2):
            if thinking_callback:
                raw, metrics = await self._chat_structured_streaming(
                    messages, schema, temp, tokens, thinking_callback,
                )
            else:
                async def _chat():
                    return await self._client.chat(
                        model=self._model,
                        messages=messages,
                        format=schema.model_json_schema(),
                        options=self._build_options(temperature=temp, max_tokens=tokens),
                        think=self._enable_thinking,
                    )

                response = await self._retry_with_backoff(
                    _chat, label=f"structured({schema.__name__})",
                )
                raw = response["message"]["content"]
                metrics = self._extract_metrics(response)

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

    async def _chat_structured_streaming(
        self,
        messages: list[dict],
        schema: type[BaseModel],
        temp: float,
        tokens: int,
        thinking_callback,
    ) -> tuple[str, LLMMetrics]:
        """Stream structured output, forwarding thinking tokens via callback."""
        async def _start_stream():
            return await self._client.chat(
                model=self._model,
                messages=messages,
                format=schema.model_json_schema(),
                stream=True,
                options=self._build_options(temperature=temp, max_tokens=tokens),
                think=self._enable_thinking,
            )

        stream = await self._retry_with_backoff(
            _start_stream, label=f"structured({schema.__name__})(stream)",
        )

        content_parts: list[str] = []
        last_chunk: dict = {}

        async for chunk in stream:
            msg = chunk.get("message") or {}
            thinking_token = msg.get("thinking") or ""
            content_token = msg.get("content") or ""

            if thinking_token:
                await thinking_callback(thinking_token)

            if content_token:
                content_parts.append(content_token)

            if chunk.get("done"):
                last_chunk = chunk

        raw = "".join(content_parts)
        metrics = self._extract_metrics(last_chunk) if last_chunk else LLMMetrics()
        return raw, metrics

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

        if not stream_callback and not thinking_callback:
            # Non-streaming path (unchanged)
            async def _chat():
                return await self._client.chat(
                    model=self._model,
                    messages=messages,
                    tools=tools,
                    options=self._build_options(max_tokens=tokens),
                    think=self._enable_thinking,
                )

            response = await self._retry_with_backoff(
                _chat, label="chat_with_tools_single",
            )

            msg = response["message"]
            content = msg.get("content") or ""
            raw_tool_calls = msg.get("tool_calls") or []
            thinking = msg.get("thinking") or None
            metrics = self._extract_metrics(response)
            metrics.thinking = thinking

            tool_calls = [
                ToolCallInfo(
                    name=tc["function"]["name"],
                    arguments=dict(tc["function"].get("arguments") or {}),
                )
                for tc in raw_tool_calls
            ]
            return content, tool_calls, metrics

        # Streaming path — stream content/thinking tokens via callbacks
        async def _start_stream():
            return await self._client.chat(
                model=self._model,
                messages=messages,
                tools=tools,
                stream=True,
                options=self._build_options(max_tokens=tokens),
                think=self._enable_thinking,
            )

        stream = await self._retry_with_backoff(
            _start_stream, label="chat_with_tools_single(stream)",
        )

        content_parts: list[str] = []
        thinking_parts: list[str] = []
        last_chunk: dict = {}
        raw_tool_calls: list = []

        async for chunk in stream:
            msg = chunk.get("message") or {}
            thinking_token = msg.get("thinking") or ""
            content_token = msg.get("content") or ""

            if thinking_token:
                thinking_parts.append(thinking_token)
                if thinking_callback:
                    await thinking_callback(thinking_token)

            if content_token:
                content_parts.append(content_token)
                if stream_callback:
                    await stream_callback(content_token)

            if msg.get("tool_calls"):
                raw_tool_calls = msg["tool_calls"]

            if chunk.get("done"):
                last_chunk = chunk

        content = "".join(content_parts)
        thinking = "".join(thinking_parts) or None
        metrics = self._extract_metrics(last_chunk) if last_chunk else LLMMetrics()
        metrics.thinking = thinking

        tool_calls = [
            ToolCallInfo(
                name=tc["function"]["name"],
                arguments=dict(tc["function"].get("arguments") or {}),
            )
            for tc in raw_tool_calls
        ]
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
        num_predict = max_tokens if max_tokens is not None else self._max_tokens_val

        async def _chat():
            return await self._client.chat(
                model=self._model,
                messages=messages,
                stream=True,
                options=self._build_options(temperature=temp, max_tokens=num_predict),
                think=self._enable_thinking,
            )

        stream = await self._retry_with_backoff(_chat, label="chat_stream")

        token_count = 0
        done_reason = None
        async for chunk in stream:
            if chunk.get("done"):
                done_reason = chunk.get("done_reason", "unknown")
            msg = chunk.get("message") or {}
            thinking_token = msg.get("thinking") or ""
            content_token = msg.get("content") or ""

            if thinking_token and thinking_callback:
                await thinking_callback(thinking_token)

            if content_token:
                token_count += 1
                yield content_token

        logger.info("chat_stream: tokens=%d done_reason=%s", token_count, done_reason)
        if done_reason == "length":
            logger.warning(
                "chat_stream truncated (done_reason=length) — "
                "context window may be too small for prompt + output"
            )

    async def check_health(self) -> bool:
        try:
            models = await self._client.list()
            model_names = [
                m.get("model", "") or m.get("name", "")
                for m in models.get("models", [])
            ]
            return any(self._model in name for name in model_names)
        except Exception:
            logger.exception("Ollama health check failed")
            return False

    # ── Ollama-only methods (not on ABC) ──

    async def generate_completion(
        self, prompt: str, suffix: str = "", timeout: float = 5.0,
    ) -> str:
        """Raw text completion for inline predictions (FIM mode)."""
        if not self._fim_supported:
            return ""
        try:
            response = await asyncio.wait_for(
                self._client.generate(
                    model=self._model,
                    prompt=prompt,
                    suffix=suffix,
                    options=self._build_options(),
                ),
                timeout=timeout,
            )
            return response.get("response", "")
        except asyncio.TimeoutError:
            return ""
        except ConnectionError:
            logger.warning(
                "Inline prediction: cannot reach Ollama at %s", self._url,
            )
            return ""
        except Exception as exc:
            if "does not support insert" in str(exc):
                self._fim_supported = False
                logger.warning(
                    "Model %s does not support FIM/insert mode — "
                    "inline predictions disabled. Set LEAN_AI_INLINE_MODEL "
                    "to a FIM-capable model (e.g. qwen2.5-coder).",
                    self._model,
                )
                return ""
            logger.exception("Completion call failed")
            return ""

    async def embed(
        self,
        texts: list[str],
        model: str | None = None,
        timeout: float = 120.0,
        max_retries: int = 2,
    ) -> list[list[float]]:
        """Generate embeddings for a batch of texts.

        Retries on transient errors with exponential backoff and enforces
        a per-call timeout to prevent indefinite hangs.
        """
        import asyncio

        embed_model = model or settings.embedding_model
        for attempt in range(1, max_retries + 1):
            try:
                response = await asyncio.wait_for(
                    self._embed_client.embed(model=embed_model, input=texts),
                    timeout=timeout,
                )
                return response.get("embeddings", [])
            except asyncio.TimeoutError:
                if attempt < max_retries:
                    logger.warning(
                        "Embed call timed out (attempt %d/%d, %d texts), retrying…",
                        attempt, max_retries, len(texts),
                    )
                    await asyncio.sleep(2.0 * attempt)
                else:
                    raise
            except Exception:
                if attempt < max_retries:
                    logger.warning(
                        "Embed call failed (attempt %d/%d, %d texts), retrying…",
                        attempt, max_retries, len(texts),
                    )
                    await asyncio.sleep(2.0 * attempt)
                else:
                    raise
        return []  # unreachable, but keeps type checkers happy

    # ── Model info ──

    _embedding_ctx_cache: int | None = None

    async def get_embedding_context_window(self) -> int | None:
        """Query Ollama for the embedding model's context window size.

        Caches the result for the lifetime of this provider instance.
        Returns ``None`` if the info cannot be retrieved.
        """
        if self._embedding_ctx_cache is not None:
            return self._embedding_ctx_cache

        import re

        embed_model = settings.embedding_model
        if not embed_model:
            return None
        try:
            info = await self._embed_client.show(name=embed_model)
        except Exception as exc:
            logger.debug("Could not query embedding model info: %s", exc)
            return None

        # Try model_info dict first (key pattern: "{arch}.context_length").
        model_info = info.get("model_info") or {}
        for key, value in model_info.items():
            if key.endswith(".context_length") and isinstance(value, (int, float)):
                self._embedding_ctx_cache = int(value)
                return self._embedding_ctx_cache

        # Fallback: parse PARAMETER num_ctx from modelfile string.
        modelfile = info.get("modelfile") or ""
        match = re.search(r"PARAMETER\s+num_ctx\s+(\d+)", modelfile)
        if match:
            self._embedding_ctx_cache = int(match.group(1))
            return self._embedding_ctx_cache

        return None


# Backward-compat: callers that import LLMClient from this module still work.
# The facade is the real LLMClient now.
from lean_ai.llm.facade import LLMClient  # noqa: E402, F401

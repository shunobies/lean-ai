"""Ollama LLM provider — wraps the Ollama Python SDK."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator

import ollama as ollama_lib
from pydantic import BaseModel

from lean_ai.config import settings
from lean_ai.llm.base import (
    LLMMetrics,
    LLMProvider,
    StructuredOutputError,
    ToolCallInfo,
    retry_with_backoff,
    validate_structured_output,
)

logger = logging.getLogger(__name__)

_TRANSIENT_ERRORS = (ConnectionError, TimeoutError, OSError)
_UNSET = object()


def _inject_schema_into_messages(
    messages: list[dict],
    schema: type[BaseModel],
) -> list[dict]:
    """Return a copy of *messages* with the schema inlined into the system prompt.

    Ollama's ``format=`` parameter enforces the schema via constrained decoding
    at inference time, but the schema itself never reaches the model's context.
    Without seeing the schema, small / non-reasoning models produce JSON that's
    technically valid but semantically poor (fields filled in the wrong shape,
    nested types collapsed into strings, enums miswritten).

    Inlining ``schema.model_json_schema()`` into the system message gives the
    model both halves: constrained decoding for safety + structural context
    for quality.  Mirrors the Anthropic provider's approach.

    If there's no system message, one is prepended.  Otherwise the schema
    block is appended to the existing system message.
    """
    schema_json = json.dumps(schema.model_json_schema(), indent=2)
    schema_block = (
        "\n\n"
        "Respond with a JSON object that matches this schema exactly. "
        "Populate every required field; use empty lists / empty strings for "
        "optional fields you cannot determine.\n\n"
        "```json\n"
        f"{schema_json}\n"
        "```"
    )

    augmented: list[dict] = []
    system_injected = False
    for msg in messages:
        if msg.get("role") == "system" and not system_injected:
            new_msg = dict(msg)
            new_msg["content"] = (new_msg.get("content") or "") + schema_block
            augmented.append(new_msg)
            system_injected = True
        else:
            augmented.append(msg)

    if not system_injected:
        augmented.insert(
            0,
            {
                "role": "system",
                "content": "Produce JSON matching the schema below." + schema_block,
            },
        )

    return augmented


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
        temperature: float | None | object = _UNSET,
        top_p: float | None | object = _UNSET,
        top_k: int | None | object = _UNSET,
        repeat_penalty: float | None | object = _UNSET,
        min_p: float | None | object = _UNSET,
        presence_penalty: float | None | object = _UNSET,
        enable_thinking: bool | None = None,
        preserve_thinking: bool = False,
        reasoning_effort: str = "",
    ):
        effective_url = ollama_url or settings.ollama_url
        self._url = effective_url
        self._client = ollama_lib.AsyncClient(host=effective_url)
        self._model = model or settings.ollama_model
        self._max_tokens_val = max_tokens if max_tokens is not None else settings.ollama_max_tokens
        self._context_window_val = (
            context_window if context_window is not None else settings.ollama_context_window
        )
        self._temperature = settings.ollama_temperature if temperature is _UNSET else temperature
        self._top_p = settings.ollama_top_p if top_p is _UNSET else top_p
        self._top_k = settings.ollama_top_k if top_k is _UNSET else top_k
        self._repeat_penalty = (
            settings.ollama_repeat_penalty if repeat_penalty is _UNSET else repeat_penalty
        )
        self._min_p = settings.ollama_min_p if min_p is _UNSET else min_p
        self._presence_penalty = (
            settings.ollama_presence_penalty if presence_penalty is _UNSET else presence_penalty
        )
        self._enable_thinking = (
            enable_thinking if enable_thinking is not None else settings.enable_thinking
        )
        self._preserve_thinking = preserve_thinking
        # Client-side interrupt configuration.  Cloud providers enforce
        # reasoning budgets natively; Ollama has no such mechanism, so we
        # count thinking tokens during streaming and break the stream when
        # the configured soft limit or universal safety rail is exceeded.
        self._reasoning_effort = reasoning_effort or ""

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
        self,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        """Build the Ollama options dict.

        Sampling params are only added when explicitly configured.  Blank
        (None) means "omit from options" so each model can use its own
        Ollama/Modelfile defaults.
        """
        effective_temperature = temperature if temperature is not None else self._temperature
        effective_max_tokens = max_tokens if max_tokens is not None else self._max_tokens_val
        opts: dict = {
            "num_predict": effective_max_tokens,
            "num_ctx": self._context_window_val,
        }
        if effective_temperature is not None:
            opts["temperature"] = effective_temperature
        if self._top_p is not None:
            opts["top_p"] = self._top_p
        if self._top_k is not None:
            opts["top_k"] = self._top_k
        if self._repeat_penalty is not None:
            opts["repeat_penalty"] = self._repeat_penalty
        if self._min_p is not None:
            opts["min_p"] = self._min_p
        if self._presence_penalty is not None:
            opts["presence_penalty"] = self._presence_penalty
        return opts

    def _build_chat_template_kwargs(self) -> dict:
        """Ollama uses a compiled ``RENDERER`` (Go code), not a Jinja template,
        so the vLLM-style ``chat_template_kwargs`` has nowhere to land here.

        Preserve-thinking behaviour for Ollama is handled in
        :func:`lean_ai.llm.facade` by folding the ``thinking`` blob into
        the assistant message's ``content`` as ``<think>...</think>``.  The
        renderer then forwards the tokens like any other content.

        This helper is retained for API parity with the chat-call splat
        pattern but always returns an empty dict.
        """
        return {}

    def _thinking_budget_exceeded(self, thinking_chars: int) -> bool:
        """Client-side interrupt check — should we abort streaming now?

        Applies two gates:
        1. The per-role ``reasoning_effort`` soft limit (``low`` / ``medium``
           / ``high``; ``""`` and ``"max"`` skip this gate).
        2. The universal ``settings.max_thinking_tokens`` safety rail (fires
           even when effort is ``"max"`` / ``""`` — catches runaway loops).

        Argument is accumulated thinking **characters**.  Approximate
        tokens = ``(chars + 3) // 4`` (rounds up for safety).
        """
        from lean_ai.config import reasoning_effort_to_ollama_limit

        approx_tokens = (thinking_chars + 3) // 4
        effort_limit = reasoning_effort_to_ollama_limit(self._reasoning_effort)
        if effort_limit is not None and approx_tokens >= effort_limit:
            return True
        return approx_tokens >= settings.max_thinking_tokens

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
            "LLM chat_raw: model=%s messages=%d temp=%s max_tokens=%d streaming=%s",
            self._model,
            len(messages),
            temp if temp is not None else "ollama-default",
            tokens,
            bool(stream_callback or thinking_callback),
        )

        if stream_callback or thinking_callback:
            return await self._chat_raw_streaming(
                messages,
                temp,
                tokens,
                stream_callback,
                thinking_callback,
            )

        async def _chat():
            return await self._client.chat(
                model=self._model,
                messages=messages,
                options=self._build_options(temperature=temp, max_tokens=tokens),
                think=self._enable_thinking,
                **self._build_chat_template_kwargs(),
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
                **self._build_chat_template_kwargs(),
            )

        stream = await self._retry_with_backoff(_start_stream, label="chat_raw(stream)")

        content_parts: list[str] = []
        thinking_parts: list[str] = []
        thinking_chars = 0
        budget_exceeded = False
        last_chunk: dict = {}

        async for chunk in stream:
            msg = chunk.get("message") or {}
            thinking_token = msg.get("thinking") or ""
            content_token = msg.get("content") or ""

            if thinking_token:
                thinking_parts.append(thinking_token)
                thinking_chars += len(thinking_token)
                if thinking_callback:
                    await thinking_callback(thinking_token)
                if self._thinking_budget_exceeded(thinking_chars):
                    budget_exceeded = True
                    logger.info(
                        "chat_raw(stream): thinking budget exceeded at ~%d tokens; aborting stream",
                        (thinking_chars + 3) // 4,
                    )
                    break

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
        metrics.thinking_token_count = (thinking_chars + 3) // 4
        metrics.thinking_budget_exceeded = budget_exceeded

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
        retry_on_validation_error: bool = True,
    ) -> tuple[BaseModel, LLMMetrics]:
        temp = temperature if temperature is not None else self._temperature
        tokens = max_tokens if max_tokens is not None else self._max_tokens_val

        logger.info(
            "LLM chat_structured: schema=%s model=%s streaming=%s",
            schema.__name__,
            self._model,
            bool(thinking_callback),
        )

        augmented_messages = _inject_schema_into_messages(messages, schema)

        last_error = None
        for attempt in range(2):
            if thinking_callback:
                raw, metrics = await self._chat_structured_streaming(
                    augmented_messages,
                    schema,
                    temp,
                    tokens,
                    thinking_callback,
                )
            else:

                async def _chat():
                    return await self._client.chat(
                        model=self._model,
                        messages=augmented_messages,
                        format=schema.model_json_schema(),
                        options=self._build_options(temperature=temp, max_tokens=tokens),
                        think=self._enable_thinking,
                        **self._build_chat_template_kwargs(),
                    )

                response = await self._retry_with_backoff(
                    _chat,
                    label=f"structured({schema.__name__})",
                )
                raw = response["message"]["content"]
                metrics = self._extract_metrics(response)

            try:
                parsed, _cleaned = validate_structured_output(raw, schema)
                return parsed, metrics
            except StructuredOutputError as exc:
                last_error = exc
                if retry_on_validation_error and attempt == 0:
                    logger.warning(
                        "Schema validation failed for %s, retrying: %s",
                        schema.__name__,
                        exc.summary,
                    )
                    continue
                logger.error(
                    "Schema validation failed after retry for %s. Raw: %s",
                    schema.__name__,
                    exc.cleaned_output[:1000],
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
                **self._build_chat_template_kwargs(),
            )

        stream = await self._retry_with_backoff(
            _start_stream,
            label=f"structured({schema.__name__})(stream)",
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

        logger.info(
            "LLM chat_with_tools_single: model=%s messages=%d tools=%d max_tokens=%d streaming=%s",
            self._model,
            len(messages),
            len(tools),
            tokens,
            bool(stream_callback or thinking_callback),
        )

        if not stream_callback and not thinking_callback:
            # Non-streaming path (unchanged)
            async def _chat():
                return await self._client.chat(
                    model=self._model,
                    messages=messages,
                    tools=tools,
                    options=self._build_options(max_tokens=tokens),
                    think=self._enable_thinking,
                    **self._build_chat_template_kwargs(),
                )

            response = await self._retry_with_backoff(
                _chat,
                label="chat_with_tools_single",
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
            logger.info(
                "LLM chat_with_tools_single response: content_chars=%d tool_calls=%d",
                len(content),
                len(tool_calls),
            )
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
                **self._build_chat_template_kwargs(),
            )

        stream = await self._retry_with_backoff(
            _start_stream,
            label="chat_with_tools_single(stream)",
        )

        content_parts: list[str] = []
        thinking_parts: list[str] = []
        thinking_chars = 0
        budget_exceeded = False
        last_chunk: dict = {}
        raw_tool_calls: list = []

        async for chunk in stream:
            msg = chunk.get("message") or {}
            thinking_token = msg.get("thinking") or ""
            content_token = msg.get("content") or ""

            if thinking_token:
                thinking_parts.append(thinking_token)
                thinking_chars += len(thinking_token)
                if thinking_callback:
                    await thinking_callback(thinking_token)
                if self._thinking_budget_exceeded(thinking_chars):
                    budget_exceeded = True
                    logger.info(
                        "chat_with_tools_single(stream): thinking budget "
                        "exceeded at ~%d tokens; aborting stream",
                        (thinking_chars + 3) // 4,
                    )
                    break

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
        metrics.thinking_token_count = (thinking_chars + 3) // 4
        metrics.thinking_budget_exceeded = budget_exceeded

        tool_calls = [
            ToolCallInfo(
                name=tc["function"]["name"],
                arguments=dict(tc["function"].get("arguments") or {}),
            )
            for tc in raw_tool_calls
        ]
        logger.info(
            "LLM chat_with_tools_single response (streamed): content_chars=%d tool_calls=%d",
            len(content),
            len(tool_calls),
        )
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
                **self._build_chat_template_kwargs(),
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
                m.get("model", "") or m.get("name", "") for m in models.get("models", [])
            ]
            return any(self._model in name for name in model_names)
        except Exception:
            logger.exception("Ollama health check failed")
            return False

    # ── Ollama-only methods (not on ABC) ──

    async def generate_completion(
        self,
        prompt: str,
        suffix: str = "",
        timeout: float = 5.0,
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
                "Inline prediction: cannot reach Ollama at %s",
                self._url,
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
        max_retries: int = 2,
    ) -> list[list[float]]:
        """Generate embeddings for a batch of texts.

        Retries on *transient* errors (429 rate-limit, 500/502 server
        errors, connection resets) with exponential backoff.  Permanent
        errors (400 bad request, 404 model not found) are raised
        immediately — retrying would not help.

        No application-level timeout is applied:

        - Ollama queues concurrent requests internally, so a busy model
          is not a failure — the ``await`` naturally waits.
        - If Ollama crashes, httpx raises ``ConnectionError`` immediately.
        - Large batches can legitimately take minutes; a fixed timeout
          would kill valid work.
        """
        import asyncio

        from ollama import ResponseError

        embed_model = model or settings.embedding_model

        # HTTP codes that are permanent — retrying won't help.
        permanent_codes = {400, 404}

        for attempt in range(1, max_retries + 1):
            try:
                response = await self._embed_client.embed(
                    model=embed_model,
                    input=texts,
                )
                return response.get("embeddings", [])
            except ResponseError as exc:
                if exc.status_code in permanent_codes:
                    raise  # bad request / model not found — don't retry
                # Retryable: 429 (rate limit), 500/502 (server error),
                # -1 (inline streaming error like model crash or GPU OOM).
                if attempt < max_retries:
                    logger.warning(
                        "Embed call failed (status=%d, attempt %d/%d, %d texts): %s — retrying…",
                        exc.status_code,
                        attempt,
                        max_retries,
                        len(texts),
                        exc.error,
                    )
                    await asyncio.sleep(2.0 * attempt)
                else:
                    raise
            except ConnectionError:
                if attempt < max_retries:
                    logger.warning(
                        "Embed connection lost (attempt %d/%d, %d texts), retrying…",
                        attempt,
                        max_retries,
                        len(texts),
                    )
                    await asyncio.sleep(2.0 * attempt)
                else:
                    raise
        return []  # unreachable, but keeps type checkers happy

    # ── Model info ──

    _embedding_ctx_cache: int | None = None

    async def get_embedding_context_window(self) -> int | None:
        """Return the embedding model's context window size from config.

        Reads ``settings.embedding_context_window`` only — no Ollama
        round-trip. The prior ``ollama show`` auto-detect path was
        removed because it provided no value at runtime (the default
        8192 is sufficient for batch sizing) and repeatedly wedged
        ``/init`` when Ollama was slow. Users with larger embedding
        models can raise ``LEAN_AI_EMBEDDING_CONTEXT_WINDOW``.
        """
        if self._embedding_ctx_cache is not None:
            return self._embedding_ctx_cache
        if settings.embedding_context_window > 0:
            self._embedding_ctx_cache = settings.embedding_context_window
            return self._embedding_ctx_cache
        return None


# Backward-compat: callers that import LLMClient from this module still work.
# The facade is the real LLMClient now.
from lean_ai.llm.facade import LLMClient  # noqa: E402, F401

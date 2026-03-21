"""Ollama LLM provider — wraps the Ollama Python SDK."""

import asyncio
import logging
from collections.abc import AsyncIterator

import ollama as ollama_lib
from pydantic import BaseModel, ValidationError

from lean_ai.config import settings
from lean_ai.llm.base import LLMMetrics, LLMProvider, ToolCallInfo

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
            prompt_tokens = response.get("prompt_eval_count", 0) or 0
            tps = (
                round(eval_count / (eval_duration / 1_000_000_000), 1)
                if eval_count and eval_duration and eval_duration > 0
                else None
            )
            return LLMMetrics(
                prompt_tokens=prompt_tokens,
                completion_tokens=eval_count,
                tokens_per_second=tps,
                stop_reason=response.get("done_reason"),
            )
        except Exception:
            return LLMMetrics()

    async def _retry_with_backoff(self, coro_factory, label: str = "LLM call"):
        """Retry an async callable with exponential backoff for transient errors."""
        max_retries = settings.llm_retry_max
        base_delay = settings.llm_retry_base_delay

        for attempt in range(max_retries + 1):
            try:
                return await coro_factory()
            except _TRANSIENT_ERRORS as exc:
                if attempt >= max_retries:
                    raise
                delay = base_delay * (2**attempt)
                logger.warning(
                    "%s failed (attempt %d/%d), retrying in %.1fs: %s",
                    label, attempt + 1, max_retries + 1, delay, exc,
                )
                await asyncio.sleep(delay)
            except ollama_lib.ResponseError as exc:
                if exc.status_code and exc.status_code >= 500 and attempt < max_retries:
                    delay = base_delay * (2**attempt)
                    logger.warning(
                        "%s server error %d (attempt %d/%d), retrying in %.1fs: %s",
                        label, exc.status_code, attempt + 1, max_retries + 1, delay, exc,
                    )
                    await asyncio.sleep(delay)
                else:
                    raise

    async def chat_raw(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> tuple[str, LLMMetrics]:
        temp = temperature if temperature is not None else self._temperature
        tokens = max_tokens if max_tokens is not None else self._max_tokens_val

        logger.info(
            "LLM chat_raw: model=%s messages=%d temp=%.1f max_tokens=%d",
            self._model, len(messages), temp, tokens,
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

    async def chat_structured(
        self,
        messages: list[dict],
        schema: type[BaseModel],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> tuple[BaseModel, LLMMetrics]:
        temp = temperature if temperature is not None else self._temperature
        tokens = max_tokens if max_tokens is not None else self._max_tokens_val

        logger.info(
            "LLM chat_structured: schema=%s model=%s", schema.__name__, self._model,
        )

        async def _chat():
            return await self._client.chat(
                model=self._model,
                messages=messages,
                format=schema.model_json_schema(),
                options=self._build_options(temperature=temp, max_tokens=tokens),
                think=self._enable_thinking,
            )

        last_error = None
        for attempt in range(2):
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

    async def chat_with_tools_single(
        self,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int | None = None,
    ) -> tuple[str, list[ToolCallInfo], LLMMetrics]:
        tokens = max_tokens or self._max_tokens_val

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

    async def chat_stream(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        temp = temperature if temperature is not None else self._temperature
        num_predict = max_tokens if max_tokens is not None else self._max_tokens_val

        async def _chat():
            return await self._client.chat(
                model=self._model,
                messages=messages,
                stream=True,
                options=self._build_options(temperature=temp, max_tokens=num_predict),
                think=False,  # Inline predictions: no thinking overhead
            )

        stream = await self._retry_with_backoff(_chat, label="chat_stream")

        async for chunk in stream:
            token = chunk["message"]["content"]
            if token:
                yield token

    async def check_health(self) -> bool:
        try:
            models = await self._client.list()
            model_names = [m.get("name", "") for m in models.get("models", [])]
            return any(self._model in name for name in model_names)
        except Exception:
            logger.exception("Ollama health check failed")
            return False

    # ── Ollama-only methods (not on ABC) ──

    async def generate_completion(
        self, prompt: str, suffix: str = "", timeout: float = 5.0,
    ) -> str:
        """Raw text completion for inline predictions (FIM mode)."""
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
        except Exception:
            logger.exception("Completion call failed")
            return ""

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        """Generate embeddings for a batch of texts."""
        embed_model = model or settings.embedding_model
        response = await self._embed_client.embed(model=embed_model, input=texts)
        return response.get("embeddings", [])


# Backward-compat: callers that import LLMClient from this module still work.
# The facade is the real LLMClient now.
from lean_ai.llm.facade import LLMClient  # noqa: E402, F401

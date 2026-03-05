"""Async Ollama client wrapper with tool calling and streaming."""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

import ollama as ollama_lib
from pydantic import BaseModel, ValidationError

from lean_ai.config import settings

logger = logging.getLogger(__name__)

_TRANSIENT_ERRORS = (ConnectionError, TimeoutError, OSError)


@dataclass
class ToolCall:
    """Record of an executed tool call."""

    tool_name: str
    parameters: dict = field(default_factory=dict)
    description: str = ""


class LLMClient:
    """Async wrapper around the Ollama Python SDK.

    Provides:
    - chat_raw: arbitrary multi-turn conversation
    - chat_structured: JSON-schema-enforced structured output
    - chat_with_tools: multi-turn tool calling loop
    - generate_completion: raw text continuation (for inline predictions)
    - embed: batch embedding generation
    """

    def __init__(
        self,
        ollama_url: str | None = None,
        embed_ollama_url: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        context_window: int | None = None,
        temperature: float | None = None,
    ):
        effective_url = ollama_url or settings.ollama_url
        self._url = effective_url
        self._client = ollama_lib.AsyncClient(host=effective_url)
        self.last_stream_metrics: dict | None = None
        self.last_chat_metrics: dict | None = None
        self._model = model or settings.ollama_model
        self._max_tokens = max_tokens if max_tokens is not None else settings.ollama_max_tokens
        self._context_window = (
            context_window if context_window is not None else settings.ollama_context_window
        )
        self._temperature = (
            temperature if temperature is not None else settings.ollama_temperature
        )

        effective_embed_url = embed_ollama_url or settings.effective_embedding_url
        if effective_embed_url != effective_url:
            self._embed_client = ollama_lib.AsyncClient(host=effective_embed_url)
        else:
            self._embed_client = self._client

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
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Send a multi-turn conversation and return the response text."""
        temp = temperature if temperature is not None else self._temperature
        tokens = max_tokens if max_tokens is not None else self._max_tokens

        logger.info(
            "LLM chat_raw: model=%s messages=%d temp=%.1f max_tokens=%d",
            self._model, len(messages), temp, tokens,
        )

        self.last_chat_metrics = None

        async def _chat():
            return await self._client.chat(
                model=self._model,
                messages=messages,
                options={
                    "temperature": temp,
                    "num_predict": tokens,
                    "num_ctx": self._context_window,
                },
            )

        response = await self._retry_with_backoff(_chat, label="chat_raw")
        text = response["message"]["content"]

        try:
            eval_count = response.get("eval_count", 0) or 0
            eval_duration = response.get("eval_duration", 0) or 0
            prompt_tokens = response.get("prompt_eval_count", 0) or 0
            tps = (
                round(eval_count / (eval_duration / 1_000_000_000), 1)
                if eval_count and eval_duration and eval_duration > 0
                else None
            )
            self.last_chat_metrics = {
                "tokens_per_second": tps,
                "eval_count": eval_count,
                "prompt_tokens": prompt_tokens,
            }
        except Exception:
            pass

        logger.info("LLM chat_raw response (%d chars): %s", len(text), text[:200])
        return text

    async def chat_structured(
        self,
        messages: list[dict[str, str]],
        schema: type[BaseModel],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> BaseModel:
        """Send a conversation and parse the response into a Pydantic model.

        Uses Ollama's JSON schema enforcement so the response is guaranteed
        to match the schema (assuming the model cooperates).
        """
        temp = temperature if temperature is not None else self._temperature
        tokens = max_tokens if max_tokens is not None else self._max_tokens

        logger.info(
            "LLM chat_structured: schema=%s model=%s", schema.__name__, self._model,
        )

        async def _chat():
            return await self._client.chat(
                model=self._model,
                messages=messages,
                format=schema.model_json_schema(),
                options={
                    "temperature": temp,
                    "num_predict": tokens,
                    "num_ctx": self._context_window,
                },
            )

        last_error = None
        for attempt in range(2):
            response = await self._retry_with_backoff(
                _chat, label=f"structured({schema.__name__})",
            )
            raw = response["message"]["content"]
            try:
                return schema.model_validate_json(raw)
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

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Stream tokens for real-time display."""
        temp = temperature if temperature is not None else self._temperature
        num_predict = max_tokens if max_tokens is not None else self._max_tokens

        async def _chat():
            return await self._client.chat(
                model=self._model,
                messages=messages,
                stream=True,
                options={
                    "temperature": temp,
                    "num_predict": num_predict,
                    "num_ctx": self._context_window,
                },
            )

        stream = await self._retry_with_backoff(_chat, label="chat_stream")

        async for chunk in stream:
            token = chunk["message"]["content"]
            if token:
                yield token

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_executor_fn: Callable,
        *,
        max_turns: int = 50,
        max_tokens: int | None = None,
        task_reminder: str | Callable[[], str] | None = None,
        reminder_interval: int = 10,
        on_tool_call: Callable | None = None,
        on_tool_result: Callable | None = None,
        on_content: Callable | None = None,
    ) -> tuple[list[ToolCall], str]:
        """Multi-turn tool calling loop using Ollama's native tools= parameter.

        Sends messages with tool definitions to the LLM. When the response
        contains tool_calls, executes each one via tool_executor_fn, appends
        results, and calls the LLM again. Repeats until the LLM responds
        with text-only (no tool_calls) or max_turns is reached.

        If the model produces a text-only response but has not yet made any
        write operations (edit_file, create_file), it gets one nudge asking
        it to continue with tool calls before the loop exits.

        Returns (executed_tool_calls, final_explanation).
        """
        write_tools = {"edit_file", "create_file"}
        nudge_msg = (
            "You responded with text but have not made any file changes yet. "
            "The task is not complete. Continue by calling the appropriate "
            "tools (edit_file, create_file, etc.) to implement the changes."
        )

        tokens = max_tokens or self._max_tokens
        executed: list[ToolCall] = []
        explanation_parts: list[str] = []
        nudged = False

        for turn in range(max_turns):
            logger.info(
                "chat_with_tools turn %d/%d: %d messages",
                turn + 1, max_turns, len(messages),
            )

            async def _chat():
                return await self._client.chat(
                    model=self._model,
                    messages=messages,
                    tools=tools,
                    options={
                        "temperature": self._temperature,
                        "num_predict": tokens,
                        "num_ctx": self._context_window,
                    },
                )

            response = await self._retry_with_backoff(
                _chat, label=f"chat_with_tools(turn={turn + 1})",
            )

            msg = response["message"]
            content = msg.get("content") or ""
            tool_calls = msg.get("tool_calls") or []

            if content.strip():
                explanation_parts.append(content.strip())
                if on_content:
                    await on_content(content.strip())

            if not tool_calls:
                # Check if the model has performed any write operations yet
                has_written = any(tc.tool_name in write_tools for tc in executed)
                if not has_written and not nudged:
                    # Model stopped before making changes — nudge it to continue
                    logger.info(
                        "chat_with_tools: no writes yet, nudging model to continue"
                    )
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "user", "content": nudge_msg})
                    nudged = True
                    continue
                break

            # Build assistant message with tool_calls for conversation history
            assistant_msg: dict = {
                "role": "assistant",
                "content": content,
                "tool_calls": [
                    {
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": dict(tc["function"]["arguments"]),
                        },
                    }
                    for tc in tool_calls
                ],
            }
            messages.append(assistant_msg)

            # Execute each tool call
            for tc in tool_calls:
                fn = tc["function"]
                name = fn["name"]
                arguments = dict(fn.get("arguments") or {})

                if on_tool_call:
                    await on_tool_call(name, arguments)

                try:
                    result_str = await tool_executor_fn(name, arguments)
                except Exception as exc:
                    result_str = f"ERROR: {exc}"
                    logger.warning("chat_with_tools: tool %s raised: %s", name, exc)

                executed.append(ToolCall(
                    tool_name=name,
                    parameters=arguments,
                    description=f"{name} {arguments.get('path', arguments.get('command', ''))}",
                ))

                if on_tool_result:
                    await on_tool_result(name, result_str)

                messages.append({"role": "tool", "content": result_str})

            # Inject periodic task reminder to keep the original task in
            # the model's active attention window.  Ollama truncates from the
            # beginning when messages exceed num_ctx, so the system prompt and
            # original task are the first things evicted.
            if (
                task_reminder
                and reminder_interval > 0
                and (turn + 1) % reminder_interval == 0
                and turn + 1 < max_turns
            ):
                reminder_text = task_reminder() if callable(task_reminder) else task_reminder
                logger.info(
                    "chat_with_tools: injecting task reminder at turn %d (%d chars)",
                    turn + 1, len(reminder_text),
                )
                messages.append({"role": "user", "content": reminder_text})
        else:
            logger.warning(
                "chat_with_tools: reached max_turns=%d without completion", max_turns,
            )

        return executed, "\n".join(explanation_parts)

    async def generate_completion(self, prompt: str, timeout: float = 5.0) -> str:
        """Raw text completion for inline predictions.

        Uses /api/generate (not chat). Timeout prevents stale predictions
        when GPU is busy with the main model.
        """
        try:
            response = await asyncio.wait_for(
                self._client.generate(
                    model=self._model,
                    prompt=prompt,
                    options={
                        "temperature": self._temperature,
                        "num_predict": self._max_tokens,
                        "num_ctx": self._context_window,
                    },
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

    async def check_health(self) -> bool:
        """Check if Ollama is reachable and the model is available."""
        try:
            models = await self._client.list()
            model_names = [m.get("name", "") for m in models.get("models", [])]
            return any(self._model in name for name in model_names)
        except Exception:
            logger.exception("Ollama health check failed")
            return False

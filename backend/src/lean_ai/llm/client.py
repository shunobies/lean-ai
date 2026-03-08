"""Async Ollama client wrapper with tool calling and streaming."""

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

import ollama as ollama_lib
from pydantic import BaseModel, ValidationError

from lean_ai.config import settings

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
        self._top_p = settings.ollama_top_p
        self._top_k = settings.ollama_top_k
        self._repeat_penalty = settings.ollama_repeat_penalty

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
                    "top_p": self._top_p,
                    "top_k": self._top_k,
                    "repeat_penalty": self._repeat_penalty,
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
                    "top_p": self._top_p,
                    "top_k": self._top_k,
                    "repeat_penalty": self._repeat_penalty,
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
                    "top_p": self._top_p,
                    "top_k": self._top_k,
                    "repeat_penalty": self._repeat_penalty,
                    "num_predict": num_predict,
                    "num_ctx": self._context_window,
                },
            )

        stream = await self._retry_with_backoff(_chat, label="chat_stream")

        async for chunk in stream:
            token = chunk["message"]["content"]
            if token:
                yield token

    async def _maybe_compress(
        self, messages: list[dict], threshold: float, preserve: float,
    ) -> None:
        """Compress older conversation history in-place when nearing context limits.

        Estimates token usage from message content length.  When estimated
        tokens exceed *threshold* fraction of the context window, older
        messages (after the system prompt) are summarised by an LLM call
        and replaced with a single summary message.  The most recent
        *preserve* fraction of the conversation is kept intact.
        """
        est_tokens = sum(len(m.get("content") or "") for m in messages) // 4
        limit = int(threshold * self._context_window)

        if est_tokens < limit:
            return
        if len(messages) < 4:
            return

        # Walk backward to find split point — keep `preserve` fraction.
        # If total content is small enough that preserve covers everything,
        # split at the midpoint to still compress the older half.
        preserve_tokens = int(preserve * self._context_window)
        accum = 0
        split = 2  # Default: compress everything except system + first message
        for idx in range(len(messages) - 1, 0, -1):
            accum += len(messages[idx].get("content") or "") // 4
            if accum >= preserve_tokens:
                split = idx
                break

        # Ensure split lands on a valid boundary (not mid-tool-exchange).
        # Walk backward to find a user message or a non-tool message.
        while split > 1 and messages[split].get("role") == "tool":
            split -= 1
        # Also skip the assistant message with tool_calls that precedes tools
        if (
            split > 1
            and messages[split].get("role") == "assistant"
            and messages[split].get("tool_calls")
        ):
            split -= 1

        if split <= 1:
            return  # Nothing to compress

        old_messages = messages[1:split]
        if not old_messages:
            return

        # Build summary from old messages
        history_text = "\n".join(
            f"[{m.get('role', '?')}] {(m.get('content') or '')[:500]}"
            for m in old_messages
        )

        compress_prompt = (
            "Summarize the following conversation history into a concise state snapshot.\n"
            "Include: what was accomplished, what files were modified, current errors "
            "or blockers, and what remains to be done.\n"
            "Be factual and specific. Preserve file paths, function names, and error "
            "messages exactly.\n\n"
            f"{history_text}"
        )

        try:
            summary = await self.chat_raw(
                messages=[{"role": "user", "content": compress_prompt}],
                max_tokens=2048,
            )
            if not summary.strip():
                return
        except Exception:
            logger.warning("chat_with_tools: compression LLM call failed, skipping")
            return

        logger.info(
            "chat_with_tools: compressed %d messages (%d→%d est. tokens)",
            len(old_messages), est_tokens,
            sum(len(m.get("content") or "") for m in messages[split:]) // 4
            + len(summary) // 4,
        )
        messages[1:split] = [
            {"role": "user", "content": f"[Previous conversation summary]\n{summary}"},
        ]

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
        loop_detection_threshold: int | None = None,
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

        # Loop detection state
        ld_threshold = (
            loop_detection_threshold
            if loop_detection_threshold is not None
            else settings.loop_detection_threshold
        )
        prev_tool_hash: str | None = None
        consecutive_count: int = 0

        # 0 means unlimited — use a practically infinite ceiling
        effective_max = max_turns if max_turns > 0 else 2**31

        for turn in range(effective_max):
            logger.info(
                "chat_with_tools turn %d/%s: %d messages",
                turn + 1,
                max_turns if max_turns > 0 else "∞",
                len(messages),
            )

            async def _chat():
                return await self._client.chat(
                    model=self._model,
                    messages=_sanitize_messages(messages),
                    tools=tools,
                    options={
                        "temperature": self._temperature,
                        "top_p": self._top_p,
                        "top_k": self._top_k,
                        "repeat_penalty": self._repeat_penalty,
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
                has_done_anything = len(executed) > 0
                if not has_written and not has_done_anything and not nudged:
                    # Model stopped on first turn without calling any tools —
                    # nudge it once.  Skip the nudge if the model has already
                    # executed tools (e.g. run_tests/run_lint steps that don't
                    # involve file writes).
                    logger.info(
                        "chat_with_tools: no tool calls yet, nudging model to continue"
                    )
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "user", "content": nudge_msg})
                    nudged = True
                    continue
                break

            # If the only tool call is update_scratchpad and the model has
            # already made write operations, treat this as a completion
            # signal — the model is posting a final summary.
            only_scratchpad = (
                len(tool_calls) == 1
                and tool_calls[0]["function"]["name"] == "update_scratchpad"
            )
            has_written = any(tc.tool_name in write_tools for tc in executed)
            if only_scratchpad and has_written:
                scratchpad_exit = True
            else:
                scratchpad_exit = False

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

                # Loop detection: hash tool name + args, track consecutive
                if ld_threshold > 0:
                    call_sig = f"{name}:{json.dumps(arguments, sort_keys=True)}"
                    call_hash = hashlib.sha256(call_sig.encode()).hexdigest()
                    if call_hash == prev_tool_hash:
                        consecutive_count += 1
                    else:
                        consecutive_count = 1
                        prev_tool_hash = call_hash

                    if consecutive_count >= ld_threshold:
                        logger.warning(
                            "chat_with_tools: loop detected — %s called %d times "
                            "with identical arguments",
                            name, consecutive_count,
                        )
                        messages.append({
                            "role": "user",
                            "content": (
                                f"You have called {name} with identical arguments "
                                f"{consecutive_count} times consecutively and it "
                                f"keeps failing. Try a different approach — read "
                                f"the file first, check the error, or use different "
                                f"arguments."
                            ),
                        })
                        consecutive_count = 0

            # If the model's only action was a scratchpad update after
            # already making file changes, it's posting a final summary.
            # Exit the loop instead of prompting for another turn.
            if scratchpad_exit:
                logger.info(
                    "chat_with_tools: exiting — sole scratchpad update after writes"
                )
                break

            # Compress conversation history if approaching context limits
            await self._maybe_compress(
                messages,
                threshold=settings.compression_threshold,
                preserve=settings.compression_preserve,
            )

            # Inject periodic task reminder to keep the original task in
            # the model's active attention window.  Ollama truncates from the
            # beginning when messages exceed num_ctx, so the system prompt and
            # original task are the first things evicted.
            if (
                task_reminder
                and reminder_interval > 0
                and (turn + 1) % reminder_interval == 0
                and turn + 1 < effective_max
            ):
                reminder_text = task_reminder() if callable(task_reminder) else task_reminder
                logger.info(
                    "chat_with_tools: injecting task reminder at turn %d (%d chars)",
                    turn + 1, len(reminder_text),
                )
                messages.append({"role": "user", "content": reminder_text})
        else:
            logger.warning(
                "chat_with_tools: reached max_turns=%s without completion",
                max_turns if max_turns > 0 else "∞",
            )

        return executed, "\n".join(explanation_parts)

    async def generate_completion(
        self, prompt: str, suffix: str = "", timeout: float = 5.0,
    ) -> str:
        """Raw text completion for inline predictions.

        Uses /api/generate (not chat). When *suffix* is provided, Ollama
        uses Fill-in-the-Middle (FIM) mode for context-aware infilling.
        Timeout prevents stale predictions when GPU is busy with the main model.
        """
        try:
            response = await asyncio.wait_for(
                self._client.generate(
                    model=self._model,
                    prompt=prompt,
                    suffix=suffix,
                    options={
                        "temperature": self._temperature,
                        "top_p": self._top_p,
                        "top_k": self._top_k,
                        "repeat_penalty": self._repeat_penalty,
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

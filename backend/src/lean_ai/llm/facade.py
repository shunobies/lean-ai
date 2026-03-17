"""LLMClient facade — delegates to an LLMProvider for chat, keeps Ollama for FIM/embed."""

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator, Callable

from pydantic import BaseModel

from lean_ai.config import settings
from lean_ai.llm.base import LLMMetrics, LLMProvider, ToolCall, ToolCallInfo

logger = logging.getLogger(__name__)


class LLMClient:
    """Unified LLM interface that delegates to a provider.

    Chat methods (``chat_raw``, ``chat_structured``, ``chat_with_tools``,
    ``chat_stream``) route through the selected ``LLMProvider``.

    Ollama-only methods (``generate_completion``, ``embed``) always use
    a local Ollama instance.  When the main provider is already Ollama,
    it is reused; otherwise a secondary ``OllamaProvider`` handles these.
    """

    def __init__(
        self,
        provider: LLMProvider | None = None,
        concurrency_semaphore: asyncio.Semaphore | None = None,
    ):
        if provider is None:
            from lean_ai.llm.client import OllamaProvider
            provider = OllamaProvider()
        self._provider = provider
        self._semaphore = concurrency_semaphore

        # For embed / generate_completion — always Ollama
        from lean_ai.llm.client import OllamaProvider
        if isinstance(provider, OllamaProvider):
            self._ollama: OllamaProvider | None = provider
        else:
            # Try to create a secondary Ollama provider for embed/inline
            try:
                self._ollama = OllamaProvider()
            except Exception:
                logger.warning("Could not create Ollama provider for embed/inline")
                self._ollama = None

        self.last_chat_metrics: dict | None = None
        self.last_stream_metrics: dict | None = None

    @property
    def model_name(self) -> str:
        return self._provider.model_name

    # ── Delegated chat methods ──

    async def chat_raw(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        if self._semaphore is not None:
            async with self._semaphore:
                text, metrics = await self._provider.chat_raw(
                    messages, temperature, max_tokens,
                )
        else:
            text, metrics = await self._provider.chat_raw(
                messages, temperature, max_tokens,
            )
        self.last_chat_metrics = _metrics_to_dict(metrics)
        return text

    async def chat_structured(
        self,
        messages: list[dict],
        schema: type[BaseModel],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> BaseModel:
        result, metrics = await self._provider.chat_structured(
            messages, schema, temperature, max_tokens,
        )
        self.last_chat_metrics = _metrics_to_dict(metrics)
        return result

    async def chat_stream(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        async for token in self._provider.chat_stream(messages, temperature, max_tokens):
            yield token

    async def check_health(self) -> bool:
        return await self._provider.check_health()

    # ── Ollama-only methods ──

    async def generate_completion(
        self, prompt: str, suffix: str = "", timeout: float = 5.0,
    ) -> str:
        if self._ollama is None:
            return ""
        return await self._ollama.generate_completion(prompt, suffix, timeout)

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        if self._ollama is None:
            return []
        return await self._ollama.embed(texts, model)

    # ── Multi-turn tool calling orchestration loop ──

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
        on_metrics: Callable | None = None,
        on_context_refresh: Callable | None = None,
    ) -> tuple[list[ToolCall], str]:
        """Multi-turn tool calling loop.

        Calls the provider's ``chat_with_tools_single`` in a loop.  When
        the response contains tool calls, executes each one via
        *tool_executor_fn*, appends results, and calls again.  Repeats
        until ``task_complete`` is called, too many consecutive text-only
        responses occur, or *max_turns* is reached.
        """
        from lean_ai.llm.client import _sanitize_messages

        max_text_only = 3
        tokens = max_tokens or self._provider.max_tokens
        executed: list[ToolCall] = []
        explanation_parts: list[str] = []
        consecutive_text_only: int = 0

        # Loop detection state
        ld_threshold = (
            loop_detection_threshold
            if loop_detection_threshold is not None
            else settings.loop_detection_threshold
        )
        prev_tool_hash: str | None = None
        consecutive_count: int = 0
        last_refresh_turn: int = -10

        effective_max = max_turns if max_turns > 0 else 2**31

        messages[:] = _sanitize_messages(messages)

        for turn in range(effective_max):
            logger.info(
                "chat_with_tools turn %d/%s: %d messages",
                turn + 1,
                max_turns if max_turns > 0 else "∞",
                len(messages),
            )

            content, tool_calls, metrics = await self._provider.chat_with_tools_single(
                messages, tools, max_tokens=tokens,
            )

            last_prompt_tokens = metrics.prompt_tokens

            if on_metrics and last_prompt_tokens:
                await on_metrics(last_prompt_tokens, self._provider.context_window)

            if content.strip():
                explanation_parts.append(content.strip())
                if on_content:
                    await on_content(content.strip())

            if not tool_calls:
                messages.append({"role": "assistant", "content": content})
                consecutive_text_only += 1
                if consecutive_text_only >= max_text_only:
                    logger.warning(
                        "chat_with_tools: %d consecutive text-only responses "
                        "without task_complete — exiting",
                        consecutive_text_only,
                    )
                    break
                continue

            # Reset text-only counter when tools are called
            consecutive_text_only = 0

            # Check for task_complete signal
            completion_call: ToolCallInfo | None = None
            for tc in tool_calls:
                if tc.name == "task_complete":
                    completion_call = tc
                    break

            # Build assistant message via provider (format differs per provider)
            assistant_msg = self._provider.format_assistant_tool_message(
                content, tool_calls,
            )
            messages.append(assistant_msg)

            # Execute each tool call
            for tc in tool_calls:
                if tc.name == "task_complete":
                    result_msgs = self._provider.format_tool_result_messages(
                        tc, "Task marked complete.",
                    )
                    messages.extend(result_msgs)
                    continue

                if on_tool_call:
                    await on_tool_call(tc.name, tc.arguments)

                try:
                    result_str = await tool_executor_fn(tc.name, tc.arguments)
                except Exception as exc:
                    result_str = f"ERROR: {exc}"
                    logger.warning("chat_with_tools: tool %s raised: %s", tc.name, exc)

                executed.append(ToolCall(
                    tool_name=tc.name,
                    parameters=tc.arguments,
                    description=(
                        f"{tc.name} "
                        f"{tc.arguments.get('path', tc.arguments.get('command', ''))}"
                    ),
                ))

                if on_tool_result:
                    await on_tool_result(tc.name, result_str)

                result_msgs = self._provider.format_tool_result_messages(tc, result_str)
                messages.extend(result_msgs)

                # Loop detection
                if ld_threshold > 0:
                    call_sig = f"{tc.name}:{json.dumps(tc.arguments, sort_keys=True)}"
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
                            tc.name, consecutive_count,
                        )
                        messages.append({
                            "role": "user",
                            "content": (
                                f"You have called {tc.name} with identical arguments "
                                f"{consecutive_count} times consecutively and it "
                                f"keeps failing. Try a different approach — read "
                                f"the file first, check the error, or use different "
                                f"arguments."
                            ),
                        })
                        consecutive_count = 0

            # Exit if task_complete was called
            if completion_call:
                summary = completion_call.arguments.get("summary", "")
                if summary:
                    explanation_parts.append(summary)
                logger.info("chat_with_tools: task_complete called — exiting loop")
                break

            # Context refresh
            if turn - last_refresh_turn >= 5:
                refreshed = await self._maybe_refresh_context(
                    messages,
                    threshold=settings.refresh_threshold,
                    prompt_tokens=last_prompt_tokens or None,
                    on_context_refresh=on_context_refresh,
                )
                if refreshed:
                    last_refresh_turn = turn
                    if on_metrics:
                        est_new = sum(
                            len(m.get("content") or "") for m in messages
                        ) // 4
                        await on_metrics(est_new, self._provider.context_window)

            # Periodic task reminder
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

    async def _maybe_refresh_context(
        self,
        messages: list[dict],
        threshold: float,
        prompt_tokens: int | None = None,
        on_context_refresh: Callable | None = None,
    ) -> bool:
        """Refresh conversation context when nearing context window limits."""
        from lean_ai.llm.client import _sanitize_messages

        if on_context_refresh is None:
            return False

        if prompt_tokens is not None:
            est_tokens = prompt_tokens
        else:
            est_tokens = sum(len(m.get("content") or "") for m in messages) // 4
        limit = int(threshold * self._provider.context_window)

        if est_tokens < limit:
            return False
        if len(messages) < 4:
            return False

        logger.info(
            "chat_with_tools: context refresh triggered at %d/%d tokens (%.0f%%)",
            est_tokens, self._provider.context_window,
            (est_tokens / self._provider.context_window) * 100,
        )

        try:
            new_messages = on_context_refresh(messages)
        except Exception:
            logger.warning(
                "chat_with_tools: context refresh callback failed, skipping",
                exc_info=True,
            )
            return False

        messages[:] = _sanitize_messages(new_messages)

        new_est = sum(len(m.get("content") or "") for m in messages) // 4
        logger.info(
            "chat_with_tools: context refreshed — %d→%d est. tokens, %d messages",
            est_tokens, new_est, len(messages),
        )
        return True


def _metrics_to_dict(metrics: LLMMetrics) -> dict:
    """Convert LLMMetrics to the legacy dict format for backward compat."""
    return {
        "tokens_per_second": metrics.tokens_per_second,
        "eval_count": metrics.completion_tokens,
        "prompt_tokens": metrics.prompt_tokens,
    }

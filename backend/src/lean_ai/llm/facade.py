"""LLMClient facade — delegates to an LLMProvider for chat, keeps Ollama for FIM/embed."""

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

from pydantic import BaseModel

from lean_ai.config import settings
from lean_ai.llm.base import LLMMetrics, LLMProvider, ToolCall, ToolCallInfo

if TYPE_CHECKING:
    from lean_ai.workflow.ws_dispatcher import WSMessageDispatcher

logger = logging.getLogger(__name__)


# ── Turn supervisor types ─────────────────────────────────────────


class TurnVerdict(Enum):
    """Single decision made after each turn in the tool-calling loop."""
    CONTINUE = auto()
    NUDGE = auto()
    REFRESH = auto()
    EXIT = auto()


@dataclass
class TurnAction:
    """Result of evaluating a turn — one verdict with optional payload."""
    verdict: TurnVerdict
    message: str = ""
    exit_reason: str = ""


@dataclass
class _TurnState:
    """Mutable counters tracked across turns in chat_with_tools."""
    consecutive_text_only: int = 0
    consecutive_truncated: int = 0
    prev_tool_hash: str | None = None
    consecutive_same_tool: int = 0
    max_text_only: int = 3
    max_truncated: int = 5
    loop_detection_threshold: int = 3
    recent_test_failures: int = 0


# ── Claim verification ────────────────────────────────────────────

# Tools whose results provide evidence for claims — suppress the
# verification nudge when any of these were called in the current turn.
_VERIFICATION_TOOLS = frozenset({
    "search_internet", "fetch_url",
    "search_wiki", "fetch_wiki_page",
    "grep_files", "read_file", "list_directory", "directory_tree",
})

# Patterns indicating the LLM is making an unverified external claim.
_CLAIM_PATTERNS = re.compile(
    r"(?:"
    # Existence / non-existence claims
    r"(?:does(?:n't| not)|doesn't) (?:exist|have|support|provide|include|offer|expose)"
    r"|(?:is(?:n't| not)|isn't) (?:available|supported|implemented|released|possible)"
    r"|(?:not yet (?:available|supported|released|implemented))"
    r"|(?:no longer (?:available|supported|maintained))"
    r"|(?:not (?:a )?(?:valid|real|actual|existing) "
    r"(?:function|method|class|module|package|library|API|endpoint|feature))"
    # Future / deprecation claims
    r"|(?:(?:will be|has been|was) (?:deprecated|removed|discontinued))"
    r"|(?:only (?:available|supported) in .{0,30}"
    r"(?:future|upcoming|next|later|beta|preview|unreleased))"
    r"|(?:(?:future|upcoming|planned|proposed) (?:version|release|feature|API))"
    # Training data caveat
    r"|(?:as of my (?:knowledge|training|last update|cutoff))"
    r"|(?:my (?:training|knowledge) (?:data |cutoff |)"
    r"(?:only (?:goes|extends)|doesn't (?:cover|include)))"
    r"|(?:I (?:don't|do not) have (?:information|data|knowledge) "
    r"(?:about|on|regarding))"
    # Assumption markers about external things
    r"|(?:I (?:assume|believe|think) (?:this|that|the) "
    r"(?:library|package|API|module|function|feature|version))"
    r")",
    re.IGNORECASE,
)

# If these appear near a match, the claim is about project files, not external.
_PROJECT_CONTEXT_PATTERNS = re.compile(
    r"(?:this (?:file|test|module|class|function)|"
    r"(?:creat|writ|add|implement)(?:e|ing) (?:this|the|a new) (?:file|test)|"
    r"we (?:need to|will|should|can) (?:create|add|implement|write))",
    re.IGNORECASE,
)


def _detect_unverified_claims(content: str) -> bool:
    """Return True if *content* contains unverified claims about external things."""
    if not content or len(content) < 20:
        return False
    for match in _CLAIM_PATTERNS.finditer(content):
        start = max(0, match.start() - 80)
        end = min(len(content), match.end() + 80)
        window = content[start:end]
        if _PROJECT_CONTEXT_PATTERNS.search(window):
            continue
        return True
    return False


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
        *,
        stream_callback=None,
        thinking_callback=None,
    ) -> str:
        kwargs = {}
        if stream_callback is not None:
            kwargs["stream_callback"] = stream_callback
        if thinking_callback is not None:
            kwargs["thinking_callback"] = thinking_callback

        if self._semaphore is not None:
            async with self._semaphore:
                text, metrics = await self._provider.chat_raw(
                    messages, temperature, max_tokens, **kwargs,
                )
        else:
            text, metrics = await self._provider.chat_raw(
                messages, temperature, max_tokens, **kwargs,
            )
        self.last_chat_metrics = _metrics_to_dict(metrics)
        return text

    async def chat_structured(
        self,
        messages: list[dict],
        schema: type[BaseModel],
        temperature: float | None = None,
        max_tokens: int | None = None,
        *,
        thinking_callback=None,
    ) -> BaseModel:
        kwargs = {}
        if thinking_callback is not None:
            kwargs["thinking_callback"] = thinking_callback

        result, metrics = await self._provider.chat_structured(
            messages, schema, temperature, max_tokens, **kwargs,
        )
        self.last_chat_metrics = _metrics_to_dict(metrics)
        return result

    async def chat_stream(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        *,
        thinking_callback=None,
    ) -> AsyncIterator[str]:
        async for token in self._provider.chat_stream(
            messages, temperature, max_tokens,
            thinking_callback=thinking_callback,
        ):
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

    async def compute_embedding_batch_size(
        self,
        chunks: list[tuple[str, str, str]],
    ) -> int:
        """Compute the optimal embedding batch size.

        If ``LEAN_AI_EMBEDDING_BATCH_SIZE`` is set to a positive value, that
        value is returned directly.  Otherwise, the embedding model's context
        window is queried from Ollama and the batch size is calculated to fill
        50% of it, based on the average chunk size in *chunks*.

        Args:
            chunks: list of ``(chunk_id, text, content_hash)`` tuples to embed.

        Returns:
            Batch size clamped to ``[16, 1024]``.
        """
        # Reduced from 1024 to 256 to cap worst-case batch duration.
        # Larger batches on big embedding models (e.g. qwen3-embedding:8b)
        # were taking long enough that the extension's health monitor
        # would time out mid-batch and restart the backend. Users on
        # fast hardware can still override via LEAN_AI_EMBEDDING_BATCH_SIZE.
        min_batch, max_batch, fallback = 16, 256, 128

        # Manual override.
        if settings.embedding_batch_size > 0:
            return max(min_batch, min(settings.embedding_batch_size, max_batch))

        if not chunks:
            return fallback

        # Average chars across all pending chunks → estimate tokens.
        avg_chars = sum(len(text) for _, text, _ in chunks) / len(chunks)
        tokens_per_chunk = max(avg_chars / 4.0, 1.0)  # Conservative: ~4 chars/token

        ctx_window: int | None = None
        if self._ollama is not None:
            ctx_window = await self._ollama.get_embedding_context_window()

        if ctx_window is None or ctx_window <= 0:
            logger.info(
                "Embedding context window unknown — using fallback batch size %d",
                fallback,
            )
            return fallback

        batch_size = int((ctx_window * 0.5) / tokens_per_chunk)
        batch_size = max(min_batch, min(batch_size, max_batch))

        logger.info(
            "Computed embedding batch size: %d "
            "(context_window=%d, avg_tokens=%.0f)",
            batch_size, ctx_window, tokens_per_chunk,
        )
        return batch_size

    async def check_embedding_model(self) -> tuple[bool, str]:
        """Check if the embedding model is available in Ollama.

        Returns ``(True, model_name)`` on success,
        ``(False, reason)`` on failure.

        First verifies the model exists via ``ollama show`` (instant,
        no loading).  Then sends a single test embed call which may
        trigger a cold model load into VRAM — this can take minutes
        for large models but Ollama handles it naturally.
        """
        if not settings.enable_embeddings:
            return False, "Embeddings disabled (LEAN_AI_ENABLE_EMBEDDINGS=false)"
        if self._ollama is None:
            return False, "No Ollama provider available for embeddings"
        embed_model = settings.embedding_model
        if not embed_model:
            return False, "No embedding model configured (LEAN_AI_EMBEDDING_MODEL)"

        # Quick existence check — show() returns model info without loading.
        try:
            await self._ollama._embed_client.show(model=embed_model)
        except Exception as exc:
            return False, (
                f"Embedding model '{embed_model}' not found in Ollama: {exc}"
            )

        # Warm-up call — triggers cold model load if needed.
        # Tag the call with ``ollama.warmup`` in the runtime_state
        # registry so ``/api/health`` reports what the backend is
        # waiting on. Large embedding models can take minutes to load
        # from cold disk; the tag lets extension-side tooling and
        # humans distinguish "alive but warming" from "dead".
        from lean_ai.runtime_state import busy

        try:
            logger.info("Warming up embedding model '%s'…", embed_model)
            with busy("ollama.warmup"):
                result = await self._ollama.embed(["test"], model=embed_model)
            if result:
                return True, embed_model
            return False, f"Embedding model '{embed_model}' returned empty result"
        except Exception as exc:
            return False, f"Embedding model '{embed_model}' not available: {exc}"

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
        text_only_nudge: str | None = None,
        text_only_exit_count: int = 3,
        stream_content: bool = False,
        on_tool_call: Callable | None = None,
        on_tool_result: Callable | None = None,
        on_content: Callable | None = None,
        on_thinking: Callable | None = None,
        on_metrics: Callable | None = None,
        on_context_refresh: Callable | None = None,
        dispatcher: "WSMessageDispatcher | None" = None,
    ) -> tuple[list[ToolCall], str]:
        """Multi-turn tool calling loop with unified turn supervisor.

        Calls the provider's ``chat_with_tools_single`` in a loop.  When
        the response contains tool calls, executes each one via
        *tool_executor_fn*, appends results, and calls again.  After each
        turn, ``_evaluate_turn`` makes a single decision: continue, nudge,
        refresh context, or exit.  Repeats until ``task_complete`` is
        called, the supervisor decides to exit, or *max_turns* is reached.

        When *text_only_exit_count* is 1 the loop exits immediately on
        the first text-only response (no nudge).  Default is 3.

        When *stream_content* is True and *on_content* / *on_thinking*
        callbacks are provided, content and thinking tokens are streamed
        to the callbacks as they arrive from the provider (token-level
        streaming).  The bulk ``on_content`` call after each turn is
        skipped to avoid double-delivery.
        """
        from lean_ai.llm.client import _sanitize_messages

        ld_threshold = (
            loop_detection_threshold
            if loop_detection_threshold is not None
            else settings.loop_detection_threshold
        )
        state = _TurnState(loop_detection_threshold=ld_threshold)
        state.max_text_only = text_only_exit_count
        tokens = max_tokens or self._provider.max_tokens
        executed: list[ToolCall] = []
        explanation_parts: list[str] = []
        last_prompt_tokens: int | None = None

        effective_max = max_turns if max_turns > 0 else 2**31

        messages[:] = _sanitize_messages(messages)

        for turn in range(effective_max):
            # ── Check for cancel / user interrupt ─────────────────
            if dispatcher:
                dispatcher.check_cancelled()
                pending = dispatcher.get_pending_message()
                if pending:
                    logger.info(
                        "chat_with_tools: injecting user interrupt (%d chars)",
                        len(pending),
                    )
                    messages.append({
                        "role": "user",
                        "content": (
                            "[USER INTERRUPT] The user has sent you new "
                            "instructions. Read carefully and adjust your "
                            "approach:\n\n" + pending
                        ),
                    })

            logger.info(
                "chat_with_tools turn %d/%s: %d messages",
                turn + 1,
                max_turns if max_turns > 0 else "∞",
                len(messages),
            )

            # Build streaming callbacks when stream_content is enabled
            _stream_cb = on_content if stream_content and on_content else None
            _think_cb = on_thinking if stream_content and on_thinking else None
            _streamed_content: list[str] = []

            async def _stream_wrapper(
                token: str,
                _buf: list[str] = _streamed_content,
            ) -> None:
                _buf.append(token)
                if on_content:
                    await on_content(token)

            content, tool_calls, metrics = await self._provider.chat_with_tools_single(
                messages, tools, max_tokens=tokens,
                stream_callback=_stream_wrapper if _stream_cb else None,
                thinking_callback=_think_cb,
            )

            last_prompt_tokens = metrics.prompt_tokens

            if on_metrics and last_prompt_tokens:
                await on_metrics(last_prompt_tokens, self._provider.context_window)

            # Deliver thinking in bulk when not streamed
            if not _think_cb and on_thinking and metrics.thinking:
                await on_thinking(metrics.thinking)

            if content.strip():
                explanation_parts.append(content.strip())
                # Deliver content in bulk when not streamed
                if not _stream_cb and on_content:
                    await on_content(content.strip())

            # ── Process turn results ──────────────────────────────
            completion_call: ToolCallInfo | None = None

            if not tool_calls:
                messages.append({"role": "assistant", "content": content})
            else:
                # Reset text-only/truncation counters on tool use
                state.consecutive_text_only = 0
                state.consecutive_truncated = 0

                # Check for task_complete signal
                for tc in tool_calls:
                    if tc.name == "task_complete":
                        completion_call = tc
                        break

                # Build assistant message via provider
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
                        logger.warning(
                            "chat_with_tools: tool %s raised: %s", tc.name, exc,
                        )

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

                    result_msgs = self._provider.format_tool_result_messages(
                        tc, result_str,
                    )
                    messages.extend(result_msgs)

                    # Track test/lint failure streak for claim verification
                    if tc.name in ("run_tests", "run_lint"):
                        if isinstance(result_str, str) and (
                            result_str.startswith("FAILED")
                        ):
                            state.recent_test_failures += 1
                        else:
                            state.recent_test_failures = 0

                    # Loop detection (inline — can coexist with reminders)
                    if state.loop_detection_threshold > 0:
                        call_sig = (
                            f"{tc.name}:"
                            f"{json.dumps(tc.arguments, sort_keys=True)}"
                        )
                        tool_hash = hashlib.sha256(
                            call_sig.encode(),
                        ).hexdigest()
                        if tool_hash == state.prev_tool_hash:
                            state.consecutive_same_tool += 1
                        else:
                            state.consecutive_same_tool = 1
                            state.prev_tool_hash = tool_hash

                        if (
                            state.consecutive_same_tool
                            >= state.loop_detection_threshold
                        ):
                            logger.warning(
                                "chat_with_tools: loop detected — %s "
                                "called %d times with identical arguments",
                                tc.name, state.consecutive_same_tool,
                            )
                            from lean_ai.llm.prompt_registry import registry
                            messages.append({
                                "role": "user",
                                "content": registry.format(
                                    "nudge.loop_detected",
                                    tool_name=tc.name,
                                    count=str(state.consecutive_same_tool),
                                ),
                            })
                            state.consecutive_same_tool = 0

            # Exit if task_complete was called
            if completion_call:
                summary = completion_call.arguments.get("summary", "")
                if summary:
                    explanation_parts.append(summary)
                logger.info("chat_with_tools: task_complete called — exiting loop")
                break

            # ── Evaluate turn — single decision point ─────────────
            action = self._evaluate_turn(
                turn=turn,
                has_tool_calls=bool(tool_calls),
                is_truncated=metrics.stop_reason in ("length", "max_tokens"),
                prompt_tokens=last_prompt_tokens,
                state=state,
                text_only_nudge=text_only_nudge,
                task_reminder=task_reminder,
                reminder_interval=reminder_interval,
                max_turns=effective_max,
                on_context_refresh=on_context_refresh,
            )

            if action.verdict == TurnVerdict.EXIT:
                logger.warning(
                    "chat_with_tools: exiting — %s", action.exit_reason,
                )
                break
            if action.verdict == TurnVerdict.REFRESH:
                refreshed = await self._maybe_refresh_context(
                    messages,
                    threshold=settings.refresh_threshold,
                    prompt_tokens=last_prompt_tokens or None,
                    on_context_refresh=on_context_refresh,
                )
                if refreshed and on_metrics:
                    est_new = sum(
                        len(m.get("content") or "") for m in messages
                    ) // 4
                    await on_metrics(est_new, self._provider.context_window)
            elif action.verdict == TurnVerdict.NUDGE:
                messages.append({"role": "user", "content": action.message})
                if not tool_calls:
                    continue  # Skip to next turn for text-only nudges

            # ── Claim verification nudge (loop-aware) ──
            # Only fire when the model is stuck: tests/lint have failed
            # repeatedly AND the model's response suggests stale API
            # knowledge.  This helps the model break out of fix loops
            # caused by deprecated or renamed APIs in its training data.
            if (
                settings.enable_claim_verification
                and state.recent_test_failures >= 2
                and content.strip()
                and _detect_unverified_claims(content)
                and not any(
                    tc.name in _VERIFICATION_TOOLS for tc in (tool_calls or [])
                )
            ):
                logger.info(
                    "chat_with_tools: repeated test failures + unverified "
                    "claim detected, nudging internet search"
                )
                from lean_ai.llm.prompt_registry import registry
                messages.append({
                    "role": "user",
                    "content": registry.get("nudge.claim_verification"),
                })
                # Reset so we don't nudge every turn once triggered
                state.recent_test_failures = 0

            # ── Confidence verification nudge ──
            # Detect [UNVERIFIED] markers in model output. Only nudge if
            # search_internet is available in the tool list and wasn't
            # already called this turn.
            _has_search = any(
                t["function"]["name"] == "search_internet" for t in tools
            )
            if (
                content.strip()
                and "[UNVERIFIED]" in content.upper()
                and _has_search
                and not any(
                    tc.name == "search_internet" for tc in (tool_calls or [])
                )
            ):
                logger.info(
                    "chat_with_tools: [UNVERIFIED] marker detected, "
                    "nudging confidence verification"
                )
                from lean_ai.llm.prompt_registry import registry
                messages.append({
                    "role": "user",
                    "content": registry.get("nudge.confidence_verification"),
                })
        else:
            logger.warning(
                "chat_with_tools: reached max_turns=%s without completion",
                max_turns if max_turns > 0 else "∞",
            )

        return executed, "\n".join(explanation_parts)

    def _evaluate_turn(
        self,
        *,
        turn: int,
        has_tool_calls: bool,
        is_truncated: bool,
        prompt_tokens: int | None,
        state: _TurnState,
        text_only_nudge: str | None,
        task_reminder: str | Callable[[], str] | None,
        reminder_interval: int,
        max_turns: int,
        on_context_refresh: Callable | None,
    ) -> TurnAction:
        """Make ONE decision after each turn.

        Priority: EXIT > REFRESH > NUDGE (reminder) > CONTINUE.

        Loop detection is handled inline during tool execution (not here)
        so it can coexist with reminders on the same turn.

        Mutates *state* counters as a side effect.
        """
        # ── EXIT conditions ───────────────────────────────────────
        if not has_tool_calls:
            if is_truncated:
                state.consecutive_truncated += 1
                logger.warning(
                    "chat_with_tools: response truncated (streak=%d)",
                    state.consecutive_truncated,
                )
                if state.consecutive_truncated >= state.max_truncated:
                    return TurnAction(
                        verdict=TurnVerdict.EXIT,
                        exit_reason=(
                            f"{state.consecutive_truncated} consecutive "
                            f"truncated responses"
                        ),
                    )
                from lean_ai.llm.prompt_registry import registry
                return TurnAction(
                    verdict=TurnVerdict.NUDGE,
                    message=registry.get("nudge.truncation"),
                )

            state.consecutive_truncated = 0
            state.consecutive_text_only += 1
            if state.consecutive_text_only >= state.max_text_only:
                return TurnAction(
                    verdict=TurnVerdict.EXIT,
                    exit_reason=(
                        f"{state.consecutive_text_only} consecutive "
                        f"text-only responses without task_complete"
                    ),
                )

            if text_only_nudge:
                nudge = text_only_nudge
            else:
                from lean_ai.llm.prompt_registry import registry
                nudge = registry.get("nudge.text_only")
            return TurnAction(verdict=TurnVerdict.NUDGE, message=nudge)

        # ── Context refresh (event-driven by token threshold) ─────
        if on_context_refresh and prompt_tokens is not None:
            limit = int(settings.refresh_threshold * self._provider.context_window)
            if prompt_tokens >= limit:
                return TurnAction(verdict=TurnVerdict.REFRESH)

        # ── Periodic task reminder ────────────────────────────────
        if (
            task_reminder
            and reminder_interval > 0
            and (turn + 1) % reminder_interval == 0
            and turn + 1 < max_turns
        ):
            reminder_text = (
                task_reminder() if callable(task_reminder) else task_reminder
            )
            logger.info(
                "chat_with_tools: injecting task reminder at turn %d (%d chars)",
                turn + 1, len(reminder_text),
            )
            return TurnAction(verdict=TurnVerdict.NUDGE, message=reminder_text)

        return TurnAction(verdict=TurnVerdict.CONTINUE)

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
        "stop_reason": metrics.stop_reason,
    }

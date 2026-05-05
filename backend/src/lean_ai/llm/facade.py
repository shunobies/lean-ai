"""LLMClient facade — delegates to an LLMProvider for chat, keeps Ollama for FIM/embed."""

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from enum import Enum, auto
from time import monotonic as _monotonic
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
    nudge_key: str = ""
    log_level: int = logging.INFO


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
    # Reasoning-effort interrupt tracking.  Fires only on providers that
    # set metrics.thinking_budget_exceeded (Ollama today).  After 2
    # consecutive budget-exceeded turns the loop exits cleanly with
    # whatever content has accumulated.
    consecutive_budget_interrupts: int = 0
    max_budget_interrupts: int = 2
    pre_refresh_nudge_sent: bool = False


# ── Claim verification ────────────────────────────────────────────

# Tools whose results provide evidence for claims — suppress the
# verification nudge when any of these were called in the current turn.
_VERIFICATION_TOOLS = frozenset(
    {
        "search_internet",
        "fetch_url",
        "search_wiki",
        "fetch_wiki_page",
        "grep_files",
        "read_file",
        "list_directory",
        "directory_tree",
    }
)

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


def _preview_log_text(text: str, limit: int = 160) -> str:
    """Return a compact single-line preview suitable for backend logs."""
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _log_harness_message(
    *,
    kind: str,
    key: str,
    content: str,
    level: int = logging.INFO,
    turn: int | None = None,
    **metadata,
) -> None:
    """Log a backend-injected control message before it reaches the LLM."""
    metadata_parts = []
    if turn is not None:
        metadata_parts.append(f"turn={turn + 1}")
    for meta_key, meta_value in metadata.items():
        if meta_value is None:
            continue
        metadata_parts.append(f"{meta_key}={meta_value}")
    suffix = f" ({', '.join(metadata_parts)})" if metadata_parts else ""
    logger.log(
        level,
        "LLM harness message injected: kind=%s key=%s chars=%d%s preview=%r",
        kind,
        key,
        len(content or ""),
        suffix,
        _preview_log_text(content or ""),
    )


def _build_user_interrupt_message(user_text: str) -> str:
    """Format a queued user interrupt for prompt injection."""
    return (
        "[USER INTERRUPT] The user has sent you new "
        "instructions. Read carefully and adjust your "
        "approach:\n\n" + user_text
    )


def _inject_user_interrupt_message(
    messages: list[dict],
    *,
    user_text: str,
    turn: int,
) -> None:
    """Append a normalized user-interrupt message and log it."""
    interrupt_message = _build_user_interrupt_message(user_text)
    _log_harness_message(
        kind="interrupt",
        key="user_interrupt",
        content=interrupt_message,
        level=logging.INFO,
        turn=turn,
    )
    messages.append(
        {
            "role": "user",
            "content": interrupt_message,
        }
    )


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

    @property
    def provider_name(self) -> str:
        """Kebab-case provider name used by media routing (image/audio)."""
        return self._provider.provider_name

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
                    messages,
                    temperature,
                    max_tokens,
                    **kwargs,
                )
        else:
            text, metrics = await self._provider.chat_raw(
                messages,
                temperature,
                max_tokens,
                **kwargs,
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
        on_metrics: Callable | None = None,
        on_metrics_reset: Callable | None = None,
    ) -> BaseModel:
        kwargs = {}
        if thinking_callback is not None:
            kwargs["thinking_callback"] = thinking_callback

        if on_metrics_reset:
            await on_metrics_reset()

        result, metrics = await self._provider.chat_structured(
            messages,
            schema,
            temperature,
            max_tokens,
            **kwargs,
        )
        self.last_chat_metrics = _metrics_to_dict(metrics)
        if on_metrics and metrics.prompt_tokens:
            await on_metrics(metrics.prompt_tokens, self._provider.context_window)
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
            messages,
            temperature,
            max_tokens,
            thinking_callback=thinking_callback,
        ):
            yield token

    async def check_health(self) -> bool:
        return await self._provider.check_health()

    # ── Ollama-only methods ──

    async def generate_completion(
        self,
        prompt: str,
        suffix: str = "",
        timeout: float = 5.0,
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
            "Computed embedding batch size: %d (context_window=%d, avg_tokens=%.0f)",
            batch_size,
            ctx_window,
            tokens_per_chunk,
        )
        return batch_size

    async def check_embedding_model(self) -> tuple[bool, str]:
        """Pure config check for the embedding pipeline.

        Returns ``(True, model_name)`` when embeddings are configured
        and an Ollama provider is available; ``(False, reason)``
        otherwise. Does NOT call Ollama — the prior ``ollama show``
        round-trip was pure overhead: it didn't load the model (only
        a real ``embed`` call does that), barely verified the model
        (just "registered in Ollama"), and added a hang/timeout risk
        that repeatedly wedged ``/init``. Any real failure (missing
        model, connection error, broken GGUF) surfaces naturally from
        the first ``embed`` call inside ``generate_embeddings``, where
        ``asyncio.gather(return_exceptions=True)`` catches it and
        ``embedding_status`` becomes ``failed`` with a real error.
        """
        if not settings.enable_embeddings:
            return False, "Embeddings disabled (LEAN_AI_ENABLE_EMBEDDINGS=false)"
        if self._ollama is None:
            return False, "No Ollama provider available for embeddings"
        embed_model = settings.embedding_model
        if not embed_model:
            return False, "No embedding model configured (LEAN_AI_EMBEDDING_MODEL)"
        return True, embed_model

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
        on_metrics_reset: Callable | None = None,
        on_context_refresh: Callable | None = None,
        on_budget_interrupt: Callable | None = None,
        dispatcher: "WSMessageDispatcher | None" = None,
        telemetry_context: dict | None = None,
        task_complete_validator: Callable | None = None,
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

        # Telemetry is opt-in: callers pass a dict with repo_root +
        # session_id to enable per-turn trace capture and in-loop events
        # (loop_detected, context_refresh, reminder_injected,
        # claim_unverified). If absent, chat_with_tools behaves exactly
        # as before — no capture, no events.
        telemetry_context = telemetry_context if telemetry_context else None
        if telemetry_context is not None:
            # Initialise mutable output keys so callers can read them
            # after return without KeyError.
            telemetry_context.setdefault("trace_uuids", [])
            telemetry_context.setdefault("last_trace_uuid", None)

        messages[:] = _sanitize_messages(messages)

        if on_metrics_reset:
            await on_metrics_reset()

        for turn in range(effective_max):
            # ── Check for cancel / user interrupt ─────────────────
            if dispatcher:
                dispatcher.check_cancelled()
                pending = dispatcher.get_pending_message()
                if pending:
                    _inject_user_interrupt_message(
                        messages,
                        user_text=pending,
                        turn=turn,
                    )

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

            # Snapshot the input messages before the provider call so the
            # per-turn capture records exactly what was sent, independent
            # of the tool-result appends that follow.
            turn_messages_in: list[dict] | None = None
            turn_started_at: float | None = None
            if telemetry_context is not None:
                turn_messages_in = [dict(m) for m in messages]
                turn_started_at = _monotonic()

            content, tool_calls, metrics = await self._provider.chat_with_tools_single(
                messages,
                tools,
                max_tokens=tokens,
                stream_callback=_stream_wrapper if _stream_cb else None,
                thinking_callback=_think_cb,
            )

            # Fire per-turn training capture (fire-and-forget). Must
            # happen BEFORE any mutation of messages (tool results)
            # because the capture payload is the pre-turn prompt +
            # model output.
            if telemetry_context is not None and turn_messages_in is not None:
                latency_ms = (
                    int((_monotonic() - turn_started_at) * 1000)
                    if turn_started_at is not None
                    else None
                )
                _fire_capture_turn(
                    telemetry_context=telemetry_context,
                    turn_index=turn,
                    messages=turn_messages_in,
                    content=content,
                    thinking=metrics.thinking,
                    tool_calls=tool_calls,
                    model_name=self._provider.model_name,
                    provider=self._provider.provider_name,
                    metrics=metrics,
                    latency_ms=latency_ms,
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

            # Preserve chain-of-thought across turns when the provider has it
            # enabled.  Two delivery strategies:
            #
            #   * Ollama — fold into ``content`` as ``<think>...</think>\n\n...``.
            #     Ollama's compiled ``RENDERER qwen3.5`` has no
            #     ``preserve_thinking`` knob; folding makes the thinking visible
            #     to the renderer via normal content regardless of renderer
            #     behaviour.  Works with every Ollama version.
            #   * Everything else (Serve/vLLM, OpenAI, Anthropic, Gemini) —
            #     attach as a separate ``thinking`` field.  Serve/vLLM's
            #     Jinja chat template reads it via
            #     ``chat_template_kwargs={"preserve_thinking": True}`` (set by
            #     ``OpenAIProvider._extra_body``).  Others silently ignore.
            preserve_thinking = getattr(self._provider, "_preserve_thinking", False)
            turn_thinking = metrics.thinking if preserve_thinking else None
            fold_into_content = (
                bool(turn_thinking) and getattr(self._provider, "provider_name", "") == "ollama"
            )

            if not tool_calls:
                if fold_into_content:
                    assistant_msg: dict = {
                        "role": "assistant",
                        "content": f"<think>\n{turn_thinking}\n</think>\n\n{content}",
                    }
                else:
                    assistant_msg = {"role": "assistant", "content": content}
                    if turn_thinking:
                        assistant_msg["thinking"] = turn_thinking
                messages.append(assistant_msg)
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
                    content,
                    tool_calls,
                )
                if fold_into_content:
                    existing = assistant_msg.get("content") or ""
                    assistant_msg["content"] = f"<think>\n{turn_thinking}\n</think>\n\n{existing}"
                elif turn_thinking:
                    assistant_msg["thinking"] = turn_thinking
                messages.append(assistant_msg)

                # Execute each tool call.  task_complete is deferred until
                # AFTER every other tool in this turn has run so that a
                # task_complete_validator (if provided) sees the side
                # effects of same-turn observation/scratchpad writes.
                pending_complete_calls: list[ToolCallInfo] = []
                interrupt_injected = False
                for idx, tc in enumerate(tool_calls):
                    if tc.name == "task_complete":
                        pending_complete_calls.append(tc)
                        continue

                    if on_tool_call:
                        await on_tool_call(tc.name, tc.arguments)

                    try:
                        result_str = await tool_executor_fn(tc.name, tc.arguments)
                    except Exception as exc:
                        result_str = f"ERROR: {exc}"
                        logger.warning(
                            "chat_with_tools: tool %s raised: %s",
                            tc.name,
                            exc,
                        )

                    executed.append(
                        ToolCall(
                            tool_name=tc.name,
                            parameters=tc.arguments,
                            description=(
                                f"{tc.name} "
                                f"{tc.arguments.get('path', tc.arguments.get('command', ''))}"
                            ),
                        )
                    )

                    if on_tool_result:
                        await on_tool_result(tc.name, result_str)

                    result_msgs = self._provider.format_tool_result_messages(
                        tc,
                        result_str,
                    )
                    messages.extend(result_msgs)

                    if dispatcher:
                        dispatcher.check_cancelled()
                        pending = dispatcher.get_pending_message()
                        if pending:
                            _inject_user_interrupt_message(
                                messages,
                                user_text=pending,
                                turn=turn,
                            )
                            messages[:] = _sanitize_messages(messages)
                            logger.info(
                                "chat_with_tools: user interrupt received mid-turn; "
                                "deferring %d remaining tool call(s)",
                                max(0, len(tool_calls) - idx - 1) + len(pending_complete_calls),
                            )
                            completion_call = None
                            pending_complete_calls = []
                            interrupt_injected = True
                            break

                    # Track test/lint failure streak for claim verification
                    if tc.name in ("run_tests", "run_lint"):
                        if isinstance(result_str, str) and (result_str.startswith("FAILED")):
                            state.recent_test_failures += 1
                        else:
                            state.recent_test_failures = 0

                    # Loop detection (inline — can coexist with reminders)
                    if state.loop_detection_threshold > 0:
                        call_sig = f"{tc.name}:{json.dumps(tc.arguments, sort_keys=True)}"
                        tool_hash = hashlib.sha256(
                            call_sig.encode(),
                        ).hexdigest()
                        if tool_hash == state.prev_tool_hash:
                            state.consecutive_same_tool += 1
                        else:
                            state.consecutive_same_tool = 1
                            state.prev_tool_hash = tool_hash

                        if state.consecutive_same_tool >= state.loop_detection_threshold:
                            logger.warning(
                                "chat_with_tools: loop detected — %s "
                                "called %d times with identical arguments",
                                tc.name,
                                state.consecutive_same_tool,
                            )
                            from lean_ai.llm.prompt_registry import registry

                            loop_nudge = registry.format(
                                "nudge.loop_detected",
                                tool_name=tc.name,
                                count=str(state.consecutive_same_tool),
                            )
                            _log_harness_message(
                                kind="nudge",
                                key="nudge.loop_detected",
                                content=loop_nudge,
                                level=logging.WARNING,
                                turn=turn,
                                tool_name=tc.name,
                                repeat_count=state.consecutive_same_tool,
                            )
                            messages.append(
                                {
                                    "role": "user",
                                    "content": loop_nudge,
                                }
                            )
                            _fire_in_loop_event(
                                telemetry_context,
                                event_type="loop_detected",
                                payload={
                                    "tool_name": tc.name,
                                    "count": state.consecutive_same_tool,
                                    "turn_index": turn,
                                },
                            )
                            state.consecutive_same_tool = 0

                if not interrupt_injected and pending_complete_calls and dispatcher:
                    dispatcher.check_cancelled()
                    pending = dispatcher.get_pending_message()
                    if pending:
                        _inject_user_interrupt_message(
                            messages,
                            user_text=pending,
                            turn=turn,
                        )
                        messages[:] = _sanitize_messages(messages)
                        logger.info(
                            "chat_with_tools: user interrupt received before deferred "
                            "task completion; deferring completion to the next turn"
                        )
                        completion_call = None
                        pending_complete_calls = []
                        interrupt_injected = True

                if interrupt_injected:
                    continue

                # Emit deferred task_complete results AFTER every other
                # tool in this turn has run.  When a
                # task_complete_validator is supplied and returns a
                # non-empty rejection string, we feed that back to the
                # model as the task_complete tool result and clear
                # ``completion_call`` so the loop continues instead of
                # exiting.  This lets callers (e.g. Phase 2) gate
                # completion on side effects like recorded observations.
                if pending_complete_calls:
                    rejection: str | None = None
                    if task_complete_validator is not None:
                        try:
                            rejection_maybe = task_complete_validator()
                            if asyncio.iscoroutine(rejection_maybe):
                                rejection_maybe = await rejection_maybe
                            if rejection_maybe:
                                rejection = str(rejection_maybe)
                        except Exception as exc:
                            logger.warning(
                                "chat_with_tools: task_complete_validator raised: %s",
                                exc,
                            )
                    if rejection:
                        completion_call = None
                        result_text = rejection
                        logger.warning(
                            "LLM tool result overridden: key=task_complete_validator_rejection "
                            "turn=%d preview=%r",
                            turn + 1,
                            _preview_log_text(result_text),
                        )
                    else:
                        result_text = "Task marked complete."
                    for tc in pending_complete_calls:
                        result_msgs = self._provider.format_tool_result_messages(
                            tc,
                            result_text,
                        )
                        messages.extend(result_msgs)

            # Bound thinking history so long tool loops don't blow the
            # context window — the 3 most recent assistant turns keep their
            # thinking (covers the model's back-reference to its own
            # reasoning), older turns drop it.
            if preserve_thinking:
                _trim_old_thinking(messages, keep_recent=3)

            # Exit if task_complete was called
            if completion_call:
                summary = completion_call.arguments.get("summary", "")
                if summary:
                    explanation_parts.append(summary)
                logger.info("chat_with_tools: task_complete called — exiting loop")
                break

            # ── Reasoning-budget interrupt ────────────────────────
            # When the provider's streaming helper aborted because
            # thinking tokens exceeded the configured soft limit (or the
            # universal safety rail), inject a user nudge asking the
            # model to commit.  The partial assistant message is already
            # appended; the next turn sees the nudge + its own prior
            # (truncated) reasoning.
            if getattr(metrics, "thinking_budget_exceeded", False):
                state.consecutive_budget_interrupts += 1
                if state.consecutive_budget_interrupts > state.max_budget_interrupts:
                    logger.warning(
                        "chat_with_tools: exiting — %d consecutive reasoning-"
                        "budget interrupts exceeded cap (%d)",
                        state.consecutive_budget_interrupts,
                        state.max_budget_interrupts,
                    )
                    break
                from lean_ai.llm.prompt_registry import registry

                budget_nudge = registry.get("nudge.reasoning_budget_exceeded")
                _log_harness_message(
                    kind="nudge",
                    key="nudge.reasoning_budget_exceeded",
                    content=budget_nudge,
                    level=logging.WARNING,
                    turn=turn,
                    interrupt_count=state.consecutive_budget_interrupts,
                    thinking_tokens=getattr(metrics, "thinking_token_count", 0),
                )
                messages.append(
                    {
                        "role": "user",
                        "content": budget_nudge,
                    }
                )
                if on_budget_interrupt:
                    await on_budget_interrupt(
                        getattr(metrics, "thinking_token_count", 0),
                    )
                continue  # skip _evaluate_turn; start next iteration
            state.consecutive_budget_interrupts = 0

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
                    "chat_with_tools: exiting — %s",
                    action.exit_reason,
                )
                break
            if action.verdict == TurnVerdict.REFRESH:
                refreshed = await self._maybe_refresh_context(
                    messages,
                    threshold=settings.refresh_threshold,
                    prompt_tokens=last_prompt_tokens or None,
                    on_context_refresh=on_context_refresh,
                )
                if refreshed:
                    _fire_in_loop_event(
                        telemetry_context,
                        event_type="context_refresh",
                        payload={
                            "turn_index": turn,
                            "prompt_tokens_before": last_prompt_tokens,
                            "context_window": self._provider.context_window,
                        },
                    )
                    if on_metrics_reset:
                        await on_metrics_reset()
                    if on_metrics:
                        est_new = sum(len(m.get("content") or "") for m in messages) // 4
                        await on_metrics(est_new, self._provider.context_window)
            elif action.verdict == TurnVerdict.NUDGE:
                _log_harness_message(
                    kind="nudge",
                    key=action.nudge_key or "nudge.unspecified",
                    content=action.message,
                    level=action.log_level,
                    turn=turn,
                )
                messages.append({"role": "user", "content": action.message})
                # Classify the nudge — reminder is the only kind that
                # fires on a turn that had tool calls; the text-only
                # and truncation nudges have their own continue path.
                is_reminder = (
                    bool(task_reminder)
                    and reminder_interval > 0
                    and (turn + 1) % reminder_interval == 0
                )
                if is_reminder:
                    _fire_in_loop_event(
                        telemetry_context,
                        event_type="reminder_injected",
                        payload={
                            "turn_index": turn,
                            "reminder_chars": len(action.message),
                        },
                    )
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
                and not any(tc.name in _VERIFICATION_TOOLS for tc in (tool_calls or []))
            ):
                logger.info(
                    "chat_with_tools: repeated test failures + unverified "
                    "claim detected, nudging internet search"
                )
                from lean_ai.llm.prompt_registry import registry

                claim_nudge = registry.get("nudge.claim_verification")
                _log_harness_message(
                    kind="nudge",
                    key="nudge.claim_verification",
                    content=claim_nudge,
                    level=logging.WARNING,
                    turn=turn,
                    recent_test_failures=state.recent_test_failures,
                )
                messages.append(
                    {
                        "role": "user",
                        "content": claim_nudge,
                    }
                )
                _fire_in_loop_event(
                    telemetry_context,
                    event_type="claim_unverified",
                    payload={
                        "turn_index": turn,
                        "recent_test_failures": state.recent_test_failures,
                    },
                )
                # Reset so we don't nudge every turn once triggered
                state.recent_test_failures = 0

            # ── Confidence verification nudge ──
            # Detect [UNVERIFIED] markers in model output. Only nudge if
            # search_internet is available in the tool list and wasn't
            # already called this turn.
            _has_search = any(t["function"]["name"] == "search_internet" for t in tools)
            if (
                content.strip()
                and "[UNVERIFIED]" in content.upper()
                and _has_search
                and not any(tc.name == "search_internet" for tc in (tool_calls or []))
            ):
                logger.info(
                    "chat_with_tools: [UNVERIFIED] marker detected, nudging confidence verification"
                )
                from lean_ai.llm.prompt_registry import registry

                confidence_nudge = registry.get("nudge.confidence_verification")
                _log_harness_message(
                    kind="nudge",
                    key="nudge.confidence_verification",
                    content=confidence_nudge,
                    level=logging.INFO,
                    turn=turn,
                )
                messages.append(
                    {
                        "role": "user",
                        "content": confidence_nudge,
                    }
                )
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
                            f"{state.consecutive_truncated} consecutive truncated responses"
                        ),
                    )
                from lean_ai.llm.prompt_registry import registry

                return TurnAction(
                    verdict=TurnVerdict.NUDGE,
                    message=registry.get("nudge.truncation"),
                    nudge_key="nudge.truncation",
                    log_level=logging.WARNING,
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
                nudge_key = "nudge.text_only.custom"
            else:
                from lean_ai.llm.prompt_registry import registry

                nudge = registry.get("nudge.text_only")
                nudge_key = "nudge.text_only"
            return TurnAction(
                verdict=TurnVerdict.NUDGE,
                message=nudge,
                nudge_key=nudge_key,
                log_level=logging.INFO,
            )

        # ── Context refresh (event-driven by token threshold) ─────
        if on_context_refresh and prompt_tokens is not None:
            limit = int(settings.refresh_threshold * self._provider.context_window)
            # Pre-refresh warning should be tied to the existing configurable
            # refresh threshold (default 0.7), not a separate hardcoded ratio.
            # Fire shortly before refresh so the model can checkpoint journal
            # and scratchpad state before callback-based context rebuild.
            pre_refresh_buffer = max(256, int(self._provider.context_window * 0.03))
            pre_refresh_limit = max(1, limit - pre_refresh_buffer)
            if (
                prompt_tokens >= pre_refresh_limit
                and prompt_tokens < limit
                and not state.pre_refresh_nudge_sent
            ):
                state.pre_refresh_nudge_sent = True
                return TurnAction(
                    verdict=TurnVerdict.NUDGE,
                    message=(
                        "CONTEXT WARNING: You are nearing context refresh. "
                        "Before continuing, summarize progress and key findings with "
                        "add_journal_entry, then write the single best next action to "
                        "update_scratchpad so it can be resumed after refresh."
                    ),
                    nudge_key="nudge.pre_context_refresh",
                    log_level=logging.WARNING,
                )
            if prompt_tokens >= limit:
                state.pre_refresh_nudge_sent = False
                return TurnAction(verdict=TurnVerdict.REFRESH)
            if prompt_tokens < pre_refresh_limit:
                state.pre_refresh_nudge_sent = False

        # ── Periodic task reminder ────────────────────────────────
        if (
            task_reminder
            and reminder_interval > 0
            and (turn + 1) % reminder_interval == 0
            and turn + 1 < max_turns
        ):
            reminder_text = task_reminder() if callable(task_reminder) else task_reminder
            return TurnAction(
                verdict=TurnVerdict.NUDGE,
                message=reminder_text,
                nudge_key="nudge.task_reminder",
                log_level=logging.INFO,
            )

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
            est_tokens,
            self._provider.context_window,
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
            est_tokens,
            new_est,
            len(messages),
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


_FOLDED_THINK_RE = re.compile(r"^<think>\n.*?\n</think>\n\n", re.DOTALL)


def _trim_old_thinking(messages: list[dict], *, keep_recent: int = 3) -> None:
    """Strip thinking from all but the ``keep_recent`` most recent assistant
    messages.  Handles both delivery strategies:

    * Separate ``thinking`` field on the message dict (Serve / OpenAI / others).
    * ``<think>...</think>\\n\\n`` folded into the start of ``content`` (Ollama).

    Operates in place.  Used when ``preserve_thinking`` is on to bound the
    context-window cost of chain-of-thought retention in long tool loops —
    recent thinking stays visible to the model (useful for self-coherence),
    older thinking is dropped.
    """

    def _has_thinking(msg: dict) -> bool:
        if msg.get("role") != "assistant":
            return False
        if "thinking" in msg:
            return True
        content = msg.get("content")
        return isinstance(content, str) and bool(_FOLDED_THINK_RE.match(content))

    def _strip(msg: dict) -> None:
        msg.pop("thinking", None)
        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = _FOLDED_THINK_RE.sub("", content, count=1)

    if keep_recent <= 0:
        for msg in messages:
            _strip(msg) if msg.get("role") == "assistant" else None
        return

    assistant_indices = [i for i, m in enumerate(messages) if _has_thinking(m)]
    for idx in assistant_indices[:-keep_recent]:
        _strip(messages[idx])


# ── Training capture helpers ─────────────────────────────────────
# Kept at module scope so chat_with_tools stays readable. All calls
# are fire-and-forget: on failure we log at debug level and continue.


def _serialize_tool_call_for_capture(tc: ToolCallInfo) -> dict:
    return {"name": tc.name, "arguments": tc.arguments}


def _build_assistant_output(
    content: str,
    thinking: str | None,
    tool_calls: list[ToolCallInfo] | None,
) -> dict:
    out: dict = {"content": content or ""}
    if thinking and getattr(settings, "capture_thinking", True):
        out["thinking"] = thinking
    if tool_calls:
        out["tool_calls"] = [_serialize_tool_call_for_capture(tc) for tc in tool_calls]
    return out


def _fire_capture_turn(
    *,
    telemetry_context: dict,
    turn_index: int,
    messages: list[dict],
    content: str,
    thinking: str | None,
    tool_calls: list[ToolCallInfo] | None,
    model_name: str,
    provider: str,
    metrics: LLMMetrics,
    latency_ms: int | None,
) -> None:
    """Schedule a training_traces write for the turn that just completed.

    The resulting trace_uuid is appended to
    ``telemetry_context['trace_uuids']`` and mirrored on
    ``telemetry_context['last_trace_uuid']`` so downstream hooks (plan
    decisions, validation attempts, workflow events) can reference the
    exact turn they followed.
    """
    repo_root = telemetry_context.get("repo_root")
    session_id = telemetry_context.get("session_id")
    phase = telemetry_context.get("phase") or "unknown"
    role = telemetry_context.get("role")
    if not repo_root or not session_id:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    assistant_output = _build_assistant_output(content, thinking, tool_calls)

    async def _run() -> None:
        try:
            from lean_ai.training.capture import capture_turn as _capture_turn

            trace_uuid = await _capture_turn(
                repo_root,
                session_id=session_id,
                phase=phase,
                role=role,
                turn_index=turn_index,
                model_name=model_name,
                provider=provider,
                messages=messages,
                assistant_output=assistant_output,
                outcome="success",
                tokens_prompt=metrics.prompt_tokens,
                tokens_completion=metrics.completion_tokens,
                latency_ms=latency_ms,
            )
            if trace_uuid:
                telemetry_context["last_trace_uuid"] = trace_uuid
                telemetry_context.setdefault("trace_uuids", []).append(trace_uuid)
        except Exception:
            logger.debug("capture_turn scheduling failed", exc_info=True)

    t = loop.create_task(_run())
    t.add_done_callback(_log_task_exc)


def _fire_in_loop_event(
    telemetry_context: dict | None,
    *,
    event_type: str,
    payload: dict,
) -> None:
    """Schedule a workflow_events write for an in-loop guardrail firing.

    Covers loop_detected, context_refresh, reminder_injected, and
    claim_unverified — the four events that were scaffolded but left
    unwired. Uses the fire_workflow_event helper so capture runs on the
    running loop without blocking chat_with_tools.
    """
    if telemetry_context is None:
        return
    repo_root = telemetry_context.get("repo_root")
    session_id = telemetry_context.get("session_id")
    if not repo_root or not session_id:
        return
    try:
        from lean_ai.workflow.hooks import fire_workflow_event

        # Tag the event with the current phase/role so consumers can
        # slice recovery behavior by where in the pipeline it fired.
        enriched = dict(payload)
        enriched["phase"] = telemetry_context.get("phase")
        enriched["role"] = telemetry_context.get("role")
        fire_workflow_event(
            repo_root=repo_root,
            session_id=session_id,
            event_type=event_type,
            payload=enriched,
            trace_uuid=telemetry_context.get("last_trace_uuid"),
        )
    except Exception:
        logger.debug("fire_in_loop_event failed", exc_info=True)


def _log_task_exc(task: asyncio.Task) -> None:
    try:
        exc = task.exception()
    except (asyncio.CancelledError, asyncio.InvalidStateError):
        return
    if exc is not None:
        logger.debug("background capture task raised", exc_info=exc)

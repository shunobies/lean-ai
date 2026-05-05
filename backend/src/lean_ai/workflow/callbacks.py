"""Shared workflow callback factories.

All workflow modules (pipeline, fix_mode, validation, tdd) need the same
set of progress-reporting callbacks for ``chat_with_tools``.  This module
provides a single factory so the logic lives in one place.
"""

import asyncio
import json
import logging
from collections.abc import Callable
from typing import NamedTuple

from fastapi import WebSocket

from lean_ai.tools.descriptions import humanize_tool_call
from lean_ai.workflow.ws_messages import (
    fire_assistant_content,
    fire_metrics_reset,
    fire_metrics_update,
    fire_thinking_content,
    fire_tool_progress,
)

logger = logging.getLogger(__name__)


def log_task_exception(task: asyncio.Task) -> None:
    """Callback for fire-and-forget tasks — log unhandled exceptions."""
    if not task.cancelled() and task.exception():
        logger.warning("Background task failed: %s", task.exception())


class WorkflowCallbacks(NamedTuple):
    """Callback set returned by :func:`build_workflow_callbacks`."""

    on_tool_call: Callable
    on_tool_result: Callable
    on_content: Callable
    on_thinking: Callable | None
    on_metrics: Callable | None
    on_metrics_reset: Callable | None
    on_budget_interrupt: Callable


def build_workflow_callbacks(
    ws: WebSocket,
    *,
    conversation_logger: Callable | None = None,
    include_thinking: bool = True,
    include_metrics: bool = True,
    description_prefix: str = "",
    content_prefix: str = "",
    streaming: bool = False,
) -> WorkflowCallbacks:
    """Build the standard set of WebSocket progress callbacks.

    Parameters
    ----------
    ws:
        WebSocket for sending progress messages.
    conversation_logger:
        Optional async callable for persisting conversation events.
    include_thinking:
        When *False*, ``on_thinking`` is returned as *None*.
    include_metrics:
        When *False*, ``on_metrics`` is returned as *None*.
    description_prefix:
        Prefix prepended to tool call descriptions (e.g. ``"[TDD dispute] "``).
    content_prefix:
        Prefix prepended to assistant content (e.g. ``"[TDD dispute] "``).
    streaming:
        When *True*, assistant/thinking content payloads include
        ``"streaming": True`` (used for planning-phase token streaming).
    """

    # Track last description per tool so on_tool_result can forward it.
    _last_tool_desc: dict[str, str] = {}

    async def on_tool_call(name: str, args: dict) -> None:
        desc = humanize_tool_call(name, args)
        full_desc = f"{description_prefix}{desc}"
        _last_tool_desc[name] = full_desc
        fire_tool_progress(
            ws,
            tool=name,
            status="running",
            description=full_desc,
        )
        if conversation_logger:
            t = asyncio.create_task(
                conversation_logger(
                    "tool_call",
                    desc,
                    tool_name=name,
                    tool_args=json.dumps(args),
                )
            )
            t.add_done_callback(log_task_exception)

    async def on_tool_result(name: str, result: str) -> None:
        is_error = result.startswith("ERROR:")
        fire_tool_progress(
            ws,
            tool=name,
            status="error" if is_error else "complete",
            description=_last_tool_desc.pop(name, name),
            output=result[:500] if is_error else None,
        )
        if conversation_logger:
            t = asyncio.create_task(
                conversation_logger(
                    "tool_result",
                    result[:2000],
                    tool_name=name,
                )
            )
            t.add_done_callback(log_task_exception)

    async def on_content(text: str) -> None:
        fire_assistant_content(
            ws,
            content=f"{content_prefix}{text}",
            streaming=streaming,
        )
        if conversation_logger:
            t = asyncio.create_task(conversation_logger("assistant", text))
            t.add_done_callback(log_task_exception)

    on_thinking: Callable | None = None
    if include_thinking:

        async def _on_thinking(text: str) -> None:
            fire_thinking_content(ws, content=text, streaming=streaming)

        on_thinking = _on_thinking

    on_metrics: Callable | None = None
    on_metrics_reset: Callable | None = None
    if include_metrics:

        async def _on_metrics(prompt_tokens: int, context_window: int) -> None:
            context_percent = round((prompt_tokens / context_window) * 100) if context_window else 0
            fire_metrics_update(
                ws,
                prompt_tokens=prompt_tokens,
                context_window=context_window,
                context_percent=context_percent,
            )

        on_metrics = _on_metrics

        async def _on_metrics_reset() -> None:
            fire_metrics_reset(ws)

        on_metrics_reset = _on_metrics_reset

    async def on_budget_interrupt(thinking_token_count: int) -> None:
        """Emit a ``thinking_content`` marker so the extension renders a
        ⚠ Reasoning budget exceeded chip in the streaming panel.  Fired
        by :func:`lean_ai.llm.facade.chat_with_tools` when the provider
        aborted thinking mid-stream."""
        fire_thinking_content(ws, content="", truncated=True)
        logger.info(
            "Reasoning budget interrupt — notifying extension (thinking ~%d tokens)",
            thinking_token_count,
        )

    return WorkflowCallbacks(
        on_tool_call=on_tool_call,
        on_tool_result=on_tool_result,
        on_content=on_content,
        on_thinking=on_thinking,
        on_metrics=on_metrics,
        on_metrics_reset=on_metrics_reset,
        on_budget_interrupt=on_budget_interrupt,
    )

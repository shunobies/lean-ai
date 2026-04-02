"""Fix and request mode workflows — no planning, direct tool execution.

Extracted from pipeline.py for separation of concerns.
"""

import asyncio
import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import WebSocket

from lean_ai.config import settings
from lean_ai.llm.tool_definitions import build_implementation_tools, build_investigation_tools
from lean_ai.routers.context_helpers import load_condensed_context
from lean_ai.tools import scratchpad
from lean_ai.workflow.prompts import (
    build_fix_investigation_prompt,
    build_fix_system_prompt,
    build_request_system_prompt,
)
from lean_ai.workflow.tool_executor import make_tool_executor
from lean_ai.workflow.validation import (
    _effective_post_commands,
    _run_post_validation,
    _run_validation_fix_loop,
)
from lean_ai.workflow.ws_dispatcher import WSMessageDispatcher
from lean_ai.workflow.ws_handler import ws_send, ws_send_nowait

if TYPE_CHECKING:
    from lean_ai.llm.facade import LLMClient

logger = logging.getLogger(__name__)


def _log_task_exception(task: asyncio.Task) -> None:
    """Callback for fire-and-forget tasks — log unhandled exceptions."""
    if not task.cancelled() and task.exception():
        logger.warning("Background task failed: %s", task.exception())


async def _run_fix(
    task: str,
    repo_root: str,
    ws: WebSocket,
    llm_client: "LLMClient",
    context: str,
    branch_name: str,
    base_branch: str = "",
    conversation_logger: Callable | None = None,
    session_id: str = "",
    expert_llm_client: "LLMClient | None" = None,
    request_llm_client: "LLMClient | None" = None,
    mode: str = "fix",
    dispatcher: WSMessageDispatcher | None = None,
) -> str:
    """Execute directly — no planning, no approval.

    The LLM gets the full tool set and runs until it decides it's done.
    When *mode* is ``"request"``, uses a neutral prompt and optionally
    a separate request model.
    """
    if dispatcher:
        dispatcher.enter_execution_mode()

    is_request = mode == "request"
    # Use dedicated model when available: request model for /request,
    # expert model for /fix (bug diagnosis is reasoning-heavy).
    if is_request:
        active_client = request_llm_client or llm_client
    else:
        active_client = expert_llm_client or llm_client
    tool_executor = make_tool_executor(
        repo_root, ws, session_id, llm_client=active_client,
        dispatcher=dispatcher,
    )
    commands = _effective_post_commands(repo_root)
    execution_context = load_condensed_context(repo_root)
    if is_request:
        system_prompt = build_request_system_prompt(execution_context)
    else:
        system_prompt = build_fix_system_prompt(
            execution_context, test_command=commands.get("test", ""),
        )

    # Callbacks — fire-and-forget (same rationale as plan mode callbacks)
    async def on_tool_call(name: str, args: dict) -> None:
        ws_send_nowait(ws, "tool_progress", {
            "tool": name,
            "status": "running",
            "description": f"{name} {args.get('path', args.get('command', ''))}",
        })
        if conversation_logger:
            t = asyncio.create_task(conversation_logger(
                "tool_call",
                f"{name} {args.get('path', args.get('command', ''))}",
                tool_name=name, tool_args=json.dumps(args),
            ))
            t.add_done_callback(_log_task_exception)

    async def on_tool_result(name: str, result: str) -> None:
        is_error = result.startswith("ERROR:")
        ws_send_nowait(ws, "tool_progress", {
            "tool": name,
            "status": "error" if is_error else "complete",
            "output": result[:500],
        })
        if conversation_logger:
            t = asyncio.create_task(conversation_logger(
                "tool_result", result[:2000],
                tool_name=name,
            ))
            t.add_done_callback(_log_task_exception)

    async def on_content(text: str) -> None:
        ws_send_nowait(ws, "assistant_content", {"content": text})
        if conversation_logger:
            t = asyncio.create_task(conversation_logger("assistant", text))
            t.add_done_callback(_log_task_exception)

    async def on_thinking(text: str) -> None:
        ws_send_nowait(ws, "thinking_content", {"content": text})

    async def on_metrics(prompt_tokens: int, context_window: int) -> None:
        context_pct = round((prompt_tokens / context_window) * 100) if context_window else 0
        ws_send_nowait(ws, "metrics_update", {
            "context_percent": context_pct,
            "prompt_tokens": prompt_tokens,
            "context_window": context_window,
        })

    # ── Investigation phase (fix mode only) ───────────────────────
    # Run read-only tools first so the LLM gathers context before editing.
    executed_investigation: list = []
    if not is_request and settings.enable_fix_investigation:
        await ws_send(ws, "stage_change", {"stage": "investigating"})

        investigation_prompt = build_fix_investigation_prompt(
            execution_context, test_command=commands.get("test", ""),
        )

        messages = [
            {"role": "system", "content": investigation_prompt},
            {"role": "user", "content": task},
        ]

        # Inject existing scratchpad for session recovery
        if session_id:
            existing_pad = scratchpad.read_scratchpad(repo_root, session_id)
            if existing_pad:
                messages.append({
                    "role": "user",
                    "content": (
                        "[SCRATCHPAD FROM PREVIOUS EXECUTION — resume from here]\n"
                        f"{existing_pad}"
                    ),
                })

        executed_investigation, _ = await active_client.chat_with_tools(
            messages=messages,
            tools=build_investigation_tools(),
            tool_executor_fn=tool_executor,
            max_turns=20,
            max_tokens=settings.implementation_max_tokens,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            on_content=on_content,
            on_thinking=on_thinking,
            on_metrics=on_metrics,
            dispatcher=dispatcher,
        )

        # Transition: swap system prompt, nudge LLM to start fixing
        messages[0] = {"role": "system", "content": system_prompt}
        messages.append({
            "role": "user",
            "content": (
                "MODE: IMPLEMENTATION\n"
                "All tools now available: create_file, edit_file, "
                "run_command, format_code (plus all investigation tools).\n"
                "Use your scratchpad diagnosis. Make the minimal fix. "
                "Do not continue investigating."
            ),
        })
    else:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task},
        ]

        # Inject existing scratchpad for session recovery (resume after crash)
        if session_id:
            existing_pad = scratchpad.read_scratchpad(repo_root, session_id)
            if existing_pad:
                messages.append({
                    "role": "user",
                    "content": (
                        "[SCRATCHPAD FROM PREVIOUS EXECUTION — resume from here]\n"
                        f"{existing_pad}"
                    ),
                })

    def _build_fix_reminder() -> str:
        parts = [f"REMINDER — Your task: {task}"]
        pad = scratchpad.read_scratchpad(repo_root, session_id)
        if pad:
            parts.append(f"\nYour current scratchpad:\n{pad}")
        else:
            parts.append(
                "\nCall update_scratchpad to record your progress so far."
            )
        return "\n".join(parts)

    def _build_context_refresh(current_messages: list[dict]) -> list[dict]:
        """Rebuild message list from fresh disk state."""
        fresh_context = load_condensed_context(repo_root)
        if is_request:
            fresh_system_prompt = build_request_system_prompt(fresh_context)
        else:
            fresh_system_prompt = build_fix_system_prompt(
                fresh_context, test_command=commands.get("test", ""),
            )
        pad = scratchpad.read_scratchpad(repo_root, session_id)

        new_messages: list[dict] = [
            {"role": "system", "content": fresh_system_prompt},
            {"role": "user", "content": task},
        ]
        if pad:
            new_messages.append({
                "role": "user",
                "content": f"[CONTEXT REFRESHED]\n\n{pad}",
            })
        else:
            new_messages.append({
                "role": "user",
                "content": "[CONTEXT REFRESHED]\n\nContinue working on the task.",
            })

        ws_send_nowait(ws, "context_refreshed", {
            "message": "Context refreshed — scratchpad provides continuity.",
        })
        return new_messages

    # Request mode: stronger nudge that suggests specific tools for open-ended tasks
    request_nudge = (
        "Call one tool now. Prefer reading workspace state before "
        "broader research."
    ) if is_request else None

    # ── Implementation phase ──────────────────────────────────────
    await ws_send(ws, "stage_change", {"stage": "implementing"})

    executed, explanation = await active_client.chat_with_tools(
        messages=messages,
        tools=build_implementation_tools(),
        tool_executor_fn=tool_executor,
        max_turns=settings.implementation_max_turns,
        max_tokens=settings.implementation_max_tokens,
        task_reminder=_build_fix_reminder,
        reminder_interval=settings.reminder_interval,
        text_only_nudge=request_nudge,
        on_tool_call=on_tool_call,
        on_tool_result=on_tool_result,
        on_content=on_content,
        on_thinking=on_thinking,
        on_metrics=on_metrics,
        on_context_refresh=_build_context_refresh,
        dispatcher=dispatcher,
    )

    # ── Completion ────────────────────────────────────────────────
    files_modified = list({
        tc.parameters.get("path", "")
        for tc in executed
        if tc.tool_name in ("create_file", "edit_file")
        and tc.parameters.get("path")
    })

    # ── Post-execution validation ──
    validation_results: dict = {}
    if files_modified and settings.enable_post_validation:
        validation_results = await _run_post_validation(repo_root, ws)

        # Attempt to fix validation failures via LLM
        if (
            validation_results
            and any(not r["success"] for r in validation_results.values())
            and settings.post_validation_max_retries > 0
        ):
            validation_results = await _run_validation_fix_loop(
                repo_root, ws, llm_client, context,
                validation_results, session_id,
                conversation_logger=conversation_logger,
                expert_llm_client=expert_llm_client,
                dispatcher=dispatcher,
                allowed_files=files_modified,
            )

    all_executed = executed_investigation + executed
    summary = (
        f"Fix complete: {len(all_executed)} tool calls. "
        f"Files modified: {', '.join(files_modified) if files_modified else 'none'}."
    )
    if explanation.strip():
        summary += f"\n\n{explanation.strip()}"
    if validation_results:
        failed = {k: r for k, r in validation_results.items() if not r["success"]}
        if failed:
            summary += "\n\n⚠️ Post-validation failures:"
            for name, result in failed.items():
                summary += f"\n  {name}: {result['output'][:200]}"
        else:
            summary += "\n\n✓ Post-validation passed."

    # ── Incremental project_context.md update ──
    if files_modified and settings.enable_project_context:
        await ws_send(ws, "stage_status", {
            "stage": "context_update",
            "status": "running",
            "summary": f"Updating project context with {len(files_modified)} modified file(s)...",
        })
        try:
            from lean_ai.context.generation import update_project_context

            ctx_path = await update_project_context(
                repo_root, files_modified, llm_client,
            )
            if ctx_path:
                logger.info(
                    "project_context.md updated with %d modified files",
                    len(files_modified),
                )
                await ws_send(ws, "stage_status", {
                    "stage": "context_update",
                    "status": "done",
                    "summary": "Project context updated.",
                })
            else:
                logger.info("project_context.md update skipped (no changes needed)")
                await ws_send(ws, "stage_status", {
                    "stage": "context_update",
                    "status": "done",
                    "summary": "Project context update skipped (no changes needed).",
                })
        except Exception as exc:
            logger.warning("Incremental context update failed (non-fatal): %s", exc)
            await ws_send(ws, "stage_status", {
                "stage": "context_update",
                "status": "done",
                "summary": f"Project context update failed: {exc}",
            })

    complete_data: dict = {"summary": summary, "files_modified": files_modified}
    if branch_name:
        complete_data["plan_branch"] = branch_name
    if base_branch:
        complete_data["base_branch"] = base_branch
    await ws_send(ws, "complete", complete_data)
    logger.info(
        "Fix complete: %d tool calls, %d files",
        len(all_executed), len(files_modified),
    )

    task_summary = task[:72].replace("\n", " ")
    prefix = "lean-ai(request)" if is_request else "lean-ai(fix)"
    commit_msg = f"{prefix}: {task_summary}"
    if files_modified:
        commit_msg += f"\n\nFiles modified: {', '.join(files_modified)}"
    return commit_msg

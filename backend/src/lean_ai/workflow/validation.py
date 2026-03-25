"""Post-execution validation and LLM-driven fix loop.

Extracted from pipeline.py for separation of concerns.
"""

import asyncio
import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import WebSocket

from lean_ai.config import settings
from lean_ai.llm.tool_definitions import (
    IMPLEMENTATION_TOOLS,
    build_tdd_implementation_tools,
)
from lean_ai.routers.context_helpers import load_condensed_context
from lean_ai.workflow.prompts import build_fix_system_prompt
from lean_ai.workflow.tool_executor import make_tool_executor
from lean_ai.workflow.ws_dispatcher import WSMessageDispatcher
from lean_ai.workflow.ws_handler import ws_send, ws_send_nowait

if TYPE_CHECKING:
    from lean_ai.llm.facade import LLMClient

logger = logging.getLogger(__name__)


def _log_task_exception(task: asyncio.Task) -> None:
    """Callback for fire-and-forget tasks — log unhandled exceptions."""
    if not task.cancelled() and task.exception():
        logger.warning("Background task failed: %s", task.exception())


# ── Deterministic Post-Execution Validation ───────────────────────


def _effective_post_commands(repo_root: str) -> dict[str, str]:
    """Resolve post-validation commands: manual settings > auto-detected.

    Returns dict with keys: ``format``, ``lint_fix``, ``lint``, ``test``.
    Manual ``LEAN_AI_POST_*`` settings always take priority over
    auto-detected commands from ``.lean_ai/commands.json``.
    """
    from lean_ai.context.command_detection import load_commands_json

    auto = load_commands_json(repo_root)
    return {
        "format": settings.post_format_command or auto.get("format", ""),
        "lint_fix": settings.post_lint_fix_command or auto.get("lint_fix", ""),
        "lint": settings.post_lint_command or auto.get("lint", ""),
        "test": settings.post_test_command or auto.get("test", ""),
    }


async def _run_post_validation(
    repo_root: str,
    ws: WebSocket,
) -> dict:
    """Run deterministic post-execution lint, test, and auto-fix commands.

    Uses manually configured settings with auto-detected commands as
    fallback.  Auto-fix commands (format, lint-fix) run first and
    silently succeed; lint and test commands report pass/fail status
    via WebSocket.

    Returns a dict of results keyed by step name.
    """
    from lean_ai.tools import shell

    results: dict[str, dict] = {}
    commands = _effective_post_commands(repo_root)

    if not any(commands.values()):
        return results

    await ws_send(ws, "stage_status", {
        "stage": "post_validation",
        "status": "running",
        "summary": "Running post-execution validation...",
    })

    # ── Auto-fix passes (silent success, report failure) ──
    for label, command, runner in [
        ("format", commands["format"], shell.format_code),
        ("lint_fix", commands["lint_fix"], shell.run_lint),
    ]:
        if not command:
            continue
        try:
            result = await runner(command=command, repo_root=repo_root)
            results[label] = {
                "success": result.success,
                "output": result.output[:2000] if result.output else "",
                "full_output": result.output or "",
            }
            if not result.success:
                logger.warning(
                    "Post-validation %s failed (exit %s): %s",
                    label, result.exit_code, result.output[:500],
                )
        except Exception as exc:
            logger.warning("Post-validation %s error: %s", label, exc)
            results[label] = {
                "success": False, "output": str(exc), "full_output": str(exc),
            }

    # ── Reporting passes (lint + test in parallel — both read-only) ──
    reporting_steps = [
        (label, cmd, runner)
        for label, cmd, runner in [
            ("lint", commands["lint"], shell.run_lint),
            ("test", commands["test"], shell.run_tests),
        ]
        if cmd
    ]

    if reporting_steps:
        async def _run_report(label, command, runner):
            try:
                result = await runner(command=command, repo_root=repo_root)
                return label, command, result, None
            except Exception as exc:
                return label, command, None, exc

        report_results = await asyncio.gather(
            *[_run_report(lbl, cmd, fn) for lbl, cmd, fn in reporting_steps],
        )

        for label, command, result, exc in report_results:
            if exc is not None:
                logger.warning("Post-validation %s error: %s", label, exc)
                results[label] = {
                    "success": False,
                    "output": str(exc),
                    "full_output": str(exc),
                }
                await ws_send(ws, "test_result", {
                    "command": command,
                    "passed": False,
                    "output": str(exc),
                })
            else:
                results[label] = {
                    "success": result.success,
                    "output": result.output[:2000] if result.output else "",
                    "full_output": result.output or "",
                }
                await ws_send(ws, "test_result", {
                    "command": command,
                    "passed": result.success,
                    "output": result.output[:2000] if result.output else "",
                })

    # ── Summary ──
    passed = sum(1 for r in results.values() if r["success"])
    total = len(results)
    summary = f"Post-validation: {passed}/{total} passed."
    if any(not r["success"] for r in results.values()):
        failed_names = [k for k, r in results.items() if not r["success"]]
        summary += f" Failed: {', '.join(failed_names)}."

    await ws_send(ws, "stage_status", {
        "stage": "post_validation",
        "status": "done",
        "summary": summary,
    })

    logger.info("Post-validation complete: %s", summary)
    return results


# ── Validation-Resubmission Loop ──────────────────────────────────


async def _run_validation_fix_loop(
    repo_root: str,
    ws: WebSocket,
    llm_client: "LLMClient",
    context: str,
    validation_results: dict,
    session_id: str = "",
    conversation_logger: Callable | None = None,
    expert_llm_client: "LLMClient | None" = None,
    dispatcher: WSMessageDispatcher | None = None,
) -> dict:
    """Attempt to fix post-validation failures by resubmitting to the LLM.

    Runs up to ``post_validation_max_retries`` fix attempts.  Each attempt:

    1. Builds a focused fix prompt from failure output
    2. Runs ``chat_with_tools`` with a 30-turn budget
    3. Re-runs ``_run_post_validation`` (including auto-fix passes)

    Returns the final validation results dict.
    """
    max_retries = settings.post_validation_max_retries
    if max_retries <= 0:
        return validation_results

    system_prompt = build_fix_system_prompt(load_condensed_context(repo_root))

    # Callbacks — same WebSocket progress reporting used by the main loop
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

    async def on_metrics(prompt_tokens: int, context_window: int) -> None:
        context_pct = (
            round((prompt_tokens / context_window) * 100) if context_window else 0
        )
        ws_send_nowait(ws, "metrics_update", {
            "context_percent": context_pct,
            "prompt_tokens": prompt_tokens,
            "context_window": context_window,
        })

    attempts_used = 0
    for attempt in range(max_retries):
        # Check which validations failed
        failures = {k: v for k, v in validation_results.items() if not v["success"]}
        if not failures:
            break  # All fixed

        attempts_used = attempt + 1

        # Escalate to expert model on final attempt
        is_final_attempt = (attempt == max_retries - 1)
        active_client = (
            expert_llm_client if is_final_attempt and expert_llm_client
            else llm_client
        )

        logger.info(
            "Validation fix attempt %d/%d: %d failure(s) — %s%s",
            attempts_used, max_retries, len(failures),
            ", ".join(failures.keys()),
            " (escalating to expert model)" if is_final_attempt and expert_llm_client else "",
        )

        escalation_note = ""
        if is_final_attempt and expert_llm_client:
            escalation_note = (
                f" (escalating to expert model: "
                f"{expert_llm_client._provider.model_name})"
            )

        await ws_send(ws, "stage_status", {
            "stage": "validation_fix",
            "status": "running",
            "summary": (
                f"Fix attempt {attempts_used}/{max_retries}: "
                f"fixing {len(failures)} failure(s)..."
                f"{escalation_note}"
            ),
        })

        # Build focused fix prompt with full error output
        failure_parts: list[str] = []
        for name, result in failures.items():
            raw = result.get("full_output", result["output"])
            if len(raw) > 8000:
                # Tail is where errors are — keep last 80 lines
                lines = raw.splitlines()
                raw = "\n".join(lines[-80:])
            failure_parts.append(
                f"### {name}\n```\n{raw}\n```"
            )
        failure_text = "\n\n".join(failure_parts)

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "Post-execution validation detected the following failures. "
                    "Follow this workflow:\n"
                    "1. Re-run the failing command to confirm the error before "
                    "touching any code.\n"
                    "2. Read the relevant files to locate the root cause.\n"
                    "3. State your diagnosis in update_scratchpad before making "
                    "changes — if your assumption about the cause is wrong, "
                    "update it.\n"
                    "4. Make the minimal fix.\n"
                    "5. If the fix does not work on the first try, call "
                    "search_internet with the error message before guessing "
                    "again — documentation is more reliable than "
                    "trial-and-error.\n"
                    "6. Re-run the command to verify the fix works. "
                    "If it still fails, revise your diagnosis and repeat "
                    "from step 2.\n"
                    "Focus ONLY on these specific failures — do not make "
                    "unrelated changes.\n\n"
                    + failure_text
                ),
            },
        ]

        # Run LLM with a tight turn budget
        # In TDD mode, protect test files and allow disputes
        tdd_fix_protect = settings.enable_tdd and expert_llm_client is not None
        tdd_fix_dispute = None
        if tdd_fix_protect:
            from lean_ai.workflow.tdd import evaluate_test_dispute as _eval_dispute

            async def tdd_fix_dispute(arguments: dict) -> str:
                return await _eval_dispute(
                    test_file=arguments["test_file"],
                    test_function=arguments["test_function"],
                    reason=arguments["reason"],
                    repo_root=repo_root,
                    expert_client=expert_llm_client,
                    ws=ws,
                    session_id=session_id,
                    dispatcher=dispatcher,
                )

        fix_tools = (
            build_tdd_implementation_tools()
            if tdd_fix_protect
            else IMPLEMENTATION_TOOLS
        )
        tool_executor = make_tool_executor(
            repo_root, ws, session_id, llm_client=active_client,
            dispatcher=dispatcher,
            tdd_protect_tests=tdd_fix_protect,
            on_test_dispute=tdd_fix_dispute,
        )
        executed, explanation = await active_client.chat_with_tools(
            messages=messages,
            tools=fix_tools,
            tool_executor_fn=tool_executor,
            max_turns=settings.post_validation_fix_turns,
            max_tokens=settings.implementation_max_tokens,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            on_content=on_content,
            on_metrics=on_metrics,
            dispatcher=dispatcher,
        )

        logger.info(
            "Validation fix attempt %d: %d tool calls, re-validating...",
            attempts_used, len(executed),
        )

        # Re-run full validation (including auto-fix passes)
        validation_results = await _run_post_validation(repo_root, ws)

    # Report final status
    final_failures = {
        k: v for k, v in validation_results.items() if not v["success"]
    }
    if attempts_used > 0:
        if final_failures:
            fix_summary = (
                f"Validation fix: {attempts_used} attempt(s), "
                f"{len(final_failures)} failure(s) remain: "
                f"{', '.join(final_failures.keys())}."
            )
        else:
            fix_summary = (
                f"Validation fix: all issues resolved after "
                f"{attempts_used} attempt(s)."
            )

        await ws_send(ws, "stage_status", {
            "stage": "validation_fix",
            "status": "done",
            "summary": fix_summary,
        })
        logger.info("Validation fix loop complete: %s", fix_summary)

    return validation_results

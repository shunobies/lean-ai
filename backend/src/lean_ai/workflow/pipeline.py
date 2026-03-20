"""Plan-driven agentic workflow: plan → approve → execute per step.

The planner does ALL investigatory work (reads files, explores the codebase,
designs changes).  It produces a structured ExecutionPlan where each step
maps to one tool call.  After user approval, a constrained LLM executor
handles each step in 1-3 turns — translating the planner's detailed
instruction into a single tool invocation.
"""

import asyncio
import json
import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import WebSocket, WebSocketDisconnect

from lean_ai.config import settings
from lean_ai.llm.plan_schema import ExecutionPlan, plan_to_markdown
from lean_ai.llm.planner import assess_clarity, create_plan
from lean_ai.llm.tool_definitions import IMPLEMENTATION_TOOLS
from lean_ai.routers.context_helpers import load_full_context
from lean_ai.tools import scratchpad
from lean_ai.workflow.prompts import (
    build_fix_system_prompt,
    build_request_system_prompt,
    build_step_system_prompt,
    build_step_user_message,
)
from lean_ai.workflow.tool_executor import make_tool_executor
from lean_ai.workflow.ws_handler import safe_receive, ws_send, ws_send_nowait

if TYPE_CHECKING:
    from lean_ai.llm.client import LLMClient
    from lean_ai.llm.refiner import PromptRefiner

logger = logging.getLogger(__name__)

# Max tool-calling turns per step.
# 0 = unlimited — the agent makes as many exploratory and fix-up
# calls as it needs.  Override via LEAN_AI_IMPLEMENTATION_MAX_TURNS.
_MAX_TURNS_PER_STEP = 0  # default unlimited; overridden by settings

# Max plan revision rounds before giving up
_MAX_REVISIONS = 5


# ── Public API ──────────────────────────────────────────────────────


async def run_workflow(
    task: str,
    repo_root: str,
    ws: WebSocket,
    llm_client: "LLMClient",
    context: str = "",
    branch_name: str = "",
    base_branch: str = "",
    conversation_logger: Callable | None = None,
    mode: str = "plan",
    session_id: str = "",
    refiner: "PromptRefiner | None" = None,
    expert_llm_client: "LLMClient | None" = None,
    request_llm_client: "LLMClient | None" = None,
) -> str:
    """Run a workflow. Supports three modes:

    - ``"plan"`` (default): clarify → plan → approve → execute
    - ``"fix"``: skip planning, bug-fix prompt
    - ``"request"``: skip planning, neutral prompt with internet search

    Returns a structured commit message summarising the actions taken.
    """
    logger.info("Workflow (%s): starting task: %s", mode, task[:100])

    # Log the initial task
    if conversation_logger:
        await conversation_logger("user", task)

    if mode in ("fix", "request"):
        return await _run_fix(
            task=task,
            repo_root=repo_root,
            ws=ws,
            llm_client=llm_client,
            context=context,
            branch_name=branch_name,
            base_branch=base_branch,
            conversation_logger=conversation_logger,
            session_id=session_id,
            expert_llm_client=expert_llm_client,
            request_llm_client=request_llm_client,
            mode=mode,
        )

    # ── Phase 1: Clarify (optional) ──────────────────────────────
    task_with_answers = await _clarify_task(task, ws, llm_client, context)

    # ── Phase 2: Plan ────────────────────────────────────────────
    await ws_send(ws, "stage_change", {"stage": "planning"})
    plan_commands = _effective_post_commands(repo_root)
    plan = await create_plan(
        task=task_with_answers,
        repo_root=repo_root,
        llm_client=llm_client,
        context=context,
        ws=ws,
        refiner=refiner,
        test_command=plan_commands.get("test", ""),
        session_id=session_id,
        expert_llm_client=expert_llm_client,
    )

    # ── Phase 3: Approve ─────────────────────────────────────────
    approved_plan = await _wait_for_approval(
        plan=plan,
        task=task_with_answers,
        repo_root=repo_root,
        llm_client=llm_client,
        context=context,
        ws=ws,
        refiner=refiner,
        test_command=plan_commands.get("test", ""),
        expert_llm_client=expert_llm_client,
    )

    # ── Phase 4: Execute per-step ────────────────────────────────
    await ws_send(ws, "stage_change", {"stage": "implementing"})
    return await _execute_plan(
        plan=approved_plan,
        task=task_with_answers,
        repo_root=repo_root,
        ws=ws,
        llm_client=llm_client,
        context=context,
        branch_name=branch_name,
        base_branch=base_branch,
        conversation_logger=conversation_logger,
        session_id=session_id,
        expert_llm_client=expert_llm_client,
    )


# ── Phase 1: Clarification ─────────────────────────────────────────


async def _clarify_task(
    task: str,
    ws: WebSocket,
    llm_client: "LLMClient",
    context: str,
) -> str:
    """Optionally ask clarifying questions before planning.

    Returns the original task augmented with user answers, or the task
    unchanged if no clarifications were needed.
    """
    questions = await assess_clarity(task, llm_client, context)
    if questions is None:
        logger.info("Task is clear — skipping clarification")
        return task

    logger.info("Clarification needed — %d questions", len(questions))
    await ws_send(ws, "clarification_needed", {"questions": questions})

    # Wait for user to respond
    while True:
        msg = await safe_receive(ws)
        if msg is None:
            raise WebSocketDisconnect()

        if msg.get("type") == "user_message":
            answer = msg.get("content", "")
            augmented = (
                f"{task}\n\n"
                f"ADDITIONAL DETAILS (from clarification):\n{answer}"
            )
            logger.info("Received clarification answer (%d chars)", len(answer))
            return augmented

        if msg.get("type") == "ping":
            await ws_send(ws, "pong")
            continue


# ── Phase 3: Approval ──────────────────────────────────────────────


async def _wait_for_approval(
    plan: ExecutionPlan,
    task: str,
    repo_root: str,
    llm_client: "LLMClient",
    context: str,
    ws: WebSocket,
    refiner: "PromptRefiner | None" = None,
    test_command: str = "",
    expert_llm_client: "LLMClient | None" = None,
) -> ExecutionPlan:
    """Send the plan for user approval. Handle feedback/revision loop.

    Returns the approved ExecutionPlan.
    """
    plan_md = plan_to_markdown(plan)
    await ws_send(ws, "approval_required", {
        "plan": plan_md,
        "user_summary": plan.user_summary,
    })
    revision_count = 0

    while True:
        msg = await safe_receive(ws)
        if msg is None:
            raise WebSocketDisconnect()

        if msg.get("type") == "approve":
            logger.info("Plan approved by user")
            return plan

        if msg.get("type") == "user_message":
            # User sent feedback — revise the plan
            feedback = msg.get("content", "")
            revision_count += 1

            if revision_count > _MAX_REVISIONS:
                logger.warning("Max plan revisions reached (%d)", _MAX_REVISIONS)
                await ws_send(ws, "error", {
                    "message": (
                        f"Maximum revision limit ({_MAX_REVISIONS}) reached. "
                        "Please start a new session."
                    ),
                    "recoverable": False,
                })
                raise WebSocketDisconnect()

            await ws_send(ws, "plan_rejected", {
                "feedback": feedback,
                "stage": "planning",
            })

            revision_context = (
                f"PREVIOUS PLAN:\n{plan.model_dump_json(indent=2)}\n\n"
                f"USER FEEDBACK:\n{feedback}"
            )
            plan = await create_plan(
                task=task,
                repo_root=repo_root,
                llm_client=llm_client,
                context=context,
                revision_context=revision_context,
                ws=ws,
                refiner=refiner,
                test_command=test_command,
                expert_llm_client=expert_llm_client,
            )
            plan_md = plan_to_markdown(plan)
            await ws_send(ws, "plan_revision", {
                "review_feedback": feedback,
                "revision_number": revision_count,
            })
            await ws_send(ws, "approval_required", {
                "plan": plan_md,
                "user_summary": plan.user_summary,
            })
            continue

        if msg.get("type") == "ping":
            await ws_send(ws, "pong")
            continue


# ── Phase 4: Per-Step Execution ────────────────────────────────────


async def _execute_plan(
    plan: ExecutionPlan,
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
) -> str:
    """Execute each plan step sequentially with a constrained LLM."""
    tool_executor = make_tool_executor(repo_root, ws, session_id, llm_client=llm_client)
    total_steps = len(plan.steps)
    all_executed = []
    step_explanations: list[str] = []
    completed_descriptions: list[str] = []

    # Build the system prompt once (shared across all steps)
    system_prompt = build_step_system_prompt(
        context,
        naming_conventions=getattr(plan, "naming_conventions", ""),
    )

    # Callbacks for WebSocket progress + conversation logging.
    # Progress messages are fire-and-forget (non-blocking) since they are
    # informational.  Conversation logging is also fire-and-forget — the
    # data is for post-hoc review and does not need to block tool execution.
    async def on_tool_call(name: str, args: dict) -> None:
        ws_send_nowait(ws, "tool_progress", {
            "tool": name,
            "status": "running",
            "description": f"{name} {args.get('path', args.get('command', ''))}",
        })
        if conversation_logger:
            asyncio.create_task(conversation_logger(
                "tool_call", f"{name} {args.get('path', args.get('command', ''))}",
                tool_name=name, tool_args=json.dumps(args),
            ))

    async def on_tool_result(name: str, result: str) -> None:
        is_error = result.startswith("ERROR:")
        ws_send_nowait(ws, "tool_progress", {
            "tool": name,
            "status": "error" if is_error else "complete",
            "output": result[:500],
        })
        if conversation_logger:
            asyncio.create_task(conversation_logger(
                "tool_result", result[:2000],
                tool_name=name,
            ))

    async def on_content(text: str) -> None:
        ws_send_nowait(ws, "assistant_content", {"content": text})
        if conversation_logger:
            asyncio.create_task(conversation_logger("assistant", text))

    async def on_thinking(text: str) -> None:
        ws_send_nowait(ws, "thinking_content", {"content": text})

    async def on_metrics(prompt_tokens: int, context_window: int) -> None:
        context_pct = round((prompt_tokens / context_window) * 100) if context_window else 0
        ws_send_nowait(ws, "metrics_update", {
            "context_percent": context_pct,
            "prompt_tokens": prompt_tokens,
            "context_window": context_window,
        })

    # Execute each step
    for step in plan.steps:
        logger.info(
            "Executing step %d/%d: %s %s — %s",
            step.step_number, total_steps, step.tool,
            step.file_path, step.instruction[:80],
        )

        # Send checkpoint: step starting
        await ws_send(ws, "checkpoint", {
            "step_index": step.step_number - 1,
            "step_description": f"Step {step.step_number}: {step.instruction[:100]}",
            "status": "running",
            "head_commit_sha": None,
        })

        # Build step-specific user message
        user_msg = build_step_user_message(
            step, completed_descriptions, total_steps,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]

        # Execute this step — turn budget comes from settings (0 = unlimited)
        executed, explanation = await llm_client.chat_with_tools(
            messages=messages,
            tools=IMPLEMENTATION_TOOLS,
            tool_executor_fn=tool_executor,
            max_turns=settings.implementation_max_turns,
            max_tokens=settings.implementation_max_tokens,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            on_content=on_content,
            on_thinking=on_thinking,
            on_metrics=on_metrics,
        )

        all_executed.extend(executed)
        if explanation.strip():
            step_explanations.append(
                f"Step {step.step_number}: {explanation.strip()}"
            )
        completed_descriptions.append(
            f"Step {step.step_number}: {step.instruction[:100]}"
        )

        # Send checkpoint: step completed
        await ws_send(ws, "checkpoint", {
            "step_index": step.step_number - 1,
            "step_description": f"Step {step.step_number}: {step.instruction[:100]}",
            "status": "completed",
            "head_commit_sha": None,
        })

    # ── All steps done ───────────────────────────────────────────
    files_modified = list({
        tc.parameters.get("path", "")
        for tc in all_executed
        if tc.tool_name in ("create_file", "edit_file") and tc.parameters.get("path")
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
            )

    # Check for incomplete.md
    incomplete_path = os.path.join(repo_root, ".lean_ai", "incomplete.md")
    incomplete_content = ""
    if os.path.isfile(incomplete_path):
        try:
            with open(incomplete_path, encoding="utf-8") as f:
                incomplete_content = f.read()
        except Exception:
            pass

    summary = (
        f"Completed {len(plan.steps)} plan steps, "
        f"{len(all_executed)} tool calls. "
        f"Files modified: {', '.join(files_modified) if files_modified else 'none'}."
    )
    if step_explanations:
        summary += "\n\n" + "\n".join(step_explanations)
    if incomplete_content:
        summary += (
            "\n\n⚠️ Some steps had issues — see "
            f".lean_ai/incomplete.md:\n{incomplete_content}"
        )
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
        "Workflow complete: %d steps, %d tool calls, %d files",
        len(plan.steps), len(all_executed), len(files_modified),
    )

    # Build commit message
    task_summary = task[:72].replace("\n", " ")
    commit_msg = f"lean-ai: {task_summary}"
    if files_modified:
        commit_msg += f"\n\nFiles modified: {', '.join(files_modified)}"
    return commit_msg


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

    system_prompt = build_fix_system_prompt(context)

    # Callbacks — same WebSocket progress reporting used by the main loop
    async def on_tool_call(name: str, args: dict) -> None:
        ws_send_nowait(ws, "tool_progress", {
            "tool": name,
            "status": "running",
            "description": f"{name} {args.get('path', args.get('command', ''))}",
        })
        if conversation_logger:
            asyncio.create_task(conversation_logger(
                "tool_call",
                f"{name} {args.get('path', args.get('command', ''))}",
                tool_name=name, tool_args=json.dumps(args),
            ))

    async def on_tool_result(name: str, result: str) -> None:
        is_error = result.startswith("ERROR:")
        ws_send_nowait(ws, "tool_progress", {
            "tool": name,
            "status": "error" if is_error else "complete",
            "output": result[:500],
        })
        if conversation_logger:
            asyncio.create_task(conversation_logger(
                "tool_result", result[:2000],
                tool_name=name,
            ))

    async def on_content(text: str) -> None:
        ws_send_nowait(ws, "assistant_content", {"content": text})
        if conversation_logger:
            asyncio.create_task(conversation_logger("assistant", text))

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
                    "5. Re-run the command to verify the fix works. "
                    "If it still fails, revise your diagnosis and repeat.\n"
                    "Focus ONLY on these specific failures — do not make "
                    "unrelated changes.\n\n"
                    + failure_text
                ),
            },
        ]

        # Run LLM with a tight turn budget
        tool_executor = make_tool_executor(
            repo_root, ws, session_id, llm_client=active_client,
        )
        executed, explanation = await active_client.chat_with_tools(
            messages=messages,
            tools=IMPLEMENTATION_TOOLS,
            tool_executor_fn=tool_executor,
            max_turns=settings.post_validation_fix_turns,
            max_tokens=settings.implementation_max_tokens,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            on_content=on_content,
            on_metrics=on_metrics,
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


# ── Fix Mode (no planning) ─────────────────────────────────────────


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
) -> str:
    """Execute directly — no planning, no approval.

    The LLM gets the full tool set and runs until it decides it's done.
    When *mode* is ``"request"``, uses a neutral prompt and optionally
    a separate request model.
    """
    await ws_send(ws, "stage_change", {"stage": "implementing"})

    is_request = mode == "request"
    # Use dedicated request model when available for /request mode
    active_client = (
        request_llm_client if is_request and request_llm_client else llm_client
    )
    tool_executor = make_tool_executor(
        repo_root, ws, session_id, llm_client=active_client,
    )
    commands = _effective_post_commands(repo_root)
    if is_request:
        system_prompt = build_request_system_prompt(context)
    else:
        system_prompt = build_fix_system_prompt(
            context, test_command=commands.get("test", ""),
        )

    # Callbacks — fire-and-forget (same rationale as plan mode callbacks)
    async def on_tool_call(name: str, args: dict) -> None:
        ws_send_nowait(ws, "tool_progress", {
            "tool": name,
            "status": "running",
            "description": f"{name} {args.get('path', args.get('command', ''))}",
        })
        if conversation_logger:
            asyncio.create_task(conversation_logger(
                "tool_call",
                f"{name} {args.get('path', args.get('command', ''))}",
                tool_name=name, tool_args=json.dumps(args),
            ))

    async def on_tool_result(name: str, result: str) -> None:
        is_error = result.startswith("ERROR:")
        ws_send_nowait(ws, "tool_progress", {
            "tool": name,
            "status": "error" if is_error else "complete",
            "output": result[:500],
        })
        if conversation_logger:
            asyncio.create_task(conversation_logger(
                "tool_result", result[:2000],
                tool_name=name,
            ))

    async def on_content(text: str) -> None:
        ws_send_nowait(ws, "assistant_content", {"content": text})
        if conversation_logger:
            asyncio.create_task(conversation_logger("assistant", text))

    async def on_thinking(text: str) -> None:
        ws_send_nowait(ws, "thinking_content", {"content": text})

    async def on_metrics(prompt_tokens: int, context_window: int) -> None:
        context_pct = round((prompt_tokens / context_window) * 100) if context_window else 0
        ws_send_nowait(ws, "metrics_update", {
            "context_percent": context_pct,
            "prompt_tokens": prompt_tokens,
            "context_window": context_window,
        })

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
        fresh_context = load_full_context(repo_root)
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
                "content": (
                    "[CONTEXT REFRESHED — conversation history cleared to "
                    "free context space. Your scratchpad has your "
                    "progress.]\n\n" + pad
                ),
            })
        else:
            new_messages.append({
                "role": "user",
                "content": (
                    "[CONTEXT REFRESHED — conversation history cleared to "
                    "free context space.]\n\n"
                    "Call update_scratchpad to record your progress, "
                    "then continue working on the task."
                ),
            })

        ws_send_nowait(ws, "context_refreshed", {
            "message": "Context refreshed — scratchpad provides continuity.",
        })
        return new_messages

    # Request mode: stronger nudge that suggests specific tools for open-ended tasks
    request_nudge = (
        "STOP generating text. You MUST call a tool now. "
        "Based on the task, call search_internet to research the topic, "
        "or call directory_tree to explore the project. "
        "Do not explain — act."
    ) if is_request else None

    executed, explanation = await active_client.chat_with_tools(
        messages=messages,
        tools=IMPLEMENTATION_TOOLS,
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
            )

    summary = (
        f"Fix complete: {len(executed)} tool calls. "
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
        len(executed), len(files_modified),
    )

    task_summary = task[:72].replace("\n", " ")
    prefix = "lean-ai(request)" if is_request else "lean-ai(fix)"
    commit_msg = f"{prefix}: {task_summary}"
    if files_modified:
        commit_msg += f"\n\nFiles modified: {', '.join(files_modified)}"
    return commit_msg

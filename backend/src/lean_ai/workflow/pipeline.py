"""Plan-driven agentic workflow: plan → approve → execute per step.

The planner does ALL investigatory work (reads files, explores the codebase,
designs changes).  It produces a structured ExecutionPlan where each step
maps to one tool call.  After user approval, a constrained LLM executor
handles each step in 1-3 turns — translating the planner's detailed
instruction into a single tool invocation.
"""

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import WebSocket, WebSocketDisconnect

from lean_ai.config import settings
from lean_ai.llm.plan_schema import ExecutionPlan, PlanStep, plan_to_markdown
from lean_ai.llm.planner import assess_clarity, create_plan
from lean_ai.llm.prompts import FIX_SYSTEM_PROMPT, STEP_EXECUTION_SYSTEM_PROMPT
from lean_ai.llm.tool_definitions import IMPLEMENTATION_TOOLS
from lean_ai.tools import file_ops, scratchpad, shell
from lean_ai.tools.command_safety import CommandRisk, check_command
from lean_ai.workflow.ws_handler import safe_receive, ws_send

if TYPE_CHECKING:
    from lean_ai.llm.client import LLMClient

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
    conversation_logger: Callable | None = None,
    mode: str = "plan",
    session_id: str = "",
) -> str:
    """Run a workflow. Supports two modes:

    - ``"plan"`` (default): clarify → plan → approve → execute
    - ``"fix"``: skip planning, give the LLM tools and let it work

    Returns a structured commit message summarising the actions taken.
    """
    logger.info("Workflow (%s): starting task: %s", mode, task[:100])

    # Log the initial task
    if conversation_logger:
        await conversation_logger("user", task)

    if mode == "fix":
        return await _run_fix(
            task=task,
            repo_root=repo_root,
            ws=ws,
            llm_client=llm_client,
            context=context,
            branch_name=branch_name,
            conversation_logger=conversation_logger,
            session_id=session_id,
        )

    # ── Phase 1: Clarify (optional) ──────────────────────────────
    task_with_answers = await _clarify_task(task, ws, llm_client, context)

    # ── Phase 2: Plan ────────────────────────────────────────────
    await ws_send(ws, "stage_change", {"stage": "planning"})
    plan = await create_plan(
        task=task_with_answers,
        repo_root=repo_root,
        llm_client=llm_client,
        context=context,
        ws=ws,
    )

    # ── Phase 3: Approve ─────────────────────────────────────────
    approved_plan = await _wait_for_approval(
        plan=plan,
        task=task_with_answers,
        repo_root=repo_root,
        llm_client=llm_client,
        context=context,
        ws=ws,
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
        conversation_logger=conversation_logger,
        session_id=session_id,
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
) -> ExecutionPlan:
    """Send the plan for user approval. Handle feedback/revision loop.

    Returns the approved ExecutionPlan.
    """
    plan_md = plan_to_markdown(plan)
    await ws_send(ws, "approval_required", {"plan": plan_md})
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
            )
            plan_md = plan_to_markdown(plan)
            await ws_send(ws, "plan_revision", {
                "review_feedback": feedback,
                "revision_number": revision_count,
            })
            await ws_send(ws, "approval_required", {"plan": plan_md})
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
    conversation_logger: Callable | None,
    session_id: str = "",
) -> str:
    """Execute each plan step sequentially with a constrained LLM."""
    tool_executor = _make_tool_executor(repo_root, ws, session_id)
    total_steps = len(plan.steps)
    all_executed = []
    step_explanations: list[str] = []
    completed_descriptions: list[str] = []

    # Build the system prompt once (shared across all steps)
    system_prompt = _build_step_system_prompt(context)

    # Callbacks for WebSocket progress + conversation logging
    async def on_tool_call(name: str, args: dict) -> None:
        await ws_send(ws, "tool_progress", {
            "tool": name,
            "status": "running",
            "description": f"{name} {args.get('path', args.get('command', ''))}",
        })
        if conversation_logger:
            await conversation_logger(
                "tool_call", f"{name} {args.get('path', args.get('command', ''))}",
                tool_name=name, tool_args=json.dumps(args),
            )

    async def on_tool_result(name: str, result: str) -> None:
        is_error = result.startswith("ERROR:")
        await ws_send(ws, "tool_progress", {
            "tool": name,
            "status": "error" if is_error else "complete",
            "output": result[:500],
        })
        if conversation_logger:
            await conversation_logger(
                "tool_result", result[:2000],
                tool_name=name,
            )

    async def on_content(text: str) -> None:
        await ws_send(ws, "assistant_content", {"content": text})
        if conversation_logger:
            await conversation_logger("assistant", text)

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
        user_msg = _build_step_user_message(
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

    # ── Incremental project_context.md update ──
    if files_modified:
        try:
            if settings.enable_project_context:
                from lean_ai.context.generation import update_project_context

                ctx_path = await update_project_context(
                    repo_root, files_modified, llm_client,
                )
                if ctx_path:
                    logger.info(
                        "project_context.md updated with %d modified files",
                        len(files_modified),
                    )
        except Exception as exc:
            logger.warning("Incremental context update failed (non-fatal): %s", exc)

    complete_data: dict = {"summary": summary, "files_modified": files_modified}
    if branch_name:
        complete_data["plan_branch"] = branch_name
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


# ── Fix Mode (no planning) ─────────────────────────────────────────


async def _run_fix(
    task: str,
    repo_root: str,
    ws: WebSocket,
    llm_client: "LLMClient",
    context: str,
    branch_name: str,
    conversation_logger: Callable | None,
    session_id: str = "",
) -> str:
    """Execute a fix directly — no planning, no approval.

    The LLM gets the full tool set and runs until it decides it's done.
    """
    await ws_send(ws, "stage_change", {"stage": "implementing"})

    tool_executor = _make_tool_executor(repo_root, ws, session_id)
    system_prompt = _build_fix_system_prompt(context)

    # Callbacks for WebSocket progress + conversation logging
    async def on_tool_call(name: str, args: dict) -> None:
        await ws_send(ws, "tool_progress", {
            "tool": name,
            "status": "running",
            "description": f"{name} {args.get('path', args.get('command', ''))}",
        })
        if conversation_logger:
            await conversation_logger(
                "tool_call",
                f"{name} {args.get('path', args.get('command', ''))}",
                tool_name=name, tool_args=json.dumps(args),
            )

    async def on_tool_result(name: str, result: str) -> None:
        is_error = result.startswith("ERROR:")
        await ws_send(ws, "tool_progress", {
            "tool": name,
            "status": "error" if is_error else "complete",
            "output": result[:500],
        })
        if conversation_logger:
            await conversation_logger(
                "tool_result", result[:2000],
                tool_name=name,
            )

    async def on_content(text: str) -> None:
        await ws_send(ws, "assistant_content", {"content": text})
        if conversation_logger:
            await conversation_logger("assistant", text)

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

    executed, explanation = await llm_client.chat_with_tools(
        messages=messages,
        tools=IMPLEMENTATION_TOOLS,
        tool_executor_fn=tool_executor,
        max_turns=settings.implementation_max_turns,
        max_tokens=settings.implementation_max_tokens,
        task_reminder=(
            f"REMINDER — Your task: {task}\n\n"
            "Have you verified the fix works? Run tests or lint if "
            "applicable. If done, call task_complete with a summary."
        ),
        reminder_interval=settings.reminder_interval,
        on_tool_call=on_tool_call,
        on_tool_result=on_tool_result,
        on_content=on_content,
    )

    # ── Completion ────────────────────────────────────────────────
    files_modified = list({
        tc.parameters.get("path", "")
        for tc in executed
        if tc.tool_name in ("create_file", "edit_file")
        and tc.parameters.get("path")
    })

    summary = (
        f"Fix complete: {len(executed)} tool calls. "
        f"Files modified: {', '.join(files_modified) if files_modified else 'none'}."
    )
    if explanation.strip():
        summary += f"\n\n{explanation.strip()}"

    # ── Incremental project_context.md update ──
    if files_modified:
        try:
            if settings.enable_project_context:
                from lean_ai.context.generation import update_project_context

                ctx_path = await update_project_context(
                    repo_root, files_modified, llm_client,
                )
                if ctx_path:
                    logger.info(
                        "project_context.md updated with %d modified files",
                        len(files_modified),
                    )
        except Exception as exc:
            logger.warning("Incremental context update failed (non-fatal): %s", exc)

    complete_data: dict = {"summary": summary, "files_modified": files_modified}
    if branch_name:
        complete_data["plan_branch"] = branch_name
    await ws_send(ws, "complete", complete_data)
    logger.info(
        "Fix complete: %d tool calls, %d files",
        len(executed), len(files_modified),
    )

    task_summary = task[:72].replace("\n", " ")
    commit_msg = f"lean-ai(fix): {task_summary}"
    if files_modified:
        commit_msg += f"\n\nFiles modified: {', '.join(files_modified)}"
    return commit_msg


# ── Prompt Builders ────────────────────────────────────────────────


def _build_fix_system_prompt(context: str) -> str:
    """Build the system prompt for fix mode (no planning)."""
    if not context:
        return FIX_SYSTEM_PROMPT

    max_context = 3000
    ctx = context[:max_context]
    if len(context) > max_context:
        ctx += "\n... (condensed)"

    return (
        f"{FIX_SYSTEM_PROMPT}\n"
        f"## Project Context\n\n{ctx}"
    )


def _build_step_system_prompt(context: str) -> str:
    """Build the system prompt for per-step execution."""
    if not context:
        return STEP_EXECUTION_SYSTEM_PROMPT

    # Include condensed project context so the executor knows patterns
    max_context = 3000
    ctx = context[:max_context]
    if len(context) > max_context:
        ctx += "\n... (condensed)"

    return (
        f"{STEP_EXECUTION_SYSTEM_PROMPT}\n"
        f"## Project Context\n\n{ctx}"
    )


def _build_step_user_message(
    step: PlanStep,
    completed: list[str],
    total_steps: int,
) -> str:
    """Build the user message for a specific step execution."""
    parts: list[str] = []

    # Progress header
    parts.append(
        f"STEP {step.step_number} OF {total_steps}"
    )

    if completed:
        parts.append("\nCompleted so far:")
        for desc in completed:
            parts.append(f"  ✓ {desc}")
        parts.append("")

    # Step details
    parts.append(f"Tool: {step.tool}")
    if step.file_path:
        parts.append(f"File: {step.file_path}")
    parts.append(f"Instruction: {step.instruction}")

    if step.context:
        parts.append(
            "\nContext (file content from planner investigation):"
            f"\n```\n{step.context}\n```"
        )

    # Explicit directive
    if step.tool in ("run_tests", "run_lint", "format_code"):
        parts.append(
            f"\nCall {step.tool} with the command specified in the instruction."
        )
    elif step.tool == "edit_file":
        parts.append(
            f"\nRead {step.file_path} first if the context above seems "
            "incomplete, then call edit_file with accurate search/replace blocks."
        )
    elif step.tool == "create_file":
        parts.append(
            f"\nCall create_file to create {step.file_path} with the content "
            "described in the instruction. Produce complete, working code."
        )

    return "\n".join(parts)


# ── Tool Executor ──────────────────────────────────────────────────


def _make_tool_executor(repo_root: str, ws: WebSocket, session_id: str = ""):
    """Create a tool executor closure for the workflow."""

    async def execute(name: str, arguments: dict) -> str:
        """Execute a tool and return the result as a string."""

        if name == "create_file":
            result = await file_ops.create_file(
                path=arguments["path"],
                content=arguments["content"],
                repo_root=repo_root,
            )
            diff = result.metadata.get("diff", "")
            if diff:
                await ws_send(ws, "diff", {"file": arguments["path"], "diff": diff})
            return result.output if result.success else f"ERROR: {result.error}"

        elif name == "edit_file":
            result = await file_ops.edit_file(
                path=arguments["path"],
                search=arguments["search"],
                replace=arguments["replace"],
                repo_root=repo_root,
            )
            diff = result.metadata.get("diff", "")
            if diff:
                await ws_send(ws, "diff", {"file": arguments["path"], "diff": diff})
            return result.output if result.success else f"ERROR: {result.error}"

        elif name == "read_file":
            result = await file_ops.read_file(
                path=arguments["path"],
                repo_root=repo_root,
                start_line=arguments.get("start_line"),
                end_line=arguments.get("end_line"),
            )
            return result.output if result.success else f"ERROR: {result.error}"

        elif name in ("run_tests", "run_lint", "format_code"):
            command = arguments["command"]
            risk, reason = check_command(command)
            if risk == CommandRisk.ALWAYS_BLOCK:
                return f"ERROR: Command blocked: {reason}"
            if risk == CommandRisk.REQUIRES_APPROVAL:
                await ws_send(ws, "tool_approval_required", {
                    "tool": name, "command": command, "reason": reason,
                })
                approval_msg = await safe_receive(ws)
                if approval_msg is None:
                    return "ERROR: WebSocket disconnected — command skipped (requires approval)"
                if approval_msg.get("type") != "approve_tool":
                    return "ERROR: Command not approved by user"

            handler = {
                "run_tests": shell.run_tests,
                "run_lint": shell.run_lint,
                "format_code": shell.format_code,
            }[name]
            result = await handler(command=command, repo_root=repo_root)
            if name == "run_tests":
                await ws_send(ws, "test_result", {
                    "command": command,
                    "passed": result.success,
                    "output": result.output[:2000],
                })
            if result.success:
                output = result.output or ""
            else:
                prefix = (
                    f"FAILED (exit code {result.exit_code})\n"
                    if result.exit_code else "FAILED\n"
                )
                output = prefix + (
                    result.output or result.error or "No output"
                )
            max_output = 8000
            if len(output) > max_output:
                output = (
                    output[:max_output]
                    + f"\n\n[OUTPUT TRUNCATED — showing first"
                    f" {max_output} of {len(output)} characters]"
                )
            return output

        elif name == "list_directory":
            target = Path(repo_root) / arguments.get("path", "")
            if not target.is_dir():
                return f"ERROR: Not a directory: {arguments.get('path', '')}"
            max_entries = arguments.get("max_entries", 100)
            all_entries = sorted(target.iterdir())
            total = len(all_entries)
            entries = all_entries[:max_entries]
            lines = []
            for e in entries:
                prefix = "d" if e.is_dir() else "f"
                lines.append(f"  {prefix}  {e.name}")
            output = "\n".join(lines) or "(empty)"
            if total > max_entries:
                output += (
                    f"\n\n[TRUNCATED — showing {max_entries} of {total}"
                    f" entries. Use max_entries parameter to see more.]"
                )
            return output

        elif name == "update_scratchpad":
            result = await scratchpad.update_scratchpad(
                content=arguments["content"],
                repo_root=repo_root,
                session_id=session_id,
            )
            return result.output if result.success else f"ERROR: {result.error}"

        elif name == "directory_tree":
            from lean_ai.indexer.tree import list_repo_tree
            sub_path = arguments.get("path", "")
            tree_root = f"{repo_root}/{sub_path}" if sub_path else repo_root
            entries = list_repo_tree(tree_root)
            total = len(entries)
            max_entries = 200
            max_depth = arguments.get("max_depth", 3)
            lines = []
            for e in entries[:max_entries]:
                depth = e.path.count("/")
                if depth <= max_depth:
                    indent = "  " * depth
                    lines.append(f"{indent}{e.path.split('/')[-1]}")
            output = "\n".join(lines) or "(empty)"
            if total > max_entries:
                output += (
                    f"\n\n[TRUNCATED — showing {max_entries} of"
                    f" {total} entries. Use path parameter to"
                    f" focus on a subtree, or increase max_depth.]"
                )
            return output

        elif name == "task_complete":
            return "Task marked complete."

        return f"ERROR: Unknown tool: {name}"

    return execute

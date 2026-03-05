"""Direct agentic workflow: give the LLM the task and tools, let it work.

No separate planning phase. The model explores the codebase, plans
naturally via chain-of-thought, and executes — all in one continuous
conversation with full context.
"""

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import WebSocket

from lean_ai.config import settings
from lean_ai.llm.prompts import IMPLEMENTATION_SYSTEM_PROMPT
from lean_ai.llm.tool_definitions import IMPLEMENTATION_TOOLS
from lean_ai.tools import file_ops, shell
from lean_ai.tools.command_safety import CommandRisk, check_command
from lean_ai.workflow.ws_handler import safe_receive, ws_send

if TYPE_CHECKING:
    from lean_ai.llm.client import LLMClient

logger = logging.getLogger(__name__)


async def run_workflow(
    task: str,
    repo_root: str,
    ws: WebSocket,
    llm_client: "LLMClient",
    context: str = "",
    branch_name: str = "",
    conversation_logger: Callable | None = None,
) -> str:
    """Run the agentic workflow: task + tools → let the model work.

    Single conversation, single context. The model explores, plans,
    and executes in one continuous tool-calling loop.

    Returns a structured commit message summarising the actions taken.
    """
    await ws_send(ws, "stage_change", {"stage": "implementing"})
    logger.info("Workflow: starting task: %s", task[:100])

    # Build system prompt with codebase context baked in
    system_prompt = _build_system_prompt(context)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]

    # Log the initial task
    if conversation_logger:
        await conversation_logger("user", task)

    # Create tool executor
    tool_executor = _make_tool_executor(repo_root, ws)

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

    # Let the LLM work — single conversation, all tools available
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

    # Done
    files_modified = list({
        tc.parameters.get("path", "")
        for tc in executed
        if tc.tool_name in ("create_file", "edit_file") and tc.parameters.get("path")
    })

    summary = (
        f"Completed {len(executed)} tool calls. "
        f"Files modified: {', '.join(files_modified) if files_modified else 'none'}."
    )

    complete_data: dict = {"summary": summary, "files_modified": files_modified}
    if branch_name:
        complete_data["plan_branch"] = branch_name
    await ws_send(ws, "complete", complete_data)
    logger.info("Workflow complete: %d tool calls, %d files", len(executed), len(files_modified))

    # Build commit message: short subject + LLM's summary as body
    task_summary = task[:72].replace("\n", " ")
    commit_msg = f"lean-ai: {task_summary}"
    if explanation:
        commit_msg += f"\n\n{explanation}"
    if files_modified:
        commit_msg += f"\n\nFiles modified: {', '.join(files_modified)}"
    return commit_msg


def _build_system_prompt(context: str) -> str:
    """Build the system prompt with optional codebase context."""
    if not context:
        return IMPLEMENTATION_SYSTEM_PROMPT

    return (
        f"{IMPLEMENTATION_SYSTEM_PROMPT}\n"
        f"## Project Context\n\n{context}"
    )


def _make_tool_executor(repo_root: str, ws: WebSocket):
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
                # Wait for approval (safe_receive returns None on disconnect)
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
            # Return output to the LLM, capped to avoid flooding context.
            # Prefix with pass/fail so the model knows the result clearly.
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

        return f"ERROR: Unknown tool: {name}"

    return execute

"""Linear workflow pipeline: plan -> approve -> execute -> done.

No FSM. No stagnation detection. No implementation review.
Give the LLM the plan and tools, let it work.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import WebSocket

from lean_ai.config import settings
from lean_ai.llm.planner import create_plan
from lean_ai.llm.prompts import IMPLEMENTATION_SYSTEM_PROMPT
from lean_ai.llm.tool_definitions import IMPLEMENTATION_TOOLS
from lean_ai.tools import file_ops, shell
from lean_ai.tools.command_safety import CommandRisk, check_command

if TYPE_CHECKING:
    from lean_ai.llm.client import LLMClient

logger = logging.getLogger(__name__)


async def ws_send(ws: WebSocket, msg_type: str, data: dict | None = None) -> None:
    """Send a typed WebSocket message."""
    payload = {"type": msg_type, **(data or {})}
    await ws.send_json(payload)


async def run_workflow(
    task: str,
    repo_root: str,
    ws: WebSocket,
    llm_client: "LLMClient",
    context: str = "",
) -> None:
    """Run the full workflow: plan -> approve -> execute -> done.

    This is the entire pipeline in one function. No FSM needed.
    """
    # ── Phase 1: Planning ──
    await ws_send(ws, "stage_change", {"stage": "planning"})
    logger.info("Workflow: planning for task: %s", task[:100])

    plan = await create_plan(task, repo_root, llm_client, context=context)

    # ── Phase 2: User Approval ──
    await ws_send(ws, "approval_required", {"plan": plan})

    # Wait for user response
    while True:
        msg = await ws.receive_json()
        msg_type = msg.get("type", "")

        if msg_type == "approve":
            break
        elif msg_type == "deny" or msg_type == "reject":
            await ws_send(ws, "complete", {"summary": "Plan rejected by user."})
            return
        elif msg_type == "revise":
            feedback = msg.get("feedback", "")
            await ws_send(ws, "stage_change", {"stage": "planning"})
            revision_context = f"PREVIOUS PLAN:\n{plan}\n\nUSER FEEDBACK:\n{feedback}"
            plan = await create_plan(
                task, repo_root, llm_client,
                context=context, revision_context=revision_context,
            )
            await ws_send(ws, "approval_required", {"plan": plan})
        elif msg_type == "user_message":
            # Treat messages during approval as revision requests
            feedback = msg.get("content", msg.get("text", ""))
            if feedback:
                await ws_send(ws, "stage_change", {"stage": "planning"})
                revision_context = f"PREVIOUS PLAN:\n{plan}\n\nUSER FEEDBACK:\n{feedback}"
                plan = await create_plan(
                    task, repo_root, llm_client,
                    context=context, revision_context=revision_context,
                )
                await ws_send(ws, "approval_required", {"plan": plan})

    # ── Phase 3: Implementation ──
    await ws_send(ws, "stage_change", {"stage": "implementing"})
    logger.info("Workflow: implementing plan")

    # Pre-read files referenced in the plan
    file_contents = await _pre_read_plan_files(plan, repo_root)

    # Build the implementation prompt
    user_prompt = _build_implementation_prompt(task, plan, file_contents)

    messages = [
        {"role": "system", "content": IMPLEMENTATION_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    # Create tool executor
    tool_executor = _make_tool_executor(repo_root, ws)

    # Callbacks for WebSocket progress
    async def on_tool_call(name: str, args: dict) -> None:
        await ws_send(ws, "tool_progress", {
            "tool": name,
            "status": "running",
            "description": f"{name} {args.get('path', args.get('command', ''))}",
        })

    async def on_tool_result(name: str, result: str) -> None:
        is_error = result.startswith("ERROR:")
        await ws_send(ws, "tool_progress", {
            "tool": name,
            "status": "error" if is_error else "complete",
            "output": result[:500],
        })

    # Let the LLM loose with tools
    executed, explanation = await llm_client.chat_with_tools(
        messages=messages,
        tools=IMPLEMENTATION_TOOLS,
        tool_executor_fn=tool_executor,
        max_turns=settings.implementation_max_turns,
        max_tokens=settings.implementation_max_tokens,
        on_tool_call=on_tool_call,
        on_tool_result=on_tool_result,
    )

    # ── Phase 4: Done ──
    files_modified = list({
        tc.parameters.get("path", "")
        for tc in executed
        if tc.tool_name in ("create_file", "edit_file") and tc.parameters.get("path")
    })

    summary = (
        f"Completed {len(executed)} tool calls. "
        f"Files modified: {', '.join(files_modified) if files_modified else 'none'}."
    )
    if explanation:
        summary += f"\n\n{explanation}"

    await ws_send(ws, "complete", {"summary": summary, "files_modified": files_modified})
    logger.info("Workflow complete: %d tool calls, %d files", len(executed), len(files_modified))


async def _pre_read_plan_files(plan: str, repo_root: str, max_files: int = 8) -> str:
    """Read existing files referenced in the plan for context."""
    # Simple heuristic: find file paths in the plan text
    # Look for lines that contain paths with extensions
    paths_found: list[str] = []
    for line in plan.splitlines():
        for word in line.split():
            # Strip markdown formatting
            cleaned = word.strip("`*_-[]()#>")
            if "/" in cleaned and "." in cleaned.split("/")[-1]:
                # Looks like a file path
                candidate = cleaned
                # Verify it exists
                full_path = Path(repo_root) / candidate
                if full_path.is_file() and candidate not in paths_found:
                    paths_found.append(candidate)

    if not paths_found:
        return ""

    contents: list[str] = []
    for path in paths_found[:max_files]:
        try:
            text = (Path(repo_root) / path).read_text(encoding="utf-8")
            # Truncate very large files
            if len(text) > 5000:
                text = text[:5000] + "\n[... truncated ...]"
            contents.append(f"=== {path} ===\n{text}")
        except (OSError, UnicodeDecodeError):
            pass

    return "\n\n".join(contents)


def _build_implementation_prompt(
    task: str, plan: str, file_contents: str,
) -> str:
    """Build the user message for the implementation phase."""
    parts = [f"TASK: {task}", f"\nPLAN:\n{plan}"]
    if file_contents:
        parts.append(f"\nEXISTING FILE CONTENTS:\n{file_contents}")
    parts.append(
        "\nImplement this plan now. Work through the steps, reading files "
        "before editing them. Run tests when appropriate."
    )
    return "\n".join(parts)


def _make_tool_executor(repo_root: str, ws: WebSocket):
    """Create a tool executor closure for the implementation phase."""

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
                # Wait for approval
                approval_msg = await ws.receive_json()
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
            return result.output if result.success else f"ERROR: {result.error}"

        elif name == "list_directory":
            target = Path(repo_root) / arguments.get("path", "")
            if not target.is_dir():
                return f"ERROR: Not a directory: {arguments.get('path', '')}"
            max_entries = arguments.get("max_entries", 100)
            entries = sorted(target.iterdir())[:max_entries]
            lines = []
            for e in entries:
                prefix = "d" if e.is_dir() else "f"
                lines.append(f"  {prefix}  {e.name}")
            return "\n".join(lines) or "(empty)"

        elif name == "directory_tree":
            from lean_ai.indexer.tree import list_repo_tree
            sub_path = arguments.get("path", "")
            tree_root = f"{repo_root}/{sub_path}" if sub_path else repo_root
            entries = list_repo_tree(tree_root)
            max_depth = arguments.get("max_depth", 3)
            lines = []
            for e in entries[:200]:
                depth = e.path.count("/")
                if depth <= max_depth:
                    indent = "  " * depth
                    lines.append(f"{indent}{e.path.split('/')[-1]}")
            return "\n".join(lines) or "(empty)"

        return f"ERROR: Unknown tool: {name}"

    return execute

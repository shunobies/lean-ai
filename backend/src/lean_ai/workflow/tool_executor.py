"""Tool executor factory for workflow execution."""

import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import WebSocket

from lean_ai.tools import file_ops, scratchpad, shell
from lean_ai.tools.command_safety import CommandRisk, check_command
from lean_ai.workflow.ws_handler import ws_send

if TYPE_CHECKING:
    from lean_ai.llm.facade import LLMClient
    from lean_ai.workflow.ws_dispatcher import WSMessageDispatcher

# Short output is returned inline; longer output is saved to a file
# so the LLM can page through it with read_file.
_INLINE_LIMIT = 2000  # chars — fits comfortably in a single tool result


_TOOL_OUTPUT_DIR = ".lean_ai/tool_output"
_MAX_AGE_SECONDS = 3600  # auto-delete files older than 1 hour


def _save_tool_output(
    repo_root: str,
    tool_name: str,
    output: str,
) -> str:
    """Save full tool output to .lean_ai/tool_output/ and return the relative path.

    Automatically cleans up files older than ``_MAX_AGE_SECONDS``.
    """
    out_dir = Path(repo_root) / _TOOL_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    # Auto-cleanup: remove stale output files
    _cleanup_tool_output(out_dir)

    timestamp = int(time.time() * 1000)
    filename = f"{tool_name}_{timestamp}.txt"
    out_path = out_dir / filename
    out_path.write_text(output, encoding="utf-8")
    return f"{_TOOL_OUTPUT_DIR}/{filename}"


def _cleanup_tool_output(out_dir: Path, max_age: float = _MAX_AGE_SECONDS) -> int:
    """Delete tool output files older than *max_age* seconds.  Returns count deleted."""
    if not out_dir.is_dir():
        return 0
    now = time.time()
    deleted = 0
    for f in out_dir.iterdir():
        if f.is_file() and f.suffix == ".txt":
            try:
                if now - f.stat().st_mtime > max_age:
                    f.unlink()
                    deleted += 1
            except OSError:
                pass
    return deleted


def cleanup_all_tool_output(repo_root: str) -> int:
    """Delete ALL tool output files.  Called during /init workspace reset."""
    out_dir = Path(repo_root) / _TOOL_OUTPUT_DIR
    if not out_dir.is_dir():
        return 0
    deleted = 0
    for f in out_dir.iterdir():
        if f.is_file():
            try:
                f.unlink()
                deleted += 1
            except OSError:
                pass
    return deleted


def make_tool_executor(
    repo_root: str,
    ws: WebSocket,
    session_id: str = "",
    llm_client: "LLMClient | None" = None,
    dispatcher: "WSMessageDispatcher | None" = None,
    tdd_protect_tests: bool = False,
    on_test_dispute: "Callable | None" = None,
):
    """Create a tool executor closure for the workflow.

    Args:
        tdd_protect_tests: When True, block ``create_file``/``edit_file``
            calls targeting test files.  The LLM should use
            ``request_test_change`` instead.
        on_test_dispute: Async callback invoked when ``request_test_change``
            is called.  Receives the tool arguments dict and returns a
            string result for the primary model.
    """

    async def execute(name: str, arguments: dict) -> str:
        """Execute a tool and return the result as a string."""

        # TDD guard: block writes to test files during implementation
        if tdd_protect_tests and name in ("create_file", "edit_file"):
            from lean_ai.tools.test_file_utils import is_test_file_path
            target_path = arguments.get("path", "")
            if is_test_file_path(target_path):
                return (
                    "ERROR: TDD mode — you cannot modify test files during "
                    "the implementation phase. If you believe a test is "
                    "flawed, use the request_test_change tool to dispute it "
                    "with a specific programmatic reason."
                )

        # Handle test dispute tool
        if name == "request_test_change":
            if on_test_dispute is not None:
                return await on_test_dispute(arguments)
            return (
                "ERROR: request_test_change is not available in this "
                "context."
            )

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
                if dispatcher:
                    from lean_ai.workflow.ws_dispatcher import WorkflowCancelledError
                    try:
                        approval_msg = await dispatcher.wait_for_approval()
                    except WorkflowCancelledError:
                        return "ERROR: Workflow cancelled by user"
                else:
                    from lean_ai.workflow.ws_handler import safe_receive
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

            # Short output → return inline.
            # Long output → save to file so the LLM can page through
            # with read_file (500 lines at a time) and never miss
            # failures buried in the middle.
            if len(output) <= _INLINE_LIMIT:
                return output

            total_lines = output.count("\n") + 1
            out_path = _save_tool_output(repo_root, name, output)

            # Return a concise summary + the last 40 lines (summary/failures)
            # and tell the LLM where to find the full output.
            tail_lines = output.splitlines()[-40:]
            tail_block = "\n".join(tail_lines)

            if result.success:
                return (
                    f"PASSED — full output saved to {out_path} "
                    f"({total_lines} lines)\n"
                    f"Last {len(tail_lines)} lines:\n\n"
                    f"{tail_block}"
                )
            else:
                return (
                    f"FAILED — full output saved to {out_path} "
                    f"({total_lines} lines)\n"
                    f"REQUIRED: You MUST call read_file on '{out_path}' before "
                    f"diagnosing or fixing this failure. "
                    f"The tail preview below may omit stack traces and assertion "
                    f"details that appear earlier in the output.\n"
                    f"Last {len(tail_lines)} lines (preview only):\n\n"
                    f"{tail_block}"
                )

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

        elif name == "grep_files":
            result = await file_ops.grep_files(
                pattern=arguments.get("pattern", ""),
                repo_root=repo_root,
                file_glob=arguments.get("file_glob"),
            )
            return result.output if result.success else f"ERROR: {result.error}"

        elif name == "search_internet":
            from lean_ai.tools.internet import search_internet
            result = await search_internet(
                query=arguments.get("query", ""),
                llm_client=llm_client,
            )
            return result.output if result.success else f"ERROR: {result.error}"

        elif name == "fetch_url":
            from lean_ai.tools.internet import fetch_url
            result = await fetch_url(
                url=arguments.get("url", ""),
                repo_root=repo_root,
                llm_client=llm_client,
            )
            return result.output if result.success else f"ERROR: {result.error}"

        elif name == "task_complete":
            return "Task marked complete."

        return f"ERROR: Unknown tool: {name}"

    return execute

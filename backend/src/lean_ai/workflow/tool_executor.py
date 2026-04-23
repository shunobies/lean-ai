"""Tool executor factory for workflow execution."""

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import WebSocket

from lean_ai.config import settings
from lean_ai.tools import file_ops, scratchpad, shell
from lean_ai.tools.command_safety import CommandRisk, check_command
from lean_ai.workflow.ws_messages import (
    send_diff,
    send_test_result,
    send_tool_approval_required,
)

if TYPE_CHECKING:
    from lean_ai.llm.facade import LLMClient
    from lean_ai.workflow.ws_dispatcher import WSMessageDispatcher

logger = logging.getLogger(__name__)

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


def _is_external_path(path: str, repo_root: str) -> bool:
    """Return True if *path* resolves outside the repository root."""
    resolved = (Path(repo_root) / path).resolve()
    return not resolved.is_relative_to(Path(repo_root).resolve())


async def _request_tool_approval(
    ws: WebSocket,
    dispatcher: "WSMessageDispatcher | None",
    tool: str,
    command: str,
    reason: str,
) -> bool:
    """Send ``tool_approval_required`` and wait for user response.

    Returns ``True`` if the user approves, ``False`` otherwise (denied,
    disconnected, or cancelled).
    """
    await send_tool_approval_required(
        ws, tool=tool, command=command, reason=reason,
    )
    if dispatcher:
        from lean_ai.workflow.ws_dispatcher import WorkflowCancelledError
        # Loop to skip unexpected message types (e.g. stale user_message)
        while True:
            try:
                approval_msg = await dispatcher.wait_for_approval()
            except WorkflowCancelledError:
                return False
            if approval_msg is None:
                return False
            msg_type = approval_msg.get("type")
            if msg_type == "approve_tool":
                return True
            if msg_type == "deny_tool":
                return False
            logger.debug(
                "Skipping unexpected '%s' message during tool approval",
                msg_type,
            )
    else:
        from lean_ai.workflow.ws_handler import safe_receive
        approval_msg = await safe_receive(ws)
        if approval_msg is None:
            return False
        return approval_msg.get("type") == "approve_tool"


# Tools eligible for worker-model compression
_COMPRESSIBLE_TOOLS = frozenset({
    "read_file", "grep_files", "run_tests", "run_lint",
    "run_command", "search_internet", "fetch_url",
})

_COMPRESS_PROMPTS: dict[str, str] = {
    "read_file": (
        "Summarize this source file preserving: all function/class "
        "signatures with parameters, imports, key constants, docstrings. "
        "Omit function bodies."
    ),
    "grep_files": (
        "Condense these search results: keep file paths and matched line "
        "content. Remove duplicate patterns. Group by file."
    ),
    "run_command": (
        "Extract: exit code, error messages, failing test names, key "
        "output lines. Remove verbose stack traces and passing test details."
    ),
    "run_tests": (
        "Extract: exit code, error messages, failing test names, key "
        "output lines. Remove verbose stack traces and passing test details."
    ),
}


async def _compress_tool_output(
    output: str,
    tool_name: str,
    worker_client: "LLMClient",
    *,
    telemetry_context: dict | None = None,
) -> str:
    """Compress large tool output using the worker model.

    Trigger threshold: 5% of context window in chars.  When the output
    exceeds this, the worker model produces a concise summary.

    When *telemetry_context* is set the (raw, compressed) pair is
    captured to ``tool_compressions`` so fine-tuning a smaller worker
    on this distillation signal is possible.
    """
    threshold = int(settings._active_context_window * 0.05 * 3.5)
    if len(output) <= threshold:
        return output

    prompt = _COMPRESS_PROMPTS.get(tool_name, (
        "Summarize this tool output concisely. Preserve key facts, "
        "file paths, error messages, and actionable details."
    ))

    try:
        compressed = await worker_client.chat_raw(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": output[:threshold * 3]},
            ],
            max_tokens=1024,
        )
        if compressed and len(compressed.strip()) > 50:
            stripped = compressed.strip()
            logger.info(
                "Compressed %s output: %d -> %d chars (%.0f%%)",
                tool_name, len(output), len(stripped),
                len(stripped) / len(output) * 100,
            )
            _fire_compression_capture(
                telemetry_context,
                tool_name=tool_name,
                raw_output=output,
                compressed_output=stripped,
                worker_client=worker_client,
            )
            return stripped
    except Exception:
        logger.warning(
            "Worker model compression failed for %s, using truncation",
            tool_name, exc_info=True,
        )

    # Fallback: truncate
    return output[:threshold] + "\n... (truncated)"


def _fire_compression_capture(
    telemetry_context: dict | None,
    *,
    tool_name: str,
    raw_output: str,
    compressed_output: str,
    worker_client: "LLMClient | None",
) -> None:
    """Fire-and-forget capture of a worker compression pair."""
    if telemetry_context is None or worker_client is None:
        return
    repo_root = telemetry_context.get("repo_root")
    session_id = telemetry_context.get("session_id")
    if not repo_root or not session_id:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _run():
        try:
            from lean_ai.training.capture import capture_tool_compression

            await capture_tool_compression(
                repo_root,
                session_id=session_id,
                phase=telemetry_context.get("phase"),
                tool_name=tool_name,
                raw_output=raw_output,
                compressed_output=compressed_output,
                worker_model=getattr(worker_client, "model_name", None),
                worker_provider=getattr(worker_client, "provider_name", None),
            )
        except Exception:
            logger.debug(
                "capture_tool_compression failed (non-fatal)", exc_info=True,
            )

    t = loop.create_task(_run())
    t.add_done_callback(lambda tk: tk.exception() if tk.done() else None)


def _fire_tool_execution_capture(
    telemetry_context: dict | None,
    *,
    tool_name: str,
    arguments: dict,
    result: str,
    success: bool,
    latency_ms: int,
) -> None:
    """Fire-and-forget capture of one tool invocation.

    Writes to ``tool_executions`` so fine-tuners can build DPO pairs
    from (failed call, successful call) on the same session/tool. When
    telemetry_context is absent, capture is skipped — unchanged
    behaviour for callers that haven't opted in.
    """
    if telemetry_context is None:
        return
    repo_root = telemetry_context.get("repo_root")
    session_id = telemetry_context.get("session_id")
    if not repo_root or not session_id:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _run():
        try:
            from lean_ai.training.capture import capture_tool_execution

            await capture_tool_execution(
                repo_root,
                session_id=session_id,
                tool_name=tool_name,
                arguments=arguments,
                result=result,
                success=success,
                latency_ms=latency_ms,
                phase=telemetry_context.get("phase"),
                trace_uuid=telemetry_context.get("last_trace_uuid"),
            )
        except Exception:
            logger.debug(
                "capture_tool_execution failed (non-fatal)", exc_info=True,
            )

    t = loop.create_task(_run())
    t.add_done_callback(lambda tk: tk.exception() if tk.done() else None)


# Required parameters per tool — used for early validation
_REQUIRED_PARAMS: dict[str, list[str]] = {
    "create_file": ["path", "content"],
    "edit_file": ["path", "search", "replace"],
    "read_file": ["path"],
    "run_tests": ["command"],
    "run_lint": ["command"],
    "format_code": ["command"],
    "run_command": ["command"],
    "update_scratchpad": ["content"],
    "add_journal_entry": ["content"],
    "grep_files": ["pattern"],
    "search_internet": ["query"],
    "fetch_url": ["url"],
    "search_wiki": ["query"],
    "fetch_wiki_page": ["title"],
    "search_reference": ["query"],
    "verify_web_ui": ["url", "question"],
    "verify_desktop_ui": ["launch_command", "window_title", "question"],
}

# Parameters where empty string is valid
_ALLOW_EMPTY: frozenset[tuple[str, str]] = frozenset({
    ("create_file", "content"),   # empty file creation is valid
    ("edit_file", "replace"),     # replacing with empty = deletion
})


def _validate_required_params(name: str, arguments: dict) -> str | None:
    """Return an error message if required parameters are missing or empty."""
    required = _REQUIRED_PARAMS.get(name)
    if not required:
        return None
    problems: list[str] = []
    for p in required:
        if p not in arguments:
            problems.append(p)
        elif (
            isinstance(arguments[p], str)
            and not arguments[p].strip()
            and (name, p) not in _ALLOW_EMPTY
        ):
            problems.append(f"{p} (was empty)")
    if problems:
        return (
            f"ERROR: {name} requires parameter(s): {', '.join(problems)}. "
            f"Provide all required parameters and retry."
        )
    return None


_REFERENCE_FOOTER_MAX_DOCS = 20


def _format_reference_doc_listing(docs: list[dict], query: str) -> str:
    """Render a hint listing available reference documents + an example call.

    Used when the LLM calls ``search_reference`` without a ``document``
    filter so it can narrow follow-up searches without needing a
    separate ``list_reference_documents`` round-trip.
    """
    shown = docs[:_REFERENCE_FOOTER_MAX_DOCS]
    lines = [
        f"Reference library has {len(docs)} document(s). "
        "Pass `document` to restrict a follow-up search to one source:"
    ]
    for d in shown:
        lines.append(f"  - {d['doc_title']}  [path={d['doc_path']}]")
    if len(docs) > _REFERENCE_FOOTER_MAX_DOCS:
        lines.append(
            f"  ... {len(docs) - _REFERENCE_FOOTER_MAX_DOCS} more — "
            "call list_reference_documents to see all."
        )
    example_path = shown[0]["doc_path"] if shown else "path/from/list_reference_documents"
    example_query = query.strip() or "your query"
    lines.append(
        f'Example: search_reference(query="{example_query}", '
        f'document="{example_path}")'
    )
    return "\n".join(lines)


def make_tool_executor(
    repo_root: str,
    ws: WebSocket,
    session_id: str = "",
    llm_client: "LLMClient | None" = None,
    worker_client: "LLMClient | None" = None,
    dispatcher: "WSMessageDispatcher | None" = None,
    tdd_protect_tests: bool = False,
    on_test_dispute: "Callable | None" = None,
    allowed_files: "list[str] | None" = None,
    session_created_regression_files: "set[str] | None" = None,
    telemetry_context: dict | None = None,
):
    """Create a tool executor closure for the workflow.

    Args:
        worker_client: Optional small model for compressing large tool outputs.
            When provided and output exceeds 5% of the context window, the
            worker model summarizes it before returning to the primary model.
        tdd_protect_tests: When True, block ``create_file``/``edit_file``
            calls targeting test files.  The LLM should use
            ``request_test_change`` instead.
        on_test_dispute: Async callback invoked when ``request_test_change``
            is called.  Receives the tool arguments dict and returns a
            string result for the primary model.
        allowed_files: When set, restrict ``edit_file`` to only these paths.
            ``create_file`` is allowed for genuinely new files but blocked
            if it would overwrite an existing file not on the list.
        session_created_regression_files: When provided, the executor
            treats any regression-test-path edit as allowed if the path
            is in this set (i.e. the current plan is the one that
            created the regression file). Finalized regression files
            (not in the set) are unconditionally protected.  A shared
            ``set`` instance lets the caller inspect which regression
            paths were created during this session.  When ``None``,
            every edit to a regression path is rejected.
    """

    # Default to an empty local set so the closure works without a
    # caller-provided set. Shared reference means caller can inspect.
    if session_created_regression_files is None:
        session_created_regression_files = set()

    async def execute(name: str, arguments: dict) -> str:
        """Execute a tool and return the result as a string."""
        started = time.monotonic()
        try:
            result = await _execute_inner(name, arguments)
        except Exception as exc:
            logger.exception("Unhandled exception in tool %s", name)
            result = (
                f"ERROR: {name} failed: {exc}. "
                f"You may retry or try a different approach."
            )
        latency_ms = int((time.monotonic() - started) * 1000)
        success = not (
            isinstance(result, str) and result.lstrip().upper().startswith(
                ("ERROR:", "FAILED"),
            )
        )
        _fire_tool_execution_capture(
            telemetry_context,
            tool_name=name,
            arguments=arguments,
            result=result if isinstance(result, str) else str(result),
            success=success,
            latency_ms=latency_ms,
        )
        return result

    async def _execute_inner(name: str, arguments: dict) -> str:
        """Inner dispatch — all tool logic lives here."""

        # Validate required parameters upfront
        param_error = _validate_required_params(name, arguments)
        if param_error:
            return param_error

        # File whitelist guard (validation fix loop scoping)
        if allowed_files is not None and name in ("create_file", "edit_file"):
            target_path = arguments.get("path", "")
            if target_path and target_path not in allowed_files:
                full_target = Path(repo_root) / target_path
                if name == "edit_file" or full_target.exists():
                    return (
                        f"ERROR: File '{target_path}' is not in the allowed "
                        f"modification list for this fix attempt. Only modify "
                        f"files from the original plan."
                    )

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

        # Regression guard: regression tests are immutable once
        # finalized. An edit is only permitted when the file was
        # created by the CURRENT session (i.e. Phase 5 within this
        # plan wrote the test and is now refining it).
        if name == "edit_file":
            from lean_ai.tools.regression_guard import (
                REGRESSION_GUARD_ERROR,
                is_regression_test_path,
            )
            target_path = arguments.get("path", "")
            if (
                is_regression_test_path(target_path)
                and target_path not in session_created_regression_files
            ):
                return REGRESSION_GUARD_ERROR.format(path=target_path)

        # Handle test dispute tool
        if name == "request_test_change":
            if on_test_dispute is not None:
                return await on_test_dispute(arguments)
            return (
                "ERROR: request_test_change is not available in this "
                "context."
            )

        if name == "create_file":
            target_path = arguments["path"]
            external = _is_external_path(target_path, repo_root)
            if external:
                approved = await _request_tool_approval(
                    ws, dispatcher, "create_file", target_path,
                    "File is outside the project directory",
                )
                if not approved:
                    return "ERROR: Access to external file not approved by user"
            result = await file_ops.create_file(
                path=target_path,
                content=arguments["content"],
                repo_root=repo_root,
                allow_external=external,
            )
            diff = result.metadata.get("diff", "")
            if diff:
                await send_diff(ws, file=target_path, diff=diff)
            # Track regression files created this session so subsequent
            # edit_file calls within the same plan can refine them.
            # The regression guard promotes them to "finalized" (read-
            # only) once the plan completes by discarding this set.
            if result.success:
                from lean_ai.tools.regression_guard import is_regression_test_path
                if is_regression_test_path(target_path):
                    session_created_regression_files.add(target_path)
            return result.output if result.success else f"ERROR: {result.error}"

        elif name == "edit_file":
            target_path = arguments["path"]
            external = _is_external_path(target_path, repo_root)
            if external:
                approved = await _request_tool_approval(
                    ws, dispatcher, "edit_file", target_path,
                    "File is outside the project directory",
                )
                if not approved:
                    return "ERROR: Access to external file not approved by user"
            result = await file_ops.edit_file(
                path=target_path,
                search=arguments["search"],
                replace=arguments["replace"],
                repo_root=repo_root,
                allow_external=external,
            )
            diff = result.metadata.get("diff", "")
            if diff:
                await send_diff(ws, file=target_path, diff=diff)
            return result.output if result.success else f"ERROR: {result.error}"

        elif name == "read_file":
            target_path = arguments["path"]
            external = _is_external_path(target_path, repo_root)
            if external:
                approved = await _request_tool_approval(
                    ws, dispatcher, "read_file", target_path,
                    "File is outside the project directory",
                )
                if not approved:
                    return "ERROR: Access to external file not approved by user"
            result = await file_ops.read_file(
                path=target_path,
                repo_root=repo_root,
                start_line=arguments.get("start_line"),
                end_line=arguments.get("end_line"),
                allow_external=external,
            )
            return result.output if result.success else f"ERROR: {result.error}"

        elif name in ("run_tests", "run_lint", "format_code"):
            command = arguments["command"]
            risk, reason = check_command(command)
            if risk == CommandRisk.ALWAYS_BLOCK:
                return f"ERROR: Command blocked: {reason}"
            if risk == CommandRisk.REQUIRES_APPROVAL:
                if not await _request_tool_approval(
                    ws, dispatcher, name, command, reason,
                ):
                    return "ERROR: Command not approved by user"

            handler = {
                "run_tests": shell.run_tests,
                "run_lint": shell.run_lint,
                "format_code": shell.format_code,
            }[name]
            result = await handler(command=command, repo_root=repo_root)
            if name == "run_tests":
                await send_test_result(
                    ws,
                    passed=result.success,
                    output=result.output[:2000],
                    command=command,
                )
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

        elif name == "run_command":
            command = arguments["command"]
            risk, reason = check_command(command)
            if risk == CommandRisk.ALWAYS_BLOCK:
                return f"ERROR: Command blocked: {reason}"
            if risk == CommandRisk.REQUIRES_APPROVAL:
                if not await _request_tool_approval(
                    ws, dispatcher, name, command, reason,
                ):
                    return "ERROR: Command not approved by user"

            result = await shell.run_command(
                command=command,
                repo_root=repo_root,
                working_directory=arguments.get("working_directory", ""),
            )
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

            if len(output) <= _INLINE_LIMIT:
                return output

            total_lines = output.count("\n") + 1
            out_path = _save_tool_output(repo_root, name, output)
            tail_lines = output.splitlines()[-40:]
            tail_block = "\n".join(tail_lines)

            if result.success:
                return (
                    f"Command completed — full output saved to {out_path} "
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
                    f"The tail preview below may omit important details "
                    f"that appear earlier in the output.\n"
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

        elif name == "add_journal_entry":
            from lean_ai.tools.journal import add_journal_entry
            result = await add_journal_entry(
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
            query = arguments.get("query", "")

            # Pre-search memory check: return cached findings if available
            if settings.enable_session_memory and repo_root and query:
                try:
                    from lean_ai.memory.index import (
                        search_memories_with_threshold,
                    )

                    cached = search_memories_with_threshold(
                        repo_root, query,
                    )
                    if cached:
                        lines = [
                            "FROM WORKSPACE MEMORY "
                            "(previous session findings):\n",
                        ]
                        for r in cached:
                            cat = r.get("category") or "general"
                            lines.append(f"[{cat}] {r['content']}")
                            if r.get("source_task"):
                                task_snip = r["source_task"][:60]
                                lines.append(f"  (from task: {task_snip})")
                        lines.append(
                            "\nThese are cached findings from a "
                            "previous session. Call search_internet "
                            "again if you need the latest information."
                        )
                        return "\n".join(lines)
                except Exception:
                    logger.debug(
                        "Pre-search memory check failed", exc_info=True,
                    )

            from lean_ai.tools.internet import search_internet
            result = await search_internet(
                query=query,
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

        elif name == "search_wiki":
            from lean_ai.tools.wiki import search_wiki
            result = await search_wiki(query=arguments.get("query", ""))
            return result.output if result.success else f"ERROR: {result.error}"

        elif name == "fetch_wiki_page":
            from lean_ai.tools.wiki import fetch_wiki_page
            result = await fetch_wiki_page(
                title=arguments.get("title", ""),
                repo_root=repo_root,
            )
            return result.output if result.success else f"ERROR: {result.error}"

        elif name == "search_reference":
            from lean_ai.reference.indexer import is_reference_available
            from lean_ai.reference.indexer import list_documents as _list_documents
            from lean_ai.reference.indexer import search_reference as _search_reference

            if not is_reference_available(repo_root):
                return (
                    "ERROR: No reference library index found. "
                    "Place documents in .lean_ai/reference/ and run /init to index them."
                )

            from lean_ai.config import settings as _ref_settings
            query = arguments.get("query", "")
            limit = arguments.get("limit", _ref_settings.reference_search_default_limit)
            document = arguments.get("document") or None

            # Best-effort embedding for RRF re-ranking
            query_embedding: list[float] | None = None
            if llm_client is not None:
                try:
                    embeddings = await llm_client.embed([query])
                    if embeddings:
                        query_embedding = embeddings[0]
                except Exception:
                    pass

            chunks = await asyncio.to_thread(
                _search_reference,
                repo_root,
                query,
                limit,
                query_embedding,
                True,  # expand
                document,
            )

            # Unfiltered searches get a listing footer so the LLM can
            # narrow follow-up calls without a separate discovery round.
            # Skipped when the library has 0-1 documents (nothing to narrow to)
            # or when the caller already scoped the search.
            doc_listing = ""
            if document is None:
                all_docs = await asyncio.to_thread(_list_documents, repo_root, "")
                if len(all_docs) > 1:
                    doc_listing = _format_reference_doc_listing(all_docs, query)

            if not chunks:
                scope = f" in '{document}'" if document else ""
                base = f"No reference library results for '{query}'{scope}."
                if doc_listing:
                    return f"{base}\n\n{doc_listing}"
                return base

            parts = []
            for chunk in chunks:
                title = chunk.get("doc_title", "Unknown")
                content = chunk.get("content", "")
                # Merged passages (neighbor-expanded) carry a section range
                # + chunk range; point hits carry a single section.
                sections = chunk.get("sections")
                start_idx = chunk.get("chunk_index_start")
                end_idx = chunk.get("chunk_index_end")
                if sections:
                    if len(sections) == 1:
                        section_label = sections[0]
                    else:
                        section_label = f"{sections[0]} … {sections[-1]}"
                else:
                    section_label = chunk.get("section", "")

                header = f"[{title} > {section_label}]" if section_label else f"[{title}]"
                if start_idx is not None and end_idx is not None and start_idx != end_idx:
                    header = f"{header} (chunks {start_idx}-{end_idx})"
                parts.append(f"{header}\n{content}")
            body = "\n\n---\n\n".join(parts)
            if doc_listing:
                return f"{body}\n\n---\n\n{doc_listing}"
            return body

        elif name == "list_reference_documents":
            from lean_ai.reference.indexer import is_reference_available
            from lean_ai.reference.indexer import list_documents as _list_documents

            if not is_reference_available(repo_root):
                return (
                    "ERROR: No reference library index found. "
                    "Place documents in .lean_ai/reference/ and run /init to index them."
                )

            name_filter = arguments.get("name_filter", "") or ""
            docs = await asyncio.to_thread(_list_documents, repo_root, name_filter)
            if not docs:
                if name_filter:
                    return (
                        f"No reference documents matching '{name_filter}'. "
                        "Call list_reference_documents with no name_filter to see "
                        "the full list."
                    )
                return "No documents in the reference library index."

            lines = [f"{len(docs)} document(s) in the reference library:"]
            for d in docs:
                lines.append(
                    f"  - {d['doc_title']}  "
                    f"[format={d['format']}, chunks={d['chunk_count']}, "
                    f"path={d['doc_path']}]"
                )
            return "\n".join(lines)

        elif name == "query_project_context":
            from lean_ai.context.context_db import get_context_db, query_entries
            db = await get_context_db(repo_root)
            try:
                results = await query_entries(
                    db,
                    section=arguments.get("section"),
                    file_path=arguments.get("file_path"),
                    keyword=arguments.get("keyword"),
                )
                if not results:
                    return "No matching context entries found."
                lines = []
                for r in results:
                    lines.append(f"[{r['section']}] {r['content']}")
                return "\n".join(lines)
            finally:
                await db.close()

        elif name == "verify_web_ui":
            from lean_ai.tools.ui_verification import verify_web_ui
            return await verify_web_ui(
                url=arguments["url"],
                question=arguments["question"],
                repo_root=repo_root,
                viewport=arguments.get("viewport"),
                wait_for_selector=arguments.get("wait_for_selector"),
                wait_seconds=arguments.get("wait_seconds"),
                full_page=bool(arguments.get("full_page", False)),
            )

        elif name == "verify_desktop_ui":
            from lean_ai.tools.ui_verification import verify_desktop_ui
            launch_command = arguments.get("launch_command")
            if not isinstance(launch_command, list):
                return (
                    "ERROR: verify_desktop_ui requires launch_command as a "
                    "list of strings, got "
                    f"{type(launch_command).__name__}"
                )
            return await verify_desktop_ui(
                launch_command=launch_command,
                window_title=arguments["window_title"],
                question=arguments["question"],
                repo_root=repo_root,
                wait_seconds=arguments.get("wait_seconds"),
                window_timeout=arguments.get("window_timeout"),
            )

        elif name == "task_complete":
            return "Task marked complete."

        return f"ERROR: Unknown tool: {name}"

    async def execute_with_compression(name: str, arguments: dict) -> str:
        """Execute a tool and optionally compress large output via worker model."""
        result = await execute(name, arguments)
        if (
            worker_client is not None
            and name in _COMPRESSIBLE_TOOLS
            and not result.startswith("ERROR:")
        ):
            result = await _compress_tool_output(
                result, name, worker_client,
                telemetry_context=telemetry_context,
            )
        return result

    if worker_client is not None:
        return execute_with_compression
    return execute

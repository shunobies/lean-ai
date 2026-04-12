"""Phase 2 codebase exploration for the planning pipeline.

Handles read-only tool execution, parallel fan-out/merge, and serial
exploration paths. Extracted from planner.py for maintainability.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import WebSocket

from lean_ai.config import settings
from lean_ai.llm.planner_helpers import (
    _extract_file_paths,
    _save_debug_phase,
    _send_stage,
    _split_list,
)
from lean_ai.llm.prompt_registry import registry
from lean_ai.llm.prompts import PLAN_EXPLORATION_SYSTEM_PROMPT
from lean_ai.llm.tool_definitions import (
    build_planning_tools,
    build_planning_tools_with_scratchpad,
)

if TYPE_CHECKING:
    from lean_ai.llm.facade import LLMClient
    from lean_ai.workflow.ws_dispatcher import WSMessageDispatcher

logger = logging.getLogger(__name__)


def _make_read_only_executor(
    explorer: "LLMClient",
    repo_root: str,
    session_id: str,
    ws: WebSocket | None,
    dispatcher: "WSMessageDispatcher | None",
    small_ctx: bool,
) -> Callable:
    """Create a tool executor for read-only planning tools.

    Returns an async function usable as ``tool_executor_fn`` in
    ``chat_with_tools``.
    """

    async def _read_only_executor(name: str, arguments: dict) -> str:
        """Execute read-only tools for planning phase."""
        from lean_ai.tools.file_ops import grep_files, read_file
        from lean_ai.workflow.tool_executor import (
            _is_external_path,
            _request_tool_approval,
        )

        if name == "read_file":
            target_path = arguments.get("path", "")
            external = _is_external_path(target_path, repo_root)
            if external:
                if ws is None:
                    return (
                        "ERROR: Cannot read files outside the "
                        "project without an active connection"
                    )
                approved = await _request_tool_approval(
                    ws, dispatcher, "read_file", target_path,
                    "File is outside the project directory",
                )
                if not approved:
                    return (
                        "ERROR: Access to external file not "
                        "approved by user"
                    )
            # At small windows, cap visible range to 200 lines
            end_line = arguments.get("end_line")
            if small_ctx and end_line is None:
                start = arguments.get("start_line") or 1
                end_line = start + 199
            result = await read_file(
                path=target_path,
                repo_root=repo_root,
                start_line=arguments.get("start_line"),
                end_line=end_line,
                allow_external=external,
            )
            return (
                result.output if result.success else result.error or "Error"
            )
        elif name == "grep_files":
            result = await grep_files(
                pattern=arguments.get("pattern", ""),
                repo_root=repo_root,
                file_glob=arguments.get("file_glob"),
                max_results=30 if small_ctx else None,
            )
            return (
                result.output if result.success else result.error or "Error"
            )
        elif name == "list_directory":
            target = Path(repo_root) / arguments.get("path", "")
            if not target.is_dir():
                return f"Not a directory: {arguments.get('path', '')}"
            default_max = 50 if small_ctx else 100
            max_entries = arguments.get("max_entries", default_max)
            entries = sorted(target.iterdir())[:max_entries]
            lines = []
            for e in entries:
                prefix = "d" if e.is_dir() else "f"
                lines.append(f"  {prefix}  {e.name}")
            return "\n".join(lines) or "(empty)"
        elif name == "directory_tree":
            from lean_ai.indexer.tree import list_repo_tree
            sub_path = arguments.get("path", "")
            tree_root = (
                f"{repo_root}/{sub_path}" if sub_path else repo_root
            )
            entries = list_repo_tree(tree_root)
            max_depth = arguments.get("max_depth", 3)
            max_tree_entries = 100 if small_ctx else 200
            lines = []
            for e in entries[:max_tree_entries]:
                depth = e.path.count("/")
                if depth <= max_depth:
                    indent = "  " * depth
                    lines.append(f"{indent}{e.path.split('/')[-1]}")
            return "\n".join(lines) or "(empty)"
        elif name == "search_internet":
            from lean_ai.tools.internet import search_internet
            result = await search_internet(
                query=arguments.get("query", ""),
                llm_client=explorer,
            )
            return (
                result.output if result.success else result.error or "Error"
            )
        elif name == "fetch_url":
            from lean_ai.tools.internet import fetch_url
            result = await fetch_url(
                url=arguments.get("url", ""),
                repo_root=repo_root,
                llm_client=explorer,
            )
            return (
                result.output if result.success else result.error or "Error"
            )
        elif name == "update_scratchpad":
            from lean_ai.tools.scratchpad import update_scratchpad
            result = await update_scratchpad(
                content=arguments.get("content", ""),
                repo_root=repo_root,
                session_id=session_id,
            )
            return (
                result.output if result.success else result.error or "Error"
            )
        elif name == "add_journal_entry":
            from lean_ai.tools.journal import add_journal_entry
            result = await add_journal_entry(
                content=arguments.get("content", ""),
                repo_root=repo_root,
                session_id=session_id,
            )
            return (
                result.output if result.success else result.error or "Error"
            )
        elif name == "task_complete":
            return "Exploration marked complete."
        return f"Unknown tool: {name}"

    return _read_only_executor


async def run_phase2_exploration(
    *,
    task: str,
    scope: str,
    context: str,
    repo_root: str,
    session_id: str,
    explorer: "LLMClient",
    phase_max_tokens: int,
    ws: WebSocket | None,
    dispatcher: "WSMessageDispatcher | None",
    on_content: Callable | None = None,
    on_thinking: Callable | None = None,
    on_tool_call: Callable | None = None,
    on_tool_result: Callable | None = None,
    on_metrics: Callable | None = None,
) -> tuple[str, float]:
    """Run Phase 2: File identification + content reading.

    Returns (file_identification_output, elapsed_seconds).
    """
    t0 = time.monotonic()
    small_ctx = settings._active_context_window <= 32768

    executor = _make_read_only_executor(
        explorer, repo_root, session_id, ws, dispatcher, small_ctx,
    )

    phase2_messages = [
        {"role": "system", "content": PLAN_EXPLORATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": registry.format(
                "planning.exploration_user",
                task=task, scope=scope, context=context,
            ),
        },
    ]

    if settings.num_parallel >= 2:
        file_identification = await _run_parallel_exploration(
            task=task,
            scope=scope,
            context=context,
            repo_root=repo_root,
            session_id=session_id,
            explorer=explorer,
            phase_max_tokens=phase_max_tokens,
            ws=ws,
            executor=executor,
            on_content=on_content,
            on_thinking=on_thinking,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            on_metrics=on_metrics,
            t0=t0,
        )
    else:
        file_identification = await _run_serial_exploration(
            task=task,
            scope=scope,
            context=context,
            repo_root=repo_root,
            session_id=session_id,
            explorer=explorer,
            phase_max_tokens=phase_max_tokens,
            ws=ws,
            executor=executor,
            phase2_messages=phase2_messages,
            on_content=on_content,
            on_thinking=on_thinking,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            on_metrics=on_metrics,
        )

    elapsed = time.monotonic() - t0
    return file_identification, elapsed


async def _run_parallel_exploration(
    *,
    task: str,
    scope: str,
    context: str,
    repo_root: str,
    session_id: str,
    explorer: "LLMClient",
    phase_max_tokens: int,
    ws: WebSocket | None,
    executor: Callable,
    on_content: Callable | None,
    on_thinking: Callable | None,
    on_tool_call: Callable | None,
    on_tool_result: Callable | None,
    on_metrics: Callable | None,
    t0: float,
) -> str:
    """Parallel Phase 2: fan-out scan then merge deep-dive reads."""
    # Phase 2a: broad scan — identify files without reading contents
    scan_tools = [
        t for t in build_planning_tools()
        if t["function"]["name"] in (
            "list_directory", "directory_tree", "grep_files", "task_complete",
        )
    ]
    scan_messages = [
        {"role": "system", "content": PLAN_EXPLORATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "PHASE 2a — BROAD SCAN ONLY.\n"
                "Survey the codebase for the given task. "
                "Use directory_tree, grep_files, list_directory to identify "
                "ALL relevant files. Output a structured file list with "
                "each file's role (create/edit/reference). "
                "Do NOT read file contents.\n\n"
                + registry.format(
                    "planning.exploration_user",
                    task=task, scope=scope, context=context,
                )
            ),
        },
    ]

    _, scan_output = await explorer.chat_with_tools(
        messages=scan_messages,
        tools=scan_tools,
        tool_executor_fn=executor,
        max_turns=15,
        max_tokens=phase_max_tokens,
        on_tool_call=on_tool_call,
        on_tool_result=on_tool_result,
        on_content=on_content,
        on_thinking=on_thinking,
        on_metrics=on_metrics,
    )

    _save_debug_phase(
        repo_root, session_id, "phase_2a_scan",
        scan_output, time.monotonic() - t0,
    )

    # Parse file paths from scan output
    file_paths = _extract_file_paths(scan_output, repo_root)
    logger.info("Phase 2a scan identified %d file paths", len(file_paths))

    if not file_paths:
        return scan_output

    # Phase 2b: parallel deep-dive — read identified files
    n_workers = min(len(file_paths), settings.num_parallel)
    chunks = _split_list(file_paths, n_workers)

    async def _deep_dive(file_subset: list[str]) -> str:
        """Read a subset of files and produce a summary."""
        file_list = "\n".join(f"- {f}" for f in file_subset)
        dive_messages = [
            {"role": "system", "content": PLAN_EXPLORATION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"PHASE 2b — READ THESE FILES.\n"
                    f"Read each file and note: purpose, exports, imports, "
                    f"classes/functions with signatures, and what needs to "
                    f"change for the task.\n\nTask: {task}\n\n"
                    f"Files to read:\n{file_list}\n\n"
                    f"Call task_complete when done."
                ),
            },
        ]
        read_tools = [
            t for t in build_planning_tools()
            if t["function"]["name"] in (
                "read_file", "grep_files", "task_complete",
            )
        ]
        max_turns = max(10, 30 // n_workers)
        _, dive_output = await explorer.chat_with_tools(
            messages=dive_messages,
            tools=read_tools,
            tool_executor_fn=executor,
            max_turns=max_turns,
            max_tokens=phase_max_tokens,
        )
        return dive_output

    await _send_stage(
        ws,
        f"Phase 2b: {n_workers} parallel workers reading "
        f"{len(file_paths)} files...",
        model=explorer.model_name, phase=2,
    )

    dive_results = await asyncio.gather(
        *[_deep_dive(chunk) for chunk in chunks]
    )
    return (
        scan_output + "\n\n"
        + "\n\n".join(dive_results)
    )


async def _run_serial_exploration(
    *,
    task: str,
    scope: str,
    context: str,
    repo_root: str,
    session_id: str,
    explorer: "LLMClient",
    phase_max_tokens: int,
    ws: WebSocket | None,
    executor: Callable,
    phase2_messages: list[dict],
    on_content: Callable | None,
    on_thinking: Callable | None,
    on_tool_call: Callable | None,
    on_tool_result: Callable | None,
    on_metrics: Callable | None,
) -> str:
    """Serial Phase 2 (num_parallel=1) with scratchpad + context refresh."""
    from lean_ai.tools import scratchpad
    from lean_ai.tools.journal import read_journal
    from lean_ai.workflow.ws_handler import ws_send_nowait

    # Inject existing scratchpad + journal (crash recovery)
    if session_id:
        existing_pad = scratchpad.read_scratchpad(repo_root, session_id)
        existing_journal = read_journal(repo_root, session_id)
        if existing_journal:
            phase2_messages.append({
                "role": "user",
                "content": (
                    "[JOURNAL FROM PREVIOUS EXPLORATION]\n\n"
                    + existing_journal
                ),
            })
        if existing_pad:
            phase2_messages.append({
                "role": "user",
                "content": (
                    "[SCRATCHPAD FROM PREVIOUS EXPLORATION — "
                    "resume from here]\n\n" + existing_pad
                ),
            })

    def _build_phase2_refresh(
        current_messages: list[dict],
    ) -> list[dict]:
        """Rebuild Phase 2 messages for context refresh."""
        pad = scratchpad.read_scratchpad(repo_root, session_id)
        jrnl = read_journal(repo_root, session_id)
        new_messages: list[dict] = [
            {"role": "system", "content": PLAN_EXPLORATION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": registry.format(
                    "planning.exploration_user",
                    task=task, scope=scope, context=context,
                ),
            },
        ]
        refresh_parts = ["[CONTEXT REFRESHED]"]
        if jrnl:
            refresh_parts.append(
                f"SESSION JOURNAL (permanent findings):\n{jrnl}"
            )
        if pad:
            refresh_parts.append(
                f"SCRATCHPAD (current state):\n{pad}"
            )
        if pad or jrnl:
            new_messages.append({
                "role": "user",
                "content": "\n\n".join(refresh_parts),
            })
        else:
            new_messages.append({
                "role": "user",
                "content": (
                    "[CONTEXT REFRESHED]\n\n"
                    "Continue exploring the codebase for this task."
                ),
            })
        if ws:
            ws_send_nowait(ws, "context_refreshed", {
                "message": "Phase 2 context refreshed.",
            })
        return new_messages

    base_reminder = registry.format(
        "planning.task_reminder", task=task,
    )

    def _phase2_reminder() -> str:
        return (
            base_reminder
            + "\n\nCall update_scratchpad to save volatile progress "
            "and add_journal_entry for key findings that must "
            "survive context refresh."
        )

    _tool_calls, file_identification = await explorer.chat_with_tools(
        messages=phase2_messages,
        tools=build_planning_tools_with_scratchpad(),
        tool_executor_fn=executor,
        max_turns=settings.implementation_max_turns,
        max_tokens=phase_max_tokens,
        task_reminder=_phase2_reminder,
        reminder_interval=15,
        on_tool_call=on_tool_call,
        on_tool_result=on_tool_result,
        on_content=on_content,
        on_thinking=on_thinking,
        on_metrics=on_metrics,
        on_context_refresh=_build_phase2_refresh,
    )

    return file_identification

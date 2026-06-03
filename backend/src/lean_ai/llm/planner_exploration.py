"""Phase 2 codebase exploration for the planning pipeline.

Handles read-only tool execution, parallel fan-out/merge, and serial
exploration paths. Extracted from planner.py for maintainability.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from lean_ai.config import settings
from lean_ai.workflow.state import StateManager
from lean_ai.llm.planner_helpers import (
    _extract_file_paths,
    _save_debug_phase,
    _send_stage,
    _split_list,
)
from lean_ai.llm.prompt_registry import registry
from lean_ai.llm.prompts import resolve_prompt_text
from lean_ai.llm.tool_definitions import (
    build_planning_tools,
    build_planning_tools_with_scratchpad,
)

if TYPE_CHECKING:
    from lean_ai.llm.facade import LLMClient
    from lean_ai.llm.plan_schema import FileSummary
    from lean_ai.workflow.ws_dispatcher import WSMessageDispatcher
    from lean_ai.workflow.ws_protocol import WorkflowSession

logger = logging.getLogger(__name__)


@dataclass
class ToolContext:
    """Execution context passed to every tool handler."""

    repo_root: str
    session_id: str
    explorer: "LLMClient"
    small_ctx: bool = False
    ws: Optional["WorkflowSession"] = None
    dispatcher: Optional["WSMessageDispatcher"] = None


class ToolRegistry:
    """Central registry that maps tool names to async handler callables.

    Follows the same module-level dictionary pattern used by
    ``integrations.registry`` for ``@register_integration``.
    """

    _handlers: dict[str, Callable] = {}

    @classmethod
    def register(cls, name: str) -> Callable[[Callable], Callable]:
        """Decorator to register a tool handler under *name*."""

        def decorator(func: Callable) -> Callable:
            cls._handlers[name] = func
            logger.debug("Registered tool handler: %s", name)
            return func

        return decorator

    @classmethod
    def get_handler(cls, name: str) -> Callable | None:
        """Return the handler for *name*, or None if not registered."""
        return cls._handlers.get(name)


def _fire_clarification_capture(
    repo_root: str,
    session_id: str,
    *,
    question: str,
    answer: str | None,
    outcome: str,
    task: str | None = None,
) -> None:
    """Fire-and-forget capture of a Phase 1 clarification Q/A pair."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _run():
        try:
            from lean_ai.training.capture import capture_clarification

            await capture_clarification(
                repo_root,
                session_id=session_id,
                question=question,
                answer=answer,
                outcome=outcome,
                task=task,
                phase="planning.phase1",
            )
        except Exception:
            logger.debug(
                "capture_clarification failed (non-fatal)",
                exc_info=True,
            )

    t = loop.create_task(_run())
    t.add_done_callback(lambda tk: tk.exception() if tk.done() else None)


def _fire_phase2_synthesis_capture(
    repo_root: str,
    session_id: str,
    *,
    task: str | None,
    scope: str | None,
    observations: list | None,
    scratchpad: str | None,
    journal: str | None,
    exploration_output: str | None,
    file_summary: dict | None,
) -> None:
    """Fire-and-forget capture of the Phase 2 synthesis pair."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _run():
        try:
            from lean_ai.training.capture import capture_phase2_synthesis

            await capture_phase2_synthesis(
                repo_root,
                session_id=session_id,
                task=task,
                scope=scope,
                observations=observations,
                scratchpad=scratchpad,
                journal=journal,
                exploration_output=exploration_output,
                file_summary=file_summary,
            )
        except Exception:
            logger.debug(
                "capture_phase2_synthesis failed (non-fatal)",
                exc_info=True,
            )

    t = loop.create_task(_run())
    t.add_done_callback(lambda tk: tk.exception() if tk.done() else None)


# ---------------------------------------------------------------------------
# Tool handler classes — each implements execute(context, args) -> str
# and is registered via @ToolRegistry.register('tool_name').
# ---------------------------------------------------------------------------


class ReadFileHandler:
    """Handle read_file tool calls with external-path approval and small-context truncation."""

    @ToolRegistry.register("read_file")
    @staticmethod
    async def execute(context: ToolContext, args: dict) -> str:
        from lean_ai.tools.file_ops import read_file
        from lean_ai.workflow.tool_executor import (
            _is_external_path,
            _request_tool_approval,
        )

        target_path = args.get("path", "")
        external = _is_external_path(target_path, context.repo_root)
        if external:
            if context.ws is None:
                return (
                    "ERROR: Cannot read files outside the project without an active connection"
                )
            approved = await _request_tool_approval(
                context.ws,
                context.dispatcher,
                "read_file",
                target_path,
                "File is outside the project directory",
            )
            if not approved:
                return "ERROR: Access to external file not approved by user"
        # At small windows, cap visible range to 200 lines
        end_line = args.get("end_line")
        if context.small_ctx and end_line is None:
            start = args.get("start_line") or 1
            end_line = start + 199
        result = await read_file(
            path=target_path,
            repo_root=context.repo_root,
            start_line=args.get("start_line"),
            end_line=end_line,
            allow_external=external,
        )
        output = result.output if result.success else result.error or "Error"
        # Notify the LLM when a small-context cap truncated the file
        if (
            context.small_ctx
            and end_line is not None
            and result.success
            and "[END OF FILE]" not in output
        ):
            output += (
                f"\n\n[TRUNCATED at line {end_line} — small context "
                f"window. Use start_line={end_line + 1} to read more.]"
            )
        return output


class GrepFilesHandler:
    """Handle grep_files tool calls for pattern-based file searches."""

    @ToolRegistry.register("grep_files")
    @staticmethod
    async def execute(context: ToolContext, args: dict) -> str:
        from lean_ai.tools.file_ops import grep_files

        result = await grep_files(
            pattern=args.get("pattern", ""),
            repo_root=context.repo_root,
            file_glob=args.get("file_glob"),
            max_results=30 if context.small_ctx else None,
        )
        return result.output if result.success else result.error or "Error"


class ListDirectoryHandler:
    """Handle list_directory tool calls to list directory contents."""

    @ToolRegistry.register("list_directory")
    @staticmethod
    async def execute(context: ToolContext, args: dict) -> str:
        target = Path(context.repo_root) / args.get("path", "")
        if not target.is_dir():
            return f"Not a directory: {args.get('path', '')}"
        default_max = 50 if context.small_ctx else 100
        max_entries = args.get("max_entries", default_max)
        entries = sorted(target.iterdir())[:max_entries]
        lines = []
        for e in entries:
            prefix = "d" if e.is_dir() else "f"
            lines.append(f"  {prefix}  {e.name}")
        return "\n".join(lines) or "(empty)"


class DirectoryTreeHandler:
    """Handle directory_tree tool calls for recursive repository tree listings."""

    @ToolRegistry.register("directory_tree")
    @staticmethod
    async def execute(context: ToolContext, args: dict) -> str:
        from lean_ai.indexer.tree import list_repo_tree

        sub_path = args.get("path", "")
        tree_root = f"{context.repo_root}/{sub_path}" if sub_path else context.repo_root
        entries = list_repo_tree(tree_root)
        max_depth = args.get("max_depth", 3)
        max_tree_entries = 100 if context.small_ctx else 200
        lines = []
        for e in entries[:max_tree_entries]:
            depth = e.path.count("/")
            if depth <= max_depth:
                indent = "  " * depth
                lines.append(f"{indent}{e.path.split('/')[-1]}")
        return "\n".join(lines) or "(empty)"


class SearchInternetHandler:
    """Handle search_internet tool calls for web searches."""

    @ToolRegistry.register("search_internet")
    @staticmethod
    async def execute(context: ToolContext, args: dict) -> str:
        from lean_ai.tools.internet import search_internet

        result = await search_internet(
            query=args.get("query", ""),
            llm_client=context.explorer,
        )
        return result.output if result.success else result.error or "Error"


class FetchUrlHandler:
    """Handle fetch_url tool calls for fetching URL content."""

    @ToolRegistry.register("fetch_url")
    @staticmethod
    async def execute(context: ToolContext, args: dict) -> str:
        from lean_ai.tools.internet import fetch_url

        result = await fetch_url(
            url=args.get("url", ""),
            repo_root=context.repo_root,
            llm_client=context.explorer,
        )
        return result.output if result.success else result.error or "Error"


class UpdateScratchpadHandler:
    """Handle update_scratchpad tool calls for managing the planning scratchpad."""

    @ToolRegistry.register("update_scratchpad")
    @staticmethod
    async def execute(context: ToolContext, args: dict) -> str:
        from lean_ai.tools.scratchpad import update_scratchpad

        result = await update_scratchpad(
            content=args.get("content", ""),
            repo_root=context.repo_root,
            session_id=context.session_id,
        )
        return result.output if result.success else result.error or "Error"


class AddJournalEntryHandler:
    """Handle add_journal_entry tool calls for appending to the session journal."""

    @ToolRegistry.register("add_journal_entry")
    @staticmethod
    async def execute(context: ToolContext, args: dict) -> str:
        from lean_ai.tools.journal import add_journal_entry

        result = await add_journal_entry(
            content=args.get("content", ""),
            repo_root=context.repo_root,
            session_id=context.session_id,
        )
        return result.output if result.success else result.error or "Error"


class QueryProjectContextHandler:
    """Handle query_project_context tool calls for querying the project context database."""

    @ToolRegistry.register("query_project_context")
    @staticmethod
    async def execute(context: ToolContext, args: dict) -> str:
        from lean_ai.context.context_db import (
            get_context_db,
            query_entries,
        )

        db = await get_context_db(context.repo_root)
        try:
            results = await query_entries(
                db,
                section=args.get("section"),
                file_path=args.get("file_path"),
                keyword=args.get("keyword"),
            )
            if not results:
                return "No matching context entries found."
            lines = []
            for r in results:
                lines.append(f"[{r['section']}] {r['content']}")
            return "\n".join(lines)
        finally:
            await db.close()


class RecordFileObservationHandler:
    """Handle record_file_observation tool calls for recording file analysis notes."""

    @ToolRegistry.register("record_file_observation")
    @staticmethod
    async def execute(context: ToolContext, args: dict) -> str:
        from lean_ai.tools.observations import record_observation

        result = await record_observation(
            file_path=args.get("file_path", ""),
            role=args.get("role", ""),
            reason=args.get("reason", ""),
            relevant_sections=args.get("relevant_sections", ""),
            key_snippets=args.get("key_snippets") or [],
            repo_root=context.repo_root,
            session_id=context.session_id,
        )
        return result.output if result.success else result.error or "Error"


class TaskCompleteHandler:
    """Handle task_complete tool calls to signal exploration completion."""

    @ToolRegistry.register("task_complete")
    @staticmethod
    async def execute(context: ToolContext, args: dict) -> str:
        return "Exploration marked complete."


class RequestClarificationHandler:
    """Handle request_clarification tool calls for Phase 1 scope verification."""

    @ToolRegistry.register("request_clarification")
    @staticmethod
    async def execute(context: ToolContext, args: dict) -> str:
        # Phase 1 scope verification only. Blocks until the user replies
        # via the approval queue — safe because planning runs before
        # dispatcher.enter_execution_mode() switches user_message to
        # the interrupt queue.
        from lean_ai.workflow.ws_dispatcher import WorkflowCancelledError
        from lean_ai.workflow.ws_handler import ws_send

        question = (args.get("question") or "").strip()
        if not question:
            return "ERROR: request_clarification requires a non-empty 'question' string."
        if context.ws is None or context.dispatcher is None:
            _fire_clarification_capture(
                context.repo_root,
                context.session_id,
                question=question,
                answer=None,
                outcome="error",
            )
            return (
                "ERROR: no active user session — cannot request "
                "clarification. Proceed with a best-guess interpretation "
                "and record it as an ASSUMPTION."
            )
        await ws_send(
            context.ws,
            "clarification_needed",
            {"questions": [question]},
        )
        try:
            msg = await context.dispatcher.wait_for_approval()
        except WorkflowCancelledError:
            _fire_clarification_capture(
                context.repo_root,
                context.session_id,
                question=question,
                answer=None,
                outcome="cancelled",
            )
            return "ERROR: user cancelled while clarification was pending."
        if msg is None:
            _fire_clarification_capture(
                context.repo_root,
                context.session_id,
                question=question,
                answer=None,
                outcome="disconnected",
            )
            return (
                "ERROR: session disconnected before the user "
                "responded to the clarification question."
            )
        if msg.get("type") != "user_message":
            _fire_clarification_capture(
                context.repo_root,
                context.session_id,
                question=question,
                answer=None,
                outcome="error",
            )
            return f"ERROR: unexpected message type during clarification: {msg.get('type')}"
        answer = (msg.get("content") or "").strip()
        if not answer:
            _fire_clarification_capture(
                context.repo_root,
                context.session_id,
                question=question,
                answer="",
                outcome="empty",
            )
            return (
                "User response was empty — proceed with a best-guess "
                "interpretation and record it as an ASSUMPTION."
            )
        _fire_clarification_capture(
            context.repo_root,
            context.session_id,
            question=question,
            answer=answer,
            outcome="answered",
        )
        return f"User answered: {answer}"


def _make_read_only_executor(
    explorer: LLMClient,
    repo_root: str,
    session_id: str,
    ws: "WorkflowSession | None",
    dispatcher: WSMessageDispatcher | None,
    small_ctx: bool,
) -> Callable:
    """Create a tool executor for read-only planning tools.

    Resolves the handler from ToolRegistry, constructs a ToolContext,
    and delegates to handler.execute().
    """

    async def _read_only_executor(name: str, arguments: dict) -> str:
        """Execute read-only tools for planning phase via registry dispatch."""
        handler = ToolRegistry.get_handler(name)
        if handler is None:
            return f"Unknown tool: {name}"
        ctx = ToolContext(
            repo_root=repo_root,
            session_id=session_id,
            explorer=explorer,
            small_ctx=small_ctx,
            ws=ws,
            dispatcher=dispatcher,
        )
        return await handler(ctx, arguments)

    return _read_only_executor


async def run_phase2_exploration(
    *,
    task: str,
    scope: str,
    context: str,
    repo_root: str,
    session_id: str,
    explorer: LLMClient,
    phase_max_tokens: int,
    ws: "WorkflowSession | None",
    dispatcher: WSMessageDispatcher | None,
    state_manager: StateManager,
    prompt_scope=None,
    on_content: Callable | None = None,
    on_thinking: Callable | None = None,
    on_tool_call: Callable | None = None,
    on_tool_result: Callable | None = None,
    on_metrics: Callable | None = None,
    on_metrics_reset: Callable | None = None,
) -> tuple[FileSummary | None, str, float]:
    """Run Phase 2: File identification + content reading.

    Returns ``(FileSummary | None, file_identification_markdown, elapsed)``.
    The structured ``FileSummary`` is non-None when the synthesis pass
    succeeded (both serial and parallel paths now run synthesis). On
    synthesis failure the object is ``None`` and the raw prose is used
    as the markdown handoff. Phase 4 validators skip cleanly when the
    object is ``None``.
    """
    t0 = time.monotonic()
    small_ctx = settings._active_context_window <= 32768

    executor = _make_read_only_executor(
        explorer,
        repo_root,
        session_id,
        ws,
        dispatcher,
        small_ctx,
    )

    phase2_system_prompt = resolve_prompt_text("planning.exploration_system", scope=prompt_scope)
    phase2_messages = [
        {"role": "system", "content": phase2_system_prompt},
        {
            "role": "user",
            "content": registry.format_text(
                "planning.exploration_user",
                task=task,
                scope=scope,
                context=context,
            ),
        },
    ]

    file_summary_obj: FileSummary | None = None

    if settings.num_parallel >= 2:
        file_identification, file_summary_obj = await _run_parallel_exploration(
            task=task,
            scope=scope,
            context=context,
            repo_root=repo_root,
            session_id=session_id,
            explorer=explorer,
            phase_max_tokens=phase_max_tokens,
            ws=ws,
            dispatcher=dispatcher,
            state_manager=state_manager,
            executor=executor,
            on_content=on_content,
            on_thinking=on_thinking,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            on_metrics=on_metrics,
            on_metrics_reset=on_metrics_reset,
            prompt_scope=prompt_scope,
            t0=t0,
        )
    else:
        exploration_output = await _run_serial_exploration(
            task=task,
            scope=scope,
            context=context,
            repo_root=repo_root,
            session_id=session_id,
            explorer=explorer,
            phase_max_tokens=phase_max_tokens,
            ws=ws,
            dispatcher=dispatcher,
            state_manager=state_manager,
            executor=executor,
            phase2_messages=phase2_messages,
            on_content=on_content,
            on_thinking=on_thinking,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            on_metrics=on_metrics,
            on_metrics_reset=on_metrics_reset,
        )

        # Synthesis pass: coerce recorded observations + scratchpad +
        # journal + prose into a validated FileSummary, then render to
        # markdown for the downstream {file_summary} contract. We keep
        # the structured object around so Phase 4 validators can do
        # set-membership checks without re-parsing markdown.
        file_summary_obj, file_identification = await _synthesize_file_summary(
            task=task,
            scope=scope,
            exploration_output=exploration_output,
            repo_root=repo_root,
            session_id=session_id,
            explorer=explorer,
            phase_max_tokens=phase_max_tokens,
            state_manager=state_manager,
            on_thinking=on_thinking,
            on_metrics=on_metrics,
            on_metrics_reset=on_metrics_reset,
        )

        # Snapshot raw evidence + structured output for training. This
        # must happen before session close cleans up the observations
        # file — the pair is the highest-quality supervised data the
        # pipeline produces for "exploration → structured summary".
        if file_summary_obj is not None:
            try:
                state = await state_manager.get_state_async()
                observations = state.observations
                scratchpad_text = state.scratchpad_content or ""
                journal_text = "\n".join(state.journal_entries) if state.journal_entries else ""
                _fire_phase2_synthesis_capture(
                    repo_root,
                    session_id,
                    task=task,
                    scope=scope,
                    observations=observations,
                    scratchpad=scratchpad_text or None,
                    journal=journal_text or None,
                    exploration_output=exploration_output,
                    file_summary=file_summary_obj.model_dump(),
                )
            except Exception:
                logger.debug(
                    "phase2 synthesis snapshot failed (non-fatal)",
                    exc_info=True,
                )

    elapsed = time.monotonic() - t0
    return file_summary_obj, file_identification, elapsed


async def _run_parallel_exploration(
    *,
    task: str,
    scope: str,
    context: str,
    repo_root: str,
    session_id: str,
    explorer: LLMClient,
    phase_max_tokens: int,
    ws: "WorkflowSession | None",
    dispatcher: WSMessageDispatcher | None,
    state_manager: StateManager,
    executor: Callable,
    on_content: Callable | None,
    on_thinking: Callable | None,
    on_tool_call: Callable | None,
    on_tool_result: Callable | None,
    on_metrics: Callable | None,
    on_metrics_reset: Callable | None,
    prompt_scope,
    t0: float,
) -> tuple[str, FileSummary | None]:
    """Parallel Phase 2: fan-out scan then merge deep-dive reads."""
    # Phase 2a: broad scan — identify files without reading contents
    scan_tools = [
        t
        for t in build_planning_tools()
        if t["function"]["name"]
        in (
            "list_directory",
            "directory_tree",
            "grep_files",
            "task_complete",
        )
    ]
    scan_messages = [
        {
            "role": "system",
            "content": resolve_prompt_text("planning.exploration_system", scope=prompt_scope),
        },
        {
            "role": "user",
            "content": (
                "PHASE 2a — BROAD SCAN ONLY.\n"
                "Survey the codebase for the given task. "
                "Use directory_tree, grep_files, list_directory to identify "
                "ALL relevant files. Output a structured file list with "
                "each file's role (create/edit/reference). "
                "Do NOT read file contents.\n\n"
                + registry.format_text(
                    "planning.exploration_user",
                    task=task,
                    scope=scope,
                    context=context,
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
        on_metrics_reset=on_metrics_reset,
        dispatcher=dispatcher,
    )

    _save_debug_phase(
        repo_root,
        session_id,
        "phase_2a_scan",
        scan_output,
        time.monotonic() - t0,
    )

    # Parse file paths from scan output
    file_paths = _extract_file_paths(scan_output, repo_root)
    logger.info("Phase 2a scan identified %d file paths", len(file_paths))

    if not file_paths:
        return scan_output, None

    # Phase 2b: parallel deep-dive — read identified files
    n_workers = min(len(file_paths), settings.num_parallel)
    chunks = _split_list(file_paths, n_workers)

    async def _deep_dive(file_subset: list[str]) -> str:
        """Read a subset of files and produce a summary."""
        from lean_ai.llm.tool_definitions import RECORD_FILE_OBSERVATION_TOOL

        file_list = "\n".join(f"- {f}" for f in file_subset)
        dive_messages = [
            {
                "role": "system",
                "content": resolve_prompt_text("planning.exploration_system", scope=prompt_scope),
            },
            {
                "role": "user",
                "content": (
                    f"PHASE 2b — READ THESE FILES.\n"
                    f"Read each file and note: purpose, exports, imports, "
                    f"classes/functions with signatures, and what needs to "
                    f"change for the task.\n\nTask: {task}\n\n"
                    f"Files to read:\n{file_list}\n\n"
                    f"Call record_file_observation for every relevant file "
                    f"you read, then call task_complete when done."
                ),
            },
        ]
        read_tools = [
            t
            for t in build_planning_tools()
            if t["function"]["name"]
            in (
                "read_file",
                "grep_files",
                "task_complete",
            )
        ]
        read_tools.append(RECORD_FILE_OBSERVATION_TOOL)
        max_turns = max(10, 30 // n_workers)
        _, dive_output = await explorer.chat_with_tools(
            messages=dive_messages,
            tools=read_tools,
            tool_executor_fn=executor,
            max_turns=max_turns,
            max_tokens=phase_max_tokens,
            dispatcher=dispatcher,
        )
        return dive_output

    await _send_stage(
        ws,
        f"Phase 2b: {n_workers} parallel workers reading {len(file_paths)} files...",
        model=explorer.model_name,
        phase=2,
    )

    dive_results = await asyncio.gather(
        *[_deep_dive(chunk) for chunk in chunks],
        return_exceptions=True,
    )

    # Collect successful results, log failures
    good_results: list[str] = []
    for i, result in enumerate(dive_results):
        if isinstance(result, BaseException):
            logger.warning(
                "Phase 2b worker %d/%d failed: %s",
                i + 1,
                len(dive_results),
                result,
            )
        elif isinstance(result, str):
            good_results.append(result)

    if not good_results:
        return scan_output, None

    merged_prose = scan_output + "\n\n" + "\n\n".join(good_results)

    # Synthesis pass: coerce observations recorded by deep-dive workers
    # into a validated FileSummary so downstream phases get structured data.
    file_summary_obj, file_identification = await _synthesize_file_summary(
        task=task,
        scope=scope,
        exploration_output=merged_prose,
        repo_root=repo_root,
        session_id=session_id,
        explorer=explorer,
        phase_max_tokens=phase_max_tokens,
        state_manager=state_manager,
        on_thinking=on_thinking,
        on_metrics=on_metrics,
        on_metrics_reset=on_metrics_reset,
    )

    return file_identification, file_summary_obj


async def _run_serial_exploration(
    *,
    task: str,
    scope: str,
    context: str,
    repo_root: str,
    session_id: str,
    explorer: LLMClient,
    phase_max_tokens: int,
    ws: "WorkflowSession | None",
    dispatcher: WSMessageDispatcher | None,
    state_manager: StateManager,
    executor: Callable,
    phase2_messages: list[dict],
    on_content: Callable | None,
    on_thinking: Callable | None,
    on_tool_call: Callable | None,
    on_tool_result: Callable | None,
    on_metrics: Callable | None,
    on_metrics_reset: Callable | None,
) -> str:
    """Serial Phase 2 (num_parallel=1) with scratchpad + context refresh."""
    import json as _json

    from lean_ai.llm.tool_definitions import RECORD_FILE_OBSERVATION_TOOL
    from lean_ai.tools.state_ledger import append_event, summarize_recent_events
    from lean_ai.workflow.ws_handler import ws_send_nowait

    append_event(
        repo_root=repo_root,
        session_id=session_id,
        event_type="phase_transition",
        payload={"phase": "planning.phase2"},
    )

    # Inject existing scratchpad + journal (crash recovery)
    if session_id:
        state = await state_manager.get_state_async()
        existing_pad = state.scratchpad_content or ""
        existing_journal = "\n".join(state.journal_entries) if state.journal_entries else ""
        if existing_journal:
            phase2_messages.append(
                {
                    "role": "user",
                    "content": ("[JOURNAL FROM PREVIOUS EXPLORATION]\n\n" + existing_journal),
                }
            )
        if existing_pad:
            phase2_messages.append(
                {
                    "role": "user",
                    "content": (
                        "[SCRATCHPAD FROM PREVIOUS EXPLORATION — "
                        "resume from here]\n\n" + existing_pad
                    ),
                }
            )

    def _build_phase2_refresh(
        current_messages: list[dict],
    ) -> list[dict]:
        """Rebuild Phase 2 messages for context refresh."""
        state = state_manager.get_cached_state()
        pad = state.scratchpad_content or ""
        jrnl = "\n".join(state.journal_entries) if state.journal_entries else ""
        obs = state.observations
        new_messages: list[dict] = [
            {"role": "system", "content": phase2_messages[0]["content"]},
            {
                "role": "user",
                "content": registry.format_text(
                    "planning.exploration_user",
                    task=task,
                    scope=scope,
                    context=context,
                ),
            },
        ]
        refresh_parts = ["[CONTEXT REFRESHED]"]
        if obs:
            refresh_parts.append(
                "RECORDED FILE OBSERVATIONS (do not re-record these):\n"
                + _json.dumps(obs, indent=2, ensure_ascii=False)
            )
        if jrnl:
            refresh_parts.append(f"SESSION JOURNAL (permanent findings):\n{jrnl}")
        if pad:
            refresh_parts.append(f"SCRATCHPAD (current state):\n{pad}")
        ledger_summary = summarize_recent_events(repo_root, session_id)
        if ledger_summary:
            refresh_parts.append(f"RECENT STATE LEDGER (machine events):\n{ledger_summary}")
        if obs or pad or jrnl:
            # Build an "already explored" summary from observations
            explored_files = []
            if obs:
                if isinstance(obs, list):
                    for item in obs:
                        if isinstance(item, dict) and item.get("file_path"):
                            explored_files.append(item["file_path"])
                        elif isinstance(item, str):
                            explored_files.append(item)
                elif isinstance(obs, dict):
                    for item in obs.values():
                        if isinstance(item, dict) and item.get("file_path"):
                            explored_files.append(item["file_path"])

            refresh_parts.append(
                "INSTRUCTIONS AFTER CONTEXT REFRESH:\n"
                "1. Review the journal, observations, and scratchpad above. These represent work "
                "you ALREADY completed before the refresh.\n"
                "2. Do NOT re-read or re-explore files that are already recorded in observations.\n"
                "3. If you explore new files, call record_file_observation immediately after "
                "reading or grepping each relevant file.\n"
                "4. Resume from where you left off — check the scratchpad for your "
                "last known state."
            )
            if explored_files:
                refresh_parts.append(
                    f"ALREADY EXPLORED FILES (do not re-read): {', '.join(explored_files)}"
                )
            new_messages.append(
                {
                    "role": "user",
                    "content": "\n\n".join(refresh_parts),
                }
            )
        else:
            new_messages.append(
                {
                    "role": "user",
                    "content": (
                        "[CONTEXT REFRESHED]\n\nNo file observations, journal, or scratchpad "
                        "state were found from before the refresh. Continue Phase 2 exploration, "
                        "and call record_file_observation immediately after each relevant read "
                        "or grep so findings survive future refreshes."
                    ),
                }
            )
        if ws:
            ws_send_nowait(
                ws,
                "context_refreshed",
                {
                    "message": "Phase 2 context refreshed.",
                },
            )
        append_event(
            repo_root=repo_root,
            session_id=session_id,
            event_type="context_refreshed",
            payload={"phase": "planning.phase2"},
        )
        return new_messages

    base_reminder = registry.format_text(
        "planning.task_reminder",
        task=task,
    )
    await state_manager.get_state_async()

    def _phase2_reminder() -> str:
        return (
            base_reminder + "\n\nCall record_file_observation for every relevant file, "
            "update_scratchpad for volatile progress, and add_journal_entry "
            "for findings that must survive a context refresh."
        )

    def _phase2_pre_context_refresh_nudge() -> str:
        state = state_manager.get_cached_state()
        obs = state.observations
        recorded_paths: list[str] = []
        if isinstance(obs, list):
            for item in obs:
                if isinstance(item, dict) and item.get("file_path"):
                    recorded_paths.append(str(item["file_path"]))

        recorded_note = ""
        if recorded_paths:
            shown = ", ".join(recorded_paths[:12])
            suffix = " ..." if len(recorded_paths) > 12 else ""
            recorded_note = f" Already recorded: {shown}{suffix}."

        return (
            "CONTEXT WARNING: Phase 2 is nearing context refresh. Before continuing, "
            "call record_file_observation for every relevant file you have already "
            "read or grepped but have not recorded yet. Observations are the "
            "authoritative bridge into downstream synthesis; prose alone is not "
            "a reliable refresh bridge. Then add_journal_entry for durable findings "
            "and update_scratchpad with the single best next action."
            + recorded_note
        )

    # Phase-2-specific tool filter: drop KB tools (not useful for file
    # identification), add record_file_observation for deterministic capture.
    phase2_tools = [
        t
        for t in build_planning_tools_with_scratchpad()
        if t["function"]["name"]
        not in (
            "search_reference",
            "list_reference_documents",
        )
    ]
    phase2_tools.append(RECORD_FILE_OBSERVATION_TOOL)

    # Hard-gate task_complete on at least one recorded observation.
    # Phase 2's downstream contract is the observations file — prose
    # alone reaches the synthesis pass with zero structured data and
    # produces a near-empty FileSummary.  The validator fires from the
    # facade AFTER any same-turn record_file_observation call has
    # written to disk, so combined turns like
    # [record_file_observation, task_complete] approve naturally.
    def _phase2_task_complete_validator() -> str | None:
        state = state_manager.get_cached_state()
        obs = state.observations
        if obs:
            return None
        return (
            "ERROR: Cannot call task_complete — zero file observations "
            "have been recorded. Phase 2's output reaches downstream "
            "phases through record_file_observation, not prose. Before "
            "calling task_complete, call record_file_observation for "
            "every relevant file you have read, with role "
            "(modify / create / reference / missing), a one-line "
            "reason, relevant_sections (line ranges), and key_snippets "
            "(15-25 line excerpts). Continue exploring and recording "
            "now."
        )

    _tool_calls, file_identification = await explorer.chat_with_tools(
        messages=phase2_messages,
        tools=phase2_tools,
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
        on_metrics_reset=on_metrics_reset,
        dispatcher=dispatcher,
        on_context_refresh=_build_phase2_refresh,
        pre_context_refresh_nudge=_phase2_pre_context_refresh_nudge,
        telemetry_context={
            "repo_root": repo_root,
            "session_id": session_id,
            "phase": "planning.phase2",
            "role": "primary",
        },
        task_complete_validator=_phase2_task_complete_validator,
    )

    return file_identification


async def _synthesize_file_summary(
    *,
    task: str,
    scope: str,
    exploration_output: str,
    repo_root: str,
    session_id: str,
    explorer: LLMClient,
    phase_max_tokens: int,
    state_manager: StateManager,
    on_thinking: Callable | None = None,
    on_metrics: Callable | None = None,
    on_metrics_reset: Callable | None = None,
) -> tuple[FileSummary | None, str]:
    """Consolidate Phase 2 findings into a validated FileSummary and
    render it to markdown for downstream phases.

    Reads recorded observations, scratchpad, and journal, plus the
    exploration model's prose output, and coerces everything into a
    ``FileSummary`` via ``chat_structured``. Returns a tuple of
    ``(FileSummary | None, markdown)`` — the structured object (or
    ``None`` on synthesis failure) alongside the markdown that flows
    into Phase 3 / Phase 4 as ``{file_summary}``. Phase 4's validators
    use the structured form for path-set membership checks.

    On structured-output failure, falls back to ``(None, exploration_output)``
    so the pipeline keeps moving.
    """
    import json as _json

    from lean_ai.llm.plan_schema import FileSummary

    state = await state_manager.get_state_async()
    observations = state.observations
    pad = state.scratchpad_content or ""
    jrnl = "\n".join(state.journal_entries) if state.journal_entries else ""

    if not observations:
        logger.warning(
            "Phase 2 synthesis: zero recorded observations — "
            "falling back to exploration prose only.",
        )

    observations_json = _json.dumps(
        observations,
        indent=2,
        ensure_ascii=False,
    )

    user_payload_parts = [
        f"TASK: {task}",
        f"PHASE 1 SCOPE (source of ASSUMPTIONS):\n{scope}",
        f"RECORDED FILE OBSERVATIONS ({len(observations)}):\n{observations_json}",
    ]
    if pad:
        user_payload_parts.append(f"EXPLORATION SCRATCHPAD:\n{pad}")
    if jrnl:
        user_payload_parts.append(f"EXPLORATION JOURNAL:\n{jrnl}")
    if exploration_output.strip():
        user_payload_parts.append(f"EXPLORATION PROSE (model narrative):\n{exploration_output}")
    user_payload_parts.append(
        "Produce a FileSummary merging every source above. Bucket "
        "observations by role, populate missing_infrastructure, "
        "verified_references, and assumptions_resolved from the scope + "
        "prose + journal. Use the notes field for anything that does "
        "not fit the structured fields."
    )

    synthesis_system = registry.get_text("planning.exploration_synthesis_system")

    try:
        summary = await explorer.chat_structured(
            messages=[
                {"role": "system", "content": synthesis_system},
                {"role": "user", "content": "\n\n".join(user_payload_parts)},
            ],
            schema=FileSummary,
            max_tokens=phase_max_tokens,
            thinking_callback=on_thinking,
            on_metrics=on_metrics,
            on_metrics_reset=on_metrics_reset,
        )
    except Exception:
        logger.warning(
            "Phase 2 synthesis failed — falling back to raw exploration output",
            exc_info=True,
        )
        return None, exploration_output

    logger.info(
        "Phase 2 synthesis: modify=%d create=%d reference=%d missing=%d refs=%d assumptions=%d",
        len(summary.files_to_modify),
        len(summary.files_to_create),
        len(summary.files_read_for_context),
        len(summary.missing_infrastructure),
        len(summary.verified_references),
        len(summary.assumptions_resolved),
    )

    return summary, _format_file_summary(summary)


def _format_file_summary(summary: FileSummary) -> str:
    """Render a FileSummary to the markdown shape downstream phases consume
    as ``{file_summary}`` — preserves the historical section names so Phase
    3 / Phase 4 prompts do not need to change.
    """
    lines: list[str] = []

    def _render_files(header: str, items: list) -> None:
        if not items:
            return
        lines.append(f"{header}:")
        for i, item in enumerate(items, 1):
            entry = f"{i}. {item.file_path} — {item.reason}"
            if item.relevant_sections:
                entry += f" — {item.relevant_sections}"
            lines.append(entry)
            for snippet in item.key_snippets:
                snippet = snippet.strip("\n")
                if not snippet:
                    continue
                lines.append("   ```")
                for snippet_line in snippet.splitlines():
                    lines.append(f"   {snippet_line}")
                lines.append("   ```")
        lines.append("")

    _render_files(
        "FILES TO MODIFY (include key content you read)",
        summary.files_to_modify,
    )
    _render_files(
        "FILES TO CREATE",
        summary.files_to_create,
    )
    _render_files(
        "FILES READ FOR CONTEXT (not modified, but content informs changes)",
        summary.files_read_for_context,
    )

    if summary.missing_infrastructure:
        lines.append("MISSING INFRASTRUCTURE (assumed by the task but not found):")
        for i, m in enumerate(summary.missing_infrastructure, 1):
            blocking = " [BLOCKING]" if m.blocking else ""
            lines.append(f"{i}. {m.name} — {m.reason}{blocking}")
        lines.append("")

    if summary.verified_references:
        lines.append("VERIFIED REFERENCES (from internet search):")
        for i, r in enumerate(summary.verified_references, 1):
            entry = f"{i}. {r.dependency} — {r.docs_url}"
            if r.version:
                entry += f" — version: {r.version}"
            if r.confirmed_patterns:
                entry += f" — {r.confirmed_patterns}"
            lines.append(entry)
        lines.append("")

    if summary.assumptions_resolved:
        lines.append("ASSUMPTIONS RESOLVED (from Phase 1 checklist):")
        for i, a in enumerate(summary.assumptions_resolved, 1):
            entry = f"{i}. [{a.status}] {a.assumption}"
            if a.evidence:
                entry += f" — {a.evidence}"
            lines.append(entry)
        lines.append("")

    if summary.notes.strip():
        lines.append("NOTES:")
        lines.append(summary.notes.strip())
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"

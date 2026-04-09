"""5-phase decomposed planning pipeline with structured output.

Phase 1: Scope analysis
Phase 2: File identification + content reading (with codebase exploration via tools)
Phase 3: Design + risk synthesis (change design, naming conventions, gap analysis)
Phase 4: Structured plan assembly (produces ExecutionPlan via chat_structured)
Phase 5: Verification step generation (test file creation + test execution)

Each phase is a focused LLM call. The planner uses read-only tools
(read_file, list_directory, directory_tree, grep_files) during Phase 2
to explore the codebase and read every file it plans to modify.
Phase 5 only runs when a test command is available.
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import WebSocket

from lean_ai.config import settings
from lean_ai.llm.plan_schema import (
    IMPLEMENTATION_STEP_TOOLS,
    ExecutionPlan,
    VerificationPlan,
    plan_to_markdown,
)
from lean_ai.llm.prompt_registry import registry
from lean_ai.llm.prompts import (
    CLARIFICATION_SYSTEM_PROMPT,
    PLAN_ASSEMBLY_SYSTEM_PROMPT,
    PLAN_DESIGN_SYSTEM_PROMPT,
    PLAN_EXPLORATION_SYSTEM_PROMPT,
    PLAN_SCOPE_SYSTEM_PROMPT,
    PLAN_VERIFICATION_SYSTEM_PROMPT,
)
from lean_ai.llm.tool_definitions import build_design_tools, build_planning_tools

if TYPE_CHECKING:
    from lean_ai.llm.client import LLMClient
    from lean_ai.llm.refiner import PromptRefiner
    from lean_ai.workflow.ws_dispatcher import WSMessageDispatcher

logger = logging.getLogger(__name__)

# Phase 4/5 produce structured JSON plans with enriched step instructions
# and context fields — give them 40% of the expert context window so
# detailed plans are not truncated (vs. the default 25% for general output).
PLAN_OUTPUT_PERCENT = 0.40


def _save_debug_phase(
    repo_root: str,
    session_id: str,
    phase_name: str,
    content: str,
    elapsed: float,
) -> None:
    """Save a planning phase output to disk when debug_planning is enabled."""
    if not settings.debug_planning or not session_id:
        return
    debug_dir = Path(repo_root) / ".lean_ai" / "plan_debug" / session_id
    debug_dir.mkdir(parents=True, exist_ok=True)
    (debug_dir / f"{phase_name}.md").write_text(content, encoding="utf-8")
    logger.info(
        "Debug: saved %s (%d chars, %.1fs)", phase_name, len(content), elapsed,
    )


def _extract_file_paths(scan_output: str, repo_root: str) -> list[str]:
    """Extract file paths from Phase 2a scan output.

    Looks for lines containing path-like strings (with / separators
    and common extensions).  Validates that each path exists on disk.
    """
    import re
    path_pattern = re.compile(
        r'(?:^|[\s`"\'\-•])([a-zA-Z0-9_.][a-zA-Z0-9_./\-]*'
        r'\.[a-zA-Z]{1,10})(?:[\s`"\'\-:,]|$)',
    )
    seen: set[str] = set()
    paths: list[str] = []
    for line in scan_output.splitlines():
        for match in path_pattern.finditer(line):
            candidate = match.group(1).strip()
            if candidate in seen or "/" not in candidate:
                continue
            full = Path(repo_root) / candidate
            if full.is_file():
                seen.add(candidate)
                paths.append(candidate)
    return paths


def _split_list(items: list, n: int) -> list[list]:
    """Split *items* into *n* roughly equal chunks."""
    if n <= 1:
        return [items]
    chunk_size = max(1, len(items) // n)
    chunks: list[list] = []
    for i in range(0, len(items), chunk_size):
        chunks.append(items[i:i + chunk_size])
    # Merge trailing runt into last real chunk
    while len(chunks) > n:
        chunks[-2].extend(chunks[-1])
        chunks.pop()
    return chunks


async def _send_stage(
    ws: WebSocket | None,
    summary: str,
    model: str | None = None,
    phase: int | None = None,
) -> None:
    """Send a planning stage_status running message if WebSocket is available."""
    if ws is None:
        return
    from lean_ai.workflow.ws_handler import ws_send
    payload: dict = {"stage": "planning", "status": "running", "summary": summary}
    if model:
        payload["model"] = model
    if phase is not None:
        payload["phase"] = phase
    await ws_send(ws, "stage_status", payload)


async def _send_stage_done(
    ws: WebSocket | None,
    summary: str,
    model: str | None = None,
    phase: int | None = None,
) -> None:
    """Send a planning stage_status done message if WebSocket is available."""
    if ws is None:
        return
    from lean_ai.workflow.ws_handler import ws_send
    payload: dict = {"stage": "planning", "status": "done", "summary": summary}
    if model:
        payload["model"] = model
    if phase is not None:
        payload["phase"] = phase
    await ws_send(ws, "stage_status", payload)


async def _send_content_done(
    ws: WebSocket | None,
    text: str,
) -> None:
    """Signal that content streaming for a planning phase is complete."""
    if ws is None:
        return
    from lean_ai.workflow.ws_handler import ws_send_nowait
    ws_send_nowait(ws, "assistant_content", {"content": text, "done": True})


async def _extract_missing_files(
    risks: str,
    llm_client: "LLMClient",
) -> str:
    """Extract the missing file list from the gap analysis output.

    Returns a short bullet list of files that the gap analysis identified
    as required but missing from the plan, or empty string if none.
    """
    result = await llm_client.chat_raw(
        messages=[
            {
                "role": "user",
                "content": (
                    "From the risk assessment below, extract ONLY the "
                    "files identified as MISSING — files that are REQUIRED "
                    "for the app to work at runtime but are NOT yet in the "
                    "change design.\n\n"
                    "Output a simple numbered list:\n"
                    "1. file/path — what it is and why it is needed\n\n"
                    "If no missing files were identified, output: NONE\n\n"
                    f"RISK ASSESSMENT:\n{risks}"
                ),
            },
        ],
        max_tokens=1024,
    )
    stripped = result.strip()
    if stripped.upper() == "NONE" or len(stripped) < 10:
        return ""
    return stripped


async def _compact_file_summary(
    file_summary: str,
    llm_client: "LLMClient",
    ctx_window: int,
) -> str:
    """Compact file_summary to fit within a token budget at small context windows.

    Uses the explorer (request) model — code understanding is needed here.
    Target budget: 20% of the context window in characters (~= tokens * 3.5).
    Returns the original if it already fits.
    """
    budget = int(ctx_window * 0.20 * 3.5)
    if len(file_summary) <= budget:
        return file_summary

    logger.info(
        "Compacting file_summary: %d chars -> target %d chars",
        len(file_summary), budget,
    )

    try:
        compacted = await llm_client.chat_raw(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Compress the codebase exploration output below. "
                        "Preserve ALL of the following:\n"
                        "- File paths and whether they are create/edit/reference\n"
                        "- Function/class signatures with parameters\n"
                        "- Import statements\n"
                        "- VERIFIED REFERENCES and MISSING INFRASTRUCTURE sections\n"
                        "- Key constants and configuration values\n\n"
                        "Remove:\n"
                        "- Inline code blocks longer than 5 lines\n"
                        "- Verbose explanations of file purposes\n"
                        "- Redundant observations\n\n"
                        "Output the compressed version directly."
                    ),
                },
                {"role": "user", "content": file_summary[:budget * 3]},
            ],
            max_tokens=2048,
        )
        if compacted and len(compacted.strip()) > 200:
            logger.info(
                "File summary compacted: %d -> %d chars (%.0f%%)",
                len(file_summary), len(compacted),
                len(compacted) / len(file_summary) * 100,
            )
            return compacted.strip()
    except Exception:
        logger.warning(
            "File summary compaction failed, using truncation fallback",
            exc_info=True,
        )

    # Fallback: hard truncate
    return file_summary[:budget] + "\n... (truncated to fit context budget)"


async def assess_clarity(
    task: str,
    llm_client: "LLMClient",
    context: str = "",
) -> list[str] | None:
    """Assess whether a task is clear enough to plan.

    Returns None if the task is clear, or a list of clarifying questions.
    """
    logger.info("Assessing task clarity")

    response = await llm_client.chat_raw(
        messages=[
            {"role": "system", "content": CLARIFICATION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"TASK:\n{task}\n\n"
                    f"PROJECT CONTEXT:\n{context[:5000]}\n\n"
                    "Is this task clear enough to create a detailed "
                    "implementation plan?"
                ),
            },
        ],
        max_tokens=1024,
    )

    stripped = response.strip()
    upper = stripped.upper()
    if upper.startswith("CLEAR") or upper in ("OK", "YES", "N/A", "NONE", "-"):
        return None

    # Try to parse as JSON array of questions
    try:
        questions = json.loads(stripped)
        if isinstance(questions, list) and all(isinstance(q, str) for q in questions):
            return questions[:5]
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: extract lines that look like questions
    lines = [
        ln.strip().lstrip("- ").lstrip("0123456789.)")
        for ln in stripped.splitlines()
        if ln.strip() and "?" in ln
    ]
    if lines:
        return lines[:5]
    # Short non-question responses (e.g. "-", "OK", "N/A") = task is clear
    if len(stripped) < 20 and "?" not in stripped:
        logger.info("Treating short non-question response as CLEAR: %r", stripped)
        return None
    return [stripped]


async def create_plan(
    task: str,
    repo_root: str,
    llm_client: "LLMClient",
    context: str = "",
    revision_context: str | None = None,
    ws: WebSocket | None = None,
    dispatcher: "WSMessageDispatcher | None" = None,
    refiner: "PromptRefiner | None" = None,
    test_command: str = "",
    session_id: str = "",
    expert_llm_client: "LLMClient | None" = None,
    request_llm_client: "LLMClient | None" = None,
    on_content: "Callable | None" = None,
    on_thinking: "Callable | None" = None,
    on_tool_call: "Callable | None" = None,
    on_tool_result: "Callable | None" = None,
    on_metrics: "Callable | None" = None,
) -> ExecutionPlan:
    """Create a plan using 5-phase decomposed planning.

    Args:
        task: The user's task description (may include clarification answers).
        repo_root: Path to the repository root.
        llm_client: LLM client for making calls.
        context: Pre-assembled context (project context, search results, etc.).
        revision_context: If revising, the previous plan JSON + user feedback.
        ws: Optional WebSocket for streaming stage progress.
        refiner: Optional local refiner for privacy-stripping file summaries.
        test_command: If set, planner includes test creation steps.
        request_llm_client: Optional request model for phases 1-2 (codebase
            exploration). Falls back to llm_client when not configured.
        on_content: Streaming callback for content tokens.
        on_thinking: Streaming callback for thinking tokens.
        on_tool_call: Callback for tool call events (phase 2).
        on_tool_result: Callback for tool result events (phase 2).
        on_metrics: Callback for metrics updates (phase 2).

    Returns:
        Structured ExecutionPlan ready for per-step execution.
    """
    if revision_context:
        return await _revise_plan(
            task, revision_context, llm_client, context, ws,
            expert_llm_client=expert_llm_client,
            on_thinking=on_thinking,
        )

    # Explorer client for phases 1-2 (scope + file identification),
    # falls back to primary when no request model is configured
    explorer = request_llm_client or llm_client
    phase_max_tokens = (
        settings.effective_request_max_tokens
        if request_llm_client
        else settings.ollama_max_tokens
    )

    # Expert client for reasoning-heavy phases (3-5), falls back to standard
    expert = expert_llm_client or llm_client
    expert_max_tokens = (
        settings.effective_expert_max_tokens
        if expert_llm_client
        else phase_max_tokens
    )

    expert_ctx = (
        settings.effective_expert_context_window
        if expert_llm_client
        else settings._active_context_window
    )
    plan_assembly_max_tokens = max(
        expert_max_tokens,
        int(expert_ctx * PLAN_OUTPUT_PERCENT),
    )
    plan_start = time.monotonic()
    phase_timings: dict[str, float] = {}

    # Expert phases (3, 4) need project context for architectural awareness.
    # Reuse the context parameter (already loaded by the workflow router)
    # rather than re-reading from disk.
    project_context = context

    # Phase 1: Scope Analysis
    await _send_stage(ws, "Phase 1: Analyzing scope...", model=explorer.model_name, phase=1)
    logger.info("Planning Phase 1: Scope analysis (model=%s)", explorer.model_name)
    t0 = time.monotonic()
    scope = await explorer.chat_raw(
        messages=[
            {"role": "system", "content": PLAN_SCOPE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": registry.format(
                    "planning.scope_user", task=task, context=context,
                ),
            },
        ],
        max_tokens=phase_max_tokens,
        stream_callback=on_content,
        thinking_callback=on_thinking,
    )
    if on_content:
        await _send_content_done(ws, scope)

    phase_timings["phase_1_scope"] = time.monotonic() - t0
    _save_debug_phase(
        repo_root, session_id, "phase_1_scope", scope, phase_timings["phase_1_scope"],
    )
    await _send_stage_done(ws, "Scope analysis complete", model=explorer.model_name, phase=1)

    # Phase 2: File Identification + Content Reading (with tool access)
    await _send_stage(
        ws, "Phase 2: Exploring codebase and reading files...",
        model=explorer.model_name, phase=2,
    )
    logger.info("Planning Phase 2: File identification and reading")
    t0 = time.monotonic()
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

    # Let the LLM explore with read-only tools — generous budget
    # Tighter tool limits at small context windows to prevent Phase 2
    # from filling the conversation before exploration is complete.
    _small_ctx = settings._active_context_window <= 32768

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
                    return "ERROR: Access to external file not approved by user"
            # At small windows, cap visible range to 200 lines
            end_line = arguments.get("end_line")
            if _small_ctx and end_line is None:
                start = arguments.get("start_line") or 1
                end_line = start + 199
            result = await read_file(
                path=target_path,
                repo_root=repo_root,
                start_line=arguments.get("start_line"),
                end_line=end_line,
                allow_external=external,
            )
            return result.output if result.success else result.error or "Error"
        elif name == "grep_files":
            result = await grep_files(
                pattern=arguments.get("pattern", ""),
                repo_root=repo_root,
                file_glob=arguments.get("file_glob"),
                max_results=30 if _small_ctx else None,
            )
            return result.output if result.success else result.error or "Error"
        elif name == "list_directory":
            from pathlib import Path
            target = Path(repo_root) / arguments.get("path", "")
            if not target.is_dir():
                return f"Not a directory: {arguments.get('path', '')}"
            default_max = 50 if _small_ctx else 100
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
            tree_root = f"{repo_root}/{sub_path}" if sub_path else repo_root
            entries = list_repo_tree(tree_root)
            max_depth = arguments.get("max_depth", 3)
            max_tree_entries = 100 if _small_ctx else 200
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
            return result.output if result.success else result.error or "Error"
        elif name == "fetch_url":
            from lean_ai.tools.internet import fetch_url
            result = await fetch_url(
                url=arguments.get("url", ""),
                repo_root=repo_root,
                llm_client=explorer,
            )
            return result.output if result.success else result.error or "Error"
        elif name == "task_complete":
            return "Exploration marked complete."
        return f"Unknown tool: {name}"

    if settings.num_parallel >= 2:
        # ── Parallel Phase 2: fan-out then merge ──────────────────
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
            tool_executor_fn=_read_only_executor,
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
        logger.info(
            "Phase 2a scan identified %d file paths", len(file_paths),
        )

        if file_paths:
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
                    tool_executor_fn=_read_only_executor,
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
            file_identification = (
                scan_output + "\n\n"
                + "\n\n".join(dive_results)
            )
        else:
            file_identification = scan_output

    else:
        # ── Serial Phase 2 (num_parallel=1) ───────────────────────
        tool_calls, file_identification = await explorer.chat_with_tools(
            messages=phase2_messages,
            tools=build_planning_tools(),
            tool_executor_fn=_read_only_executor,
            max_turns=settings.implementation_max_turns,
            max_tokens=phase_max_tokens,
            task_reminder=registry.format("planning.task_reminder", task=task),
            reminder_interval=15,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            on_content=on_content,
            on_thinking=on_thinking,
            on_metrics=on_metrics,
        )

    phase_timings["phase_2_file_identification"] = time.monotonic() - t0
    _save_debug_phase(
        repo_root, session_id, "phase_2_file_identification",
        file_identification, phase_timings["phase_2_file_identification"],
    )
    await _send_stage_done(
        ws, "Codebase exploration complete", model=explorer.model_name, phase=2,
    )

    # Pass exploration results directly to downstream phases
    file_summary = file_identification

    # Privacy pass: strip sensitive data from file summary before
    # it enters Phases 3-5 (which may run on a cloud provider)
    if refiner and refiner.is_active:
        file_summary, redactions = await refiner.strip_privacy(file_summary)
        if redactions:
            logger.info(
                "Privacy: stripped %d items from file summary", len(redactions),
            )

    # Compact file_summary at small context windows to prevent Phase 3/4 overflow
    if settings._active_context_window <= 32768:
        file_summary = await _compact_file_summary(
            file_summary, explorer, settings._active_context_window,
        )

    # Phase 3: Design + Risk Synthesis
    if expert_llm_client:
        await _send_stage(
            ws,
            f"Switching to expert model ({expert_llm_client.model_name}) "
            f"for design phases...",
            model=expert_llm_client.model_name,
        )
        logger.info(
            "Switching to expert model for phases 3-5: %s",
            expert_llm_client.model_name,
        )
    await _send_stage(
        ws, "Phase 3: Designing changes and assessing risks...",
        model=expert.model_name, phase=3,
    )
    logger.info("Planning Phase 3: Design + risk synthesis")
    t0 = time.monotonic()

    phase3_messages = [
        {"role": "system", "content": PLAN_DESIGN_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": registry.format(
                "planning.design_user",
                task=task,
                scope=scope,
                project_context=(
                    f"PROJECT CONTEXT:\n{project_context}\n\n"
                    if project_context else ""
                ),
                file_summary=file_summary,
            ),
        },
    ]

    # Search-only tool executor for Phase 3 design synthesis
    async def _search_only_executor(name: str, arguments: dict) -> str:
        """Execute search tools for Phase 3 design verification."""
        if name == "search_internet":
            from lean_ai.tools.internet import search_internet
            result = await search_internet(
                query=arguments.get("query", ""),
                llm_client=expert,
            )
            return result.output if result.success else result.error or "Error"
        elif name == "fetch_url":
            from lean_ai.tools.internet import fetch_url
            result = await fetch_url(
                url=arguments.get("url", ""),
                repo_root=repo_root,
                llm_client=expert,
            )
            return result.output if result.success else result.error or "Error"
        elif name == "task_complete":
            return "Design synthesis marked complete."
        return f"Unknown tool: {name}"

    # Use chat_with_tools so the expert can search the internet to verify
    # external frameworks, APIs, and patterns during design.
    # text_only_exit_count=1 preserves single-shot behavior when no search
    # is needed — the model exits immediately on the first text response.
    _phase3_tool_calls, design_and_risks = await expert.chat_with_tools(
        messages=phase3_messages,
        tools=build_design_tools(),
        tool_executor_fn=_search_only_executor,
        max_turns=15,
        max_tokens=expert_max_tokens,
        text_only_exit_count=1,
        on_tool_call=on_tool_call,
        on_tool_result=on_tool_result,
        on_content=on_content,
        on_thinking=on_thinking,
        on_metrics=on_metrics,
    )
    if on_content:
        await _send_content_done(ws, design_and_risks)

    phase_timings["phase_3_design_and_risks"] = time.monotonic() - t0
    _save_debug_phase(
        repo_root, session_id, "phase_3_design_and_risks",
        design_and_risks, phase_timings["phase_3_design_and_risks"],
    )
    await _send_stage_done(
        ws, "Design and risk synthesis complete", model=expert.model_name, phase=3,
    )

    # Extract missing files from gap analysis for explicit injection into Phase 4
    missing_files = await _extract_missing_files(design_and_risks, expert)
    if missing_files:
        logger.info("Extracted %d chars of missing files from Phase 3", len(missing_files))

    # Phase 4: Structured Plan Assembly
    await _send_stage(
        ws, "Phase 4: Assembling structured plan...", model=expert.model_name, phase=4,
    )
    logger.info("Planning Phase 4: Structured plan assembly")
    t0 = time.monotonic()

    # At small context windows, design_and_risks already synthesized scope
    # and project_context — drop redundant re-injection to save tokens.
    phase4_scope = scope
    phase4_project_context = project_context
    if expert_ctx <= 32768:
        phase4_scope = ""
        phase4_project_context = ""
        logger.info(
            "Phase 4: small context window (%d) — dropping scope and "
            "project_context re-injection (already in design_and_risks)",
            expert_ctx,
        )

    plan = await expert.chat_structured(
        messages=[
            {"role": "system", "content": PLAN_ASSEMBLY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": registry.format(
                    "planning.assembly_user",
                    task=task,
                    design_and_risks=design_and_risks,
                    file_summary=file_summary,
                    project_context=(
                        f"PROJECT CONTEXT:\n{phase4_project_context}\n\n"
                        if phase4_project_context else ""
                    ),
                    scope=phase4_scope,
                    missing_files=(
                        "REQUIRED MISSING FILES — these were identified "
                        "during risk assessment as files that MUST exist "
                        "for the app to work. Each one MUST have a "
                        "corresponding create_file or edit_file step in "
                        f"the plan:\n{missing_files}\n\n"
                        if missing_files else ""
                    ),
                ),
            },
        ],
        schema=ExecutionPlan,
        max_tokens=plan_assembly_max_tokens,
        thinking_callback=on_thinking,
    )

    phase_timings["phase_4_plan_assembly"] = time.monotonic() - t0

    # Safety: strip exploration tools (list_directory, grep_files, etc.)
    # that have no business in an implementation plan.
    impl_steps = [s for s in plan.steps if s.tool in IMPLEMENTATION_STEP_TOOLS]
    stripped_count = len(plan.steps) - len(impl_steps)
    if stripped_count:
        stripped_tools = [s.tool for s in plan.steps if s.tool not in IMPLEMENTATION_STEP_TOOLS]
        logger.warning(
            "Stripped %d non-implementation steps from Phase 4 plan: %s",
            stripped_count, stripped_tools,
        )
        for i, step in enumerate(impl_steps, 1):
            step.step_number = i
        plan.steps = impl_steps
    if not plan.steps and stripped_count:
        logger.error(
            "Phase 4 produced zero implementation steps — all %d steps "
            "were exploration/verification tools. file_summary may be "
            "insufficient.",
            stripped_count,
        )

    # Warn if plan has steps but none are actual implementation
    has_implementation = any(
        s.tool in ("create_file", "edit_file") for s in plan.steps
    )
    if plan.steps and not has_implementation:
        logger.warning(
            "Phase 4 plan has %d steps but none are create_file or "
            "edit_file — plan may be exploration-only. Tools: %s",
            len(plan.steps),
            [s.tool for s in plan.steps],
        )

    # Save Phase 4 outputs
    _save_debug_phase(
        repo_root, session_id, "phase_4_plan",
        plan.model_dump_json(indent=2), phase_timings["phase_4_plan_assembly"],
    )
    _save_debug_phase(
        repo_root, session_id, "phase_4_plan_markdown",
        plan_to_markdown(plan), phase_timings["phase_4_plan_assembly"],
    )

    await _send_stage_done(
        ws,
        f"Plan assembled — {len(plan.steps)} steps across "
        f"{len(plan.affected_files)} file(s)",
        model=expert.model_name, phase=4,
    )

    # Phase 5: Verification (only when test_command is available)
    # Reviews the complete implementation plan and appends test file
    # creation + test execution steps at the end.  Tests should only
    # run after all implementation files are created — running them
    # mid-plan on an incomplete codebase would cause premature failures.
    #
    # In TDD mode, test steps are separated into tdd_test_steps and
    # executed first by the expert model during pipeline execution.
    if test_command:
        tdd_mode = settings.enable_tdd
        phase_label = (
            "Phase 5: Designing TDD test steps..."
            if tdd_mode
            else "Phase 5: Adding verification steps..."
        )
        await _send_stage(ws, phase_label, model=expert.model_name, phase=5)
        logger.info("Planning Phase 5: Verification step generation (tdd=%s)", tdd_mode)
        t0 = time.monotonic()

        impl_plan_md = plan_to_markdown(plan, include_context=False)
        next_step = len(plan.steps) + 1

        # TDD-specific guidance for comprehensive, well-documented tests
        tdd_guidance = ""
        if tdd_mode:
            tdd_guidance = (
                "\n\nTDD MODE — These tests will be written and executed "
                "BEFORE any implementation code exists. Write tests that:\n"
                "- Test PUBLIC interfaces and contracts, not internal "
                "implementation details\n"
                "- Import from the paths that WILL exist after "
                "implementation (based on the plan)\n"
                "- Use clear, descriptive assertion messages so failures "
                "guide the implementor toward the correct solution\n"
                "- Do NOT depend on implementation order — each test "
                "file must be independently valid\n"
                "- Mock external dependencies (DB, HTTP, filesystem) at "
                "the boundary\n\n"
                "DOCUMENTATION REQUIREMENTS (mandatory for TDD):\n"
                "- Module-level docstring explaining what feature/module "
                "is under test\n"
                "- Per-test-function docstring with: what behavior is "
                "tested, expected input/output, why this case matters\n"
                "- Descriptive assertion messages in assert statements "
                "so failures immediately tell the implementor what went "
                "wrong\n"
                "- Comments on non-obvious setup/mocking explaining "
                "what boundary is being mocked and why\n\n"
                "Do NOT include a run_tests step — tests will be "
                "executed after implementation by the pipeline.\n"
            )

        verification = await expert.chat_structured(
            messages=[
                {"role": "system", "content": PLAN_VERIFICATION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"TASK: {task}\n\n"
                        f"IMPLEMENTATION PLAN:\n{impl_plan_md}\n\n"
                        f"TEST COMMAND: {test_command}\n\n"
                        f"FILE SUMMARY (existing test patterns):\n"
                        f"{file_summary}\n\n"
                        "Review the implementation plan above and produce "
                        "ONLY the verification steps that should run AFTER "
                        "all implementation is complete.\n\n"
                        "RULES:\n"
                        "- For each new module or significant feature, "
                        "include a 'create_file' step for a test file.\n"
                        "- Only create tests for NEW functionality — do "
                        "not duplicate existing test coverage.\n"
                        + (
                            ""
                            if tdd_mode
                            else (
                                "- End with a single 'run_tests' step using "
                                f"the test command: {test_command}\n"
                            )
                        )
                        + f"- Start step numbering at {next_step}\n"
                        "- Follow the naming conventions from the plan\n\n"
                        "TEST FILE STEP — REQUIRED CONTENT IN `instruction`:\n"
                        "List each test function by name with the specific "
                        "assertion it makes, e.g.:\n"
                        "  test_valid_input_returns_id: "
                        "assert result['id'] is not None\n"
                        "  test_empty_name_raises: "
                        "pytest.raises(ValueError, match='required')\n"
                        "  test_duplicate_rejected: "
                        "second call raises IntegrityError\n\n"
                        "Cover ALL applicable categories:\n"
                        "  HAPPY PATH   — primary use case, expected "
                        "inputs → correct outputs\n"
                        "  EDGE CASES   — None, empty str/list/dict, zero, "
                        "boundary values, unicode, strings > 10 000 chars\n"
                        "  ERROR PATHS  — each invalid input raises the "
                        "correct exception type; assert the message text, "
                        "not just the type\n"
                        "  INTEGRATION  — mock external I/O (DB, HTTP, "
                        "filesystem) and verify the component's contract "
                        "with its direct callers\n"
                        "  SECURITY     — required when the code handles:\n"
                        "    · file paths   : '../../../etc/passwd' is "
                        "rejected or sandboxed\n"
                        "    · shell input  : ';rm -rf /' and '$(id)' do "
                        "not execute\n"
                        "    · user strings written to DB/files: "
                        "injection payloads\n"
                        "    · auth/authz   : unauthenticated → 401/403, "
                        "not 500; insufficient privilege → 403\n"
                        "    · resource size: inputs > configured limit "
                        "are bounded, not crashed\n\n"
                        "The `context` field must include the relevant "
                        "existing test file content (imports, fixtures, "
                        "assertion style) so the executor can replicate "
                        "the pattern without reading additional files."
                        + tdd_guidance
                    ),
                },
            ],
            schema=VerificationPlan,
            max_tokens=plan_assembly_max_tokens,
            thinking_callback=on_thinking,
        )

        if tdd_mode:
            # TDD: keep test steps separate for expert-first execution.
            # Filter out run_tests steps (tests will intentionally fail
            # without implementation; post-validation handles execution).
            test_steps_only = [
                s for s in verification.steps if s.tool != "run_tests"
            ]
            for i, step in enumerate(test_steps_only, 1):
                step.step_number = i
            plan.tdd_test_steps = test_steps_only

            # Re-number implementation steps starting after test steps
            offset = len(test_steps_only)
            for i, step in enumerate(plan.steps, offset + 1):
                step.step_number = i
        else:
            # Normal mode: append to plan as before
            for i, step in enumerate(verification.steps, next_step):
                step.step_number = i
            plan.steps.extend(verification.steps)

        # Update affected_files with any new test files
        all_verification_steps = (
            plan.tdd_test_steps if tdd_mode else verification.steps
        )
        existing = set(plan.affected_files)
        for step in all_verification_steps:
            if step.file_path and step.file_path not in existing:
                plan.affected_files.append(step.file_path)

        phase_timings["phase_5_verification"] = time.monotonic() - t0
        _save_debug_phase(
            repo_root, session_id, "phase_5_verification",
            verification.model_dump_json(indent=2),
            phase_timings["phase_5_verification"],
        )
        test_steps = len(all_verification_steps)
        stage_msg = (
            f"TDD test steps designed — {test_steps} step(s)"
            if tdd_mode
            else f"Verification steps added — {test_steps} test step(s)"
        )
        await _send_stage_done(ws, stage_msg, model=expert.model_name, phase=5)

    phase_timings["total"] = time.monotonic() - plan_start

    # Save meta.json
    if settings.debug_planning and session_id:
        meta = {
            "session_id": session_id,
            "task": task,
            "timings": phase_timings,
            "steps": len(plan.steps),
            "affected_files": len(plan.affected_files),
        }
        debug_dir = Path(repo_root) / ".lean_ai" / "plan_debug" / session_id
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / "meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8",
        )

    logger.info(
        "Plan created: %d steps, %d affected files",
        len(plan.steps), len(plan.affected_files),
    )
    return plan


async def _revise_plan(
    task: str,
    revision_context: str,
    llm_client: "LLMClient",
    context: str = "",
    ws: WebSocket | None = None,
    expert_llm_client: "LLMClient | None" = None,
    on_thinking: "Callable | None" = None,
) -> ExecutionPlan:
    """Revise an existing plan based on user feedback.

    Args:
        task: The original task.
        revision_context: Previous plan JSON + user feedback.
        llm_client: LLM client.
        context: Project context.
        ws: Optional WebSocket for progress.
        expert_llm_client: Optional expert LLM client for reasoning-heavy work.

    Returns:
        Revised ExecutionPlan.
    """
    expert = expert_llm_client or llm_client
    expert_max_tokens = (
        settings.effective_expert_max_tokens
        if expert_llm_client
        else settings.ollama_max_tokens
    )
    expert_ctx = (
        settings.effective_expert_context_window
        if expert_llm_client
        else settings._active_context_window
    )
    plan_max_tokens = max(
        expert_max_tokens,
        int(expert_ctx * PLAN_OUTPUT_PERCENT),
    )
    await _send_stage(
        ws, "Revising plan based on feedback...", model=expert.model_name,
    )
    logger.info("Plan revision")
    plan = await expert.chat_structured(
        messages=[
            {"role": "system", "content": PLAN_ASSEMBLY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"TASK: {task}\n\n"
                    f"CODEBASE CONTEXT:\n{context}\n\n"
                    f"REVISION CONTEXT:\n{revision_context}\n\n"
                    "Revise the plan based on the user's feedback. "
                    "Make targeted edits — don't rewrite from scratch. "
                    "Keep the same structured format with step_number, tool, "
                    "file_path, instruction, and context fields."
                ),
            },
        ],
        schema=ExecutionPlan,
        max_tokens=plan_max_tokens,
        thinking_callback=on_thinking,
    )
    # Safety: strip non-implementation steps (same as Phase 4)
    impl_steps = [s for s in plan.steps if s.tool in IMPLEMENTATION_STEP_TOOLS]
    if len(impl_steps) < len(plan.steps):
        stripped_count = len(plan.steps) - len(impl_steps)
        stripped_tools = [s.tool for s in plan.steps if s.tool not in IMPLEMENTATION_STEP_TOOLS]
        logger.warning(
            "Stripped %d non-implementation steps from revised plan: %s",
            stripped_count, stripped_tools,
        )
        for i, step in enumerate(impl_steps, 1):
            step.step_number = i
        plan.steps = impl_steps
    logger.info(
        "Plan revised: %d steps, %d affected files",
        len(plan.steps), len(plan.affected_files),
    )
    return plan

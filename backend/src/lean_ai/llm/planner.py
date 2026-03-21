"""6-phase decomposed planning pipeline with structured output.

Phase 1: Scope analysis
Phase 2: File identification + content reading (with codebase exploration via tools)
Phase 3: Change design (specific changes per file, using exploration results)
Phase 4: Risk assessment
Phase 5: Structured plan assembly (produces ExecutionPlan via chat_structured)
Phase 6: Verification step generation (test file creation + test execution)

Each phase is a focused LLM call. The planner uses read-only tools
(read_file, list_directory, directory_tree, grep_files) during Phase 2
to explore the codebase and read every file it plans to modify.
Phase 6 only runs when a test command is available.
"""

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import WebSocket

from lean_ai.config import settings
from lean_ai.llm.plan_schema import (
    ExecutionPlan,
    PlanStep,
    VerificationPlan,
    plan_to_markdown,
)
from lean_ai.llm.prompts import CLARIFICATION_SYSTEM_PROMPT, PLAN_SYSTEM_PROMPT
from lean_ai.llm.tool_definitions import PLANNING_TOOLS

if TYPE_CHECKING:
    from lean_ai.llm.client import LLMClient
    from lean_ai.llm.refiner import PromptRefiner

logger = logging.getLogger(__name__)

# Phase 5/6 produce structured JSON plans with enriched step instructions
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


async def _send_stage(
    ws: WebSocket | None,
    summary: str,
    model: str | None = None,
) -> None:
    """Send a planning stage_status running message if WebSocket is available."""
    if ws is None:
        return
    from lean_ai.workflow.ws_handler import ws_send
    payload: dict = {"stage": "planning", "status": "running", "summary": summary}
    if model:
        payload["model"] = model
    await ws_send(ws, "stage_status", payload)


async def _send_stage_done(
    ws: WebSocket | None,
    summary: str,
    model: str | None = None,
) -> None:
    """Send a planning stage_status done message if WebSocket is available."""
    if ws is None:
        return
    from lean_ai.workflow.ws_handler import ws_send
    payload: dict = {"stage": "planning", "status": "done", "summary": summary}
    if model:
        payload["model"] = model
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
    """Extract the missing file list from Phase 4 risks output.

    Returns a short bullet list of files that Phase 4 identified as
    required but missing from the plan, or empty string if none.
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
    if stripped.upper().startswith("CLEAR"):
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
    return lines[:5] if lines else [stripped]


async def create_plan(
    task: str,
    repo_root: str,
    llm_client: "LLMClient",
    context: str = "",
    revision_context: str | None = None,
    ws: WebSocket | None = None,
    refiner: "PromptRefiner | None" = None,
    test_command: str = "",
    session_id: str = "",
    expert_llm_client: "LLMClient | None" = None,
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

    phase_max_tokens = settings.ollama_max_tokens

    # Expert client for reasoning-heavy phases (3-6), falls back to standard
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

    # Phase 1: Scope Analysis
    await _send_stage(ws, "Phase 1: Analyzing scope...", model=llm_client.model_name)
    logger.info("Planning Phase 1: Scope analysis")
    t0 = time.monotonic()
    scope = await llm_client.chat_raw(
        messages=[
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"TASK: {task}\n\n"
                    f"CODEBASE CONTEXT:\n{context}\n\n"
                    "Analyze the scope of this task. In 300-500 words, describe:\n"
                    "- What needs to change\n"
                    "- **Data flow and consumers**: For every model, table, "
                    "schema, or data structure being modified, trace ALL "
                    "downstream consumers — controllers that query it, views "
                    "that display it, API resources/transformers that serialize "
                    "it, forms that accept input for it, tests that assert on "
                    "it. These consumers likely need updates too.\n"
                    "- What is out of scope\n"
                    "- Key assumptions\n"
                    "- Patterns to follow from the existing codebase\n\n"
                    "IMPORTANT: If the task mentions specific files to modify, "
                    "treat that list as a STARTING POINT, not an exhaustive "
                    "list. The codebase may have additional files that depend "
                    "on the changed data and need corresponding updates."
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
    await _send_stage_done(ws, "Scope analysis complete", model=llm_client.model_name)

    # Phase 2: File Identification + Content Reading (with tool access)
    await _send_stage(
        ws, "Phase 2: Exploring codebase and reading files...",
        model=llm_client.model_name,
    )
    logger.info("Planning Phase 2: File identification and reading")
    t0 = time.monotonic()
    phase2_messages = [
        {"role": "system", "content": PLAN_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"TASK: {task}\n\n"
                f"SCOPE ANALYSIS:\n{scope}\n\n"
                f"CODEBASE CONTEXT:\n{context}\n\n"
                "Identify EVERY file that needs to be created or modified.\n\n"
                "CRITICAL — TRACE ALL CONSUMERS:\n"
                "Before finalizing the file list, use grep_files to search for "
                "references to every model, class, table, route, or component "
                "being modified. For example, if you are modifying a Customer "
                "model, search for 'Customer' across the codebase to find "
                "controllers, views, API resources, form requests, and tests "
                "that reference it. Files that read or display the data you are "
                "changing almost certainly need updates too.\n\n"
                "Do NOT treat file lists in the task description as exhaustive. "
                "The task may only mention the data layer (models, migrations) "
                "but omit presentation layer files (controllers, views, API "
                "resources) that also need changes.\n\n"
                "EXPLORATION STEPS:\n"
                "1. Use grep_files to find all references to modified entities\n"
                "2. Use directory_tree / list_directory to understand project structure\n"
                "3. Use read_file to read the FULL CONTENT of every file you "
                "plan to modify — the content will be included in the plan so "
                "the executor can make accurate edits without re-reading\n"
                "4. Also read files that contain patterns the executor should "
                "follow when creating new files\n"
                "5. VERIFY ASSUMPTIONS: If the task depends on specific "
                "infrastructure (authentication, base templates/layouts, "
                "database setup, package dependencies), verify it actually "
                "exists in the codebase. Use grep_files and list_directory "
                "to check. If the task mentions features that require "
                "installed packages or scaffolding, search the dependency "
                "file (package.json, composer.json, Gemfile, requirements.txt, "
                "go.mod, Cargo.toml, etc.) to confirm they are present. "
                "List any assumed infrastructure that is MISSING — these "
                "gaps must be addressed in the plan.\n"
                "6. EXISTING STATE CHECK: For each entity the task introduces "
                "or modifies (database table, model, route group, config "
                "entry, migration), search the codebase to determine if it "
                "ALREADY EXISTS.\n"
                "   - For database tables: search for existing migrations "
                "that create or modify the table. Use grep_files to search "
                "migration files for the table name.\n"
                "   - For models/classes: check if a file already defines "
                "this class.\n"
                "   - For routes: check if the route/endpoint is already "
                "registered.\n"
                "   Note the results in the output — this determines whether "
                "the plan should CREATE new files or MODIFY existing ones, "
                "and whether database changes need a new CREATE migration or "
                "an ALTER/add-column migration.\n"
                "7. READ REGISTRATION FILES: Read the main files where new "
                "modules must be registered — route definition files, "
                "service container/DI config, middleware registration, "
                "plugin/module bootstrap files, package __init__.py files, "
                "etc. The executor needs to know WHERE and HOW to register "
                "new components. Include these files in your output under "
                "FILES READ FOR CONTEXT.\n\n"
                "EFFICIENCY: You can call multiple tools in a single response. "
                "For example, call read_file on several files at once instead "
                "of reading them one at a time.\n\n"
                "OUTPUT FORMAT:\n\n"
                "FILES TO MODIFY (include key content you read):\n"
                "1. path/to/file — reason — relevant sections read\n\n"
                "FILES TO CREATE:\n"
                "1. path/to/new/file — purpose — patterns to follow\n\n"
                "FILES READ FOR CONTEXT (not modified, but content informs changes):\n"
                "1. path/to/source — what it contains\n\n"
                "MISSING INFRASTRUCTURE (assumed by the task but not found):\n"
                "1. what is missing — why it is needed"
            ),
        },
    ]

    # Let the LLM explore with read-only tools — generous budget
    async def _read_only_executor(name: str, arguments: dict) -> str:
        """Execute read-only tools for planning phase."""
        from lean_ai.tools.file_ops import grep_files, read_file

        if name == "read_file":
            result = await read_file(
                path=arguments.get("path", ""),
                repo_root=repo_root,
                start_line=arguments.get("start_line"),
                end_line=arguments.get("end_line"),
            )
            return result.output if result.success else result.error or "Error"
        elif name == "grep_files":
            result = await grep_files(
                pattern=arguments.get("pattern", ""),
                repo_root=repo_root,
                file_glob=arguments.get("file_glob"),
            )
            return result.output if result.success else result.error or "Error"
        elif name == "list_directory":
            from pathlib import Path
            target = Path(repo_root) / arguments.get("path", "")
            if not target.is_dir():
                return f"Not a directory: {arguments.get('path', '')}"
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
        elif name == "task_complete":
            return "Exploration marked complete."
        return f"Unknown tool: {name}"

    tool_calls, file_identification = await llm_client.chat_with_tools(
        messages=phase2_messages,
        tools=PLANNING_TOOLS,
        tool_executor_fn=_read_only_executor,
        max_turns=settings.implementation_max_turns,
        max_tokens=phase_max_tokens,
        task_reminder=(
            f"REMINDER — You are exploring the codebase for this task: {task}\n\n"
            "Have you used grep_files to trace ALL consumers of modified "
            "entities? Do NOT finalize until you have searched for every "
            "model/class being changed and read every file that references it."
        ),
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
        ws, "Codebase exploration complete", model=llm_client.model_name,
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

    # Phase 3: Change Design
    if expert_llm_client:
        await _send_stage(
            ws,
            f"Switching to expert model ({expert_llm_client.model_name}) "
            f"for design phases...",
            model=expert_llm_client.model_name,
        )
        logger.info(
            "Switching to expert model for phases 3-6: %s",
            expert_llm_client.model_name,
        )
    await _send_stage(
        ws, "Phase 3: Designing specific changes...", model=expert.model_name,
    )
    logger.info("Planning Phase 3: Change design")
    t0 = time.monotonic()
    change_design = await expert.chat_raw(
        messages=[
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"TASK: {task}\n\n"
                    f"SCOPE:\n{scope}\n\n"
                    f"FILES IDENTIFIED AND READ:\n{file_summary}\n\n"
                    "First, list the NAMING CONVENTIONS observed in the "
                    "existing codebase. For EACH convention, include 2-3 "
                    "CONCRETE EXAMPLES from the files you read:\n"
                    "- Variable/property naming: <pattern> (examples from "
                    "code: <example1>, <example2>)\n"
                    "- Function/method naming: <pattern> (examples: "
                    "<example1>, <example2>)\n"
                    "- Class naming: <pattern> (examples: <example1>, "
                    "<example2>)\n"
                    "- File naming: <pattern> (examples: <example1>, "
                    "<example2>)\n"
                    "- Route/URL naming: <pattern> (examples: <example1>, "
                    "<example2>)\n"
                    "- Database table/collection naming: <pattern> (examples: "
                    "<example1>, <example2>)\n"
                    "- Import style and organization\n"
                    "Include ONLY conventions clearly demonstrated by the "
                    "existing code — do not guess. All new code MUST follow "
                    "these conventions.\n\n"
                    "Then, for each identified file, describe the SPECIFIC "
                    "changes:\n"
                    "- Functions/classes to add or modify (with signatures)\n"
                    "- What section of the file to modify (reference the content "
                    "from the file summary)\n"
                    "- Integration points with existing code\n"
                    "- For new files: structure, imports, patterns to follow "
                    "from existing files you read\n"
                    "- For test/lint commands: the exact command string\n\n"
                    "IMPORTANT: Describe changes at the DESIGN level — "
                    "method signatures, relationships, column types, route "
                    "patterns. Do NOT write full implementation code, complete "
                    "file contents, or HTML/template markup. The implementation "
                    "agent will write the actual code later. Keep each file's "
                    "description to 5-15 lines."
                ),
            },
        ],
        max_tokens=expert_max_tokens,
        stream_callback=on_content,
        thinking_callback=on_thinking,
    )
    if on_content:
        await _send_content_done(ws, change_design)

    phase_timings["phase_3_change_design"] = time.monotonic() - t0
    _save_debug_phase(
        repo_root, session_id, "phase_3_change_design",
        change_design, phase_timings["phase_3_change_design"],
    )
    await _send_stage_done(ws, "Change design complete", model=expert.model_name)

    # Phase 4: Risk Assessment
    await _send_stage(ws, "Phase 4: Assessing risks...", model=expert.model_name)
    logger.info("Planning Phase 4: Risk assessment")
    t0 = time.monotonic()
    risks = await expert.chat_raw(
        messages=[
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"TASK: {task}\n\n"
                    f"CHANGE DESIGN:\n{change_design}\n\n"
                    "Evaluate risks, failure modes, and edge cases:\n"
                    "- What could break?\n"
                    "- Security implications?\n"
                    "- Backward compatibility concerns?\n"
                    "- Rollback strategy?\n"
                    "- **Missing file coverage**: Are there files that are "
                    "REQUIRED for the planned features to actually work at "
                    "runtime that are NOT in the change design? Focus only on "
                    "files without which the app would crash or produce errors "
                    "— for example: base templates or layouts that views "
                    "inherit from, route definitions for new controllers, "
                    "configuration or registration files that must reference "
                    "new modules, database seed data registration. Do NOT "
                    "suggest optional architectural patterns (repositories, "
                    "services, events, jobs, helpers, decorators, etc.) "
                    "unless the task explicitly requests them."
                ),
            },
        ],
        max_tokens=expert_max_tokens,
        stream_callback=on_content,
        thinking_callback=on_thinking,
    )
    if on_content:
        await _send_content_done(ws, risks)

    phase_timings["phase_4_risks"] = time.monotonic() - t0
    _save_debug_phase(
        repo_root, session_id, "phase_4_risks",
        risks, phase_timings["phase_4_risks"],
    )
    await _send_stage_done(ws, "Risk assessment complete", model=expert.model_name)

    # Extract missing files from Phase 4 for explicit injection into Phase 5
    missing_files = await _extract_missing_files(risks, expert)
    if missing_files:
        logger.info("Extracted %d chars of missing files from Phase 4", len(missing_files))

    # Phase 5: Structured Plan Assembly
    await _send_stage(
        ws, "Phase 5: Assembling structured plan...", model=expert.model_name,
    )
    logger.info("Planning Phase 5: Structured plan assembly")
    t0 = time.monotonic()
    plan = await expert.chat_structured(
        messages=[
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    # -- U-curve optimized ordering --
                    # Start (high attention): task + risks (actionable)
                    # Middle (lower attention): reference material
                    # End (high attention): assembly rules + checklist
                    f"TASK: {task}\n\n"
                    f"RISKS AND GAPS:\n{risks}\n\n"
                    f"CHANGE DESIGN (includes naming conventions):\n"
                    f"{change_design}\n\n"
                    f"FILE SUMMARY:\n{file_summary}\n\n"
                    f"SCOPE:\n{scope}\n\n"
                    "Assemble the final execution plan as structured JSON. "
                    "Each step must represent ONE tool call.\n\n"
                    "NAMING CONVENTIONS: The change design above lists naming "
                    "conventions observed in the codebase. Extract them into "
                    "the 'naming_conventions' field of the plan. All step "
                    "instructions must follow these conventions.\n\n"
                    "NAME REGISTRY: Populate the 'name_registry' field with "
                    "every NEW entity introduced by this plan and its "
                    "canonical names across the full stack. Use this format:\n"
                    "Entity \"<Name>\":\n"
                    "  model/class: <ExactClassName>\n"
                    "  namespace/module: <exact.namespace.path>\n"
                    "  import: <exact import statement>\n"
                    "  table/collection: <exact_table_name>\n"
                    "  file: <exact/file/path>\n"
                    "  route/endpoint: </exact/route>\n"
                    "  registered in: <config/file1, routes/file2>\n"
                    "  test: <exact/test/file>\n"
                    "Include ONLY the rows that apply to each entity. The "
                    "namespace/module and import rows are critical — the "
                    "executor uses them for import statements in other files. "
                    "The 'registered in' row lists files where this entity "
                    "must be registered — each one must have a corresponding "
                    "edit_file step.\n\n"
                    "IMPORTANT: If the risk assessment identified missing files "
                    "(files that consume or display the modified data but were "
                    "not in the original change design), you MUST include steps "
                    "to update those files too. The plan must cover the full "
                    "data flow — not just the data layer.\n\n"
                    "RULES FOR STEPS:\n"
                    "- Use 'create_file' for new files, 'edit_file' for "
                    "modifications to existing files\n"
                    "- Do NOT include 'run_tests' or 'run_lint' steps — "
                    "verification will be appended automatically after "
                    "all implementation steps are complete\n"
                    "- For edit_file steps: the instruction field must specify: "
                    "(a) The exact location in the file (function name, class "
                    "name, or line range from the files read during exploration). "
                    "(b) What currently exists at that location. "
                    "(c) The exact new code to add — not 'add a route' but the "
                    "actual code like 'Route::resource(\"reviews\", "
                    "ReviewController::class)' or 'from app.models.review "
                    "import Review'. "
                    "(d) What surrounding code looks like (so the executor can "
                    "build accurate search blocks). "
                    "In the context field, include the relevant section of the "
                    "actual file content (10+ lines around the modification "
                    "point) as read during exploration.\n"
                    "- For create_file steps: the instruction field must be a "
                    "DETAILED SPECIFICATION, not a brief description. Include: "
                    "(a) The exact type of file (e.g., 'ALTER migration to add "
                    "columns' not just 'migration'; 'resource controller' not "
                    "just 'controller'). "
                    "(b) If this entity already exists (found during "
                    "exploration), state that explicitly: 'The reviews table "
                    "already exists (created by migration/2024_01_create_"
                    "reviews_table). This migration ADDS two columns to it.' "
                    "(c) The exact namespace/module path for the new file. "
                    "(d) Every import statement the file will need. "
                    "(e) For database files: every column with its type and "
                    "constraints (e.g., 'rating: integer, unsigned, nullable, "
                    "default 0'). "
                    "(f) For code files: method signatures with parameter types "
                    "and return types. "
                    "(g) The exact existing file whose pattern to follow, by "
                    "name. "
                    "In the context field, include: "
                    "(1) A substantial code snippet (15+ lines) from the "
                    "pattern file showing the exact structure to replicate — "
                    "imports, class declaration, key methods. Do NOT "
                    "abbreviate with '...'. "
                    "(2) All design details: column types with constraints, "
                    "method signatures, relationships/associations with exact "
                    "syntax. "
                    "(3) If modifying existing infrastructure (ALTER migration, "
                    "adding to existing route file): include what currently "
                    "exists so the executor knows the starting state. "
                    "Do NOT use generic template comments like "
                    "'// Example migration structure'. The executor model is "
                    "smaller and needs concrete details, not placeholders.\n"
                    "- Order steps so dependencies come first\n"
                    "- EXISTING INFRASTRUCTURE: If the file summary shows "
                    "that a database table, route group, or config entry "
                    "already exists, do NOT create duplicate infrastructure. "
                    "For database tables that already exist, create a NEW "
                    "migration that ALTERS the table (adds columns, indexes, "
                    "etc.) — do NOT create a second CREATE TABLE migration. "
                    "For route files that already exist, use edit_file to add "
                    "new routes to the existing file. For config files that "
                    "already exist, use edit_file.\n\n"
                    "EXAMPLE STEP (edit_file — add route registration):\n"
                    '{\n'
                    '  "step_number": 5,\n'
                    '  "tool": "edit_file",\n'
                    '  "file_path": "routes/web.php",\n'
                    '  "instruction": "Add review routes AFTER the existing '
                    "book routes (around line 34). Add exactly: "
                    "Route::resource('reviews', ReviewController::class); "
                    "Also add the import at the top of the file: "
                    "use App\\\\Http\\\\Controllers\\\\ReviewController; "
                    "The import should go after the existing BookController "
                    'import on line 8.",\n'
                    '  "context": "// Current routes/web.php lines 5-12 '
                    "and 30-36:\\nuse App\\\\Http\\\\Controllers\\\\"
                    "BookController;\\n// ...\\nRoute::resource('books', "
                    'BookController::class);"\n'
                    "}\n\n"
                    "EXAMPLE STEP (create_file — model with full spec):\n"
                    '{\n'
                    '  "step_number": 3,\n'
                    '  "tool": "create_file",\n'
                    '  "file_path": "app/Models/Review.php",\n'
                    '  "instruction": "Create the Review Eloquent model in '
                    "namespace App\\\\Models. Fillable: user_id (bigint FK), "
                    "book_id (bigint FK), rating (integer, unsigned), "
                    "review_text (text, nullable). Relationships: "
                    "user() belongsTo User::class, book() belongsTo "
                    "Book::class. Cast rating as integer. Imports: "
                    "Illuminate\\\\Database\\\\Eloquent\\\\Model, "
                    "Illuminate\\\\Database\\\\Eloquent\\\\Relations\\\\"
                    "BelongsTo. Follow the exact pattern from "
                    'app/Models/Book.php.",\n'
                    '  "context": "Pattern from app/Models/Book.php:\\n'
                    "<?php\\nnamespace App\\\\Models;\\n\\n"
                    "use Illuminate\\\\Database\\\\Eloquent\\\\Model;\\n"
                    "use Illuminate\\\\Database\\\\Eloquent\\\\Relations\\\\"
                    "HasMany;\\n\\nclass Book extends Model\\n{\\n"
                    "    protected $fillable = ['title', 'author', 'isbn'];"
                    "\\n\\n    protected $casts = ['published_at' => "
                    "'date'];\\n\\n    public function reviews(): HasMany\\n"
                    '    {\\n        return $this->hasMany(Review::class);'
                    '\\n    }\\n}"\n'
                    "}\n\n"
                    + (
                        "REQUIRED MISSING FILES — these were identified "
                        "during risk assessment as files that MUST exist "
                        "for the app to work. Each one MUST have a "
                        "corresponding create_file or edit_file step in "
                        f"the plan:\n{missing_files}\n\n"
                        if missing_files else ""
                    )
                    + "FINAL CHECKLIST — verify before producing the plan:\n"
                    "- Every file identified in the risk assessment as missing "
                    "is included as a step\n"
                    "- The plan covers the full data flow: "
                    "model -> controller -> view\n"
                    "- Each edit_file step has specific line references and "
                    "context\n"
                    "- Steps are ordered so dependencies come first\n"
                    "- All new names follow the naming conventions extracted "
                    "from existing code\n"
                    "- If any view or template inherits from a base/layout "
                    "template, there is a step to create that base template "
                    "if it does not already exist in the codebase\n"
                    "- Every route or URL endpoint has a corresponding handler "
                    "(controller, view function, etc.) that produces a response\n"
                    "- If new modules are created (seeders, plugins, middleware, "
                    "etc.), there is a step to register them in the appropriate "
                    "configuration or bootstrap file\n"
                    "- If the task requires infrastructure that was identified as "
                    "missing during exploration (auth scaffolding, base templates, "
                    "etc.), those setup steps come FIRST in the plan\n"
                    "- No duplicate infrastructure: if a table already exists, "
                    "the plan uses ALTER (not CREATE). If a route file exists, "
                    "the plan edits it (not creates a new one)\n"
                    "- Every new module/class has a registration step: if the "
                    "framework requires registration (routes, service providers, "
                    "middleware, plugins, __init__.py exports), there is an "
                    "edit_file step that adds the registration\n"
                    "- All import paths use the project's namespace/module "
                    "conventions (from the naming conventions)\n\n"
                    "USER SUMMARY — populate the `user_summary` field with up to "
                    "1000 words of plain English explaining: (1) what problem this "
                    "plan solves and the overall approach, (2) why specific "
                    "architectural decisions were made — what existing structures "
                    "are being extended and why, (3) any design trade-offs or "
                    "assumptions the user should be aware of before approving. "
                    "Write for a developer who may not know the codebase deeply but "
                    "needs to understand what load-bearing walls are being touched "
                    "and why. Do NOT list file paths or step numbers — describe "
                    "intent and reasoning in prose."
                ),
            },
        ],
        schema=ExecutionPlan,
        max_tokens=plan_assembly_max_tokens,
        thinking_callback=on_thinking,
    )

    phase_timings["phase_5_plan_assembly"] = time.monotonic() - t0

    # Safety: strip any run_tests/run_lint steps the LLM snuck into Phase 5
    verification_tools = {"run_tests", "run_lint", "format_code"}
    impl_steps = [s for s in plan.steps if s.tool not in verification_tools]
    stripped_count = len(plan.steps) - len(impl_steps)
    if stripped_count:
        logger.info("Stripped %d mid-plan verification steps", stripped_count)
        for i, step in enumerate(impl_steps, 1):
            step.step_number = i
        plan.steps = impl_steps

    # Dedup: if Phase 5 produces multiple steps for the same file path,
    # keep only the first one (e.g. edit_file then create_file for same path)
    seen_paths: set[str] = set()
    deduped: list[PlanStep] = []
    for step in plan.steps:
        if step.file_path and step.file_path in seen_paths:
            logger.info("Stripped duplicate step for %s", step.file_path)
            continue
        if step.file_path:
            seen_paths.add(step.file_path)
        deduped.append(step)
    if len(deduped) < len(plan.steps):
        for i, step in enumerate(deduped, 1):
            step.step_number = i
        plan.steps = deduped

    # Save Phase 5 outputs
    _save_debug_phase(
        repo_root, session_id, "phase_5_plan",
        plan.model_dump_json(indent=2), phase_timings["phase_5_plan_assembly"],
    )
    _save_debug_phase(
        repo_root, session_id, "phase_5_plan_markdown",
        plan_to_markdown(plan), phase_timings["phase_5_plan_assembly"],
    )

    await _send_stage_done(
        ws,
        f"Plan assembled — {len(plan.steps)} steps across "
        f"{len(plan.affected_files)} file(s)",
        model=expert.model_name,
    )

    # Phase 6: Verification (only when test_command is available)
    # Reviews the complete implementation plan and appends test file
    # creation + test execution steps at the end.  Tests should only
    # run after all implementation files are created — running them
    # mid-plan on an incomplete codebase would cause premature failures.
    if test_command:
        await _send_stage(
            ws, "Phase 6: Adding verification steps...", model=expert.model_name,
        )
        logger.info("Planning Phase 6: Verification step generation")
        t0 = time.monotonic()

        impl_plan_md = plan_to_markdown(plan, include_context=True)
        next_step = len(plan.steps) + 1

        verification = await expert.chat_structured(
            messages=[
                {"role": "system", "content": PLAN_SYSTEM_PROMPT},
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
                        "- End with a single 'run_tests' step using the "
                        f"test command: {test_command}\n"
                        f"- Start step numbering at {next_step}\n"
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
                    ),
                },
            ],
            schema=VerificationPlan,
            max_tokens=plan_assembly_max_tokens,
            thinking_callback=on_thinking,
        )

        # Append verification steps, re-number for safety
        for i, step in enumerate(verification.steps, next_step):
            step.step_number = i
        plan.steps.extend(verification.steps)

        # Update affected_files with any new test files
        existing = set(plan.affected_files)
        for step in verification.steps:
            if step.file_path and step.file_path not in existing:
                plan.affected_files.append(step.file_path)

        phase_timings["phase_6_verification"] = time.monotonic() - t0
        _save_debug_phase(
            repo_root, session_id, "phase_6_verification",
            verification.model_dump_json(indent=2),
            phase_timings["phase_6_verification"],
        )
        test_steps = len(verification.steps)
        await _send_stage_done(
            ws,
            f"Verification steps added — {test_steps} test step(s)",
            model=expert.model_name,
        )

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
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
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
    logger.info(
        "Plan revised: %d steps, %d affected files",
        len(plan.steps), len(plan.affected_files),
    )
    return plan

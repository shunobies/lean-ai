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
    DesignAndRisks,
    ExecutionPlan,
    FileSummary,
    MissingFile,
    VerificationPlan,
    plan_to_markdown,
)
from lean_ai.llm.planner_exploration import (
    _make_read_only_executor,
    run_phase2_exploration,
)
from lean_ai.llm.planner_helpers import (
    PLAN_OUTPUT_PERCENT,
    _compact_file_summary,
    _retrieve_session_memories,
    _revise_plan,
    _save_debug_phase,
    _send_content_done,
    _send_stage,
    _send_stage_done,
)
from lean_ai.llm.planner_helpers import (
    assess_clarity as assess_clarity,
)
from lean_ai.llm.prompt_registry import registry
from lean_ai.llm.prompts import (
    PLAN_ASSEMBLY_SYSTEM_PROMPT,
    PLAN_DESIGN_SYSTEM_PROMPT,
    PLAN_VERIFICATION_SYSTEM_PROMPT,
)
from lean_ai.llm.tool_definitions import build_design_tools, build_planning_tools

if TYPE_CHECKING:
    from lean_ai.llm.facade import LLMClient
    from lean_ai.llm.refiner import PromptRefiner
    from lean_ai.workflow.ws_dispatcher import WSMessageDispatcher

logger = logging.getLogger(__name__)


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

    # ── Cross-session memory retrieval ──
    memory_context = ""
    if settings.enable_session_memory:
        memory_context = _retrieve_session_memories(repo_root, task)

    # ── Phase 1: Scope Analysis ──
    await _send_stage(
        ws, "Phase 1: Analyzing scope...",
        model=explorer.model_name, phase=1,
    )
    logger.info(
        "Planning Phase 1: Scope analysis (model=%s, tool_budget=%d)",
        explorer.model_name, settings.plan_phase1_max_turns,
    )
    t0 = time.monotonic()

    phase1_turns_str = str(settings.plan_phase1_max_turns)
    phase1_system = registry.format(
        "planning.scope_system", PHASE1_MAX_TURNS=phase1_turns_str,
    )
    phase1_user_content = registry.format(
        "planning.scope_user",
        task=task, context=context,
        PHASE1_MAX_TURNS=phase1_turns_str,
    )
    if memory_context:
        phase1_user_content += memory_context

    # Restricted tool subset for Phase 1 — verify assumptions, don't explore.
    phase1_tools = [
        t for t in build_planning_tools()
        if t["function"]["name"] in (
            "grep_files", "read_file", "list_directory",
            "query_project_context", "search_knowledge", "task_complete",
        )
    ]

    # Reuse the same read-only executor Phase 2 uses.
    small_ctx = settings._active_context_window <= 32768
    phase1_executor = _make_read_only_executor(
        explorer, repo_root, session_id, ws, dispatcher, small_ctx,
    )

    _phase1_tool_calls, scope = await explorer.chat_with_tools(
        messages=[
            {"role": "system", "content": phase1_system},
            {"role": "user", "content": phase1_user_content},
        ],
        tools=phase1_tools,
        tool_executor_fn=phase1_executor,
        max_turns=settings.plan_phase1_max_turns,
        max_tokens=phase_max_tokens,
        text_only_exit_count=1,  # any text-only turn exits
        on_tool_call=on_tool_call,
        on_tool_result=on_tool_result,
        on_content=on_content,
        on_thinking=on_thinking,
        on_metrics=on_metrics,
    )
    if on_content:
        await _send_content_done(ws, scope)

    phase_timings["phase_1_scope"] = time.monotonic() - t0
    _save_debug_phase(
        repo_root, session_id, "phase_1_scope",
        scope, phase_timings["phase_1_scope"],
    )
    logger.info(
        "Phase 1 used %d tool calls in %.1fs",
        len(_phase1_tool_calls), phase_timings["phase_1_scope"],
    )
    await _send_stage_done(
        ws, "Scope analysis complete",
        model=explorer.model_name, phase=1,
    )

    # ── Phase 2: File Identification + Content Reading ──
    await _send_stage(
        ws, "Phase 2: Exploring codebase and reading files...",
        model=explorer.model_name, phase=2,
    )
    logger.info("Planning Phase 2: File identification and reading")

    file_summary_obj, file_identification, phase2_elapsed = (
        await run_phase2_exploration(
            task=task,
            scope=scope,
            context=context,
            repo_root=repo_root,
            session_id=session_id,
            explorer=explorer,
            phase_max_tokens=phase_max_tokens,
            ws=ws,
            dispatcher=dispatcher,
            on_content=on_content,
            on_thinking=on_thinking,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            on_metrics=on_metrics,
        )
    )

    phase_timings["phase_2_file_identification"] = phase2_elapsed
    _save_debug_phase(
        repo_root, session_id, "phase_2_file_identification",
        file_identification, phase2_elapsed,
    )
    await _send_stage_done(
        ws, "Codebase exploration complete",
        model=explorer.model_name, phase=2,
    )

    # Pass exploration results directly to downstream phases
    file_summary = file_identification

    # Privacy pass: strip sensitive data from file summary before
    # it enters Phases 3-5 (which may run on a cloud provider)
    if refiner and refiner.is_active:
        file_summary, redactions = await refiner.strip_privacy(file_summary)
        if redactions:
            logger.info(
                "Privacy: stripped %d items from file summary",
                len(redactions),
            )

    # Compact file_summary at small context windows
    if settings._active_context_window <= 32768:
        file_summary = await _compact_file_summary(
            file_summary, explorer, settings._active_context_window,
        )

    # ── Phase 3: Design + Risk Synthesis ──
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

    phase3_project_context_block = (
        f"PROJECT CONTEXT:\n{project_context}\n\n"
        if project_context else ""
    )
    phase3_user_content = registry.format(
        "planning.design_user",
        task=task,
        scope=scope,
        project_context=phase3_project_context_block,
        file_summary=file_summary,
    )
    phase3_messages = [
        {"role": "system", "content": PLAN_DESIGN_SYSTEM_PROMPT},
        {"role": "user", "content": phase3_user_content},
    ]

    # Search-only tool executor for Phase 3 Pass 1 verification.
    async def _search_only_executor(name: str, arguments: dict) -> str:
        """Execute search tools for Phase 3 design verification."""
        if name == "search_internet":
            from lean_ai.tools.internet import search_internet
            result = await search_internet(
                query=arguments.get("query", ""),
                llm_client=expert,
            )
            return (
                result.output if result.success
                else result.error or "Error"
            )
        elif name == "fetch_url":
            from lean_ai.tools.internet import fetch_url
            result = await fetch_url(
                url=arguments.get("url", ""),
                repo_root=repo_root,
                llm_client=expert,
            )
            return (
                result.output if result.success
                else result.error or "Error"
            )
        elif name == "task_complete":
            return "Design synthesis marked complete."
        return f"Unknown tool: {name}"

    # Pass 1: expert reasons through design + verifies external patterns.
    # text_only_exit_count=1 preserves single-shot behaviour when the
    # FileSummary's VERIFIED REFERENCES already cover every external
    # surface — the model exits on its first text response.
    _phase3_tool_calls, phase3_exploration_prose = (
        await expert.chat_with_tools(
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
    )
    if on_content:
        await _send_content_done(ws, phase3_exploration_prose)

    # Pass 2: coerce exploration prose + FileSummary into DesignAndRisks.
    design_and_risks_obj = await _synthesize_design_and_risks(
        task=task,
        scope=scope,
        project_context_block=phase3_project_context_block,
        file_summary=file_summary,
        exploration_prose=phase3_exploration_prose,
        expert=expert,
        expert_max_tokens=expert_max_tokens,
        on_thinking=on_thinking,
    )
    design_and_risks = _format_design_and_risks(design_and_risks_obj)
    missing_files = _format_missing_files(design_and_risks_obj.missing_files)

    phase_timings["phase_3_design_and_risks"] = time.monotonic() - t0
    _save_debug_phase(
        repo_root, session_id, "phase_3_design_and_risks",
        design_and_risks, phase_timings["phase_3_design_and_risks"],
    )
    logger.info(
        "Phase 3 synthesis: naming=%d designs=%d missing=%d "
        "deps=%d risks=%d citations=%d in %.1fs",
        len(design_and_risks_obj.naming_conventions),
        len(design_and_risks_obj.change_designs),
        len(design_and_risks_obj.missing_files),
        len(design_and_risks_obj.dependency_order),
        len(design_and_risks_obj.critical_risks),
        len(design_and_risks_obj.citations),
        phase_timings["phase_3_design_and_risks"],
    )
    await _send_stage_done(
        ws, "Design and risk synthesis complete",
        model=expert.model_name, phase=3,
    )

    # ── Phase 4: Structured Plan Assembly ──
    await _send_stage(
        ws, "Phase 4: Assembling structured plan...",
        model=expert.model_name, phase=4,
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

    # Safety: strip exploration tools from the implementation plan
    impl_steps = [
        s for s in plan.steps if s.tool in IMPLEMENTATION_STEP_TOOLS
    ]
    stripped_count = len(plan.steps) - len(impl_steps)
    if stripped_count:
        stripped_tools = [
            s.tool for s in plan.steps
            if s.tool not in IMPLEMENTATION_STEP_TOOLS
        ]
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

    # ── Phase 4 plan validation ────────────────────────────────────────
    # Set-membership checks over structured Phase 2 + Phase 3 outputs.
    # Non-blocking warnings are logged AND surfaced on the plan for the
    # extension approval screen. Uncovered BLOCKING missing_files
    # trigger a single auto-revision.
    plan_warnings = _run_plan_validations(
        plan, file_summary_obj, design_and_risks_obj,
    )

    blocking_uncovered = [
        mf for mf in _uncovered_missing_files(plan, design_and_risks_obj)
        if mf.blocking
    ]
    if blocking_uncovered:
        logger.warning(
            "Phase 4 plan validation — %d BLOCKING uncovered missing "
            "file(s); triggering auto-revision",
            len(blocking_uncovered),
        )
        feedback = (
            "Phase 3 identified BLOCKING missing files that the plan "
            "does not cover. Add a create_file or edit_file step for "
            "each:\n"
            + "\n".join(
                f"- {mf.file_path}: {mf.purpose}"
                for mf in blocking_uncovered
            )
        )
        plan = await _revise_plan(
            task=task,
            revision_context=(
                f"PREVIOUS PLAN (JSON):\n"
                f"{plan.model_dump_json(indent=2)}\n\n"
                f"USER FEEDBACK:\n{feedback}"
            ),
            llm_client=llm_client,
            context=context,
            ws=ws,
            expert_llm_client=expert_llm_client,
            on_thinking=on_thinking,
        )
        # Revision may re-introduce non-implementation tool steps —
        # strip again, renumber, then re-validate. On the second pass,
        # any still-uncovered blocking files fall through to warn-only.
        plan.steps = [
            s for s in plan.steps if s.tool in IMPLEMENTATION_STEP_TOOLS
        ]
        for i, step in enumerate(plan.steps, 1):
            step.step_number = i
        plan_warnings = _run_plan_validations(
            plan, file_summary_obj, design_and_risks_obj,
        )

    plan.plan_validation_warnings = plan_warnings

    # Save Phase 4 outputs
    _save_debug_phase(
        repo_root, session_id, "phase_4_plan",
        plan.model_dump_json(indent=2),
        phase_timings["phase_4_plan_assembly"],
    )
    _save_debug_phase(
        repo_root, session_id, "phase_4_plan_markdown",
        plan_to_markdown(plan),
        phase_timings["phase_4_plan_assembly"],
    )

    await _send_stage_done(
        ws,
        f"Plan assembled — {len(plan.steps)} steps across "
        f"{len(plan.affected_files)} file(s)",
        model=expert.model_name, phase=4,
    )

    # ── Phase 5: Verification (only when test_command is available) ──
    if test_command:
        phase5_elapsed = await _run_phase5_verification(
            plan=plan,
            task=task,
            file_summary=file_summary,
            test_command=test_command,
            expert=expert,
            plan_assembly_max_tokens=plan_assembly_max_tokens,
            ws=ws,
            repo_root=repo_root,
            session_id=session_id,
            on_thinking=on_thinking,
        )
        phase_timings["phase_5_verification"] = phase5_elapsed

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
        debug_dir = (
            Path(repo_root) / ".lean_ai" / "plan_debug" / session_id
        )
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / "meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8",
        )

    logger.info(
        "Plan created: %d steps, %d affected files",
        len(plan.steps), len(plan.affected_files),
    )
    return plan


async def _run_phase5_verification(
    *,
    plan: ExecutionPlan,
    task: str,
    file_summary: str,
    test_command: str,
    expert: "LLMClient",
    plan_assembly_max_tokens: int,
    ws: WebSocket | None,
    repo_root: str,
    session_id: str,
    on_thinking: Callable | None,
) -> float:
    """Run Phase 5: Verification step generation.

    Appends test creation + test execution steps to the plan (normal mode)
    or stores them separately in plan.tdd_test_steps (TDD mode).

    Returns elapsed time in seconds.
    """
    tdd_mode = settings.enable_tdd
    phase_label = (
        "Phase 5: Designing TDD test steps..."
        if tdd_mode
        else "Phase 5: Adding verification steps..."
    )
    await _send_stage(ws, phase_label, model=expert.model_name, phase=5)
    logger.info(
        "Planning Phase 5: Verification step generation (tdd=%s)", tdd_mode,
    )
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

    # In TDD mode, the implementation does not exist yet — design tests
    # from the PLAN, not from existing patterns. Each test step's
    # `instruction` must be self-contained because Phase A executes it
    # without access to other steps.
    if tdd_mode:
        source_section = (
            "BEHAVIOR TO TEST (derived from the IMPLEMENTATION PLAN above):\n"
            "Design tests that pin down the *intended* behavior of each new "
            "or modified entity in the plan. The implementation does NOT "
            "exist yet — do not look for existing source files. Tests "
            "drive the implementation, not the reverse.\n"
        )
        run_tests_rule = ""
        context_rule = (
            "The `context` field must describe the EXPECTED BEHAVIOR for "
            "this test file: the public function/class signatures the "
            "tests will call, the expected inputs and outputs for each "
            "test, and any contracts/invariants the implementation must "
            "uphold. Do NOT describe existing code — there is none yet. "
            "If you need to mirror an existing fixture or import style "
            "(e.g. conftest.py path, common test base class), name it "
            "explicitly and quote the import line — do not assume Phase A "
            "will browse the repo for it.\n"
        )
        self_contained_rule = (
            "SELF-CONTAINED INSTRUCTIONS — CRITICAL:\n"
            "Phase A (the test-writing model) sees ONLY this step's "
            "`instruction`, `file_path`, and `context` fields. It does "
            "NOT see the implementation plan, other test steps, or "
            "existing source files. Therefore:\n"
            "- Never write 'see step N above', 'as in step N', or any "
            "cross-reference.\n"
            "- Never write '[TEST]' as a description placeholder — the "
            "description must be a complete sentence stating what is "
            "being tested.\n"
            "- Inline every public signature, expected input/output, "
            "and exception/error type the test asserts on.\n"
            "- If a test depends on a fixture or import, write the full "
            "import path (e.g. `from lean_ai.tools.store_issue import "
            "store_issue`) inside the instruction.\n\n"
        )
    else:
        source_section = (
            f"FILE SUMMARY (existing test patterns):\n{file_summary}\n"
        )
        run_tests_rule = (
            "- End with a single 'run_tests' step using "
            f"the test command: {test_command}\n"
        )
        context_rule = (
            "The `context` field must include the relevant existing test "
            "file content (imports, fixtures, assertion style) so the "
            "executor can replicate the pattern without reading "
            "additional files.\n"
        )
        self_contained_rule = ""

    verification = await expert.chat_structured(
        messages=[
            {"role": "system", "content": PLAN_VERIFICATION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"TASK: {task}\n\n"
                    f"IMPLEMENTATION PLAN:\n{impl_plan_md}\n\n"
                    f"TEST COMMAND: {test_command}\n\n"
                    f"{source_section}\n"
                    "Review the implementation plan above and produce "
                    "ONLY the verification steps that should run AFTER "
                    "all implementation is complete.\n\n"
                    "RULES:\n"
                    "- For each new module or significant feature, "
                    "include a 'create_file' step for a test file.\n"
                    "- Only create tests for NEW functionality — do "
                    "not duplicate existing test coverage.\n"
                    + run_tests_rule
                    + f"- Start step numbering at {next_step}\n"
                    "- Follow the naming conventions from the plan\n\n"
                    + self_contained_rule
                    + "TEST FILE STEP — REQUIRED CONTENT IN `instruction`:\n"
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
                    + context_rule
                    + "\nASSERTION QUALITY:\n"
                    "- Each assertion must test ONE specific behavior, "
                    "not a vague 'it works'\n"
                    "- Use exact expected values, not just truthiness "
                    "(assert result == 42, not assert result)\n"
                    "- For exceptions: assert BOTH the type AND the "
                    "message substring "
                    "(pytest.raises(ValueError, match='cannot be negative'))\n"
                    "- For collections: assert length AND specific "
                    "element values\n"
                    "- For async code: verify that coroutines are "
                    "properly awaited (not returned as coroutine objects)\n\n"
                    "ANTI-PATTERNS TO AVOID IN TEST STEPS:\n"
                    "- Do NOT create test functions that only assert True "
                    "or assert something is not None\n"
                    "- Do NOT mock the unit under test — only mock its "
                    "external dependencies\n"
                    "- Do NOT write tests that pass regardless of "
                    "implementation (tautological tests)\n"
                    "- Do NOT create tests that depend on execution order "
                    "within the file"
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

    elapsed = time.monotonic() - t0
    _save_debug_phase(
        repo_root, session_id, "phase_5_verification",
        verification.model_dump_json(indent=2), elapsed,
    )
    test_steps = len(all_verification_steps)
    stage_msg = (
        f"TDD test steps designed — {test_steps} step(s)"
        if tdd_mode
        else f"Verification steps added — {test_steps} test step(s)"
    )
    await _send_stage_done(
        ws, stage_msg, model=expert.model_name, phase=5,
    )

    return elapsed


# ── Phase 3 synthesis + rendering ───────────────────────────────────────────


async def _synthesize_design_and_risks(
    *,
    task: str,
    scope: str,
    project_context_block: str,
    file_summary: str,
    exploration_prose: str,
    expert: "LLMClient",
    expert_max_tokens: int,
    on_thinking: "Callable | None" = None,
) -> DesignAndRisks:
    """Coerce Phase 3's exploration prose + inputs into a DesignAndRisks.

    On structured-output failure, returns a minimal DesignAndRisks with the
    exploration prose stashed in ``notes`` so the pipeline keeps moving.
    """
    synthesis_system = registry.get("planning.design_synthesis_system")
    user_parts = [
        f"TASK: {task}",
        f"SCOPE:\n{scope}",
    ]
    if project_context_block:
        user_parts.append(project_context_block.rstrip())
    user_parts.append(f"FILE SUMMARY:\n{file_summary}")
    if exploration_prose.strip():
        user_parts.append(
            f"PASS 1 EXPLORATION PROSE:\n{exploration_prose}"
        )
    user_parts.append(
        "Produce a DesignAndRisks object from the inputs above. "
        "Populate every field per the system-prompt rubric. Empty lists "
        "are acceptable when an input contains nothing relevant."
    )

    try:
        return await expert.chat_structured(
            messages=[
                {"role": "system", "content": synthesis_system},
                {"role": "user", "content": "\n\n".join(user_parts)},
            ],
            schema=DesignAndRisks,
            max_tokens=expert_max_tokens,
            thinking_callback=on_thinking,
        )
    except Exception:
        logger.warning(
            "Phase 3 synthesis failed — returning minimal DesignAndRisks "
            "with exploration prose in notes",
            exc_info=True,
        )
        return DesignAndRisks(notes=exploration_prose.strip())


def _format_design_and_risks(dar: DesignAndRisks) -> str:
    """Render a DesignAndRisks object to the markdown shape Phase 4 consumes
    as ``{design_and_risks}``. Empty sections are omitted.
    """
    lines: list[str] = []

    if dar.naming_conventions:
        lines.append("## Naming Conventions")
        lines.append("")
        lines.append("| category | pattern | source_file |")
        lines.append("|---|---|---|")
        for nc in dar.naming_conventions:
            lines.append(
                f"| {nc.category} | {nc.pattern} | {nc.source_file} |"
            )
        lines.append("")

    if dar.change_designs:
        lines.append("## Change Designs")
        lines.append("")
        for cd in dar.change_designs:
            lines.append(f"### {cd.file_path}")
            lines.append("")
            lines.append(cd.decisions.strip())
            lines.append("")

    if dar.missing_files:
        lines.append("## Missing Files")
        lines.append("")
        for i, m in enumerate(dar.missing_files, 1):
            blocking = " [BLOCKING]" if m.blocking else ""
            lines.append(f"{i}. {m.file_path} — {m.purpose}{blocking}")
        lines.append("")

    if dar.dependency_order:
        lines.append("## Dependency Order")
        lines.append("")
        for d in dar.dependency_order:
            lines.append(
                f"- {d.file_path} depends on {d.depends_on} — {d.reason}"
            )
        lines.append("")

    if dar.critical_risks:
        lines.append("## Critical Risks")
        lines.append("")
        for r in dar.critical_risks:
            lines.append(
                f"- **[{r.severity}]** {r.risk} — {r.mitigation}"
            )
        lines.append("")

    if dar.citations:
        lines.append("## Citations")
        lines.append("")
        seen_urls: set[str] = set()
        for c in dar.citations:
            if c.docs_url in seen_urls:
                continue
            seen_urls.add(c.docs_url)
            entry = f"- {c.dependency} — {c.docs_url}"
            if c.version:
                entry += f" — version: {c.version}"
            if c.confirmed_patterns:
                entry += f" — {c.confirmed_patterns}"
            lines.append(entry)
        lines.append("")

    if dar.notes.strip():
        lines.append("## Notes")
        lines.append("")
        lines.append(dar.notes.strip())
        lines.append("")

    if not lines:
        return "(no design output)\n"
    return "\n".join(lines).rstrip() + "\n"


def _format_missing_files(missing: list[MissingFile]) -> str:
    """Render the missing-files list as the numbered bullet string Phase 4
    consumes as ``{missing_files}``. Empty string when no entries — matches
    the prior behaviour of ``_extract_missing_files``.
    """
    if not missing:
        return ""
    rows: list[str] = []
    for i, m in enumerate(missing, 1):
        blocking = " [BLOCKING]" if m.blocking else ""
        rows.append(f"{i}. {m.file_path} — {m.purpose}{blocking}")
    return "\n".join(rows)


# ── Phase 4 plan validation helpers ─────────────────────────────────────────
#
# All checks are set-membership against structured inputs from Phases 2 and
# 3 — no regex, no parsing of LLM-generated prose. Warnings are logged and
# also returned as a list so the caller can stash them on the plan for UI
# surfacing.


def _collect_known_paths(
    file_summary: FileSummary | None,
    dar: DesignAndRisks,
) -> set[str]:
    """Union of every file path the prior phases know about.

    Returns an empty set when Phase 2 produced no structured output
    (parallel path), which tells the caller to skip membership-based
    checks cleanly rather than flag every path as invented.
    """
    if file_summary is None:
        return set()
    paths: set[str] = set()
    for obs in file_summary.files_to_modify:
        paths.add(obs.file_path)
    for obs in file_summary.files_to_create:
        paths.add(obs.file_path)
    for obs in file_summary.files_read_for_context:
        paths.add(obs.file_path)
    for item in file_summary.missing_infrastructure:
        paths.add(item.name)
    for mf in dar.missing_files:
        paths.add(mf.file_path)
    return paths


def _check_hallucinated_paths(
    plan: ExecutionPlan,
    known_paths: set[str],
) -> list[str]:
    """Flag any step.file_path that is not in the prior-phase path universe."""
    if not known_paths:
        return []
    plan_paths = {s.file_path for s in plan.steps if s.file_path}
    return [
        f"invented path: {p}"
        for p in sorted(plan_paths - known_paths)
    ]


def _uncovered_missing_files(
    plan: ExecutionPlan,
    dar: DesignAndRisks,
) -> list[MissingFile]:
    """Return MissingFile entries not covered by any plan step.

    Returns the structured objects so the caller can branch on
    ``.blocking`` (triggers auto-revision) versus non-blocking (warn only).
    """
    step_paths = {s.file_path for s in plan.steps}
    return [
        mf for mf in dar.missing_files
        if mf.file_path not in step_paths
    ]


def _check_edit_create_consistency(
    plan: ExecutionPlan,
    file_summary: FileSummary | None,
    dar: DesignAndRisks,
) -> list[str]:
    """Flag edit_file on unknown paths and create_file on existing paths."""
    if file_summary is None:
        return []
    to_modify: set[str] = {
        o.file_path for o in file_summary.files_to_modify
    }
    to_modify |= {
        o.file_path for o in file_summary.files_read_for_context
    }
    to_create: set[str] = {
        o.file_path for o in file_summary.files_to_create
    }
    to_create |= {mf.file_path for mf in dar.missing_files}
    warnings: list[str] = []
    for s in plan.steps:
        if not s.file_path:
            continue
        if s.tool == "edit_file" and s.file_path not in to_modify:
            warnings.append(
                f"edit_file on unknown-to-modify path: {s.file_path}"
            )
        elif s.tool == "create_file" and s.file_path not in to_create:
            warnings.append(
                f"create_file on unknown-to-create path: {s.file_path}"
            )
    return warnings


def _run_plan_validations(
    plan: ExecutionPlan,
    file_summary: FileSummary | None,
    dar: DesignAndRisks,
) -> list[str]:
    """Run every validator, log each warning, and return the full list.

    Shared between the pre- and post-revision passes so the logic stays
    in one place.
    """
    warnings: list[str] = []
    known = _collect_known_paths(file_summary, dar)
    warnings.extend(_check_hallucinated_paths(plan, known))
    warnings.extend(_check_edit_create_consistency(plan, file_summary, dar))
    for mf in _uncovered_missing_files(plan, dar):
        tag = " [BLOCKING]" if mf.blocking else ""
        warnings.append(
            f"uncovered missing file: {mf.file_path} — {mf.purpose}{tag}"
        )
    for w in warnings:
        logger.warning("Phase 4 plan validation — %s", w)
    return warnings

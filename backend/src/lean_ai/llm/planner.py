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
    PlanStep,
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
    _synthesize_scope,
)
from lean_ai.llm.prompt_registry import registry
from lean_ai.llm.prompts import (
    PLAN_ASSEMBLY_SYSTEM_PROMPT,
    PLAN_DESIGN_SYSTEM_PROMPT,
    PLAN_VERIFICATION_SYSTEM_PROMPT,
)
from lean_ai.llm.tool_definitions import (
    REQUEST_CLARIFICATION_TOOL,
    build_design_tools,
    build_planning_tools,
)

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
        memory_context = await _retrieve_session_memories(repo_root, task)

    # ── Phase 1: Clarification / verification ──
    await _send_stage(
        ws, "Phase 1: Verifying task (asking clarifying questions if needed)...",
        model=explorer.model_name, phase=1,
    )
    logger.info(
        "Planning Phase 1 clarification (model=%s, tool_budget=%d)",
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

    # Restricted tool subset for Phase 1 — verify the task against the
    # codebase, ask the user only when a decision genuinely can't be
    # inferred. Scope document assembly happens in Phase 1a (synthesis).
    phase1_tools = [
        t for t in build_planning_tools()
        if t["function"]["name"] in (
            "grep_files", "read_file", "list_directory",
            "query_project_context", "search_reference", "task_complete",
        )
    ]
    phase1_tools.append(REQUEST_CLARIFICATION_TOOL)

    # Reuse the same read-only executor Phase 2 uses.
    small_ctx = settings._active_context_window <= 32768
    phase1_executor = _make_read_only_executor(
        explorer, repo_root, session_id, ws, dispatcher, small_ctx,
    )

    _phase1_tool_calls, scope_prose = await explorer.chat_with_tools(
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
        await _send_content_done(ws, scope_prose)

    phase_timings["phase_1_clarification"] = time.monotonic() - t0
    _save_debug_phase(
        repo_root, session_id, "phase_1_clarification",
        scope_prose, phase_timings["phase_1_clarification"],
    )
    logger.info(
        "Phase 1 clarification used %d tool calls in %.1fs",
        len(_phase1_tool_calls), phase_timings["phase_1_clarification"],
    )
    await _send_stage_done(
        ws, "Task verified",
        model=explorer.model_name, phase=1,
    )

    # ── Phase 1a: Scope document generation ──
    # chat_structured → ScopeDocument with programmatic fallback guarantees
    # Phase 2 always receives the 8-section shape, never raw prose.
    await _send_stage(
        ws, "Phase 1a: Generating scope document...",
        model=explorer.model_name, phase=1,
    )
    t0a = time.monotonic()
    scope_obj, scope, scope_synthesized = await _synthesize_scope(
        task=task,
        context=context,
        exploration_prose=scope_prose,
        explorer=explorer,
        phase_max_tokens=phase_max_tokens,
        on_thinking=on_thinking,
    )
    if not scope_synthesized:
        logger.warning(
            "Phase 1a scope synthesis fell through to programmatic "
            "fallback (task-text-only ScopeDocument). Phase 2 will "
            "reconstruct missing sections from the task during "
            "exploration.",
        )

    phase_timings["phase_1a_scope"] = time.monotonic() - t0a
    _save_debug_phase(
        repo_root, session_id, "phase_1a_scope",
        scope, phase_timings["phase_1a_scope"],
    )
    await _send_stage_done(
        ws, "Scope document generated",
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
    if settings.enable_session_memory and getattr(
        settings, "enable_phase3_memory", True,
    ):
        from lean_ai.llm.planner_helpers import retrieve_design_memories

        design_memories = await retrieve_design_memories(repo_root, task)
        if design_memories:
            phase3_user_content += design_memories
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

    # Layer 9 — propagate core_functionality tags from Phase 3 into
    # the ExecutionPlan so they survive plan approval and reach
    # Phase 5's regression-coverage mandate. Gated by the feature
    # flag so disabling Layer 9 cleanly suppresses the field.
    if settings.enable_core_functionality_tagging:
        plan.core_functionality = list(
            design_and_risks_obj.core_functionality
        )

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

    # ── Phase 5: Verification (Layer 4 — always runs) ─────────────
    # When test_command is empty, Phase 5 still runs and seeds test
    # files on disk; it just omits the final run_tests step and the
    # run_tests safety-net injection. This guarantees that every plan
    # leaves the workspace better-tested than it found it, even before
    # a test runner is configured.
    phase5_elapsed = await _run_phase5_verification(
        plan=plan,
        task=task,
        file_summary=file_summary,
        file_summary_obj=file_summary_obj,
        design_and_risks_obj=design_and_risks_obj,
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
    file_summary_obj: FileSummary | None,
    design_and_risks_obj: DesignAndRisks,
    test_command: str,
    expert: "LLMClient",
    plan_assembly_max_tokens: int,
    ws: WebSocket | None,
    repo_root: str,
    session_id: str,
    on_thinking: Callable | None,
) -> float:
    """Run Phase 5: Verification step generation.

    Appends test creation + test execution steps to the plan (normal
    mode) or stores them separately in ``plan.tdd_test_steps`` (TDD
    mode). Receives the structured Phase 2 ``FileSummary`` and Phase 3
    ``DesignAndRisks`` so the user prompt can target specific files
    for coverage and cite critical_risks as security cases.

    Test-path convention warnings are appended to
    ``plan.plan_validation_warnings`` so the approval UI (the Phase 4
    surfacing mechanism) carries them through to the user.

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

    verification_targets = _build_verification_targets(
        file_summary_obj, design_and_risks_obj,
    )
    security_concerns = _build_security_concerns(design_and_risks_obj)

    # Layer 6 (testing inventory) populates in a later PR; for now pass
    # an explicit empty-marker so the prompt still formats cleanly.
    testing_inventory = _format_testing_inventory(file_summary_obj)

    # Layer 9 (core functionality) populates in a later PR; for now pass
    # an explicit empty-marker.
    core_functionality = _format_core_functionality(plan)

    # Layer 4 graceful-degradation scaffolding: when ``test_command`` is
    # empty, omit the "end with run_tests" rule so the LLM doesn't
    # invent a phantom test command. The Layer 4 PR removes the
    # ``if test_command:`` gate around Phase 5 entirely.
    if test_command:
        run_tests_rule = (
            f"- Exactly ONE final run_tests step invoking: "
            f"{test_command}\n"
        )
    else:
        run_tests_rule = (
            "- Do NOT include a run_tests step — no test runner is "
            "configured for this workspace yet. Create the test "
            "files on disk; the runner will be added later.\n"
        )

    if tdd_mode:
        user_content = registry.format(
            "planning.verification_user_tdd",
            task=task,
            impl_plan_md=impl_plan_md,
            testing_inventory=testing_inventory,
            verification_targets=verification_targets,
            security_concerns=security_concerns,
            core_functionality=core_functionality,
            next_step=str(next_step),
        )
    else:
        user_content = registry.format(
            "planning.verification_user_normal",
            task=task,
            test_command=test_command or "(none configured yet)",
            impl_plan_md=impl_plan_md,
            file_summary=file_summary,
            testing_inventory=testing_inventory,
            verification_targets=verification_targets,
            security_concerns=security_concerns,
            core_functionality=core_functionality,
            next_step=str(next_step),
            run_tests_rule=run_tests_rule,
        )

    verification = await expert.chat_structured(
        messages=[
            {"role": "system", "content": PLAN_VERIFICATION_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        schema=VerificationPlan,
        max_tokens=plan_assembly_max_tokens,
        thinking_callback=on_thinking,
    )

    if tdd_mode:
        # TDD: keep test steps separate for expert-first execution.
        # The TDD user prompt asks explicitly for no run_tests step;
        # keep the filter as defensive safety in case the model
        # ignores that instruction.
        test_steps_only = [
            s for s in verification.steps if s.tool != "run_tests"
        ]
        for i, step in enumerate(test_steps_only, 1):
            step.step_number = i
        plan.tdd_test_steps = test_steps_only

        # Re-number implementation steps starting after test steps.
        offset = len(test_steps_only)
        for i, step in enumerate(plan.steps, offset + 1):
            step.step_number = i
    else:
        # Normal mode: append verification steps to plan.
        appended = list(verification.steps)

        # Safety net: Phase 5 must never skip running the existing
        # test suite when a runner is configured. If the model omitted
        # a run_tests step, inject one so the plan always ends with a
        # test execution step — even when no new test files were
        # created. When test_command is empty (Layer 4 — always run
        # Phase 5 without a runner), the safety-net is disabled and
        # test files are seeded on disk without a run_tests step.
        if test_command and not any(s.tool == "run_tests" for s in appended):
            logger.warning(
                "Phase 5 produced no run_tests step — injecting one "
                "so existing tests run (%s).",
                test_command,
            )
            # Defensively filter any run_tests step the LLM tried to
            # produce with an empty command — only inject when we have
            # a real command to invoke.
            appended.append(
                PlanStep(
                    step_number=0,
                    tool="run_tests",
                    file_path="",
                    instruction=(
                        f"Run the project's test suite to confirm "
                        f"the implementation works: {test_command}"
                    ),
                    reason=(
                        "Verify the existing test suite still passes "
                        "after the plan's changes."
                    ),
                    context="",
                )
            )
            # Keep the injected step in the debug payload too so the
            # saved JSON reflects what actually ran.
            verification.steps = appended
        elif not test_command:
            # Layer 4 — defensive: drop any run_tests step the LLM
            # produced despite our prompt telling it not to. Running
            # an empty command is a no-op at best and a crash at
            # worst.
            stripped = [s for s in appended if s.tool != "run_tests"]
            if len(stripped) != len(appended):
                logger.info(
                    "Phase 5 dropped %d run_tests step(s) — no test "
                    "runner is configured for this workspace.",
                    len(appended) - len(stripped),
                )
                appended = stripped
                verification.steps = appended

        for i, step in enumerate(appended, next_step):
            step.step_number = i
        plan.steps.extend(appended)

    # Update affected_files with any new test files.
    all_verification_steps = (
        plan.tdd_test_steps if tdd_mode else verification.steps
    )
    existing = set(plan.affected_files)
    for step in all_verification_steps:
        if step.file_path and step.file_path not in existing:
            plan.affected_files.append(step.file_path)

    # Test-path convention check — append warnings to the plan so the
    # approval UI surfacing (from Phase 4) picks them up.
    path_warnings = _check_test_path_conventions(
        verification, file_summary_obj,
    )
    if path_warnings:
        plan.plan_validation_warnings.extend(path_warnings)

    # Layer 2 — coverage validator: warn when an executable affected
    # file has no test step referencing it. Non-blocking.
    coverage_warnings = _check_affected_files_covered(
        verification, plan, file_summary_obj,
    )
    if coverage_warnings:
        plan.plan_validation_warnings.extend(coverage_warnings)

    # Layer 9 — core-functionality coverage: warn when a core entity
    # has no matching regression test step. Non-blocking.
    core_warnings = _check_core_functionality_covered(verification, plan)
    if core_warnings:
        plan.plan_validation_warnings.extend(core_warnings)

    elapsed = time.monotonic() - t0
    _save_debug_phase(
        repo_root, session_id, "phase_5_verification",
        verification.model_dump_json(indent=2), elapsed,
    )
    test_steps = len(all_verification_steps)
    if tdd_mode:
        stage_msg = f"TDD test steps designed — {test_steps} step(s)"
    elif not test_command:
        stage_msg = (
            f"Test files seeded — {test_steps} step(s); no test "
            f"runner configured"
        )
    else:
        stage_msg = f"Verification steps added — {test_steps} test step(s)"
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


# ── Phase 5 helpers ─────────────────────────────────────────────────────────
#
# Inputs derived from structured Phase 2/3 outputs, so Phase 5's prompt can
# target test generation precisely. All three helpers operate on structured
# Pydantic objects; no regex or LLM-prose parsing.


def _build_verification_targets(
    file_summary: FileSummary | None,
    dar: DesignAndRisks,
) -> str:
    """Markdown bullet list of files that need test coverage.

    Sources: ``dar.change_designs`` (non-obvious files Phase 3 designed)
    plus ``file_summary.files_to_create`` (new files Phase 2 identified).
    Deduplicates by path, preserves input order. Returns empty string
    when neither source has entries so the prompt can omit the section
    gracefully.
    """
    paths: list[str] = []
    seen: set[str] = set()
    for cd in dar.change_designs:
        if cd.file_path and cd.file_path not in seen:
            paths.append(cd.file_path)
            seen.add(cd.file_path)
    if file_summary is not None:
        for obs in file_summary.files_to_create:
            if obs.file_path and obs.file_path not in seen:
                paths.append(obs.file_path)
                seen.add(obs.file_path)
    if not paths:
        return ""
    return "\n".join(f"- {p}" for p in paths)


def _build_security_concerns(dar: DesignAndRisks) -> str:
    """Markdown bullet list of Phase 3 critical risks for Phase 5 to
    cover with tests.

    Returns empty string when ``critical_risks`` is empty so the prompt
    can omit the section gracefully.
    """
    if not dar.critical_risks:
        return ""
    return "\n".join(
        f"- **[{r.severity}]** {r.risk} — mitigation: {r.mitigation}"
        for r in dar.critical_risks
    )


def _format_testing_inventory(file_summary: FileSummary | None) -> str:
    """Render ``FileSummary.testing_inventory`` (Layer 6) for Phase 5.

    Returns a concise markdown block with framework, directory,
    assertion style, existing regression files, and per-affected-file
    coverage. Returns the ``(none)`` sentinel when Phase 2 did not
    populate the field so the prompt reads cleanly.

    Phase 2 population lands in a later PR; this helper keeps the
    Phase 5 call site stable until then.
    """
    inv = getattr(file_summary, "testing_inventory", None) if file_summary else None
    if inv is None:
        return (
            "(none reported by Phase 2 — detect the framework and "
            "directory from FILE SUMMARY yourself.)"
        )
    lines: list[str] = []
    if inv.test_framework:
        lines.append(f"- Framework: {inv.test_framework}")
    if inv.test_directory:
        lines.append(f"- Directory: {inv.test_directory}")
    if inv.test_file_pattern:
        lines.append(f"- File pattern: {inv.test_file_pattern}")
    if inv.assertion_style_excerpt:
        lines.append(
            "- Assertion style excerpt:\n```\n"
            + inv.assertion_style_excerpt
            + "\n```"
        )
    if inv.existing_regression_files:
        lines.append(
            "- Existing regression files (MUST NOT be modified):"
        )
        for p in inv.existing_regression_files:
            lines.append(f"  - {p}")
    if inv.affected_files_existing_coverage:
        lines.append("- Existing coverage for affected files:")
        for cov in inv.affected_files_existing_coverage:
            tests = ", ".join(cov.test_files) if cov.test_files else "(none)"
            lines.append(f"  - {cov.source_file} → {tests}")
    if inv.notes:
        lines.append(f"- Notes: {inv.notes}")
    return "\n".join(lines) if lines else "(empty)"


def _format_core_functionality(plan: "ExecutionPlan") -> str:
    """Render ``plan.core_functionality`` (Layer 9) for Phase 5.

    Returns the ``(none)`` sentinel when no tags were detected or the
    feature flag is disabled. Layer 9 populates the plan field; this
    helper keeps the Phase 5 call site stable until then.
    """
    tags = getattr(plan, "core_functionality", []) or []
    if not tags:
        return (
            "(none tagged — no mandatory regression tests required "
            "by Phase 3.)"
        )
    lines: list[str] = []
    for tag in tags:
        lines.append(
            f"- **{tag.entity}** in `{tag.file_path}` "
            f"[{tag.source_signal}, confidence={tag.confidence}] "
            f"— {tag.reason}"
        )
    return "\n".join(lines)


_TEST_PATH_TOKENS: tuple[str, ...] = ("test", "spec")
"""Common test-file naming tokens across languages: ``test`` (Python,
Go, Rust, Java, Ruby minitest, JS *.test.js) and ``spec`` (Ruby RSpec,
JS/TS *.spec.ts, Elixir). Case-insensitive ``in`` check against the
file path — captures ``tests/``, ``spec/``, ``__tests__/``,
``*_test.go``, ``*.spec.ts``, ``TestFoo.java``, etc."""


def _check_test_path_conventions(
    verification: VerificationPlan,
    file_summary: FileSummary | None,
) -> list[str]:
    """Flag Phase 5 ``create_file`` steps with paths that violate test
    conventions.

    A path passes if it contains any common test token (``test`` or
    ``spec``, case-insensitive) OR starts with any directory prefix
    learned from ``file_summary.files_read_for_context`` for files
    that themselves contain a test token — so repos with unusual test
    dirs can be accepted when Phase 2 read one of their files as a
    pattern reference. Pure string-contains and prefix checks over
    structured fields; no regex on LLM prose.
    """
    warnings: list[str] = []
    learned_prefixes: set[str] = set()
    if file_summary is not None:
        for obs in file_summary.files_read_for_context:
            p = (obs.file_path or "").lower()
            if "/" not in p:
                continue
            if any(tok in p for tok in _TEST_PATH_TOKENS):
                learned_prefixes.add(p.rsplit("/", 1)[0])
    for step in verification.steps:
        if step.tool != "create_file" or not step.file_path:
            continue
        low = step.file_path.lower()
        if any(tok in low for tok in _TEST_PATH_TOKENS):
            continue
        if any(low.startswith(pfx) for pfx in learned_prefixes):
            continue
        warnings.append(
            f"test step path outside test convention: {step.file_path}"
        )
    for w in warnings:
        logger.warning("Phase 5 plan validation — %s", w)
    return warnings


# Layer 2 — files that would benefit from a test. We only expand
# coverage checks to files with executable extensions. Docs / config /
# lockfiles / generated assets are skipped.
_EXECUTABLE_EXTENSIONS: frozenset[str] = frozenset({
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".kts", ".cs", ".fs", ".vb",
    ".rb", ".php", ".swift", ".m", ".mm", ".cpp", ".cxx", ".cc",
    ".c", ".h", ".hpp", ".hh", ".ex", ".exs", ".erl", ".hrl",
    ".scala", ".clj", ".cljs", ".lua", ".dart", ".ml", ".mli",
    ".hs", ".r", ".nim", ".zig", ".v", ".d",
})


def _has_executable_extension(path: str) -> bool:
    lower = path.lower()
    return any(lower.endswith(ext) for ext in _EXECUTABLE_EXTENSIONS)


def _check_core_functionality_covered(
    verification: VerificationPlan,
    plan: ExecutionPlan,
) -> list[str]:
    """Flag core-functionality tags missing a regression test step.

    Layer 9 mandates that every ``plan.core_functionality`` tag whose
    confidence is at or above
    ``settings.core_functionality_min_confidence`` receives a
    regression-file test step in Phase 5. Tags below the confidence
    threshold are advisory only and do not trigger warnings.

    A step qualifies as a regression test for a tag when its
    ``file_path`` matches the regression convention AND its
    ``file_path + instruction + context`` haystack mentions the tag's
    entity or file_path. Warnings are non-blocking and go to
    ``plan.plan_validation_warnings``.
    """
    from lean_ai.tools.regression_guard import is_regression_test_path

    tags = getattr(plan, "core_functionality", None) or []
    if not tags:
        return []

    # Confidence gating — min_confidence is "low" | "medium" | "high".
    confidence_rank = {"low": 0, "medium": 1, "high": 2}
    try:
        min_rank = confidence_rank[settings.core_functionality_min_confidence]
    except KeyError:
        min_rank = confidence_rank["medium"]

    enforced_tags = [
        t for t in tags
        if confidence_rank.get(t.confidence, 1) >= min_rank
    ]
    if not enforced_tags:
        return []

    # Build haystack of regression-convention test steps only.
    haystacks: list[tuple[str, str]] = []
    for step in verification.steps:
        if step.tool != "create_file" or not step.file_path:
            continue
        if not is_regression_test_path(step.file_path):
            continue
        haystack = "\n".join([
            step.file_path or "",
            step.instruction or "",
            step.context or "",
            step.reason or "",
        ])
        haystacks.append((step.file_path, haystack))

    warnings: list[str] = []
    for tag in enforced_tags:
        entity = tag.entity.strip()
        file_path = tag.file_path.strip()
        covered = any(
            (entity and entity in hay) or (file_path and file_path in hay)
            for _, hay in haystacks
        )
        if not covered:
            warnings.append(
                f"core-functionality tag missing regression test: "
                f"'{entity}' in {file_path} "
                f"[{tag.source_signal}, confidence={tag.confidence}]"
            )

    for w in warnings:
        logger.warning("Phase 5 core-functionality — %s", w)
    return warnings


def _check_affected_files_covered(
    verification: VerificationPlan,
    plan: ExecutionPlan,
    file_summary: FileSummary | None,
) -> list[str]:
    """Flag plan files that receive no test coverage.

    For every file in ``plan.affected_files`` that has an executable
    extension AND corresponds to a ``FileSummary.files_to_create`` or
    ``files_to_modify`` observation, verify at least one ``create_file``
    step in ``verification.steps`` references that path in
    ``file_path``, ``instruction``, or ``context``. Uncovered paths
    append a warning to ``plan.plan_validation_warnings``.

    This is intentionally a *warning*, not a blocker — the plan still
    proceeds and the user sees the warning on the approval screen, so
    they can decide whether the gap is acceptable (e.g. trivial data
    classes, pure config).
    """
    # Set of paths Phase 2 said this plan will touch as code. Fall back
    # to ``affected_files`` when no FileSummary was produced (parallel
    # Phase 2 path returns None).
    code_paths: set[str] = set()
    if file_summary is not None:
        for obs in file_summary.files_to_create:
            if obs.file_path and _has_executable_extension(obs.file_path):
                code_paths.add(obs.file_path)
        for obs in file_summary.files_to_modify:
            if obs.file_path and _has_executable_extension(obs.file_path):
                code_paths.add(obs.file_path)
    else:
        for p in plan.affected_files:
            if _has_executable_extension(p):
                code_paths.add(p)

    if not code_paths:
        return []

    # Build a haystack of everything Phase 5's create_file test steps
    # reference. Any code path that appears anywhere in this haystack
    # is considered covered.
    haystacks: list[str] = []
    for step in verification.steps:
        if step.tool != "create_file":
            continue
        haystacks.append(step.file_path or "")
        haystacks.append(step.instruction or "")
        haystacks.append(step.context or "")
    combined = "\n".join(haystacks)

    warnings: list[str] = []
    for code_path in sorted(code_paths):
        # Treat the bare filename as a coarse match too, so a test step
        # that says "test the foo() function from foo.py" counts.
        filename = code_path.rsplit("/", 1)[-1]
        if code_path in combined or filename in combined:
            continue
        warnings.append(
            f"affected file has no test coverage: {code_path}"
        )

    for w in warnings:
        logger.warning("Phase 5 coverage — %s", w)
    return warnings

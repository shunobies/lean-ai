"""Plan-driven agentic workflow: plan → approve → execute per step.

The planner does ALL investigatory work (reads files, explores the codebase,
designs changes).  It produces a structured ExecutionPlan where each step
maps to one tool call.  After user approval, a constrained LLM executor
handles each step in 1-3 turns — translating the planner's detailed
instruction into a single tool invocation.
"""

import asyncio
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import WebSocket, WebSocketDisconnect

from lean_ai.config import settings
from lean_ai.llm.plan_schema import ExecutionPlan, PlanStep, plan_to_markdown
from lean_ai.llm.planner import assess_clarity, create_plan
from lean_ai.llm.tool_definitions import (
    build_implementation_tools,
    build_tdd_implementation_tools,
)
from lean_ai.routers.context_helpers import load_execution_context
from lean_ai.tools import scratchpad
from lean_ai.workflow.callbacks import build_workflow_callbacks
from lean_ai.workflow.fix_mode import _run_fix  # noqa: F401 — used by run_workflow
from lean_ai.workflow.prompts import (
    build_step_system_prompt,
    build_step_user_message,
    build_tdd_review_prompt,
    build_tdd_step_system_prompt,
)
from lean_ai.workflow.tool_executor import make_tool_executor
from lean_ai.workflow.validation import (
    _effective_post_commands,
    _run_post_validation,
    _run_validation_fix_loop,
)
from lean_ai.workflow.ws_dispatcher import WSMessageDispatcher
from lean_ai.workflow.ws_handler import ws_send, ws_send_nowait

if TYPE_CHECKING:
    from lean_ai.llm.client import LLMClient
    from lean_ai.llm.refiner import PromptRefiner

logger = logging.getLogger(__name__)

# Max plan revision rounds before giving up
_MAX_REVISIONS = 5


# ── Public API ──────────────────────────────────────────────────────


async def run_workflow(
    task: str,
    repo_root: str,
    ws: WebSocket,
    llm_client: "LLMClient",
    context: str = "",
    branch_name: str = "",
    base_branch: str = "",
    conversation_logger: Callable | None = None,
    mode: str = "plan",
    session_id: str = "",
    refiner: "PromptRefiner | None" = None,
    expert_llm_client: "LLMClient | None" = None,
    request_llm_client: "LLMClient | None" = None,
    dispatcher: WSMessageDispatcher | None = None,
) -> str:
    """Run a workflow. Supports three modes:

    - ``"plan"`` (default): clarify → plan → approve → execute
    - ``"fix"``: skip planning, bug-fix prompt
    - ``"request"``: skip planning, neutral prompt with internet search

    Returns a structured commit message summarising the actions taken.
    """
    logger.info("Workflow (%s): starting task: %s", mode, task[:100])

    # Validate TDD requirements: expert model + sufficient context window
    if settings.enable_tdd and expert_llm_client is None:
        logger.warning(
            "TDD mode enabled but no expert model configured — "
            "falling back to normal mode",
        )
        await ws_send(ws, "stage_status", {
            "stage": "tdd",
            "status": "done",
            "summary": (
                "TDD mode requires an expert model. "
                "Falling back to normal mode."
            ),
        })

    if settings.enable_tdd and settings._active_context_window <= 32768:
        logger.warning(
            "TDD mode disabled — context window too small (%d). "
            "TDD requires cross-phase context that exceeds 32k budget.",
            settings._active_context_window,
        )
        await ws_send(ws, "stage_status", {
            "stage": "tdd",
            "status": "done",
            "summary": (
                "TDD mode auto-disabled: context window too small "
                f"({settings._active_context_window}). "
                "Increase to 64k+ to enable TDD."
            ),
        })

    # Log the initial task
    if conversation_logger:
        await conversation_logger("user", task)

    if mode in ("fix", "request"):
        return await _run_fix(
            task=task,
            repo_root=repo_root,
            ws=ws,
            llm_client=llm_client,
            context=context,
            branch_name=branch_name,
            base_branch=base_branch,
            conversation_logger=conversation_logger,
            session_id=session_id,
            expert_llm_client=expert_llm_client,
            request_llm_client=request_llm_client,
            mode=mode,
            dispatcher=dispatcher,
        )

    # ── Phase 1: Clarify (optional) ──────────────────────────────
    clarify_client = request_llm_client or llm_client
    task_with_answers = await _clarify_task(
        task, ws, clarify_client, context, dispatcher=dispatcher,
    )

    # ── Phase 2: Plan ────────────────────────────────────────────
    await ws_send(ws, "stage_change", {"stage": "planning"})
    plan_commands = _effective_post_commands(repo_root)

    # Planning-specific streaming callbacks — include streaming flag
    # so the extension can distinguish token-level updates from
    # per-turn bulk content used during execution.
    planning_cb = build_workflow_callbacks(ws, streaming=True)

    plan = await create_plan(
        task=task_with_answers,
        repo_root=repo_root,
        llm_client=llm_client,
        context=context,
        ws=ws,
        dispatcher=dispatcher,
        refiner=refiner,
        test_command=plan_commands.get("test", ""),
        session_id=session_id,
        expert_llm_client=expert_llm_client,
        request_llm_client=request_llm_client,
        on_content=planning_cb.on_content,
        on_thinking=planning_cb.on_thinking,
        on_tool_call=planning_cb.on_tool_call,
        on_tool_result=planning_cb.on_tool_result,
        on_metrics=planning_cb.on_metrics,
    )

    # ── Phase 3: Approve ─────────────────────────────────────────
    approved_plan = await _wait_for_approval(
        plan=plan,
        task=task_with_answers,
        repo_root=repo_root,
        llm_client=llm_client,
        context=context,
        ws=ws,
        refiner=refiner,
        test_command=plan_commands.get("test", ""),
        expert_llm_client=expert_llm_client,
        request_llm_client=request_llm_client,
        dispatcher=dispatcher,
    )

    # ── Phase 4: Execute per-step ────────────────────────────────
    await ws_send(ws, "stage_change", {"stage": "implementing"})
    return await _execute_plan(
        plan=approved_plan,
        task=task_with_answers,
        repo_root=repo_root,
        ws=ws,
        llm_client=llm_client,
        context=context,
        branch_name=branch_name,
        base_branch=base_branch,
        conversation_logger=conversation_logger,
        session_id=session_id,
        expert_llm_client=expert_llm_client,
        dispatcher=dispatcher,
    )


# ── Phase 1: Clarification ─────────────────────────────────────────


async def _clarify_task(
    task: str,
    ws: WebSocket,
    llm_client: "LLMClient",
    context: str,
    dispatcher: WSMessageDispatcher | None = None,
) -> str:
    """Optionally ask clarifying questions before planning.

    Returns the original task augmented with user answers, or the task
    unchanged if no clarifications were needed.
    """
    questions = await assess_clarity(task, llm_client, context)
    if questions is None:
        logger.info("Task is clear — skipping clarification")
        return task

    logger.info("Clarification needed — %d questions", len(questions))
    await ws_send(ws, "clarification_needed", {"questions": questions})

    # Wait for user to respond
    while True:
        msg = await dispatcher.wait_for_approval() if dispatcher else None
        if msg is None:
            raise WebSocketDisconnect()

        if msg.get("type") == "user_message":
            answer = msg.get("content", "")
            augmented = (
                f"{task}\n\n"
                f"ADDITIONAL DETAILS (from clarification):\n{answer}"
            )
            logger.info("Received clarification answer (%d chars)", len(answer))
            return augmented


# ── Phase 3: Approval ──────────────────────────────────────────────


async def _wait_for_approval(
    plan: ExecutionPlan,
    task: str,
    repo_root: str,
    llm_client: "LLMClient",
    context: str,
    ws: WebSocket,
    refiner: "PromptRefiner | None" = None,
    test_command: str = "",
    expert_llm_client: "LLMClient | None" = None,
    request_llm_client: "LLMClient | None" = None,
    dispatcher: WSMessageDispatcher | None = None,
) -> ExecutionPlan:
    """Send the plan for user approval. Handle feedback/revision loop.

    Returns the approved ExecutionPlan.
    """
    plan_md = plan_to_markdown(plan)
    await ws_send(ws, "approval_required", {
        "plan": plan_md,
        "user_summary": plan.user_summary,
    })
    revision_count = 0

    while True:
        msg = await dispatcher.wait_for_approval() if dispatcher else None
        if msg is None:
            raise WebSocketDisconnect()

        if msg.get("type") == "approve":
            logger.info("Plan approved by user")
            return plan

        if msg.get("type") == "user_message":
            # User sent feedback — revise the plan
            feedback = msg.get("content", "")
            revision_count += 1

            if revision_count > _MAX_REVISIONS:
                logger.warning("Max plan revisions reached (%d)", _MAX_REVISIONS)
                await ws_send(ws, "error", {
                    "message": (
                        f"Maximum revision limit ({_MAX_REVISIONS}) reached. "
                        "Please start a new session."
                    ),
                    "recoverable": False,
                })
                raise WebSocketDisconnect()

            await ws_send(ws, "plan_rejected", {
                "feedback": feedback,
                "stage": "planning",
            })

            revision_context = (
                f"PREVIOUS PLAN:\n{plan.model_dump_json(indent=2)}\n\n"
                f"USER FEEDBACK:\n{feedback}"
            )
            plan = await create_plan(
                task=task,
                repo_root=repo_root,
                llm_client=llm_client,
                context=context,
                revision_context=revision_context,
                ws=ws,
                dispatcher=dispatcher,
                refiner=refiner,
                test_command=test_command,
                expert_llm_client=expert_llm_client,
                request_llm_client=request_llm_client,
            )
            plan_md = plan_to_markdown(plan)
            await ws_send(ws, "plan_revision", {
                "review_feedback": feedback,
                "revision_number": revision_count,
            })
            await ws_send(ws, "approval_required", {
                "plan": plan_md,
                "user_summary": plan.user_summary,
            })
            continue


# ── Step dependency analysis ──────────────────────────────────────

# Tools that act as barriers — they depend on ALL prior steps completing.
_BARRIER_TOOLS = frozenset({"run_tests", "run_lint", "format_code", "run_command"})


def _build_step_groups(
    steps: list[PlanStep],
) -> list[list[PlanStep]]:
    """Group plan steps by dependency for parallel execution.

    Rules:
    - Same ``file_path`` → sequential (same group boundary).
    - Step B's instruction/context mentions step A's ``file_path``
      → cross-file dependency.
    - Barrier tools (run_tests, run_lint, etc.) depend on ALL prior steps.
    - Steps with no dependency on each other land in the same parallel group.

    Returns a list of groups.  Steps within a group are independent
    and can run concurrently.
    """
    if not steps:
        return []

    # Track which group each step belongs to
    group_idx: list[int] = []  # group index for each step

    # Map file_path → latest step index that touches it
    file_owners: dict[str, int] = {}

    for i, step in enumerate(steps):
        max_dep_group = -1

        # Barrier tool: depends on everything before it
        if step.tool in _BARRIER_TOOLS:
            if group_idx:
                max_dep_group = max(group_idx)
            group_idx.append(max_dep_group + 1)
            # After a barrier, subsequent steps start fresh
            file_owners.clear()
            continue

        # Same file_path → sequential dependency
        if step.file_path and step.file_path in file_owners:
            dep_step = file_owners[step.file_path]
            max_dep_group = max(max_dep_group, group_idx[dep_step])

        # Cross-file reference: check if instruction/context mentions
        # any previously-touched file
        searchable = (step.instruction or "") + " " + (step.file_path or "")
        for fpath, dep_step in file_owners.items():
            if fpath and fpath in searchable:
                max_dep_group = max(max_dep_group, group_idx[dep_step])

        group_idx.append(max_dep_group + 1)

        # Register this step's file
        if step.file_path:
            file_owners[step.file_path] = i

    # Collect groups
    num_groups = max(group_idx) + 1 if group_idx else 0
    groups: list[list[PlanStep]] = [[] for _ in range(num_groups)]
    for i, step in enumerate(steps):
        groups[group_idx[i]].append(step)

    return groups


# ── Phase 4: Per-Step Execution ────────────────────────────────────


async def _execute_plan(
    plan: ExecutionPlan,
    task: str,
    repo_root: str,
    ws: WebSocket,
    llm_client: "LLMClient",
    context: str,
    branch_name: str,
    base_branch: str = "",
    conversation_logger: Callable | None = None,
    session_id: str = "",
    expert_llm_client: "LLMClient | None" = None,
    dispatcher: WSMessageDispatcher | None = None,
) -> str:
    """Execute each plan step sequentially with a constrained LLM."""
    if dispatcher:
        dispatcher.enter_execution_mode()
    tool_executor = make_tool_executor(
        repo_root, ws, session_id, llm_client=llm_client,
        dispatcher=dispatcher,
    )
    total_steps = len(plan.steps)
    all_executed = []
    step_explanations: list[str] = []
    completed_descriptions: list[str] = []
    step_artifacts: dict[str, str] = {}  # {relative_path: file_content}
    _artifacts_lock = asyncio.Lock()  # guards shared state in parallel groups

    # Build the system prompt once (shared across all steps)
    # Use execution context (framework guide + custom docs) — step instructions
    # are specific enough that project_context.md is not needed here.
    system_prompt = build_step_system_prompt(
        load_execution_context(repo_root),
        naming_conventions=getattr(plan, "naming_conventions", ""),
        name_registry=getattr(plan, "name_registry", ""),
    )

    # Callbacks for WebSocket progress + conversation logging.
    cb = build_workflow_callbacks(
        ws, conversation_logger=conversation_logger,
    )

    # ── Helper: execute a single step with a given client/tools ─────
    async def _run_step(
        step, client, tools, executor, sys_prompt,
        label_prefix: str = "",
    ):
        """Execute one plan step, collecting artifacts and progress."""
        step_label = f"{label_prefix}Step {step.step_number}"
        logger.info(
            "Executing %s/%d: %s %s — %s",
            step_label, total_steps, step.tool,
            step.file_path, step.instruction[:80],
        )

        await ws_send(ws, "checkpoint", {
            "step_index": step.step_number - 1,
            "step_description": f"{step_label}: {step.instruction[:100]}",
            "status": "running",
            "head_commit_sha": None,
        })

        user_msg = build_step_user_message(
            step, completed_descriptions, total_steps,
            step_artifacts=step_artifacts,
        )
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_msg},
        ]

        def _build_step_refresh(current_messages: list[dict]) -> list[dict]:
            """Rebuild message list from fresh disk state for context refresh."""
            fresh_ctx = load_execution_context(repo_root)
            fresh_sys = build_step_system_prompt(
                fresh_ctx,
                naming_conventions=getattr(plan, "naming_conventions", ""),
                name_registry=getattr(plan, "name_registry", ""),
            )
            fresh_user = build_step_user_message(
                step, completed_descriptions, total_steps,
                step_artifacts=step_artifacts,
            )
            pad = scratchpad.read_scratchpad(repo_root, session_id)
            new_messages: list[dict] = [
                {"role": "system", "content": fresh_sys},
                {"role": "user", "content": fresh_user},
            ]
            if pad:
                new_messages.append({
                    "role": "user",
                    "content": f"[CONTEXT REFRESHED]\n\n{pad}",
                })
            else:
                new_messages.append({
                    "role": "user",
                    "content": (
                        "[CONTEXT REFRESHED]\n\n"
                        "Continue working on the current step."
                    ),
                })
            ws_send_nowait(ws, "context_refreshed", {
                "message": "Step context refreshed.",
            })
            return new_messages

        executed, explanation = await client.chat_with_tools(
            messages=messages,
            tools=tools,
            tool_executor_fn=executor,
            max_turns=settings.implementation_max_turns,
            max_tokens=settings.implementation_max_tokens,
            on_tool_call=cb.on_tool_call,
            on_tool_result=cb.on_tool_result,
            on_content=cb.on_content,
            on_thinking=cb.on_thinking,
            on_metrics=cb.on_metrics,
            on_context_refresh=_build_step_refresh,
            dispatcher=dispatcher,
        )

        # Update shared state under lock for parallel safety
        async with _artifacts_lock:
            all_executed.extend(executed)
            if explanation.strip():
                step_explanations.append(
                    f"{step_label}: {explanation.strip()}"
                )
            completed_descriptions.append(
                f"{step_label}: {step.instruction}"
            )

            # Collect files created/modified for cross-step context
            artifact_budget = int(
                settings._active_context_window * 0.10 * 3.5
            )
            for tc in executed:
                if tc.tool_name in ("create_file", "edit_file"):
                    fpath = tc.parameters.get("path", "")
                    if fpath:
                        full = os.path.join(repo_root, fpath)
                        try:
                            if os.path.isfile(full):
                                content = await asyncio.to_thread(
                                    Path(full).read_text,
                                    encoding="utf-8",
                                    errors="replace",
                                )
                                step_artifacts[fpath] = content
                        except Exception:
                            pass

            while (
                sum(len(c) for c in step_artifacts.values()) > artifact_budget
                and step_artifacts
            ):
                oldest_key = next(iter(step_artifacts))
                del step_artifacts[oldest_key]

        await ws_send(ws, "checkpoint", {
            "step_index": step.step_number - 1,
            "step_description": f"{step_label}: {step.instruction[:100]}",
            "status": "completed",
            "head_commit_sha": None,
        })

    # ── TDD three-phase execution ─────────────────────────────────
    tdd_active = (
        settings.enable_tdd
        and plan.tdd_test_steps
        and expert_llm_client is not None
        and settings._active_context_window > 32768
    )

    if tdd_active:
        from lean_ai.workflow.tdd import evaluate_test_dispute

        total_steps = len(plan.tdd_test_steps) + len(plan.steps)

        # ── Phase A: Expert writes tests ──────────────────────────
        await ws_send(ws, "stage_status", {
            "stage": "tdd_test_writing",
            "status": "running",
            "summary": (
                f"TDD: Expert writing {len(plan.tdd_test_steps)} "
                f"test step(s)..."
            ),
        })

        test_tool_executor = make_tool_executor(
            repo_root, ws, session_id,
            llm_client=expert_llm_client,
            dispatcher=dispatcher,
        )
        test_system_prompt = build_step_system_prompt(
            load_execution_context(repo_root),
            naming_conventions=getattr(plan, "naming_conventions", ""),
            name_registry=getattr(plan, "name_registry", ""),
        )

        for step in plan.tdd_test_steps:
            await _run_step(
                step, expert_llm_client, build_implementation_tools(),
                test_tool_executor, test_system_prompt,
                label_prefix="[TDD Test] ",
            )

        await ws_send(ws, "stage_status", {
            "stage": "tdd_test_writing",
            "status": "done",
            "summary": "TDD: All test steps complete.",
        })

        # Identify test files created for the review phase
        tdd_test_files = [
            s.file_path for s in plan.tdd_test_steps
            if s.file_path and s.tool == "create_file"
        ]

        # ── Phase B: Primary reviews tests (read-only) ───────────
        if tdd_test_files:
            await ws_send(ws, "stage_status", {
                "stage": "tdd_test_review",
                "status": "running",
                "summary": (
                    f"TDD: Primary reviewing {len(tdd_test_files)} "
                    f"test file(s)..."
                ),
            })

            review_prompt = build_tdd_review_prompt(
                load_execution_context(repo_root),
                tdd_test_files,
            )

            # Build review message — include test file contents
            review_parts = [
                "Review the following test files. Use "
                "request_test_change for any flawed tests, or call "
                "task_complete if all tests look correct.\n"
            ]
            for tf in tdd_test_files:
                full_path = os.path.join(repo_root, tf)
                try:
                    with open(full_path, encoding="utf-8") as f:
                        review_parts.append(
                            f"\n--- {tf} ---\n```\n{f.read()}\n```"
                        )
                except Exception:
                    review_parts.append(f"\n--- {tf} --- (could not read)")

            # Dispute callback for review phase
            plan_context_md = plan_to_markdown(plan)

            async def _review_dispute(arguments: dict) -> str:
                return await evaluate_test_dispute(
                    test_file=arguments["test_file"],
                    test_function=arguments["test_function"],
                    reason=arguments["reason"],
                    repo_root=repo_root,
                    expert_client=expert_llm_client,
                    ws=ws,
                    session_id=session_id,
                    dispatcher=dispatcher,
                    plan_context=plan_context_md,
                    step_artifacts=step_artifacts,
                )

            review_executor = make_tool_executor(
                repo_root, ws, session_id,
                llm_client=llm_client,
                dispatcher=dispatcher,
                tdd_protect_tests=True,
                on_test_dispute=_review_dispute,
            )

            review_messages = [
                {"role": "system", "content": review_prompt},
                {"role": "user", "content": "\n".join(review_parts)},
            ]
            await llm_client.chat_with_tools(
                messages=review_messages,
                tools=build_tdd_implementation_tools(),
                tool_executor_fn=review_executor,
                max_turns=settings.implementation_max_turns,
                max_tokens=settings.implementation_max_tokens,
                on_tool_call=cb.on_tool_call,
                on_tool_result=cb.on_tool_result,
                on_content=cb.on_content,
                on_thinking=cb.on_thinking,
                on_metrics=cb.on_metrics,
                dispatcher=dispatcher,
            )

            await ws_send(ws, "stage_status", {
                "stage": "tdd_test_review",
                "status": "done",
                "summary": "TDD: Test review complete.",
            })

        # ── Phase C: Primary implements code ──────────────────────
        await ws_send(ws, "stage_status", {
            "stage": "tdd_implementation",
            "status": "running",
            "summary": (
                f"TDD: Primary implementing {len(plan.steps)} "
                f"step(s)..."
            ),
        })

        tdd_impl_prompt = build_tdd_step_system_prompt(
            load_execution_context(repo_root),
            naming_conventions=getattr(plan, "naming_conventions", ""),
            name_registry=getattr(plan, "name_registry", ""),
        )
        for step in plan.steps:
            impl_executor = make_tool_executor(
                repo_root, ws, session_id,
                llm_client=llm_client,
                dispatcher=dispatcher,
                tdd_protect_tests=True,
            )

            await _run_step(
                step, llm_client, build_implementation_tools(),
                impl_executor, tdd_impl_prompt,
                label_prefix="[TDD Impl] ",
            )

        await ws_send(ws, "stage_status", {
            "stage": "tdd_implementation",
            "status": "done",
            "summary": "TDD: Implementation complete.",
        })

    else:
        # ── Normal (non-TDD) execution ────────────────────────────
        # Group independent steps for parallel execution when
        # num_parallel > 1.  Barrier tools (run_tests, etc.) force
        # sequential boundaries.
        step_groups = _build_step_groups(plan.steps)

        for group in step_groups:
            if len(group) == 1 or settings.num_parallel <= 1:
                for step in group:
                    await _run_step(
                        step, llm_client, build_implementation_tools(),
                        tool_executor, system_prompt,
                    )
            else:
                # Run independent steps concurrently
                logger.info(
                    "Parallel group: %d steps (%s)",
                    len(group),
                    ", ".join(s.file_path or s.tool for s in group),
                )
                await asyncio.gather(*[
                    _run_step(
                        step, llm_client, build_implementation_tools(),
                        tool_executor, system_prompt,
                    )
                    for step in group
                ])

    # ── All steps done ───────────────────────────────────────────
    files_modified = list({
        tc.parameters.get("path", "")
        for tc in all_executed
        if tc.tool_name in ("create_file", "edit_file") and tc.parameters.get("path")
    })

    # ── Post-execution validation ──
    validation_results: dict = {}
    if files_modified and settings.enable_post_validation:
        validation_results = await _run_post_validation(repo_root, ws)

        # Attempt to fix validation failures via LLM
        if (
            validation_results
            and any(not r["success"] for r in validation_results.values())
            and settings.post_validation_max_retries > 0
        ):
            validation_results = await _run_validation_fix_loop(
                repo_root, ws, llm_client, context,
                validation_results, session_id,
                conversation_logger=conversation_logger,
                expert_llm_client=expert_llm_client,
                dispatcher=dispatcher,
                allowed_files=plan.affected_files,
            )

    # Check for incomplete.md
    incomplete_path = os.path.join(repo_root, ".lean_ai", "incomplete.md")
    incomplete_content = ""
    if os.path.isfile(incomplete_path):
        try:
            with open(incomplete_path, encoding="utf-8") as f:
                incomplete_content = f.read()
        except Exception:
            pass

    summary = (
        f"Completed {len(plan.steps)} plan steps, "
        f"{len(all_executed)} tool calls. "
        f"Files modified: {', '.join(files_modified) if files_modified else 'none'}."
    )
    if step_explanations:
        summary += "\n\n" + "\n".join(step_explanations)
    if incomplete_content:
        summary += (
            "\n\n⚠️ Some steps had issues — see "
            f".lean_ai/incomplete.md:\n{incomplete_content}"
        )
    if validation_results:
        failed = {k: r for k, r in validation_results.items() if not r["success"]}
        if failed:
            summary += "\n\n⚠️ Post-validation failures:"
            for name, result in failed.items():
                summary += f"\n  {name}: {result['output'][:200]}"
        else:
            summary += "\n\n✓ Post-validation passed."

    # ── Incremental project_context.md update ──
    if files_modified and settings.enable_project_context:
        await ws_send(ws, "stage_status", {
            "stage": "context_update",
            "status": "running",
            "summary": f"Updating project context with {len(files_modified)} modified file(s)...",
        })
        try:
            from lean_ai.context.generation import update_project_context

            ctx_path = await update_project_context(
                repo_root, files_modified, llm_client,
            )
            if ctx_path:
                logger.info(
                    "project_context.md updated with %d modified files",
                    len(files_modified),
                )
                await ws_send(ws, "stage_status", {
                    "stage": "context_update",
                    "status": "done",
                    "summary": "Project context updated.",
                })
            else:
                logger.info("project_context.md update skipped (no changes needed)")
                await ws_send(ws, "stage_status", {
                    "stage": "context_update",
                    "status": "done",
                    "summary": "Project context update skipped (no changes needed).",
                })
        except Exception as exc:
            logger.warning("Incremental context update failed (non-fatal): %s", exc)
            await ws_send(ws, "stage_status", {
                "stage": "context_update",
                "status": "done",
                "summary": f"Project context update failed: {exc}",
            })

    # ── Auto-push to linked integrations (fire-and-forget) ──
    if settings.enable_integrations and settings.integration_auto_push:
        asyncio.create_task(_auto_push_integration(repo_root, session_id))

    complete_data: dict = {"summary": summary, "files_modified": files_modified}
    if branch_name:
        complete_data["plan_branch"] = branch_name
    if base_branch:
        complete_data["base_branch"] = base_branch
    await ws_send(ws, "complete", complete_data)
    logger.info(
        "Workflow complete: %d steps, %d tool calls, %d files",
        len(plan.steps), len(all_executed), len(files_modified),
    )

    # Build commit message
    task_summary = task[:72].replace("\n", " ")
    commit_msg = f"lean-ai: {task_summary}"
    if files_modified:
        commit_msg += f"\n\nFiles modified: {', '.join(files_modified)}"
    return commit_msg


async def _auto_push_integration(repo_root: str, session_id: str) -> None:
    """Push session summary to any linked external tasks (best-effort)."""
    try:
        from lean_ai.integrations.db import get_integrations_db, get_linked_tasks
        from lean_ai.integrations.registry import get_integration
        from lean_ai.integrations.summary import build_session_summary

        db = await get_integrations_db()
        try:
            links = await get_linked_tasks(db, session_id=session_id)
        finally:
            await db.close()

        if not links:
            return

        summary = await build_session_summary(repo_root, session_id)
        if not summary:
            return

        for link in links:
            integration = get_integration(link["integration_name"])
            if integration:
                try:
                    await integration.push_session_summary(
                        link["external_id"], summary,
                    )
                    logger.info(
                        "Auto-pushed session %s to %s/%s",
                        session_id, link["integration_name"], link["external_id"],
                    )
                except Exception:
                    logger.debug(
                        "Auto-push failed for %s/%s",
                        link["integration_name"], link["external_id"],
                        exc_info=True,
                    )
    except Exception:
        logger.debug("Auto-push integration failed (non-fatal)", exc_info=True)

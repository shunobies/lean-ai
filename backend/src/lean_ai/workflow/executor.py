"""Plan step execution engine: sequential and parallel step execution.

Handles both normal execution and TDD three-phase execution
(expert writes tests → primary reviews → primary implements).
"""

import asyncio
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import WebSocket

from lean_ai.config import settings
from lean_ai.llm.plan_schema import (
    ExecutionPlan,
    PlanStep,
    format_name_registry_for_prompt,
    format_naming_conventions_for_prompt,
    plan_to_markdown,
)
from lean_ai.llm.tool_definitions import (
    build_implementation_tools,
    build_tdd_implementation_tools,
)
from lean_ai.routers.context_helpers import load_execution_context
from lean_ai.tools import scratchpad
from lean_ai.tools.journal import read_journal
from lean_ai.workflow.callbacks import build_workflow_callbacks
from lean_ai.workflow.hooks import (
    auto_extract_session_memories,
    auto_push_integration,
)
from lean_ai.workflow.prompts import (
    build_step_system_prompt,
    build_step_user_message,
    build_tdd_review_prompt,
    build_tdd_step_system_prompt,
    build_tdd_test_writing_prompt,
)
from lean_ai.workflow.tool_executor import make_tool_executor
from lean_ai.workflow.validation import (
    _run_post_validation,
    _run_validation_fix_loop,
)
from lean_ai.workflow.ws_dispatcher import WSMessageDispatcher
from lean_ai.workflow.ws_handler import ws_send, ws_send_nowait

if TYPE_CHECKING:
    from lean_ai.llm.facade import LLMClient

logger = logging.getLogger(__name__)

# Tools that act as barriers — they depend on ALL prior steps completing.
_BARRIER_TOOLS = frozenset({
    "run_tests", "run_lint", "format_code", "run_command",
})


def _normalize_path(p: str) -> str:
    """Strip leading ``./`` for consistent path comparison."""
    while p.startswith("./"):
        p = p[2:]
    return p


def _path_mentioned_in(fpath: str, text: str) -> bool:
    """Check if *fpath* is explicitly referenced in *text*.

    Uses boundary-aware matching so that ``a.py`` does not falsely
    match ``baa.py`` or other longer strings.
    """
    if not fpath or not text:
        return False
    escaped = re.escape(_normalize_path(fpath))
    # Boundary: start-of-string or common delimiters.
    # Trailing boundary excludes '.' to prevent config.py matching config.py.bak
    pattern = r"(?:^|[\s`\"'(,\[])" + escaped + r"(?:[\s`\"')\],;:\n]|$)"
    return bool(re.search(pattern, text))


def _build_step_groups(
    steps: list[PlanStep],
) -> list[list[PlanStep]]:
    """Group plan steps by dependency for parallel execution.

    Rules:
    - Same ``file_path`` → sequential (same group boundary).
    - Step B's instruction/context mentions step A's ``file_path``
      → cross-file dependency (boundary-aware matching).
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

    # Minimum group for steps following the most recent barrier
    min_group_after_barrier = 0

    for i, step in enumerate(steps):
        max_dep_group = min_group_after_barrier - 1

        # Barrier tool: depends on everything before it
        if step.tool in _BARRIER_TOOLS:
            if group_idx:
                max_dep_group = max(max_dep_group, max(group_idx))
            group_idx.append(max_dep_group + 1)
            # Steps after a barrier must come after the barrier group
            min_group_after_barrier = max_dep_group + 2
            file_owners.clear()
            continue

        step_path = _normalize_path(step.file_path or "")

        # Same file_path → sequential dependency
        if step_path and step_path in file_owners:
            dep_step = file_owners[step_path]
            max_dep_group = max(max_dep_group, group_idx[dep_step])

        # Cross-file reference: check if instruction/context mentions
        # any previously-touched file (boundary-aware)
        searchable = (
            (step.instruction or "") + " " + (step.file_path or "")
        )
        for fpath, dep_step in file_owners.items():
            if _path_mentioned_in(fpath, searchable):
                max_dep_group = max(max_dep_group, group_idx[dep_step])

        group_idx.append(max_dep_group + 1)

        # Register this step's file
        if step_path:
            file_owners[step_path] = i

    # Collect groups
    num_groups = max(group_idx) + 1 if group_idx else 0
    groups: list[list[PlanStep]] = [[] for _ in range(num_groups)]
    for i, step in enumerate(steps):
        groups[group_idx[i]].append(step)

    return groups


async def execute_plan(
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
    _artifacts_lock = asyncio.Lock()

    # Send execution checklist to the extension for progress UI
    checklist_steps = []
    for step in plan.steps:
        checklist_steps.append({
            "step_index": step.step_number - 1,
            "description": step.instruction[:120],
            "tool": step.tool,
            "file_path": step.file_path or "",
        })
    if getattr(plan, "tdd_test_steps", None):
        tdd_steps = [
            {
                "step_index": s.step_number - 1,
                "description": f"[TEST] {s.instruction[:110]}",
                "tool": s.tool,
                "file_path": s.file_path or "",
            }
            for s in plan.tdd_test_steps
        ]
        checklist_steps = tdd_steps + checklist_steps
    await ws_send(ws, "execution_checklist", {
        "steps": checklist_steps,
        "total": len(checklist_steps),
    })

    # Build the system prompt once (shared across all steps)
    naming_text = format_naming_conventions_for_prompt(
        getattr(plan, "naming_conventions", []) or [],
    )
    name_registry_text = format_name_registry_for_prompt(
        getattr(plan, "name_registry", []) or [],
    )
    system_prompt = build_step_system_prompt(
        load_execution_context(repo_root),
        naming_conventions=naming_text,
        name_registry=name_registry_text,
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
            "step_description": (
                f"{step_label}: {step.instruction[:100]}"
            ),
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

        def _build_step_refresh(
            current_messages: list[dict],
        ) -> list[dict]:
            """Rebuild messages from fresh disk state for context refresh."""
            fresh_ctx = load_execution_context(repo_root)
            fresh_sys = build_step_system_prompt(
                fresh_ctx,
                naming_conventions=naming_text,
                name_registry=name_registry_text,
            )
            fresh_user = build_step_user_message(
                step, completed_descriptions, total_steps,
                step_artifacts=step_artifacts,
            )
            pad = scratchpad.read_scratchpad(repo_root, session_id)
            jrnl = read_journal(repo_root, session_id)
            new_messages: list[dict] = [
                {"role": "system", "content": fresh_sys},
                {"role": "user", "content": fresh_user},
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
                        "Continue working on the current step."
                    ),
                })
            ws_send_nowait(ws, "context_refreshed", {
                "message": "Step context refreshed.",
            })
            return new_messages

        def _build_step_reminder() -> str:
            parts = [
                f"REMINDER — STEP {step.step_number} OF {total_steps}",
                f"Tool: {step.tool}",
            ]
            if step.file_path:
                parts.append(f"File: {step.file_path}")
            parts.append(f"Instruction: {step.instruction}")
            return "\n".join(parts)

        executed, explanation = await client.chat_with_tools(
            messages=messages,
            tools=tools,
            tool_executor_fn=executor,
            max_turns=settings.implementation_max_turns,
            max_tokens=settings.implementation_max_tokens,
            task_reminder=_build_step_reminder,
            reminder_interval=1,
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
                sum(len(c) for c in step_artifacts.values())
                > artifact_budget
                and step_artifacts
            ):
                oldest_key = next(iter(step_artifacts))
                del step_artifacts[oldest_key]

        await ws_send(ws, "checkpoint", {
            "step_index": step.step_number - 1,
            "step_description": (
                f"{step_label}: {step.instruction[:100]}"
            ),
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
        await _run_tdd_execution(
            plan=plan,
            repo_root=repo_root,
            ws=ws,
            llm_client=llm_client,
            expert_llm_client=expert_llm_client,
            session_id=session_id,
            dispatcher=dispatcher,
            cb=cb,
            step_artifacts=step_artifacts,
            run_step=_run_step,
        )
    else:
        # ── Normal (non-TDD) execution ────────────────────────────
        step_groups = _build_step_groups(plan.steps)

        for group in step_groups:
            if len(group) == 1 or settings.num_parallel <= 1:
                for step in group:
                    await _run_step(
                        step, llm_client,
                        build_implementation_tools(),
                        tool_executor, system_prompt,
                    )
            else:
                # Run independent steps concurrently
                logger.info(
                    "Parallel group: %d steps (%s)",
                    len(group),
                    ", ".join(
                        s.file_path or s.tool for s in group
                    ),
                )
                await asyncio.gather(*[
                    _run_step(
                        step, llm_client,
                        build_implementation_tools(),
                        tool_executor, system_prompt,
                    )
                    for step in group
                ])

    # ── All steps done ───────────────────────────────────────────
    files_modified = list({
        tc.parameters.get("path", "")
        for tc in all_executed
        if tc.tool_name in ("create_file", "edit_file")
        and tc.parameters.get("path")
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
    incomplete_path = os.path.join(
        repo_root, ".lean_ai", "incomplete.md",
    )
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
        f"Files modified: "
        f"{', '.join(files_modified) if files_modified else 'none'}."
    )
    if step_explanations:
        summary += "\n\n" + "\n".join(step_explanations)
    if incomplete_content:
        summary += (
            "\n\n⚠️ Some steps had issues — see "
            f".lean_ai/incomplete.md:\n{incomplete_content}"
        )
    if validation_results:
        failed = {
            k: r for k, r in validation_results.items()
            if not r["success"]
        }
        if failed:
            summary += "\n\n⚠️ Post-validation failures:"
            for name, result in failed.items():
                summary += f"\n  {name}: {result['output'][:200]}"
        else:
            summary += "\n\n✓ Post-validation passed."

    journal_content = read_journal(repo_root, session_id)
    if journal_content:
        summary += f"\n\nSession Journal:\n{journal_content}"

    # ── Incremental project_context.md update ──
    if files_modified and settings.enable_project_context:
        await _update_project_context(
            repo_root, ws, llm_client, files_modified,
        )

    # ── Auto-push to linked integrations (fire-and-forget) ──
    if settings.enable_integrations and settings.integration_auto_push:
        asyncio.create_task(
            auto_push_integration(repo_root, session_id),
        )

    # ── Cross-session memory extraction (fire-and-forget) ──
    if settings.enable_session_memory:
        asyncio.create_task(
            auto_extract_session_memories(
                repo_root, session_id, task, plan, llm_client,
                files_modified, validation_results,
            ),
        )

    complete_data: dict = {
        "summary": summary, "files_modified": files_modified,
    }
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


async def _run_tdd_execution(
    *,
    plan: ExecutionPlan,
    repo_root: str,
    ws: WebSocket,
    llm_client: "LLMClient",
    expert_llm_client: "LLMClient",
    session_id: str,
    dispatcher: WSMessageDispatcher | None,
    cb,
    step_artifacts: dict[str, str],
    run_step: Callable,
) -> None:
    """TDD three-phase execution: expert tests → review → implement."""
    from lean_ai.workflow.tdd import evaluate_test_dispute

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
    test_system_prompt = build_tdd_test_writing_prompt(
        load_execution_context(repo_root),
        implementation_plan_md=plan_to_markdown(plan, include_context=False),
        naming_conventions=format_naming_conventions_for_prompt(
            getattr(plan, "naming_conventions", []) or [],
        ),
        name_registry=format_name_registry_for_prompt(
            getattr(plan, "name_registry", []) or [],
        ),
    )

    for step in plan.tdd_test_steps:
        await run_step(
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
                review_parts.append(
                    f"\n--- {tf} --- (could not read)"
                )

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
        naming_conventions=format_naming_conventions_for_prompt(
            getattr(plan, "naming_conventions", []) or [],
        ),
        name_registry=format_name_registry_for_prompt(
            getattr(plan, "name_registry", []) or [],
        ),
    )
    for step in plan.steps:
        impl_executor = make_tool_executor(
            repo_root, ws, session_id,
            llm_client=llm_client,
            dispatcher=dispatcher,
            tdd_protect_tests=True,
        )

        await run_step(
            step, llm_client, build_implementation_tools(),
            impl_executor, tdd_impl_prompt,
            label_prefix="[TDD Impl] ",
        )

    await ws_send(ws, "stage_status", {
        "stage": "tdd_implementation",
        "status": "done",
        "summary": "TDD: Implementation complete.",
    })


async def _update_project_context(
    repo_root: str,
    ws: WebSocket,
    llm_client: "LLMClient",
    files_modified: list[str],
) -> None:
    """Incremental project_context.md update after execution."""
    await ws_send(ws, "stage_status", {
        "stage": "context_update",
        "status": "running",
        "summary": (
            f"Updating project context with "
            f"{len(files_modified)} modified file(s)..."
        ),
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
            logger.info(
                "project_context.md update skipped (no changes needed)",
            )
            await ws_send(ws, "stage_status", {
                "stage": "context_update",
                "status": "done",
                "summary": (
                    "Project context update skipped "
                    "(no changes needed)."
                ),
            })
    except Exception as exc:
        logger.warning(
            "Incremental context update failed (non-fatal): %s", exc,
        )
        await ws_send(ws, "stage_status", {
            "stage": "context_update",
            "status": "done",
            "summary": f"Project context update failed: {exc}",
        })

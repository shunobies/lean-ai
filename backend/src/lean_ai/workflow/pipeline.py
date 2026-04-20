"""Plan-driven agentic workflow: plan → approve → execute per step.

The planner does ALL investigatory work (reads files, explores the codebase,
designs changes).  It produces a structured ExecutionPlan where each step
maps to one tool call.  After user approval, a constrained LLM executor
handles each step in 1-3 turns — translating the planner's detailed
instruction into a single tool invocation.
"""

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import WebSocket, WebSocketDisconnect

from lean_ai.config import settings
from lean_ai.llm.plan_schema import ExecutionPlan, plan_to_markdown
from lean_ai.llm.planner import create_plan
from lean_ai.workflow.callbacks import build_workflow_callbacks
from lean_ai.workflow.executor import execute_plan
from lean_ai.workflow.fix_mode import _run_fix
from lean_ai.workflow.validation import _effective_post_commands
from lean_ai.workflow.ws_dispatcher import WSMessageDispatcher
from lean_ai.workflow.ws_handler import ws_send

if TYPE_CHECKING:
    from lean_ai.llm.facade import LLMClient
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

    # Clarifications happen in the chat's two-round Suggested Agent Prompt
    # flow — by the time a task reaches the planner, the user has already
    # answered every question the chat surfaced. The planner does NOT run
    # its own clarify step: Phase 1 rewrites the task into an 8-section
    # ScopeDocument, recording any under-specified details as ASSUMPTIONS
    # with verify_hints for Phase 2 to falsify.

    # ── Phase 2: Plan ────────────────────────────────────────────
    await ws_send(ws, "stage_change", {"stage": "planning"})
    plan_commands = _effective_post_commands(repo_root)

    # Planning-specific streaming callbacks — include streaming flag
    # so the extension can distinguish token-level updates from
    # per-turn bulk content used during execution.
    planning_cb = build_workflow_callbacks(ws, streaming=True)

    plan = await create_plan(
        task=task,
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
        task=task,
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
    return await execute_plan(
        plan=approved_plan,
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
        dispatcher=dispatcher,
    )


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
        "plan_validation_warnings": list(plan.plan_validation_warnings),
    })
    revision_count = 0

    while True:
        msg = (
            await dispatcher.wait_for_approval() if dispatcher else None
        )
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
                logger.warning(
                    "Max plan revisions reached (%d)", _MAX_REVISIONS,
                )
                await ws_send(ws, "error", {
                    "message": (
                        f"Maximum revision limit ({_MAX_REVISIONS}) "
                        "reached. Please start a new session."
                    ),
                    "recoverable": False,
                })
                raise WebSocketDisconnect()

            await ws_send(ws, "plan_rejected", {
                "feedback": feedback,
                "stage": "planning",
            })

            revision_context = (
                f"PREVIOUS PLAN:\n"
                f"{plan.model_dump_json(indent=2)}\n\n"
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

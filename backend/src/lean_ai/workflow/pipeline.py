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

from lean_ai.config import settings
from lean_ai.llm.plan_schema import ExecutionPlan, plan_to_markdown
from lean_ai.llm.planner import create_plan
from lean_ai.training.span_context import trace_span
from lean_ai.workflow.callbacks import build_workflow_callbacks
from lean_ai.workflow.executor import execute_plan
from lean_ai.workflow.fix_mode import _run_fix
from lean_ai.workflow.state import StateManager
from lean_ai.workflow.validation import _effective_post_commands
from lean_ai.workflow.ws_dispatcher import WSMessageDispatcher
from lean_ai.workflow.ws_handler import ws_send
from lean_ai.workflow.ws_protocol import WorkflowSession, WorkflowSessionClosedError

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
    session: WorkflowSession | None,
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

    # Create StateManager for this session — used throughout the workflow
    state_manager = StateManager(session_id)

    async with trace_span(
        span_type="session",
        span_name=session_id,
        session_id=session_id,
        metadata={"mode": mode, "task": task},
    ) as session_span:
        # Emit a session_start fingerprint — one row per workflow invocation,
        # carrying the exact model layout used. Consumers need this to
        # partition training data by model + provider before fine-tuning.
        try:
            from lean_ai.routers.dependencies import worker_llm_client
            from lean_ai.workflow.hooks import fire_workflow_event

            fire_workflow_event(
                repo_root=repo_root,
                session_id=session_id,
                event_type="session_start",
                payload={
                    "mode": mode,
                    "task_length": len(task),
                    "primary_model": getattr(llm_client, "model_name", None),
                    "primary_provider": getattr(llm_client, "provider_name", None),
                    "expert_model": (
                        getattr(expert_llm_client, "model_name", None)
                        if expert_llm_client
                        else None
                    ),
                    "expert_provider": (
                        getattr(expert_llm_client, "provider_name", None)
                        if expert_llm_client
                        else None
                    ),
                    "request_model": (
                        getattr(request_llm_client, "model_name", None)
                        if request_llm_client
                        else None
                    ),
                    "request_provider": (
                        getattr(request_llm_client, "provider_name", None)
                        if request_llm_client
                        else None
                    ),
                    "worker_model": (
                        getattr(worker_llm_client, "model_name", None)
                        if worker_llm_client
                        else None
                    ),
                    "worker_provider": (
                        getattr(worker_llm_client, "provider_name", None)
                        if worker_llm_client
                        else None
                    ),
                    "context_window": getattr(
                        settings,
                        "_active_context_window",
                        None,
                    ),
                    "tdd_enabled": expert_llm_client is not None,
                },
            )
        except Exception:
            logger.debug("session_start event failed (non-fatal)", exc_info=True)

        # Validate TDD requirements: expert model + sufficient context window
        if expert_llm_client is None:
            logger.warning(
                "TDD mode enabled but no expert model configured — falling back to normal mode",
            )
            await ws_send(
                session,
                "stage_status",
                {
                    "stage": "tdd",
                    "status": "done",
                    "summary": ("TDD mode requires an expert model. Falling back to normal mode."),
                },
            )

        if expert_llm_client is not None and settings._active_context_window <= 32768:
            logger.warning(
                "TDD mode disabled — context window too small (%d). "
                "TDD requires cross-phase context that exceeds 32k budget.",
                settings._active_context_window,
            )
            await ws_send(
                session,
                "stage_status",
                {
                    "stage": "tdd",
                    "status": "done",
                    "summary": (
                        "TDD mode auto-disabled: context window too small "
                        f"({settings._active_context_window}). "
                        "Increase to 64k+ to enable TDD."
                    ),
                },
            )

        # Log the initial task
        if conversation_logger:
            await conversation_logger("user", task)

        if mode in ("fix", "request"):
            return await _run_fix(
                task=task,
                repo_root=repo_root,
                ws=session,
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
        await ws_send(session, "stage_change", {"stage": "planning"})
        plan_commands = _effective_post_commands(repo_root)

        # Planning-specific streaming callbacks — include streaming flag
        # so the extension can distinguish token-level updates from
        # per-turn bulk content used during execution.
        planning_cb = build_workflow_callbacks(session=session, streaming=True)

        async with trace_span(
            span_type="phase",
            span_name="planning",
            session_id=session_id,
            parent_span=session_span,
        ) as _planning_span:
            plan = await create_plan(
                task=task,
                repo_root=repo_root,
                llm_client=llm_client,
                context=context,
                ws=session,
                dispatcher=dispatcher,
                refiner=refiner,
                test_command=plan_commands.get("test", ""),
                session_id=session_id,
                expert_llm_client=expert_llm_client,
                on_content=planning_cb.on_content,
                on_thinking=planning_cb.on_thinking,
                on_tool_call=planning_cb.on_tool_call,
                on_tool_result=planning_cb.on_tool_result,
                on_metrics=planning_cb.on_metrics,
                on_metrics_reset=planning_cb.on_metrics_reset,
            )

        # Save state after planning phase
        state = state_manager.get_state()
        state.current_phase = "planning"
        state.set_current_plan(plan.model_dump())
        state_manager.save()

        # ── Phase 3: Approve ─────────────────────────────────────────
        async with trace_span(
            span_type="phase",
            span_name="approval",
            session_id=session_id,
            parent_span=session_span,
        ) as _approval_span:
            approved_plan = await _wait_for_approval(
                plan=plan,
                task=task,
                repo_root=repo_root,
                llm_client=llm_client,
                context=context,
                ws=session,
                refiner=refiner,
                test_command=plan_commands.get("test", ""),
                expert_llm_client=expert_llm_client,
                dispatcher=dispatcher,
                state_manager=state_manager,
            )

        # Save state after approval phase
        state = state_manager.get_state()
        state.current_phase = "approval"
        state_manager.save()

        # ── Phase 4: Execute per-step ────────────────────────────────
        await ws_send(session, "stage_change", {"stage": "implementing"})
        async with trace_span(
            span_type="phase",
            span_name="execution",
            session_id=session_id,
            parent_span=session_span,
        ) as _execution_span:
            return await execute_plan(
                plan=approved_plan,
                task=task,
                repo_root=repo_root,
                ws=session,
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


def _approval_feedback(msg: dict) -> str | None:
    """Extract user feedback from approval-phase messages."""
    if msg.get("type") not in ("user_message", "reject", "feedback"):
        return None
    for key in ("content", "feedback", "text", "message"):
        value = msg.get(key)
        if isinstance(value, str):
            return value
    return ""


async def _wait_for_approval(
    plan: ExecutionPlan,
    task: str,
    repo_root: str,
    llm_client: "LLMClient",
    context: str,
    ws: WorkflowSession,
    refiner: "PromptRefiner | None" = None,
    test_command: str = "",
    expert_llm_client: "LLMClient | None" = None,
    dispatcher: WSMessageDispatcher | None = None,
    state_manager: StateManager | None = None,
) -> ExecutionPlan:
    """Send the plan for user approval. Handle feedback/revision loop.

    Returns the approved ExecutionPlan.
    """
    plan_md = plan_to_markdown(plan)
    await ws_send(
        ws,
        "approval_required",
        {
            "plan": plan_md,
            "user_summary": plan.user_summary,
            "plan_validation_warnings": list(plan.plan_validation_warnings),
        },
    )
    revision_count = 0
    # Track the most-recent rejection so that if the user eventually
    # approves a revised plan, we can extract a `rejection` memory
    # capturing (plan_before, feedback, plan_after).
    last_rejection: tuple[str, str] | None = None

    while True:
        msg = await dispatcher.wait_for_approval() if dispatcher else None
        if msg is None:
            raise WorkflowSessionClosedError()

        if msg.get("type") == "approve":
            logger.info("Plan approved by user")
            from lean_ai.workflow.hooks import fire_plan_decision_hook

            if last_rejection is not None:
                prev_plan_json, prev_feedback = last_rejection
                fire_plan_decision_hook(
                    repo_root=repo_root,
                    session_id=state_manager.session_id,
                    llm_client=llm_client,
                    task=task,
                    plan_before=prev_plan_json,
                    feedback=prev_feedback,
                    plan_after=plan.model_dump_json(indent=2),
                    decision="approved",
                    revision_count=revision_count,
                    ws=ws,
                )
            else:
                # First-attempt approval — still log for training archive,
                # with empty feedback so memory extraction skips it.
                fire_plan_decision_hook(
                    repo_root=repo_root,
                    session_id=state_manager.session_id,
                    llm_client=llm_client,
                    task=task,
                    plan_before="",
                    feedback="",
                    plan_after=plan.model_dump_json(indent=2),
                    decision="approved",
                    revision_count=0,
                    ws=ws,
                )
            return plan

        feedback = _approval_feedback(msg)
        if feedback is not None:
            # User sent feedback — revise the plan
            revision_count += 1
            # Capture pre-revision state for later memory extraction
            # if the next revision is eventually approved.
            last_rejection = (plan.model_dump_json(indent=2), feedback)

            if revision_count > _MAX_REVISIONS:
                logger.warning(
                    "Max plan revisions reached (%d)",
                    _MAX_REVISIONS,
                )
                await ws_send(
                    ws,
                    "error",
                    {
                        "message": (
                            f"Maximum revision limit ({_MAX_REVISIONS}) "
                            "reached. Please start a new session."
                        ),
                        "recoverable": False,
                    },
                )
                raise WorkflowSessionClosedError()

            await ws_send(
                ws,
                "plan_rejected",
                {
                    "feedback": feedback,
                    "stage": "planning",
                },
            )

            revision_context = (
                f"PREVIOUS PLAN:\n{plan.model_dump_json(indent=2)}\n\nUSER FEEDBACK:\n{feedback}"
            )
            plan = await create_plan(
                task=task,
                repo_root=repo_root,
                llm_client=llm_client,
                context=context,
                revision_context=revision_context,
                previous_plan=plan,
                ws=ws,
                dispatcher=dispatcher,
                refiner=refiner,
                test_command=test_command,
                expert_llm_client=expert_llm_client,
            )
            plan_md = plan_to_markdown(plan)
            await ws_send(
                ws,
                "plan_revision",
                {
                    "review_feedback": feedback,
                    "revision_number": revision_count,
                },
            )
            await ws_send(
                ws,
                "approval_required",
                {
                    "plan": plan_md,
                    "user_summary": plan.user_summary,
                },
            )
            continue

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
from lean_ai.workflow.graph import (
    ApprovalNode,
    Continue,
    Fail,
    Node,
    NodeResult,
    Suspend,
    WorkflowEngine,
    WorkflowGraph,
)
from lean_ai.workflow.state import StateManager, WorkflowState
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


async def _detect_branching_and_request_feedback(
    state_manager: StateManager,
    new_checkpoint_id: str,
    new_parent_id: str | None,
    session_span_uuid: str,
    repo_root: str,
) -> None:
    """Detect checkpoint branching and request feedback when divergence occurs.

    When a user restores from a checkpoint and the workflow continues on a
    divergent path, the new checkpoint's parent_id will differ from the
    restored checkpoint ID. This signals a potential failure in the original
    path and captures valuable training data.

    Args:
        state_manager: The StateManager for the current session.
        new_checkpoint_id: The ID of the newly saved checkpoint.
        new_parent_id: The parent_id used when saving the new checkpoint.
        session_span_uuid: The trace span UUID for the current session.
        repo_root: The repository root path (for resolving the training DB).
    """
    state = state_manager.get_state()
    restored_id = state.session_metadata.get("restored_checkpoint_id")
    if restored_id is None:
        return

    if new_parent_id != restored_id:
        logger.info(
            "Branching detected: restored from %s but new checkpoint %s "
            "has parent_id %s — requesting feedback",
            restored_id,
            new_checkpoint_id,
            new_parent_id,
        )
        try:
            from lean_ai.training.db import get_training_db, insert_feedback

            train_db = await get_training_db(repo_root)
            try:
                await insert_feedback(
                    train_db,
                    session_id=state_manager.session_id,
                    thumbs_up=False,
                    comment=(
                        f"Checkpoint branching detected: restored from "
                        f"{restored_id}, but execution continued with "
                        f"parent_id {new_parent_id} (checkpoint {new_checkpoint_id})."
                    ),
                    tags=["branching-detected", "checkpoint-divergence"],
                    trace_span_uuid=session_span_uuid,
                )
            finally:
                await train_db.close()
        except Exception:
            logger.debug(
                "Failed to record branching feedback (non-fatal)",
                exc_info=True,
            )


# ── Graph node implementations ───────────────────────────────────


class PlanningNode(Node):
    """Node that calls create_plan to produce an ExecutionPlan."""

    def __init__(
        self,
        task: str,
        repo_root: str,
        llm_client: "LLMClient",
        context: str,
        session: WorkflowSession | None,
        dispatcher: WSMessageDispatcher | None,
        refiner: "PromptRefiner | None",
        test_command: str,
        session_id: str,
        expert_llm_client: "LLMClient | None",
    ) -> None:
        super().__init__("planning_node")
        self.task = task
        self.repo_root = repo_root
        self.llm_client = llm_client
        self.context = context
        self.session = session
        self.dispatcher = dispatcher
        self.refiner = refiner
        self.test_command = test_command
        self.session_id = session_id
        self.expert_llm_client = expert_llm_client

    async def execute(self, state: WorkflowState) -> NodeResult:
        """Run the planning phase and store the plan in state."""
        planning_cb = build_workflow_callbacks(session=self.session, streaming=True)
        try:
            async with trace_span(
                span_type="phase",
                span_name="planning",
                session_id=self.session_id,
            ) as _planning_span:
                plan = await create_plan(
                    task=self.task,
                    repo_root=self.repo_root,
                    llm_client=self.llm_client,
                    context=self.context,
                    ws=self.session,
                    dispatcher=self.dispatcher,
                    refiner=self.refiner,
                    test_command=self.test_command,
                    session_id=self.session_id,
                    expert_llm_client=self.expert_llm_client,
                    on_content=planning_cb.on_content,
                    on_thinking=planning_cb.on_thinking,
                    on_tool_call=planning_cb.on_tool_call,
                    on_tool_result=planning_cb.on_tool_result,
                    on_metrics=planning_cb.on_metrics,
                    on_metrics_reset=planning_cb.on_metrics_reset,
                )
            state.current_phase = "planning"
            state.set_current_plan(plan.model_dump())
            state.session_metadata["plan"] = plan
            state.session_metadata["planning_checkpoint_id"] = (
                state.session_metadata.get("planning_checkpoint_id")
            )
            return Continue(
                next_node_id=None,
                payload={"plan": plan.model_dump()},
            )
        except Exception as exc:
            return Fail(error=str(exc))


class ApprovalNodeExtended(ApprovalNode):
    """ApprovalNode that handles the full approval/revision loop."""

    def __init__(
        self,
        task: str,
        repo_root: str,
        llm_client: "LLMClient",
        context: str,
        session: WorkflowSession | None,
        dispatcher: WSMessageDispatcher | None,
        refiner: "PromptRefiner | None",
        test_command: str,
        expert_llm_client: "LLMClient | None",
        session_id: str,
    ) -> None:
        super().__init__("approval_node", prompt="Approve the generated plan")
        self.task = task
        self.repo_root = repo_root
        self.llm_client = llm_client
        self.context = context
        self.session = session
        self.dispatcher = dispatcher
        self.refiner = refiner
        self.test_command = test_command
        self.expert_llm_client = expert_llm_client
        self.session_id = session_id

    async def execute(self, state: WorkflowState) -> NodeResult:
        """Wait for user approval, handling feedback/revision loop."""
        plan = state.session_metadata.get("plan")
        if plan is None:
            return Fail(error="No plan found in state")

        plan_md = plan_to_markdown(plan)
        await ws_send(
            self.session,
            "approval_required",
            {
                "plan": plan_md,
                "user_summary": plan.user_summary,
                "plan_validation_warnings": list(plan.plan_validation_warnings),
            },
        )
        revision_count = 0
        last_rejection: tuple[str, str] | None = None

        while True:
            msg = await self.dispatcher.wait_for_approval() if self.dispatcher else None
            if msg is None:
                raise WorkflowSessionClosedError()

            if msg.get("type") == "approve":
                from lean_ai.workflow.hooks import fire_plan_decision_hook

                if last_rejection is not None:
                    prev_plan_json, prev_feedback = last_rejection
                    fire_plan_decision_hook(
                        repo_root=self.repo_root,
                        session_id=self.session_id,
                        llm_client=self.llm_client,
                        task=self.task,
                        plan_before=prev_plan_json,
                        feedback=prev_feedback,
                        plan_after=plan.model_dump_json(indent=2),
                        decision="approved",
                        revision_count=revision_count,
                        ws=self.session,
                    )
                else:
                    fire_plan_decision_hook(
                        repo_root=self.repo_root,
                        session_id=self.session_id,
                        llm_client=self.llm_client,
                        task=self.task,
                        plan_before="",
                        feedback="",
                        plan_after=plan.model_dump_json(indent=2),
                        decision="approved",
                        revision_count=0,
                        ws=self.session,
                    )
                state.current_phase = "approval"
                state.session_metadata["approved_plan"] = plan
                return Continue(next_node_id=None, payload={"approved": True})

            feedback = _approval_feedback(msg)
            if feedback is not None:
                revision_count += 1
                last_rejection = (plan.model_dump_json(indent=2), feedback)

                if revision_count > _MAX_REVISIONS:
                    await ws_send(
                        self.session,
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
                    self.session,
                    "plan_rejected",
                    {"feedback": feedback, "stage": "planning"},
                )

                revision_context = (
                    f"PREVIOUS PLAN:\n{plan.model_dump_json(indent=2)}\n\nUSER FEEDBACK:\n{feedback}"
                )
                plan = await create_plan(
                    task=self.task,
                    repo_root=self.repo_root,
                    llm_client=self.llm_client,
                    context=self.context,
                    revision_context=revision_context,
                    previous_plan=plan,
                    ws=self.session,
                    dispatcher=self.dispatcher,
                    refiner=self.refiner,
                    test_command=self.test_command,
                    expert_llm_client=self.expert_llm_client,
                )
                plan_md = plan_to_markdown(plan)
                state.set_current_plan(plan.model_dump())
                state.session_metadata["plan"] = plan
                await ws_send(
                    self.session,
                    "plan_revision",
                    {
                        "review_feedback": feedback,
                        "revision_number": revision_count,
                    },
                )
                await ws_send(
                    self.session,
                    "approval_required",
                    {
                        "plan": plan_md,
                        "user_summary": plan.user_summary,
                    },
                )
                continue

        return Continue(next_node_id=None)


class ExecutionNode(Node):
    """Node that calls execute_plan to carry out the approved plan."""

    def __init__(
        self,
        task: str,
        repo_root: str,
        session: WorkflowSession | None,
        llm_client: "LLMClient",
        context: str,
        branch_name: str,
        base_branch: str,
        conversation_logger: Callable | None,
        session_id: str,
        expert_llm_client: "LLMClient | None",
        dispatcher: WSMessageDispatcher | None,
        state_manager: StateManager | None = None,
    ) -> None:
        super().__init__("execution_node")
        self.task = task
        self.repo_root = repo_root
        self.session = session
        self.llm_client = llm_client
        self.context = context
        self.branch_name = branch_name
        self.base_branch = base_branch
        self.conversation_logger = conversation_logger
        self.session_id = session_id
        self.expert_llm_client = expert_llm_client
        self.dispatcher = dispatcher
        self.state_manager = state_manager

    async def execute(self, state: WorkflowState) -> NodeResult:
        """Run the execution phase and store the result in state."""
        approved_plan = state.session_metadata.get("approved_plan")
        if approved_plan is None:
            return Fail(error="No approved plan found in state")
        try:
            result = await execute_plan(
                plan=approved_plan,
                task=self.task,
                repo_root=self.repo_root,
                ws=self.session,
                llm_client=self.llm_client,
                context=self.context,
                branch_name=self.branch_name,
                base_branch=self.base_branch,
                conversation_logger=self.conversation_logger,
                session_id=self.session_id,
                expert_llm_client=self.expert_llm_client,
                dispatcher=self.dispatcher,
                state_manager=self.state_manager,
            )
            state.session_metadata["execution_result"] = result
            return Continue(next_node_id=None, payload={"result": result})
        except Exception as exc:
            return Fail(error=str(exc))


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

        # ── Build declarative graph and run via WorkflowEngine ──
        await ws_send(session, "stage_change", {"stage": "planning"})
        plan_commands = _effective_post_commands(repo_root)

        # Load or create initial state
        state = state_manager.get_state()

        # Build the workflow graph with planning, approval, and execution nodes
        graph = WorkflowGraph()
        graph.add_node(
            PlanningNode(
                task=task,
                repo_root=repo_root,
                llm_client=llm_client,
                context=context,
                session=session,
                dispatcher=dispatcher,
                refiner=refiner,
                test_command=plan_commands.get("test", ""),
                session_id=session_id,
                expert_llm_client=expert_llm_client,
            )
        )
        graph.add_node(
            ApprovalNodeExtended(
                task=task,
                repo_root=repo_root,
                llm_client=llm_client,
                context=context,
                session=session,
                dispatcher=dispatcher,
                refiner=refiner,
                test_command=plan_commands.get("test", ""),
                expert_llm_client=expert_llm_client,
                session_id=session_id,
            )
        )
        graph.add_node(
            ExecutionNode(
                task=task,
                repo_root=repo_root,
                session=session,
                llm_client=llm_client,
                context=context,
                branch_name=branch_name,
                base_branch=base_branch,
                conversation_logger=conversation_logger,
                session_id=session_id,
                expert_llm_client=expert_llm_client,
                dispatcher=dispatcher,
                state_manager=state_manager,
            )
        )

        # Run the graph via WorkflowEngine — state saved after each node
        engine = WorkflowEngine()
        result_node = await engine.run(graph, state_manager=state_manager, state=state)

        # Extract the execution result from state
        state = state_manager.get_state()
        result = state.session_metadata.get("execution_result", "")

        # Save checkpoint after execution phase
        state.current_phase = "execution_complete"
        state_manager.save()
        _execution_checkpoint_id = state_manager.save_checkpoint(
            state=state,
            phase="Phase 3: Execution Complete",
            summary="Execution completed successfully",
        )

        session_span_uuid = getattr(session_span, "span_uuid", "")

        # Detect branching after execution checkpoint
        await _detect_branching_and_request_feedback(
            state_manager=state_manager,
            new_checkpoint_id=_execution_checkpoint_id,
            new_parent_id=None,
            session_span_uuid=session_span_uuid,
            repo_root=repo_root,
        )

        # Save checkpoint after final validation phase
        state.current_phase = "validation"
        state_manager.save()
        _validation_checkpoint_id = state_manager.save_checkpoint(
            state=state,
            phase="Phase 4: Validation",
            summary="Final validation completed",
            parent_id=_execution_checkpoint_id,
        )

        # Detect branching after validation checkpoint
        await _detect_branching_and_request_feedback(
            state_manager=state_manager,
            new_checkpoint_id=_validation_checkpoint_id,
            new_parent_id=_execution_checkpoint_id,
            session_span_uuid=session_span_uuid,
            repo_root=repo_root,
        )

    return result


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

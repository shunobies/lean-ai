"""4-phase decomposed planning pipeline with structured output.

Phase 1: Scope analysis
Phase 2: File identification + content reading (with codebase exploration via tools)
Phase 3: Design + risk synthesis (change design, naming conventions, gap analysis)
Phase 4: Structured plan assembly (produces ExecutionPlan via chat_structured,
including per-step success checks)

Each phase is a focused LLM call. The planner uses read-only tools
(read_file, list_directory, directory_tree, grep_files) during Phase 2
to explore the codebase and read every file it plans to modify.
Verification is folded into each Phase 4 step's success checks.
"""

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lean_ai.config import settings
from lean_ai.llm.plan_schema import (
    IMPLEMENTATION_STEP_TOOLS,
    DesignAndRisks,
    ExecutionPlan,
    FileSummary,
    MissingFile,
    PlanStep,
    ScopeDocument,
    VerificationPlan,
    plan_to_markdown,
)
from lean_ai.llm.planner_exploration import (
    _make_read_only_executor,
    run_phase2_exploration,
)
from lean_ai.llm.planner_helpers import (
    PLAN_OUTPUT_PERCENT,
    _build_fallback_execution_plan,
    _chat_structured_with_repair,
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
from lean_ai.llm.prompts import resolve_prompt_text
from lean_ai.llm.role_tuning import ensure_expert_role_tuning, ensure_primary_role_tuning
from lean_ai.llm.tool_definitions import (
    REQUEST_CLARIFICATION_TOOL,
    build_design_tools,
    build_planning_tools,
)
from lean_ai.training.span_context import trace_span
from lean_ai.workflow.graph import (
    Continue,
    Fail,
    LLMNode,
    NodeResult,
    ToolNode,
    WorkflowEngine,
    WorkflowGraph,
)
from lean_ai.workflow.state import StateManager, WorkflowState
from lean_ai.workflow.ws_protocol import WorkflowSession

if TYPE_CHECKING:
    from lean_ai.llm.facade import LLMClient
    from lean_ai.llm.refiner import PromptRefiner
    from lean_ai.workflow.ws_dispatcher import WSMessageDispatcher

logger = logging.getLogger(__name__)


def _looks_like_final_suggested_agent_prompt(task: str) -> bool:
    """Return True for completed Grill Me handoffs that can skip Phase 1 tools.

    The fast path is intentionally conservative: Phase 2 still performs the
    real codebase evidence pass, but Phase 1's exploratory loop is redundant
    when the chat flow already produced a complete Suggested Agent Prompt.
    """
    if not task or "## Suggested Agent Prompt" not in task:
        return False

    prompt_start = task.find("## Suggested Agent Prompt")
    prompt = task[prompt_start:]
    lowered = prompt.lower()

    unresolved_markers = (
        "grill me question",
        "question for you",
        "before i can",
        "need to know",
        "tbd",
        "todo:",
        "[placeholder",
        "<placeholder",
    )
    if any(marker in lowered for marker in unresolved_markers):
        return False

    required_section_groups = (
        ("requirements", "deliverables", "goal", "objective", "task"),
        ("success criteria", "acceptance criteria", "verification", "test plan", "tests"),
        ("references", "context", "files", "relevant files"),
        ("user decisions", "decisions", "constraints", "assumptions"),
    )
    for names in required_section_groups:
        if not any(re.search(rf"(^|\n)#+\s+.*{re.escape(name)}", lowered) for name in names):
            return False

    concrete_reference_patterns = (
        r"`[^`\n]+\.[A-Za-z0-9]+`",
        r"\b[\w./-]+/[A-Za-z0-9_.-]+\.[A-Za-z0-9]+\b",
        r"\b[A-Za-z0-9_.-]+\.(py|ts|tsx|js|jsx|go|rs|java|kt|cs|rb|php)\b",
    )
    return any(re.search(pattern, prompt) for pattern in concrete_reference_patterns)


class RoutingPolicy:
    """Resolve per-phase model roles to LLMClient instances.

    Reads the per-phase role settings (scope_model_role, exploration_model_role,
    design_model_role, assembly_model_role) and maps them to the configured
    LLMClient for that role. Falls back to the primary client if the role
    client is not configured.
    """

    def __init__(
        self,
        primary: "LLMClient",
        expert: "LLMClient | None" = None,
        worker: "LLMClient | None" = None,
        request: "LLMClient | None" = None,
    ) -> None:
        self._clients: dict[str, "LLMClient"] = {
            "primary": primary,
        }
        if expert is not None:
            self._clients["expert"] = expert
        if worker is not None:
            self._clients["worker"] = worker
        if request is not None:
            self._clients["request"] = request

    def get_client(self, phase_name: str) -> "LLMClient":
        """Return the LLMClient for the given phase based on settings.

        Args:
            phase_name: One of "scope", "exploration", "design", "assembly".

        Returns:
            The LLMClient configured for the phase's model role.
        """
        role_field = f"{phase_name}_model_role"
        role = getattr(settings, role_field, "primary")
        return self._clients.get(role, self._clients["primary"])


class PlanningPhase(ABC):
    """Abstract base class for all planning phase implementations.

    Concrete subclasses implement ``execute()`` to perform a single phase
    of the planning pipeline (scope analysis, codebase exploration,
    design synthesis, or plan assembly).
    """

    @abstractmethod
    async def execute(
        self,
        task: str,
        context: str,
        repo_root: str,
        llm_client: "LLMClient",
        session_id: str = "",
    ) -> dict:
        """Execute this phase of the planning pipeline.

        Args:
            task: The user's task description.
            context: Pre-assembled context (project context, search results).
            repo_root: Path to the repository root.
            llm_client: The LLM client to use for this phase.
            session_id: Optional session identifier for debug output.

        Returns:
            A dict with phase-specific output data.
        """


class ScopePhase(PlanningPhase):
    """Phase 1: Scope analysis — clarification loop + structured scope synthesis.

    Runs the LLM with read-only tools (grep, read_file, list_directory, etc.)
    to explore the codebase and clarify the task, then synthesizes the
    exploration prose into a validated ``ScopeDocument``.
    """

    async def execute(
        self,
        task: str,
        llm_client: "LLMClient",
        ws: "WorkflowSession | None" = None,
        dispatcher: "WSMessageDispatcher | None" = None,
        **kwargs,
    ) -> ScopeDocument:
        """Execute Phase 1 and return a validated ScopeDocument.

        Args:
            task: The user's task description.
            llm_client: The LLM client to use for scope analysis.
            ws: Optional WorkflowSession for streaming stage progress.
            dispatcher: Optional WebSocket message dispatcher.
            **kwargs: Additional keyword arguments (context, repo_root,
                session_id, on_content, on_thinking, on_tool_call,
                on_tool_result, on_metrics, on_metrics_reset).

        Returns:
            A validated ScopeDocument with the 8 required scope sections.
        """
        context = kwargs.get("context", "")
        repo_root = kwargs.get("repo_root", "")
        session_id = kwargs.get("session_id", "")
        on_content = kwargs.get("on_content")
        on_thinking = kwargs.get("on_thinking")
        on_tool_call = kwargs.get("on_tool_call")
        on_tool_result = kwargs.get("on_tool_result")
        on_metrics = kwargs.get("on_metrics")
        on_metrics_reset = kwargs.get("on_metrics_reset")

        phase_max_tokens = settings.ollama_max_tokens

        await _send_stage(
            ws,
            "Phase 1: Verifying task (asking clarifying questions if needed)...",
            model=llm_client.model_name,
            phase=1,
        )
        logger.info(
            "Planning Phase 1 clarification (model=%s, tool_budget=%d)",
            llm_client.model_name,
            settings.plan_phase1_max_turns,
        )
        t0 = time.monotonic()
        scope_prose = ""
        _phase1_tool_calls: list[Any] = []
        fast_path = _looks_like_final_suggested_agent_prompt(task)
        if fast_path:
            scope_prose = (
                "Phase 1 fast path: the task is a completed Suggested Agent "
                "Prompt from the Grill Me flow. Skip redundant exploratory "
                "verification here and synthesize the ScopeDocument directly "
                "from the handoff; Phase 2 remains responsible for codebase "
                "evidence gathering and assumption checks."
            )
            _phase1_tool_calls = []
            logger.info("Phase 1 fast path activated for Suggested Agent Prompt handoff")
            if on_content:
                await on_content(scope_prose)
                await _send_content_done(ws, scope_prose)
        else:
            phase1_turns_str = str(settings.plan_phase1_max_turns)
            primary_prompt_scope = await ensure_primary_role_tuning(
                repo_root=repo_root,
                assigned_client=llm_client,
                primary_client=llm_client,
                expert_client=kwargs.get("expert_llm_client"),
            )
            phase1_system = registry.format_text(
                "planning.scope_system",
                prompt_scope=primary_prompt_scope,
                PHASE1_MAX_TURNS=phase1_turns_str,
            )
            phase1_user_content = registry.format_text(
                "planning.scope_user",
                task=task,
                context=context,
                PHASE1_MAX_TURNS=phase1_turns_str,
            )

            # Cross-session memory retrieval
            memory_context = ""
            if settings.enable_session_memory:
                memory_context = await _retrieve_session_memories(repo_root, task)
            if memory_context:
                phase1_user_content += memory_context

            phase1_tools = [
                t
                for t in build_planning_tools()
                if t["function"]["name"]
                in (
                    "grep_files",
                    "read_file",
                    "list_directory",
                    "query_project_context",
                    "search_reference",
                    "task_complete",
                )
            ]
            phase1_tools.append(REQUEST_CLARIFICATION_TOOL)

            small_ctx = settings._active_context_window <= 32768
            phase1_executor = _make_read_only_executor(
                llm_client,
                repo_root,
                session_id,
                ws,
                dispatcher,
                small_ctx,
            )

            phase1_exc: Exception | None = None
            async with trace_span(
                span_type="turn",
                span_name="scope_turn",
                session_id=session_id,
                metadata={
                    "model": llm_client.model_name,
                    "provider": getattr(llm_client, "provider", "unknown"),
                    "phase": "planning.phase1",
                },
            ) as turn_span:
                try:
                    _phase1_tool_calls, scope_prose = await llm_client.chat_with_tools(
                        messages=[
                            {"role": "system", "content": phase1_system},
                            {"role": "user", "content": phase1_user_content},
                        ],
                        tools=phase1_tools,
                        tool_executor_fn=phase1_executor,
                        max_turns=settings.plan_phase1_max_turns,
                        max_tokens=phase_max_tokens,
                        text_only_exit_count=1,
                        on_tool_call=on_tool_call,
                        on_tool_result=on_tool_result,
                        on_content=on_content,
                        on_thinking=on_thinking,
                        on_metrics=on_metrics,
                        on_metrics_reset=on_metrics_reset,
                        dispatcher=dispatcher,
                        telemetry_context={
                            "repo_root": repo_root,
                            "session_id": session_id,
                            "phase": "planning.phase1",
                            "role": "primary",
                        },
                    )
                except Exception as exc:
                    phase1_exc = exc
                    raise
            if phase1_exc is not None:
                raise phase1_exc
            if not scope_prose:
                raise RuntimeError("Phase 1 produced no scope prose")
            if on_content:
                await _send_content_done(ws, scope_prose)

        elapsed = time.monotonic() - t0
        _save_debug_phase(
            repo_root,
            session_id,
            "phase_1_suggested_prompt_fast_path" if fast_path else "phase_1_clarification",
            scope_prose,
            elapsed,
        )
        logger.info(
            "Phase 1 clarification used %d tool calls in %.1fs",
            len(_phase1_tool_calls),
            elapsed,
        )
        await _send_stage_done(
            ws,
            "Task verified",
            model=llm_client.model_name,
            phase=1,
        )

        # Phase 1a: Synthesize structured ScopeDocument
        await _send_stage(
            ws,
            "Phase 1a: Generating scope document...",
            model=llm_client.model_name,
            phase=1,
        )
        t0a = time.monotonic()
        scope_obj, scope_markdown, scope_synthesized = await _synthesize_scope(
            task=task,
            context=context,
            exploration_prose=scope_prose,
            explorer=llm_client,
            phase_max_tokens=phase_max_tokens,
            on_thinking=on_thinking,
            on_metrics=on_metrics,
            on_metrics_reset=on_metrics_reset,
        )
        if not scope_synthesized:
            logger.warning(
                "Phase 1a scope synthesis fell through to programmatic "
                "fallback (task-text-only ScopeDocument). Phase 2 will "
                "reconstruct missing sections from the task during "
                "exploration.",
            )

        elapsed_a = time.monotonic() - t0a
        _save_debug_phase(
            repo_root,
            session_id,
            "phase_1a_scope",
            scope_markdown,
            elapsed_a,
        )
        await _send_stage_done(
            ws,
            "Scope document generated",
            model=llm_client.model_name,
            phase=1,
        )

        return scope_obj


class ExplorationPhase(PlanningPhase):
    """Phase 2: Codebase exploration — file identification + content reading.

    Runs the LLM with read-only tools to explore the codebase, identify
    relevant files, and produce a validated ``FileSummary``.
    """

    async def execute(
        self,
        task: str,
        scope: str,
        llm_client: "LLMClient",
        ws: "WorkflowSession | None" = None,
        dispatcher: "WSMessageDispatcher | None" = None,
        **kwargs,
    ) -> FileSummary:
        """Execute Phase 2 and return a validated FileSummary.

        Args:
            task: The user's task description.
            scope: The scope markdown from Phase 1.
            llm_client: The LLM client to use for exploration.
            ws: Optional WorkflowSession for streaming stage progress.
            dispatcher: Optional WebSocket message dispatcher.
            **kwargs: Additional keyword arguments (context, repo_root,
                session_id, refiner, on_content, on_thinking, on_tool_call,
                on_tool_result, on_metrics, on_metrics_reset).

        Returns:
            A validated FileSummary with file observations and metadata.
        """
        context = kwargs.get("context", "")
        repo_root = kwargs.get("repo_root", "")
        session_id = kwargs.get("session_id", "")
        refiner = kwargs.get("refiner")
        state_manager = kwargs.get("state_manager")
        on_content = kwargs.get("on_content")
        on_thinking = kwargs.get("on_thinking")
        on_tool_call = kwargs.get("on_tool_call")
        on_tool_result = kwargs.get("on_tool_result")
        on_metrics = kwargs.get("on_metrics")
        on_metrics_reset = kwargs.get("on_metrics_reset")
        if state_manager is None:
            raise RuntimeError("ExplorationPhase.execute requires a state_manager")

        phase_max_tokens = settings.ollama_max_tokens

        await _send_stage(
            ws,
            "Phase 2: Exploring codebase and reading files...",
            model=llm_client.model_name,
            phase=2,
        )
        logger.info("Planning Phase 2: File identification and reading")

        file_summary_obj: FileSummary | None = None
        file_identification = ""
        phase2_elapsed = 0.0
        phase2_exc: Exception | None = None
        async with trace_span(
            span_type="turn",
            span_name="exploration_turn",
            session_id=session_id,
            metadata={
                "model": llm_client.model_name,
                "provider": getattr(llm_client, "provider", "unknown"),
                "phase": "planning.phase2",
            },
        ) as exploration_turn_span:
            primary_prompt_scope = await ensure_primary_role_tuning(
                repo_root=repo_root,
                assigned_client=llm_client,
                primary_client=llm_client,
                expert_client=kwargs.get("expert_llm_client"),
            )
            try:
                file_summary_obj, file_identification, phase2_elapsed = await run_phase2_exploration(
                    task=task,
                    scope=scope,
                    context=context,
                    repo_root=repo_root,
                    session_id=session_id,
                    explorer=llm_client,
                    phase_max_tokens=phase_max_tokens,
                    ws=ws,
                    dispatcher=dispatcher,
                    state_manager=state_manager,
                    prompt_scope=primary_prompt_scope,
                    on_content=on_content,
                    on_thinking=on_thinking,
                    on_tool_call=on_tool_call,
                    on_tool_result=on_tool_result,
                    on_metrics=on_metrics,
                    on_metrics_reset=on_metrics_reset,
                )
            except Exception as exc:
                phase2_exc = exc
                raise
        if phase2_exc is not None:
            raise phase2_exc
        if not file_identification:
            raise RuntimeError("Phase 2 produced no file identification summary")

        _save_debug_phase(
            repo_root,
            session_id,
            "phase_2_file_identification",
            file_identification,
            phase2_elapsed,
        )
        await _send_stage_done(
            ws,
            "Codebase exploration complete",
            model=llm_client.model_name,
            phase=2,
        )

        # Privacy stripping if refiner is active
        file_summary_markdown = file_identification
        if refiner is not None and hasattr(refiner, "is_active") and refiner.is_active:
            file_summary_markdown, redactions = await refiner.strip_privacy(
                file_summary_markdown
            )
            if redactions:
                logger.info(
                    "Privacy: stripped %d items from file summary",
                    len(redactions),
                )

        # Compact file summary for small context windows
        if settings._active_context_window <= 32768:
            file_summary_markdown = await _compact_file_summary(
                file_summary_markdown,
                llm_client,
                settings._active_context_window,
            )

        # Return the structured object if available, otherwise create a minimal one
        if file_summary_obj is not None:
            return file_summary_obj

        return FileSummary()


class DesignPhase(PlanningPhase):
    """Phase 3: Design synthesis — change design, naming conventions, gap analysis.

    Runs the LLM with search-only tools (search_internet, fetch_url) to
    verify external dependencies, then synthesizes the exploration prose
    into a validated ``DesignAndRisks``.
    """

    async def execute(
        self,
        task: str,
        scope: str,
        file_summary: str,
        llm_client: "LLMClient",
        ws: "WorkflowSession | None" = None,
        dispatcher: "WSMessageDispatcher | None" = None,
        **kwargs,
    ) -> DesignAndRisks:
        """Execute Phase 3 and return a validated DesignAndRisks.

        Args:
            task: The user's task description.
            scope: The scope markdown from Phase 1.
            file_summary: The file summary markdown from Phase 2.
            llm_client: The LLM client to use for design synthesis.
            ws: Optional WorkflowSession for streaming stage progress.
            dispatcher: Optional WebSocket message dispatcher.
            **kwargs: Additional keyword arguments (context, repo_root,
                session_id, on_content, on_thinking, on_tool_call,
                on_tool_result, on_metrics, on_metrics_reset).

        Returns:
            A validated DesignAndRisks with design decisions and risks.
        """
        context = kwargs.get("context", "")
        repo_root = kwargs.get("repo_root", "")
        session_id = kwargs.get("session_id", "")
        on_content = kwargs.get("on_content")
        on_thinking = kwargs.get("on_thinking")
        on_tool_call = kwargs.get("on_tool_call")
        on_tool_result = kwargs.get("on_tool_result")
        on_metrics = kwargs.get("on_metrics")
        on_metrics_reset = kwargs.get("on_metrics_reset")

        expert = llm_client
        expert_max_tokens = settings.effective_expert_max_tokens
        expert_prompt_scope = await ensure_expert_role_tuning(
            repo_root=repo_root,
            assigned_client=expert,
            primary_client=kwargs.get("primary_llm_client") or expert,
            expert_client=kwargs.get("expert_llm_client") or expert,
        )

        await _send_stage(
            ws,
            "Phase 3: Designing changes and assessing risks...",
            model=expert.model_name,
            phase=3,
        )
        logger.info("Planning Phase 3: Design + risk synthesis")
        t0 = time.monotonic()

        phase3_project_context_block = (
            f"PROJECT CONTEXT:\n{context}\n\n" if context else ""
        )
        phase3_user_content = registry.get_text("planning.design_user").format(
            task=task,
            scope=scope,
            project_context=phase3_project_context_block,
            file_summary=file_summary,
        )
        if settings.enable_session_memory and getattr(
            settings,
            "enable_phase3_memory",
            True,
        ):
            from lean_ai.llm.planner_helpers import retrieve_design_memories

            design_memories = await retrieve_design_memories(repo_root, task)
            if design_memories:
                phase3_user_content += design_memories
        phase3_messages = [
            {
                "role": "system",
                "content": resolve_prompt_text("planning.design_system", scope=expert_prompt_scope),
            },
            {"role": "user", "content": phase3_user_content},
        ]
        phase3_exploration_prose = ""

        async def _search_only_executor(name: str, arguments: dict) -> str:
            """Execute search tools for Phase 3 design verification."""
            if name == "search_internet":
                from lean_ai.tools.internet import search_internet

                result = await search_internet(
                    query=arguments.get("query", ""),
                    llm_client=expert,
                )
                return result.output if result.success else result.error or "Error"
            if name == "fetch_url":
                from lean_ai.tools.internet import fetch_url

                result = await fetch_url(
                    url=arguments.get("url", ""),
                    repo_root=repo_root,
                    llm_client=expert,
                )
                return result.output if result.success else result.error or "Error"
            if name == "task_complete":
                return "Design synthesis marked complete."
            return f"Unknown tool: {name}"

        phase3_exc: Exception | None = None
        async with trace_span(
            span_type="turn",
            span_name="design_turn",
            session_id=session_id,
            metadata={
                "model": expert.model_name,
                "provider": getattr(expert, "provider", "unknown"),
                "phase": "planning.phase3",
            },
        ) as design_turn_span:
            try:
                _phase3_tool_calls, phase3_exploration_prose = await expert.chat_with_tools(
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
                    on_metrics_reset=on_metrics_reset,
                    dispatcher=dispatcher,
                    telemetry_context={
                        "repo_root": repo_root,
                        "session_id": session_id,
                        "phase": "planning.phase3",
                        "role": "expert",
                    },
                )
            except Exception as exc:
                phase3_exc = exc
                raise
        if phase3_exc is not None:
            raise phase3_exc
        if not phase3_exploration_prose:
            raise RuntimeError("Phase 3 produced no design exploration prose")
        if on_content:
            await _send_content_done(ws, phase3_exploration_prose)

        design_and_risks_obj = await _synthesize_design_and_risks(
            task=task,
            scope=scope,
            project_context_block=phase3_project_context_block,
            file_summary=file_summary,
            exploration_prose=phase3_exploration_prose,
            expert=expert,
            expert_max_tokens=expert_max_tokens,
            on_thinking=on_thinking,
            on_metrics=on_metrics,
            on_metrics_reset=on_metrics_reset,
        )

        elapsed = time.monotonic() - t0
        _save_debug_phase(
            repo_root,
            session_id,
            "phase_3_design_and_risks",
            _format_design_and_risks(design_and_risks_obj),
            elapsed,
        )
        logger.info(
            (
                "Phase 3 synthesis: naming=%d designs=%d missing=%d deps=%d "
                "risks=%d citations=%d in %.1fs"
            ),
            len(design_and_risks_obj.naming_conventions),
            len(design_and_risks_obj.change_designs),
            len(design_and_risks_obj.missing_files),
            len(design_and_risks_obj.dependency_order),
            len(design_and_risks_obj.critical_risks),
            len(design_and_risks_obj.citations),
            elapsed,
        )
        await _send_stage_done(
            ws,
            "Design and risk synthesis complete",
            model=expert.model_name,
            phase=3,
        )

        return design_and_risks_obj


class AssemblyPhase(PlanningPhase):
    """Phase 4: Structured plan assembly — produces ExecutionPlan.

    Runs deterministic test discovery (Phase 4a), assembles the structured
    plan via chat_structured (Phase 4b/4c), and runs validation with
    auto-revision for blocking warnings.
    """

    async def execute(
        self,
        task: str,
        scope: str,
        file_summary: str,
        design_and_risks: DesignAndRisks,
        llm_client: "LLMClient",
        ws: "WorkflowSession | None" = None,
        dispatcher: "WSMessageDispatcher | None" = None,
        **kwargs,
    ) -> ExecutionPlan:
        """Execute Phase 4 and return a validated ExecutionPlan.

        Args:
            task: The user's task description.
            scope: The scope markdown from Phase 1.
            file_summary: The file summary markdown from Phase 2.
            design_and_risks: The DesignAndRisks from Phase 3.
            llm_client: The LLM client to use for plan assembly.
            ws: Optional WorkflowSession for streaming stage progress.
            dispatcher: Optional WebSocket message dispatcher.
            **kwargs: Additional keyword arguments (context, repo_root,
                session_id, refiner, test_command, expert_llm_client,
                file_summary_obj, on_content, on_thinking, on_tool_call,
                on_tool_result, on_metrics, on_metrics_reset).

        Returns:
            A validated ExecutionPlan ready for per-step execution.
        """
        context = kwargs.get("context", "")
        repo_root = kwargs.get("repo_root", "")
        session_id = kwargs.get("session_id", "")
        refiner = kwargs.get("refiner")
        test_command = kwargs.get("test_command", "")
        expert_llm_client = kwargs.get("expert_llm_client")
        file_summary_obj = kwargs.get("file_summary_obj")
        on_content = kwargs.get("on_content")
        on_thinking = kwargs.get("on_thinking")
        on_tool_call = kwargs.get("on_tool_call")
        on_tool_result = kwargs.get("on_tool_result")
        on_metrics = kwargs.get("on_metrics")
        on_metrics_reset = kwargs.get("on_metrics_reset")

        expert = expert_llm_client or llm_client
        expert_prompt_scope = await ensure_expert_role_tuning(
            repo_root=repo_root,
            assigned_client=expert,
            primary_client=kwargs.get("primary_llm_client") or llm_client,
            expert_client=expert_llm_client or expert,
        )
        expert_max_tokens = (
            settings.effective_expert_max_tokens if expert_llm_client else settings.ollama_max_tokens
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

        # Keep the structured object for validation; format for prompts.
        dar_obj = design_and_risks
        design_and_risks_md = _format_design_and_risks(dar_obj)
        missing_files = _format_missing_files(dar_obj.missing_files)

        # Phase 4a: Deterministic test discovery
        await _send_stage(
            ws,
            "Phase 4a: Discovering test files...",
            model=expert.model_name,
            phase=4,
        )
        logger.info("Planning Phase 4a: Deterministic test discovery")
        t0_4a = time.monotonic()

        phase4a_affected: list[str] = []
        for cd in dar_obj.change_designs:
            if cd.file_path and cd.file_path not in phase4a_affected:
                phase4a_affected.append(cd.file_path)
        if file_summary_obj is not None:
            for obs in file_summary_obj.files_to_create:
                if obs.file_path and obs.file_path not in phase4a_affected:
                    phase4a_affected.append(obs.file_path)
            for obs in file_summary_obj.files_to_modify:
                if obs.file_path and obs.file_path not in phase4a_affected:
                    phase4a_affected.append(obs.file_path)

        test_discovery = await _run_phase_4a(repo_root, phase4a_affected)

        _save_debug_phase(
            repo_root,
            session_id,
            "phase_4a_test_discovery",
            test_discovery,
            time.monotonic() - t0_4a,
        )
        logger.info(
            "Phase 4a test discovery completed in %.1fs",
            time.monotonic() - t0_4a,
        )
        await _send_stage_done(
            ws,
            "Test discovery complete",
            model=expert.model_name,
            phase=4,
        )

        phase4_scope = scope
        phase4_project_context = context

        verification_targets = _build_verification_targets(
            file_summary_obj,
            dar_obj,
        )
        security_concerns = _build_security_concerns(dar_obj)
        testing_inventory_raw = _format_testing_inventory(file_summary_obj)
        testing_inventory = _format_test_inventory_for_phase4(
            test_discovery,
            testing_inventory_raw,
        )
        core_functionality = _format_core_functionality(dar_obj)
        dependency_order_block = _format_dependency_order(dar_obj)
        naming_conventions_block = _format_naming_conventions_section(dar_obj)
        risk_assessment_block = _format_risk_assessment_section(dar_obj)
        missing_files_block = (
            "REQUIRED MISSING FILES — these were identified "
            "during risk assessment as files that MUST exist "
            "for the app to work. Each one MUST have a "
            "corresponding create_file or edit_file step in "
            f"the plan:\n{missing_files}\n\n"
            if missing_files
            else ""
        )

        tdd_verification: VerificationPlan | None = None
        final_assembly_timing_key = "phase_4_plan_assembly"

        if settings.enable_strict_test_contract:
            draft_plan, draft_elapsed = await _assemble_phase4_plan(
                task=task,
                design_and_risks=design_and_risks_md,
                file_summary=file_summary,
                project_context=phase4_project_context,
                scope=phase4_scope,
                missing_files=missing_files_block,
                test_command=test_command,
                testing_inventory=testing_inventory,
                verification_targets=verification_targets,
                security_concerns=security_concerns,
                core_functionality=core_functionality,
                dependency_order=dependency_order_block,
                naming_conventions=naming_conventions_block,
                risk_assessment=risk_assessment_block,
                tdd_guidance="",
                planned_tdd_tests="",
                expert=expert,
                plan_assembly_max_tokens=plan_assembly_max_tokens,
                ws=ws,
                on_thinking=on_thinking,
                on_metrics=on_metrics,
                on_metrics_reset=on_metrics_reset,
                prompt_scope=expert_prompt_scope,
                stage_summary="Phase 4b: Drafting implementation plan for TDD...",
                done_prefix="Draft implementation plan assembled",
                artifact_label="draft structured plan",
            )
            if settings.enable_core_functionality_tagging:
                draft_plan.core_functionality = list(dar_obj.core_functionality)
            _save_debug_phase(
                repo_root,
                session_id,
                "phase_4b_draft_plan",
                draft_plan.model_dump_json(indent=2),
                draft_elapsed,
            )

            tdd_verification, tdd_elapsed = await _run_phase_4b_tdd_test_design(
                draft_plan=draft_plan,
                task=task,
                testing_inventory=testing_inventory,
                verification_targets=verification_targets,
                security_concerns=security_concerns,
                core_functionality=core_functionality,
                dependency_order=dependency_order_block,
                naming_conventions=naming_conventions_block,
                risk_assessment=risk_assessment_block,
                expert=expert,
                plan_assembly_max_tokens=plan_assembly_max_tokens,
                ws=ws,
                on_thinking=on_thinking,
                on_metrics=on_metrics,
                on_metrics_reset=on_metrics_reset,
                prompt_scope=expert_prompt_scope,
            )
            _save_debug_phase(
                repo_root,
                session_id,
                "phase_4b_tdd_test_design",
                tdd_verification.model_dump_json(indent=2),
                tdd_elapsed,
            )

            tdd_guidance = (
                "TDD MODE IS ACTIVE.\n"
                "- A dedicated pre-implementation test phase will run before "
                "the implementation steps.\n"
                "- Keep authored test creation in `tdd_test_steps` and keep "
                "`steps` focused on implementation.\n"
                "- Implementation `success_checks` must reference the "
                "planned tests by concrete file path or test command.\n\n"
            )
            planned_tdd_tests = _render_tdd_test_plan_for_phase4(tdd_verification)
            plan, final_elapsed = await _assemble_phase4_plan(
                task=task,
                design_and_risks=design_and_risks_md,
                file_summary=file_summary,
                project_context=phase4_project_context,
                scope=phase4_scope,
                missing_files=missing_files_block,
                test_command=test_command,
                testing_inventory=testing_inventory,
                verification_targets=verification_targets,
                security_concerns=security_concerns,
                core_functionality=core_functionality,
                dependency_order=dependency_order_block,
                naming_conventions=naming_conventions_block,
                risk_assessment=risk_assessment_block,
                tdd_guidance=tdd_guidance,
                planned_tdd_tests=planned_tdd_tests,
                expert=expert,
                plan_assembly_max_tokens=plan_assembly_max_tokens,
                ws=ws,
                on_thinking=on_thinking,
                on_metrics=on_metrics,
                on_metrics_reset=on_metrics_reset,
                prompt_scope=expert_prompt_scope,
                stage_summary="Phase 4c: Assembling TDD implementation plan...",
                done_prefix="TDD implementation plan assembled",
                artifact_label="structured TDD plan",
            )
            final_assembly_timing_key = "phase_4c_plan_assembly"
            _attach_tdd_contract(plan, tdd_verification.steps)
        else:
            plan, final_elapsed = await _assemble_phase4_plan(
                task=task,
                design_and_risks=design_and_risks_md,
                file_summary=file_summary,
                project_context=phase4_project_context,
                scope=phase4_scope,
                missing_files=missing_files_block,
                test_command=test_command,
                testing_inventory=testing_inventory,
                verification_targets=verification_targets,
                security_concerns=security_concerns,
                core_functionality=core_functionality,
                dependency_order=dependency_order_block,
                naming_conventions=naming_conventions_block,
                risk_assessment=risk_assessment_block,
                tdd_guidance="",
                planned_tdd_tests="",
                expert=expert,
                plan_assembly_max_tokens=plan_assembly_max_tokens,
                ws=ws,
                on_thinking=on_thinking,
                on_metrics=on_metrics,
                on_metrics_reset=on_metrics_reset,
                prompt_scope=expert_prompt_scope,
                stage_summary="Phase 4: Assembling structured plan...",
                done_prefix="Plan assembled",
                artifact_label="structured plan",
            )

        if plan is None:
            raise RuntimeError("Phase 4 produced no execution plan")
        if final_elapsed < 0:
            raise RuntimeError("Phase 4 produced an invalid elapsed time")
        if settings.enable_core_functionality_tagging:
            plan.core_functionality = list(dar_obj.core_functionality)
        if tdd_verification is None:
            plan.tdd_mode = False

        plan_warnings, is_blocking = _run_plan_validations(
            plan,
            file_summary_obj,
            dar_obj,
            test_command,
        )

        # Revision loop with hard cap of 2 iterations for blocking warnings.
        max_revisions = 2
        revision_count = 0
        while is_blocking and revision_count < max_revisions:
            revision_count += 1
            logger.warning(
                "Phase 4 plan validation — blocking warnings detected "
                "(revision %d/%d); triggering auto-revision",
                revision_count,
                max_revisions,
            )
            blocking_warnings = [
                w for w in plan_warnings
                if "[BLOCKING]" in w
                or "invented path:" in w
                or "write target not found" in w
            ]
            if not blocking_warnings:
                blocking_warnings = plan_warnings
            feedback = (
                "Phase 4 plan validation produced BLOCKING warnings. "
                "Revise the plan to address each one:\n"
                + "\n".join(f"- {w}" for w in blocking_warnings)
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
                repo_root=repo_root,
                expert_llm_client=expert_llm_client,
                primary_llm_client=kwargs.get("primary_llm_client") or llm_client,
                previous_plan=plan,
                on_thinking=on_thinking,
                on_metrics=on_metrics,
                on_metrics_reset=on_metrics_reset,
                file_summary=file_summary,
                design_and_risks=design_and_risks_md,
                scope=scope,
            )
            _strip_non_implementation_steps(plan)
            if settings.enable_core_functionality_tagging:
                plan.core_functionality = list(dar_obj.core_functionality)
            if tdd_verification is not None:
                _attach_tdd_contract(plan, tdd_verification.steps)
            else:
                plan.tdd_mode = False
                _sync_affected_files_from_steps(plan)
            plan_warnings, is_blocking = _run_plan_validations(
                plan,
                file_summary_obj,
                dar_obj,
                test_command,
            )

        if revision_count >= max_revisions and is_blocking:
            logger.warning(
                "Phase 4 revision cap reached (%d iterations) — "
                "plan ships with %d blocking warning(s)",
                max_revisions,
                len([w for w in plan_warnings if "[BLOCKING]" in w]),
            )

        plan.plan_validation_warnings = plan_warnings

        _save_debug_phase(
            repo_root,
            session_id,
            "phase_4_plan",
            plan.model_dump_json(indent=2),
            final_elapsed,
        )
        _save_debug_phase(
            repo_root,
            session_id,
            "phase_4_plan_markdown",
            plan_to_markdown(plan),
            final_elapsed,
        )

        logger.info(
            "Plan created: %d steps, %d affected files",
            len(plan.steps),
            len(plan.affected_files),
        )
        return plan


# ── Phase graph nodes ────────────────────────────────────────────────────────
# Each phase node wraps the corresponding PlanningPhase logic as an LLMNode
# or ToolNode that can be composed into a SubgraphNode WorkflowGraph.


class ScopePhaseNode(LLMNode):
    """Graph node for Phase 1: Scope analysis.

    Executes the ScopePhase logic via an LLM interaction with read-only
    tools for codebase exploration and clarification, then synthesizes
    a structured ScopeDocument.
    """

    def __init__(
        self,
        llm_client: "LLMClient",
        ws: "WorkflowSession | None" = None,
        dispatcher: "WSMessageDispatcher | None" = None,
        **kwargs,
    ) -> None:
        """Initialise a scope phase node.

        Args:
            llm_client: The LLM client for scope analysis.
            ws: Optional WorkflowSession for streaming.
            dispatcher: Optional WebSocket dispatcher.
            **kwargs: Additional keyword arguments forwarded to ScopePhase.
        """
        super().__init__("scope_phase")
        self._llm_client = llm_client
        self._ws = ws
        self._dispatcher = dispatcher
        self._kwargs = kwargs

    async def execute(self, state: WorkflowState) -> NodeResult:
        """Run Phase 1 scope analysis and store the result in state."""
        try:
            scope_phase = ScopePhase()
            scope_obj = await scope_phase.execute(
                task=state.session_metadata.get("task", ""),
                llm_client=self._llm_client,
                ws=self._ws,
                dispatcher=self._dispatcher,
                context=state.session_metadata.get("context", ""),
                repo_root=state.session_metadata.get("repo_root", ""),
                session_id=state.session_metadata.get("session_id", ""),
                on_content=self._kwargs.get("on_content"),
                on_thinking=self._kwargs.get("on_thinking"),
                on_tool_call=self._kwargs.get("on_tool_call"),
                on_tool_result=self._kwargs.get("on_tool_result"),
                on_metrics=self._kwargs.get("on_metrics"),
                on_metrics_reset=self._kwargs.get("on_metrics_reset"),
            )
            state.session_metadata["scope_obj"] = scope_obj
            state.session_metadata["scope"] = (
                scope_obj.to_markdown() if scope_obj else state.session_metadata.get("task", "")
            )
            return Continue(
                next_node_id=None,
                payload={"scope": state.session_metadata["scope"]},
            )
        except Exception as exc:
            return Fail(error=f"Scope phase failed: {exc}")


class ExplorationPhaseNode(ToolNode):
    """Graph node for Phase 2: Codebase exploration.

    Executes the ExplorationPhase logic using read-only tools to explore
    the codebase, identify relevant files, and produce a FileSummary.
    """

    def __init__(
        self,
        llm_client: "LLMClient",
        ws: "WorkflowSession | None" = None,
        dispatcher: "WSMessageDispatcher | None" = None,
        **kwargs,
    ) -> None:
        """Initialise an exploration phase node.

        Args:
            llm_client: The LLM client for exploration.
            ws: Optional WorkflowSession for streaming.
            dispatcher: Optional WebSocket dispatcher.
            **kwargs: Additional keyword arguments forwarded to ExplorationPhase.
        """
        super().__init__("exploration_phase")
        self._llm_client = llm_client
        self._ws = ws
        self._dispatcher = dispatcher
        self._kwargs = kwargs

    async def execute(self, state: WorkflowState) -> NodeResult:
        """Run Phase 2 codebase exploration and store the result in state."""
        try:
            exploration_phase = ExplorationPhase()
            file_summary_obj = await exploration_phase.execute(
                task=state.session_metadata.get("task", ""),
                scope=state.session_metadata.get("scope", ""),
                llm_client=self._llm_client,
                ws=self._ws,
                dispatcher=self._dispatcher,
                context=state.session_metadata.get("context", ""),
                repo_root=state.session_metadata.get("repo_root", ""),
                session_id=state.session_metadata.get("session_id", ""),
                state_manager=self._kwargs.get("state_manager"),
                refiner=self._kwargs.get("refiner"),
                on_content=self._kwargs.get("on_content"),
                on_thinking=self._kwargs.get("on_thinking"),
                on_tool_call=self._kwargs.get("on_tool_call"),
                on_tool_result=self._kwargs.get("on_tool_result"),
                on_metrics=self._kwargs.get("on_metrics"),
                on_metrics_reset=self._kwargs.get("on_metrics_reset"),
            )
            state.session_metadata["file_summary_obj"] = file_summary_obj
            state.session_metadata["file_summary"] = (
                file_summary_obj.to_markdown() if file_summary_obj else ""
            )
            return Continue(
                next_node_id=None,
                payload={"file_summary": state.session_metadata["file_summary"]},
            )
        except Exception as exc:
            return Fail(error=f"Exploration phase failed: {exc}")


class DesignPhaseNode(LLMNode):
    """Graph node for Phase 3: Design synthesis.

    Executes the DesignPhase logic using an expert LLM with search tools
    to verify external dependencies and synthesize design decisions and risks.
    """

    def __init__(
        self,
        llm_client: "LLMClient",
        ws: "WorkflowSession | None" = None,
        dispatcher: "WSMessageDispatcher | None" = None,
        **kwargs,
    ) -> None:
        """Initialise a design phase node.

        Args:
            llm_client: The LLM client for design synthesis.
            ws: Optional WorkflowSession for streaming.
            dispatcher: Optional WebSocket dispatcher.
            **kwargs: Additional keyword arguments forwarded to DesignPhase.
        """
        super().__init__("design_phase")
        self._llm_client = llm_client
        self._ws = ws
        self._dispatcher = dispatcher
        self._kwargs = kwargs

    async def execute(self, state: WorkflowState) -> NodeResult:
        """Run Phase 3 design synthesis and store the result in state."""
        try:
            design_phase = DesignPhase()
            design_and_risks_obj = await design_phase.execute(
                task=state.session_metadata.get("task", ""),
                scope=state.session_metadata.get("scope", ""),
                file_summary=state.session_metadata.get("file_summary", ""),
                llm_client=self._llm_client,
                ws=self._ws,
                dispatcher=self._dispatcher,
                context=state.session_metadata.get("context", ""),
                repo_root=state.session_metadata.get("repo_root", ""),
                session_id=state.session_metadata.get("session_id", ""),
                on_content=self._kwargs.get("on_content"),
                on_thinking=self._kwargs.get("on_thinking"),
                on_tool_call=self._kwargs.get("on_tool_call"),
                on_tool_result=self._kwargs.get("on_tool_result"),
                on_metrics=self._kwargs.get("on_metrics"),
                on_metrics_reset=self._kwargs.get("on_metrics_reset"),
            )
            state.session_metadata["design_and_risks_obj"] = design_and_risks_obj
            return Continue(
                next_node_id=None,
                payload={"design_and_risks": design_and_risks_obj},
            )
        except Exception as exc:
            return Fail(error=f"Design phase failed: {exc}")


class AssemblyPhaseNode(LLMNode):
    """Graph node for Phase 4: Structured plan assembly.

    Executes the AssemblyPhase logic to produce a validated ExecutionPlan
    with per-step success checks, including optional TDD test design.
    """

    def __init__(
        self,
        llm_client: "LLMClient",
        ws: "WorkflowSession | None" = None,
        dispatcher: "WSMessageDispatcher | None" = None,
        **kwargs,
    ) -> None:
        """Initialise an assembly phase node.

        Args:
            llm_client: The LLM client for plan assembly.
            ws: Optional WorkflowSession for streaming.
            dispatcher: Optional WebSocket dispatcher.
            **kwargs: Additional keyword arguments forwarded to AssemblyPhase.
        """
        super().__init__("assembly_phase")
        self._llm_client = llm_client
        self._ws = ws
        self._dispatcher = dispatcher
        self._kwargs = kwargs

    async def execute(self, state: WorkflowState) -> NodeResult:
        """Run Phase 4 plan assembly and store the result in state."""
        try:
            assembly_phase = AssemblyPhase()
            plan = await assembly_phase.execute(
                task=state.session_metadata.get("task", ""),
                scope=state.session_metadata.get("scope", ""),
                file_summary=state.session_metadata.get("file_summary", ""),
                design_and_risks=state.session_metadata.get("design_and_risks_obj", DesignAndRisks()),
                llm_client=self._llm_client,
                ws=self._ws,
                dispatcher=self._dispatcher,
                context=state.session_metadata.get("context", ""),
                repo_root=state.session_metadata.get("repo_root", ""),
                session_id=state.session_metadata.get("session_id", ""),
                refiner=self._kwargs.get("refiner"),
                test_command=self._kwargs.get("test_command", ""),
                expert_llm_client=self._kwargs.get("expert_llm_client"),
                file_summary_obj=state.session_metadata.get("file_summary_obj"),
                on_content=self._kwargs.get("on_content"),
                on_thinking=self._kwargs.get("on_thinking"),
                on_tool_call=self._kwargs.get("on_tool_call"),
                on_tool_result=self._kwargs.get("on_tool_result"),
                on_metrics=self._kwargs.get("on_metrics"),
                on_metrics_reset=self._kwargs.get("on_metrics_reset"),
            )
            state.session_metadata["plan"] = plan
            return Continue(
                next_node_id=None,
                payload={"plan": plan},
            )
        except Exception as exc:
            return Fail(error=f"Assembly phase failed: {exc}")


class PlanningPipeline:
    """Orchestrator for the 4-phase planning pipeline with telemetry integration.

    Manages phase execution, telemetry signaling, and model routing without
    leaking implementation details between phases. Uses ``RoutingPolicy`` to
    resolve per-phase model roles and fires ``_send_stage`` / ``_send_stage_done``
    at phase boundaries for WebSocket streaming.
    """

    def __init__(
        self,
        task: str,
        repo_root: str,
        llm_client: "LLMClient",
        state_manager: StateManager,
        context: str = "",
        revision_context: str | None = None,
        previous_plan: ExecutionPlan | None = None,
        ws: WorkflowSession | None = None,
        dispatcher: "WSMessageDispatcher | None" = None,
        refiner: "PromptRefiner | None" = None,
        test_command: str = "",
        expert_llm_client: "LLMClient | None" = None,
        on_content: "Callable | None" = None,
        on_thinking: "Callable | None" = None,
        on_tool_call: "Callable | None" = None,
        on_tool_result: "Callable | None" = None,
        on_metrics: "Callable | None" = None,
        on_metrics_reset: "Callable | None" = None,
    ) -> None:
        self.task = task
        self.repo_root = repo_root
        self.llm_client = llm_client
        self.context = context
        self.revision_context = revision_context
        self.previous_plan = previous_plan
        self.ws = ws
        self.dispatcher = dispatcher
        self.refiner = refiner
        self.test_command = test_command
        self.state_manager = state_manager
        self.session_id = state_manager.session_id
        self.expert_llm_client = expert_llm_client
        self.on_content = on_content
        self.on_thinking = on_thinking
        self.on_tool_call = on_tool_call
        self.on_tool_result = on_tool_result
        self.on_metrics = on_metrics
        self.on_metrics_reset = on_metrics_reset
        self.routing = RoutingPolicy(
            primary=llm_client,
            expert=expert_llm_client,
        )

    async def run(self) -> ExecutionPlan:
        """Execute the four-phase planning pipeline and return an ExecutionPlan.

        Orchestrates ScopePhase, ExplorationPhase, DesignPhase, and AssemblyPhase
        with explicit argument passing. Fires _send_stage/_send_stage_done at
        phase boundaries. Uses RoutingPolicy for per-phase model selection.

        Returns:
            A validated ExecutionPlan ready for per-step execution.
        """
        # If revision context is provided, delegate to the revision helper.
        if self.revision_context:
            return await _revise_plan(
                self.task,
                self.revision_context,
                self.llm_client,
                self.context,
                self.ws,
                repo_root=self.repo_root,
                expert_llm_client=self.expert_llm_client,
                primary_llm_client=self.llm_client,
                previous_plan=self.previous_plan,
                on_thinking=self.on_thinking,
                on_metrics=self.on_metrics,
                on_metrics_reset=self.on_metrics_reset,
            )

        # Resolve model clients via RoutingPolicy.
        explorer = self.routing.get_client("scope")
        phase_max_tokens = settings.ollama_max_tokens

        expert = self.expert_llm_client or self.llm_client
        expert_max_tokens = (
            settings.effective_expert_max_tokens
            if self.expert_llm_client
            else phase_max_tokens
        )

        expert_ctx = (
            settings.effective_expert_context_window
            if self.expert_llm_client
            else settings._active_context_window
        )
        plan_assembly_max_tokens = max(
            expert_max_tokens,
            int(expert_ctx * PLAN_OUTPUT_PERCENT),
        )
        plan_start = time.monotonic()
        phase_timings: dict[str, float] = {}
        scope = self.task
        file_summary = ""
        file_summary_obj: FileSummary | None = None
        design_and_risks_obj = DesignAndRisks()
        project_context = self.context

        # ── Cross-session memory retrieval ──
        memory_context = ""
        if settings.enable_session_memory:
            memory_context = await _retrieve_session_memories(self.repo_root, self.task)

        try:
            # ── Phase 1: Scope analysis ──
            scope_phase = ScopePhase()
            scope_obj = await scope_phase.execute(
                task=self.task,
                llm_client=explorer,
                ws=self.ws,
                dispatcher=self.dispatcher,
                context=self.context,
                repo_root=self.repo_root,
                session_id=self.session_id,
                on_content=self.on_content,
                on_thinking=self.on_thinking,
                on_tool_call=self.on_tool_call,
                on_tool_result=self.on_tool_result,
                on_metrics=self.on_metrics,
                on_metrics_reset=self.on_metrics_reset,
                expert_llm_client=self.expert_llm_client,
            )
            # Convert ScopeDocument to markdown for downstream phases.
            scope = scope_obj.to_markdown() if scope_obj else self.task

            # ── Phase 2: Codebase exploration ──
            exploration_phase = ExplorationPhase()
            file_summary_obj = await exploration_phase.execute(
                task=self.task,
                scope=scope,
                llm_client=explorer,
                ws=self.ws,
                dispatcher=self.dispatcher,
                context=self.context,
                repo_root=self.repo_root,
                session_id=self.session_id,
                refiner=self.refiner,
                on_content=self.on_content,
                on_thinking=self.on_thinking,
                on_tool_call=self.on_tool_call,
                on_tool_result=self.on_tool_result,
                on_metrics=self.on_metrics,
                on_metrics_reset=self.on_metrics_reset,
                expert_llm_client=self.expert_llm_client,
            )
            file_summary = file_summary_obj.to_markdown() if file_summary_obj else ""

            # Privacy stripping if refiner is active
            if self.refiner is not None and hasattr(self.refiner, "is_active") and self.refiner.is_active:
                file_summary, redactions = await self.refiner.strip_privacy(file_summary)
                if redactions:
                    logger.info(
                        "Privacy: stripped %d items from file summary",
                        len(redactions),
                    )

            # Compact file summary for small context windows
            if settings._active_context_window <= 32768:
                file_summary = await _compact_file_summary(
                    file_summary,
                    explorer,
                    settings._active_context_window,
                )

            # Signal model switch if using expert
            if self.expert_llm_client:
                await _send_stage(
                    self.ws,
                    f"Switching to expert model ({self.expert_llm_client.model_name}) for design phases...",
                    model=self.expert_llm_client.model_name,
                )
                logger.info(
                    "Switching to expert model for phases 3-4: %s",
                    self.expert_llm_client.model_name,
                )

            # ── Phase 3: Design synthesis ──
            design_phase = DesignPhase()
            design_and_risks_obj = await design_phase.execute(
                task=self.task,
                scope=scope,
                file_summary=file_summary,
                llm_client=expert,
                ws=self.ws,
                dispatcher=self.dispatcher,
                context=self.context,
                repo_root=self.repo_root,
                session_id=self.session_id,
                on_content=self.on_content,
                on_thinking=self.on_thinking,
                on_tool_call=self.on_tool_call,
                on_tool_result=self.on_tool_result,
                on_metrics=self.on_metrics,
                on_metrics_reset=self.on_metrics_reset,
                expert_llm_client=self.expert_llm_client,
                primary_llm_client=explorer,
            )

            design_and_risks = _format_design_and_risks(design_and_risks_obj)
            missing_files = _format_missing_files(design_and_risks_obj.missing_files)

            # ── Phase 4: Plan assembly ──
            assembly_phase = AssemblyPhase()
            plan = await assembly_phase.execute(
                task=self.task,
                scope=scope,
                file_summary=file_summary,
                design_and_risks=design_and_risks_obj,
                llm_client=expert,
                ws=self.ws,
                dispatcher=self.dispatcher,
                context=self.context,
                repo_root=self.repo_root,
                session_id=self.session_id,
                refiner=self.refiner,
                test_command=self.test_command,
                expert_llm_client=self.expert_llm_client,
                primary_llm_client=explorer,
                file_summary_obj=file_summary_obj,
                on_content=self.on_content,
                on_thinking=self.on_thinking,
                on_tool_call=self.on_tool_call,
                on_tool_result=self.on_tool_result,
                on_metrics=self.on_metrics,
                on_metrics_reset=self.on_metrics_reset,
            )

            phase_timings["total"] = time.monotonic() - plan_start

            if settings.debug_planning and self.session_id:
                meta = {
                    "session_id": self.session_id,
                    "task": self.task,
                    "timings": phase_timings,
                    "steps": len(plan.steps),
                    "affected_files": len(plan.affected_files),
                }
                debug_dir = Path(self.repo_root) / ".lean_ai" / "plan_debug" / self.session_id
                debug_dir.mkdir(parents=True, exist_ok=True)
                (debug_dir / "meta.json").write_text(
                    json.dumps(meta, indent=2),
                    encoding="utf-8",
                )

            logger.info(
                "Plan created: %d steps, %d affected files",
                len(plan.steps),
                len(plan.affected_files),
            )
            state = await self.state_manager.get_state_async()
            state.current_plan = plan.model_dump()
            self.state_manager.save()
            return plan
        except Exception as exc:
            logger.exception("Planning pipeline failed — returning fallback plan")
            plan = _build_fallback_execution_plan(
                task=self.task,
                scope=scope,
                file_summary_obj=file_summary_obj,
                design_and_risks_obj=design_and_risks_obj,
                test_command=self.test_command,
                failure_summary=(
                    f"planning pipeline aborted before normal completion: {type(exc).__name__}: {exc}"
                ),
            )
            phase_timings["total"] = time.monotonic() - plan_start
            if settings.debug_planning and self.session_id:
                _save_debug_phase(
                    self.repo_root,
                    self.session_id,
                    "phase_fallback_plan",
                    plan.model_dump_json(indent=2),
                    phase_timings["total"],
                )
            await _send_stage_done(
                self.ws,
                (
                    "Fallback plan assembled — "
                    f"{len(plan.steps)} steps across {len(plan.affected_files)} file(s)"
                ),
                model=expert.model_name,
                phase=4,
            )
            state = await self.state_manager.get_state_async()
            state.current_plan = plan.model_dump()
            self.state_manager.save()
            return plan


async def create_plan(
    task: str,
    repo_root: str,
    llm_client: "LLMClient",
    state_manager: StateManager | None = None,
    session_id: str = "",
    context: str = "",
    revision_context: str | None = None,
    previous_plan: ExecutionPlan | None = None,
    ws: WorkflowSession | None = None,
    dispatcher: "WSMessageDispatcher | None" = None,
    refiner: "PromptRefiner | None" = None,
    test_command: str = "",
    expert_llm_client: "LLMClient | None" = None,
    on_content: "Callable | None" = None,
    on_thinking: "Callable | None" = None,
    on_tool_call: "Callable | None" = None,
    on_tool_result: "Callable | None" = None,
    on_metrics: "Callable | None" = None,
    on_metrics_reset: "Callable | None" = None,
) -> ExecutionPlan:
    """Create a plan using decomposed planning.

    Phases 1–2 (scope + codebase exploration) run on the **primary** model
    because exploration benefits from a coder-tuned model that can read
    files precisely. The worker model already compresses large tool
    outputs before they re-enter the primary's context (see
    ``workflow/tool_executor.py``), so the primary isn't on its own.
    Phases 3–4 (design + assembly) run on the **expert**
    model when configured. The **request** model is reserved for chat
    and ``/request`` mode — not planning.

    Args:
        task: The user's task description (may include clarification answers).
        repo_root: Path to the repository root.
        llm_client: Primary LLM client — runs phases 1–2 and implementation.
        context: Pre-assembled context (project context, search results, etc.).
        revision_context: If revising, the previous plan JSON + user feedback.
        ws: Optional WorkflowSession for streaming stage progress.
        refiner: Optional local refiner for privacy-stripping file summaries.
        test_command: If set, planner folds test commands into success checks.
        expert_llm_client: Optional expert model for phases 3–4 reasoning.
        on_content: Streaming callback for content tokens.
        on_thinking: Streaming callback for thinking tokens.
        on_tool_call: Callback for tool call events (phase 2).
        on_tool_result: Callback for tool result events (phase 2).
        on_metrics: Callback for metrics updates (phase 2).

    Returns:
        Structured ExecutionPlan ready for per-step execution.
    """
    # If revision context is provided, delegate to the revision helper.
    if revision_context:
        return await _revise_plan(
            task,
            revision_context,
            llm_client,
            context,
            ws,
            repo_root=repo_root,
            expert_llm_client=expert_llm_client,
            primary_llm_client=llm_client,
            previous_plan=previous_plan,
            on_thinking=on_thinking,
            on_metrics=on_metrics,
            on_metrics_reset=on_metrics_reset,
        )

    # Resolve model clients via RoutingPolicy.
    routing = RoutingPolicy(
        primary=llm_client,
        expert=expert_llm_client,
    )
    explorer = routing.get_client("scope")
    expert = expert_llm_client or llm_client

    effective_session_id = state_manager.session_id if state_manager is not None else session_id
    if state_manager is None:
        state_manager = StateManager(effective_session_id or "planning")

    # Build the 4-phase subgraph
    phase_kwargs = {
        "refiner": refiner,
        "test_command": test_command,
        "expert_llm_client": expert_llm_client,
        "primary_llm_client": llm_client,
        "state_manager": state_manager,
        "on_content": on_content,
        "on_thinking": on_thinking,
        "on_tool_call": on_tool_call,
        "on_tool_result": on_tool_result,
        "on_metrics": on_metrics,
        "on_metrics_reset": on_metrics_reset,
    }

    scope_node = ScopePhaseNode(
        llm_client=explorer,
        ws=ws,
        dispatcher=dispatcher,
        **phase_kwargs,
    )
    exploration_node = ExplorationPhaseNode(
        llm_client=explorer,
        ws=ws,
        dispatcher=dispatcher,
        **phase_kwargs,
    )
    design_node = DesignPhaseNode(
        llm_client=expert,
        ws=ws,
        dispatcher=dispatcher,
        **phase_kwargs,
    )
    assembly_node = AssemblyPhaseNode(
        llm_client=expert,
        ws=ws,
        dispatcher=dispatcher,
        **phase_kwargs,
    )

    # Build the WorkflowGraph with 4 phase nodes
    graph = WorkflowGraph()
    graph.add_node(scope_node)
    graph.add_node(exploration_node)
    graph.add_node(design_node)
    graph.add_node(assembly_node)

    # Prepare state with metadata needed by phase nodes
    state = await state_manager.get_state_async()
    state.session_metadata["task"] = task
    state.session_metadata["context"] = context
    state.session_metadata["repo_root"] = repo_root
    state.session_metadata["session_id"] = effective_session_id

    # Execute the subgraph
    engine = WorkflowEngine()
    result = await engine.run(graph, state_manager=state_manager, state=state)

    if isinstance(result, Fail):
        logger.exception("Planning subgraph failed — returning fallback plan")
        plan = _build_fallback_execution_plan(
            task=task,
            scope=task,
            file_summary_obj=state.session_metadata.get("file_summary_obj"),
            design_and_risks_obj=state.session_metadata.get("design_and_risks_obj", DesignAndRisks()),
            test_command=test_command,
            failure_summary=f"planning subgraph failed: {result.error}",
        )
        state.current_plan = plan.model_dump()
        state_manager.save()
        return plan

    # Extract the plan from state
    plan = state.session_metadata.get("plan")
    if plan is None:
        logger.warning("Planning subgraph completed but no plan found — returning fallback")
        plan = _build_fallback_execution_plan(
            task=task,
            scope=task,
            file_summary_obj=None,
            design_and_risks_obj=DesignAndRisks(),
            test_command=test_command,
            failure_summary="planning subgraph completed without producing a plan",
        )

    state.current_plan = plan.model_dump()
    state_manager.save()
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
    ws: WorkflowSession | None,
    repo_root: str,
    session_id: str,
    on_thinking: Callable | None,
    on_metrics: Callable | None,
    on_metrics_reset: Callable | None,
    prompt_scope=None,
) -> float:
    """Run the legacy Phase 5 verification helper.

    This path is retained for compatibility tests and older debug
    artifacts. Active TDD planning now happens in Phase 4b/4c. The
    helper still appends test creation + test execution steps to the
    plan (normal mode) or stores them separately in
    ``plan.tdd_test_steps`` (legacy TDD mode). Receives the structured
    Phase 2 ``FileSummary`` and Phase 3 ``DesignAndRisks`` so the user
    prompt can target specific files for coverage and cite
    critical_risks as security cases.

    Test-path convention warnings are appended to
    ``plan.plan_validation_warnings`` so the approval UI (the Phase 4
    surfacing mechanism) carries them through to the user.

    Returns elapsed time in seconds.
    """
    tdd_mode = False
    phase_label = (
        "Phase 5: Designing TDD test steps..."
        if tdd_mode
        else "Phase 5: Adding verification steps..."
    )
    await _send_stage(ws, phase_label, model=expert.model_name, phase=5)
    logger.info(
        "Planning Phase 5: Verification step generation (tdd=%s)",
        tdd_mode,
    )
    t0 = time.monotonic()

    impl_plan_md = plan_to_markdown(plan)
    next_step = len(plan.steps) + 1

    verification_targets = _build_verification_targets(
        file_summary_obj,
        design_and_risks_obj,
    )
    security_concerns = _build_security_concerns(design_and_risks_obj)

    # Layer 6 (testing inventory) populates in a later PR; for now pass
    # an explicit empty-marker so the prompt still formats cleanly.
    testing_inventory = _format_testing_inventory(file_summary_obj)

    # Layer 9 (core functionality) populates in a later PR; for now pass
    # an explicit empty-marker.
    core_functionality = _format_core_functionality(plan)

    # Structured sections from Phase 3 — always included in Phase 5 prompts.
    dependency_order_block = _format_dependency_order(design_and_risks_obj)
    naming_conventions_block = _format_naming_conventions_section(design_and_risks_obj)
    risk_assessment_block = _format_risk_assessment_section(design_and_risks_obj)

    # Layer 4 graceful-degradation scaffolding: when ``test_command`` is
    # empty, omit the "end with run_tests" rule so the LLM doesn't
    # invent a phantom test command. The Layer 4 PR removes the
    # ``if test_command:`` gate around Phase 5 entirely.
    if test_command:
        run_tests_rule = f"- Exactly ONE final run_tests step invoking: {test_command}\n"
    else:
        run_tests_rule = (
            "- Do NOT include a run_tests step — no test runner is "
            "configured for this workspace yet. Create the test "
            "files on disk; the runner will be added later.\n"
        )

    if tdd_mode:
        user_content = registry.format_text(
            "planning.verification_user_tdd",
            task=task,
            impl_plan_md=impl_plan_md,
            testing_inventory=testing_inventory,
            verification_targets=verification_targets,
            security_concerns=security_concerns,
            core_functionality=core_functionality,
            next_step=str(next_step),
            dependency_order=dependency_order_block,
            naming_conventions=naming_conventions_block,
            risk_assessment=risk_assessment_block,
        )
    else:
        user_content = registry.format_text(
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
            dependency_order=dependency_order_block,
            naming_conventions=naming_conventions_block,
            risk_assessment=risk_assessment_block,
        )

    verification = await _chat_structured_with_repair(
        messages=[
            {
                "role": "system",
                "content": resolve_prompt_text("planning.verification_system", scope=prompt_scope),
            },
            {"role": "user", "content": user_content},
        ],
        schema=VerificationPlan,
        expert=expert,
        max_tokens=plan_assembly_max_tokens,
        artifact_label="verification plan",
        ws=ws,
        phase=5,
        on_thinking=on_thinking,
        on_metrics=on_metrics,
        on_metrics_reset=on_metrics_reset,
    )

    if tdd_mode:
        # TDD: keep test steps separate for expert-first execution.
        # The TDD user prompt asks explicitly for no run_tests step;
        # keep the filter as defensive safety in case the model
        # ignores that instruction.
        test_steps_only = [s for s in verification.steps if s.tool != "run_tests"]
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
                "Phase 5 produced no run_tests step — injecting one so existing tests run (%s).",
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
                        "Verify the existing test suite still passes after the plan's changes."
                    ),
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
    all_verification_steps = plan.tdd_test_steps if tdd_mode else verification.steps
    existing = set(plan.affected_files)
    for step in all_verification_steps:
        if step.file_path and step.file_path not in existing:
            plan.affected_files.append(step.file_path)

    # Test-path convention check — append warnings to the plan so the
    # approval UI surfacing (from Phase 4) picks them up.
    path_warnings = _check_test_path_conventions(
        verification,
        file_summary_obj,
    )
    if path_warnings:
        plan.plan_validation_warnings.extend(path_warnings)

    # Layer 2 — coverage validator: warn when an executable affected
    # file has no test step referencing it. Non-blocking.
    coverage_warnings = _check_affected_files_covered(
        verification,
        plan,
        file_summary_obj,
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
        repo_root,
        session_id,
        "phase_5_verification",
        verification.model_dump_json(indent=2),
        elapsed,
    )
    test_steps = len(all_verification_steps)
    if tdd_mode:
        stage_msg = f"TDD test steps designed — {test_steps} step(s)"
    elif not test_command:
        stage_msg = f"Test files seeded — {test_steps} step(s); no test runner configured"
    else:
        stage_msg = f"Verification steps added — {test_steps} test step(s)"
    await _send_stage_done(
        ws,
        stage_msg,
        model=expert.model_name,
        phase=5,
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
    on_metrics: "Callable | None" = None,
    on_metrics_reset: "Callable | None" = None,
) -> DesignAndRisks:
    """Coerce Phase 3's exploration prose + inputs into a DesignAndRisks.

    On structured-output failure, returns a minimal DesignAndRisks with the
    exploration prose stashed in ``notes`` so the pipeline keeps moving.
    """
    synthesis_system = registry.get_text("planning.design_synthesis_system")
    user_parts = [
        f"TASK: {task}",
        f"SCOPE:\n{scope}",
    ]
    if project_context_block:
        user_parts.append(project_context_block.rstrip())
    user_parts.append(f"FILE SUMMARY:\n{file_summary}")
    if exploration_prose.strip():
        user_parts.append(f"PASS 1 EXPLORATION PROSE:\n{exploration_prose}")
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
            on_metrics=on_metrics,
            on_metrics_reset=on_metrics_reset,
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
            lines.append(f"| {nc.category} | {nc.pattern} | {nc.source_file} |")
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
            lines.append(f"- {d.file_path} depends on {d.depends_on} — {d.reason}")
        lines.append("")

    if dar.critical_risks:
        lines.append("## Critical Risks")
        lines.append("")
        for r in dar.critical_risks:
            lines.append(f"- **[{r.severity}]** {r.risk} — {r.mitigation}")
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


def _format_dependency_order(dar: DesignAndRisks) -> str:
    """Render dependency_order as a structured block for Phase 4 prompts.

    Returns empty string when no dependency entries exist.
    """
    if not dar.dependency_order:
        return ""
    lines: list[str] = []
    for d in dar.dependency_order:
        lines.append(f"- {d.file_path} depends on {d.depends_on} — {d.reason}")
    return "DEPENDENCY ORDER:\n" + "\n".join(lines) + "\n\n"


def _format_naming_conventions_section(dar: DesignAndRisks) -> str:
    """Render naming_conventions as a structured block for Phase 4 prompts.

    Returns empty string when no naming conventions exist.
    """
    if not dar.naming_conventions:
        return ""
    lines: list[str] = []
    lines.append("| category | pattern | source_file |")
    lines.append("|---|---|---|")
    for nc in dar.naming_conventions:
        lines.append(f"| {nc.category} | {nc.pattern} | {nc.source_file} |")
    return "NAMING CONVENTIONS:\n" + "\n".join(lines) + "\n\n"


def _format_risk_assessment_section(dar: DesignAndRisks) -> str:
    """Render critical_risks as a structured block for Phase 4/5 prompts.

    Returns empty string when no risks exist.
    """
    if not dar.critical_risks:
        return ""
    lines: list[str] = []
    for r in dar.critical_risks:
        lines.append(f"- **[{r.severity}]** {r.risk} — {r.mitigation}")
    return "RISK ASSESSMENT:\n" + "\n".join(lines) + "\n\n"


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
) -> tuple[list[str], bool]:
    """Flag any step.file_path that is not in the prior-phase path universe.

    Returns ``(warnings, is_blocking)``. Invented paths are blocking —
    the plan references files the prior phases never identified.
    """
    if not known_paths:
        return [], False
    plan_paths: set[str] = set()
    for step in plan.steps:
        if step.file_path:
            plan_paths.add(step.file_path)
        for target in step.may_change:
            if target.path:
                plan_paths.add(target.path)
    warnings = [f"invented path: {p}" for p in sorted(plan_paths - known_paths)]
    return warnings, bool(warnings)


def _uncovered_missing_files(
    plan: ExecutionPlan,
    dar: DesignAndRisks,
) -> list[MissingFile]:
    """Return MissingFile entries not covered by any plan step.

    Returns the structured objects so the caller can branch on
    ``.blocking`` (triggers auto-revision) versus non-blocking (warn only).
    """
    step_paths = {s.file_path for s in plan.steps}
    for step in plan.steps:
        step_paths.update(target.path for target in step.may_change if target.path)
    return [mf for mf in dar.missing_files if mf.file_path not in step_paths]


def _check_edit_create_consistency(
    plan: ExecutionPlan,
    file_summary: FileSummary | None,
    dar: DesignAndRisks,
) -> tuple[list[str], bool]:
    """Flag edit_file on unknown paths and create_file on existing paths.

    Returns ``(warnings, is_blocking)``. Tool/path mismatches are blocking
    because the executor will fail if asked to edit a file it cannot find
    or create a file that already exists.
    """
    if file_summary is None:
        return [], False
    to_modify: set[str] = {o.file_path for o in file_summary.files_to_modify}
    to_modify |= {o.file_path for o in file_summary.files_read_for_context}
    to_create: set[str] = {o.file_path for o in file_summary.files_to_create}
    to_create |= {mf.file_path for mf in dar.missing_files}
    warnings: list[str] = []
    for s in plan.steps:
        paths = [target.path for target in s.may_change if target.path]
        if not paths and s.file_path:
            paths = [s.file_path]
        for path in paths:
            if path in to_modify or path in to_create:
                continue
            if "edit_file" in s.allowed_tools or "create_file" in s.allowed_tools:
                warnings.append(f"write target not found in prior-phase paths: {path}")
    return warnings, bool(warnings)


def _sync_affected_files_from_steps(plan: ExecutionPlan) -> None:
    """Ensure affected_files includes every declared mutation target."""
    seen = set(plan.affected_files)
    for step in list(plan.tdd_test_steps) + list(plan.steps):
        if step.file_path and step.file_path not in seen and step.tool in IMPLEMENTATION_STEP_TOOLS:
            plan.affected_files.append(step.file_path)
            seen.add(step.file_path)
        for target in step.may_change:
            if target.path and target.path not in seen:
                plan.affected_files.append(target.path)
                seen.add(target.path)


def _step_contract_haystack(step: PlanStep) -> str:
    """Text surface for checking whether a job contract covers a path/entity."""
    parts: list[str] = [
        step.job or "",
        step.instruction or "",
        step.reason or "",
        step.output_shape or "",
        step.file_path or "",
    ]
    parts.extend(f"{inp.source} {inp.details}" for inp in step.inputs)
    parts.extend(f"{target.path} {target.change}" for target in step.may_change)
    parts.extend(step.must_not_change)
    for check in step.success_checks:
        parts.extend([check.description, check.tool, check.command, check.expected])
    return "\n".join(part for part in parts if part)


def _collect_executable_code_paths(
    plan: ExecutionPlan,
    file_summary: FileSummary | None,
) -> set[str]:
    """Return executable affected-file paths that need verification coverage."""
    code_paths: set[str] = set()
    if file_summary is not None:
        for obs in file_summary.files_to_create:
            if obs.file_path and _has_executable_extension(obs.file_path):
                code_paths.add(obs.file_path)
        for obs in file_summary.files_to_modify:
            if obs.file_path and _has_executable_extension(obs.file_path):
                code_paths.add(obs.file_path)
    else:
        for path in plan.affected_files:
            if _has_executable_extension(path):
                code_paths.add(path)
    return code_paths


def _path_is_covered_in_step(path: str, step: PlanStep) -> bool:
    """Return True when a plan step clearly references *path*."""
    haystack = _step_contract_haystack(step)
    filename = path.rsplit("/", 1)[-1]
    return path in haystack or filename in haystack


def _check_success_checks_cover_affected_files(
    plan: ExecutionPlan,
    file_summary: FileSummary | None,
) -> tuple[list[str], bool]:
    """Block when executable affected files have no test/success-check contract.

    Returns ``(errors, is_blocking)``. Missing success checks are
    blocking — the plan must be revised to include concrete success
    checks for every affected executable file.
    """
    code_paths = _collect_executable_code_paths(plan, file_summary)
    if not code_paths:
        return [], False

    warnings: list[str] = []
    for code_path in sorted(code_paths):
        covered = False
        for step in plan.steps:
            if _path_is_covered_in_step(code_path, step) and step.success_checks:
                covered = True
                break
        if not covered:
            warnings.append(f"affected file has no success-check coverage: {code_path}")
    return warnings, bool(warnings)


def _check_tdd_test_contract_cover_affected_files(
    plan: ExecutionPlan,
    file_summary: FileSummary | None,
) -> tuple[list[str], bool]:
    """Block when TDD plans omit both authored tests and executable checks.

    In TDD mode, affected executable files need at least one of:
    1. A Phase 4b-authored test step in ``tdd_test_steps`` that references
       the file or its primary symbol, or
    2. An implementation-step success check that references the file.
    """
    if not plan.tdd_mode:
        return [], False

    code_paths = _collect_executable_code_paths(plan, file_summary)
    if not code_paths:
        return [], False

    warnings: list[str] = []
    for code_path in sorted(code_paths):
        success_checked = any(
            _path_is_covered_in_step(code_path, step) and step.success_checks
            for step in plan.steps
        )
        tdd_tested = any(
            _path_is_covered_in_step(code_path, step) for step in plan.tdd_test_steps
        )
        if not (success_checked or tdd_tested):
            warnings.append(
                f"TDD contract missing authored test or success-check coverage: {code_path}"
            )
    return warnings, bool(warnings)


def _plan_adds_test_setup(plan: ExecutionPlan) -> bool:
    """Return True when a plan explicitly establishes test infrastructure."""
    setup_terms = (
        "test setup",
        "testing setup",
        "test infrastructure",
        "testing infrastructure",
        "commands.json",
        "pytest",
        "vitest",
        "jest",
        "cargo test",
        "go test",
        "phpunit",
        "rspec",
        "junit",
        "make test",
    )
    setup_paths = (
        ".lean_ai/commands.json",
        "pyproject.toml",
        "pytest.ini",
        "package.json",
        "vitest.config",
        "jest.config",
        "Cargo.toml",
        "go.mod",
        "composer.json",
        "phpunit.xml",
        "Gemfile",
        "build.gradle",
        "pom.xml",
    )
    for step in plan.steps:
        setup_text_parts = [
            step.job or "",
            step.instruction or "",
            step.reason or "",
            step.output_shape or "",
            *(target.change for target in step.may_change),
        ]
        haystack = "\n".join(setup_text_parts).lower()
        paths = [step.file_path, *(target.path for target in step.may_change)]
        if any(term in haystack for term in setup_terms):
            return True
        if any(path and any(marker in path for marker in setup_paths) for path in paths):
            return True
    return False


def _check_tdd_required_for_executable_files(
    plan: ExecutionPlan,
    file_summary: FileSummary | None,
) -> tuple[list[str], bool]:
    """Block strict-test plans that do not include authored TDD tests."""
    if not settings.enable_strict_test_contract:
        return [], False

    code_paths = _collect_executable_code_paths(plan, file_summary)
    if not code_paths:
        return [], False
    if plan.tdd_mode and plan.tdd_test_steps:
        return [], False
    if _plan_adds_test_setup(plan):
        return [], False

    paths = ", ".join(sorted(code_paths))
    return [
        "strict TDD contract requires pre-implementation test steps for "
        f"executable affected files: {paths}"
    ], True


def _check_full_suite_command_available(
    plan: ExecutionPlan,
    file_summary: FileSummary | None,
    test_command: str,
) -> tuple[list[str], bool]:
    """Block executable plans that have no final full-suite test command."""
    if not settings.enable_strict_test_contract:
        return [], False
    if test_command.strip():
        return [], False
    if not _collect_executable_code_paths(plan, file_summary):
        return [], False
    if _plan_adds_test_setup(plan):
        return [], False
    return [
        "strict test contract requires a project test command for final full-suite "
        "validation; add test setup and record `.lean_ai/commands.json`"
    ], True


def _check_success_checks_are_specific(
    plan: ExecutionPlan,
) -> tuple[list[str], bool]:
    """Reject vague success checks that lack concrete tool/command references.

    Scans every success check's description for vague patterns like
    'verify', 'test that', 'check that', 'ensure' that are not followed
    by a concrete tool or command reference. A check is considered
    specific if its combined text (description + tool + command + expected)
    mentions a concrete tool name or shell command.

    Returns ``(errors, is_blocking)``. Vague success checks are blocking
    because they cannot be mechanically verified by the executor.
    """
    vague_prefixes = ("verify", "test that", "check that", "ensure")
    concrete_signals = (
        "pytest", "npm test", "vitest", "jest", "mocha", "unittest",
        "python -m", "node", "cargo test", "go test", "make test",
        "grep", "cat", "read_file", "list_directory", "directory_tree",
        "python ", "node ", "npm ", "yarn ", "pnpm ", "bun ",
        "./", "bash", "sh -c", "curl", "http", "diff",
    )
    errors: list[str] = []
    for step in plan.steps:
        for check in step.success_checks:
            text = f"{check.description} {check.tool} {check.command} {check.expected}".lower()
            # Check if description starts with a vague pattern
            desc_lower = check.description.lower()
            if not any(desc_lower.startswith(p) for p in vague_prefixes):
                continue
            # Check if there's a concrete signal anywhere in the check
            if any(signal in text for signal in concrete_signals):
                continue
            errors.append(
                f"vague success check in step {step.step_number}: "
                f"'{check.description}' lacks concrete tool/command reference"
            )
    return errors, bool(errors)


def _check_core_functionality_success_checked(
    plan: ExecutionPlan,
) -> tuple[list[str], bool]:
    """Warn when core-functionality tags lack regression-oriented checks.

    Returns ``(warnings, is_blocking)``. Missing regression checks on
    core functionality are non-blocking — the plan still executes but
    the user is warned.
    """
    tags = getattr(plan, "core_functionality", None) or []
    if not tags:
        return [], False

    confidence_rank = {"low": 0, "medium": 1, "high": 2}
    try:
        min_rank = confidence_rank[settings.core_functionality_min_confidence]
    except KeyError:
        min_rank = confidence_rank["medium"]

    enforced_tags = [t for t in tags if confidence_rank.get(t.confidence, 1) >= min_rank]
    warnings: list[str] = []
    for tag in enforced_tags:
        entity = tag.entity.strip()
        file_path = tag.file_path.strip()
        covered = False
        for step in list(plan.tdd_test_steps) + list(plan.steps):
            haystack = _step_contract_haystack(step).lower()
            if (
                (
                    (entity and entity.lower() in haystack)
                    or (file_path and file_path.lower() in haystack)
                )
                and "regression" in haystack
                and (step.success_checks or step in plan.tdd_test_steps)
            ):
                covered = True
                break
        if not covered:
            warnings.append(
                f"core-functionality tag missing regression success check: "
                f"'{entity}' in {file_path} "
                f"[{tag.source_signal}, confidence={tag.confidence}]"
            )
    return warnings, False


def _run_plan_validations(
    plan: ExecutionPlan,
    file_summary: FileSummary | None,
    dar: DesignAndRisks,
    test_command: str = "",
) -> tuple[list[str], bool]:
    """Run every validator, log each warning, and return ``(warnings, is_blocking)``.

    Shared between the pre- and post-revision passes so the logic stays
    in one place. The ``is_blocking`` flag is True when any blocking
    validator produced warnings, indicating the plan should be revised.
    """
    warnings: list[str] = []
    is_blocking = False
    known = _collect_known_paths(file_summary, dar)

    w, b = _check_hallucinated_paths(plan, known)
    warnings.extend(w)
    is_blocking = is_blocking or b

    w, b = _check_edit_create_consistency(plan, file_summary, dar)
    warnings.extend(w)
    is_blocking = is_blocking or b

    w, b = _check_success_checks_cover_affected_files(plan, file_summary)
    warnings.extend(w)
    is_blocking = is_blocking or b

    w, b = _check_tdd_test_contract_cover_affected_files(plan, file_summary)
    warnings.extend(w)
    is_blocking = is_blocking or b

    w, b = _check_tdd_required_for_executable_files(plan, file_summary)
    warnings.extend(w)
    is_blocking = is_blocking or b

    w, b = _check_full_suite_command_available(plan, file_summary, test_command)
    warnings.extend(w)
    is_blocking = is_blocking or b

    w, b = _check_success_checks_are_specific(plan)
    warnings.extend(w)
    is_blocking = is_blocking or b

    w, b = _check_core_functionality_success_checked(plan)
    warnings.extend(w)
    # Non-blocking — do not flip is_blocking

    uncovered = _uncovered_missing_files(plan, dar)
    for mf in uncovered:
        tag = " [BLOCKING]" if mf.blocking else ""
        warnings.append(f"uncovered missing file: {mf.file_path} — {mf.purpose}{tag}")
        if mf.blocking:
            is_blocking = True

    for w in warnings:
        logger.warning("Phase 4 plan validation — %s", w)
    return warnings, is_blocking


# ── Phase 4a helpers ───────────
#
# Phase 4a performs deterministic grep-based test discovery before Phase 4
# plan assembly. This gives the LLM concrete test file paths and patterns
# to reference when writing success checks, avoiding hallucinated test paths.


async def _run_phase_4a(
    repo_root: str,
    affected_files: list[str],
) -> str:
    """Perform deterministic grep-based test discovery for affected files.

    Searches the repository for test files related to each affected file
    using pattern-based grep. Returns a formatted string summarizing
    discovered test files and patterns, or a sentinel if no tests found.
    This output is injected into the Phase 4 prompt so the LLM can write
    concrete success checks referencing real test infrastructure.

    Args:
        repo_root: Path to the repository root.
        affected_files: List of file paths affected by the plan.

    Returns:
        Formatted test inventory string for Phase 4 prompt injection.
    """
    import subprocess

    results: list[str] = []
    for filepath in affected_files:
        basename = (
            filepath.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            if "." in filepath
            else filepath.rsplit("/", 1)[-1]
        )
        # Search for test files referencing this source file
        try:
            proc = subprocess.run(
                [
                    "grep",
                    "-rl",
                    "--include=*.py",
                    "--include=*.ts",
                    "--include=*.js",
                    "--include=*.test.*",
                    "--include=*.spec.*",
                    "--include=*_test.*",
                    "--include=*test_*",
                    basename,
                    str(Path(repo_root)),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                test_files = [f.strip() for f in proc.stdout.strip().split("\n") if f.strip()]
                results.append(f"- {filepath} → tests: {', '.join(test_files[:5])}")
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            # grep not available or timed out — skip this file
            pass

    if not results:
        return "(no existing test files discovered via grep — use test_command for verification)"
    return "\n".join(results)


def _format_test_inventory_for_phase4(
    test_discovery: str,
    testing_inventory: str,
) -> str:
    """Combine Phase 4a test discovery with Phase 2 testing inventory.

    Merges the deterministic grep results from Phase 4a with the
    structured testing inventory from Phase 2 to produce a single
    test inventory block for the Phase 4 assembly prompt.

    Args:
        test_discovery: Output from _run_phase_4a grep-based discovery.
        testing_inventory: Output from _format_testing_inventory.

    Returns:
        Combined test inventory string for Phase 4 prompt.
    """
    no_tests_found = (
        "(no existing test files discovered via grep — use test_command "
        "for verification)"
    )
    parts: list[str] = []
    if test_discovery and test_discovery != no_tests_found:
        parts.append("## Discovered Test Files (Phase 4a grep-based discovery)")
        parts.append(test_discovery)
    if testing_inventory:
        parts.append("## Testing Inventory (Phase 2)")
        parts.append(testing_inventory)
    if not parts:
        return "(no test infrastructure detected)"
    return "\n\n".join(parts)


def _render_tdd_test_plan_for_phase4(verification: VerificationPlan) -> str:
    """Render authored TDD test steps for the final Phase 4 assembly prompt."""
    if not verification.steps:
        return ""
    lines = ["PLANNED TDD TEST STEPS (Phase 4b):"]
    for step in verification.steps:
        target = step.file_path or "(no file path provided)"
        lines.append(f"- Step {step.step_number}: {target}")
        if step.instruction:
            lines.append(f"  instruction: {step.instruction}")
        if step.reason:
            lines.append(f"  reason: {step.reason}")
    return "\n".join(lines) + "\n\n"


def _attach_tdd_contract(
    plan: ExecutionPlan,
    tdd_test_steps: list[PlanStep],
) -> None:
    """Attach TDD test steps to a plan and normalize numbering."""
    normalized_test_steps = list(tdd_test_steps)
    for i, step in enumerate(normalized_test_steps, 1):
        step.step_number = i
    offset = len(normalized_test_steps)
    for i, step in enumerate(plan.steps, offset + 1):
        step.step_number = i
    plan.tdd_mode = bool(normalized_test_steps)
    plan.tdd_test_steps = normalized_test_steps
    _sync_affected_files_from_steps(plan)


def _strip_non_implementation_steps(plan: ExecutionPlan) -> None:
    """Keep only implementation-compatible steps in ``plan.steps``."""
    impl_steps = [s for s in plan.steps if s.tool in IMPLEMENTATION_STEP_TOOLS]
    stripped_count = len(plan.steps) - len(impl_steps)
    if stripped_count:
        stripped_tools = [s.tool for s in plan.steps if s.tool not in IMPLEMENTATION_STEP_TOOLS]
        logger.warning(
            "Stripped %d non-implementation steps from Phase 4 plan: %s",
            stripped_count,
            stripped_tools,
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

    has_implementation = any(s.tool in ("create_file", "edit_file") for s in plan.steps)
    if plan.steps and not has_implementation:
        logger.warning(
            "Phase 4 plan has %d steps but none are create_file or "
            "edit_file — plan may be exploration-only. Tools: %s",
            len(plan.steps),
            [s.tool for s in plan.steps],
        )


async def _assemble_phase4_plan(
    *,
    task: str,
    design_and_risks: str,
    file_summary: str,
    project_context: str,
    scope: str,
    missing_files: str,
    test_command: str,
    testing_inventory: str,
    verification_targets: str,
    security_concerns: str,
    core_functionality: str,
    dependency_order: str,
    naming_conventions: str,
    risk_assessment: str,
    tdd_guidance: str,
    planned_tdd_tests: str,
    expert: "LLMClient",
    plan_assembly_max_tokens: int,
    ws: WorkflowSession | None,
    on_thinking: Callable | None,
    on_metrics: Callable | None,
    on_metrics_reset: Callable | None,
    prompt_scope=None,
    stage_summary: str,
    done_prefix: str,
    artifact_label: str,
) -> tuple[ExecutionPlan, float]:
    """Run one Phase 4 assembly pass and strip non-implementation steps."""
    await _send_stage(
        ws,
        stage_summary,
        model=expert.model_name,
        phase=4,
    )
    logger.info("Planning Phase 4 assembly pass: %s", artifact_label)
    t0 = time.monotonic()

    plan = await _chat_structured_with_repair(
        messages=[
            {
                "role": "system",
                "content": resolve_prompt_text("planning.assembly_system", scope=prompt_scope),
            },
            {
                "role": "user",
                "content": registry.get_text("planning.assembly_user").format(
                    task=task,
                    design_and_risks=design_and_risks,
                    file_summary=file_summary,
                    project_context=(
                        f"PROJECT CONTEXT:\n{project_context}\n\n" if project_context else ""
                    ),
                    scope=scope,
                    missing_files=missing_files,
                    test_command=test_command or "(none configured yet)",
                    testing_inventory=testing_inventory,
                    verification_targets=(
                        verification_targets
                        or "(derive from affected behavioral files)"
                    ),
                    security_concerns=security_concerns or "(none identified by Phase 3)",
                    core_functionality=core_functionality,
                    dependency_order=dependency_order,
                    naming_conventions=naming_conventions,
                    risk_assessment=risk_assessment,
                    tdd_guidance=tdd_guidance,
                    planned_tdd_tests=planned_tdd_tests,
                ),
            },
        ],
        schema=ExecutionPlan,
        expert=expert,
        max_tokens=plan_assembly_max_tokens,
        artifact_label=artifact_label,
        ws=ws,
        phase=4,
        on_thinking=on_thinking,
        on_metrics=on_metrics,
        on_metrics_reset=on_metrics_reset,
    )

    elapsed = time.monotonic() - t0
    _strip_non_implementation_steps(plan)
    _sync_affected_files_from_steps(plan)
    await _send_stage_done(
        ws,
        f"{done_prefix} — {len(plan.steps)} steps across {len(plan.affected_files)} file(s)",
        model=expert.model_name,
        phase=4,
    )
    return plan, elapsed


async def _run_phase_4b_tdd_test_design(
    *,
    draft_plan: ExecutionPlan,
    task: str,
    testing_inventory: str,
    verification_targets: str,
    security_concerns: str,
    core_functionality: str,
    dependency_order: str,
    naming_conventions: str,
    risk_assessment: str,
    expert: "LLMClient",
    plan_assembly_max_tokens: int,
    ws: WorkflowSession | None,
    on_thinking: Callable | None,
    on_metrics: Callable | None,
    on_metrics_reset: Callable | None,
    prompt_scope,
) -> tuple[VerificationPlan, float]:
    """Run the active Phase 4b TDD test-design pass."""
    await _send_stage(
        ws,
        "Phase 4b: Designing TDD test steps...",
        model=expert.model_name,
        phase=4,
    )
    logger.info("Planning Phase 4b: TDD test design")
    t0 = time.monotonic()

    verification = await _chat_structured_with_repair(
        messages=[
            {
                "role": "system",
                "content": resolve_prompt_text("planning.verification_system", scope=prompt_scope),
            },
            {
                "role": "user",
                "content": registry.format_text(
                    "planning.verification_user_tdd",
                    task=task,
                    impl_plan_md=plan_to_markdown(draft_plan),
                    testing_inventory=testing_inventory,
                    verification_targets=verification_targets,
                    security_concerns=security_concerns,
                    core_functionality=core_functionality,
                    next_step=str(1),
                    dependency_order=dependency_order,
                    naming_conventions=naming_conventions,
                    risk_assessment=risk_assessment,
                ),
            },
        ],
        schema=VerificationPlan,
        expert=expert,
        max_tokens=plan_assembly_max_tokens,
        artifact_label="phase 4b TDD test plan",
        ws=ws,
        phase=4,
        on_thinking=on_thinking,
        on_metrics=on_metrics,
        on_metrics_reset=on_metrics_reset,
    )

    for i, step in enumerate(verification.steps, 1):
        step.step_number = i
    elapsed = time.monotonic() - t0
    await _send_stage_done(
        ws,
        f"TDD test steps designed — {len(verification.steps)} step(s)",
        model=expert.model_name,
        phase=4,
    )
    return verification, elapsed


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
        f"- **[{r.severity}]** {r.risk} — mitigation: {r.mitigation}" for r in dar.critical_risks
    )


def _format_testing_inventory(file_summary: FileSummary | None) -> str:
    """Render ``FileSummary.testing_inventory`` (Layer 6) for TDD planning.

    Returns a concise markdown block with framework, directory,
    assertion style, existing regression files, and per-affected-file
    coverage. Returns the ``(none)`` sentinel when Phase 2 did not
    populate the field so the prompt reads cleanly.

    Phase 4 and the legacy Phase 5 helper both consume this formatter.
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
    if inv.strategy_summary:
        lines.append(f"- Strategy summary: {inv.strategy_summary}")
    if inv.assertion_style_excerpt:
        lines.append("- Assertion style excerpt:\n```\n" + inv.assertion_style_excerpt + "\n```")
    if inv.existing_regression_files:
        lines.append("- Existing regression files (MUST NOT be modified):")
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


def _format_core_functionality(source: "ExecutionPlan | DesignAndRisks") -> str:
    """Render core-functionality tags for planning prompts.

    Returns the ``(none)`` sentinel when no tags were detected or the
    feature flag is disabled.
    """
    tags = getattr(source, "core_functionality", []) or []
    if not tags:
        return "(none tagged — no mandatory regression tests required by Phase 3.)"
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
        warnings.append(f"test step path outside test convention: {step.file_path}")
    for w in warnings:
        logger.warning("Phase 5 plan validation — %s", w)
    return warnings


# Layer 2 — files that would benefit from a test. We only expand
# coverage checks to files with executable extensions. Docs / config /
# lockfiles / generated assets are skipped.
_EXECUTABLE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".pyi",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".kts",
        ".cs",
        ".fs",
        ".vb",
        ".rb",
        ".php",
        ".swift",
        ".m",
        ".mm",
        ".cpp",
        ".cxx",
        ".cc",
        ".c",
        ".h",
        ".hpp",
        ".hh",
        ".ex",
        ".exs",
        ".erl",
        ".hrl",
        ".scala",
        ".clj",
        ".cljs",
        ".lua",
        ".dart",
        ".ml",
        ".mli",
        ".hs",
        ".r",
        ".nim",
        ".zig",
        ".v",
        ".d",
    }
)


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

    enforced_tags = [t for t in tags if confidence_rank.get(t.confidence, 1) >= min_rank]
    if not enforced_tags:
        return []

    # Build haystack of regression-convention test steps only.
    haystacks: list[tuple[str, str]] = []
    for step in verification.steps:
        if step.tool != "create_file" or not step.file_path:
            continue
        if not is_regression_test_path(step.file_path):
            continue
        haystack = _step_contract_haystack(step)
        haystacks.append((step.file_path, haystack))

    warnings: list[str] = []
    for tag in enforced_tags:
        entity = tag.entity.strip()
        file_path = tag.file_path.strip()
        covered = any(
            (entity and entity in hay) or (file_path and file_path in hay) for _, hay in haystacks
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
    step in ``verification.steps`` references that path anywhere in its
    structured job contract. Uncovered paths append a warning to
    ``plan.plan_validation_warnings``.

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
    # reference. Any code path that appears anywhere in this structured
    # contract text is considered covered.
    haystacks: list[str] = []
    for step in verification.steps:
        if step.tool != "create_file":
            continue
        haystacks.append(_step_contract_haystack(step))
    combined = "\n".join(haystacks)

    warnings: list[str] = []
    for code_path in sorted(code_paths):
        # Treat the bare filename as a coarse match too, so a test step
        # that says "test the foo() function from foo.py" counts.
        filename = code_path.rsplit("/", 1)[-1]
        if code_path in combined or filename in combined:
            continue
        warnings.append(f"affected file has no test coverage: {code_path}")

    for w in warnings:
        logger.warning("Phase 5 coverage — %s", w)
    return warnings

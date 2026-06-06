"""Unit tests for planner phase nodes (ScopePhaseNode, ExplorationPhaseNode,
DesignPhaseNode, AssemblyPhaseNode).

Covers:
  1. Each phase node executes correctly and stores results in state.
  2. Phase nodes return Continue on success and Fail on exceptions.
  3. Prompts / node_ids are correctly set on LLMNode subclasses.
  4. ToolContext is properly initialised in ExplorationPhaseNode.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lean_ai.llm.plan_schema import (
    DesignAndRisks,
    ExecutionPlan,
    FileObservation,
    FileSummary,
    PlanStep,
    ScopeDocument,
)
from lean_ai.llm.planner import (
    AssemblyPhaseNode,
    DesignPhaseNode,
    ExplorationPhaseNode,
    ScopePhaseNode,
    create_plan,
)
from lean_ai.workflow.graph import Continue, Fail, NodeResult, WorkflowEngine
from lean_ai.workflow.state import WorkflowState


# ── Helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _disable_role_tuning(monkeypatch):
    monkeypatch.setattr("lean_ai.llm.planner.ensure_primary_role_tuning", AsyncMock(return_value=None))
    monkeypatch.setattr("lean_ai.llm.planner.ensure_expert_role_tuning", AsyncMock(return_value=None))


def _make_state(session_id: str = "test-sess") -> WorkflowState:
    """Create a fresh WorkflowState pre-populated with planning metadata."""
    state = WorkflowState.from_scratch(session_id)
    state.session_metadata["task"] = "Add a new API endpoint"
    state.session_metadata["context"] = "Project context here"
    state.session_metadata["repo_root"] = str(Path.cwd())
    state.session_metadata["session_id"] = session_id
    return state


def _make_scope_document() -> ScopeDocument:
    """Return a minimal valid ScopeDocument for test fixtures."""
    return ScopeDocument(
        problem="Need a new endpoint.",
        deliverables=["New endpoint"],
        in_scope=["src/api.py"],
        out_of_scope=["database changes"],
        downstream_consumers=["tests/"],
        assumptions=[],
        success_criteria=["Endpoint returns 200"],
        risks=["Breaking change"],
    )


def _make_file_summary() -> FileSummary:
    """Return a minimal valid FileSummary for test fixtures."""
    return FileSummary()


def _make_rich_file_summary() -> FileSummary:
    """Return a FileSummary with all primary file buckets populated."""
    return FileSummary(
        files_to_modify=[
            FileObservation(
                file_path="src/api.py",
                role="modify",
                reason="Add the new endpoint here.",
                relevant_sections="L10-L40",
                key_snippets=["def existing_endpoint(): ..."],
            )
        ],
        files_to_create=[
            FileObservation(
                file_path="tests/test_api.py",
                role="create",
                reason="Regression coverage for the new endpoint.",
            )
        ],
        files_read_for_context=[
            FileObservation(
                file_path="src/routes.py",
                role="reference",
                reason="Shows route registration pattern.",
            )
        ],
    )


def _make_design_and_risks() -> DesignAndRisks:
    """Return a minimal valid DesignAndRisks for test fixtures."""
    return DesignAndRisks(
        naming_conventions=[],
        change_designs=[],
        missing_files=[],
        dependency_order=[],
        critical_risks=[],
        citations=[],
    )


def _make_execution_plan() -> ExecutionPlan:
    """Return a minimal valid ExecutionPlan for test fixtures."""
    return ExecutionPlan(
        scope="Add endpoint.",
        steps=[
            PlanStep(
                step_number=1,
                tool="edit_file",
                file_path="src/api.py",
                instruction="Add the endpoint.",
                reason="Implement the feature.",
            )
        ],
        affected_files=["src/api.py"],
        test_strategy="Run tests.",
    )


class MockLLMClient:
    """Minimal LLMClient mock that returns controlled responses."""

    def __init__(self, model_name: str = "test-model") -> None:
        self.model_name = model_name
        self.provider = "test"

    async def chat_with_tools(self, *args, **kwargs):
        """Return a dummy tool call list and prose response."""
        return [], "Exploration prose response"

    async def chat_structured(self, *args, **kwargs):
        """Return a dummy structured response."""
        return _make_scope_document()


class DummyStateManager:
    """Tiny StateManager stand-in for graph-level planning tests."""

    def __init__(self, session_id: str = "sess-graph") -> None:
        self.session_id = session_id
        self.state = _make_state(session_id)
        self.save_count = 0

    async def get_state_async(self) -> WorkflowState:
        return self.state

    def save(self) -> None:
        self.save_count += 1


# ── 1. ScopePhaseNode ───────────────────────────────────────────────────────


class TestScopePhaseNode:
    """Tests for ScopePhaseNode — Phase 1 scope analysis."""

    def test_scope_phase_node_sets_correct_node_id(self) -> None:
        """ScopePhaseNode initialises with node_id 'scope_phase'."""
        client = MockLLMClient()
        node = ScopePhaseNode(client)
        assert node.node_id == "scope_phase"

    def test_scope_phase_node_stores_llm_client(self) -> None:
        """ScopePhaseNode stores the LLM client for later use."""
        client = MockLLMClient("my-model")
        node = ScopePhaseNode(client)
        assert node._llm_client is client

    @pytest.mark.asyncio
    async def test_scope_phase_node_returns_continue_on_success(self) -> None:
        """ScopePhaseNode.execute returns Continue with scope payload."""
        client = MockLLMClient()
        state = _make_state()

        with (
            patch.object(client, "chat_with_tools", new_callable=AsyncMock) as mock_tools,
            patch("lean_ai.llm.planner._synthesize_scope", new_callable=AsyncMock) as mock_syn,
            patch("lean_ai.llm.planner._send_stage", new_callable=AsyncMock),
            patch("lean_ai.llm.planner._send_stage_done", new_callable=AsyncMock),
            patch("lean_ai.llm.planner._save_debug_phase", new_callable=MagicMock),
            patch("lean_ai.training.span_context.trace_span") as mock_span,
        ):
            mock_tools.return_value = ([], "scope prose")
            scope_doc = _make_scope_document()
            mock_syn.return_value = (scope_doc, scope_doc.to_markdown(), True)

            # trace_span is an async context manager
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=None)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_span.return_value = mock_cm

            node = ScopePhaseNode(client)
            result = await node.execute(state)

        assert isinstance(result, Continue)
        assert "scope" in result.payload
        assert state.session_metadata["scope_obj"] is scope_doc

    @pytest.mark.asyncio
    async def test_scope_phase_node_returns_fail_on_exception(self) -> None:
        """ScopePhaseNode.execute returns Fail when the phase raises."""
        client = MockLLMClient()
        state = _make_state()

        with (
            patch.object(client, "chat_with_tools", new_callable=AsyncMock) as mock_tools,
            patch("lean_ai.llm.planner._send_stage", new_callable=AsyncMock),
            patch("lean_ai.training.span_context.trace_span") as mock_span,
        ):
            mock_tools.side_effect = RuntimeError("LLM unavailable")
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=None)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_span.return_value = mock_cm

            node = ScopePhaseNode(client)
            result = await node.execute(state)

        assert isinstance(result, Fail)
        assert "Scope phase failed" in result.error

    @pytest.mark.asyncio
    async def test_scope_phase_node_recovers_from_empty_scope_prose(self) -> None:
        """Phase 1a still receives a useful handoff when the tool loop is empty."""
        client = MockLLMClient()
        state = _make_state()

        with (
            patch.object(client, "chat_with_tools", new_callable=AsyncMock) as mock_tools,
            patch("lean_ai.llm.planner._synthesize_scope", new_callable=AsyncMock) as mock_syn,
            patch("lean_ai.llm.planner._send_stage", new_callable=AsyncMock),
            patch("lean_ai.llm.planner._send_stage_done", new_callable=AsyncMock),
            patch("lean_ai.llm.planner._save_debug_phase", new_callable=MagicMock),
            patch("lean_ai.training.span_context.trace_span") as mock_span,
        ):
            mock_tools.return_value = ([], "")
            scope_doc = _make_scope_document()
            mock_syn.return_value = (scope_doc, scope_doc.to_markdown(), True)

            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=None)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_span.return_value = mock_cm

            node = ScopePhaseNode(client)
            result = await node.execute(state)

        assert isinstance(result, Continue)
        exploration_prose = mock_syn.await_args.kwargs["exploration_prose"]
        assert "no additional verified findings" in exploration_prose
        assert state.session_metadata["scope_obj"] is scope_doc

    @pytest.mark.asyncio
    async def test_scope_phase_node_stores_scope_in_state(self) -> None:
        """ScopePhaseNode stores scope_obj and scope markdown in session_metadata."""
        client = MockLLMClient()
        state = _make_state()

        with (
            patch.object(client, "chat_with_tools", new_callable=AsyncMock) as mock_tools,
            patch("lean_ai.llm.planner._synthesize_scope", new_callable=AsyncMock) as mock_syn,
            patch("lean_ai.llm.planner._send_stage", new_callable=AsyncMock),
            patch("lean_ai.llm.planner._send_stage_done", new_callable=AsyncMock),
            patch("lean_ai.llm.planner._save_debug_phase", new_callable=MagicMock),
            patch("lean_ai.training.span_context.trace_span") as mock_span,
        ):
            mock_tools.return_value = ([], "scope prose")
            scope_doc = _make_scope_document()
            mock_syn.return_value = (scope_doc, scope_doc.to_markdown(), True)

            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=None)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_span.return_value = mock_cm

            node = ScopePhaseNode(client)
            await node.execute(state)

        assert "scope_obj" in state.session_metadata
        assert "scope" in state.session_metadata


# ── 2. ExplorationPhaseNode ─────────────────────────────────────────────────


class TestExplorationPhaseNode:
    """Tests for ExplorationPhaseNode — Phase 2 codebase exploration."""

    def test_exploration_phase_node_sets_correct_node_id(self) -> None:
        """ExplorationPhaseNode initialises with node_id 'exploration_phase'."""
        client = MockLLMClient()
        node = ExplorationPhaseNode(client)
        assert node.node_id == "exploration_phase"

    def test_exploration_phase_node_stores_llm_client(self) -> None:
        """ExplorationPhaseNode stores the LLM client for later use."""
        client = MockLLMClient("explorer-model")
        node = ExplorationPhaseNode(client)
        assert node._llm_client is client

    @pytest.mark.asyncio
    async def test_exploration_phase_node_returns_continue_on_success(self) -> None:
        """ExplorationPhaseNode.execute returns Continue with file_summary payload."""
        client = MockLLMClient()
        state = _make_state()

        with (
            patch("lean_ai.llm.planner.run_phase2_exploration", new_callable=AsyncMock) as mock_exp,
            patch("lean_ai.llm.planner._send_stage", new_callable=AsyncMock),
            patch("lean_ai.llm.planner._send_stage_done", new_callable=AsyncMock),
            patch("lean_ai.llm.planner._save_debug_phase", new_callable=MagicMock),
            patch("lean_ai.training.span_context.trace_span") as mock_span,
        ):
            file_summary = _make_file_summary()
            mock_exp.return_value = (file_summary, "file identification text", 1.0)

            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=None)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_span.return_value = mock_cm

            node = ExplorationPhaseNode(client, state_manager=MagicMock())
            result = await node.execute(state)

        assert isinstance(result, Continue)
        assert "file_summary" in result.payload
        assert state.session_metadata["file_summary_obj"] is file_summary

    @pytest.mark.asyncio
    async def test_exploration_phase_node_returns_fail_on_exception(self) -> None:
        """ExplorationPhaseNode.execute returns Fail when the phase raises."""
        client = MockLLMClient()
        state = _make_state()

        with (
            patch("lean_ai.llm.planner.run_phase2_exploration", new_callable=AsyncMock) as mock_exp,
            patch("lean_ai.llm.planner._send_stage", new_callable=AsyncMock),
            patch("lean_ai.training.span_context.trace_span") as mock_span,
        ):
            mock_exp.side_effect = RuntimeError("Exploration failed")
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=None)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_span.return_value = mock_cm

            node = ExplorationPhaseNode(client, state_manager=MagicMock())
            result = await node.execute(state)

        assert isinstance(result, Fail)
        assert "Exploration phase failed" in result.error

    @pytest.mark.asyncio
    async def test_exploration_phase_node_stores_file_summary_in_state(self) -> None:
        """ExplorationPhaseNode stores file_summary_obj and file_summary in state."""
        client = MockLLMClient()
        state = _make_state()

        with (
            patch("lean_ai.llm.planner.run_phase2_exploration", new_callable=AsyncMock) as mock_exp,
            patch("lean_ai.llm.planner._send_stage", new_callable=AsyncMock),
            patch("lean_ai.llm.planner._send_stage_done", new_callable=AsyncMock),
            patch("lean_ai.llm.planner._save_debug_phase", new_callable=MagicMock),
            patch("lean_ai.training.span_context.trace_span") as mock_span,
        ):
            file_summary = _make_file_summary()
            mock_exp.return_value = (file_summary, "file identification text", 1.0)

            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=None)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_span.return_value = mock_cm

            node = ExplorationPhaseNode(client, state_manager=MagicMock())
            await node.execute(state)

        assert "file_summary_obj" in state.session_metadata
        assert "file_summary" in state.session_metadata

    @pytest.mark.asyncio
    async def test_exploration_phase_node_passes_tool_context(self) -> None:
        """ExplorationPhaseNode passes correct parameters to run_phase2_exploration."""
        client = MockLLMClient()
        state = _make_state()
        state.session_metadata["task"] = "Build a feature"
        state.session_metadata["scope"] = "Scope markdown"
        state.session_metadata["repo_root"] = "/repo/path"
        state.session_metadata["session_id"] = "sess-123"
        state_manager = MagicMock()

        with (
            patch("lean_ai.llm.planner.run_phase2_exploration", new_callable=AsyncMock) as mock_exp,
            patch("lean_ai.llm.planner._send_stage", new_callable=AsyncMock),
            patch("lean_ai.llm.planner._send_stage_done", new_callable=AsyncMock),
            patch("lean_ai.llm.planner._save_debug_phase", new_callable=MagicMock),
            patch("lean_ai.training.span_context.trace_span") as mock_span,
        ):
            mock_exp.return_value = (_make_file_summary(), "text", 1.0)

            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=None)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_span.return_value = mock_cm

            node = ExplorationPhaseNode(client, state_manager=state_manager)
            await node.execute(state)

        # Verify the call passed the right task, scope, repo_root, session_id
        call_kwargs = mock_exp.call_args.kwargs
        assert call_kwargs["task"] == "Build a feature"
        assert call_kwargs["scope"] == "Scope markdown"
        assert call_kwargs["repo_root"] == "/repo/path"
        assert call_kwargs["session_id"] == "sess-123"
        assert call_kwargs["explorer"] is client
        assert call_kwargs["state_manager"] is state_manager


# ── 3. DesignPhaseNode ──────────────────────────────────────────────────────


class TestDesignPhaseNode:
    """Tests for DesignPhaseNode — Phase 3 design synthesis."""

    def test_design_phase_node_sets_correct_node_id(self) -> None:
        """DesignPhaseNode initialises with node_id 'design_phase'."""
        client = MockLLMClient()
        node = DesignPhaseNode(client)
        assert node.node_id == "design_phase"

    def test_design_phase_node_stores_llm_client(self) -> None:
        """DesignPhaseNode stores the LLM client for later use."""
        client = MockLLMClient("design-model")
        node = DesignPhaseNode(client)
        assert node._llm_client is client

    @pytest.mark.asyncio
    async def test_design_phase_node_returns_continue_on_success(self) -> None:
        """DesignPhaseNode.execute returns Continue with design_and_risks payload."""
        client = MockLLMClient()
        state = _make_state()
        state.session_metadata["scope"] = "Scope markdown"
        state.session_metadata["file_summary"] = "File summary markdown"

        with (
            patch.object(client, "chat_with_tools", new_callable=AsyncMock) as mock_tools,
            patch("lean_ai.llm.planner._synthesize_design_and_risks", new_callable=AsyncMock) as mock_syn,
            patch("lean_ai.llm.planner._send_stage", new_callable=AsyncMock),
            patch("lean_ai.llm.planner._send_stage_done", new_callable=AsyncMock),
            patch("lean_ai.llm.planner._save_debug_phase", new_callable=MagicMock),
            patch("lean_ai.llm.planner._format_design_and_risks", return_value="formatted"),
            patch("lean_ai.training.span_context.trace_span") as mock_span,
        ):
            mock_tools.return_value = ([], "design prose")
            dar = _make_design_and_risks()
            mock_syn.return_value = dar

            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=None)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_span.return_value = mock_cm

            node = DesignPhaseNode(client)
            result = await node.execute(state)

        assert isinstance(result, Continue)
        assert "design_and_risks" in result.payload
        assert state.session_metadata["design_and_risks_obj"] is dar

    @pytest.mark.asyncio
    async def test_design_phase_node_returns_fail_on_exception(self) -> None:
        """DesignPhaseNode.execute returns Fail when the phase raises."""
        client = MockLLMClient()
        state = _make_state()

        with (
            patch.object(client, "chat_with_tools", new_callable=AsyncMock) as mock_tools,
            patch("lean_ai.llm.planner._send_stage", new_callable=AsyncMock),
            patch("lean_ai.training.span_context.trace_span") as mock_span,
        ):
            mock_tools.side_effect = RuntimeError("Design LLM failed")
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=None)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_span.return_value = mock_cm

            node = DesignPhaseNode(client)
            result = await node.execute(state)

        assert isinstance(result, Fail)
        assert "Design phase failed" in result.error

    @pytest.mark.asyncio
    async def test_design_phase_node_stores_design_in_state(self) -> None:
        """DesignPhaseNode stores design_and_risks_obj in session_metadata."""
        client = MockLLMClient()
        state = _make_state()
        state.session_metadata["scope"] = "Scope"
        state.session_metadata["file_summary"] = "Summary"

        with (
            patch.object(client, "chat_with_tools", new_callable=AsyncMock) as mock_tools,
            patch("lean_ai.llm.planner._synthesize_design_and_risks", new_callable=AsyncMock) as mock_syn,
            patch("lean_ai.llm.planner._send_stage", new_callable=AsyncMock),
            patch("lean_ai.llm.planner._send_stage_done", new_callable=AsyncMock),
            patch("lean_ai.llm.planner._save_debug_phase", new_callable=MagicMock),
            patch("lean_ai.llm.planner._format_design_and_risks", return_value="formatted"),
            patch("lean_ai.training.span_context.trace_span") as mock_span,
        ):
            mock_tools.return_value = ([], "design prose")
            dar = _make_design_and_risks()
            mock_syn.return_value = dar

            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=None)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_span.return_value = mock_cm

            node = DesignPhaseNode(client)
            await node.execute(state)

        assert "design_and_risks_obj" in state.session_metadata


# ── 4. AssemblyPhaseNode ────────────────────────────────────────────────────


class TestAssemblyPhaseNode:
    """Tests for AssemblyPhaseNode — Phase 4 plan assembly."""

    def test_assembly_phase_node_sets_correct_node_id(self) -> None:
        """AssemblyPhaseNode initialises with node_id 'assembly_phase'."""
        client = MockLLMClient()
        node = AssemblyPhaseNode(client)
        assert node.node_id == "assembly_phase"

    def test_assembly_phase_node_stores_llm_client(self) -> None:
        """AssemblyPhaseNode stores the LLM client for later use."""
        client = MockLLMClient("assembly-model")
        node = AssemblyPhaseNode(client)
        assert node._llm_client is client

    @pytest.mark.asyncio
    async def test_assembly_phase_node_returns_continue_on_success(self) -> None:
        """AssemblyPhaseNode.execute returns Continue with plan payload."""
        client = MockLLMClient()
        state = _make_state()
        state.session_metadata["scope"] = "Scope"
        state.session_metadata["file_summary"] = "Summary"
        state.session_metadata["design_and_risks_obj"] = _make_design_and_risks()

        with (
            patch("lean_ai.llm.planner.AssemblyPhase", new_callable=MagicMock) as mock_phase_cls,
        ):
            mock_phase = mock_phase_cls.return_value
            mock_phase.execute = AsyncMock(return_value=_make_execution_plan())

            node = AssemblyPhaseNode(client)
            result = await node.execute(state)

        assert isinstance(result, Continue)
        assert "plan" in result.payload
        assert state.session_metadata["plan"] is not None

    @pytest.mark.asyncio
    async def test_assembly_phase_node_returns_fail_on_exception(self) -> None:
        """AssemblyPhaseNode.execute returns Fail when the phase raises."""
        client = MockLLMClient()
        state = _make_state()
        state.session_metadata["scope"] = "Scope"
        state.session_metadata["file_summary"] = "Summary"
        state.session_metadata["design_and_risks_obj"] = _make_design_and_risks()

        with (
            patch("lean_ai.llm.planner.AssemblyPhase", new_callable=MagicMock) as mock_phase_cls,
        ):
            mock_phase = mock_phase_cls.return_value
            mock_phase.execute = AsyncMock(side_effect=RuntimeError("Assembly failed"))

            node = AssemblyPhaseNode(client)
            result = await node.execute(state)

        assert isinstance(result, Fail)
        assert "Assembly phase failed" in result.error

    @pytest.mark.asyncio
    async def test_assembly_phase_node_stores_plan_in_state(self) -> None:
        """AssemblyPhaseNode stores the plan in session_metadata."""
        client = MockLLMClient()
        state = _make_state()
        state.session_metadata["scope"] = "Scope"
        state.session_metadata["file_summary"] = "Summary"
        state.session_metadata["design_and_risks_obj"] = _make_design_and_risks()

        with (
            patch("lean_ai.llm.planner.AssemblyPhase", new_callable=MagicMock) as mock_phase_cls,
        ):
            mock_phase = mock_phase_cls.return_value
            plan = _make_execution_plan()
            mock_phase.execute = AsyncMock(return_value=plan)

            node = AssemblyPhaseNode(client)
            await node.execute(state)

        assert state.session_metadata["plan"] is plan


# ── 5. Cross-cutting: prompts and LLMNode integration ───────────────────────


class TestPhaseNodePrompts:
    """Verify that phase nodes correctly configure their LLMNode base class."""

    def test_scope_phase_node_is_llm_node_subclass(self) -> None:
        """ScopePhaseNode inherits from LLMNode."""
        from lean_ai.workflow.graph import LLMNode

        client = MockLLMClient()
        node = ScopePhaseNode(client)
        assert isinstance(node, LLMNode)

    def test_design_phase_node_is_llm_node_subclass(self) -> None:
        """DesignPhaseNode inherits from LLMNode."""
        from lean_ai.workflow.graph import LLMNode

        client = MockLLMClient()
        node = DesignPhaseNode(client)
        assert isinstance(node, LLMNode)

    def test_assembly_phase_node_is_llm_node_subclass(self) -> None:
        """AssemblyPhaseNode inherits from LLMNode."""
        from lean_ai.workflow.graph import LLMNode

        client = MockLLMClient()
        node = AssemblyPhaseNode(client)
        assert isinstance(node, LLMNode)

    def test_exploration_phase_node_is_tool_node_subclass(self) -> None:
        """ExplorationPhaseNode inherits from ToolNode."""
        from lean_ai.workflow.graph import ToolNode

        client = MockLLMClient()
        node = ExplorationPhaseNode(client)
        assert isinstance(node, ToolNode)


# ── 6. Four-phase planning tree contracts ───────────────────────────────────


class TestPlanningTreeContracts:
    """High-signal contract tests for all four planning tree phases.

    These tests intentionally patch the concrete phase implementations at the
    graph-node boundary. They verify the planning tree's durable contracts:
    each phase receives the upstream metadata it depends on, writes the
    downstream metadata the next phase consumes, and forwards callbacks/model
    dependencies without requiring live LLM calls.
    """

    @pytest.mark.asyncio
    async def test_phase1_scope_node_forwards_inputs_callbacks_and_writes_scope(self) -> None:
        client = MockLLMClient("scope-model")
        state = _make_state("sess-phase1")
        state.session_metadata.update(
            {
                "task": "Add endpoint",
                "context": "Context block",
                "repo_root": "/repo/root",
                "session_id": "sess-phase1",
            }
        )
        callbacks = {
            "on_content": MagicMock(name="on_content"),
            "on_thinking": MagicMock(name="on_thinking"),
            "on_tool_call": MagicMock(name="on_tool_call"),
            "on_tool_result": MagicMock(name="on_tool_result"),
            "on_metrics": MagicMock(name="on_metrics"),
            "on_metrics_reset": MagicMock(name="on_metrics_reset"),
        }
        scope_doc = _make_scope_document()

        with patch("lean_ai.llm.planner.ScopePhase", new_callable=MagicMock) as phase_cls:
            phase = phase_cls.return_value
            phase.execute = AsyncMock(return_value=scope_doc)

            node = ScopePhaseNode(client, ws="ws", dispatcher="dispatcher", **callbacks)
            result = await node.execute(state)

        assert isinstance(result, Continue)
        assert result.payload["scope"] == scope_doc.to_markdown()
        assert state.session_metadata["scope_obj"] is scope_doc
        assert state.session_metadata["scope"] == scope_doc.to_markdown()
        phase.execute.assert_awaited_once()
        call = phase.execute.call_args.kwargs
        assert call["task"] == "Add endpoint"
        assert call["llm_client"] is client
        assert call["ws"] == "ws"
        assert call["dispatcher"] == "dispatcher"
        assert call["context"] == "Context block"
        assert call["repo_root"] == "/repo/root"
        assert call["session_id"] == "sess-phase1"
        for name, cb in callbacks.items():
            assert call[name] is cb

    @pytest.mark.asyncio
    async def test_phase2_exploration_node_requires_scope_and_writes_file_summary(self) -> None:
        client = MockLLMClient("explorer-model")
        state = _make_state("sess-phase2")
        state.session_metadata.update(
            {
                "task": "Add endpoint",
                "scope": "Scope markdown from Phase 1",
                "context": "Context block",
                "repo_root": "/repo/root",
                "session_id": "sess-phase2",
            }
        )
        state_manager = DummyStateManager("sess-phase2")
        refiner = object()
        file_summary = _make_rich_file_summary()

        with patch("lean_ai.llm.planner.ExplorationPhase", new_callable=MagicMock) as phase_cls:
            phase = phase_cls.return_value
            phase.execute = AsyncMock(return_value=file_summary)

            node = ExplorationPhaseNode(
                client,
                ws="ws",
                dispatcher="dispatcher",
                state_manager=state_manager,
                refiner=refiner,
            )
            result = await node.execute(state)

        assert isinstance(result, Continue)
        assert "FILES TO MODIFY" in result.payload["file_summary"]
        assert "src/api.py" in result.payload["file_summary"]
        assert state.session_metadata["file_summary_obj"] is file_summary
        assert state.session_metadata["file_summary"] == file_summary.to_markdown()
        phase.execute.assert_awaited_once()
        call = phase.execute.call_args.kwargs
        assert call["task"] == "Add endpoint"
        assert call["scope"] == "Scope markdown from Phase 1"
        assert call["llm_client"] is client
        assert call["state_manager"] is state_manager
        assert call["refiner"] is refiner
        assert call["repo_root"] == "/repo/root"
        assert call["session_id"] == "sess-phase2"

    @pytest.mark.asyncio
    async def test_phase3_design_node_requires_phase1_and_phase2_outputs(self) -> None:
        client = MockLLMClient("design-model")
        state = _make_state("sess-phase3")
        state.session_metadata.update(
            {
                "task": "Add endpoint",
                "scope": "Scope markdown from Phase 1",
                "file_summary": "File summary markdown from Phase 2",
                "context": "Context block",
                "repo_root": "/repo/root",
                "session_id": "sess-phase3",
            }
        )
        design = _make_design_and_risks()

        with patch("lean_ai.llm.planner.DesignPhase", new_callable=MagicMock) as phase_cls:
            phase = phase_cls.return_value
            phase.execute = AsyncMock(return_value=design)

            node = DesignPhaseNode(client, ws="ws", dispatcher="dispatcher")
            result = await node.execute(state)

        assert isinstance(result, Continue)
        assert result.payload["design_and_risks"] is design
        assert state.session_metadata["design_and_risks_obj"] is design
        phase.execute.assert_awaited_once()
        call = phase.execute.call_args.kwargs
        assert call["task"] == "Add endpoint"
        assert call["scope"] == "Scope markdown from Phase 1"
        assert call["file_summary"] == "File summary markdown from Phase 2"
        assert call["llm_client"] is client
        assert call["context"] == "Context block"
        assert call["repo_root"] == "/repo/root"
        assert call["session_id"] == "sess-phase3"

    @pytest.mark.asyncio
    async def test_phase4_assembly_node_requires_all_prior_phase_outputs(self) -> None:
        client = MockLLMClient("assembly-model")
        state = _make_state("sess-phase4")
        file_summary = _make_rich_file_summary()
        design = _make_design_and_risks()
        state.session_metadata.update(
            {
                "task": "Add endpoint",
                "scope": "Scope markdown from Phase 1",
                "file_summary": file_summary.to_markdown(),
                "file_summary_obj": file_summary,
                "design_and_risks_obj": design,
                "context": "Context block",
                "repo_root": "/repo/root",
                "session_id": "sess-phase4",
            }
        )
        plan = _make_execution_plan()
        refiner = object()
        expert = MockLLMClient("expert-model")

        with patch("lean_ai.llm.planner.AssemblyPhase", new_callable=MagicMock) as phase_cls:
            phase = phase_cls.return_value
            phase.execute = AsyncMock(return_value=plan)

            node = AssemblyPhaseNode(
                client,
                ws="ws",
                dispatcher="dispatcher",
                refiner=refiner,
                test_command="pytest",
                expert_llm_client=expert,
            )
            result = await node.execute(state)

        assert isinstance(result, Continue)
        assert result.payload["plan"] is plan
        assert state.session_metadata["plan"] is plan
        phase.execute.assert_awaited_once()
        call = phase.execute.call_args.kwargs
        assert call["task"] == "Add endpoint"
        assert call["scope"] == "Scope markdown from Phase 1"
        assert call["file_summary"] == file_summary.to_markdown()
        assert call["design_and_risks"] is design
        assert call["file_summary_obj"] is file_summary
        assert call["test_command"] == "pytest"
        assert call["refiner"] is refiner
        assert call["expert_llm_client"] is expert
        assert call["llm_client"] is client

    @pytest.mark.asyncio
    async def test_create_plan_runs_four_phase_tree_in_order_and_persists_handoffs(
        self,
        monkeypatch,
    ) -> None:
        primary = MockLLMClient("primary-model")
        expert = MockLLMClient("expert-model")
        state_manager = DummyStateManager("sess-tree")
        state_manager.state.session_metadata.clear()
        order: list[str] = []
        scope_doc = _make_scope_document()
        file_summary = _make_rich_file_summary()
        design = _make_design_and_risks()
        plan = _make_execution_plan()

        class FakeScopePhase:
            async def execute(self, **kwargs):
                order.append("phase1")
                assert kwargs["task"] == "Add endpoint"
                assert kwargs["llm_client"] is primary
                assert kwargs["context"] == "Project context"
                assert kwargs["repo_root"] == "/repo/root"
                assert kwargs["session_id"] == "sess-tree"
                return scope_doc

        class FakeExplorationPhase:
            async def execute(self, **kwargs):
                order.append("phase2")
                assert kwargs["scope"] == scope_doc.to_markdown()
                assert kwargs["llm_client"] is primary
                assert kwargs["state_manager"] is state_manager
                return file_summary

        class FakeDesignPhase:
            async def execute(self, **kwargs):
                order.append("phase3")
                assert kwargs["scope"] == scope_doc.to_markdown()
                assert kwargs["file_summary"] == file_summary.to_markdown()
                assert kwargs["llm_client"] is expert
                return design

        class FakeAssemblyPhase:
            async def execute(self, **kwargs):
                order.append("phase4")
                assert kwargs["scope"] == scope_doc.to_markdown()
                assert kwargs["file_summary"] == file_summary.to_markdown()
                assert kwargs["file_summary_obj"] is file_summary
                assert kwargs["design_and_risks"] is design
                assert kwargs["llm_client"] is expert
                assert kwargs["expert_llm_client"] is expert
                assert kwargs["test_command"] == "pytest"
                return plan

        monkeypatch.setattr("lean_ai.llm.planner.ScopePhase", FakeScopePhase)
        monkeypatch.setattr("lean_ai.llm.planner.ExplorationPhase", FakeExplorationPhase)
        monkeypatch.setattr("lean_ai.llm.planner.DesignPhase", FakeDesignPhase)
        monkeypatch.setattr("lean_ai.llm.planner.AssemblyPhase", FakeAssemblyPhase)

        result = await create_plan(
            task="Add endpoint",
            repo_root="/repo/root",
            llm_client=primary,
            context="Project context",
            session_id="sess-tree",
            state_manager=state_manager,
            expert_llm_client=expert,
            test_command="pytest",
        )

        assert result is plan
        assert order == ["phase1", "phase2", "phase3", "phase4"]
        assert state_manager.state.session_metadata["task"] == "Add endpoint"
        assert state_manager.state.session_metadata["context"] == "Project context"
        assert state_manager.state.session_metadata["scope_obj"] is scope_doc
        assert state_manager.state.session_metadata["scope"] == scope_doc.to_markdown()
        assert state_manager.state.session_metadata["file_summary_obj"] is file_summary
        assert state_manager.state.session_metadata["file_summary"] == file_summary.to_markdown()
        assert state_manager.state.session_metadata["design_and_risks_obj"] is design
        assert state_manager.state.session_metadata["plan"] is plan
        assert state_manager.state.current_plan == plan.model_dump()
        # Four saves from WorkflowEngine, plus the final current_plan save.
        assert state_manager.save_count >= 5

    @pytest.mark.asyncio
    async def test_workflow_engine_stops_tree_on_phase_failure(self) -> None:
        state = _make_state("sess-fail-tree")
        order: list[str] = []

        class FirstPhase(ScopePhaseNode):
            async def execute(self, state):  # type: ignore[override]
                order.append("phase1")
                return Continue()

        class FailingSecondPhase(ExplorationPhaseNode):
            async def execute(self, state):  # type: ignore[override]
                order.append("phase2")
                return Fail(error="phase2 broke")

        class ThirdPhase(DesignPhaseNode):
            async def execute(self, state):  # type: ignore[override]
                order.append("phase3")
                return Continue()

        from lean_ai.workflow.graph import WorkflowGraph

        graph = WorkflowGraph()
        graph.add_node(FirstPhase(MockLLMClient()))
        graph.add_node(FailingSecondPhase(MockLLMClient()))
        graph.add_node(ThirdPhase(MockLLMClient()))

        result = await WorkflowEngine().run(graph, state=state)

        assert isinstance(result, Fail)
        assert result.error == "phase2 broke"
        assert order == ["phase1", "phase2"]

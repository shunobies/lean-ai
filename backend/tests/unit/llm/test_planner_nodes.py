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
    FileSummary,
    PlanStep,
    ScopeDocument,
)
from lean_ai.llm.planner import (
    AssemblyPhaseNode,
    DesignPhaseNode,
    ExplorationPhaseNode,
    ScopePhaseNode,
)
from lean_ai.workflow.graph import Continue, Fail, NodeResult
from lean_ai.workflow.state import WorkflowState


# ── Helpers ──────────────────────────────────────────────────────────────────


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
            patch("lean_ai.llm.planner._save_debug_phase", new_callable=AsyncMock),
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
    async def test_scope_phase_node_stores_scope_in_state(self) -> None:
        """ScopePhaseNode stores scope_obj and scope markdown in session_metadata."""
        client = MockLLMClient()
        state = _make_state()

        with (
            patch.object(client, "chat_with_tools", new_callable=AsyncMock) as mock_tools,
            patch("lean_ai.llm.planner._synthesize_scope", new_callable=AsyncMock) as mock_syn,
            patch("lean_ai.llm.planner._send_stage", new_callable=AsyncMock),
            patch("lean_ai.llm.planner._send_stage_done", new_callable=AsyncMock),
            patch("lean_ai.llm.planner._save_debug_phase", new_callable=AsyncMock),
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
            patch("lean_ai.llm.planner._save_debug_phase", new_callable=AsyncMock),
            patch("lean_ai.training.span_context.trace_span") as mock_span,
        ):
            file_summary = _make_file_summary()
            mock_exp.return_value = (file_summary, "file identification text", 1.0)

            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=None)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_span.return_value = mock_cm

            node = ExplorationPhaseNode(client)
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

            node = ExplorationPhaseNode(client)
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
            patch("lean_ai.llm.planner._save_debug_phase", new_callable=AsyncMock),
            patch("lean_ai.training.span_context.trace_span") as mock_span,
        ):
            file_summary = _make_file_summary()
            mock_exp.return_value = (file_summary, "file identification text", 1.0)

            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=None)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_span.return_value = mock_cm

            node = ExplorationPhaseNode(client)
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

        with (
            patch("lean_ai.llm.planner.run_phase2_exploration", new_callable=AsyncMock) as mock_exp,
            patch("lean_ai.llm.planner._send_stage", new_callable=AsyncMock),
            patch("lean_ai.llm.planner._send_stage_done", new_callable=AsyncMock),
            patch("lean_ai.llm.planner._save_debug_phase", new_callable=AsyncMock),
            patch("lean_ai.training.span_context.trace_span") as mock_span,
        ):
            mock_exp.return_value = (_make_file_summary(), "text", 1.0)

            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=None)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_span.return_value = mock_cm

            node = ExplorationPhaseNode(client)
            await node.execute(state)

        # Verify the call passed the right task, scope, repo_root, session_id
        call_kwargs = mock_exp.call_args.kwargs
        assert call_kwargs["task"] == "Build a feature"
        assert call_kwargs["scope"] == "Scope markdown"
        assert call_kwargs["repo_root"] == "/repo/path"
        assert call_kwargs["session_id"] == "sess-123"
        assert call_kwargs["explorer"] is client


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
            patch("lean_ai.llm.planner._save_debug_phase", new_callable=AsyncMock),
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
            patch("lean_ai.llm.planner._save_debug_phase", new_callable=AsyncMock),
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

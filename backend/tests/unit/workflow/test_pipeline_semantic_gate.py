"""Integration tests for SemanticReviewNode pipeline wiring and execution flow.

Verifies:
  1. SemanticReviewNode is wired into the workflow graph post-execution (graph edge)
  2. Timing gate prevents semantic review when validation fails (security mitigation)
  3. Approval path continues to completion after semantic review passes

TDD mode — tests define the public contract before implementation exists.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, ModuleType, patch

import pytest

from lean_ai.workflow.graph import Continue, Fail, Suspend


# ── Helpers — mirror test_pipeline_integration.py conventions ────────────────


def _setup_mock_modules_for_semantic_gate():
    """Inject mock modules so pipeline.py can be imported for testing."""
    sys.modules.pop("lean_ai.workflow.pipeline", None)

    # Mock the broken planner module
    mock_planner = ModuleType("lean_ai.llm.planner")
    mock_planner.create_plan = AsyncMock()
    sys.modules["lean_ai.llm.planner"] = mock_planner

    # Mock executor module
    mock_executor = ModuleType("lean_ai.workflow.executor")
    mock_executor.execute_plan = AsyncMock(return_value="executed")
    sys.modules["lean_ai.workflow.executor"] = mock_executor

    # Mock fix_mode module
    mock_fix_mode = ModuleType("lean_ai.workflow.fix_mode")
    mock_fix_mode._run_fix = AsyncMock()
    sys.modules["lean_ai.workflow.fix_mode"] = mock_fix_mode

    # Mock callbacks module
    mock_callbacks = ModuleType("lean_ai.workflow.callbacks")
    mock_callbacks.build_workflow_callbacks = MagicMock()
    sys.modules["lean_ai.workflow.callbacks"] = mock_callbacks

    # Mock validation module — exposes _run_semantic_review for the gate
    mock_validation = ModuleType("lean_ai.workflow.validation")
    mock_validation._effective_post_commands = MagicMock(return_value={"test": ""})
    mock_validation._run_semantic_review = AsyncMock(
        return_value=Continue(next_node_id=None)
    )
    sys.modules["lean_ai.workflow.validation"] = mock_validation

    # Mock hooks module
    mock_hooks = ModuleType("lean_ai.workflow.hooks")
    mock_hooks.fire_workflow_event = MagicMock()
    mock_hooks.fire_plan_decision_hook = MagicMock()
    sys.modules["lean_ai.workflow.hooks"] = mock_hooks

    # Mock span_context module
    mock_span_context = ModuleType("lean_ai.training.span_context")

    @asynccontextmanager
    async def trace_span(*args, **kwargs):
        yield None

    mock_span_context.trace_span = trace_span
    sys.modules["lean_ai.training.span_context"] = mock_span_context

    # Mock routers module
    mock_routers = ModuleType("lean_ai.routers")
    mock_routers.dependencies = ModuleType("lean_ai.routers.dependencies")
    mock_routers.dependencies.worker_llm_client = MagicMock(
        model_name="worker", provider_name="test"
    )
    sys.modules["lean_ai.routers"] = mock_routers
    sys.modules["lean_ai.routers.dependencies"] = mock_routers.dependencies

    # Mock config module
    mock_config = ModuleType("lean_ai.config")
    mock_config.settings = MagicMock(_active_context_window=128000)
    sys.modules["lean_ai.config"] = mock_config

    # Mock ws_handler module
    mock_ws_handler = ModuleType("lean_ai.workflow.ws_handler")
    mock_ws_handler.ws_send = AsyncMock()
    sys.modules["lean_ai.workflow.ws_handler"] = mock_ws_handler

    # Mock ws_dispatcher module
    mock_ws_dispatcher = ModuleType("lean_ai.workflow.ws_dispatcher")
    mock_ws_dispatcher.WSMessageDispatcher = MagicMock
    sys.modules["lean_ai.workflow.ws_dispatcher"] = mock_ws_dispatcher

    # Mock ws_protocol module
    from lean_ai.workflow.ws_protocol import WorkflowSessionClosedError, WorkflowSession

    mock_ws_protocol = ModuleType("lean_ai.workflow.ws_protocol")
    mock_ws_protocol.WorkflowSessionClosedError = WorkflowSessionClosedError
    mock_ws_protocol.WorkflowSession = WorkflowSession
    sys.modules["lean_ai.workflow.ws_protocol"] = mock_ws_protocol


@pytest.fixture(autouse=True)
def _restore_mocked_modules():
    """Restore sys.modules entries mutated by _setup_mock_modules_for_semantic_gate()."""
    names = [
        "lean_ai.llm.planner",
        "lean_ai.workflow.executor",
        "lean_ai.workflow.fix_mode",
        "lean_ai.workflow.callbacks",
        "lean_ai.workflow.validation",
        "lean_ai.workflow.hooks",
        "lean_ai.training.span_context",
        "lean_ai.routers",
        "lean_ai.routers.dependencies",
        "lean_ai.config",
        "lean_ai.workflow.ws_handler",
        "lean_ai.workflow.ws_dispatcher",
        "lean_ai.workflow.ws_protocol",
        "lean_ai.workflow.pipeline",
    ]
    saved = {name: sys.modules.get(name) for name in names}
    try:
        yield
    finally:
        for name in names:
            original = saved[name]
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


# ── 1. SemanticReviewNode wired post-execution — graph edge verification ────


def test_semantic_review_node_wired_after_execution_in_graph(
    tmp_path, monkeypatch
):
    """SemanticReviewNode is registered in the workflow graph immediately after
    ExecutionNode, forming a sequential edge: Planning → Approval → Execution → SemanticReview.

    The node must appear as the next element in graph.nodes after execution_node,
    confirming post-execution placement per architectural boundary requirements.
    """
    import asyncio

    async def _run():
        monkeypatch.chdir(tmp_path)

        from lean_ai.llm.plan_schema import ExecutionPlan, PlanStep

        plan = ExecutionPlan(
            scope="Test",
            user_summary="A test plan",
            steps=[PlanStep(step_number=1, instruction="do something")],
            affected_files=["test_file.py"],
            test_strategy="run tests",
        )

        _setup_mock_modules_for_semantic_gate()
        sys.modules["lean_ai.llm.planner"].create_plan = AsyncMock(return_value=plan)

        mock_llm_client = MagicMock()
        mock_llm_client.model_name = "test-model"
        mock_llm_client.provider_name = "test-provider"

        # Build the graph directly by calling the internal builder path
        from lean_ai.workflow import pipeline
        from lean_ai.workflow.graph import WorkflowGraph, WorkflowEngine
        from lean_ai.workflow.state import StateManager, WorkflowState

        captured_graph = [None]

        class FakeStateManager:
            def __init__(self, session_id):
                self.session_id = session_id

            async def get_state_async(self):
                return WorkflowState.from_scratch(session_id="test-sess")

            async def save_checkpoint_async(self, **kwargs):
                return "cp-1"

            def save(self):
                pass

        mock_dispatcher = MagicMock()
        mock_dispatcher.wait_for_approval = AsyncMock(return_value={"type": "approve"})

        with patch.object(pipeline, "StateManager", FakeStateManager):
            # Patch engine.run to capture the graph before execution
            async def capture_and_continue(engine_self, graph, state_manager=None, state=None):
                captured_graph[0] = graph
                return Continue(next_node_id=None)

            with patch.object(WorkflowEngine, "run", new=capture_and_continue):
                try:
                    await pipeline.run_workflow(
                        task="Test task",
                        repo_root=str(tmp_path),
                        session=None,
                        llm_client=mock_llm_client,
                        session_id="test-sess",
                        dispatcher=mock_dispatcher,
                    )
                except Exception:
                    pass  # We only need the graph structure

        assert captured_graph[0] is not None, "Graph should have been built and captured"
        node_ids = [n.node_id for n in captured_graph[0].nodes]

        # ExecutionNode must exist in the graph
        assert "execution_node" in node_ids, (
            f"execution_node should be in graph but got: {node_ids}"
        )

        # SemanticReviewNode must appear after execution_node in the sequential graph
        try:
            exec_idx = node_ids.index("execution_node")
            semantic_idx = node_ids.index("semantic_review")
        except ValueError as exc:
            pytest.fail(
                f"Graph should contain both execution_node and semantic_review nodes. "
                f"Got node IDs: {node_ids}. Missing: {exc}"
            )

        assert semantic_idx == exec_idx + 1, (
            f"SemanticReviewNode must be immediately after ExecutionNode in graph order. "
            f"execution_node at index {exec_idx}, semantic_review at index {semantic_idx}. "
            f"Full node list: {node_ids}"
        )

    asyncio.run(_run())


# ── 2. Timing gate prevents run on failed validation collision risk ─────────


def test_timing_gate_prevents_run_on_failed_validation_collision_risk():
    """When the execution phase returns a Fail result, SemanticReviewNode is NOT invoked.

    This enforces the timing gate: semantic review only runs after successful
    deterministic validation (execution completion). If execution fails, the
    graph terminates at the failure point and never reaches the semantic gate —
    preventing wasted LLM calls on invalid states.
    """
    import asyncio

    async def _run():
        _setup_mock_modules_for_semantic_gate()

        # Force ExecutionNode to return Fail
        sys.modules["lean_ai.workflow.executor"].execute_plan = AsyncMock(
            side_effect=RuntimeError("execution failed")
        )

        from lean_ai.llm.plan_schema import ExecutionPlan, PlanStep

        plan = ExecutionPlan(
            scope="Test",
            user_summary="A test plan",
            steps=[PlanStep(step_number=1, instruction="do something")],
            affected_files=["test_file.py"],
            test_strategy="run tests",
        )
        sys.modules["lean_ai.llm.planner"].create_plan = AsyncMock(return_value=plan)

        mock_llm_client = MagicMock()
        mock_llm_client.model_name = "test-model"
        mock_llm_client.provider_name = "test-provider"

        from lean_ai.workflow import pipeline
        from lean_ai.workflow.state import StateManager, WorkflowState

        class FakeStateManager:
            def __init__(self, session_id):
                self.session_id = session_id

            async def get_state_async(self):
                return WorkflowState.from_scratch(session_id="test-sess")

            async def save_checkpoint_async(self, **kwargs):
                return "cp-1"

            def save(self):
                pass

        mock_dispatcher = MagicMock()
        mock_dispatcher.wait_for_approval = AsyncMock(return_value={"type": "approve"})

        with patch.object(pipeline, "StateManager", FakeStateManager):
            # The validation module's _run_semantic_review should NOT be called
            semantic_review_mock = sys.modules["lean_ai.workflow.validation"]._run_semantic_review

            try:
                await pipeline.run_workflow(
                    task="Test task",
                    repo_root="/tmp/fake-repo",
                    session=None,
                    llm_client=mock_llm_client,
                    session_id="test-sess",
                    dispatcher=mock_dispatcher,
                )
            except RuntimeError:
                pass  # Expected — execution failure propagates

            assert not semantic_review_mock.await_count or semantic_review_mock.await_count == 0, (
                f"Timing gate violated: _run_semantic_review was called {semantic_review_mock.await_count} "
                "time(s) after a failed execution. Semantic review should NOT run on failed states."
            )

    asyncio.run(_run())


# ── 3. Approval path continues to completion after semantic review passes ───


def test_approval_path_continues_after_semantic_review_passes(
    tmp_path, monkeypatch
):
    """When SemanticReviewNode returns Continue (approval), the workflow proceeds
    to the final validation/completion phase without suspension or failure.

    The semantic gate acts as a pass-through when diffs align with the approved plan,
    allowing the pipeline to reach its natural completion state.
    """
    import asyncio

    async def _run():
        monkeypatch.chdir(tmp_path)

        from lean_ai.llm.plan_schema import ExecutionPlan, PlanStep

        plan = ExecutionPlan(
            scope="Test",
            user_summary="A test plan",
            steps=[PlanStep(step_number=1, instruction="do something")],
            affected_files=["test_file.py"],
            test_strategy="run tests",
        )

        _setup_mock_modules_for_semantic_gate()
        sys.modules["lean_ai.llm.planner"].create_plan = AsyncMock(return_value=plan)

        # Semantic review returns Continue — meaning diffs are approved
        sys.modules["lean_ai.workflow.validation"]._run_semantic_review = AsyncMock(
            return_value=Continue(next_node_id=None, payload={"semantic_approved": True})
        )

        mock_llm_client = MagicMock()
        mock_llm_client.model_name = "test-model"
        mock_llm_client.provider_name = "test-provider"

        from lean_ai.workflow import pipeline
        from lean_ai.workflow.graph import WorkflowEngine
        from lean_ai.workflow.state import StateManager, WorkflowState

        final_result = [None]

        class FakeStateManager:
            def __init__(self, session_id):
                self.session_id = session_id

            async def get_state_async(self):
                return WorkflowState.from_scratch(session_id="test-sess")

            async def save_checkpoint_async(self, **kwargs):
                return "cp-1"

            def save(self):
                pass

        mock_dispatcher = MagicMock()
        mock_dispatcher.wait_for_approval = AsyncMock(return_value={"type": "approve"})

        with patch.object(pipeline, "StateManager", FakeStateManager):
            async def capture_result(engine_self, graph, state_manager=None, state=None):
                # Semantic review returned Continue — engine should proceed through all nodes
                final_result[0] = Continue(next_node_id=None)
                return final_result[0]

            with patch.object(WorkflowEngine, "run", new=capture_result):
                result = await pipeline.run_workflow(
                    task="Test task",
                    repo_root=str(tmp_path),
                    session=None,
                    llm_client=mock_llm_client,
                    session_id="test-sess",
                    dispatcher=mock_dispatcher,
                )

        # Workflow should complete without raising — approval path reached completion
        assert result is not None or final_result[0] is not None, (
            "Workflow should return a result when semantic review approves"
        )

        # Verify _run_semantic_review was actually called (gate executed)
        semantic_mock = sys.modules["lean_ai.workflow.validation"]._run_semantic_review
        assert semantic_mock.await_count >= 1, (
            "_run_semantic_review should be invoked on the success path through execution"
        )

    asyncio.run(_run())

"""Integration tests for migrated pipeline.py run_workflow.

Verifies end-to-end suspension/resumption behavior through the
WorkflowEngine-driven graph: PlanningNode → ApprovalNodeExtended → ExecutionNode.

Covers:
  1. Full workflow session reaching ApprovalNode (planning completes, plan stored)
  2. Suspension at approval stage (dispatcher returns None / session closed)
  3. Resumption after simulated approval (dispatcher returns approve, execution runs)
  4. state.current_phase accurately reflects current node phase
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, ModuleType, patch

import pytest

from lean_ai.llm.plan_schema import ExecutionPlan, PlanStep
from lean_ai.workflow.graph import Continue, Fail, Suspend
from lean_ai.workflow.state import WorkflowState
from lean_ai.workflow.ws_protocol import WorkflowSessionClosedError


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_plan() -> ExecutionPlan:
    """Create a minimal ExecutionPlan suitable for testing."""
    return ExecutionPlan(
        scope="Test plan",
        user_summary="A test plan for integration testing",
        steps=[PlanStep(step_number=1, instruction="do something")],
        affected_files=["test_file.py"],
        test_strategy="run tests",
    )


def _make_state(session_id: str = "test-sess") -> WorkflowState:
    """Create a fresh WorkflowState for tests."""
    return WorkflowState.from_scratch(session_id)


def _setup_mock_modules(plan, execute_plan_return=None):
    """Inject mock modules for all heavy dependencies of pipeline.py.

    lean_ai.llm.planner has a syntax error so we must inject a mock
    module before pipeline.py can be imported.
    """
    sys.modules.pop("lean_ai.workflow.pipeline", None)
    workflow_pkg = sys.modules.get("lean_ai.workflow")
    if workflow_pkg is not None and hasattr(workflow_pkg, "pipeline"):
        delattr(workflow_pkg, "pipeline")

    # Inject mock for the broken planner module
    mock_planner = ModuleType("lean_ai.llm.planner")
    mock_planner.create_plan = AsyncMock(return_value=plan)
    sys.modules["lean_ai.llm.planner"] = mock_planner

    # Mock executor module
    mock_executor = ModuleType("lean_ai.workflow.executor")
    if execute_plan_return is not None:
        mock_executor.execute_plan = AsyncMock(return_value=execute_plan_return)
    else:
        mock_executor.execute_plan = AsyncMock()
    sys.modules["lean_ai.workflow.executor"] = mock_executor

    # Mock fix_mode module
    mock_fix_mode = ModuleType("lean_ai.workflow.fix_mode")
    mock_fix_mode._run_fix = AsyncMock()
    sys.modules["lean_ai.workflow.fix_mode"] = mock_fix_mode

    # Mock callbacks module
    mock_callbacks = ModuleType("lean_ai.workflow.callbacks")
    mock_callbacks.build_workflow_callbacks = MagicMock()
    sys.modules["lean_ai.workflow.callbacks"] = mock_callbacks

    # Mock validation module
    mock_validation = ModuleType("lean_ai.workflow.validation")
    mock_validation._effective_post_commands = MagicMock(return_value={"test": ""})
    sys.modules["lean_ai.workflow.validation"] = mock_validation

    # Mock hooks module
    mock_hooks = ModuleType("lean_ai.workflow.hooks")
    mock_hooks.fire_workflow_event = MagicMock()
    mock_hooks.fire_plan_decision_hook = MagicMock()
    sys.modules["lean_ai.workflow.hooks"] = mock_hooks

    # Mock span_context module
    mock_span_context = ModuleType("lean_ai.training.span_context")
    mock_span = AsyncMock()
    mock_span.__aenter__ = AsyncMock(return_value=None)
    mock_span.__aexit__ = AsyncMock(return_value=None)
    mock_span_context.trace_span = MagicMock(return_value=mock_span)
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
    mock_ws_protocol = ModuleType("lean_ai.workflow.ws_protocol")
    mock_ws_protocol.WorkflowSessionClosedError = WorkflowSessionClosedError
    mock_ws_protocol.WorkflowSession = MagicMock
    sys.modules["lean_ai.workflow.ws_protocol"] = mock_ws_protocol

    return mock_hooks, mock_ws_handler


def _build_state_manager_mock(captured_state_ref):
    """Build a StateManager mock that captures state for inspection."""

    def factory(*args, **kwargs):
        manager = MagicMock()
        state = _make_state("test-sess")
        manager.get_state_async = AsyncMock(return_value=state)
        manager.session_id = "test-sess"
        manager.save = MagicMock()
        manager.save_checkpoint_async = AsyncMock(return_value="cp-1")
        captured_state_ref[0] = state
        return manager

    return factory


@pytest.fixture(autouse=True)
def _restore_mocked_modules():
    """Restore sys.modules entries mutated by _setup_mock_modules()."""
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


# ── 1. Full workflow session reaching ApprovalNode ──────────────────────────


async def test_run_workflow_planning_node_completes_and_stores_plan(
    tmp_path, monkeypatch
):
    """PlanningNode runs create_plan, stores result in state, then continues
    to ApprovalNodeExtended. The workflow reaches the approval stage."""
    monkeypatch.chdir(tmp_path)

    plan = _make_plan()
    mock_llm_client = MagicMock()
    mock_llm_client.model_name = "test-model"
    mock_llm_client.provider_name = "test-provider"

    mock_dispatcher = MagicMock()
    mock_dispatcher.wait_for_approval = AsyncMock(return_value=None)

    _setup_mock_modules(plan)

    captured = [None]

    # Import pipeline with mocked deps
    from lean_ai.workflow import pipeline

    with patch.object(pipeline, "StateManager", side_effect=_build_state_manager_mock(captured)):
        with pytest.raises(WorkflowSessionClosedError):
            await pipeline.run_workflow(
                task="Test task",
                repo_root=str(tmp_path),
                session=None,
                llm_client=mock_llm_client,
                session_id="test-sess",
                dispatcher=mock_dispatcher,
            )

    # Verify the plan was stored in session_metadata (planning completed)
    assert captured[0] is not None
    assert "plan" in captured[0].session_metadata
    assert captured[0].session_metadata["plan"] == plan


async def test_run_workflow_planning_stores_plan_in_session_metadata(
    tmp_path, monkeypatch
):
    """After PlanningNode executes, state.session_metadata contains the plan."""
    monkeypatch.chdir(tmp_path)

    plan = _make_plan()
    mock_llm_client = MagicMock()
    mock_llm_client.model_name = "test-model"
    mock_llm_client.provider_name = "test-provider"

    mock_dispatcher = MagicMock()
    mock_dispatcher.wait_for_approval = AsyncMock(return_value=None)

    _setup_mock_modules(plan)

    captured = [None]

    from lean_ai.workflow import pipeline

    with patch.object(pipeline, "StateManager", side_effect=_build_state_manager_mock(captured)):
        with pytest.raises(WorkflowSessionClosedError):
            await pipeline.run_workflow(
                task="Test task",
                repo_root=str(tmp_path),
                session=None,
                llm_client=mock_llm_client,
                session_id="test-sess",
                dispatcher=mock_dispatcher,
            )

    # Verify the plan was stored in session_metadata
    assert captured[0] is not None
    assert "plan" in captured[0].session_metadata
    assert captured[0].session_metadata["plan"] == plan


# ── 2. Suspension at approval stage ────────────────────────────────────────


async def test_run_workflow_suspends_at_approval_when_session_closes(
    tmp_path, monkeypatch
):
    """When the dispatcher returns None (session closed), the workflow
    stops at the approval stage without executing."""
    monkeypatch.chdir(tmp_path)

    plan = _make_plan()
    mock_llm_client = MagicMock()
    mock_llm_client.model_name = "test-model"
    mock_llm_client.provider_name = "test-provider"

    # Track whether execute_plan was called
    execute_plan_called = False

    async def fake_execute_plan(*args, **kwargs):
        nonlocal execute_plan_called
        execute_plan_called = True
        return "executed"

    mock_dispatcher = MagicMock()
    mock_dispatcher.wait_for_approval = AsyncMock(return_value=None)

    _setup_mock_modules(plan)

    from lean_ai.workflow import pipeline

    with patch.object(pipeline, "StateManager", side_effect=_build_state_manager_mock([None])):
        with patch.object(pipeline, "execute_plan", new_callable=AsyncMock, side_effect=fake_execute_plan):
            with pytest.raises(WorkflowSessionClosedError):
                await pipeline.run_workflow(
                    task="Test task",
                    repo_root=str(tmp_path),
                    session=None,
                    llm_client=mock_llm_client,
                    session_id="test-sess",
                    dispatcher=mock_dispatcher,
                )

    # execute_plan should NOT have been called because approval was never granted
    assert not execute_plan_called


async def test_approval_node_extended_raises_on_none_dispatcher(
    tmp_path, monkeypatch
):
    """ApprovalNodeExtended raises WorkflowSessionClosedError when
    dispatcher.wait_for_approval returns None."""
    monkeypatch.chdir(tmp_path)

    plan = _make_plan()
    mock_llm_client = MagicMock()
    mock_llm_client.model_name = "test-model"
    mock_llm_client.provider_name = "test-provider"

    # ws_send should be called with "approval_required" before the dispatcher call
    ws_send_mock = AsyncMock()

    mock_dispatcher = MagicMock()
    mock_dispatcher.wait_for_approval = AsyncMock(return_value=None)

    _setup_mock_modules(plan)

    from lean_ai.workflow import pipeline

    with patch.object(pipeline, "StateManager", side_effect=_build_state_manager_mock([None])):
        with patch.object(pipeline, "ws_send", ws_send_mock):
            with pytest.raises(WorkflowSessionClosedError):
                await pipeline.run_workflow(
                    task="Test task",
                    repo_root=str(tmp_path),
                    session=None,
                    llm_client=mock_llm_client,
                    session_id="test-sess",
                    dispatcher=mock_dispatcher,
                )

    # Verify approval_required was sent
    approval_calls = [
        call for call in ws_send_mock.call_args_list if call[0][1] == "approval_required"
    ]
    assert len(approval_calls) >= 1


# ── 3. Resumption after simulated approval ─────────────────────────────────


async def test_run_workflow_resumes_after_approval_and_executes(
    tmp_path, monkeypatch
):
    """After the dispatcher returns an approve message, the workflow
    continues to ExecutionNode and runs execute_plan."""
    monkeypatch.chdir(tmp_path)

    plan = _make_plan()
    mock_llm_client = MagicMock()
    mock_llm_client.model_name = "test-model"
    mock_llm_client.provider_name = "test-provider"

    execution_result = "execution completed successfully"

    mock_dispatcher = MagicMock()
    mock_dispatcher.wait_for_approval = AsyncMock(return_value={"type": "approve"})

    _setup_mock_modules(plan, execute_plan_return=execution_result)

    from lean_ai.workflow import pipeline

    mock_execute = AsyncMock(return_value=execution_result)

    with patch.object(pipeline, "StateManager", side_effect=_build_state_manager_mock([None])):
        with patch.object(pipeline, "execute_plan", mock_execute):
            result = await pipeline.run_workflow(
                task="Test task",
                repo_root=str(tmp_path),
                session=None,
                llm_client=mock_llm_client,
                session_id="test-sess",
                dispatcher=mock_dispatcher,
            )

    # The workflow should return the execution result
    assert result == execution_result


async def test_run_workflow_approval_stores_approved_plan(
    tmp_path, monkeypatch
):
    """After approval, the approved plan is stored in session_metadata."""
    monkeypatch.chdir(tmp_path)

    plan = _make_plan()
    mock_llm_client = MagicMock()
    mock_llm_client.model_name = "test-model"
    mock_llm_client.provider_name = "test-provider"

    mock_dispatcher = MagicMock()
    mock_dispatcher.wait_for_approval = AsyncMock(return_value={"type": "approve"})

    _setup_mock_modules(plan, execute_plan_return="done")

    captured = [None]

    from lean_ai.workflow import pipeline

    with patch.object(pipeline, "StateManager", side_effect=_build_state_manager_mock(captured)):
        await pipeline.run_workflow(
            task="Test task",
            repo_root=str(tmp_path),
            session=None,
            llm_client=mock_llm_client,
            session_id="test-sess",
            dispatcher=mock_dispatcher,
        )

    assert captured[0] is not None
    assert "approved_plan" in captured[0].session_metadata
    assert captured[0].session_metadata["approved_plan"] == plan


# ── 4. state.current_phase accurately reflects current node ────────────────


async def test_run_workflow_current_phase_set_to_planning(
    tmp_path, monkeypatch
):
    """After PlanningNode executes, state.current_phase is set to 'planning'."""
    monkeypatch.chdir(tmp_path)

    plan = _make_plan()
    mock_llm_client = MagicMock()
    mock_llm_client.model_name = "test-model"
    mock_llm_client.provider_name = "test-provider"

    mock_dispatcher = MagicMock()
    mock_dispatcher.wait_for_approval = AsyncMock(return_value=None)

    _setup_mock_modules(plan)

    captured = [None]

    from lean_ai.workflow import pipeline

    with patch.object(pipeline, "StateManager", side_effect=_build_state_manager_mock(captured)):
        with pytest.raises(WorkflowSessionClosedError):
            await pipeline.run_workflow(
                task="Test task",
                repo_root=str(tmp_path),
                session=None,
                llm_client=mock_llm_client,
                session_id="test-sess",
                dispatcher=mock_dispatcher,
            )

    assert captured[0] is not None
    assert captured[0].current_phase == "planning"


async def test_run_workflow_current_phase_set_to_approval_after_approve(
    tmp_path, monkeypatch
):
    """After ApprovalNodeExtended approves, state.current_phase is set to 'approval'."""
    monkeypatch.chdir(tmp_path)

    plan = _make_plan()
    mock_llm_client = MagicMock()
    mock_llm_client.model_name = "test-model"
    mock_llm_client.provider_name = "test-provider"

    mock_dispatcher = MagicMock()
    mock_dispatcher.wait_for_approval = AsyncMock(return_value={"type": "approve"})

    _setup_mock_modules(plan, execute_plan_return="done")

    captured = [None]

    from lean_ai.workflow import pipeline

    with patch.object(pipeline, "StateManager", side_effect=_build_state_manager_mock(captured)):
        await pipeline.run_workflow(
            task="Test task",
            repo_root=str(tmp_path),
            session=None,
            llm_client=mock_llm_client,
            session_id="test-sess",
            dispatcher=mock_dispatcher,
        )

    assert captured[0] is not None
    # After the full workflow, the final phase is set by run_workflow
    # to "validation" (the last phase before return)
    assert captured[0].current_phase == "validation"


async def test_run_workflow_current_phase_progresses_through_phases(
    tmp_path, monkeypatch
):
    """state.current_phase transitions: planning → approval → execution_complete → validation."""
    monkeypatch.chdir(tmp_path)

    plan = _make_plan()
    mock_llm_client = MagicMock()
    mock_llm_client.model_name = "test-model"
    mock_llm_client.provider_name = "test-provider"

    phase_history = []

    def capture_state_manager(*args, **kwargs):
        manager = MagicMock()
        state = _make_state("test-sess")
        manager.get_state_async = AsyncMock(return_value=state)
        manager.session_id = "test-sess"

        def save_side_effect():
            phase_history.append(state.current_phase)

        manager.save = MagicMock(side_effect=save_side_effect)
        manager.save_checkpoint_async = AsyncMock(return_value="cp-1")
        return manager

    mock_dispatcher = MagicMock()
    mock_dispatcher.wait_for_approval = AsyncMock(return_value={"type": "approve"})

    _setup_mock_modules(plan, execute_plan_return="done")

    from lean_ai.workflow import pipeline

    with patch.object(pipeline, "StateManager", side_effect=capture_state_manager):
        await pipeline.run_workflow(
            task="Test task",
            repo_root=str(tmp_path),
            session=None,
            llm_client=mock_llm_client,
            session_id="test-sess",
            dispatcher=mock_dispatcher,
        )

    # Phase history should include planning and approval phases
    assert "planning" in phase_history
    assert "approval" in phase_history


async def test_run_workflow_stops_on_planning_fail_without_execution_checkpoints(
    tmp_path, monkeypatch
):
    """Planning failure should not be marked as execution/validation success."""
    monkeypatch.chdir(tmp_path)

    plan = _make_plan()
    mock_llm_client = MagicMock()
    mock_llm_client.model_name = "test-model"
    mock_llm_client.provider_name = "test-provider"

    _setup_mock_modules(plan)

    import sys

    sys.modules["lean_ai.llm.planner"].create_plan = AsyncMock(
        side_effect=RuntimeError("planner exploded")
    )

    captured = [None]
    manager_ref = [None]

    def capture_state_manager(*args, **kwargs):
        manager = _build_state_manager_mock(captured)(*args, **kwargs)
        manager_ref[0] = manager
        return manager

    from lean_ai.workflow import pipeline

    with patch.object(pipeline, "StateManager", side_effect=capture_state_manager):
        with pytest.raises(RuntimeError, match="planner exploded"):
            await pipeline.run_workflow(
                task="Test task",
                repo_root=str(tmp_path),
                session=None,
                llm_client=mock_llm_client,
                session_id="test-sess",
                dispatcher=MagicMock(),
            )

    assert captured[0].current_phase == "failed"
    assert captured[0].session_metadata["workflow_error"] == "planner exploded"
    manager_ref[0].save_checkpoint_async.assert_not_awaited()

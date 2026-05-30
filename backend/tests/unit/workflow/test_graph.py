"""Unit tests for workflow graph infrastructure.

Covers:
  1. Linear node execution (LLMNode, ToolNode sequential flow)
  2. Conditional routing via ConditionalNode
  3. Suspend/resume cycles via ApprovalNode
  4. Graph builder API (add_node, set_entry_point, get_node)
  5. WorkflowEngine state transitions and StateManager.save() calls
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lean_ai.workflow.graph import (
    ApprovalNode,
    ConditionalNode,
    Continue,
    Fail,
    LLMNode,
    Node,
    NodeResult,
    SubgraphNode,
    Suspend,
    ToolNode,
    WorkflowEngine,
    WorkflowGraph,
)
from lean_ai.workflow.state import WorkflowState


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_state(session_id: str = "test-sess") -> WorkflowState:
    """Create a fresh WorkflowState for tests."""
    return WorkflowState.from_scratch(session_id)


class _PassThroughNode(Node):
    """Concrete Node subclass that always returns Continue with no jump."""

    def __init__(self, node_id: str) -> None:
        super().__init__(node_id)
        self.call_count = 0

    async def execute(self, state: WorkflowState) -> NodeResult:
        self.call_count += 1
        return Continue(next_node_id=None)


class _JumpNode(Node):
    """Concrete Node that returns Continue pointing to a specific next node."""

    def __init__(self, node_id: str, next_id: str | None) -> None:
        super().__init__(node_id)
        self._next_id = next_id

    async def execute(self, state: WorkflowState) -> NodeResult:
        return Continue(next_node_id=self._next_id)


class _FailNode(Node):
    """Concrete Node that always returns Fail."""

    def __init__(self, node_id: str, error_msg: str = "intentional failure") -> None:
        super().__init__(node_id)
        self._error_msg = error_msg

    async def execute(self, state: WorkflowState) -> NodeResult:
        return Fail(error=self._error_msg)


# ── 1. Linear node execution ────────────────────────────────────────────────


async def test_llm_node_returns_continue_with_payload():
    """LLMNode.execute returns Continue with node_id in payload."""
    node = LLMNode("llm-1", system_prompt="sys", user_prompt="user")
    state = _make_state()
    result = await node.execute(state)
    assert isinstance(result, Continue)
    assert result.payload["node_id"] == "llm-1"


async def test_llm_node_stores_configuration():
    """LLMNode stores all constructor parameters."""
    tools = [{"name": "test_tool"}]
    node = LLMNode("llm-1", system_prompt="sys", user_prompt="user", tools=tools, max_turns=3)
    assert node.system_prompt == "sys"
    assert node.user_prompt == "user"
    assert node.tools == tools
    assert node.max_turns == 3


async def test_llm_node_defaults_tools_to_empty_list():
    """LLMNode with no tools argument defaults to an empty list."""
    node = LLMNode("llm-1")
    assert node.tools == []


async def test_tool_node_without_fn_returns_continue():
    """ToolNode with no tool_fn returns Continue with node_id payload."""
    node = ToolNode("tool-1", tool_name="echo")
    state = _make_state()
    result = await node.execute(state)
    assert isinstance(result, Continue)
    assert result.payload["node_id"] == "tool-1"


async def test_tool_node_with_fn_returns_tool_result():
    """ToolNode with a tool_fn returns Continue with tool_result payload."""
    node = ToolNode("tool-1", tool_fn=lambda s: "output")
    state = _make_state()
    result = await node.execute(state)
    assert isinstance(result, Continue)
    assert result.payload["tool_result"] == "output"


async def test_tool_node_with_fn_returning_none_falls_back():
    """ToolNode whose tool_fn returns None falls back to node_id payload."""
    node = ToolNode("tool-1", tool_fn=lambda s: None)
    state = _make_state()
    result = await node.execute(state)
    assert isinstance(result, Continue)
    assert result.payload["node_id"] == "tool-1"


async def test_linear_execution_runs_all_nodes_in_order():
    """WorkflowEngine runs nodes sequentially when Continue.next_node_id is None."""
    graph = WorkflowGraph()
    nodes = [_PassThroughNode(f"n{i}") for i in range(3)]
    for n in nodes:
        graph.add_node(n)

    engine = WorkflowEngine()
    result = await engine.run(graph, state=_make_state())

    assert isinstance(result, Continue)
    for n in nodes:
        assert n.call_count == 1


async def test_linear_execution_stops_on_fail():
    """WorkflowEngine stops execution when a node returns Fail."""
    graph = WorkflowGraph()
    graph.add_node(_PassThroughNode("n0"))
    graph.add_node(_FailNode("n1"))
    graph.add_node(_PassThroughNode("n2"))

    engine = WorkflowEngine()
    result = await engine.run(graph, state=_make_state())

    assert isinstance(result, Fail)
    assert result.error == "intentional failure"
    # n2 should not have been executed
    assert graph.get_node("n2").call_count == 0  # type: ignore[union-attr]


async def test_engine_respects_max_iterations():
    """WorkflowEngine returns Fail when max_iterations is exceeded."""
    # Create a cycle: n0 -> n1 -> n0
    graph = WorkflowGraph()
    graph.add_node(_JumpNode("n0", "n1"))
    graph.add_node(_JumpNode("n1", "n0"))

    engine = WorkflowEngine(max_iterations=5)
    result = await engine.run(graph, state=_make_state())

    assert isinstance(result, Fail)
    assert "Max iterations" in result.error


# ── 2. Conditional routing via ConditionalNode ──────────────────────────────


async def test_conditional_node_routes_true_branch():
    """ConditionalNode routes to true_next when condition returns True."""
    node = ConditionalNode(
        "cond-1",
        condition=lambda s: True,
        true_next="branch-a",
        false_next="branch-b",
    )
    state = _make_state()
    result = await node.execute(state)
    assert isinstance(result, Continue)
    assert result.next_node_id == "branch-a"


async def test_conditional_node_routes_false_branch():
    """ConditionalNode routes to false_next when condition returns False."""
    node = ConditionalNode(
        "cond-1",
        condition=lambda s: False,
        true_next="branch-a",
        false_next="branch-b",
    )
    state = _make_state()
    result = await node.execute(state)
    assert isinstance(result, Continue)
    assert result.next_node_id == "branch-b"


async def test_conditional_node_no_condition_returns_none_next():
    """ConditionalNode with no condition returns Continue with next_node_id=None."""
    node = ConditionalNode("cond-1")
    state = _make_state()
    result = await node.execute(state)
    assert isinstance(result, Continue)
    assert result.next_node_id is None


async def test_conditional_node_condition_receives_state():
    """ConditionalNode passes WorkflowState to the condition callable."""
    received_state: WorkflowState | None = None

    def capture_state(s: WorkflowState) -> bool:
        nonlocal received_state
        received_state = s
        return True

    node = ConditionalNode("cond-1", condition=capture_state, true_next="a", false_next="b")
    state = _make_state()
    await node.execute(state)
    assert received_state is state


async def test_conditional_routing_in_engine():
    """WorkflowEngine follows next_node_id from ConditionalNode to jump nodes."""
    graph = WorkflowGraph()
    graph.add_node(_PassThroughNode("start"))
    graph.add_node(
        ConditionalNode(
            "cond",
            condition=lambda s: True,
            true_next="branch-a",
            false_next="branch-b",
        )
    )
    graph.add_node(_PassThroughNode("branch-a"))
    graph.add_node(_PassThroughNode("branch-b"))

    engine = WorkflowEngine()
    result = await engine.run(graph, state=_make_state())

    assert isinstance(result, Continue)
    # branch-a was reached via jump, branch-b was skipped then reached sequentially
    assert graph.get_node("branch-a").call_count == 1  # type: ignore[union-attr]
    assert graph.get_node("branch-b").call_count == 1  # type: ignore[union-attr]


# ── 3. Suspend/resume cycles via ApprovalNode ───────────────────────────────


async def test_approval_node_returns_suspend():
    """ApprovalNode.execute returns Suspend with the prompt as reason."""
    node = ApprovalNode("approve-1", prompt="Proceed?")
    state = _make_state()
    result = await node.execute(state)
    assert isinstance(result, Suspend)
    assert result.reason == "Proceed?"
    assert result.payload["node_id"] == "approve-1"


async def test_approval_node_default_prompt():
    """ApprovalNode with empty prompt uses default reason text."""
    node = ApprovalNode("approve-1")
    state = _make_state()
    result = await node.execute(state)
    assert isinstance(result, Suspend)
    assert result.reason == "Awaiting user approval"


async def test_engine_stops_on_suspend():
    """WorkflowEngine stops iteration when a node returns Suspend."""
    graph = WorkflowGraph()
    graph.add_node(_PassThroughNode("n0"))
    graph.add_node(ApprovalNode("n1", prompt="Go?"))
    graph.add_node(_PassThroughNode("n2"))

    engine = WorkflowEngine()
    result = await engine.run(graph, state=_make_state())

    assert isinstance(result, Suspend)
    assert result.reason == "Go?"
    # n2 should not have been executed
    assert graph.get_node("n2").call_count == 0  # type: ignore[union-attr]


async def test_suspend_preserves_state_for_resume():
    """State mutations before suspension are visible after resume."""
    graph = WorkflowGraph()
    graph.add_node(_PassThroughNode("n0"))
    graph.add_node(ApprovalNode("n1", prompt="Go?"))

    engine = WorkflowEngine()
    state = _make_state()
    state.add_journal_entry("before-suspend")
    result = await engine.run(graph, state=state)

    assert isinstance(result, Suspend)
    assert "before-suspend" in state.journal_entries


# ── 4. Graph builder API ────────────────────────────────────────────────────


def test_add_node_returns_self_for_chaining():
    """WorkflowGraph.add_node returns self for method chaining."""
    graph = WorkflowGraph()
    result = graph.add_node(LLMNode("llm-1"))
    assert result is graph


def test_add_node_sets_entry_point():
    """The first node added becomes the entry point."""
    graph = WorkflowGraph()
    graph.add_node(LLMNode("first"))
    assert graph.entry_point == "first"


def test_set_entry_point_overrides_default():
    """set_entry_point changes the entry point to the given node_id."""
    graph = WorkflowGraph()
    graph.add_node(LLMNode("a"))
    graph.add_node(LLMNode("b"))
    graph.set_entry_point("b")
    assert graph.entry_point == "b"


def test_get_node_returns_node_by_id():
    """get_node looks up a node by its ID."""
    graph = WorkflowGraph()
    graph.add_node(LLMNode("x"))
    node = graph.get_node("x")
    assert node is not None
    assert node.node_id == "x"


def test_get_node_returns_none_for_missing_id():
    """get_node returns None for an ID that was never added."""
    graph = WorkflowGraph()
    graph.add_node(LLMNode("x"))
    assert graph.get_node("missing") is None


def test_nodes_property_returns_ordered_list():
    """graph.nodes returns nodes in the order they were added."""
    graph = WorkflowGraph()
    graph.add_node(LLMNode("a"))
    graph.add_node(LLMNode("b"))
    graph.add_node(LLMNode("c"))
    ids = [n.node_id for n in graph.nodes]
    assert ids == ["a", "b", "c"]


def test_empty_graph_has_none_entry_point():
    """A freshly created graph has no entry point."""
    graph = WorkflowGraph()
    assert graph.entry_point is None


# ── 5. WorkflowEngine state transitions and StateManager.save() ─────────────


async def test_engine_calls_save_after_each_node():
    """WorkflowEngine calls state_manager.save() after each node execution."""
    mock_manager = MagicMock(spec=["save", "get_state"])
    mock_manager.get_state.return_value = _make_state()

    graph = WorkflowGraph()
    graph.add_node(_PassThroughNode("n0"))
    graph.add_node(_PassThroughNode("n1"))

    engine = WorkflowEngine()
    await engine.run(graph, state_manager=mock_manager)

    assert mock_manager.save.call_count == 2


async def test_engine_uses_provided_state():
    """WorkflowEngine uses the state passed explicitly, not from manager."""
    mock_manager = MagicMock(spec=["save", "get_state"])

    graph = WorkflowGraph()
    graph.add_node(_PassThroughNode("n0"))

    engine = WorkflowEngine()
    state = _make_state("explicit-sess")
    await engine.run(graph, state_manager=mock_manager, state=state)

    # get_state should NOT be called when state is provided
    mock_manager.get_state.assert_not_called()


async def test_engine_loads_state_from_manager_when_not_provided():
    """WorkflowEngine calls get_state() when no state is passed."""
    mock_manager = MagicMock(spec=["save", "get_state"])
    mock_manager.get_state.return_value = _make_state()

    graph = WorkflowGraph()
    graph.add_node(_PassThroughNode("n0"))

    engine = WorkflowEngine()
    await engine.run(graph, state_manager=mock_manager)

    mock_manager.get_state.assert_called_once()


async def test_engine_creates_default_state_when_no_manager():
    """WorkflowEngine creates a default state when neither state nor manager is given."""
    graph = WorkflowGraph()
    graph.add_node(_PassThroughNode("n0"))

    engine = WorkflowEngine()
    result = await engine.run(graph)

    assert isinstance(result, Continue)


async def test_engine_handles_save_exception_gracefully():
    """WorkflowEngine catches save exceptions and continues execution."""
    mock_manager = MagicMock(spec=["save", "get_state"])
    mock_manager.get_state.return_value = _make_state()
    mock_manager.save.side_effect = RuntimeError("disk full")

    graph = WorkflowGraph()
    graph.add_node(_PassThroughNode("n0"))
    graph.add_node(_PassThroughNode("n1"))

    engine = WorkflowEngine()
    result = await engine.run(graph, state_manager=mock_manager)

    # Execution should still complete despite save failures
    assert isinstance(result, Continue)
    assert mock_manager.save.call_count == 2


async def test_engine_returns_fail_for_missing_entry_point():
    """WorkflowEngine returns Fail when the entry point node doesn't exist."""
    graph = WorkflowGraph()
    graph.set_entry_point("nonexistent")

    engine = WorkflowEngine()
    result = await engine.run(graph, state=_make_state())

    assert isinstance(result, Fail)
    assert "nonexistent" in result.error


async def test_subgraph_node_with_none_subgraph():
    """SubgraphNode with no subgraph returns Continue."""
    node = SubgraphNode("sub-1")
    state = _make_state()
    result = await node.execute(state)
    assert isinstance(result, Continue)
    assert result.payload["node_id"] == "sub-1"


async def test_subgraph_node_delegates_to_nested_graph():
    """SubgraphNode with a subgraph runs it via WorkflowEngine."""
    inner_graph = WorkflowGraph()
    inner_graph.add_node(_PassThroughNode("inner-0"))

    node = SubgraphNode("sub-1", subgraph=inner_graph)
    state = _make_state()
    result = await node.execute(state)

    assert isinstance(result, Continue)
    assert inner_graph.get_node("inner-0").call_count == 1  # type: ignore[union-attr]


async def test_node_base_class_sets_node_id():
    """Node.__init__ stores the node_id."""

    class ConcreteNode(Node):
        async def execute(self, state: WorkflowState) -> NodeResult:
            return Continue()

    node = ConcreteNode("my-id")
    assert node.node_id == "my-id"

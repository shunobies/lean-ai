"""Workflow graph infrastructure for node-based execution.

Provides a Node abstract base class with execute(state) -> NodeResult,
sealed union result types (Continue, Suspend, Fail), specialized node
implementations (LLMNode, ToolNode, ApprovalNode, ConditionalNode,
SubgraphNode), a fluent WorkflowGraph builder, and a WorkflowEngine
that drives execution with state persistence via StateManager.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol

from lean_ai.workflow.state import StateManager, WorkflowState

logger = logging.getLogger(__name__)


# ── Result types ─────────────────────────────────────────────────────────────


class NodeResult(Protocol):
    """Protocol for node execution results.

    Concrete implementations are Continue, Suspend, and Fail.
    """


@dataclass
class Continue(NodeResult):
    """Signal that execution should proceed to the next node."""

    next_node_id: str | None = None
    """ID of the next node to execute, or None for sequential order."""

    payload: dict[str, Any] = field(default_factory=dict)
    """Optional data passed to the next node."""


@dataclass
class Suspend(NodeResult):
    """Signal that execution should pause and wait for external input."""

    reason: str = ""
    """Human-readable reason for suspension."""

    payload: dict[str, Any] = field(default_factory=dict)
    """State to preserve while suspended."""


@dataclass
class Fail(NodeResult):
    """Signal that execution has encountered an unrecoverable error."""

    error: str = ""
    """Description of the failure."""

    payload: dict[str, Any] = field(default_factory=dict)
    """Diagnostic data captured at the point of failure."""


# ── Node base class ──────────────────────────────────────────────────────────


class Node(ABC):
    """Abstract base class for workflow graph nodes.

    Each node receives the current WorkflowState and returns a NodeResult
    indicating whether execution should continue, suspend, or fail.
    """

    def __init__(self, node_id: str) -> None:
        """Initialise a node with a unique identifier.

        Args:
            node_id: A unique string identifying this node in the graph.
        """
        self.node_id = node_id

    @abstractmethod
    async def execute(self, state: WorkflowState) -> NodeResult:
        """Execute this node's logic against the given workflow state.

        Args:
            state: The current consolidated workflow state.

        Returns:
            A NodeResult indicating the next step (Continue, Suspend, or Fail).
        """


# ── Specialized node implementations ─────────────────────────────────────────


class LLMNode(Node):
    """Node that performs an LLM chat interaction.

    Wraps an LLM call (e.g., chat_with_tools) and updates workflow state
    with the result. Subclasses or configuration determine the prompt,
    tools, and model used.
    """

    def __init__(
        self,
        node_id: str,
        system_prompt: str = "",
        user_prompt: str = "",
        tools: list[dict[str, Any]] | None = None,
        max_turns: int = 1,
    ) -> None:
        """Initialise an LLM node.

        Args:
            node_id: Unique identifier for this node.
            system_prompt: System prompt to send to the LLM.
            user_prompt: User prompt template (may reference state fields).
            tools: Optional list of tool definitions for the LLM.
            max_turns: Maximum number of LLM turns before forcing exit.
        """
        super().__init__(node_id)
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        self.tools = tools or []
        self.max_turns = max_turns

    async def execute(self, state: WorkflowState) -> NodeResult:
        """Execute the LLM interaction.

        By default returns a Continue result. Override or inject an LLM
        client to perform actual inference.
        """
        logger.info("LLMNode %s: executing with max_turns=%d", self.node_id, self.max_turns)
        return Continue(next_node_id=None, payload={"node_id": self.node_id})


class ToolNode(Node):
    """Node that executes a tool or command.

    Runs a callable (or shell command) and captures the result into state.
    """

    def __init__(
        self,
        node_id: str,
        tool_name: str = "",
        tool_fn: Any = None,
    ) -> None:
        """Initialise a tool execution node.

        Args:
            node_id: Unique identifier for this node.
            tool_name: Human-readable name of the tool/command.
            tool_fn: Optional callable that receives state and returns a result.
        """
        super().__init__(node_id)
        self.tool_name = tool_name
        self.tool_fn = tool_fn

    async def execute(self, state: WorkflowState) -> NodeResult:
        """Execute the tool and return the result.

        If tool_fn is set, it is called with the current state.
        Otherwise returns a Continue result.
        """
        logger.info("ToolNode %s: executing tool=%s", self.node_id, self.tool_name)
        if self.tool_fn is not None:
            result = self.tool_fn(state)
            if result is not None:
                return Continue(next_node_id=None, payload={"tool_result": result})
        return Continue(next_node_id=None, payload={"node_id": self.node_id})


class ApprovalNode(Node):
    """Node that waits for human approval before proceeding.

    Suspends execution until an external signal (e.g., WebSocket message)
    indicates the user has approved or rejected the pending action.
    """

    def __init__(
        self,
        node_id: str,
        prompt: str = "",
    ) -> None:
        """Initialise an approval node.

        Args:
            node_id: Unique identifier for this node.
            prompt: Message presented to the user for approval.
        """
        super().__init__(node_id)
        self.prompt = prompt

    async def execute(self, state: WorkflowState) -> NodeResult:
        """Request approval from the user.

        Returns Suspend to pause execution until the user responds.
        """
        logger.info("ApprovalNode %s: waiting for user approval", self.node_id)
        return Suspend(reason=self.prompt or "Awaiting user approval", payload={"node_id": self.node_id})


class ConditionalNode(Node):
    """Node that branches execution based on a predicate.

    Evaluates a condition function against the current state and routes
    to the appropriate next node.
    """

    def __init__(
        self,
        node_id: str,
        condition: Any = None,
        true_next: str | None = None,
        false_next: str | None = None,
    ) -> None:
        """Initialise a conditional branching node.

        Args:
            node_id: Unique identifier for this node.
            condition: Callable(state) -> bool that determines the branch.
            true_next: Node ID to jump to if condition is True.
            false_next: Node ID to jump to if condition is False.
        """
        super().__init__(node_id)
        self.condition = condition
        self.true_next = true_next
        self.false_next = false_next

    async def execute(self, state: WorkflowState) -> NodeResult:
        """Evaluate the condition and route to the appropriate branch.

        Returns Continue with next_node_id set to the selected branch target.
        """
        logger.info("ConditionalNode %s: evaluating condition", self.node_id)
        if self.condition is not None:
            result = self.condition(state)
            if result:
                return Continue(next_node_id=self.true_next)
            return Continue(next_node_id=self.false_next)
        return Continue(next_node_id=None)


class SubgraphNode(Node):
    """Node that delegates execution to a nested WorkflowGraph.

    Allows composing complex workflows from smaller reusable subgraphs.
    """

    def __init__(
        self,
        node_id: str,
        subgraph: WorkflowGraph | None = None,
    ) -> None:
        """Initialise a subgraph delegation node.

        Args:
            node_id: Unique identifier for this node.
            subgraph: The nested WorkflowGraph to execute.
        """
        super().__init__(node_id)
        self.subgraph = subgraph

    async def execute(self, state: WorkflowState) -> NodeResult:
        """Execute the nested subgraph.

        Runs the subgraph using a WorkflowEngine and returns the final
        result propagated from the subgraph execution.
        """
        logger.info("SubgraphNode %s: executing subgraph", self.node_id)
        if self.subgraph is None:
            return Continue(next_node_id=None, payload={"node_id": self.node_id})

        engine = WorkflowEngine()
        result = await engine.run(self.subgraph, state_manager=None, state=state)
        return result


# ── WorkflowGraph builder ────────────────────────────────────────────────────


class WorkflowGraph:
    """Fluent builder for constructing a directed workflow graph.

    Nodes are added in order and can be linked explicitly by ID.
    The graph stores nodes in a list for sequential execution and
    a dict for random-access lookups by node ID.
    """

    def __init__(self) -> None:
        """Initialise an empty workflow graph."""
        self._nodes: list[Node] = []
        self._node_map: dict[str, Node] = {}
        self._entry_point: str | None = None

    def add_node(self, node: Node) -> WorkflowGraph:
        """Add a node to the graph.

        Args:
            node: The node to add.

        Returns:
            Self for method chaining.
        """
        self._nodes.append(node)
        self._node_map[node.node_id] = node
        if self._entry_point is None:
            self._entry_point = node.node_id
        return self

    def set_entry_point(self, node_id: str) -> WorkflowGraph:
        """Set the entry point node for the graph.

        Args:
            node_id: The ID of the node to start execution from.

        Returns:
            Self for method chaining.
        """
        self._entry_point = node_id
        return self

    def get_node(self, node_id: str) -> Node | None:
        """Look up a node by ID.

        Args:
            node_id: The unique identifier of the node.

        Returns:
            The Node if found, or None.
        """
        return self._node_map.get(node_id)

    @property
    def nodes(self) -> list[Node]:
        """Return the ordered list of nodes in the graph."""
        return self._nodes

    @property
    def entry_point(self) -> str | None:
        """Return the ID of the entry point node."""
        return self._entry_point


# ── WorkflowEngine ───────────────────────────────────────────────────────────


class WorkflowEngine:
    """Execution engine that drives a WorkflowGraph to completion.

    Iterates through nodes, calling execute() on each, managing state
    transitions, and persisting state via StateManager after every node.
    Supports suspension and failure handling.
    """

    def __init__(self, max_iterations: int = 100) -> None:
        """Initialise the workflow engine.

        Args:
            max_iterations: Safety limit on the number of node executions
                to prevent infinite loops.
        """
        self.max_iterations = max_iterations

    async def run(
        self,
        graph: WorkflowGraph,
        state_manager: StateManager | None = None,
        state: WorkflowState | None = None,
    ) -> NodeResult:
        """Execute the workflow graph from entry point to completion.

        Iterates through nodes in order, calling each node's execute()
        method with the current state. After each node, persists state
        via the StateManager if one is provided.

        Args:
            graph: The WorkflowGraph to execute.
            state_manager: Optional StateManager for persistence.
            state: Optional initial WorkflowState (loaded from manager if not given).

        Returns:
            The final NodeResult from the last executed node.
        """
        # Load or use provided state
        if state is None and state_manager is not None:
            state = state_manager.get_state()
        elif state is None:
            state = WorkflowState.from_scratch(session_id="")

        current_node_id = graph.entry_point
        iteration = 0
        result: NodeResult = Continue()

        while current_node_id is not None and iteration < self.max_iterations:
            iteration += 1
            node = graph.get_node(current_node_id)
            if node is None:
                logger.error("Node %s not found in graph", current_node_id)
                return Fail(error=f"Node {current_node_id} not found in graph")

            logger.info("Executing node %s (iteration %d)", node.node_id, iteration)
            result = await node.execute(state)

            # Persist state after each node
            if state_manager is not None:
                try:
                    state_manager.save()
                except Exception:
                    logger.warning(
                        "Failed to save state after node %s",
                        node.node_id,
                        exc_info=True,
                    )

            # Handle result types
            if isinstance(result, Continue):
                current_node_id = result.next_node_id
                if current_node_id is None:
                    # Advance to next node in sequential order
                    idx = self._find_node_index(graph, node.node_id)
                    if idx is not None and idx + 1 < len(graph.nodes):
                        current_node_id = graph.nodes[idx + 1].node_id
                    else:
                        current_node_id = None
            elif isinstance(result, Suspend):
                logger.info("Workflow suspended: %s", result.reason)
                break
            elif isinstance(result, Fail):
                logger.error("Workflow failed: %s", result.error)
                break

        if iteration >= self.max_iterations:
            return Fail(error=f"Max iterations ({self.max_iterations}) exceeded")

        return result

    @staticmethod
    def _find_node_index(graph: WorkflowGraph, node_id: str) -> int | None:
        """Find the index of a node in the graph's node list.

        Args:
            graph: The workflow graph to search.
            node_id: The ID of the node to find.

        Returns:
            The index of the node, or None if not found.
        """
        for idx, node in enumerate(graph.nodes):
            if node.node_id == node_id:
                return idx
        return None

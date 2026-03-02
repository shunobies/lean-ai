"""Simple tool dispatcher — maps tool names to handler functions."""

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    """Result of a tool execution."""

    success: bool
    output: str = ""
    error: str | None = None
    exit_code: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolExecutor:
    """Dispatches tool calls to registered handlers."""

    def __init__(self):
        self._handlers: dict[str, callable] = {}

    def register_handler(self, tool_name: str, handler: callable) -> None:
        """Register a handler function for a tool name."""
        self._handlers[tool_name] = handler

    async def execute(self, tool_name: str, params: dict[str, Any]) -> ToolResult:
        """Execute a tool by name with the given parameters."""
        handler = self._handlers.get(tool_name)
        if handler is None:
            return ToolResult(
                success=False,
                error=f"No handler registered for tool: {tool_name}",
            )

        start_time = time.monotonic()
        try:
            result = await handler(**params)
        except Exception as e:
            logger.exception("Tool execution failed: %s", tool_name)
            result = ToolResult(success=False, error=str(e))

        duration_ms = int((time.monotonic() - start_time) * 1000)
        logger.info(
            "Tool %s completed in %dms (success=%s)",
            tool_name, duration_ms, result.success,
        )
        return result

"""Unit tests for the WorkflowSession protocol and FastAPIWorkflowSession adapter.

Verifies that:
1. WorkflowSession is a valid Protocol with send, send_nowait, is_connected methods.
2. FastAPIWorkflowSession implements WorkflowSession.
3. An in-memory WorkflowSession implementation can capture sent messages for assertion.
4. ws_protocol.py imports without FastAPI.
"""

import asyncio
import sys

import pytest

from lean_ai.workflow.ws_protocol import WorkflowSession


class TestWorkflowSessionProtocol:
    """Test that WorkflowSession is a valid Protocol with the required methods."""

    def test_workflow_session_is_protocol(self):
        """WorkflowSession is a Protocol (not a regular class)."""
        from typing import Protocol

        assert issubclass(WorkflowSession, Protocol)

    def test_protocol_has_send_method(self):
        """WorkflowSession declares an async send method."""
        assert hasattr(WorkflowSession, "send")

    def test_protocol_has_send_nowait_method(self):
        """WorkflowSession declares a send_nowait method."""
        assert hasattr(WorkflowSession, "send_nowait")

    def test_protocol_has_is_connected_method(self):
        """WorkflowSession declares an is_connected method."""
        assert hasattr(WorkflowSession, "is_connected")

    def test_protocol_send_is_coroutine(self):
        """WorkflowSession.send is an async method."""
        import inspect

        send = getattr(WorkflowSession, "send")
        assert inspect.iscoroutinefunction(send)

    def test_protocol_send_nowait_is_not_coroutine(self):
        """WorkflowSession.send_nowait is a sync method."""
        import inspect

        send_nowait = getattr(WorkflowSession, "send_nowait")
        assert not inspect.iscoroutinefunction(send_nowait)

    def test_protocol_is_connected_is_not_coroutine(self):
        """WorkflowSession.is_connected is a sync method."""
        import inspect

        is_connected = getattr(WorkflowSession, "is_connected")
        assert not inspect.iscoroutinefunction(is_connected)


class TestFastAPIWorkflowSessionImplementsProtocol:
    """Test that FastAPIWorkflowSession satisfies the WorkflowSession Protocol."""

    def test_fastapi_adapter_has_send(self):
        """FastAPIWorkflowSession exposes an async send method."""
        from lean_ai.workflow.ws_handler import FastAPIWorkflowSession

        assert hasattr(FastAPIWorkflowSession, "send")

    def test_fastapi_adapter_has_send_nowait(self):
        """FastAPIWorkflowSession exposes a send_nowait method."""
        from lean_ai.workflow.ws_handler import FastAPIWorkflowSession

        assert hasattr(FastAPIWorkflowSession, "send_nowait")

    def test_fastapi_adapter_has_is_connected(self):
        """FastAPIWorkflowSession exposes an is_connected method."""
        from lean_ai.workflow.ws_handler import FastAPIWorkflowSession

        assert hasattr(FastAPIWorkflowSession, "is_connected")

    def test_fastapi_adapter_send_is_async(self):
        """FastAPIWorkflowSession.send is a coroutine function."""
        import inspect

        from lean_ai.workflow.ws_handler import FastAPIWorkflowSession

        assert inspect.iscoroutinefunction(FastAPIWorkflowSession.send)

    def test_fastapi_adapter_send_nowait_is_sync(self):
        """FastAPIWorkflowSession.send_nowait is a regular function."""
        import inspect

        from lean_ai.workflow.ws_handler import FastAPIWorkflowSession

        assert not inspect.iscoroutinefunction(FastAPIWorkflowSession.send_nowait)

    def test_fastapi_adapter_is_connected_is_sync(self):
        """FastAPIWorkflowSession.is_connected is a regular function."""
        import inspect

        from lean_ai.workflow.ws_handler import FastAPIWorkflowSession

        assert not inspect.iscoroutinefunction(FastAPIWorkflowSession.is_connected)


class TestInMemoryWorkflowSession:
    """Test an in-memory WorkflowSession mock that captures sent messages.

    This demonstrates the testability benefit of the abstraction:
    callers can inject a mock WorkflowSession to assert on messages
    without needing a real WebSocket transport.
    """

    def _make_session(self):
        """Create an in-memory WorkflowSession implementation."""

        class InMemorySession:
            """Minimal in-memory WorkflowSession for testing."""

            def __init__(self):
                self.sent: list[dict] = []
                self._connected = True

            async def send(self, data: dict[str, object]) -> None:
                self.sent.append(data)

            def send_nowait(self, data: dict[str, object]) -> None:
                asyncio.create_task(self.send(data))

            def is_connected(self) -> bool:
                return self._connected

        return InMemorySession()

    def test_in_memory_session_has_protocol_methods(self):
        """An in-memory mock has all WorkflowSession protocol methods."""
        session = self._make_session()
        # Structural typing: verify the mock has the same shape as WorkflowSession
        assert hasattr(session, "send")
        assert hasattr(session, "send_nowait")
        assert hasattr(session, "is_connected")

    @pytest.mark.asyncio
    async def test_in_memory_session_captures_send(self):
        """In-memory session records messages sent via send()."""
        session = self._make_session()
        await session.send({"type": "stage_change", "stage": "planning"})
        assert len(session.sent) == 1
        assert session.sent[0]["type"] == "stage_change"
        assert session.sent[0]["stage"] == "planning"

    @pytest.mark.asyncio
    async def test_in_memory_session_captures_multiple_sends(self):
        """In-memory session records multiple messages in order."""
        session = self._make_session()
        await session.send({"type": "stage_change", "stage": "planning"})
        await session.send({"type": "error", "message": "oops"})
        await session.send({"type": "complete"})

        assert len(session.sent) == 3
        assert session.sent[0]["type"] == "stage_change"
        assert session.sent[1]["type"] == "error"
        assert session.sent[2]["type"] == "complete"

    def test_in_memory_session_is_connected_true(self):
        """In-memory session reports connected by default."""
        session = self._make_session()
        assert session.is_connected() is True

    def test_in_memory_session_is_connected_false(self):
        """In-memory session can report disconnected."""
        session = self._make_session()
        session._connected = False
        assert session.is_connected() is False

    @pytest.mark.asyncio
    async def test_in_memory_session_send_nowait_captures(self):
        """In-memory session captures fire-and-forget messages via send_nowait."""
        session = self._make_session()
        session.send_nowait({"type": "tool_progress", "tool": "read_file"})
        # Fire-and-forget uses create_task — give the event loop a tick
        await asyncio.sleep(0.01)
        assert len(session.sent) == 1
        assert session.sent[0]["type"] == "tool_progress"


class TestWsProtocolImportsWithoutFastAPI:
    """Test that ws_protocol.py can be imported without FastAPI."""

    def test_import_without_fastapi(self):
        """ws_protocol.py imports successfully even when fastapi is not available."""
        # Remove fastapi from sys.modules temporarily to verify ws_protocol
        # does not depend on it.
        fastapi_modules = [m for m in sys.modules if m == "fastapi" or m.startswith("fastapi.")]
        saved = {}
        for mod in fastapi_modules:
            saved[mod] = sys.modules.pop(mod, None)

        try:
            # Force reimport by removing cached ws_protocol modules
            for mod in list(sys.modules):
                if "ws_protocol" in mod:
                    del sys.modules[mod]

            # This should succeed without fastapi in sys.modules
            from lean_ai.workflow import ws_protocol

            assert hasattr(ws_protocol, "WorkflowSession")
            assert hasattr(ws_protocol, "StageChangeMessage")
            assert hasattr(ws_protocol, "ErrorMessage")
        finally:
            # Restore fastapi modules
            for mod, val in saved.items():
                sys.modules[mod] = val

"""WebSocket message handling utilities.

Provides convenience send helpers that operate on the framework-agnostic
``WorkflowSession`` protocol, plus a ``FastAPIWorkflowSession`` adapter
that wraps a raw FastAPI ``WebSocket``.
"""

import asyncio
import logging
from typing import Any, Protocol

from lean_ai.workflow.ws_protocol import WorkflowSession

logger = logging.getLogger(__name__)


# ── FastAPI adapter ──────────────────────────────────────────────


class _WebSocketLike(Protocol):
    """Small structural interface used by the FastAPI adapter."""

    client: Any

    async def send_json(self, data: dict[str, object]) -> None: ...


class FastAPIWorkflowSession:
    """Wraps a raw FastAPI ``WebSocket`` to satisfy ``WorkflowSession``."""

    def __init__(self, ws: _WebSocketLike) -> None:
        self._ws = ws

    async def send(self, data: dict[str, object]) -> None:
        """Send a JSON message, suppressing transport errors."""
        try:
            await self._ws.send_json(data)
        except Exception:
            logger.warning("Failed to send WS message: %s", data.get("type"))

    def send_nowait(self, data: dict[str, object]) -> None:
        """Fire-and-forget send backed by an asyncio task."""
        asyncio.create_task(self.send(data))

    def is_connected(self) -> bool:
        """Return True if the underlying WebSocket has not closed."""
        return self._ws.client is not None


# ── Convenience helpers (accept WorkflowSession) ─────────────────


async def ws_send(session: WorkflowSession, msg_type: str, data: dict | None = None) -> None:
    """Send a typed WebSocket message (awaited — blocks until queued)."""
    if session is None:
        return
    payload = {"type": msg_type, **(data or {})}
    if hasattr(session, "send"):
        await session.send(payload)
        return
    if hasattr(session, "send_json"):
        await session.send_json(payload)
        return
    raise AttributeError("session does not support send() or send_json()")


def ws_send_nowait(session: WorkflowSession, msg_type: str, data: dict | None = None) -> None:
    """Fire-and-forget WebSocket send for non-critical progress messages."""
    if session is None:
        return
    asyncio.create_task(_ws_send_quiet(session, msg_type, data))


async def _ws_send_quiet(
    session: WorkflowSession,
    msg_type: str,
    data: dict | None = None,
) -> None:
    """Send with suppressed errors — used by fire-and-forget tasks."""
    try:
        await ws_send(session, msg_type, data)
    except Exception:
        logger.debug("Fire-and-forget WS send failed: %s", msg_type)


def is_connected(session: WorkflowSession) -> bool:
    """Return True if the session's transport is still open."""
    if session is None:
        return False
    if hasattr(session, "is_connected"):
        return session.is_connected()
    if hasattr(session, "client"):
        return session.client is not None
    return True

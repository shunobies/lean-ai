"""WebSocket message handling utilities."""

import asyncio
import logging

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


async def ws_send(ws: WebSocket, msg_type: str, data: dict | None = None) -> None:
    """Send a typed WebSocket message (awaited — blocks until queued)."""
    payload = {"type": msg_type, **(data or {})}
    try:
        await ws.send_json(payload)
    except Exception:
        logger.warning("Failed to send WS message: %s", msg_type)


def ws_send_nowait(ws: WebSocket, msg_type: str, data: dict | None = None) -> None:
    """Fire-and-forget WebSocket send for non-critical progress messages."""
    asyncio.create_task(_ws_send_quiet(ws, msg_type, data))


async def _ws_send_quiet(
    ws: WebSocket, msg_type: str, data: dict | None = None,
) -> None:
    """Send with suppressed errors — used by fire-and-forget tasks."""
    payload = {"type": msg_type, **(data or {})}
    try:
        await ws.send_json(payload)
    except Exception:
        logger.debug("Fire-and-forget WS send failed: %s", msg_type)


async def safe_receive(ws: WebSocket) -> dict | None:
    """Receive a JSON message, return None on disconnect."""
    try:
        return await ws.receive_json()
    except WebSocketDisconnect:
        return None
    except Exception as e:
        logger.warning("WS receive error: %s", e)
        return None

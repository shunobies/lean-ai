"""WebSocket message handling utilities."""

import logging

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


async def ws_send(ws: WebSocket, msg_type: str, data: dict | None = None) -> None:
    """Send a typed WebSocket message."""
    payload = {"type": msg_type, **(data or {})}
    try:
        await ws.send_json(payload)
    except Exception:
        logger.warning("Failed to send WS message: %s", msg_type)


async def safe_receive(ws: WebSocket) -> dict | None:
    """Receive a JSON message, return None on disconnect."""
    try:
        return await ws.receive_json()
    except WebSocketDisconnect:
        return None
    except Exception as e:
        logger.warning("WS receive error: %s", e)
        return None

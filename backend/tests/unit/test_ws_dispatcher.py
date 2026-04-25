"""Tests for WSMessageDispatcher queue overflow and cancel-safety."""

import asyncio

import pytest

from lean_ai.workflow.ws_dispatcher import (
    WorkflowCancelledError,
    WSMessageDispatcher,
)


class FakeWebSocket:
    """Minimal WebSocket stub that yields queued messages."""

    def __init__(self, messages: list[dict] | None = None):
        self._messages: asyncio.Queue[dict] = asyncio.Queue()
        for m in messages or []:
            self._messages.put_nowait(m)
        self.sent: list[dict] = []

    async def receive_json(self) -> dict:
        return await self._messages.get()

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    def inject(self, msg: dict) -> None:
        """Add a message for the dispatcher to receive."""
        self._messages.put_nowait(msg)


# ── QueueFull resilience ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_safe_put_drops_oldest_on_overflow():
    """Listener survives when the approval queue is full."""
    ws = FakeWebSocket()
    dispatcher = WSMessageDispatcher(ws)
    # Shrink queue to 1 for easy overflow
    dispatcher._approval_queue = asyncio.Queue(maxsize=1)

    # Pre-fill the queue
    dispatcher._approval_queue.put_nowait({"type": "old_msg"})

    # _safe_put should drop the old message and insert the new one
    dispatcher._safe_put(
        dispatcher._approval_queue,
        {"type": "new_msg"},
    )

    msg = dispatcher._approval_queue.get_nowait()
    assert msg["type"] == "new_msg"


@pytest.mark.asyncio
async def test_listener_survives_queue_overflow():
    """Listener keeps running when queue overflows."""
    ws = FakeWebSocket()
    dispatcher = WSMessageDispatcher(ws)
    # Tiny queue
    dispatcher._approval_queue = asyncio.Queue(maxsize=1)

    await dispatcher.start()
    try:
        # Send 5 approve messages — queue can only hold 1
        for i in range(5):
            ws.inject({"type": "approve", "seq": i})
        # Give the listener time to process
        await asyncio.sleep(0.05)

        # Listener should still be alive
        assert not dispatcher._listener_task.done()

        # Should be able to get at least the latest message
        msg = dispatcher._approval_queue.get_nowait()
        assert msg["type"] == "approve"
    finally:
        await dispatcher.stop()


# ── Cancel-safe wait_for_approval ────────────────────────────────


@pytest.mark.asyncio
async def test_wait_for_approval_unblocks_on_cancel():
    """wait_for_approval raises when cancel fires while blocked."""
    ws = FakeWebSocket()
    dispatcher = WSMessageDispatcher(ws)

    async def cancel_after_delay():
        await asyncio.sleep(0.02)
        dispatcher._cancel_event.set()

    asyncio.create_task(cancel_after_delay())

    with pytest.raises(WorkflowCancelledError):
        await dispatcher.wait_for_approval()


@pytest.mark.asyncio
async def test_wait_for_approval_returns_message():
    """Normal message retrieval still works."""
    ws = FakeWebSocket()
    dispatcher = WSMessageDispatcher(ws)
    dispatcher._approval_queue.put_nowait({"type": "approve"})

    msg = await dispatcher.wait_for_approval()
    assert msg["type"] == "approve"


@pytest.mark.asyncio
async def test_wait_for_approval_raises_on_cancel_message():
    """A cancel message in the queue raises WorkflowCancelledError."""
    ws = FakeWebSocket()
    dispatcher = WSMessageDispatcher(ws)
    dispatcher._approval_queue.put_nowait({"type": "cancel"})

    with pytest.raises(WorkflowCancelledError):
        await dispatcher.wait_for_approval()


# ── Listener health check ───────────────────────────────────────


@pytest.mark.asyncio
async def test_check_cancelled_detects_dead_listener():
    """check_cancelled raises when listener died from an exception."""
    ws = FakeWebSocket()
    dispatcher = WSMessageDispatcher(ws)

    # Create a task that immediately fails
    async def die():
        raise RuntimeError("boom")

    dispatcher._listener_task = asyncio.create_task(die())
    await asyncio.sleep(0.01)  # Let it fail

    with pytest.raises(WorkflowCancelledError):
        dispatcher.check_cancelled()


@pytest.mark.asyncio
async def test_check_cancelled_ignores_healthy_listener():
    """check_cancelled does not raise when listener is alive."""
    ws = FakeWebSocket()
    dispatcher = WSMessageDispatcher(ws)
    await dispatcher.start()
    try:
        # Should not raise
        dispatcher.check_cancelled()
    finally:
        await dispatcher.stop()


# ── Ping handling ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ping_receives_pong():
    """Ping messages get an immediate pong reply."""
    ws = FakeWebSocket()
    dispatcher = WSMessageDispatcher(ws)
    await dispatcher.start()
    try:
        ws.inject({"type": "ping"})
        await asyncio.sleep(0.02)
        assert any(m.get("type") == "pong" for m in ws.sent)
    finally:
        await dispatcher.stop()

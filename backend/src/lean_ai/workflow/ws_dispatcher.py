"""Centralized WebSocket message router for concurrent message handling.

Replaces direct ``safe_receive(ws)`` calls with queue-based routing so
cancel and user-interrupt messages can be received while the workflow
is actively running.
"""

import asyncio
import logging

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class WorkflowCancelledError(Exception):
    """Raised when the user cancels the running workflow."""


class WSMessageDispatcher:
    """Route incoming WebSocket messages to typed async queues.

    During clarification and plan-approval phases, ``user_message``
    messages are routed to the approval queue (they are responses).
    Call :meth:`enter_execution_mode` before the execution phase so
    that subsequent ``user_message`` messages become mid-workflow
    interrupts instead.

    Lifecycle::

        dispatcher = WSMessageDispatcher(websocket)
        await dispatcher.start()
        try:
            await run_workflow(..., dispatcher=dispatcher)
        finally:
            await dispatcher.stop()
    """

    def __init__(self, websocket: WebSocket) -> None:
        self.ws = websocket
        self._cancel_event = asyncio.Event()
        self._user_messages: asyncio.Queue[dict] = asyncio.Queue(maxsize=1000)
        self._approval_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1000)
        self._listener_task: asyncio.Task | None = None
        self._execution_mode = False

    # ── Lifecycle ──────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background listener task."""
        self._listener_task = asyncio.create_task(self._listen())

    async def stop(self) -> None:
        """Stop the background listener (safe to call multiple times)."""
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        self._listener_task = None

    def enter_execution_mode(self) -> None:
        """Switch to execution mode.

        After this call, incoming ``user_message`` messages are routed
        to the interrupt queue (consumed by ``get_pending_message``)
        instead of the approval queue.
        """
        self._execution_mode = True

    # ── Queue helpers ──────────────────────────────────────────────

    def _safe_put(self, queue: asyncio.Queue, data: dict) -> None:
        """Enqueue *data* without crashing on overflow.

        On ``QueueFull``, drops the oldest item and retries once.
        If still full, logs an error and moves on — the listener
        must never crash from a full queue.
        """
        try:
            queue.put_nowait(data)
        except asyncio.QueueFull:
            logger.warning(
                "WSMessageDispatcher: queue full (size=%d), dropping oldest message to make room",
                queue.maxsize,
            )
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                logger.error(
                    "WSMessageDispatcher: still full after drop, message lost: %s",
                    data.get("type", "?"),
                )

    # ── Background listener ───────────────────────────────────────

    async def _listen(self) -> None:
        """Read WS messages in a loop and dispatch to queues."""
        try:
            while True:
                data = await self.ws.receive_json()
                msg_type = data.get("type")

                if msg_type == "cancel":
                    logger.info("Cancel requested by user")
                    self._cancel_event.set()
                    # Also unblock anyone waiting on the approval queue
                    self._safe_put(self._approval_queue, {"type": "cancel"})
                elif msg_type == "user_message":
                    if self._execution_mode:
                        self._safe_put(self._user_messages, data)
                    else:
                        self._safe_put(self._approval_queue, data)
                elif msg_type == "ping":
                    # Handle keepalive inline — no need to route
                    try:
                        await self.ws.send_json({"type": "pong"})
                    except Exception:
                        pass
                else:
                    # approve, approve_tool, deny_tool, feedback, etc.
                    self._safe_put(self._approval_queue, data)
        except WebSocketDisconnect:
            logger.info("WebSocket disconnected in dispatcher listener")
            # Signal cancel so any waiting coroutines unblock
            self._cancel_event.set()
            self._safe_put(self._approval_queue, {"type": "cancel"})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unexpected error in dispatcher listener")
            self._cancel_event.set()
            self._safe_put(self._approval_queue, {"type": "cancel"})

    # ── Cancellation ──────────────────────────────────────────────

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def check_cancelled(self) -> None:
        """Raise ``WorkflowCancelledError`` if cancelled or listener died."""
        if self._cancel_event.is_set():
            raise WorkflowCancelledError()
        # Detect silently-dead listener (e.g. unhandled exception)
        if self._listener_task and self._listener_task.done():
            exc = self._listener_task.exception()
            if exc and not isinstance(exc, asyncio.CancelledError):
                logger.error("Dispatcher listener died: %s", exc)
                raise WorkflowCancelledError()

    # ── User message injection ────────────────────────────────────

    def get_pending_message(self) -> str | None:
        """Non-blocking check for a user interrupt message.

        Returns the message content string, or ``None`` if no message
        is waiting.  Only returns messages when in execution mode.
        """
        try:
            msg = self._user_messages.get_nowait()
            return msg.get("content", "")
        except asyncio.QueueEmpty:
            return None

    # ── Approval wait (replaces safe_receive) ─────────────────────

    async def wait_for_approval(self) -> dict | None:
        """Block until an approval-type message arrives, or cancel.

        Returns the message dict, or ``None`` on disconnect / cancel.
        Raises ``WorkflowCancelledError`` if the user pressed stop.

        Uses ``asyncio.wait`` to race the queue against the cancel
        event, preventing deadlocks when cancel fires while blocked.
        """
        if self._cancel_event.is_set():
            raise WorkflowCancelledError()

        # Race: either a message arrives or cancel is signalled
        cancel_waiter = asyncio.ensure_future(self._cancel_event.wait())
        queue_getter = asyncio.ensure_future(self._approval_queue.get())

        done, pending = await asyncio.wait(
            {cancel_waiter, queue_getter},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        if queue_getter in done:
            msg = queue_getter.result()
            if msg.get("type") == "cancel":
                raise WorkflowCancelledError()
            return msg

        # Cancel event won the race
        raise WorkflowCancelledError()

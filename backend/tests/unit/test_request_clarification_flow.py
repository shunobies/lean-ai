"""Tests request-mode clarification pause/resume tool wiring."""

import asyncio

import pytest

from lean_ai.workflow.tool_executor import make_tool_executor
from lean_ai.workflow.ws_dispatcher import WSMessageDispatcher


class _FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_request_clarification_emits_waits_and_returns_answer(tmp_path):
    ws = _FakeWS()
    dispatcher = WSMessageDispatcher(ws)  # start() not needed for direct queue use
    executor = make_tool_executor(str(tmp_path), ws, session_id="s1", dispatcher=dispatcher)

    async def _inject_answer():
        await asyncio.sleep(0.01)
        dispatcher._user_messages.put_nowait(
            {"type": "user_message", "content": "Use PostgreSQL only."},
        )

    task = asyncio.create_task(_inject_answer())
    result = await executor(
        "request_clarification",
        {"question": "Should we support SQLite or only PostgreSQL?"},
    )
    await task

    assert result == "Use PostgreSQL only."
    assert any(m.get("type") == "clarification_needed" for m in ws.sent)

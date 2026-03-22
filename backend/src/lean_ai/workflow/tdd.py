"""TDD mode support: expert test writing and dispute resolution.

When TDD mode is enabled, the expert model writes all tests first and the
primary model implements code to make them pass.  The primary model cannot
edit test files but can dispute flawed tests through ``request_test_change``.
This module provides the dispute evaluation logic.
"""

import logging
from typing import TYPE_CHECKING

from lean_ai.config import settings
from lean_ai.llm.tool_definitions import IMPLEMENTATION_TOOLS
from lean_ai.workflow.ws_handler import ws_send_nowait

if TYPE_CHECKING:
    from fastapi import WebSocket

    from lean_ai.llm.facade import LLMClient
    from lean_ai.workflow.ws_dispatcher import WSMessageDispatcher

logger = logging.getLogger(__name__)


DISPUTE_EVALUATION_PROMPT = """\
Evaluate a test dispute from the implementation model.  The implementor \
claims a test is flawed and cannot be satisfied by a correct implementation.

RULES:
1. Read the test file to see the current test code.
2. Evaluate the implementor's reason carefully and objectively.
3. If the dispute is VALID (the test has a genuine flaw — wrong assertion, \
tests an implementation detail, impossible precondition, wrong import path, \
tests out-of-scope behavior):
   - Fix the test using edit_file.  Preserve the test's documentation \
(docstrings, comments) and update them to reflect the fix.
   - Call task_complete with a summary starting with "ACCEPTED: " followed \
by what you changed and why.
4. If the dispute is INVALID (the test is correct and the implementor \
needs to find a different approach):
   - Call task_complete with a summary starting with "REJECTED: " followed \
by: why the test is correct, what behaviour the test is validating, and \
what implementation approach would satisfy it.
5. Do NOT add new tests or remove existing ones — only fix the disputed \
test function if the dispute is valid.
"""


async def evaluate_test_dispute(
    *,
    test_file: str,
    test_function: str,
    reason: str,
    repo_root: str,
    expert_client: "LLMClient",
    ws: "WebSocket",
    session_id: str = "",
    dispatcher: "WSMessageDispatcher | None" = None,
    plan_context: str = "",
    step_artifacts: dict[str, str] | None = None,
) -> str:
    """Run the expert model to evaluate a test dispute.

    Returns a string result for the primary model indicating whether the
    dispute was accepted or rejected, with explanation.
    """
    from lean_ai.workflow.tool_executor import make_tool_executor

    tool_executor = make_tool_executor(
        repo_root, ws, session_id,
        llm_client=expert_client,
        dispatcher=dispatcher,
    )

    # Build context with relevant artifacts from implementation so far
    artifact_context = ""
    if step_artifacts:
        relevant_parts: list[str] = []
        for path, content in step_artifacts.items():
            if len(relevant_parts) < 5:
                truncated = content[:4000]
                relevant_parts.append(f"--- {path} ---\n{truncated}")
        if relevant_parts:
            artifact_context = (
                "\n\nImplementation files created so far:\n"
                + "\n".join(relevant_parts)
            )

    messages = [
        {"role": "system", "content": DISPUTE_EVALUATION_PROMPT},
        {
            "role": "user",
            "content": (
                f"DISPUTED TEST FILE: {test_file}\n"
                f"DISPUTED TEST FUNCTION: {test_function}\n"
                f"IMPLEMENTOR'S REASON: {reason}\n"
                f"{artifact_context}\n\n"
                f"Plan context:\n{plan_context[:2000]}"
            ),
        },
    ]

    async def on_tool_call(name: str, args: dict) -> None:
        ws_send_nowait(ws, "tool_progress", {
            "tool": name,
            "status": "running",
            "description": f"[TDD dispute] {name} {args.get('path', '')}",
        })

    async def on_tool_result(name: str, result: str) -> None:
        is_error = result.startswith("ERROR:")
        ws_send_nowait(ws, "tool_progress", {
            "tool": name,
            "status": "error" if is_error else "complete",
            "output": result[:500],
        })

    async def on_content(text: str) -> None:
        ws_send_nowait(ws, "assistant_content", {
            "content": f"[TDD dispute] {text}",
        })

    executed, explanation = await expert_client.chat_with_tools(
        messages=messages,
        tools=IMPLEMENTATION_TOOLS,
        tool_executor_fn=tool_executor,
        max_turns=10,
        max_tokens=settings.implementation_max_tokens,
        on_tool_call=on_tool_call,
        on_tool_result=on_tool_result,
        on_content=on_content,
        dispatcher=dispatcher,
    )

    explanation = explanation.strip()

    if explanation.upper().startswith("ACCEPTED:"):
        logger.info(
            "TDD dispute ACCEPTED for %s::%s", test_file, test_function,
        )
        return (
            f"DISPUTE ACCEPTED — The expert agreed and fixed the test.\n"
            f"Explanation: {explanation}\n"
            f"The test file has been updated. Continue implementing."
        )
    else:
        logger.info(
            "TDD dispute REJECTED for %s::%s", test_file, test_function,
        )
        return (
            f"DISPUTE REJECTED — The test is correct as written.\n"
            f"Explanation: {explanation}\n"
            f"You must find a different implementation approach to "
            f"satisfy this test."
        )

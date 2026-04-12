"""Utility functions for the 5-phase planning pipeline.

Pure helpers, WebSocket stage signaling, and standalone planning
functions (assess_clarity, _revise_plan) that don't participate
in the main create_plan orchestration.
"""

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import WebSocket

from lean_ai.config import settings
from lean_ai.llm.plan_schema import (
    IMPLEMENTATION_STEP_TOOLS,
    ExecutionPlan,
)
from lean_ai.llm.prompts import (
    CLARIFICATION_SYSTEM_PROMPT,
    PLAN_ASSEMBLY_SYSTEM_PROMPT,
)

if TYPE_CHECKING:
    from lean_ai.llm.facade import LLMClient

logger = logging.getLogger(__name__)

# Phase 4/5 produce structured JSON plans with enriched step instructions
# and context fields — give them 40% of the expert context window so
# detailed plans are not truncated (vs. the default 25% for general output).
PLAN_OUTPUT_PERCENT = 0.40

# Memory context budget: 2% of context window
MEMORY_CONTEXT_PERCENT = 0.02


def _retrieve_session_memories(repo_root: str, task: str) -> str:
    """Retrieve relevant memories from previous sessions.

    Returns a formatted string to append to the Phase 1 user message,
    or an empty string if no memories are found.
    Budget-gated at MEMORY_CONTEXT_PERCENT of the active context window.
    """
    try:
        from lean_ai.memory.index import search_memories

        results = search_memories(repo_root, task, limit=5)
        if not results:
            return ""

        budget = int(
            settings._active_context_window * MEMORY_CONTEXT_PERCENT * 3.5
        )

        lines = ["\n\nWORKSPACE MEMORY (from previous sessions):"]
        used = len(lines[0])
        for r in results:
            category = r.get("category") or "general"
            content = r["content"]
            line = f"\n- [{category}] {content}"
            if used + len(line) > budget:
                break
            lines.append(line)
            used += len(line)

        if len(lines) <= 1:
            return ""

        memory_text = "".join(lines)
        logger.info(
            "Injected %d memories into Phase 1 (%d chars)",
            len(lines) - 1, len(memory_text),
        )
        return memory_text
    except Exception:
        logger.debug("Memory retrieval failed (non-fatal)", exc_info=True)
        return ""


def _save_debug_phase(
    repo_root: str,
    session_id: str,
    phase_name: str,
    content: str,
    elapsed: float,
) -> None:
    """Save a planning phase output to disk when debug_planning is enabled."""
    if not settings.debug_planning or not session_id:
        return
    debug_dir = Path(repo_root) / ".lean_ai" / "plan_debug" / session_id
    debug_dir.mkdir(parents=True, exist_ok=True)
    (debug_dir / f"{phase_name}.md").write_text(content, encoding="utf-8")
    logger.info(
        "Debug: saved %s (%d chars, %.1fs)", phase_name, len(content), elapsed,
    )


def _extract_file_paths(scan_output: str, repo_root: str) -> list[str]:
    """Extract file paths from Phase 2a scan output.

    Looks for lines containing path-like strings (with / separators
    and common extensions).  Validates that each path exists on disk.
    """
    import re
    path_pattern = re.compile(
        r'(?:^|[\s`"\'\-•])([a-zA-Z0-9_.][a-zA-Z0-9_./\-]*'
        r'\.[a-zA-Z]{1,10})(?:[\s`"\'\-:,]|$)',
    )
    seen: set[str] = set()
    paths: list[str] = []
    for line in scan_output.splitlines():
        for match in path_pattern.finditer(line):
            candidate = match.group(1).strip()
            if candidate in seen or "/" not in candidate:
                continue
            full = Path(repo_root) / candidate
            if full.is_file():
                seen.add(candidate)
                paths.append(candidate)
    return paths


def _split_list(items: list, n: int) -> list[list]:
    """Split *items* into *n* roughly equal chunks."""
    if n <= 1:
        return [items]
    chunk_size = max(1, len(items) // n)
    chunks: list[list] = []
    for i in range(0, len(items), chunk_size):
        chunks.append(items[i:i + chunk_size])
    # Merge trailing runt into last real chunk
    while len(chunks) > n:
        chunks[-2].extend(chunks[-1])
        chunks.pop()
    return chunks


async def _send_stage(
    ws: WebSocket | None,
    summary: str,
    model: str | None = None,
    phase: int | None = None,
) -> None:
    """Send a planning stage_status running message if WebSocket is available."""
    if ws is None:
        return
    from lean_ai.workflow.ws_handler import ws_send
    payload: dict = {
        "stage": "planning", "status": "running", "summary": summary,
    }
    if model:
        payload["model"] = model
    if phase is not None:
        payload["phase"] = phase
    await ws_send(ws, "stage_status", payload)


async def _send_stage_done(
    ws: WebSocket | None,
    summary: str,
    model: str | None = None,
    phase: int | None = None,
) -> None:
    """Send a planning stage_status done message if WebSocket is available."""
    if ws is None:
        return
    from lean_ai.workflow.ws_handler import ws_send
    payload: dict = {
        "stage": "planning", "status": "done", "summary": summary,
    }
    if model:
        payload["model"] = model
    if phase is not None:
        payload["phase"] = phase
    await ws_send(ws, "stage_status", payload)


async def _send_content_done(
    ws: WebSocket | None,
    text: str,
) -> None:
    """Signal that content streaming for a planning phase is complete."""
    if ws is None:
        return
    from lean_ai.workflow.ws_handler import ws_send_nowait
    ws_send_nowait(ws, "assistant_content", {"content": text, "done": True})


async def _extract_missing_files(
    risks: str,
    llm_client: "LLMClient",
) -> str:
    """Extract the missing file list from the gap analysis output.

    Returns a short bullet list of files that the gap analysis identified
    as required but missing from the plan, or empty string if none.
    """
    result = await llm_client.chat_raw(
        messages=[
            {
                "role": "user",
                "content": (
                    "From the risk assessment below, extract ONLY the "
                    "files identified as MISSING — files that are REQUIRED "
                    "for the app to work at runtime but are NOT yet in the "
                    "change design.\n\n"
                    "Output a simple numbered list:\n"
                    "1. file/path — what it is and why it is needed\n\n"
                    "If no missing files were identified, output: NONE\n\n"
                    f"RISK ASSESSMENT:\n{risks}"
                ),
            },
        ],
        max_tokens=1024,
    )
    stripped = result.strip()
    if stripped.upper() == "NONE" or len(stripped) < 10:
        return ""
    return stripped


async def _compact_file_summary(
    file_summary: str,
    llm_client: "LLMClient",
    ctx_window: int,
) -> str:
    """Compact file_summary to fit within a token budget at small context windows.

    Uses the explorer (request) model — code understanding is needed here.
    Target budget: 20% of the context window in characters (~= tokens * 3.5).
    Returns the original if it already fits.
    """
    budget = int(ctx_window * 0.20 * 3.5)
    if len(file_summary) <= budget:
        return file_summary

    logger.info(
        "Compacting file_summary: %d chars -> target %d chars",
        len(file_summary), budget,
    )

    try:
        compacted = await llm_client.chat_raw(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Compress the codebase exploration output below. "
                        "Preserve ALL of the following:\n"
                        "- File paths and whether they are create/edit/"
                        "reference\n"
                        "- Function/class signatures with parameters\n"
                        "- Import statements\n"
                        "- VERIFIED REFERENCES and MISSING INFRASTRUCTURE "
                        "sections\n"
                        "- Key constants and configuration values\n\n"
                        "Remove:\n"
                        "- Inline code blocks longer than 5 lines\n"
                        "- Verbose explanations of file purposes\n"
                        "- Redundant observations\n\n"
                        "Output the compressed version directly."
                    ),
                },
                {"role": "user", "content": file_summary[:budget * 3]},
            ],
            max_tokens=2048,
        )
        if compacted and len(compacted.strip()) > 200:
            logger.info(
                "File summary compacted: %d -> %d chars (%.0f%%)",
                len(file_summary), len(compacted),
                len(compacted) / len(file_summary) * 100,
            )
            return compacted.strip()
    except Exception:
        logger.warning(
            "File summary compaction failed, using truncation fallback",
            exc_info=True,
        )

    # Fallback: hard truncate
    return file_summary[:budget] + "\n... (truncated to fit context budget)"


async def assess_clarity(
    task: str,
    llm_client: "LLMClient",
    context: str = "",
) -> list[str] | None:
    """Assess whether a task is clear enough to plan.

    Returns None if the task is clear, or a list of clarifying questions.
    """
    logger.info("Assessing task clarity")

    response = await llm_client.chat_raw(
        messages=[
            {"role": "system", "content": CLARIFICATION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"TASK:\n{task}\n\n"
                    f"PROJECT CONTEXT:\n{context[:5000]}\n\n"
                    "Is this task clear enough to create a detailed "
                    "implementation plan?"
                ),
            },
        ],
        max_tokens=1024,
    )

    stripped = response.strip()
    upper = stripped.upper()
    if upper.startswith("CLEAR") or upper in ("OK", "YES", "N/A", "NONE", "-"):
        return None

    # Try to parse as JSON array of questions
    try:
        questions = json.loads(stripped)
        if isinstance(questions, list) and all(
            isinstance(q, str) for q in questions
        ):
            return questions[:5]
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: extract lines that look like questions
    lines = [
        ln.strip().lstrip("- ").lstrip("0123456789.)")
        for ln in stripped.splitlines()
        if ln.strip() and "?" in ln
    ]
    if lines:
        return lines[:5]
    # Short non-question responses (e.g. "-", "OK", "N/A") = task is clear
    if len(stripped) < 20 and "?" not in stripped:
        logger.info(
            "Treating short non-question response as CLEAR: %r", stripped,
        )
        return None
    return [stripped]


async def _revise_plan(
    task: str,
    revision_context: str,
    llm_client: "LLMClient",
    context: str = "",
    ws: WebSocket | None = None,
    expert_llm_client: "LLMClient | None" = None,
    on_thinking: "Callable | None" = None,
) -> ExecutionPlan:
    """Revise an existing plan based on user feedback.

    Args:
        task: The original task.
        revision_context: Previous plan JSON + user feedback.
        llm_client: LLM client.
        context: Project context.
        ws: Optional WebSocket for progress.
        expert_llm_client: Optional expert LLM client for reasoning-heavy work.

    Returns:
        Revised ExecutionPlan.
    """
    expert = expert_llm_client or llm_client
    expert_max_tokens = (
        settings.effective_expert_max_tokens
        if expert_llm_client
        else settings.ollama_max_tokens
    )
    expert_ctx = (
        settings.effective_expert_context_window
        if expert_llm_client
        else settings._active_context_window
    )
    plan_max_tokens = max(
        expert_max_tokens,
        int(expert_ctx * PLAN_OUTPUT_PERCENT),
    )
    await _send_stage(
        ws, "Revising plan based on feedback...", model=expert.model_name,
    )
    logger.info("Plan revision")
    plan = await expert.chat_structured(
        messages=[
            {"role": "system", "content": PLAN_ASSEMBLY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"TASK: {task}\n\n"
                    f"CODEBASE CONTEXT:\n{context}\n\n"
                    f"REVISION CONTEXT:\n{revision_context}\n\n"
                    "Revise the plan based on the user's feedback. "
                    "Make targeted edits — don't rewrite from scratch. "
                    "Keep the same structured format with step_number, "
                    "tool, file_path, instruction, and context fields."
                ),
            },
        ],
        schema=ExecutionPlan,
        max_tokens=plan_max_tokens,
        thinking_callback=on_thinking,
    )
    # Safety: strip non-implementation steps (same as Phase 4)
    impl_steps = [
        s for s in plan.steps if s.tool in IMPLEMENTATION_STEP_TOOLS
    ]
    if len(impl_steps) < len(plan.steps):
        stripped_count = len(plan.steps) - len(impl_steps)
        stripped_tools = [
            s.tool for s in plan.steps
            if s.tool not in IMPLEMENTATION_STEP_TOOLS
        ]
        logger.warning(
            "Stripped %d non-implementation steps from revised plan: %s",
            stripped_count, stripped_tools,
        )
        for i, step in enumerate(impl_steps, 1):
            step.step_number = i
        plan.steps = impl_steps
    logger.info(
        "Plan revised: %d steps, %d affected files",
        len(plan.steps), len(plan.affected_files),
    )
    return plan

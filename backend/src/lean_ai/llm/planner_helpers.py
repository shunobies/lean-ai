"""Utility functions for the 5-phase planning pipeline.

Pure helpers, WebSocket stage signaling, and the ``_revise_plan`` helper
that doesn't participate in the main ``create_plan`` orchestration.
Clarifications are handled upstream by the chat two-round Suggested
Agent Prompt flow; the planner never asks the user clarifying questions.
"""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import WebSocket

from lean_ai.config import settings
from lean_ai.llm.plan_schema import (
    IMPLEMENTATION_STEP_TOOLS,
    ExecutionPlan,
    ScopeAssumption,
    ScopeDocument,
)
from lean_ai.llm.prompt_registry import registry
from lean_ai.llm.prompts import PLAN_ASSEMBLY_SYSTEM_PROMPT

if TYPE_CHECKING:
    from lean_ai.llm.facade import LLMClient

logger = logging.getLogger(__name__)

# Phase 4/5 produce structured JSON plans with enriched step instructions
# and context fields — give them 40% of the expert context window so
# detailed plans are not truncated (vs. the default 25% for general output).
PLAN_OUTPUT_PERCENT = 0.40

# Memory context budget: 2% of context window
MEMORY_CONTEXT_PERCENT = 0.02


def _allowed_curation_statuses() -> set[str]:
    """Curation statuses allowed in retrieval, from settings."""
    raw = getattr(
        settings,
        "memory_retrieval_statuses",
        "user_confirmed,high_confidence_auto",
    )
    return {s.strip() for s in str(raw).split(",") if s.strip()}


async def _load_memory_rows(
    repo_root: str,
    memory_ids: list[str],
) -> dict[str, dict]:
    """Batch-load memory rows from the per-workspace DB."""
    if not memory_ids:
        return {}
    from lean_ai.db import get_db

    db = await get_db(repo_root)
    try:
        placeholders = ",".join("?" for _ in memory_ids)
        cursor = await db.execute(
            f"SELECT * FROM session_memories WHERE id IN ({placeholders})",
            tuple(memory_ids),
        )
        rows = await cursor.fetchall()
        return {row["id"]: dict(row) for row in rows}
    finally:
        await db.close()


def _format_memory_lines(
    rows: list[dict],
    header: str,
    budget: int,
) -> str:
    """Format memory rows into a context block, budget-gated."""
    lines = [header]
    used = len(lines[0])
    for r in rows:
        category = r.get("category") or "general"
        content = r.get("content") or ""
        line = f"\n- [{category}] {content}"
        if used + len(line) > budget:
            break
        lines.append(line)
        used += len(line)
    return "".join(lines) if len(lines) > 1 else ""


async def _retrieve_memories_for_phase(
    repo_root: str,
    query: str,
    *,
    phase_label: str,
    categories: list[str] | None = None,
    limit: int = 5,
    budget_percent: float = MEMORY_CONTEXT_PERCENT,
    header: str | None = None,
) -> str:
    """Retrieve filtered, curated memories and format them for prompt injection.

    Filters by `curation_status IN settings.memory_retrieval_statuses`,
    excludes expired memories, and optionally restricts to a category set.
    Budget-gated by *budget_percent* of the active context window.
    """
    try:
        from lean_ai.memory.index import search_memories

        hits = search_memories(repo_root, query, limit=limit * 3)
        if not hits:
            return ""

        rows = await _load_memory_rows(
            repo_root,
            [h["memory_id"] for h in hits],
        )
        allowed = _allowed_curation_statuses()
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        filtered: list[dict] = []
        for hit in hits:
            row = rows.get(hit["memory_id"])
            if row is None:
                continue
            status = row.get("curation_status") or "auto"
            if status not in allowed:
                continue
            expires = row.get("expires_at")
            if expires and expires <= now:
                continue
            if categories and row.get("category") not in categories:
                continue
            filtered.append(row)
            if len(filtered) >= limit:
                break

        if not filtered:
            return ""

        budget = int(settings._active_context_window * budget_percent * 3.5)
        header_text = header or (f"\n\nWORKSPACE MEMORY ({phase_label}):")
        text = _format_memory_lines(filtered, header_text, budget)
        if text:
            logger.info(
                "Injected %d memories into %s (%d chars)",
                len(filtered),
                phase_label,
                len(text),
            )
        return text
    except Exception:
        logger.debug(
            "Memory retrieval failed for %s (non-fatal)",
            phase_label,
            exc_info=True,
        )
        return ""


async def _retrieve_session_memories(repo_root: str, task: str) -> str:
    """Retrieve relevant memories for Phase 1 (scope/clarification).

    Returns a formatted string to append to the Phase 1 user message,
    or an empty string if no memories are found.
    """
    return await _retrieve_memories_for_phase(
        repo_root,
        task,
        phase_label="from previous sessions",
        header="\n\nWORKSPACE MEMORY (from previous sessions):",
    )


async def retrieve_design_memories(repo_root: str, query: str) -> str:
    """Retrieve `gotcha`, `convention`, `rejection` memories for Phase 3."""
    budget = getattr(
        settings,
        "phase3_memory_budget_percent",
        MEMORY_CONTEXT_PERCENT,
    )
    return await _retrieve_memories_for_phase(
        repo_root,
        query,
        phase_label="design hints",
        categories=["gotcha", "convention", "rejection"],
        limit=6,
        budget_percent=budget,
        header="\n\nWORKSPACE MEMORY (design hints from past sessions):",
    )


async def retrieve_fix_pattern_memories(repo_root: str, query: str) -> str:
    """Retrieve `fix_pattern` + `gotcha` memories for the validation fix loop."""
    budget = getattr(
        settings,
        "fix_loop_memory_budget_percent",
        MEMORY_CONTEXT_PERCENT,
    )
    return await _retrieve_memories_for_phase(
        repo_root,
        query,
        phase_label="fix hints",
        categories=["fix_pattern", "gotcha"],
        limit=5,
        budget_percent=budget,
        header="\n\nPAST FIX PATTERNS (from previous validation failures):",
    )


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
        "Debug: saved %s (%d chars, %.1fs)",
        phase_name,
        len(content),
        elapsed,
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
        chunks.append(items[i : i + chunk_size])
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
        "stage": "planning",
        "status": "running",
        "summary": summary,
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
        "stage": "planning",
        "status": "done",
        "summary": summary,
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
        len(file_summary),
        budget,
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
                {"role": "user", "content": file_summary[: budget * 3]},
            ],
            max_tokens=2048,
        )
        if compacted and len(compacted.strip()) > 200:
            logger.info(
                "File summary compacted: %d -> %d chars (%.0f%%)",
                len(file_summary),
                len(compacted),
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


def format_scope_document(scope: ScopeDocument) -> str:
    """Render a ``ScopeDocument`` to the 8-section markdown shape that the
    downstream Phase 2 / 3 / 4 prompts already consume as ``{scope}``.

    Section names and ordering match ``planning.scope_user`` so prompt
    consumers do not need to change. Empty sections still emit the heading
    with a placeholder bullet so downstream parsers see the shape.
    """
    lines: list[str] = []

    def _render_bullets(header: str, items: list[str]) -> None:
        lines.append(f"{header}:")
        if items:
            for item in items:
                lines.append(f"- {item}")
        else:
            lines.append("- (none identified)")
        lines.append("")

    lines.append("PROBLEM / PURPOSE:")
    lines.append(scope.problem.strip() or "(not specified)")
    lines.append("")

    _render_bullets("DELIVERABLES", scope.deliverables)
    _render_bullets("IN SCOPE", scope.in_scope)
    _render_bullets("OUT OF SCOPE", scope.out_of_scope)
    _render_bullets("DOWNSTREAM CONSUMERS", scope.downstream_consumers)

    lines.append("ASSUMPTIONS (with verification hints):")
    if scope.assumptions:
        for a in scope.assumptions:
            lines.append(f"- Assumption: {a.assumption} — verify: {a.verify_hint}")
    else:
        lines.append("- (none identified)")
    lines.append("")

    _render_bullets("SUCCESS CRITERIA", scope.success_criteria)
    _render_bullets("RISKS", scope.risks)

    return "\n".join(lines).rstrip() + "\n"


async def _synthesize_scope(
    *,
    task: str,
    context: str,
    exploration_prose: str,
    explorer: "LLMClient",
    phase_max_tokens: int,
    on_thinking: "Callable | None" = None,
) -> tuple[ScopeDocument, str, bool]:
    """Coerce the Phase 1 exploration prose into a validated ScopeDocument.

    Runs a single ``chat_structured`` pass against
    ``planning.scope_synthesis_system``. On validation failure, retries
    once with a corrective nudge. If both attempts fail, constructs a
    programmatic fallback ``ScopeDocument`` that wraps the task text so
    Phase 2 is guaranteed to receive the 8-section shape — never thin
    prose that looks like "let me check assumptions…".

    Returns ``(scope_obj, markdown, synthesized)`` where ``synthesized``
    is ``True`` when the structured call succeeded and ``False`` when the
    programmatic fallback kicked in (downstream can log / flag that).
    """
    synthesis_system = registry.get("planning.scope_synthesis_system")

    # Trim the project context slice so the synthesis prompt stays bounded —
    # the exploration loop already consumed the full context.
    context_slice = context[:8000] if context else ""

    base_payload_parts = [
        f"TASK: {task}",
    ]
    if context_slice:
        base_payload_parts.append(f"PROJECT CONTEXT:\n{context_slice}")
    if exploration_prose.strip():
        base_payload_parts.append(
            f"SCOPE ANALYSIS PROSE (from the Phase 1 tool loop):\n{exploration_prose}"
        )
    base_payload_parts.append(
        "Translate the inputs above into the ScopeDocument. Populate every "
        "field from the task text, context, and prose — when a field is "
        "not spelled out, write your most reasonable interpretation and "
        "add a matching assumption with a verify_hint."
    )
    base_payload = "\n\n".join(base_payload_parts)

    async def _attempt(payload: str) -> ScopeDocument:
        return await explorer.chat_structured(
            messages=[
                {"role": "system", "content": synthesis_system},
                {"role": "user", "content": payload},
            ],
            schema=ScopeDocument,
            max_tokens=phase_max_tokens,
            thinking_callback=on_thinking,
        )

    try:
        scope = await _attempt(base_payload)
    except Exception:
        logger.warning(
            "Phase 1 scope synthesis attempt 1 failed — retrying with corrective payload",
            exc_info=True,
        )
        retry_payload = (
            base_payload + "\n\nCORRECTION: your previous output did not conform to "
            "the ScopeDocument schema. This is a pure translation task: "
            "take the task text and rewrite it into the 8 schema fields. "
            "Populate every field — when a detail is not spelled out, "
            "write your best interpretation and record it as an "
            "assumption with a concrete verify_hint."
        )
        try:
            scope = await _attempt(retry_payload)
        except Exception:
            logger.error(
                "Phase 1 scope synthesis failed twice — emitting "
                "programmatic fallback ScopeDocument derived from the "
                "task text so Phase 2 still receives the 8-section shape",
                exc_info=True,
            )
            scope = _fallback_scope_document(task)
            return scope, format_scope_document(scope), False

    logger.info(
        "Phase 1 synthesis: deliverables=%d in_scope=%d assumptions=%d "
        "success_criteria=%d risks=%d",
        len(scope.deliverables),
        len(scope.in_scope),
        len(scope.assumptions),
        len(scope.success_criteria),
        len(scope.risks),
    )
    return scope, format_scope_document(scope), True


def _fallback_scope_document(task: str) -> ScopeDocument:
    """Construct a minimal ScopeDocument when the synthesis LLM fails twice.

    Guarantees Phase 2 always receives the 8-section shape even when the
    request model refuses to produce valid JSON. The task text goes into
    ``problem`` verbatim (truncated) and a single assumption flags that
    downstream phases must treat the task description as authoritative.
    """
    problem = task.strip()[:4000] or "(task text was empty)"
    return ScopeDocument(
        problem=problem,
        deliverables=[],
        in_scope=[],
        out_of_scope=[],
        downstream_consumers=[],
        assumptions=[
            ScopeAssumption(
                assumption=(
                    "Phase 1 scope synthesis did not produce a validated "
                    "ScopeDocument; the PROBLEM section above is the "
                    "verbatim task and is authoritative."
                ),
                verify_hint=(
                    "Re-read the PROBLEM section, extract file paths and "
                    "entity names directly from it, and grep the codebase "
                    "to confirm each reference before planning edits."
                ),
            ),
        ],
        success_criteria=[],
        risks=[
            "Phase 1 synthesis fallback triggered — scope was derived from "
            "task text only; other structured sections are empty and must "
            "be reconstructed from the task during Phase 2 exploration.",
        ],
    )


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
        settings.effective_expert_max_tokens if expert_llm_client else settings.ollama_max_tokens
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
        ws,
        "Revising plan based on feedback...",
        model=expert.model_name,
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
    impl_steps = [s for s in plan.steps if s.tool in IMPLEMENTATION_STEP_TOOLS]
    if len(impl_steps) < len(plan.steps):
        stripped_count = len(plan.steps) - len(impl_steps)
        stripped_tools = [s.tool for s in plan.steps if s.tool not in IMPLEMENTATION_STEP_TOOLS]
        logger.warning(
            "Stripped %d non-implementation steps from revised plan: %s",
            stripped_count,
            stripped_tools,
        )
        for i, step in enumerate(impl_steps, 1):
            step.step_number = i
        plan.steps = impl_steps
    logger.info(
        "Plan revised: %d steps, %d affected files",
        len(plan.steps),
        len(plan.affected_files),
    )
    return plan

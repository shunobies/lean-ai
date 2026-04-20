"""LLM-powered memory extraction from completed sessions.

After a session completes, this module calls the worker (or primary) LLM
to extract structured memories — project-specific discoveries, gotchas,
patterns — that should persist across sessions.

Extraction requests are queued and processed by a background worker,
following the same pattern as notes_llm.py.

Phase-specific extraction entry points capture failure/success signals
that would otherwise be thrown away:
- :func:`extract_from_plan_rejection` — after a user rejects a plan
- :func:`extract_from_fix_success` — after a validation fix-loop succeeds
- :func:`extract_from_tdd_dispute` — after a TDD dispute is decided
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from lean_ai.llm.facade import LLMClient
from lean_ai.llm.prompt_registry import registry
from lean_ai.memory.db import create_memory
from lean_ai.memory.index import index_memory

logger = logging.getLogger(__name__)


class MemoryItem(BaseModel):
    """A single extracted memory."""

    category: str = Field(
        description=(
            "Memory category: architecture, build, testing, pattern, "
            "gotcha, convention, discovery, rejection, fix_pattern, "
            "or success_pattern"
        ),
    )
    content: str = Field(
        description="Concise lesson or discovery (1-3 sentences)",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Relevant tags for search",
    )


class ExtractedMemories(BaseModel):
    """Structured output from LLM extraction."""

    memories: list[MemoryItem] = Field(
        default_factory=list,
        description="Extracted memories (0-5 items)",
    )


OnMemoryCreated = Callable[[dict], Awaitable[None]]


# ── Extraction queue ──

_queue: asyncio.Queue | None = None
_worker_task: asyncio.Task | None = None


@dataclass
class _ExtractionItem:
    llm: LLMClient
    repo_root: str
    session_id: str
    session_summary: str
    source_task: str
    prompt_key: str = "memory.extract"
    source_phase: str = "session_end"
    curation_status: str = "auto"
    expires_at: str | None = None
    on_memory_created: OnMemoryCreated | None = None
    max_items: int = 5
    template_vars: dict[str, str] = field(default_factory=dict)


async def _extraction_worker() -> None:
    """Process extraction requests one at a time."""
    assert _queue is not None
    while True:
        item = await _queue.get()
        try:
            await _extract_and_store(item)
        except Exception:
            logger.exception(
                "Failed to extract memories for session %s", item.session_id,
            )
        finally:
            _queue.task_done()


def _ensure_worker() -> None:
    """Lazily create the queue and worker on first use."""
    global _queue, _worker_task
    if _queue is not None:
        return
    _queue = asyncio.Queue()
    _worker_task = asyncio.create_task(
        _extraction_worker(), name="memory-extraction-worker",
    )


# ── Core extraction logic ──


async def _extract_and_store(item: _ExtractionItem) -> list[dict]:
    """Extract memories using the item's prompt and persist them."""
    prompt_text = registry.get(item.prompt_key)
    template_vars = {"session_summary": item.session_summary, **item.template_vars}
    user_msg = prompt_text.format(**template_vars)

    result = await item.llm.chat_structured(
        messages=[{"role": "user", "content": user_msg}],
        schema=ExtractedMemories,
        temperature=0.3,
    )

    if not result.memories:
        logger.info(
            "No memories extracted for session %s (phase=%s)",
            item.session_id, item.source_phase,
        )
        return []

    items = result.memories[: item.max_items]
    model_name = getattr(item.llm, "model_name", None) or getattr(
        item.llm, "model", None,
    )

    from lean_ai.db import get_db

    db = await get_db(item.repo_root)
    stored: list[dict] = []
    try:
        for mem_item in items:
            memory = await create_memory(
                db,
                session_id=item.session_id,
                category=mem_item.category,
                content=mem_item.content,
                tags=mem_item.tags,
                source_task=item.source_task,
                curation_status=item.curation_status,
                source_phase=item.source_phase,
                model_name=str(model_name) if model_name else None,
                expires_at=item.expires_at,
            )
            index_memory(
                repo_root=item.repo_root,
                memory_id=memory["id"],
                content=mem_item.content,
                category=mem_item.category,
                tags=mem_item.tags,
                source_task=item.source_task,
            )
            stored.append(memory)
            if item.on_memory_created is not None:
                try:
                    await item.on_memory_created(memory)
                except Exception:
                    logger.debug(
                        "on_memory_created callback failed", exc_info=True,
                    )

        logger.info(
            "Extracted %d memories for session %s (phase=%s): %s",
            len(stored),
            item.session_id,
            item.source_phase,
            [m["category"] for m in stored],
        )
    finally:
        await db.close()

    return stored


_SEARCH_FINDINGS_BUDGET = 3000  # chars for the SEARCH & LOOKUPS section


def build_session_summary_for_extraction(
    task: str,
    plan_text: str | None,
    tool_stats: dict[str, int],
    failed_tools: list[str],
    validation_passed: bool,
    files_modified: list[str],
    search_findings: list[tuple[str, str]] | None = None,
) -> str:
    """Build a compact session summary for the extraction prompt."""
    parts = [f"TASK: {task}"]

    if plan_text:
        plan_snippet = plan_text[:2000]
        if len(plan_text) > 2000:
            plan_snippet += "\n... (truncated)"
        parts.append(f"\nPLAN SUMMARY:\n{plan_snippet}")

    if tool_stats:
        stats_lines = [f"  {name}: {count}" for name, count in tool_stats.items()]
        parts.append("\nTOOL CALLS:\n" + "\n".join(stats_lines))

    if failed_tools:
        parts.append(f"\nFAILED TOOLS: {', '.join(failed_tools)}")

    parts.append(
        f"\nVALIDATION: {'passed' if validation_passed else 'failed'}"
    )

    if files_modified:
        parts.append("\nFILES MODIFIED:\n  " + "\n  ".join(files_modified))

    if search_findings:
        findings_parts = ["\nSEARCH & LOOKUPS:"]
        budget_used = len(findings_parts[0])
        for tool_name, content in search_findings:
            label = "search" if tool_name == "search_internet" else "fetch"
            snippet = content[:400]
            if len(content) > 400:
                snippet += "..."
            entry = f"\n  [{label}] {snippet}"
            if budget_used + len(entry) > _SEARCH_FINDINGS_BUDGET:
                findings_parts.append(
                    "\n  ... (additional findings truncated)"
                )
                break
            findings_parts.append(entry)
            budget_used += len(entry)
        parts.append("".join(findings_parts))

    return "\n".join(parts)


def _truncate(text: str, limit: int) -> str:
    """Truncate text for inclusion in extraction prompts."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n... (truncated)"


def build_plan_rejection_summary(
    task: str,
    plan_before: str,
    feedback: str,
    plan_after: str | None,
) -> str:
    """Compact summary for extract_from_plan_rejection."""
    parts = [f"TASK: {task}"]
    parts.append(f"\nORIGINAL PLAN (rejected):\n{_truncate(plan_before, 2000)}")
    parts.append(f"\nUSER FEEDBACK:\n{_truncate(feedback, 1000)}")
    if plan_after:
        parts.append(f"\nREVISED PLAN (approved):\n{_truncate(plan_after, 2000)}")
    return "\n".join(parts)


def build_fix_success_summary(
    task: str,
    failing_command: str,
    error_output: str,
    diagnosis: str,
    fix_tool_calls: list[dict] | None,
) -> str:
    """Compact summary for extract_from_fix_success."""
    parts = [f"TASK: {task}"]
    parts.append(f"\nFAILING COMMAND: {failing_command}")
    parts.append(f"\nERROR OUTPUT:\n{_truncate(error_output, 1500)}")
    parts.append(f"\nDIAGNOSIS:\n{_truncate(diagnosis, 1500)}")
    if fix_tool_calls:
        try:
            calls_text = json.dumps(fix_tool_calls, indent=2, default=str)
        except Exception:
            calls_text = str(fix_tool_calls)
        parts.append(f"\nFIX ACTIONS:\n{_truncate(calls_text, 1500)}")
    return "\n".join(parts)


def build_tdd_dispute_summary(
    task: str,
    test_file: str,
    reason: str,
    decision: str,
    explanation: str,
) -> str:
    """Compact summary for extract_from_tdd_dispute."""
    parts = [f"TASK: {task}"]
    parts.append(f"\nTEST FILE: {test_file}")
    parts.append(f"\nDISPUTE REASON:\n{_truncate(reason, 1000)}")
    parts.append(f"\nEXPERT DECISION: {decision}")
    parts.append(f"\nEXPLANATION:\n{_truncate(explanation, 1500)}")
    return "\n".join(parts)


# ── Public API ──


def schedule_extraction(
    llm: LLMClient,
    repo_root: str,
    session_id: str,
    session_summary: str,
    source_task: str,
    *,
    on_memory_created: OnMemoryCreated | None = None,
    source_phase: str = "session_end",
) -> None:
    """Queue memory extraction for background processing."""
    _ensure_worker()
    assert _queue is not None
    _queue.put_nowait(
        _ExtractionItem(
            llm=llm,
            repo_root=repo_root,
            session_id=session_id,
            session_summary=session_summary,
            source_task=source_task,
            source_phase=source_phase,
            on_memory_created=on_memory_created,
        ),
    )
    logger.debug("Queued memory extraction for session %s", session_id)


def schedule_plan_rejection_extraction(
    llm: LLMClient,
    repo_root: str,
    session_id: str,
    *,
    task: str,
    plan_before: str,
    feedback: str,
    plan_after: str | None = None,
    on_memory_created: OnMemoryCreated | None = None,
) -> None:
    """Queue extraction of a `rejection` memory from a rejected plan."""
    _ensure_worker()
    assert _queue is not None
    summary = build_plan_rejection_summary(task, plan_before, feedback, plan_after)
    _queue.put_nowait(
        _ExtractionItem(
            llm=llm,
            repo_root=repo_root,
            session_id=session_id,
            session_summary=summary,
            source_task=task,
            prompt_key="memory.extract_rejection",
            source_phase="plan_rejection",
            on_memory_created=on_memory_created,
            max_items=2,
        ),
    )


def schedule_fix_success_extraction(
    llm: LLMClient,
    repo_root: str,
    session_id: str,
    *,
    task: str,
    failing_command: str,
    error_output: str,
    diagnosis: str,
    fix_tool_calls: list[dict] | None = None,
    on_memory_created: OnMemoryCreated | None = None,
) -> None:
    """Queue extraction of a `fix_pattern` memory from a successful fix."""
    _ensure_worker()
    assert _queue is not None
    summary = build_fix_success_summary(
        task, failing_command, error_output, diagnosis, fix_tool_calls,
    )
    _queue.put_nowait(
        _ExtractionItem(
            llm=llm,
            repo_root=repo_root,
            session_id=session_id,
            session_summary=summary,
            source_task=task,
            prompt_key="memory.extract_fix_pattern",
            source_phase="fix_loop",
            on_memory_created=on_memory_created,
            max_items=2,
        ),
    )


def schedule_tdd_dispute_extraction(
    llm: LLMClient,
    repo_root: str,
    session_id: str,
    *,
    task: str,
    test_file: str,
    reason: str,
    decision: str,
    explanation: str,
    on_memory_created: OnMemoryCreated | None = None,
) -> None:
    """Queue extraction of a memory from a TDD dispute decision."""
    _ensure_worker()
    assert _queue is not None
    summary = build_tdd_dispute_summary(
        task, test_file, reason, decision, explanation,
    )
    _queue.put_nowait(
        _ExtractionItem(
            llm=llm,
            repo_root=repo_root,
            session_id=session_id,
            session_summary=summary,
            source_task=task,
            prompt_key="memory.extract_tdd_dispute",
            source_phase="tdd_dispute",
            on_memory_created=on_memory_created,
            max_items=2,
        ),
    )


async def extract_memories(
    llm: LLMClient,
    repo_root: str,
    session_id: str,
    session_summary: str,
    source_task: str,
    *,
    source_phase: str = "session_end",
    prompt_key: str = "memory.extract",
    on_memory_created: OnMemoryCreated | None = None,
) -> list[dict]:
    """Extract memories synchronously (for direct calls / tests)."""
    return await _extract_and_store(
        _ExtractionItem(
            llm=llm,
            repo_root=repo_root,
            session_id=session_id,
            session_summary=session_summary,
            source_task=source_task,
            prompt_key=prompt_key,
            source_phase=source_phase,
            on_memory_created=on_memory_created,
        ),
    )

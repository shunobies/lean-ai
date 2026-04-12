"""LLM-powered memory extraction from completed sessions.

After a session completes, this module calls the worker (or primary) LLM
to extract structured memories — project-specific discoveries, gotchas,
patterns — that should persist across sessions.

Extraction requests are queued and processed by a background worker,
following the same pattern as notes_llm.py.
"""

import asyncio
import logging
from dataclasses import dataclass

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
            "Memory category: architecture, build, testing, "
            "pattern, gotcha, or convention"
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


async def _extraction_worker() -> None:
    """Process extraction requests one at a time."""
    assert _queue is not None
    while True:
        item = await _queue.get()
        try:
            await _extract_and_store(
                item.llm,
                item.repo_root,
                item.session_id,
                item.session_summary,
                item.source_task,
            )
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


async def _extract_and_store(
    llm: LLMClient,
    repo_root: str,
    session_id: str,
    session_summary: str,
    source_task: str,
) -> list[dict]:
    """Extract memories from a session summary and persist them."""
    prompt_text = registry.get("memory.extract")
    user_msg = prompt_text.format(session_summary=session_summary)

    result = await llm.chat_structured(
        messages=[
            {"role": "user", "content": user_msg},
        ],
        schema=ExtractedMemories,
        temperature=0.3,
    )

    if not result.memories:
        logger.info("No memories extracted for session %s", session_id)
        return []

    # Cap at 5 memories per session
    items = result.memories[:5]

    from lean_ai.db import get_db

    db = await get_db(repo_root)
    stored: list[dict] = []
    try:
        for item in items:
            memory = await create_memory(
                db,
                session_id=session_id,
                category=item.category,
                content=item.content,
                tags=item.tags,
                source_task=source_task,
            )
            index_memory(
                repo_root=repo_root,
                memory_id=memory["id"],
                content=item.content,
                category=item.category,
                tags=item.tags,
                source_task=source_task,
            )
            stored.append(memory)

        logger.info(
            "Extracted %d memories for session %s: %s",
            len(stored),
            session_id,
            [m["category"] for m in stored],
        )
    finally:
        await db.close()

    return stored


def build_session_summary_for_extraction(
    task: str,
    plan_text: str | None,
    tool_stats: dict[str, int],
    failed_tools: list[str],
    validation_passed: bool,
    files_modified: list[str],
) -> str:
    """Build a compact session summary for the extraction prompt.

    Assembles key session data into a text block that the LLM can
    analyze to extract reusable memories.
    """
    parts = [f"TASK: {task}"]

    if plan_text:
        # Truncate plan to first 2000 chars
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

    return "\n".join(parts)


# ── Public API ──


def schedule_extraction(
    llm: LLMClient,
    repo_root: str,
    session_id: str,
    session_summary: str,
    source_task: str,
) -> None:
    """Queue memory extraction for background processing.

    Requests are processed one at a time to avoid concurrent
    SQLite writes and LLM contention.
    """
    _ensure_worker()
    assert _queue is not None
    _queue.put_nowait(
        _ExtractionItem(llm, repo_root, session_id, session_summary, source_task),
    )
    logger.debug("Queued memory extraction for session %s", session_id)


async def extract_memories(
    llm: LLMClient,
    repo_root: str,
    session_id: str,
    session_summary: str,
    source_task: str,
) -> list[dict]:
    """Extract memories synchronously (for direct calls / tests)."""
    return await _extract_and_store(
        llm, repo_root, session_id, session_summary, source_task,
    )

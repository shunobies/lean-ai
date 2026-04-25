"""LLM-powered note categorization and TODO extraction.

After a note is saved, this module calls the primary LLM to:
- Assign a project category
- Extract TODO/action items from the note text
- Generate tags

Categorization requests are queued and processed one at a time by a
background worker, avoiding concurrent SQLite writes and LLM contention
when multiple notes are created in rapid succession.
"""

import asyncio
import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field

from lean_ai.llm.facade import LLMClient
from lean_ai.llm.prompt_registry import registry
from lean_ai.notes_db import (
    create_todo,
    get_notes_db,
    update_note,
)
from lean_ai.notes_index import index_note

logger = logging.getLogger(__name__)


class NoteCategorization(BaseModel):
    """Structured output from LLM categorization."""

    project: str = Field(description="Project name this note relates to")
    tags: list[str] = Field(default_factory=list, description="Relevant tags")
    todos: list[str] = Field(
        default_factory=list,
        description="Action items / TODOs extracted from the note",
    )


# ── Categorization queue ──

_queue: asyncio.Queue | None = None
_worker_task: asyncio.Task | None = None


@dataclass
class _CategorizationItem:
    llm: LLMClient
    note_id: str
    content: str
    source_workspace: str | None


async def _categorization_worker() -> None:
    """Process categorization requests one at a time."""
    assert _queue is not None
    while True:
        item = await _queue.get()
        try:
            await _categorize_note(item.llm, item.note_id, item.content, item.source_workspace)
        except Exception:
            logger.exception("Failed to categorize note %s", item.note_id)
        finally:
            _queue.task_done()


def _ensure_worker() -> None:
    """Lazily create the queue and worker on first use."""
    global _queue, _worker_task
    if _queue is not None:
        return
    _queue = asyncio.Queue()
    _worker_task = asyncio.create_task(_categorization_worker(), name="note-categorization-worker")


# ── Core categorization logic ──


async def _categorize_note(
    llm: LLMClient,
    note_id: str,
    content: str,
    source_workspace: str | None = None,
) -> None:
    """Categorize a single note using the LLM and update the database."""
    workspace_hint = ""
    if source_workspace:
        parts = source_workspace.rstrip("/").split("/")
        workspace_hint = f"\nSource workspace: {parts[-1]}" if parts else ""

    prompt_text = registry.get("notes.categorize")
    user_msg = prompt_text.format(
        note_content=content,
        workspace_hint=workspace_hint,
    )

    result = await llm.chat_structured(
        messages=[
            {"role": "user", "content": user_msg},
        ],
        schema=NoteCategorization,
        temperature=0.3,
    )

    db = await get_notes_db()
    try:
        await update_note(
            db,
            note_id,
            project=result.project,
            tags=result.tags,
        )

        for todo_text in result.todos:
            await create_todo(db, note_id, todo_text)

        index_note(
            note_id=note_id,
            content=content,
            project=result.project,
            tags=result.tags,
        )

        logger.info(
            "Categorized note %s: project=%s, tags=%s, todos=%d",
            note_id,
            result.project,
            result.tags,
            len(result.todos),
        )
    finally:
        await db.close()


# ── Public API ──


async def categorize_note(
    llm: LLMClient,
    note_id: str,
    content: str,
    source_workspace: str | None = None,
) -> None:
    """Categorize a note using the LLM and update the database.

    Kept for direct calls (e.g., tests). For background scheduling,
    use ``schedule_categorization`` instead.
    """
    await _categorize_note(llm, note_id, content, source_workspace)


def schedule_categorization(
    llm: LLMClient,
    note_id: str,
    content: str,
    source_workspace: str | None = None,
) -> None:
    """Queue note categorization for background processing.

    Requests are processed one at a time by a single worker to avoid
    concurrent SQLite writes and LLM contention.
    """
    _ensure_worker()
    assert _queue is not None
    _queue.put_nowait(_CategorizationItem(llm, note_id, content, source_workspace))
    logger.debug("Queued categorization for note %s", note_id)

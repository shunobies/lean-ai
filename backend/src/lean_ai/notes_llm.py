"""LLM-powered note categorization and TODO extraction.

After a note is saved, this module calls the primary LLM to:
- Assign a project category
- Extract TODO/action items from the note text
- Generate tags

Runs as a background asyncio task so the save response returns immediately.
"""

import asyncio
import logging

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


async def categorize_note(
    llm: LLMClient,
    note_id: str,
    content: str,
    source_workspace: str | None = None,
) -> None:
    """Categorize a note using the LLM and update the database.

    This is meant to be called as a background task.
    """
    try:
        workspace_hint = ""
        if source_workspace:
            # Extract project name from workspace path
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

            # Update the search index with categorization
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

    except Exception:
        logger.exception("Failed to categorize note %s", note_id)


def schedule_categorization(
    llm: LLMClient,
    note_id: str,
    content: str,
    source_workspace: str | None = None,
) -> asyncio.Task:
    """Schedule note categorization as a background task."""
    return asyncio.create_task(
        categorize_note(llm, note_id, content, source_workspace),
        name=f"categorize-note-{note_id}",
    )

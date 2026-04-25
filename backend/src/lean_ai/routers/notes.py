"""Notes CRUD and search endpoints."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from lean_ai.notes_db import (
    create_note,
    create_todo,
    delete_note,
    delete_todo,
    get_note,
    get_notes_db,
    list_notes,
    list_projects,
    list_todos_by_project,
    update_note,
    update_todo,
)
from lean_ai.notes_index import index_note, remove_note, search_notes
from lean_ai.notes_llm import schedule_categorization
from lean_ai.routers.dependencies import llm_client

logger = logging.getLogger(__name__)

notes_router = APIRouter(prefix="/notes", tags=["notes"])


# ── Request/Response models ──


class CreateNoteRequest(BaseModel):
    content: str
    source_workspace: str | None = None


class UpdateNoteRequest(BaseModel):
    content: str | None = None
    project: str | None = None
    tags: list[str] | None = None


class CreateTodoRequest(BaseModel):
    description: str


class UpdateTodoRequest(BaseModel):
    description: str | None = None
    completed: bool | None = None


# ── Note endpoints ──


@notes_router.post("")
async def create_note_endpoint(req: CreateNoteRequest):
    """Create a new note. LLM categorization runs in the background."""
    db = await get_notes_db()
    try:
        note = await create_note(db, req.content, req.source_workspace)

        # Index immediately with raw content
        index_note(note_id=note["id"], content=req.content)

        # Schedule background LLM categorization
        schedule_categorization(llm_client, note["id"], req.content, req.source_workspace)

        return note
    finally:
        await db.close()


@notes_router.get("")
async def list_notes_endpoint(project: str | None = None):
    """List notes, optionally filtered by project."""
    db = await get_notes_db()
    try:
        return await list_notes(db, project=project)
    finally:
        await db.close()


@notes_router.get("/search")
async def search_notes_endpoint(q: str, limit: int = 20):
    """Full-text search across notes."""
    return search_notes(q, limit=limit)


@notes_router.get("/projects")
async def list_projects_endpoint():
    """List distinct project categories."""
    db = await get_notes_db()
    try:
        return await list_projects(db)
    finally:
        await db.close()


@notes_router.get("/todos")
async def list_todos_endpoint(
    project: str | None = None,
    source_workspace: str | None = None,
    pending_only: bool = True,
):
    """List TODOs across notes, filtered by project or workspace."""
    db = await get_notes_db()
    try:
        return await list_todos_by_project(
            db,
            project=project,
            source_workspace=source_workspace,
            pending_only=pending_only,
        )
    finally:
        await db.close()


@notes_router.get("/{note_id}")
async def get_note_endpoint(note_id: str):
    """Get a single note with its TODOs."""
    db = await get_notes_db()
    try:
        note = await get_note(db, note_id)
        if not note:
            raise HTTPException(status_code=404, detail="Note not found")
        return note
    finally:
        await db.close()


@notes_router.put("/{note_id}")
async def update_note_endpoint(note_id: str, req: UpdateNoteRequest):
    """Update a note's content, project, or tags."""
    db = await get_notes_db()
    try:
        kwargs = {}
        if req.content is not None:
            kwargs["content"] = req.content
        if req.project is not None:
            kwargs["project"] = req.project
        if req.tags is not None:
            kwargs["tags"] = req.tags

        note = await update_note(db, note_id, **kwargs)
        if not note:
            raise HTTPException(status_code=404, detail="Note not found")

        # Re-index with updated content
        index_note(
            note_id=note_id,
            content=note["content"],
            project=note.get("project"),
            tags=note.get("tags"),
        )

        return note
    finally:
        await db.close()


@notes_router.delete("/{note_id}")
async def delete_note_endpoint(note_id: str):
    """Delete a note and its TODOs."""
    db = await get_notes_db()
    try:
        deleted = await delete_note(db, note_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Note not found")
        remove_note(note_id)
        return {"deleted": True}
    finally:
        await db.close()


# ── TODO endpoints ──


@notes_router.post("/{note_id}/todos")
async def create_todo_endpoint(note_id: str, req: CreateTodoRequest):
    """Add a TODO to a note."""
    db = await get_notes_db()
    try:
        note = await get_note(db, note_id)
        if not note:
            raise HTTPException(status_code=404, detail="Note not found")
        return await create_todo(db, note_id, req.description)
    finally:
        await db.close()


@notes_router.put("/todos/{todo_id}")
async def update_todo_endpoint(todo_id: int, req: UpdateTodoRequest):
    """Update a TODO (toggle completed, edit text)."""
    db = await get_notes_db()
    try:
        kwargs = {}
        if req.description is not None:
            kwargs["description"] = req.description
        if req.completed is not None:
            kwargs["completed"] = req.completed

        todo = await update_todo(db, todo_id, **kwargs)
        if not todo:
            raise HTTPException(status_code=404, detail="TODO not found")
        return todo
    finally:
        await db.close()


@notes_router.delete("/todos/{todo_id}")
async def delete_todo_endpoint(todo_id: int):
    """Delete a TODO."""
    db = await get_notes_db()
    try:
        deleted = await delete_todo(db, todo_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="TODO not found")
        return {"deleted": True}
    finally:
        await db.close()

"""Cross-session memory CRUD + curation endpoints.

Extends the Phase-A curated-memory flow: the extension's Memories panel
and inline confirmation chip post to these endpoints to promote/reject
auto-extracted memories, and to save user-authored memories directly.

All endpoints are scoped to a workspace via the ``repo_root`` query param,
mirroring the convention used by the rest of the API (no workspace_id /
user_id — localhost trust model).
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from lean_ai.db import get_db
from lean_ai.memory.db import (
    create_memory,
    delete_memory,
    get_memory,
    list_memories,
    update_curation_status,
)
from lean_ai.memory.index import index_memory, remove_memory

logger = logging.getLogger(__name__)

memories_router = APIRouter(prefix="/memories", tags=["memories"])


class CreateMemoryRequest(BaseModel):
    repo_root: str
    category: str = Field(
        description=(
            "Category: architecture, build, testing, pattern, gotcha, "
            "convention, discovery, rejection, fix_pattern, success_pattern"
        ),
    )
    content: str = Field(min_length=1)
    tags: list[str] | None = None
    source_task: str | None = None


class ConfirmMemoryRequest(BaseModel):
    repo_root: str


class RejectMemoryRequest(BaseModel):
    repo_root: str


class DeleteMemoryRequest(BaseModel):
    repo_root: str


@memories_router.get("")
async def list_memories_endpoint(
    repo_root: str,
    category: str | None = None,
    curation_status: str | None = None,
    limit: int = 100,
    include_expired: bool = False,
):
    """List memories for a workspace.

    *curation_status* accepts a comma-separated list (e.g.
    ``user_confirmed,high_confidence_auto``) or a single value.
    """
    statuses: list[str] | None = None
    if curation_status:
        statuses = [s.strip() for s in curation_status.split(",") if s.strip()]
    db = await get_db(repo_root)
    try:
        return await list_memories(
            db,
            category=category,
            limit=limit,
            curation_status=statuses,
            include_expired=include_expired,
        )
    finally:
        await db.close()


@memories_router.get("/{memory_id}")
async def get_memory_endpoint(memory_id: str, repo_root: str):
    """Fetch a single memory by id."""
    db = await get_db(repo_root)
    try:
        memory = await get_memory(db, memory_id)
        if memory is None:
            raise HTTPException(status_code=404, detail="Memory not found")
        return memory
    finally:
        await db.close()


@memories_router.post("")
async def create_memory_endpoint(req: CreateMemoryRequest):
    """Create a user-authored memory (curation_status=user_confirmed)."""
    db = await get_db(req.repo_root)
    try:
        memory = await create_memory(
            db,
            session_id="manual",
            category=req.category,
            content=req.content,
            tags=req.tags,
            source_task=req.source_task,
            curation_status="user_confirmed",
            confidence=0.9,
            source_phase="user_manual",
        )
        index_memory(
            repo_root=req.repo_root,
            memory_id=memory["id"],
            content=req.content,
            category=req.category,
            tags=req.tags,
            source_task=req.source_task,
        )
        return memory
    finally:
        await db.close()


@memories_router.post("/{memory_id}/confirm")
async def confirm_memory_endpoint(memory_id: str, req: ConfirmMemoryRequest):
    """Promote an auto memory to user_confirmed (confidence=0.9)."""
    db = await get_db(req.repo_root)
    try:
        found = await update_curation_status(
            db,
            memory_id,
            "user_confirmed",
            confidence=0.9,
        )
        if not found:
            raise HTTPException(status_code=404, detail="Memory not found")
        return await get_memory(db, memory_id)
    finally:
        await db.close()


@memories_router.post("/{memory_id}/reject")
async def reject_memory_endpoint(memory_id: str, req: RejectMemoryRequest):
    """Mark a memory as user_rejected so it is excluded from retrieval."""
    db = await get_db(req.repo_root)
    try:
        found = await update_curation_status(
            db,
            memory_id,
            "user_rejected",
            confidence=0.0,
        )
        if not found:
            raise HTTPException(status_code=404, detail="Memory not found")
        return await get_memory(db, memory_id)
    finally:
        await db.close()


@memories_router.delete("/{memory_id}")
async def delete_memory_endpoint(memory_id: str, repo_root: str):
    """Permanently delete a memory."""
    db = await get_db(repo_root)
    try:
        found = await delete_memory(db, memory_id)
        if not found:
            raise HTTPException(status_code=404, detail="Memory not found")
    finally:
        await db.close()
    remove_memory(repo_root, memory_id)
    return {"deleted": memory_id}

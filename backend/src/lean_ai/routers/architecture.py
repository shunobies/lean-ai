"""Architecture decision endpoints."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from lean_ai.architecture.decision_db import (
    create_architecture_decision,
    get_architecture_decision,
    list_architecture_decisions,
    update_architecture_decision_status,
)
from lean_ai.db import get_db

architecture_router = APIRouter(prefix="/architecture", tags=["architecture"])


class CreateArchitectureDecisionRequest(BaseModel):
    repo_root: str
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    status: str = "active"
    tags: list[str] | None = None
    source_session_id: str | None = None
    source_memory_id: str | None = None
    source_plan_decision_ref: str | None = None


class UpdateArchitectureDecisionStatusRequest(BaseModel):
    repo_root: str
    status: str = Field(min_length=1)


@architecture_router.get("/decisions")
async def list_architecture_decisions_endpoint(
    repo_root: str,
    status: str | None = "active",
    query: str | None = None,
    limit: int = 20,
):
    """List or search architecture decisions for a workspace."""
    db = await get_db(repo_root)
    try:
        return await list_architecture_decisions(
            db,
            status=status,
            query=query,
            limit=limit,
        )
    finally:
        await db.close()


@architecture_router.get("/decisions/{decision_id}")
async def get_architecture_decision_endpoint(decision_id: str, repo_root: str):
    """Fetch a single architecture decision by id."""
    db = await get_db(repo_root)
    try:
        decision = await get_architecture_decision(db, decision_id)
        if decision is None:
            raise HTTPException(status_code=404, detail="Architecture decision not found")
        return decision
    finally:
        await db.close()


@architecture_router.post("/decisions")
async def create_architecture_decision_endpoint(req: CreateArchitectureDecisionRequest):
    """Create a durable architecture decision for this workspace."""
    db = await get_db(req.repo_root)
    try:
        return await create_architecture_decision(
            db,
            title=req.title,
            summary=req.summary,
            rationale=req.rationale,
            status=req.status,
            tags=req.tags,
            source_session_id=req.source_session_id,
            source_memory_id=req.source_memory_id,
            source_plan_decision_ref=req.source_plan_decision_ref,
        )
    finally:
        await db.close()


@architecture_router.post("/decisions/{decision_id}/status")
async def update_architecture_decision_status_endpoint(
    decision_id: str,
    req: UpdateArchitectureDecisionStatusRequest,
):
    """Mark a decision as active, superseded, or archived."""
    db = await get_db(req.repo_root)
    try:
        decision = await update_architecture_decision_status(
            db,
            decision_id,
            status=req.status,
        )
        if decision is None:
            raise HTTPException(status_code=404, detail="Architecture decision not found")
        return decision
    finally:
        await db.close()

"""Session CRUD and search endpoints."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from lean_ai.db import (
    create_session,
    delete_session,
    get_conversation_log,
    get_db,
    get_session,
    get_session_raw,
    list_sessions,
    log_conversation_entry,
    update_session,
)
from lean_ai.db import (
    search_sessions as db_search_sessions,
)
from lean_ai.routers.models import (
    CreateSessionRequest,
    CreateSessionResponse,
    ResumeSessionRequest,
)
from lean_ai.tools.git_ops import (
    git_checkout,
    git_stash_pop,
    git_stash_push,
)
from lean_ai.workflow.state import StateManager

logger = logging.getLogger(__name__)

sessions_router = APIRouter()


class RestoreCheckpointRequest(BaseModel):
    """Request body for restoring a checkpoint."""

    checkpoint_id: str
    repo_root: str


@sessions_router.post("/sessions", response_model=CreateSessionResponse)
async def create_new_session(request: CreateSessionRequest):
    """Create a new workflow session."""
    db = await get_db(request.repo_root)
    try:
        session_id = await create_session(db, request.repo_root, request.task)
        return CreateSessionResponse(session_id=session_id, status="active")
    finally:
        await db.close()


@sessions_router.get("/sessions")
async def list_all_sessions(repo_root: str):
    """List all sessions for a workspace."""
    db = await get_db(repo_root)
    try:
        sessions = await list_sessions(db)
        return sessions
    finally:
        await db.close()


@sessions_router.get("/sessions/{session_id}")
async def get_session_detail(session_id: str, repo_root: str):
    """Get session detail."""
    db = await get_db(repo_root)
    try:
        session = await get_session(db, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return session
    finally:
        await db.close()


@sessions_router.delete("/sessions/{session_id}")
async def delete_session_endpoint(session_id: str, repo_root: str):
    """Delete a session and all its associated data (logs, conversation)."""
    db = await get_db(repo_root)
    try:
        found = await delete_session(db, session_id)
        if not found:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"status": "deleted", "session_id": session_id}
    finally:
        await db.close()


@sessions_router.get("/sessions/{session_id}/conversation")
async def get_session_conversation(session_id: str, repo_root: str):
    """Get the full conversation log (chain-of-thought) for a session."""
    db = await get_db(repo_root)
    try:
        session = await get_session(db, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        log = await get_conversation_log(db, session_id)
        return {"session_id": session_id, "entries": log}
    finally:
        await db.close()


@sessions_router.get("/sessions/{session_id}/checkpoints")
async def list_checkpoints(session_id: str, repo_root: str):
    """List checkpoints for a session as a tree structure."""
    db = await get_db(repo_root)
    try:
        session = await get_session_raw(db, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
    finally:
        await db.close()

    sm = StateManager(session_id)
    checkpoints = sm.list_checkpoints(session_id)
    return checkpoints


@sessions_router.post("/sessions/{session_id}/restore")
async def restore_checkpoint(session_id: str, request: RestoreCheckpointRequest):
    """Restore a session to a previous checkpoint state."""
    db = await get_db(request.repo_root)
    try:
        # Validate session exists
        session = await get_session_raw(db, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        # Load the checkpoint and validate it belongs to this session
        sm = StateManager(session_id)
        try:
            checkpoint_state = sm.get_checkpoint(request.checkpoint_id)
        except FileNotFoundError:
            raise HTTPException(
                status_code=404, detail=f"Checkpoint {request.checkpoint_id} not found"
            ) from None

        # Validate checkpoint session_id matches
        if checkpoint_state.session_id != session_id:
            raise HTTPException(
                status_code=400,
                detail="Checkpoint does not belong to this session",
            )

        # Overwrite the active state file
        sm._state = checkpoint_state
        sm.save()

        # Log restore event to conversation_logs
        await log_conversation_entry(
            db,
            session_id,
            role="system",
            content=f"Session restored to checkpoint {request.checkpoint_id}",
        )

        return {
            "status": "restored",
            "session_id": session_id,
            "checkpoint_id": request.checkpoint_id,
        }
    finally:
        await db.close()


@sessions_router.get("/sessions/{session_id}/git-events")
async def list_git_events(session_id: str):
    """List git events for a session (stub — returns empty list)."""
    return []


@sessions_router.get("/sessions/search")
async def search_sessions_endpoint(repo_root: str, q: str = "", commit: str = ""):
    """Search sessions by task text, plan content, conversation, or commit SHA."""
    if not q and not commit:
        return []
    db = await get_db(repo_root)
    try:
        return await db_search_sessions(db, query=q, commit_sha=commit)
    finally:
        await db.close()


@sessions_router.post("/sessions/{session_id}/resume")
async def resume_session(session_id: str, request: ResumeSessionRequest):
    """Prepare a session for resumption. Validates state and switches git branch."""
    repo_root = request.repo_root
    db = await get_db(repo_root)
    try:
        session = await get_session_raw(db, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        status = session.get("status")
        if status not in ("active", "completed", "failed"):
            raise HTTPException(
                status_code=400,
                detail=f"Session in status '{status}' cannot be resumed",
            )

        branch_name = session.get("branch_name")
        if branch_name:
            # Stash current changes, switch to the session's branch
            stashed = await git_stash_push(repo_root)
            co_result = await git_checkout(branch_name, repo_root)
            if not co_result.success:
                if stashed:
                    await git_stash_pop(repo_root)
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to checkout {branch_name}: {co_result.error}",
                )
            if stashed:
                await update_session(db, session_id, stashed=True)

        # Reset status to active for resumption
        await update_session(db, session_id, status="active")

        sm = StateManager(session_id)
        state = sm.get_state()
        pad_exists = bool(state.scratchpad_content)

        return {
            "status": "ready",
            "session_id": session_id,
            "branch_name": branch_name,
            "scratchpad_exists": pad_exists,
        }
    finally:
        await db.close()

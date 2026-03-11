"""Session CRUD and search endpoints."""

import logging

from fastapi import APIRouter, HTTPException

from lean_ai.db import (
    create_session,
    delete_session,
    get_conversation_log,
    get_db,
    get_session,
    get_session_raw,
    list_sessions,
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

logger = logging.getLogger(__name__)

sessions_router = APIRouter()


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
async def list_checkpoints(session_id: str):
    """List checkpoints for a session (stub — returns empty list)."""
    return []


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

        from lean_ai.tools.scratchpad import scratchpad_path
        pad_exists = scratchpad_path(repo_root, session_id).is_file()

        return {
            "status": "ready",
            "session_id": session_id,
            "branch_name": branch_name,
            "scratchpad_exists": pad_exists,
        }
    finally:
        await db.close()

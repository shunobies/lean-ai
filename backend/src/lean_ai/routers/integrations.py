"""Integration management and two-way sync endpoints."""

from __future__ import annotations

import dataclasses
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from lean_ai.integrations.base import TaskStatus, WebhookEvent
from lean_ai.integrations.db import get_integrations_db, get_linked_tasks, link_task, unlink_task
from lean_ai.integrations.registry import get_integration, list_integrations
from lean_ai.integrations.summary import build_session_summary

logger = logging.getLogger(__name__)

integrations_router = APIRouter(prefix="/integrations", tags=["integrations"])


# ── Configuration / status ──


@integrations_router.get("")
async def list_integrations_endpoint():
    """List all registered integrations and their status."""
    return list_integrations()


@integrations_router.get("/{name}/health")
async def integration_health(name: str):
    """Check health of a specific integration."""
    integration = get_integration(name)
    if not integration:
        raise HTTPException(404, f"Integration not found or not active: {name}")
    healthy = await integration.check_health()
    return {"name": name, "healthy": healthy}


# ── Pull: fetch external tasks ──


@integrations_router.get("/{name}/tasks")
async def list_external_tasks(
    name: str,
    project: str | None = None,
    status: str | None = None,
    assignee: str | None = None,
    limit: int = 50,
):
    """Fetch tasks from an external service."""
    integration = get_integration(name)
    if not integration:
        raise HTTPException(404, f"Integration not active: {name}")
    task_status = TaskStatus(status) if status else None
    tasks = await integration.list_tasks(
        project=project,
        status=task_status,
        assignee=assignee,
        limit=limit,
    )
    return [dataclasses.asdict(t) for t in tasks]


@integrations_router.get("/{name}/tasks/{external_id}")
async def get_external_task(name: str, external_id: str):
    """Fetch a single task from an external service."""
    integration = get_integration(name)
    if not integration:
        raise HTTPException(404, f"Integration not active: {name}")
    task = await integration.get_task(external_id)
    if not task:
        raise HTTPException(404, f"Task not found: {external_id}")
    return dataclasses.asdict(task)


@integrations_router.get("/{name}/search")
async def search_external_tasks(name: str, q: str, limit: int = 20):
    """Search tasks in an external service."""
    integration = get_integration(name)
    if not integration:
        raise HTTPException(404, f"Integration not active: {name}")
    tasks = await integration.search_tasks(q, limit=limit)
    return [dataclasses.asdict(t) for t in tasks]


# ── Push: send session data to external service ──


class PushSummaryRequest(BaseModel):
    repo_root: str
    session_id: str
    external_id: str


@integrations_router.post("/{name}/push")
async def push_session_summary_endpoint(name: str, req: PushSummaryRequest):
    """Push a session summary to an external task."""
    integration = get_integration(name)
    if not integration:
        raise HTTPException(404, f"Integration not active: {name}")
    summary = await build_session_summary(req.repo_root, req.session_id)
    if not summary:
        raise HTTPException(404, f"Session not found: {req.session_id}")
    success = await integration.push_session_summary(req.external_id, summary)
    return {"success": success}


# ── Task linking ──


class LinkTaskRequest(BaseModel):
    external_id: str
    session_id: str | None = None
    workspace: str | None = None


@integrations_router.post("/{name}/link")
async def link_task_endpoint(name: str, req: LinkTaskRequest):
    """Link an external task to a session or workspace."""
    integration = get_integration(name)
    if not integration:
        raise HTTPException(404, f"Integration not active: {name}")
    task = await integration.get_task(req.external_id)
    if not task:
        raise HTTPException(404, f"External task not found: {req.external_id}")
    db = await get_integrations_db()
    try:
        result = await link_task(
            db,
            name,
            req.external_id,
            session_id=req.session_id,
            workspace=req.workspace,
            title=task.title,
            status=task.status.value if task.status else None,
        )
        return result
    finally:
        await db.close()


class UnlinkTaskRequest(BaseModel):
    link_id: str


@integrations_router.post("/{name}/unlink")
async def unlink_task_endpoint(name: str, req: UnlinkTaskRequest):
    """Unlink an external task."""
    db = await get_integrations_db()
    try:
        success = await unlink_task(db, req.link_id)
        if not success:
            raise HTTPException(404, f"Link not found: {req.link_id}")
        return {"success": True}
    finally:
        await db.close()


@integrations_router.get("/linked/all")
async def get_all_linked_tasks(
    workspace: str | None = None,
    integration_name: str | None = None,
):
    """Get all linked external tasks, optionally filtered."""
    db = await get_integrations_db()
    try:
        return await get_linked_tasks(
            db,
            workspace=workspace,
            integration_name=integration_name,
        )
    finally:
        await db.close()


# ── Webhooks ──


class WebhookRequest(BaseModel):
    event_type: str
    external_id: str
    payload: dict = {}


@integrations_router.post("/{name}/webhook")
async def receive_webhook(name: str, req: WebhookRequest):
    """Receive a webhook event from an external service."""
    integration = get_integration(name)
    if not integration:
        raise HTTPException(404, f"Integration not active: {name}")
    event = WebhookEvent(
        event_type=req.event_type,
        external_id=req.external_id,
        source=name,
        payload=req.payload,
    )
    result = await integration.handle_webhook(event)
    return result

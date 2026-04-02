"""FastAPI REST + WebSocket endpoints — thin aggregator.

Sub-modules under lean_ai.routers/ contain the actual endpoint
implementations, grouped by concern.
"""

from fastapi import APIRouter

from lean_ai.routers.chat import chat_router
from lean_ai.routers.dependencies import llm_client  # noqa: F401
from lean_ai.routers.generation import generation_router
from lean_ai.routers.info import info_router
from lean_ai.routers.integrations import integrations_router
from lean_ai.routers.knowledge_endpoints import knowledge_router
from lean_ai.routers.notes import notes_router
from lean_ai.routers.prompts import prompts_router
from lean_ai.routers.scaffold_endpoints import scaffold_router
from lean_ai.routers.sessions import sessions_router
from lean_ai.routers.voice import voice_router
from lean_ai.routers.workflow import workflow_router

router = APIRouter()
router.include_router(sessions_router)
router.include_router(workflow_router)
router.include_router(generation_router)
router.include_router(chat_router)
router.include_router(info_router)
router.include_router(scaffold_router)
router.include_router(knowledge_router)
router.include_router(notes_router)
router.include_router(prompts_router)
router.include_router(voice_router)
router.include_router(integrations_router)

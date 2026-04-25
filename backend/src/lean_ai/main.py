"""FastAPI application entry point with lifespan management."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lean_ai.router import router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _auto_init_integrations() -> None:
    """Auto-initialize configured integration providers at startup."""
    from lean_ai.config import settings
    from lean_ai.integrations.registry import init_integration

    if not settings.enable_integrations:
        return

    # Jira Cloud
    if settings.jira_url and settings.jira_email and settings.jira_api_token:
        provider = await init_integration(
            "jira",
            base_url=settings.jira_url,
            email=settings.jira_email,
            api_token=settings.jira_api_token,
        )
        if provider:
            logger.info("Jira integration auto-initialized")
        else:
            logger.warning("Jira integration failed to initialize")

    # ServiceNow
    if settings.servicenow_url and settings.servicenow_username and settings.servicenow_password:
        provider = await init_integration(
            "servicenow",
            instance_url=settings.servicenow_url,
            username=settings.servicenow_username,
            password=settings.servicenow_password,
            table=settings.servicenow_table,
        )
        if provider:
            logger.info("ServiceNow integration auto-initialized")
        else:
            logger.warning("ServiceNow integration failed to initialize")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: log readiness. Shutdown: cleanup."""
    logger.info("Starting Lean AI backend...")
    await _auto_init_integrations()
    logger.info("Lean AI backend ready.")
    yield
    # Shutdown integrations
    try:
        from lean_ai.integrations.registry import shutdown_integrations

        await shutdown_integrations()
    except ImportError:
        pass
    # Cleanup headless Chrome if browser search provider was used
    try:
        from lean_ai.tools.browser_search import close_browser

        close_browser()
    except ImportError:
        pass
    # Cleanup voice audio resources
    try:
        from lean_ai.voice.audio_manager import get_audio_manager

        mgr = get_audio_manager()
        if mgr:
            mgr.cleanup()
    except ImportError:
        pass
    logger.info("Shutting down Lean AI backend.")


app = FastAPI(
    title="Lean AI",
    description="Lean agentic coding — plan well, give the LLM tools, let it work.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")

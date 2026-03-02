"""FastAPI application entry point with lifespan management."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lean_ai.router import router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: log readiness. Shutdown: cleanup."""
    logger.info("Starting Lean AI backend...")
    logger.info("Lean AI backend ready.")
    yield
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

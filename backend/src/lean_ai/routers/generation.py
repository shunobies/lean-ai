"""Workspace init, project context, and framework guide generation endpoints."""

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

from lean_ai.config import settings
from lean_ai.indexer.indexer import (
    generate_embeddings as _generate_embeddings,
)
from lean_ai.indexer.indexer import (
    index_workspace as _sync_index_workspace,
)
from lean_ai.routers.context_helpers import ensure_gitignore_entries
from lean_ai.routers.dependencies import llm_client
from lean_ai.routers.models import (
    GenerateFrameworkGuideRequest,
    GenerateFrameworkGuideResponse,
    GenerateProjectContextRequest,
    GenerateProjectContextResponse,
    InitWorkspaceRequest,
    InitWorkspaceResponse,
)

logger = logging.getLogger(__name__)

generation_router = APIRouter()


@generation_router.post("/init-workspace", response_model=InitWorkspaceResponse)
async def init_workspace(request: InitWorkspaceRequest):
    """Index the workspace and prepare for agent workflows.

    Builds the Whoosh search index, fires background embedding generation,
    and triggers knowledge base indexing.
    """
    _gitignore_entries = [
        ".lean_ai/",
        ".lean_ai/scratchpads/",
        f"{settings.index_dir}/",
        f"{settings.knowledge_index_dir}/",
    ]
    added = ensure_gitignore_entries(request.repo_root, _gitignore_entries)
    if added:
        logger.info("Added %d entries to .gitignore: %s", len(added), added)

    index_status = "failed"
    chunk_count = None
    try:
        chunk_count = await asyncio.to_thread(
            _sync_index_workspace, request.repo_root, force=request.force_reindex,
        )
        index_status = "indexed"

        # Background embedding generation
        if settings.enable_embeddings:
            async def _embed_background() -> None:
                try:
                    await _generate_embeddings(request.repo_root, llm_client)
                    logger.info("Background embedding complete for %s", request.repo_root)
                except Exception as exc:
                    logger.debug("Background embedding failed (non-fatal): %s", exc)

            asyncio.create_task(_embed_background())

        # Background knowledge indexing
        async def _index_knowledge_background() -> None:
            try:
                from lean_ai.knowledge.indexer import index_knowledge
                stats = await asyncio.to_thread(index_knowledge, request.repo_root)
                logger.info("Knowledge indexing complete: %s", stats)
            except ImportError:
                logger.debug("Knowledge module not yet available")
            except Exception as exc:
                logger.debug("Knowledge indexing failed (non-fatal): %s", exc)

        asyncio.create_task(_index_knowledge_background())

    except Exception as e:
        logger.warning("Init workspace indexing failed: %s", e)
        index_status = "failed"

    return InitWorkspaceResponse(
        index_status=index_status,
        index_chunk_count=chunk_count,
    )


@generation_router.post("/generate-project-context", response_model=GenerateProjectContextResponse)
async def generate_project_context_endpoint(request: GenerateProjectContextRequest):
    """Generate .lean_ai/project_context.md for the workspace."""
    ctx_path = Path(request.repo_root) / ".lean_ai" / "project_context.md"
    if request.skip_if_exists and ctx_path.is_file():
        return GenerateProjectContextResponse(
            path=str(ctx_path), chars=ctx_path.stat().st_size, skipped=True,
        )

    try:
        from lean_ai.context.generation import generate_project_context, write_project_context
        content = await generate_project_context(request.repo_root, llm_client)
        path = write_project_context(request.repo_root, content)
        return GenerateProjectContextResponse(path=path, chars=len(content))
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="Context generation module not yet available",
        )


@generation_router.post("/generate-framework-guide", response_model=GenerateFrameworkGuideResponse)
async def generate_framework_guide_endpoint(request: GenerateFrameworkGuideRequest):
    """Generate .lean_ai/framework_guide.md for the workspace."""
    guide_path = Path(request.repo_root) / ".lean_ai" / "framework_guide.md"
    if request.skip_if_exists and guide_path.is_file():
        return GenerateFrameworkGuideResponse(
            path=str(guide_path), chars=guide_path.stat().st_size, skipped=True,
        )

    try:
        from lean_ai.context.framework_guide import (
            generate_framework_guide,
            write_framework_guide,
        )

        content = await generate_framework_guide(request.repo_root, llm_client)
        if not content:
            raise HTTPException(
                status_code=404,
                detail="No frameworks detected in the project",
            )
        path = write_framework_guide(request.repo_root, content)
        return GenerateFrameworkGuideResponse(path=path, chars=len(content))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

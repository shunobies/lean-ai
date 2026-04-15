"""Knowledge base indexing endpoint."""

import asyncio
import logging
import os
import shutil

from fastapi import APIRouter, HTTPException

from lean_ai.config import settings
from lean_ai.routers.dependencies import llm_client
from lean_ai.routers.models import IndexKnowledgeRequest, IndexKnowledgeResponse

logger = logging.getLogger(__name__)

knowledge_router = APIRouter()


@knowledge_router.post("/index-knowledge", response_model=IndexKnowledgeResponse)
async def index_knowledge_endpoint(request: IndexKnowledgeRequest):
    """Index the knowledge directory for domain document retrieval."""
    try:
        from lean_ai.knowledge.indexer import (
            generate_knowledge_embeddings,
            index_knowledge,
            knowledge_index_dir,
        )
    except ImportError as exc:
        raise HTTPException(
            status_code=501,
            detail="Knowledge module not yet available",
        ) from exc

    if request.force_reindex:
        idx_path = knowledge_index_dir(request.repo_root)
        if os.path.exists(idx_path):
            shutil.rmtree(idx_path)

    try:
        stats = await asyncio.to_thread(index_knowledge, request.repo_root)
    except Exception as e:
        logger.warning("Knowledge indexing failed: %s", e)
        return IndexKnowledgeResponse(status="failed")

    # Generate embeddings for knowledge chunks (awaited — standalone endpoint).
    embedding_count = 0
    chunk_count = stats.get("chunk_count", 0)
    if settings.enable_embeddings and chunk_count > 0:
        try:
            embedding_count = await generate_knowledge_embeddings(
                request.repo_root, llm_client,
            )
        except Exception as e:
            logger.warning("Knowledge embedding generation failed: %s", e)

    return IndexKnowledgeResponse(
        status=stats.get("status", "indexed"),
        doc_count=stats.get("doc_count", 0),
        chunk_count=chunk_count,
        embedding_count=embedding_count,
    )

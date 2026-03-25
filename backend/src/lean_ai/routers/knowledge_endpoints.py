"""Knowledge base indexing endpoint."""

import asyncio
import logging
import os
import shutil

from fastapi import APIRouter, HTTPException

from lean_ai.routers.models import IndexKnowledgeRequest, IndexKnowledgeResponse

logger = logging.getLogger(__name__)

knowledge_router = APIRouter()


@knowledge_router.post("/index-knowledge", response_model=IndexKnowledgeResponse)
async def index_knowledge_endpoint(request: IndexKnowledgeRequest):
    """Index the knowledge directory for domain document retrieval."""
    try:
        from lean_ai.knowledge.indexer import index_knowledge, knowledge_index_dir
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="Knowledge module not yet available",
        )

    if request.force_reindex:
        idx_path = knowledge_index_dir(request.repo_root)
        if os.path.exists(idx_path):
            shutil.rmtree(idx_path)

    try:
        stats = await asyncio.to_thread(index_knowledge, request.repo_root)
    except Exception as e:
        logger.warning("Knowledge indexing failed: %s", e)
        return IndexKnowledgeResponse(status="failed")

    return IndexKnowledgeResponse(
        status=stats.get("status", "indexed"),
        doc_count=stats.get("doc_count", 0),
        chunk_count=stats.get("chunk_count", 0),
    )

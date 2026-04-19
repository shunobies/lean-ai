"""Reference library indexing endpoint."""

import asyncio
import logging
import os
import shutil

from fastapi import APIRouter, HTTPException

from lean_ai.config import settings
from lean_ai.routers.dependencies import llm_client
from lean_ai.routers.models import IndexReferenceRequest, IndexReferenceResponse

logger = logging.getLogger(__name__)

reference_router = APIRouter()


@reference_router.post("/index-reference", response_model=IndexReferenceResponse)
async def index_reference_endpoint(request: IndexReferenceRequest):
    """Index the reference directory for domain document retrieval."""
    try:
        from lean_ai.reference.indexer import (
            generate_reference_embeddings,
            index_reference,
            reference_index_dir,
        )
    except ImportError as exc:
        raise HTTPException(
            status_code=501,
            detail="Reference module not yet available",
        ) from exc

    if request.force_reindex:
        idx_path = reference_index_dir(request.repo_root)
        if os.path.exists(idx_path):
            shutil.rmtree(idx_path)

    try:
        stats = await asyncio.to_thread(index_reference, request.repo_root)
    except Exception as e:
        logger.warning("Reference indexing failed: %s", e)
        return IndexReferenceResponse(status="failed")

    # Generate embeddings for reference chunks (awaited — standalone endpoint).
    embedding_count = 0
    chunk_count = stats.get("chunk_count", 0)
    if settings.enable_embeddings and chunk_count > 0:
        try:
            embed_stats = await generate_reference_embeddings(
                request.repo_root, llm_client,
            )
            embedding_count = embed_stats.embedded
        except Exception as e:
            logger.warning("Reference embedding generation failed: %s", e)

    return IndexReferenceResponse(
        status=stats.get("status", "indexed"),
        doc_count=stats.get("doc_count", 0),
        chunk_count=chunk_count,
        embedding_count=embedding_count,
    )

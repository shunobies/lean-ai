"""Workspace init, project context, and style guide generation endpoints."""

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from lean_ai.config import settings
from lean_ai.indexer.indexer import (
    generate_embeddings as _generate_embeddings,
)
from lean_ai.indexer.indexer import (
    index_workspace as _sync_index_workspace,
)
from lean_ai.routers.context_helpers import ensure_gitignore_entries
from lean_ai.routers.dependencies import llm_client, request_llm_client, worker_llm_client
from lean_ai.routers.models import (
    GenerateProjectContextRequest,
    GenerateProjectContextResponse,
    GenerateStyleGuideRequest,
    GenerateStyleGuideResponse,
    InitWorkspaceRequest,
    InitWorkspaceResponse,
)

logger = logging.getLogger(__name__)

generation_router = APIRouter()


def _format_embedding_summary(code_stats, know_stats) -> tuple[str, str]:
    """Build ``(status, message)`` for the init response.

    Distinguishes "idle — nothing to do" from "silently broken" so the
    user can tell the difference without reading backend logs.

    Status values:
    - ``success`` — at least one chunk was embedded
    - ``up_to_date`` — all indexed chunks were unchanged (no embed calls)
    - ``partial`` — some batches succeeded, some failed
    - ``failed`` — every batch failed
    - ``skipped`` — nothing to index (covered by caller, not here)
    """
    def _fmt(label: str, s) -> str | None:
        if s is None:
            return None
        if s.embedded > 0 and s.failed_batches == 0:
            return f"{s.embedded} {label} embedded ({s.unchanged} unchanged)"
        if s.embedded > 0 and s.failed_batches > 0:
            return (
                f"{s.embedded} {label} embedded, "
                f"{s.failed_batches}/{s.total_batches} batches failed"
            )
        if s.embedded == 0 and s.failed_batches > 0:
            return (
                f"{label}: all {s.failed_batches} batches failed "
                f"— check Ollama logs"
            )
        # embedded == 0, no failures → up-to-date
        total = s.unchanged + s.orphaned_removed
        if total == 0:
            return f"no {label} to embed"
        return f"{s.unchanged} {label} already up to date"

    parts = [p for p in (_fmt("code chunks", code_stats),
                         _fmt("knowledge chunks", know_stats)) if p]

    code_failed = code_stats is not None and code_stats.failed_batches > 0
    know_failed = know_stats is not None and know_stats.failed_batches > 0
    code_embedded = code_stats is not None and code_stats.embedded > 0
    know_embedded = know_stats is not None and know_stats.embedded > 0
    any_embedded = code_embedded or know_embedded
    any_failed = code_failed or know_failed
    any_ran = code_stats is not None or know_stats is not None

    if not any_ran:
        return "failed", "Embedding task did not produce stats"
    if any_embedded and not any_failed:
        status = "success"
    elif any_embedded and any_failed:
        status = "partial"
    elif any_failed and not any_embedded:
        status = "failed"
    else:
        status = "up_to_date"

    message = "; ".join(parts) if parts else "No chunks to embed"
    return status, message


@generation_router.post("/init-workspace", response_model=InitWorkspaceResponse)
async def init_workspace(request: InitWorkspaceRequest):
    """Index the workspace and prepare for agent workflows.

    Builds the Whoosh search index, generates embeddings, and triggers
    knowledge base indexing.  Embedding generation is awaited (not
    fire-and-forget) so the caller sees the actual result.
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
        # Commit immediately so branch switches don't lose .gitignore
        from lean_ai.tools.git_ops import git_commit, git_is_repo
        if await git_is_repo(request.repo_root):
            commit_result = await git_commit(
                message="chore: add lean-ai entries to .gitignore",
                files=[".gitignore"],
                repo_root=request.repo_root,
            )
            if commit_result.success:
                logger.info("Committed .gitignore changes")
            else:
                logger.warning(
                    "Failed to commit .gitignore: %s", commit_result.error,
                )

    # Clean up stale tool output from previous sessions
    from lean_ai.workflow.tool_executor import cleanup_all_tool_output
    cleaned = cleanup_all_tool_output(request.repo_root)
    if cleaned:
        logger.info("Cleaned up %d stale tool output file(s)", cleaned)

    index_status = "failed"
    file_count = None
    chunk_count = None
    try:
        file_count, chunk_count = await asyncio.to_thread(
            _sync_index_workspace, request.repo_root, force=request.force_reindex,
        )
        index_status = "indexed"

        # Auto-detect lint/test/format commands
        from lean_ai.context.command_detection import (
            detect_commands,
            write_commands_json,
        )

        detected_commands = detect_commands(request.repo_root)
        if any(detected_commands.values()):
            write_commands_json(request.repo_root, detected_commands)
            logger.info("Auto-detected commands: %s", detected_commands)

        # Knowledge indexing (awaited — fast I/O, results needed for response)
        knowledge_status: str | None = None
        knowledge_doc_count: int | None = None
        knowledge_chunk_count: int | None = None
        knowledge_skipped_extensions: list[str] | None = None
        try:
            from lean_ai.knowledge.indexer import index_knowledge
            kstats = await asyncio.to_thread(index_knowledge, request.repo_root)
            knowledge_status = kstats.get("status")
            knowledge_doc_count = kstats.get("doc_count", 0)
            knowledge_chunk_count = kstats.get("chunk_count", 0)
            knowledge_skipped_extensions = kstats.get("skipped_extensions")
            logger.info("Knowledge indexing complete: %s", kstats)
        except ImportError:
            logger.debug("Knowledge module not available")
        except Exception as exc:
            logger.warning("Knowledge indexing failed: %s", exc)
            knowledge_status = "failed"

        # Embedding generation (code + knowledge) — run in parallel via gather
        embedding_status = "skipped"
        embedding_code_count = 0
        embedding_knowledge_count = 0
        embedding_code_unchanged = 0
        embedding_knowledge_unchanged = 0
        embedding_failed_batches = 0
        embedding_total_batches = 0
        embedding_message = ""

        embed_ok, embed_msg = await llm_client.check_embedding_model()
        if not embed_ok:
            embedding_status = "skipped"
            embedding_message = embed_msg
            logger.info("Embedding skipped: %s", embed_msg)
        else:
            try:
                embed_tasks: list[asyncio.Task] = [
                    asyncio.create_task(
                        _generate_embeddings(request.repo_root, llm_client),
                    ),
                ]

                _knowledge_chunks = knowledge_chunk_count or 0
                if _knowledge_chunks > 0:
                    try:
                        from lean_ai.knowledge.indexer import generate_knowledge_embeddings
                        embed_tasks.append(
                            asyncio.create_task(
                                generate_knowledge_embeddings(
                                    request.repo_root, llm_client,
                                ),
                            ),
                        )
                    except ImportError:
                        pass

                results = await asyncio.gather(
                    *embed_tasks, return_exceptions=True,
                )

                # Unpack results — each task returns an EmbeddingRunStats
                # dataclass (or Exception on failure).
                code_stats = None
                code_result = results[0]
                if isinstance(code_result, Exception):
                    logger.warning("Code embedding failed: %s", code_result)
                else:
                    code_stats = code_result
                    embedding_code_count = code_stats.embedded
                    embedding_code_unchanged = code_stats.unchanged
                    embedding_failed_batches += code_stats.failed_batches
                    embedding_total_batches += code_stats.total_batches
                    logger.info(
                        "Code embedding complete: +%d embedded, %d unchanged, "
                        "%d orphaned removed, %d/%d batches failed",
                        code_stats.embedded, code_stats.unchanged,
                        code_stats.orphaned_removed,
                        code_stats.failed_batches, code_stats.total_batches,
                    )

                know_stats = None
                if len(results) > 1:
                    know_result = results[1]
                    if isinstance(know_result, Exception):
                        logger.warning(
                            "Knowledge embedding failed: %s", know_result,
                        )
                    else:
                        know_stats = know_result
                        embedding_knowledge_count = know_stats.embedded
                        embedding_knowledge_unchanged = know_stats.unchanged
                        embedding_failed_batches += know_stats.failed_batches
                        embedding_total_batches += know_stats.total_batches
                        logger.info(
                            "Knowledge embedding complete: +%d embedded, "
                            "%d unchanged, %d orphaned removed, "
                            "%d/%d batches failed",
                            know_stats.embedded, know_stats.unchanged,
                            know_stats.orphaned_removed,
                            know_stats.failed_batches,
                            know_stats.total_batches,
                        )

                # Build a user-visible message that distinguishes
                # "nothing to do" from "silently broken". The old message
                # collapsed both into "No chunks to embed" which confused
                # users into thinking /init was broken when it was idle.
                embedding_status, embedding_message = _format_embedding_summary(
                    code_stats, know_stats,
                )
            except Exception as exc:
                embedding_status = "failed"
                embedding_message = str(exc)
                logger.warning("Embedding generation failed: %s", exc)

    except Exception as e:
        logger.warning("Init workspace indexing failed: %s", e)
        index_status = "failed"
        detected_commands = {}
        knowledge_status = None
        knowledge_doc_count = None
        knowledge_chunk_count = None
        knowledge_skipped_extensions = None
        embedding_status = "failed"
        embedding_code_count = 0
        embedding_knowledge_count = 0
        embedding_code_unchanged = 0
        embedding_knowledge_unchanged = 0
        embedding_failed_batches = 0
        embedding_total_batches = 0
        embedding_message = str(e)

    return InitWorkspaceResponse(
        index_status=index_status,
        index_file_count=file_count,
        index_chunk_count=chunk_count,
        commands_detected=(
            detected_commands if detected_commands and any(detected_commands.values())
            else None
        ),
        num_parallel=settings.num_parallel,
        knowledge_status=knowledge_status,
        knowledge_doc_count=knowledge_doc_count,
        knowledge_chunk_count=knowledge_chunk_count,
        knowledge_skipped_extensions=knowledge_skipped_extensions,
        embedding_status=embedding_status,
        embedding_code_count=embedding_code_count,
        embedding_knowledge_count=embedding_knowledge_count,
        embedding_code_unchanged=embedding_code_unchanged,
        embedding_knowledge_unchanged=embedding_knowledge_unchanged,
        embedding_failed_batches=embedding_failed_batches,
        embedding_total_batches=embedding_total_batches,
        embedding_message=embedding_message,
    )


@generation_router.post("/generate-project-context", response_model=GenerateProjectContextResponse)
async def generate_project_context_endpoint(request: GenerateProjectContextRequest):
    """Generate .lean_ai/project_context.md for the workspace."""
    ctx_path = Path(request.repo_root) / ".lean_ai" / "project_context.md"
    if request.skip_if_exists and ctx_path.is_file():
        if request.stream:
            return _sse_skipped(str(ctx_path), ctx_path.stat().st_size)
        return GenerateProjectContextResponse(
            path=str(ctx_path), chars=ctx_path.stat().st_size, skipped=True,
        )

    if request.stream:
        return _sse_generation_response(
            request.repo_root, "project_context",
        )

    try:
        from lean_ai.context.generation import generate_project_context
        content = await generate_project_context(
            request.repo_root,
            llm_client,
            worker_client=worker_llm_client,
            request_client=request_llm_client,
        )
        return GenerateProjectContextResponse(path=str(ctx_path), chars=len(content))
    except ImportError as exc:
        raise HTTPException(
            status_code=501,
            detail="Context generation module not yet available",
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@generation_router.post("/generate-style-guide", response_model=GenerateStyleGuideResponse)
async def generate_style_guide_endpoint(request: GenerateStyleGuideRequest):
    """Generate .lean_ai/context/style_guide.md for the workspace."""
    guide_path = Path(request.repo_root) / ".lean_ai" / "context" / "style_guide.md"
    if request.skip_if_exists and guide_path.is_file():
        if request.stream:
            return _sse_skipped(str(guide_path), guide_path.stat().st_size)
        return GenerateStyleGuideResponse(
            path=str(guide_path), chars=guide_path.stat().st_size, skipped=True,
        )

    if request.stream:
        return _sse_generation_response(
            request.repo_root, "style_guide",
        )

    try:
        from lean_ai.context.style_guide import (
            generate_style_guide,
            write_style_guide,
        )

        _client = request_llm_client or llm_client
        content = await generate_style_guide(request.repo_root, _client)
        if not content:
            raise HTTPException(
                status_code=404,
                detail="No style files detected in the project",
            )
        path = write_style_guide(request.repo_root, content)
        return GenerateStyleGuideResponse(path=path, chars=len(content))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# SSE streaming helpers
# ---------------------------------------------------------------------------

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


def _sse_event(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def _sse_skipped(path: str, size: int) -> StreamingResponse:
    """Return a minimal SSE stream for a skipped (already-exists) result."""
    async def _gen():
        yield _sse_event({"type": "result", "path": path, "chars": size, "skipped": True})
        yield _sse_event({"type": "done"})

    return StreamingResponse(_gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


def _sse_generation_response(
    repo_root: str,
    kind: str,
) -> StreamingResponse:
    """Return an SSE StreamingResponse that runs generation with thinking tokens.

    *kind* is one of ``"project_context"`` or ``"style_guide"``.
    """
    async def _generate():
        queue: asyncio.Queue[dict | None] = asyncio.Queue()

        async def thinking_cb(token: str) -> None:
            await queue.put({"type": "thinking", "content": token})

        async def progress_cb(event: dict) -> None:
            await queue.put({"type": "progress", **event})

        async def _run() -> None:
            try:
                _client = request_llm_client or llm_client

                if kind == "project_context":
                    from lean_ai.context.generation import generate_project_context
                    content = await generate_project_context(
                        repo_root, llm_client,
                        thinking_callback=thinking_cb,
                        progress_callback=progress_cb,
                        worker_client=worker_llm_client,
                        request_client=request_llm_client,
                    )
                    path = str(Path(repo_root) / ".lean_ai" / "project_context.md")
                    await queue.put({
                        "type": "result", "path": path, "chars": len(content),
                    })

                elif kind == "style_guide":
                    from lean_ai.context.style_guide import (
                        generate_style_guide,
                        write_style_guide,
                    )
                    content = await generate_style_guide(
                        repo_root, _client, thinking_callback=thinking_cb,
                    )
                    if not content:
                        await queue.put({
                            "type": "error",
                            "message": "No style files detected in the project",
                            "status": 404,
                        })
                        return
                    path = write_style_guide(repo_root, content)
                    await queue.put({
                        "type": "result", "path": path, "chars": len(content),
                    })

            except Exception as exc:
                logger.exception("SSE generation (%s) failed: %s", kind, exc)
                await queue.put({"type": "error", "message": str(exc)})
            finally:
                await queue.put(None)  # sentinel

        task = asyncio.create_task(_run())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield _sse_event(event)
        except asyncio.CancelledError:
            task.cancel()
            raise
        yield _sse_event({"type": "done"})

    return StreamingResponse(
        _generate(), media_type="text/event-stream", headers=_SSE_HEADERS,
    )

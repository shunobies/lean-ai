"""Chat and chat-streaming endpoints."""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from lean_ai.config import settings
from lean_ai.llm.refiner import RefinerResult
from lean_ai.llm.tool_definitions import build_chat_tools
from lean_ai.routers.context_helpers import (
    build_chat_system_prompt,
    extract_urls,
    get_file_tree,
    read_active_file,
    read_project_context,
    search_workspace,
)
from lean_ai.routers.dependencies import (
    llm_client,
    refiner,
    request_llm_client,
    resolve_image_handler,
)
from lean_ai.routers.models import ChatRequest, ChatResponse
from lean_ai.tools import internet
from lean_ai.tools.descriptions import humanize_tool_call

logger = logging.getLogger(__name__)

chat_router = APIRouter()

# Max tool-calling turns for chat exploration
_CHAT_MAX_TURNS = 20
# Hard cap when ChatRequest.extended_turns is supplied (used by the
# /mock-interview extension command for multi-round scored Q&A).
_CHAT_EXTENDED_TURNS_MAX = 40


def _resolve_max_turns(extended: int | None) -> int:
    """Return the effective tool-turn budget for a chat request."""
    if extended is None:
        return _CHAT_MAX_TURNS
    if extended <= _CHAT_MAX_TURNS:
        return _CHAT_MAX_TURNS
    return min(extended, _CHAT_EXTENDED_TURNS_MAX)


def _get_chat_client():
    """Return the active client for chat endpoints — request model when configured, else primary."""
    return request_llm_client or llm_client


def _chat_telemetry_context(repo_root: str | None) -> dict | None:
    """Build a telemetry context for a chat request.

    Chat runs don't have a persistent session_id, so one is minted per
    call. Rows land in the workspace training DB only when a workspace
    is open — ephemeral chats (no repo_root) are skipped to keep the
    archive scoped to a project.
    """
    if not repo_root:
        return None
    return {
        "repo_root": repo_root,
        "session_id": f"chat-{uuid.uuid4().hex[:12]}",
        "phase": "chat",
        "role": "request" if request_llm_client is not None else "primary",
    }


def _get_chat_max_tokens() -> int:
    """Return max_tokens for the chat client (request model when configured, else primary)."""
    if request_llm_client is None:
        p = settings.llm_provider.lower()
        if p == "openai":
            return settings.openai_max_tokens
        if p == "anthropic":
            return settings.anthropic_max_tokens
        return settings.ollama_max_tokens
    return settings.effective_request_max_tokens


# ── Read-only tool executor for chat ────────────────────────────────


def _make_chat_tool_executor(repo_root: str | None = None):
    """Create a tool executor for chat exploration.

    Workspace tools (read_file, grep_files, etc.) require *repo_root*.
    Search tools (search_internet, fetch_url) work without a workspace.
    """

    async def _executor(name: str, arguments: dict) -> str:
        # ── Workspace tools (need repo_root) ──
        if (
            name
            in (
                "read_file",
                "grep_files",
                "list_directory",
                "directory_tree",
                "save_note",
                "list_project_todos",
                "list_recent_sessions",
                "get_session_summary",
                "search_workspace_memory",
            )
            and not repo_root
        ):
            return f"ERROR: {name} requires an open workspace."

        from lean_ai.tools.file_ops import grep_files, read_file
        from lean_ai.workflow.tool_executor import _is_external_path

        if name == "read_file":
            target_path = arguments.get("path", "")
            external = _is_external_path(target_path, repo_root)
            result = await read_file(
                path=target_path,
                repo_root=repo_root,
                start_line=arguments.get("start_line"),
                end_line=arguments.get("end_line"),
                allow_external=external,
            )
            return result.output if result.success else result.error or "Error"
        elif name == "grep_files":
            result = await grep_files(
                pattern=arguments.get("pattern", ""),
                repo_root=repo_root,
                file_glob=arguments.get("file_glob"),
            )
            return result.output if result.success else result.error or "Error"
        elif name == "list_directory":
            target = Path(repo_root) / arguments.get("path", "")
            if not target.is_dir():
                return f"Not a directory: {arguments.get('path', '')}"
            max_entries = arguments.get("max_entries", 100)
            entries = sorted(target.iterdir())[:max_entries]
            lines = []
            for e in entries:
                prefix = "d" if e.is_dir() else "f"
                lines.append(f"  {prefix}  {e.name}")
            return "\n".join(lines) or "(empty)"
        elif name == "directory_tree":
            from lean_ai.indexer.tree import list_repo_tree

            sub_path = arguments.get("path", "")
            tree_root = f"{repo_root}/{sub_path}" if sub_path else repo_root
            tree_entries = list_repo_tree(tree_root)
            max_depth = arguments.get("max_depth", 3)
            lines = []
            for e in tree_entries[:200]:
                depth = e.path.count("/")
                if depth <= max_depth:
                    indent = "  " * depth
                    lines.append(f"{indent}{e.path.split('/')[-1]}")
            return "\n".join(lines) or "(empty)"
        elif name == "save_note":
            from lean_ai.notes_db import create_note as _create_note
            from lean_ai.notes_db import get_notes_db
            from lean_ai.notes_index import index_note
            from lean_ai.notes_llm import schedule_categorization

            content = arguments.get("content", "")
            db = await get_notes_db()
            try:
                note = await _create_note(db, content, repo_root or None)
                index_note(note_id=note["id"], content=content)
                schedule_categorization(llm_client, note["id"], content, repo_root or None)
                return f"Note saved (id: {note['id']}). Categorization in progress."
            finally:
                await db.close()
        elif name == "list_project_todos":
            from lean_ai.notes_db import get_notes_db, list_todos_by_project

            project = arguments.get("project", "")
            db = await get_notes_db()
            try:
                todos = await list_todos_by_project(
                    db,
                    project=project or None,
                    source_workspace=repo_root or None,
                    pending_only=True,
                )
                if not todos:
                    return "No pending TODOs found for this project."
                lines = []
                for t in todos:
                    status = "done" if t["completed"] else "pending"
                    lines.append(
                        f"- [{status}] {t['description']} (from note: {t['note_content']}...)"
                    )
                return "\n".join(lines)
            finally:
                await db.close()

        # ── Session history tools (need repo_root) ──
        elif name == "list_recent_sessions":
            from lean_ai.memory.session_tools import list_recent_sessions

            return await list_recent_sessions(
                repo_root,
                limit=arguments.get("limit", 5),
            )
        elif name == "get_session_summary":
            from lean_ai.memory.session_tools import get_session_summary
            from lean_ai.routers.dependencies import worker_llm_client

            return await get_session_summary(
                repo_root,
                session_id=arguments.get("session_id", ""),
                llm=worker_llm_client,
            )
        elif name == "search_workspace_memory":
            from lean_ai.memory.session_tools import (
                search_session_history,
                search_workspace_memories,
            )

            query = arguments.get("query", "")
            # Search both session titles and extracted memories
            sessions = await search_session_history(repo_root, query)
            memories = search_workspace_memories(repo_root, query)
            parts = []
            if sessions and "No sessions found" not in sessions:
                parts.append(sessions)
            if memories and "No memories found" not in memories:
                parts.append(memories)
            return "\n\n".join(parts) if parts else f"No results for '{query}'."

        # ── Search tools (no workspace needed) ──
        elif name == "search_internet":
            from lean_ai.tools.internet import search_internet

            result = await search_internet(
                query=arguments.get("query", ""),
                llm_client=llm_client,
            )
            return result.output if result.success else f"ERROR: {result.error}"
        elif name == "fetch_url":
            from lean_ai.tools.internet import fetch_url

            result = await fetch_url(
                url=arguments.get("url", ""),
                repo_root=repo_root or "",
                llm_client=llm_client,
            )
            return result.output if result.success else f"ERROR: {result.error}"

        elif name == "query_project_context":
            if not repo_root:
                return "ERROR: No workspace root available"
            from lean_ai.context.context_db import (
                get_context_db,
                query_entries,
            )

            db = await get_context_db(repo_root)
            try:
                results = await query_entries(
                    db,
                    section=arguments.get("section"),
                    file_path=arguments.get("file_path"),
                    keyword=arguments.get("keyword"),
                )
                if not results:
                    return "No matching context entries found."
                lines = []
                for r in results:
                    lines.append(f"[{r['section']}] {r['content']}")
                return "\n".join(lines)
            finally:
                await db.close()

        elif name == "search_reference":
            if not repo_root:
                return "ERROR: search_reference requires an open workspace."
            from lean_ai.reference.indexer import is_reference_available
            from lean_ai.reference.indexer import search_reference as _search_reference

            if not is_reference_available(repo_root):
                return (
                    "ERROR: No reference library index found. "
                    "Place documents in .lean_ai/reference/ and run /init to index them."
                )

            query = arguments.get("query", "")
            limit = arguments.get("limit", 10)

            # Best-effort embedding for RRF re-ranking
            query_embedding: list[float] | None = None
            try:
                embeddings = await llm_client.embed([query])
                if embeddings:
                    query_embedding = embeddings[0]
            except Exception:
                pass

            chunks = await asyncio.to_thread(
                _search_reference,
                repo_root,
                query,
                limit,
                query_embedding,
            )
            if not chunks:
                return f"No reference library results for '{query}'."

            parts = []
            for chunk in chunks:
                title = chunk.get("doc_title", "Unknown")
                section = chunk.get("section", "")
                content = chunk.get("content", "")
                header = f"[{title} > {section}]" if section else f"[{title}]"
                parts.append(f"{header}\n{content}")
            return "\n\n---\n\n".join(parts)

        return f"Unknown tool: {name}"

    return _executor


# ── Context building ────────────────────────────────────────────────


async def _build_chat_messages(
    request: ChatRequest,
) -> tuple[list[dict], RefinerResult | None, str]:
    """Gather workspace context and build the LLM message list for a chat request.

    Shared by both the blocking ``/chat`` endpoint and the streaming
    ``/chat/stream`` endpoint so context-gathering logic lives in one place.

    Returns:
        Tuple of (messages, refiner_result, image_descriptions).
    """
    workspace = request.workspace
    file_tree: list[str] = []
    active_file_content: str | None = None
    search_results: list[dict] = []
    project_context: str | None = None
    fetched_pages: list[dict] = []
    refiner_result: RefinerResult | None = None

    async def _refine_message():
        nonlocal refiner_result
        if refiner is None:
            return
        try:
            root = workspace.workspace_root if workspace else None
            refiner_result = await refiner.refine_chat_message(
                user_message=request.message,
                repo_root=root,
                history=request.history,
            )
        except Exception as e:
            logger.warning("Refiner failed (non-fatal): %s", e)

    async def _gather_workspace_context():
        nonlocal file_tree, active_file_content, search_results, project_context
        try:
            if not (workspace and workspace.workspace_root):
                return
            root = workspace.workspace_root

            file_tree = await asyncio.to_thread(get_file_tree, root)
            project_context = await asyncio.to_thread(read_project_context, root)

            if workspace.active_file and not workspace.active_selection:
                active_file_content = await asyncio.to_thread(
                    read_active_file,
                    root,
                    workspace.active_file,
                )

            if request.message and len(request.message) > 5:
                search_results = await asyncio.to_thread(
                    search_workspace,
                    root,
                    request.message,
                    8,
                )
        except Exception as e:
            logger.warning("Chat workspace context failed (non-fatal): %s", e)

    async def _fetch_urls():
        nonlocal fetched_pages
        urls = extract_urls(request.message)
        for url in urls[:3]:
            try:
                result = await internet.fetch_url(
                    url,
                    llm_client=llm_client,
                )
                if result.success:
                    fetched_pages.append({"url": url, "content": result.output})
                else:
                    fetched_pages.append(
                        {
                            "url": url,
                            "content": f"(Failed to fetch: {result.error})",
                        }
                    )
            except Exception as e:
                logger.warning("Chat URL fetch failed for %s: %s", url, e)
                fetched_pages.append({"url": url, "content": f"(Failed to fetch: {e})"})

    recent_activity: str = ""

    async def _gather_session_context():
        nonlocal recent_activity
        if not (workspace and workspace.workspace_root):
            return
        try:
            from lean_ai.memory.session_tools import get_recent_activity_context

            recent_activity = await get_recent_activity_context(
                workspace.workspace_root,
                request.message,
            )
        except Exception as e:
            logger.debug("Session context lookup failed (non-fatal): %s", e)

    image_descriptions: str = ""

    # Resolve up front so _describe_attachments can skip when the active
    # role will handle the image natively.
    image_attachments_raw: list[tuple[str, str]] = [
        (a.data, a.mime_type)
        for a in (request.attachments or [])
        if a.mime_type and a.mime_type.startswith("image/")
    ]
    image_handler = resolve_image_handler("chat") if image_attachments_raw else None
    image_mode = image_handler[0] if image_handler is not None else None

    async def _describe_attachments():
        nonlocal image_descriptions
        from lean_ai.llm.vision import (
            describe_images,
            format_image_descriptions,
            is_vision_available,
        )

        # Only run the legacy describe path when no role is flagged for
        # inline image handling.  "inline" branch attaches image blocks
        # directly to the main chat call below — no describe call needed.
        if image_mode != "describe":
            return
        if not is_vision_available():
            return
        image_attachments = [
            {"data": data, "filename": ""} for data, _mime in image_attachments_raw
        ]
        if not image_attachments:
            return
        try:
            results = await describe_images(
                image_attachments,
                prompt=request.message,
            )
            image_descriptions = format_image_descriptions(results)
        except Exception as e:
            logger.warning("Image description failed (non-fatal): %s", e)

    await asyncio.gather(
        _gather_workspace_context(),
        _gather_session_context(),
        _fetch_urls(),
        _refine_message(),
        _describe_attachments(),
    )

    # Use refined message if available, otherwise original
    user_message = request.message
    reference_ctx: str | None = None
    if refiner_result and refiner_result.was_refined:
        user_message = refiner_result.refined
    if refiner_result and refiner_result.reference_context:
        reference_ctx = refiner_result.reference_context

    system_prompt = build_chat_system_prompt(
        workspace=workspace,
        file_tree=file_tree,
        active_file_content=active_file_content,
        search_results=search_results,
        project_context=project_context,
        fetched_pages=fetched_pages or None,
        reference_context=reference_ctx,
        user_name=request.user_name,
        recent_sessions=recent_activity or None,
        max_turns=_resolve_max_turns(request.extended_turns),
    )
    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    # Append image descriptions to the user message (describe branch).
    if image_descriptions:
        user_message = f"{user_message}\n\n{image_descriptions}"

    # Warn the user in-band if images were supplied but no handler is configured.
    if image_attachments_raw and image_handler is None:
        user_message = (
            f"{user_message}\n\n[System: {len(image_attachments_raw)} image "
            "attachment(s) dropped — no vision-capable model is configured. "
            "Enable 'Supports Image' on a vision-capable role in Settings, "
            "or set LEAN_AI_VISION_MODEL.]"
        )

    for msg in request.history[-20:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_message})

    # Inline-handler branch: attach each image to the last user message in
    # the active provider's native content-block shape, bypassing the
    # describe round-trip entirely.
    if image_mode == "inline" and image_handler is not None:
        from lean_ai.llm.media_messages import CapabilityError, attach_image

        target_client = image_handler[1]
        provider = target_client.provider_name
        try:
            for img_b64, mime_type in image_attachments_raw:
                messages = attach_image(
                    messages,
                    img_b64,
                    mime_type,
                    provider=provider,
                )
        except CapabilityError as exc:
            logger.warning(
                "Inline image handler %s refused: %s; falling back to describe",
                provider,
                exc,
            )
            # Fall back to describe-and-inject if vision_model is set.
            if settings.vision_model:
                from lean_ai.llm.vision import (
                    describe_images,
                    format_image_descriptions,
                )

                try:
                    fallback = await describe_images(
                        [{"data": d, "filename": ""} for d, _ in image_attachments_raw],
                        prompt=request.message,
                    )
                    fallback_prose = format_image_descriptions(fallback)
                    if fallback_prose:
                        messages[-1]["content"] = f"{messages[-1]['content']}\n\n{fallback_prose}"
                except Exception as e:
                    logger.warning(
                        "Describe fallback failed after CapabilityError: %s",
                        e,
                    )

    logger.info(
        "Chat: history=%d, files=%d, search=%d, project_ctx=%s, refined=%s, images=%d",
        len(request.history),
        len(file_tree),
        len(search_results),
        bool(project_context),
        bool(refiner_result and refiner_result.was_refined),
        len(request.attachments),
    )

    return messages, refiner_result, image_descriptions


# ── Blocking chat endpoint ──────────────────────────────────────────


@chat_router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Read-only chat with workspace context and optional tool exploration.

    When a workspace root is available, the LLM has access to read-only
    tools (read_file, grep_files, list_directory, directory_tree) to
    explore the codebase on demand.
    """
    try:
        messages, refiner_result, image_desc = await _build_chat_messages(request)
        _chat_client = _get_chat_client()

        repo_root = (
            request.workspace.workspace_root
            if request.workspace and request.workspace.workspace_root
            else None
        )

        executor = _make_chat_tool_executor(repo_root)
        tools = build_chat_tools()
        if not repo_root:
            # Without a workspace, only expose search tools
            tools = [t for t in tools if t["function"]["name"] in ("search_internet", "fetch_url")]
        telemetry_context = _chat_telemetry_context(repo_root)
        _, reply = await _chat_client.chat_with_tools(
            messages=messages,
            tools=tools,
            tool_executor_fn=executor,
            max_turns=_resolve_max_turns(request.extended_turns),
            max_tokens=_get_chat_max_tokens(),
            text_only_exit_count=1,
            telemetry_context=telemetry_context,
        )

        metrics = _chat_client.last_chat_metrics or {}
        return ChatResponse(
            reply=reply,
            tokens_per_second=metrics.get("tokens_per_second"),
            eval_count=metrics.get("eval_count"),
            refined=bool(refiner_result and refiner_result.was_refined),
            privacy_redactions=(len(refiner_result.privacy_redactions) if refiner_result else 0),
            image_descriptions=image_desc or None,
        )
    except Exception as e:
        logger.exception("Chat call failed")
        return ChatResponse(reply=f"Error: {e}")


# ── Streaming helpers ───────────────────────────────────────────────


async def _stream_chat_with_tools(
    messages: list[dict],
    repo_root: str | None = None,
    max_turns: int = _CHAT_MAX_TURNS,
) -> AsyncGenerator[str, None]:
    """Stream LLM response with tool exploration.

    Runs ``chat_with_tools`` in a background task. Content and thinking
    tokens are streamed via callbacks that push to an asyncio queue.
    The SSE generator pulls events from the queue.
    """
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def _on_content(token: str) -> None:
        await queue.put({"type": "token", "content": token})

    async def _on_thinking(token: str) -> None:
        await queue.put({"type": "thinking", "content": token})

    async def _on_tool_call(name: str, args: dict) -> None:
        desc = humanize_tool_call(name, args)
        await queue.put({"type": "tool_call", "name": name, "description": desc})

    async def _on_tool_result(name: str, result: str) -> None:
        success = not result.startswith("ERROR:")
        await queue.put({"type": "tool_result", "name": name, "success": success})

    executor = _make_chat_tool_executor(repo_root)
    tools = build_chat_tools()
    if not repo_root:
        tools = [t for t in tools if t["function"]["name"] in ("search_internet", "fetch_url")]

    async def _run():
        try:
            await _get_chat_client().chat_with_tools(
                messages=messages,
                tools=tools,
                tool_executor_fn=executor,
                max_turns=max_turns,
                max_tokens=_get_chat_max_tokens(),
                text_only_exit_count=1,
                stream_content=True,
                on_content=_on_content,
                on_thinking=_on_thinking,
                on_tool_call=_on_tool_call,
                on_tool_result=_on_tool_result,
                telemetry_context=_chat_telemetry_context(repo_root),
            )
        except Exception as e:
            await queue.put({"type": "error", "message": str(e)})
        finally:
            await queue.put(None)  # sentinel

    task = asyncio.create_task(_run())

    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield f"data: {json.dumps(event)}\n\n"
    except asyncio.CancelledError:
        task.cancel()
        raise

    yield f"data: {json.dumps({'type': 'done'})}\n\n"


# ── Streaming chat endpoint ─────────────────────────────────────────


@chat_router.post("/chat/stream")
async def chat_stream_endpoint(request: ChatRequest):
    """Chat with workspace context, streaming tokens via Server-Sent Events.

    When a workspace root is available the LLM can explore the codebase
    with read-only tools. Tool activity is streamed as SSE events alongside
    content and thinking tokens.

    Event format::

        data: {"type": "token", "content": "..."}\n\n
        data: {"type": "thinking", "content": "..."}\n\n
        data: {"type": "tool_call", "name": "...", "description": "..."}\n\n
        data: {"type": "tool_result", "name": "...", "success": true}\n\n
        data: {"type": "vision_description", "descriptions": "..."}\n\n
        data: {"type": "done"}\n\n
        data: {"type": "error", "message": "..."}\n\n
    """

    async def generate() -> AsyncGenerator[str, None]:
        try:
            messages, _, image_desc = await _build_chat_messages(request)

            # Surface vision descriptions to the client before LLM tokens
            if image_desc:
                event = {"type": "vision_description", "descriptions": image_desc}
                yield f"data: {json.dumps(event)}\n\n"

            prompt_chars = sum(len(m.get("content", "")) for m in messages)
            logger.info(
                "Chat stream: prompt ~%d chars (~%d tokens), num_ctx=%d",
                prompt_chars,
                prompt_chars // 4,
                settings._active_context_window,
            )

            repo_root = (
                request.workspace.workspace_root
                if request.workspace and request.workspace.workspace_root
                else None
            )

            max_turns = _resolve_max_turns(request.extended_turns)
            async for sse_event in _stream_chat_with_tools(messages, repo_root, max_turns):
                yield sse_event

        except Exception as e:
            logger.exception("Chat stream failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

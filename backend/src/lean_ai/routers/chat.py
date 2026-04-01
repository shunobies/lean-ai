"""Chat and chat-streaming endpoints."""

import asyncio
import json
import logging
import re
from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from lean_ai.config import settings
from lean_ai.llm.refiner import RefinerResult
from lean_ai.llm.tool_definitions import CHAT_TOOLS
from lean_ai.routers.context_helpers import (
    build_chat_system_prompt,
    extract_urls,
    get_file_tree,
    read_active_file,
    read_project_context,
    search_workspace,
)
from lean_ai.routers.dependencies import llm_client, refiner, request_llm_client
from lean_ai.routers.models import ChatRequest, ChatResponse
from lean_ai.tools import internet

logger = logging.getLogger(__name__)

chat_router = APIRouter()

# Max tool-calling turns for chat exploration
_CHAT_MAX_TURNS = 20

# Words that carry no search value — conversational filler + English stop words
_STOP_WORDS = frozenset(
    "a about all also am an and any are as at be been being but by can could "
    "did do does don doing doesn each for from get going got had has have he "
    "her here him his how i if in into is it its just know let like make me "
    "mine my no nor not of on or our out really set she should so some stuff "
    "than that the their them then there these they thing things this those "
    "to too up us use using very want was we well were what when where which "
    "who will with would yeah yes you your".split()
)

# Short conversational replies that never need a web search
_SKIP_PREFIXES = (
    "that sounds good", "sounds good", "looks good", "that works",
    "yes", "no", "ok", "okay", "sure", "thanks", "thank you",
    "perfect", "great", "go ahead", "proceed", "let's do",
    "i don't have", "i don't know", "i'm not sure", "whatever",
)


def _extract_search_query(message: str | None) -> str | None:
    """Extract a search query from a chat message.

    Returns a cleaned keyword string suitable for web search, or ``None``
    if the message is a conversational follow-up / too short to search.
    """
    if not message or len(message) < 15:
        return None

    lower = message.lower().strip()

    # Skip pure conversational follow-ups
    if any(lower.startswith(p) for p in _SKIP_PREFIXES):
        return None

    # Tokenize — keep alphanumeric words 2+ chars, preserving hyphens
    # inside words (e.g. "vue-router" stays as one token)
    tokens = re.findall(r"\b[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\b", message)
    keywords = [t for t in tokens if t.lower() not in _STOP_WORDS and len(t) > 1]

    if len(keywords) < 2:
        return None

    return " ".join(keywords[:8])


def _get_chat_client():
    """Return the active client for chat endpoints — request model when configured, else primary."""
    return request_llm_client or llm_client


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


def _make_chat_tool_executor(repo_root: str):
    """Create a read-only tool executor for chat exploration."""

    async def _executor(name: str, arguments: dict) -> str:
        from lean_ai.tools.file_ops import grep_files, read_file

        if name == "read_file":
            result = await read_file(
                path=arguments.get("path", ""),
                repo_root=repo_root,
                start_line=arguments.get("start_line"),
                end_line=arguments.get("end_line"),
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
                schedule_categorization(
                    llm_client, note["id"], content, repo_root or None
                )
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
                        f"- [{status}] {t['description']} "
                        f"(from note: {t['note_content']}...)"
                    )
                return "\n".join(lines)
            finally:
                await db.close()
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
    web_search_text: str | None = None
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
                    read_active_file, root, workspace.active_file,
                )

            if request.message and len(request.message) > 5:
                search_results = await asyncio.to_thread(
                    search_workspace, root, request.message, 8,
                )
        except Exception as e:
            logger.warning("Chat workspace context failed (non-fatal): %s", e)

    async def _do_web_search():
        nonlocal web_search_text
        if request.skip_web_search:
            return
        # Skip web search when the user provided explicit URLs to fetch
        if extract_urls(request.message):
            return
        query = _extract_search_query(request.message)
        if not query:
            return
        try:
            result = await internet.search_internet(
                query, llm_client=llm_client,
            )
            if result.success and result.output:
                web_search_text = result.output
        except Exception as e:
            logger.debug("Chat web search failed (non-fatal): %s", e)

    async def _fetch_urls():
        nonlocal fetched_pages
        urls = extract_urls(request.message)
        for url in urls[:3]:
            try:
                result = await internet.fetch_url(
                    url, llm_client=llm_client,
                )
                if result.success:
                    fetched_pages.append({"url": url, "content": result.output})
                else:
                    fetched_pages.append({
                        "url": url,
                        "content": f"(Failed to fetch: {result.error})",
                    })
            except Exception as e:
                logger.warning("Chat URL fetch failed for %s: %s", url, e)
                fetched_pages.append({"url": url, "content": f"(Failed to fetch: {e})"})

    image_descriptions: str = ""

    async def _describe_attachments():
        nonlocal image_descriptions
        from lean_ai.llm.vision import (
            describe_images,
            format_image_descriptions,
            is_vision_available,
        )

        if not request.attachments or not is_vision_available():
            return
        image_attachments = [
            {"data": a.data, "filename": a.filename}
            for a in request.attachments
            if a.mime_type and a.mime_type.startswith("image/")
        ]
        if not image_attachments:
            return
        try:
            results = await describe_images(
                image_attachments, prompt=request.message,
            )
            image_descriptions = format_image_descriptions(results)
        except Exception as e:
            logger.warning("Image description failed (non-fatal): %s", e)

    await asyncio.gather(
        _gather_workspace_context(),
        _do_web_search(),
        _fetch_urls(),
        _refine_message(),
        _describe_attachments(),
    )

    # Use refined message if available, otherwise original
    user_message = request.message
    knowledge_ctx: str | None = None
    if refiner_result and refiner_result.was_refined:
        user_message = refiner_result.refined
    if refiner_result and refiner_result.knowledge_context:
        knowledge_ctx = refiner_result.knowledge_context

    system_prompt = build_chat_system_prompt(
        workspace=workspace,
        file_tree=file_tree,
        active_file_content=active_file_content,
        search_results=search_results,
        project_context=project_context,
        fetched_pages=fetched_pages or None,
        web_search_results=web_search_text,
        knowledge_context=knowledge_ctx,
        user_name=request.user_name,
    )
    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    # Append image descriptions to the user message
    if image_descriptions:
        user_message = f"{user_message}\n\n{image_descriptions}"

    for msg in request.history[-20:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_message})

    logger.info(
        "Chat: history=%d, files=%d, search=%d, project_ctx=%s, web=%s, refined=%s, images=%d",
        len(request.history), len(file_tree), len(search_results),
        bool(project_context), bool(web_search_text),
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

        if repo_root:
            executor = _make_chat_tool_executor(repo_root)
            _, reply = await _chat_client.chat_with_tools(
                messages=messages,
                tools=CHAT_TOOLS,
                tool_executor_fn=executor,
                max_turns=_CHAT_MAX_TURNS,
                max_tokens=_get_chat_max_tokens(),
                text_only_exit_count=1,
            )
        else:
            reply = await _chat_client.chat_raw(
                messages,
                max_tokens=_get_chat_max_tokens(),
            )

        metrics = _chat_client.last_chat_metrics or {}
        return ChatResponse(
            reply=reply,
            tokens_per_second=metrics.get("tokens_per_second"),
            eval_count=metrics.get("eval_count"),
            refined=bool(refiner_result and refiner_result.was_refined),
            privacy_redactions=(
                len(refiner_result.privacy_redactions)
                if refiner_result else 0
            ),
            image_descriptions=image_desc or None,
        )
    except Exception as e:
        logger.exception("Chat call failed")
        return ChatResponse(reply=f"Error: {e}")


# ── Streaming helpers ───────────────────────────────────────────────


async def _stream_chat_simple(messages: list[dict]) -> AsyncGenerator[str, None]:
    """Stream LLM response without tools (no workspace available)."""
    thinking_queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def _on_thinking(token: str) -> None:
        await thinking_queue.put(token)

    async def _drain_thinking():
        while not thinking_queue.empty():
            t = thinking_queue.get_nowait()
            if t is not None:
                yield f"data: {json.dumps({'type': 'thinking', 'content': t})}\n\n"

    async for token in _get_chat_client().chat_stream(
        messages, max_tokens=_get_chat_max_tokens(),
        thinking_callback=_on_thinking,
    ):
        async for sse in _drain_thinking():
            yield sse
        yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"

    async for sse in _drain_thinking():
        yield sse

    yield f"data: {json.dumps({'type': 'done'})}\n\n"


async def _stream_chat_with_tools(
    messages: list[dict],
    repo_root: str,
) -> AsyncGenerator[str, None]:
    """Stream LLM response with read-only tool exploration.

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
        desc = name
        if name == "read_file":
            desc = f"Reading {args.get('path', '...')}"
        elif name == "grep_files":
            desc = f"Searching for '{args.get('pattern', '...')}'"
        elif name == "list_directory":
            desc = f"Listing {args.get('path', '') or '.'}"
        elif name == "directory_tree":
            desc = f"Tree of {args.get('path', '') or '.'}"
        await queue.put({"type": "tool_call", "name": name, "description": desc})

    async def _on_tool_result(name: str, result: str) -> None:
        success = not result.startswith("ERROR:")
        await queue.put({"type": "tool_result", "name": name, "success": success})

    executor = _make_chat_tool_executor(repo_root)

    async def _run():
        try:
            await _get_chat_client().chat_with_tools(
                messages=messages,
                tools=CHAT_TOOLS,
                tool_executor_fn=executor,
                max_turns=_CHAT_MAX_TURNS,
                max_tokens=_get_chat_max_tokens(),
                text_only_exit_count=1,
                stream_content=True,
                on_content=_on_content,
                on_thinking=_on_thinking,
                on_tool_call=_on_tool_call,
                on_tool_result=_on_tool_result,
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
                prompt_chars, prompt_chars // 4, settings._active_context_window,
            )

            repo_root = (
                request.workspace.workspace_root
                if request.workspace and request.workspace.workspace_root
                else None
            )

            if repo_root:
                async for sse_event in _stream_chat_with_tools(messages, repo_root):
                    yield sse_event
            else:
                async for sse_event in _stream_chat_simple(messages):
                    yield sse_event

        except Exception as e:
            logger.exception("Chat stream failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

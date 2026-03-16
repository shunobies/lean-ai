"""Chat, inline prediction, scaffolding, knowledge, and health endpoints."""

import asyncio
import json
import logging
import os
import re
import shutil
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from lean_ai.config import settings
from lean_ai.llm.refiner import RefinerResult
from lean_ai.routers.context_helpers import (
    build_chat_system_prompt,
    extract_urls,
    get_file_tree,
    read_active_file,
    read_project_context,
    search_workspace,
)
from lean_ai.routers.dependencies import _inline_client, llm_client, refiner
from lean_ai.routers.models import (
    ChatRequest,
    ChatResponse,
    IndexKnowledgeRequest,
    IndexKnowledgeResponse,
    InlinePredictRequest,
    ModelInfo,
    ModelsResponse,
    ScaffoldInfo,
    ScaffoldListResponse,
    ScaffoldRequest,
    ScaffoldResponse,
)
from lean_ai.tools import internet

logger = logging.getLogger(__name__)

chat_router = APIRouter()

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


def _get_default_model_name() -> str:
    """Return the model name for the active provider."""
    p = settings.llm_provider.lower()
    if p == "openai":
        return settings.openai_model
    if p == "anthropic":
        return settings.anthropic_model
    return settings.ollama_model


def _get_active_max_tokens() -> int:
    """Return max_tokens for the active provider."""
    p = settings.llm_provider.lower()
    if p == "openai":
        return settings.openai_max_tokens
    if p == "anthropic":
        return settings.anthropic_max_tokens
    return settings.ollama_max_tokens


@chat_router.get("/models", response_model=ModelsResponse)
async def list_models():
    """List available LLM providers/models based on server configuration."""
    models: list[ModelInfo] = []

    # Ollama: query live API for available models
    try:
        import ollama as ollama_lib

        client = ollama_lib.AsyncClient(host=settings.ollama_url)
        response = await client.list()
        for m in response.models:
            name = m.model
            models.append(ModelInfo(
                provider="ollama",
                model=name,
                display_name=f"Ollama: {name}",
                is_default=(
                    settings.llm_provider == "ollama"
                    and name == settings.ollama_model
                ),
            ))
    except Exception:
        # Ollama not reachable — add the configured model as fallback
        models.append(ModelInfo(
            provider="ollama",
            model=settings.ollama_model,
            display_name=f"Ollama: {settings.ollama_model}",
            is_default=settings.llm_provider == "ollama",
        ))

    # OpenAI: show if API key configured
    if settings.openai_api_key:
        models.append(ModelInfo(
            provider="openai",
            model=settings.openai_model,
            display_name=f"OpenAI: {settings.openai_model}",
            is_default=settings.llm_provider == "openai",
        ))

    # Anthropic: show if API key configured
    if settings.anthropic_api_key:
        models.append(ModelInfo(
            provider="anthropic",
            model=settings.anthropic_model,
            display_name=f"Anthropic: {settings.anthropic_model}",
            is_default=settings.llm_provider == "anthropic",
        ))

    return ModelsResponse(
        models=models,
        default_provider=settings.llm_provider,
        default_model=_get_default_model_name(),
    )


@chat_router.get("/scaffold/list", response_model=ScaffoldListResponse)
async def list_scaffolds():
    """List all available scaffold templates."""
    from lean_ai.tools.scaffold import get_scaffold_registry

    registry = get_scaffold_registry()
    return ScaffoldListResponse(
        scaffolds=[
            ScaffoldInfo(
                name=t.name,
                display_name=t.display_name,
                description=t.description,
                language=t.language,
                framework=t.framework,
                aliases=t.aliases,
                setup_type=t.setup_type,
            )
            for t in registry.list_all()
        ]
    )


@chat_router.post("/scaffold", response_model=ScaffoldResponse)
async def scaffold_project(request: ScaffoldRequest):
    """Set up a new project from a scaffold recipe."""
    from lean_ai.tools.scaffold import get_scaffold_registry, get_scaffold_runner

    registry = get_scaffold_registry()
    template = registry.get(request.scaffold_name)
    if template is None:
        available = [t.name for t in registry.list_all()]
        raise HTTPException(
            status_code=404,
            detail=f"Unknown scaffold '{request.scaffold_name}'. Available: {available}",
        )

    runner = get_scaffold_runner()
    result = await runner.run(template, request.project_name, request.parent_dir)

    if not result.success:
        raise HTTPException(status_code=500, detail=result.error or "Scaffold failed")

    return ScaffoldResponse(
        scaffold_name=result.scaffold_name,
        project_dir=result.project_dir,
        files_created=result.files_created,
        command_output=result.command_output,
        message=(
            f"Created {template.display_name} project '{request.project_name}' "
            f"at {result.project_dir}"
        ),
    )


@chat_router.post("/index-knowledge", response_model=IndexKnowledgeResponse)
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


async def _build_chat_messages(
    request: ChatRequest,
) -> tuple[list[dict], RefinerResult | None]:
    """Gather workspace context and build the LLM message list for a chat request.

    Shared by both the blocking ``/chat`` endpoint and the streaming
    ``/chat/stream`` endpoint so context-gathering logic lives in one place.
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
        summarize_threshold = min(30_000, max(5_000, settings._active_context_window // 4))
        for url in urls[:3]:
            try:
                result = await internet.fetch_url(
                    url, llm_client=llm_client,
                    summarize_threshold=summarize_threshold,
                )
                if result.success:
                    fetched_pages.append({"url": url, "content": result.output})
                else:
                    fetched_pages.append({
                        "url": url,
                        "content": f"(Failed to fetch: {result.error})",
                    })
            except Exception as e:
                logger.debug("Chat URL fetch failed for %s: %s", url, e)
                fetched_pages.append({"url": url, "content": f"(Failed to fetch: {e})"})

    await asyncio.gather(
        _gather_workspace_context(),
        _do_web_search(),
        _fetch_urls(),
        _refine_message(),
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
    )
    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    for msg in request.history[-20:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_message})

    logger.info(
        "Chat: history=%d, files=%d, search=%d, project_ctx=%s, web=%s, refined=%s",
        len(request.history), len(file_tree), len(search_results),
        bool(project_context), bool(web_search_text),
        bool(refiner_result and refiner_result.was_refined),
    )

    return messages, refiner_result


@chat_router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Lightweight read-only chat with workspace context.

    Gathers workspace context (file tree, project architecture, active file,
    search results, web search) and sends to the LLM. No FSM, no database,
    no tool execution.
    """
    try:
        messages, refiner_result = await _build_chat_messages(request)
        reply = await llm_client.chat_raw(
            messages,
            max_tokens=_get_active_max_tokens(),
        )
        metrics = llm_client.last_chat_metrics or {}
        return ChatResponse(
            reply=reply,
            tokens_per_second=metrics.get("tokens_per_second"),
            eval_count=metrics.get("eval_count"),
            refined=bool(refiner_result and refiner_result.was_refined),
            privacy_redactions=(
                len(refiner_result.privacy_redactions)
                if refiner_result else 0
            ),
        )
    except Exception as e:
        logger.exception("Chat call failed")
        return ChatResponse(reply=f"Error: {e}")


@chat_router.post("/chat/stream")
async def chat_stream_endpoint(request: ChatRequest):
    """Chat with workspace context, streaming tokens via Server-Sent Events.

    Identical context gathering to ``/chat`` but returns a text/event-stream
    response so the client sees tokens as they arrive rather than waiting for
    the full response.

    Event format::

        data: {"type": "token", "content": "..."}\n\n
        data: {"type": "done"}\n\n
        data: {"type": "error", "message": "..."}\n\n   # only on failure
    """
    async def generate() -> AsyncGenerator[str, None]:
        try:
            messages, _ = await _build_chat_messages(request)
            async for token in llm_client.chat_stream(
                messages, max_tokens=_get_active_max_tokens(),
            ):
                yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as e:
            logger.exception("Chat stream failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@chat_router.post("/predict")
async def inline_predict(request: InlinePredictRequest):
    """Stateless inline prediction — Copilot-style completions."""
    try:
        completion = await _inline_client.generate_completion(
            request.prefix, suffix=request.suffix,
        )
        confidence = 0.8 if completion.strip() else 0.0
        return {"completion": completion, "confidence": confidence}
    except Exception as e:
        logger.exception("Inline prediction failed")
        return {"completion": "", "confidence": 0.0, "error": str(e)}


@chat_router.get("/health")
async def health():
    """Health check."""
    return {"status": "ok"}

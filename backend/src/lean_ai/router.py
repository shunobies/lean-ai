"""FastAPI REST + WebSocket endpoints.

Simplified from single_ai: no FSM, no SQLAlchemy ORM, no repositories.
All state is in the minimal SQLite DB and the linear workflow pipeline.
"""

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from lean_ai.config import settings
from lean_ai.db import (
    create_session,
    delete_session,
    get_conversation_log,
    get_db,
    get_session,
    get_session_raw,
    list_sessions,
    log_conversation_entry,
    update_session,
)
from lean_ai.indexer.indexer import (
    generate_embeddings as _generate_embeddings,
)
from lean_ai.indexer.indexer import (
    index_workspace as _sync_index_workspace,
)
from lean_ai.indexer.indexer import (
    search_index,
)
from lean_ai.indexer.tree import list_repo_tree
from lean_ai.llm.client import LLMClient
from lean_ai.llm.prompts import CHAT_SYSTEM_PROMPT
from lean_ai.tools import internet
from lean_ai.tools.git_ops import (
    git_add_and_commit,
    git_checkout,
    git_create_branch,
    git_current_branch,
    git_current_sha,
    git_delete_branch,
    git_is_repo,
    git_merge_branch,
    git_stash_pop,
    git_stash_push,
)
from lean_ai.workflow.pipeline import run_workflow

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

router = APIRouter()

# ── LLM clients (stateless singletons) ──

llm_client = LLMClient()

_inline_client: LLMClient = (
    LLMClient(
        ollama_url=settings.effective_inline_url,
        model=settings.inline_model,
        max_tokens=settings.inline_max_tokens,
        context_window=settings.inline_context_window,
        temperature=settings.inline_temperature,
    )
    if settings.inline_model
    else llm_client
)


# ── Request/Response Models ──


class CreateSessionRequest(BaseModel):
    repo_root: str
    task: str = ""


class CreateSessionResponse(BaseModel):
    session_id: str
    status: str


class InitWorkspaceRequest(BaseModel):
    repo_root: str
    force_reindex: bool = False


class InitWorkspaceResponse(BaseModel):
    index_status: str
    index_chunk_count: int | None = None


class GenerateProjectContextRequest(BaseModel):
    repo_root: str
    skip_if_exists: bool = True


class GenerateProjectContextResponse(BaseModel):
    path: str
    chars: int
    skipped: bool = False


class ScaffoldRequest(BaseModel):
    scaffold_name: str
    project_name: str
    parent_dir: str


class ScaffoldResponse(BaseModel):
    scaffold_name: str
    project_dir: str
    files_created: list[str]
    command_output: str
    message: str


class ScaffoldInfo(BaseModel):
    name: str
    display_name: str
    description: str
    language: str
    framework: str | None
    aliases: list[str]
    setup_type: str


class ScaffoldListResponse(BaseModel):
    scaffolds: list[ScaffoldInfo]


class IndexKnowledgeRequest(BaseModel):
    repo_root: str
    force_reindex: bool = False


class IndexKnowledgeResponse(BaseModel):
    status: str
    doc_count: int = 0
    chunk_count: int = 0


class WorkspaceContext(BaseModel):
    workspace_name: str | None = None
    workspace_root: str | None = None
    active_file: str | None = None
    active_language: str | None = None
    active_selection: str | None = None


class ChatRequest(BaseModel):
    message: str
    history: list[dict[str, str]] = []
    workspace: WorkspaceContext | None = None


class ChatResponse(BaseModel):
    reply: str
    tokens_per_second: float | None = None
    eval_count: int | None = None


class InlinePredictRequest(BaseModel):
    file_path: str
    language: str
    prefix: str
    suffix: str
    cursor_line: int
    cursor_character: int


# ── Helpers ──


def _ensure_gitignore_entries(repo_root: str, entries: list[str]) -> list[str]:
    """Ensure entries are present in .gitignore."""
    gitignore_path = Path(repo_root) / ".gitignore"
    existing_content = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""
    existing_lines = {line.strip() for line in existing_content.splitlines()}

    missing = []
    for entry in entries:
        bare = entry.rstrip("/")
        if bare not in existing_lines and f"{bare}/" not in existing_lines:
            missing.append(entry)

    if not missing:
        return []

    block = "# lean-ai — generated workspace files (do not commit)\n" + "\n".join(missing) + "\n"
    separator = "\n" if existing_content and not existing_content.endswith("\n") else ""
    with gitignore_path.open("a", encoding="utf-8") as fh:
        fh.write(f"{separator}\n{block}")

    return missing


def _get_file_tree(workspace_root: str, max_files: int = 50) -> list[str]:
    """Get a truncated file tree for the workspace."""
    try:
        files = list_repo_tree(workspace_root)
        tree = [f.path for f in files[:max_files]]
        if len(files) > max_files:
            tree.append(f"... and {len(files) - max_files} more files")
        return tree
    except Exception as e:
        logger.debug("Could not list file tree: %s", e)
        return []


def _read_active_file(workspace_root: str, relative_path: str, max_chars: int = 3000) -> str | None:
    """Read the content of the active file, truncated to max_chars."""
    try:
        full_path = os.path.join(workspace_root, relative_path)
        if not os.path.isfile(full_path):
            return None
        with open(full_path, encoding="utf-8", errors="replace") as f:
            content = f.read(max_chars)
        if len(content) >= max_chars:
            content += "\n... (file truncated)"
        return content
    except Exception:
        return None


def _read_project_context(workspace_root: str, max_chars: int = 20_000) -> str | None:
    """Read .lean_ai/project_context.md if it exists."""
    context_path = os.path.join(workspace_root, ".lean_ai", "project_context.md")
    if not os.path.isfile(context_path):
        return None
    try:
        with open(context_path, encoding="utf-8", errors="replace") as f:
            content = f.read(max_chars)
        if len(content) >= max_chars:
            content += "\n... (truncated)"
        return content
    except Exception:
        return None


def _search_workspace(workspace_root: str, query: str, limit: int = 8) -> list[dict]:
    """Search the workspace index for relevant code snippets."""
    try:
        return search_index(workspace_root, query, limit=limit)
    except Exception as e:
        logger.debug("Workspace search failed: %s", e)
        return []


def _extract_urls(text: str) -> list[str]:
    """Extract HTTP/HTTPS URLs from text without regex."""
    urls: list[str] = []
    for word in text.split():
        cleaned = word.strip("()[]<>\"',;")
        if cleaned.startswith("http://") or cleaned.startswith("https://"):
            if cleaned not in urls:
                urls.append(cleaned)
    return urls


def _build_chat_system_prompt(
    workspace: WorkspaceContext | None = None,
    file_tree: list[str] | None = None,
    active_file_content: str | None = None,
    search_results: list[dict] | None = None,
    project_context: str | None = None,
    fetched_pages: list[dict] | None = None,
    web_search_results: str | None = None,
) -> str:
    """Build the chat system prompt with workspace context injected."""
    parts = [
        CHAT_SYSTEM_PROMPT,
        "",
        "IMPORTANT — How your capabilities work:",
        "- The system has ALREADY read the user's project files and searched "
        "their codebase for you.",
        "- The file tree, file contents, and code snippets shown below are "
        "REAL, LIVE data from their workspace.",
        "- You DO have access to their code. DO NOT say 'I cannot access your files'.",
        "- The system AUTOMATICALLY searches the web and fetches URLs on your behalf.",
        "- When answering, reference the actual code provided below.",
    ]

    if workspace:
        parts.append("")
        parts.append("=== WORKSPACE ===")
        if workspace.workspace_name:
            parts.append(f"Project: {workspace.workspace_name}")
        if workspace.active_file:
            parts.append(f"Open file: {workspace.active_file}")
        if workspace.active_language:
            parts.append(f"Language: {workspace.active_language}")

    if file_tree:
        parts.append("")
        parts.append("=== PROJECT FILES ===")
        parts.append("\n".join(file_tree))

    if project_context:
        parts.append("")
        parts.append("=== PROJECT ARCHITECTURE ===")
        parts.append(project_context)

    if workspace and workspace.active_selection:
        parts.append("")
        parts.append("=== SELECTED CODE ===")
        parts.append(f"```\n{workspace.active_selection}\n```")
    elif active_file_content:
        file_name = workspace.active_file if workspace else "unknown"
        parts.append("")
        parts.append(f"=== ACTIVE FILE ({file_name}) ===")
        parts.append(f"```\n{active_file_content}\n```")

    if fetched_pages:
        for page in fetched_pages:
            parts.append("")
            parts.append(f"=== FETCHED PAGE: {page['url']} ===")
            parts.append(page["content"])

    if web_search_results:
        parts.append("")
        parts.append("=== WEB SEARCH RESULTS ===")
        parts.append(web_search_results)

    if search_results:
        parts.append("")
        parts.append("=== CODE SEARCH RESULTS ===")
        for result in search_results[:8]:
            parts.append(
                f"--- {result['file_path']} "
                f"(lines {result['start_line']}-{result['end_line']}) ---"
            )
            parts.append(f"```\n{result['content']}\n```")

    return "\n".join(parts)


# ── Session Endpoints ──


@router.post("/sessions", response_model=CreateSessionResponse)
async def create_new_session(request: CreateSessionRequest):
    """Create a new workflow session."""
    db = await get_db(request.repo_root)
    try:
        session_id = await create_session(db, request.repo_root, request.task)
        return CreateSessionResponse(session_id=session_id, status="active")
    finally:
        await db.close()


@router.get("/sessions")
async def list_all_sessions(repo_root: str):
    """List all sessions for a workspace."""
    db = await get_db(repo_root)
    try:
        sessions = await list_sessions(db)
        return sessions
    finally:
        await db.close()


@router.get("/sessions/{session_id}")
async def get_session_detail(session_id: str, repo_root: str):
    """Get session detail."""
    db = await get_db(repo_root)
    try:
        session = await get_session(db, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return session
    finally:
        await db.close()


@router.delete("/sessions/{session_id}")
async def delete_session_endpoint(session_id: str, repo_root: str):
    """Delete a session and all its associated data (logs, conversation)."""
    db = await get_db(repo_root)
    try:
        found = await delete_session(db, session_id)
        if not found:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"status": "deleted", "session_id": session_id}
    finally:
        await db.close()


@router.get("/sessions/{session_id}/conversation")
async def get_session_conversation(session_id: str, repo_root: str):
    """Get the full conversation log (chain-of-thought) for a session."""
    db = await get_db(repo_root)
    try:
        session = await get_session(db, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        log = await get_conversation_log(db, session_id)
        return {"session_id": session_id, "entries": log}
    finally:
        await db.close()


@router.get("/sessions/{session_id}/checkpoints")
async def list_checkpoints(session_id: str):
    """List checkpoints for a session (stub — returns empty list)."""
    return []


@router.get("/sessions/{session_id}/git-events")
async def list_git_events(session_id: str):
    """List git events for a session (stub — returns empty list)."""
    return []


# ── WebSocket Workflow ──


@router.websocket("/sessions/{session_id}/stream")
async def session_stream(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for real-time workflow streaming.

    Client messages:
      - {"type": "user_message", "content": "...", "repo_root": "..."}
        Start the agentic workflow with a task.
      - {"type": "approve_tool", ...} — approve a pending shell command
      - {"type": "ping"} — keepalive

    Server messages:
      - {"type": "stage_change", "stage": "..."}
      - {"type": "tool_progress", "tool": "...", "status": "...", ...}
      - {"type": "diff", "file": "...", "diff": "..."}
      - {"type": "test_result", ...}
      - {"type": "complete", "summary": "...", ...}
      - {"type": "error", "message": "...", "recoverable": bool}
      - {"type": "pong"}
    """
    await websocket.accept()
    logger.info("WebSocket connected for session %s", session_id)

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "user_message":
                content = data.get("content", "")
                repo_root = data.get("repo_root", "")

                if not repo_root:
                    await websocket.send_json({
                        "type": "error",
                        "message": "repo_root is required",
                        "recoverable": True,
                    })
                    continue

                try:
                    db = await get_db(repo_root)
                    try:
                        session = await get_session(db, session_id)
                        if session:
                            # --- Git branch setup ---
                            branch_name = ""
                            base_branch = ""
                            stashed = False
                            is_git = await git_is_repo(repo_root)

                            if is_git:
                                br = await git_current_branch(repo_root)
                                base_branch = br.output.strip() if br.success else "main"
                                branch_name = f"lean-ai/{session_id}"

                                # Stash uncommitted changes
                                stashed = await git_stash_push(repo_root)

                                # Create and switch to the agent branch
                                create_result = await git_create_branch(branch_name, repo_root)
                                if create_result.success:
                                    await update_session(
                                        db, session_id,
                                        branch_name=branch_name,
                                        base_branch=base_branch,
                                        stashed=stashed,
                                    )
                                    await websocket.send_json({
                                        "type": "branch_created",
                                        "branch_name": branch_name,
                                        "base_branch": base_branch,
                                    })
                                else:
                                    logger.warning(
                                        "Failed to create branch %s: %s",
                                        branch_name, create_result.error,
                                    )
                                    branch_name = ""

                            # --- Load context and run workflow ---
                            context_path = Path(repo_root) / ".lean_ai" / "project_context.md"
                            context = ""
                            if context_path.is_file():
                                context = context_path.read_text(encoding="utf-8", errors="replace")

                            # Conversation logger — writes chain-of-thought to DB
                            async def _log_conversation(
                                role: str,
                                log_content: str,
                                tool_name: str | None = None,
                                tool_args: str | None = None,
                            ) -> None:
                                try:
                                    await log_conversation_entry(
                                        db, session_id, role, log_content,
                                        tool_name=tool_name, tool_args=tool_args,
                                    )
                                except Exception:
                                    logger.debug("Failed to log conversation entry", exc_info=True)

                            commit_msg = await run_workflow(
                                task=content,
                                repo_root=repo_root,
                                ws=websocket,
                                llm_client=llm_client,
                                context=context,
                                branch_name=branch_name,
                                conversation_logger=_log_conversation,
                            )

                            # --- Auto-commit agent changes ---
                            if branch_name:
                                commit_result = await git_add_and_commit(
                                    commit_msg, repo_root,
                                )
                                if commit_result.success:
                                    logger.info(
                                        "Auto-committed agent changes on %s",
                                        branch_name,
                                    )

                            await update_session(db, session_id, status="completed")
                        else:
                            await websocket.send_json({
                                "type": "error",
                                "message": f"Session {session_id} not found",
                                "recoverable": False,
                            })
                    finally:
                        await db.close()
                except WebSocketDisconnect:
                    raise
                except Exception as e:
                    logger.exception("Workflow error for session %s", session_id)
                    try:
                        await websocket.send_json({
                            "type": "error",
                            "message": str(e),
                            "recoverable": True,
                        })
                    except Exception:
                        pass

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

            # approve_tool is handled within run_workflow's tool executor

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for session %s", session_id)
    except Exception:
        logger.exception("WebSocket error for session %s", session_id)


# ── Session Branch Operations ──


@router.post("/sessions/{session_id}/merge")
async def merge_session(session_id: str, repo_root: str):
    """Merge the agent's branch into the base branch and clean up."""
    db = await get_db(repo_root)
    try:
        session = await get_session_raw(db, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        branch_name = session.get("branch_name")
        base_branch = session.get("base_branch")
        stashed = bool(session.get("stashed", 0))

        if not branch_name or not base_branch:
            raise HTTPException(status_code=400, detail="Session has no branch to merge")

        # Checkout base branch
        co_result = await git_checkout(base_branch, repo_root)
        if not co_result.success:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to checkout {base_branch}: {co_result.error}",
            )

        # Merge the agent branch
        merge_result = await git_merge_branch(branch_name, repo_root)
        if not merge_result.success:
            raise HTTPException(status_code=500, detail=f"Merge failed: {merge_result.error}")

        # Delete the branch
        await git_delete_branch(branch_name, repo_root)

        # Pop stash if we stashed before
        if stashed:
            await git_stash_pop(repo_root)

        # Get merge commit SHA
        sha_result = await git_current_sha(repo_root)
        merge_sha = sha_result.output.strip() if sha_result.success else ""

        await update_session(db, session_id, status="merged")

        return {
            "status": "merged",
            "merge_sha": merge_sha,
            "branch_deleted": True,
        }
    finally:
        await db.close()


@router.post("/sessions/{session_id}/abandon")
async def abandon_session(session_id: str, repo_root: str):
    """Abandon the agent's branch — checkout base and delete the branch."""
    db = await get_db(repo_root)
    try:
        session = await get_session_raw(db, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        branch_name = session.get("branch_name")
        base_branch = session.get("base_branch")
        stashed = bool(session.get("stashed", 0))

        if not branch_name or not base_branch:
            raise HTTPException(status_code=400, detail="Session has no branch to abandon")

        # Checkout base branch
        co_result = await git_checkout(base_branch, repo_root)
        if not co_result.success:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to checkout {base_branch}: {co_result.error}",
            )

        # Force-delete the unmerged branch
        await git_delete_branch(branch_name, repo_root, force=True)

        # Pop stash if we stashed before
        if stashed:
            await git_stash_pop(repo_root)

        await update_session(db, session_id, status="abandoned")

        return {"status": "abandoned", "branch_deleted": True}
    finally:
        await db.close()


# ── Init Workspace ──


@router.post("/init-workspace", response_model=InitWorkspaceResponse)
async def init_workspace(request: InitWorkspaceRequest):
    """Index the workspace and prepare for agent workflows.

    Builds the Whoosh search index, fires background embedding generation,
    and triggers knowledge base indexing.
    """
    _gitignore_entries = [
        ".lean_ai/",
        f"{settings.index_dir}/",
        f"{settings.knowledge_index_dir}/",
    ]
    added = _ensure_gitignore_entries(request.repo_root, _gitignore_entries)
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


# ── Project Context Generation ──


@router.post("/generate-project-context", response_model=GenerateProjectContextResponse)
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


# ── Scaffold ──


@router.get("/scaffold/list", response_model=ScaffoldListResponse)
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


@router.post("/scaffold", response_model=ScaffoldResponse)
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


# ── Knowledge Base ──


@router.post("/index-knowledge", response_model=IndexKnowledgeResponse)
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


# ── Chat ──


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Lightweight read-only chat with workspace context.

    Gathers workspace context (file tree, project architecture, active file,
    search results, web search) and sends to the LLM. No FSM, no database,
    no tool execution.
    """
    workspace = request.workspace
    file_tree: list[str] = []
    active_file_content: str | None = None
    search_results: list[dict] = []
    project_context: str | None = None
    web_search_text: str | None = None
    fetched_pages: list[dict] = []

    async def _gather_workspace_context():
        nonlocal file_tree, active_file_content, search_results, project_context
        try:
            if not (workspace and workspace.workspace_root):
                return
            root = workspace.workspace_root

            file_tree = await asyncio.to_thread(_get_file_tree, root)
            project_context = await asyncio.to_thread(_read_project_context, root)

            if workspace.active_file and not workspace.active_selection:
                active_file_content = await asyncio.to_thread(
                    _read_active_file, root, workspace.active_file,
                )

            if request.message and len(request.message) > 5:
                search_results = await asyncio.to_thread(
                    _search_workspace, root, request.message, 8,
                )
        except Exception as e:
            logger.warning("Chat workspace context failed (non-fatal): %s", e)

    async def _do_web_search():
        nonlocal web_search_text
        if not request.message or len(request.message) < 10:
            return
        try:
            result = await internet.search_internet(
                request.message, llm_client=llm_client,
            )
            if result.success and result.output:
                web_search_text = result.output
        except Exception as e:
            logger.debug("Chat web search failed (non-fatal): %s", e)

    async def _fetch_urls():
        nonlocal fetched_pages
        urls = _extract_urls(request.message)
        summarize_threshold = min(30_000, max(5_000, settings.ollama_context_window // 4))
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
    )

    system_prompt = _build_chat_system_prompt(
        workspace=workspace,
        file_tree=file_tree,
        active_file_content=active_file_content,
        search_results=search_results,
        project_context=project_context,
        fetched_pages=fetched_pages or None,
        web_search_results=web_search_text,
    )
    messages = [{"role": "system", "content": system_prompt}]

    for msg in request.history[-20:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": request.message})

    logger.info(
        "Chat: history=%d, files=%d, search=%d, project_ctx=%s, web=%s",
        len(request.history), len(file_tree), len(search_results),
        bool(project_context), bool(web_search_text),
    )

    try:
        reply = await llm_client.chat_raw(
            messages,
            temperature=settings.chat_temperature,
            max_tokens=settings.ollama_max_tokens,
        )
        metrics = llm_client.last_chat_metrics or {}
        return ChatResponse(
            reply=reply,
            tokens_per_second=metrics.get("tokens_per_second"),
            eval_count=metrics.get("eval_count"),
        )
    except Exception as e:
        logger.exception("Chat call failed")
        return ChatResponse(reply=f"Error: {e}")


# ── Inline Prediction ──


@router.post("/predict")
async def inline_predict(request: InlinePredictRequest):
    """Stateless inline prediction — Copilot-style completions."""
    try:
        completion = await _inline_client.generate_completion(request.prefix)
        confidence = 0.8 if completion.strip() else 0.0
        return {"completion": completion, "confidence": confidence}
    except Exception as e:
        logger.exception("Inline prediction failed")
        return {"completion": "", "confidence": 0.0, "error": str(e)}


# ── Health ──


@router.get("/health")
async def health():
    """Health check."""
    return {"status": "ok"}

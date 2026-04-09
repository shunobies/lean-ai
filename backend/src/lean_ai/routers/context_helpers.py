"""Helper functions for gathering and assembling workspace context."""

import logging
import os
from pathlib import Path

from lean_ai.config import settings
from lean_ai.indexer.indexer import search_index
from lean_ai.indexer.tree import list_repo_tree
from lean_ai.llm.prompts import CHAT_SYSTEM_PROMPT
from lean_ai.routers.models import WorkspaceContext

logger = logging.getLogger(__name__)

EXECUTION_CONTEXT_PERCENT = 0.05  # 5% of context window for execution prompts
PLANNING_CONTEXT_PERCENT = 0.15  # 15% of context window for planning prompts
CUSTOM_DOCS_SHARE = 0.4  # 40% of budget reserved for custom steering docs


def ensure_gitignore_entries(repo_root: str, entries: list[str]) -> list[str]:
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


def get_file_tree(workspace_root: str, max_files: int = 50) -> list[str]:
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


def read_active_file(workspace_root: str, relative_path: str, max_chars: int = 3000) -> str | None:
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


def read_project_context(workspace_root: str, max_chars: int = 20_000) -> str | None:
    """Read .lean_ai/project_context.md if it exists."""
    parts: list[str] = []
    for filename in ("project_context.md",):
        filepath = os.path.join(workspace_root, ".lean_ai", filename)
        if not os.path.isfile(filepath):
            continue
        remaining = max(0, max_chars - sum(len(p) for p in parts))
        if remaining < 500:
            break
        try:
            with open(filepath, encoding="utf-8", errors="replace") as f:
                content = f.read(remaining)
            if content.strip():
                if len(content) >= remaining:
                    content += "\n... (truncated)"
                parts.append(content)
        except Exception:
            pass
    return "\n\n".join(parts) if parts else None


def load_custom_steering_docs(repo_root: str) -> str:
    """Load all .md files from .lean_ai/context/ (custom steering documents only).

    Unlike ``load_full_context`` this deliberately skips project_context.md so
    it can be used *during* its generation without circular dependency.
    """
    parts: list[str] = []
    context_dir = Path(repo_root) / ".lean_ai" / "context"
    if context_dir.is_dir():
        for path in sorted(context_dir.glob("*.md")):
            if path.is_file():
                chunk = path.read_text(encoding="utf-8", errors="replace")
                if chunk.strip():
                    parts.append(chunk)
    return "\n\n".join(parts)


def load_full_context(repo_root: str, *, max_chars: int | None = None) -> str:
    """Load project_context.md, then all .md from .lean_ai/context/.

    When *max_chars* is set, the combined output is truncated to fit the
    budget with priority ordering preserved.  When ``None`` (default),
    all content is returned unbounded for backward compatibility.
    """
    parts: list[str] = []
    lean_dir = Path(repo_root) / ".lean_ai"
    loaded: set[Path] = set()
    used = 0

    # Priority files first
    for filename in ("project_context.md",):
        path = lean_dir / filename
        if path.is_file():
            chunk = path.read_text(encoding="utf-8", errors="replace")
            if chunk.strip():
                if max_chars is not None:
                    remaining = max_chars - used
                    if remaining <= 200:
                        break
                    if len(chunk) > remaining:
                        chunk = chunk[:remaining] + "\n... (truncated)"
                parts.append(chunk)
                used += len(chunk)
                loaded.add(path.resolve())

    # Then all .md files in context/ subfolder, alphabetically
    context_dir = lean_dir / "context"
    if context_dir.is_dir():
        for path in sorted(context_dir.glob("*.md")):
            if path.is_file() and path.resolve() not in loaded:
                chunk = path.read_text(encoding="utf-8", errors="replace")
                if chunk.strip():
                    if max_chars is not None:
                        remaining = max_chars - used
                        if remaining <= 200:
                            break
                        if len(chunk) > remaining:
                            chunk = chunk[:remaining] + "\n... (truncated)"
                    parts.append(chunk)
                    used += len(chunk)

    return "\n\n".join(parts)


def load_planning_context(repo_root: str) -> str:
    """Load project_context.md + custom steering docs for planning phases.

    Budget-gated at PLANNING_CONTEXT_PERCENT of the active context window.
    """
    budget = int(
        settings._active_context_window * PLANNING_CONTEXT_PERCENT * 3.5
    )
    parts: list[str] = []
    lean_dir = Path(repo_root) / ".lean_ai"
    used = 0

    # Priority: project_context.md (architecture overview)
    pc_path = lean_dir / "project_context.md"
    if pc_path.is_file():
        chunk = pc_path.read_text(encoding="utf-8", errors="replace")
        if chunk.strip():
            if len(chunk) > budget:
                chunk = chunk[:budget] + "\n... (truncated)"
            parts.append(chunk)
            used += len(chunk)

    # Then custom steering docs (naming conventions, project-specific rules)
    custom_text = load_custom_steering_docs(repo_root)
    if custom_text:
        remaining = budget - used
        if remaining > 200:
            if len(custom_text) > remaining:
                custom_text = custom_text[:remaining] + "\n... (truncated)"
            parts.append(custom_text)

    return "\n\n".join(parts)


def load_execution_context(repo_root: str) -> str:
    """Load custom steering docs for step execution.

    Framework guide has been replaced by the required citations system
    (search-on-demand during execution).
    Budget-gated at EXECUTION_CONTEXT_PERCENT of the active context window.
    """
    total_budget = int(
        settings._active_context_window * EXECUTION_CONTEXT_PERCENT * 3.5
    )
    custom_text = load_custom_steering_docs(repo_root)
    if not custom_text:
        return ""
    if len(custom_text) <= total_budget:
        return custom_text
    return custom_text[:total_budget] + "\n... (condensed)"


def load_condensed_context(repo_root: str) -> str:
    """Load project context condensed for execution prompts.

    Unlike ``load_full_context`` (used by planning phases), this applies a
    percentage-based budget and guarantees custom steering documents get a
    reserved allocation so they are not truncated away by large auto-generated
    context files.
    """
    total_budget = int(
        settings._active_context_window * EXECUTION_CONTEXT_PERCENT * 3.5
    )

    # Load components separately
    lean_dir = Path(repo_root) / ".lean_ai"

    generated_parts: list[str] = []
    for filename in ("project_context.md",):
        path = lean_dir / filename
        if path.is_file():
            chunk = path.read_text(encoding="utf-8", errors="replace")
            if chunk.strip():
                generated_parts.append(chunk)
    generated_text = "\n\n".join(generated_parts)

    custom_text = load_custom_steering_docs(repo_root)

    # Budget allocation with rollover
    custom_budget = int(total_budget * CUSTOM_DOCS_SHARE)
    generated_budget = total_budget - custom_budget

    if not custom_text:
        generated_budget = total_budget
        custom_budget = 0
    if not generated_text:
        custom_budget = total_budget
        generated_budget = 0

    parts: list[str] = []

    if generated_text:
        if len(generated_text) <= generated_budget:
            parts.append(generated_text)
            custom_budget += generated_budget - len(generated_text)
        else:
            parts.append(generated_text[:generated_budget] + "\n... (condensed)")

    if custom_text:
        if len(custom_text) <= custom_budget:
            parts.append(custom_text)
        else:
            parts.append(custom_text[:custom_budget] + "\n... (condensed)")

    return "\n\n".join(parts)


def search_workspace(workspace_root: str, query: str, limit: int = 8) -> list[dict]:
    """Search the workspace index for relevant code snippets."""
    try:
        return search_index(workspace_root, query, limit=limit)
    except Exception as e:
        logger.debug("Workspace search failed: %s", e)
        return []


def extract_urls(text: str) -> list[str]:
    """Extract HTTP/HTTPS URLs from text without regex."""
    urls: list[str] = []
    for word in text.split():
        cleaned = word.strip("()[]<>\"',;")
        if cleaned.startswith("http://") or cleaned.startswith("https://"):
            if cleaned not in urls:
                urls.append(cleaned)
    return urls


def build_chat_system_prompt(
    workspace: WorkspaceContext | None = None,
    file_tree: list[str] | None = None,
    active_file_content: str | None = None,
    search_results: list[dict] | None = None,
    project_context: str | None = None,
    fetched_pages: list[dict] | None = None,
    web_search_results: str | None = None,
    knowledge_context: str | None = None,
    user_name: str | None = None,
    max_context_chars: int = 30000,
) -> str:
    """Build the chat system prompt with budget-gated context injection.

    Dynamic context sections are appended in priority order until
    *max_context_chars* is reached.  Lower-priority sections are
    dropped or truncated to keep the prompt within budget for smaller
    models (e.g. 20B request model).
    """
    parts = [
        CHAT_SYSTEM_PROMPT,
    ]

    if user_name:
        parts.append("")
        parts.append(
            f"The user's name is {user_name}. "
            "Address them by name naturally in conversation."
        )

    parts.extend([
        "",
        "IMPORTANT — How your capabilities work:",
        "- The system has ALREADY read the user's project files and searched "
        "their codebase for you.",
        "- You also have read-only tools (read_file, grep_files, list_directory, "
        "directory_tree) to explore further when you need more detail.",
        "- You DO have access to their code. DO NOT say 'I cannot access "
        "your files'.",
        "- You have search_internet and fetch_url tools to look up documentation, "
        "error messages, or any external information when needed.",
        "- When answering, reference the actual code provided below.",
    ])

    # Workspace metadata — always included (tiny)
    if workspace:
        parts.append("")
        parts.append("=== WORKSPACE ===")
        if workspace.workspace_name:
            parts.append(f"Project: {workspace.workspace_name}")
        if workspace.active_file:
            parts.append(f"Open file: {workspace.active_file}")
        if workspace.active_language:
            parts.append(f"Language: {workspace.active_language}")

    # ── Budget-gated dynamic sections ──
    # Track remaining budget for context sections
    base_size = sum(len(p) for p in parts)
    budget = max(0, max_context_chars - base_size)

    def _try_append(header: str, content: str) -> bool:
        """Append a section if it fits within the remaining budget."""
        nonlocal budget
        section = f"\n{header}\n{content}"
        if len(section) <= budget:
            parts.append(section)
            budget -= len(section)
            return True
        # Try truncating to fit
        if budget > len(header) + 200:
            truncated = content[:budget - len(header) - 50]
            parts.append(
                f"\n{header}\n{truncated}\n... (truncated to fit context budget)"
            )
            budget = 0
            return True
        return False

    # Priority 1: Selected code / active file — most directly relevant
    if workspace and workspace.active_selection:
        _try_append(
            "=== SELECTED CODE ===",
            f"```\n{workspace.active_selection}\n```",
        )
    elif active_file_content:
        file_name = workspace.active_file if workspace else "unknown"
        _try_append(
            f"=== ACTIVE FILE ({file_name}) ===",
            f"```\n{active_file_content}\n```",
        )

    # Priority 2: Code search results — directly answers user's query
    if search_results and budget > 0:
        sr_parts = []
        for result in search_results[:8]:
            sr_parts.append(
                f"--- {result['file_path']} "
                f"(lines {result['start_line']}-{result['end_line']}) ---"
            )
            sr_parts.append(f"```\n{result['content']}\n```")
        _try_append("=== CODE SEARCH RESULTS ===", "\n".join(sr_parts))

    # Priority 3: Project architecture — truncated to fit
    if project_context and budget > 0:
        _try_append("=== PROJECT ARCHITECTURE ===", project_context)

    # Priority 4: Web search results
    if web_search_results and budget > 0:
        _try_append("=== WEB SEARCH RESULTS ===", web_search_results)

    # Priority 5: Fetched pages
    if fetched_pages and budget > 0:
        for page in fetched_pages:
            if budget <= 0:
                break
            _try_append(
                f"=== FETCHED PAGE: {page['url']} ===",
                page["content"],
            )

    # Priority 6: File tree (drop if over budget)
    if file_tree and budget > 0:
        _try_append("=== PROJECT FILES ===", "\n".join(file_tree))

    # Priority 7: Domain knowledge (drop if over budget)
    if knowledge_context and budget > 0:
        _try_append("=== DOMAIN KNOWLEDGE ===", knowledge_context)

    return "\n".join(parts)

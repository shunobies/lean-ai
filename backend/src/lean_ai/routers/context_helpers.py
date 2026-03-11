"""Helper functions for gathering and assembling workspace context."""

import logging
import os
from pathlib import Path

from lean_ai.indexer.indexer import search_index
from lean_ai.indexer.tree import list_repo_tree
from lean_ai.llm.prompts import CHAT_SYSTEM_PROMPT
from lean_ai.routers.models import WorkspaceContext

logger = logging.getLogger(__name__)


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
    """Read .lean_ai/project_context.md and framework_guide.md if they exist."""
    parts: list[str] = []
    for filename in ("project_context.md", "framework_guide.md"):
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


def load_full_context(repo_root: str) -> str:
    """Load project_context.md + framework_guide.md, then all .md from .lean_ai/context/."""
    parts: list[str] = []
    lean_dir = Path(repo_root) / ".lean_ai"
    loaded: set[Path] = set()

    # Priority files first
    for filename in ("project_context.md", "framework_guide.md"):
        path = lean_dir / filename
        if path.is_file():
            chunk = path.read_text(encoding="utf-8", errors="replace")
            if chunk.strip():
                parts.append(chunk)
                loaded.add(path.resolve())

    # Then all .md files in context/ subfolder, alphabetically
    context_dir = lean_dir / "context"
    if context_dir.is_dir():
        for path in sorted(context_dir.glob("*.md")):
            if path.is_file() and path.resolve() not in loaded:
                chunk = path.read_text(encoding="utf-8", errors="replace")
                if chunk.strip():
                    parts.append(chunk)

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

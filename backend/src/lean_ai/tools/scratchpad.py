"""Per-session scratchpad for tracking agent progress across turns.

File-based state: .lean_ai/scratchpads/{session_id}.md in the target project.
Session-scoped: persists until /approve or /reject closes the session.
Survives crashes and power outages for session recovery.
"""

import logging
from pathlib import Path

from lean_ai.config import settings
from lean_ai.tools.executor import ToolResult

logger = logging.getLogger(__name__)

SCRATCHPAD_CONTEXT_PERCENT = 0.05  # 5% of context window


def _max_scratchpad_chars() -> int:
    """Scratchpad budget: 5% of context window, converted to chars."""
    ctx = settings._active_context_window
    return int(ctx * SCRATCHPAD_CONTEXT_PERCENT * 3.5)  # tokens -> chars approx


def scratchpad_path(repo_root: str, session_id: str) -> Path:
    """Return the absolute path to the per-session scratchpad file."""
    return Path(repo_root) / ".lean_ai" / "scratchpads" / f"{session_id}.md"


async def update_scratchpad(content: str, repo_root: str, session_id: str) -> ToolResult:
    """Write the entire scratchpad content (overwrite, not append).

    The content should use structured sections:
      ## Completed
      ## Current State
      ## Cross-File References
      ## Files Modified
      ## Next Step

    Capped at 5% of context window (in chars) to avoid bloating context.
    """
    max_chars = _max_scratchpad_chars()
    if len(content) > max_chars:
        content = content[:max_chars]
        content += f"\n\n[SCRATCHPAD TRUNCATED at {max_chars} chars — keep entries concise]"

    path = scratchpad_path(repo_root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    logger.info("Scratchpad updated (%d chars, limit %d) at %s", len(content), max_chars, path)
    return ToolResult(
        success=True,
        output=f"Scratchpad updated ({len(content)} chars, limit {max_chars}).",
    )


def read_scratchpad(repo_root: str, session_id: str) -> str:
    """Read the current scratchpad content. Returns empty string if absent."""
    path = scratchpad_path(repo_root, session_id)
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        logger.warning("Failed to read scratchpad at %s", path, exc_info=True)
        return ""


def delete_scratchpad(repo_root: str, session_id: str) -> None:
    """Remove the scratchpad file (cleanup on session close)."""
    path = scratchpad_path(repo_root, session_id)
    if path.is_file():
        path.unlink()
        logger.info("Scratchpad deleted: %s", path)

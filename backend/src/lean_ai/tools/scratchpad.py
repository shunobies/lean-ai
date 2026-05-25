"""Per-session scratchpad for tracking agent progress across turns.

State-based: operates on WorkflowState.scratchpad_content for unified
state management. File-based operations are deprecated; the StateManager
handles persistence to .lean_ai/state/{session_id}.json.
Session-scoped: persists until /approve or /reject closes the session.
Survives crashes and power outages for session recovery.
"""

import logging
from pathlib import Path

from lean_ai.config import settings
from lean_ai.tools.executor import ToolResult
from lean_ai.workflow.state import WorkflowState

logger = logging.getLogger(__name__)

SCRATCHPAD_CONTEXT_PERCENT = 0.05  # 5% of context window


def _max_scratchpad_chars() -> int:
    """Scratchpad budget: 5% of context window, converted to chars."""
    ctx = settings._active_context_window
    return int(ctx * SCRATCHPAD_CONTEXT_PERCENT * 3.5)  # tokens -> chars approx


def scratchpad_path(repo_root: str, session_id: str) -> Path:
    """Return the absolute path to the per-session scratchpad file."""
    return Path(repo_root) / ".lean_ai" / "scratchpads" / f"{session_id}.md"


async def update_scratchpad(
    content: str,
    repo_root: str,
    session_id: str,
    state: WorkflowState = None,
) -> ToolResult:
    """Write the entire scratchpad content (overwrite, not append).

    The content should use structured sections:
      ## Completed
      ## Current State
      ## Cross-File References
      ## Files Modified
      ## Next Step

    Capped at 5% of context window (in chars) to avoid bloating context.

    Deprecated: File-based operations are no longer used directly.
    When state is provided, sets state.scratchpad_content instead.
    """
    # DEPRECATED: File-based scratchpad writes are replaced by WorkflowState fields.
    # The StateManager persists state to .lean_ai/state/{session_id}.json.

    max_chars = _max_scratchpad_chars()
    if len(content) > max_chars:
        cut = content[:max_chars]
        last_nl = cut.rfind("\n")
        if last_nl > 0:
            content = cut[:last_nl]
        else:
            content = cut  # no newlines — keep raw truncation as fallback
        content += f"\n\n[SCRATCHPAD TRUNCATED at {len(content)} chars — keep entries concise]"

    if state is not None:
        state.scratchpad_content = content
        logger.info(
            "Scratchpad updated on WorkflowState (%d chars, limit %d)",
            len(content),
            max_chars,
        )
    else:
        path = scratchpad_path(repo_root, session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        logger.info(
            "Scratchpad updated (%d chars, limit %d) at %s",
            len(content),
            max_chars,
            path,
        )

    return ToolResult(
        success=True,
        output=f"Scratchpad updated ({len(content)} chars, limit {max_chars}).",
    )


def read_scratchpad(
    repo_root: str,
    session_id: str,
    state: WorkflowState = None,
) -> str:
    """Read the current scratchpad content. Returns empty string if absent.

    Deprecated: File-based operations are no longer used directly.
    When state is provided, returns state.scratchpad_content instead.
    """
    # DEPRECATED: File-based scratchpad reads are replaced by WorkflowState fields.
    # The StateManager persists state to .lean_ai/state/{session_id}.json.

    if state is not None:
        return state.scratchpad_content

    path = scratchpad_path(repo_root, session_id)
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        logger.warning("Failed to read scratchpad at %s", path, exc_info=True)
        return ""


def delete_scratchpad(
    repo_root: str,
    session_id: str,
    state: WorkflowState = None,
) -> None:
    """Remove the scratchpad content (cleanup on session close).

    Deprecated: File-based operations are no longer used directly.
    When state is provided, clears state.scratchpad_content instead.
    """
    # DEPRECATED: File-based scratchpad deletion is replaced by WorkflowState fields.
    # The StateManager persists state to .lean_ai/state/{session_id}.json.

    if state is not None:
        state.scratchpad_content = ""
        logger.info("Scratchpad cleared on WorkflowState for session %s", session_id)
        return

    path = scratchpad_path(repo_root, session_id)
    if path.is_file():
        path.unlink()
        logger.info("Scratchpad deleted: %s", path)

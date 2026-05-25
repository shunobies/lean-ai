"""Per-session append-only journal for recording milestones and findings.

State-based: operates on WorkflowState.journal_entries for unified
state management. File-based operations are deprecated; the StateManager
handles persistence to .lean_ai/state/{session_id}.json.
Session-scoped: persists until session close.
Survives crashes for session recovery.
Unlike the scratchpad (overwrite-based volatile memory), the journal
is append-only — entries are never lost until session cleanup.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from lean_ai.config import settings
from lean_ai.tools.executor import ToolResult
from lean_ai.workflow.state import WorkflowState

logger = logging.getLogger(__name__)

JOURNAL_CONTEXT_PERCENT = 0.03  # 3% of context window


def _max_journal_chars() -> int:
    """Journal budget: 3% of context window, converted to chars."""
    ctx = settings._active_context_window
    return int(ctx * JOURNAL_CONTEXT_PERCENT * 3.5)  # tokens -> chars approx


def journal_path(repo_root: str, session_id: str) -> Path:
    """Return the absolute path to the per-session journal file."""
    return Path(repo_root) / ".lean_ai" / "journals" / f"{session_id}.md"


async def add_journal_entry(
    content: str,
    repo_root: str,
    session_id: str,
    state: WorkflowState = None,
) -> ToolResult:
    """Append a single entry to the session journal.

    Each entry is timestamped. The journal is never overwritten —
    only appended to. Enforces the total journal budget.

    Deprecated: File-based operations are no longer used directly.
    When state is provided, calls state.add_journal_entry() instead.
    """
    # DEPRECATED: File-based journal writes are replaced by WorkflowState fields.
    # The StateManager persists state to .lean_ai/state/{session_id}.json.

    max_chars = _max_journal_chars()
    timestamp = datetime.now(timezone.utc).strftime("%H:%M")
    entry = f"- [{timestamp}] {content.strip()}"

    if state is not None:
        # Check total journal size against budget before adding
        existing_chars = sum(len(e) for e in state.journal_entries)
        if existing_chars >= max_chars:
            return ToolResult(
                success=False,
                error=(
                    f"Journal full ({existing_chars} chars, limit {max_chars}). "
                    "Entry NOT added. Use update_scratchpad for volatile working state instead."
                ),
            )

        new_chars = existing_chars + len(entry)
        if new_chars > max_chars:
            available = max_chars - existing_chars
            if available < 20:
                return ToolResult(
                    success=False,
                    error=(
                        f"Journal full ({existing_chars} chars, limit {max_chars}). "
                        "Entry NOT added. Use update_scratchpad for volatile working state instead."
                    ),
                )
            # Truncate to fit within budget
            entry = entry[:available]

        state.add_journal_entry(entry)
        entry_count = len(state.journal_entries)
        logger.info(
            "Journal entry added on WorkflowState (%d entries, %d chars, limit %d)",
            entry_count,
            new_chars,
            max_chars,
        )
        return ToolResult(
            success=True,
            output=(
                f"Journal entry recorded ({entry_count} entries, {new_chars}/{max_chars} chars)."
            ),
        )

    # Legacy file-based fallback
    path = journal_path(repo_root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = ""
    if path.is_file():
        try:
            existing = path.read_text(encoding="utf-8")
        except Exception:
            pass

    if len(existing) >= max_chars:
        return ToolResult(
            success=False,
            error=(
                f"Journal full ({len(existing)} chars, limit {max_chars}). "
                "Entry NOT added. Use update_scratchpad for volatile working state instead."
            ),
        )

    entry_with_prefix = f"\n- [{timestamp}] {content.strip()}"

    new_content = existing + entry_with_prefix
    if len(new_content) > max_chars:
        available = max_chars - len(existing)
        if available < 20:
            return ToolResult(
                success=False,
                error=(
                    f"Journal full ({len(existing)} chars, limit {max_chars}). "
                    "Entry NOT added. Use update_scratchpad for volatile working state instead."
                ),
            )
        # Truncate at last newline to avoid cutting mid-character
        truncated = entry_with_prefix[:available]
        last_nl = truncated.rfind("\n")
        if last_nl > 0:
            entry_with_prefix = truncated[:last_nl]
        else:
            entry_with_prefix = truncated
        new_content = existing + entry_with_prefix
        new_content += "\n[JOURNAL FULL — no more entries accepted]"

    path.write_text(new_content, encoding="utf-8")

    entry_count = new_content.count("\n- [")
    logger.info(
        "Journal entry added (%d entries, %d chars, limit %d) at %s",
        entry_count,
        len(new_content),
        max_chars,
        path,
    )
    return ToolResult(
        success=True,
        output=(
            f"Journal entry recorded ({entry_count} entries, {len(new_content)}/{max_chars} chars)."
        ),
    )


def read_journal(
    repo_root: str,
    session_id: str,
    state: WorkflowState = None,
) -> str:
    """Read the full journal content. Returns empty string if absent.

    Deprecated: File-based operations are no longer used directly.
    When state is provided, formats state.journal_entries as markdown.
    """
    # DEPRECATED: File-based journal reads are replaced by WorkflowState fields.
    # The StateManager persists state to .lean_ai/state/{session_id}.json.

    if state is not None:
        if not state.journal_entries:
            return ""
        return "\n".join(state.journal_entries)

    path = journal_path(repo_root, session_id)
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        logger.warning("Failed to read journal at %s", path, exc_info=True)
        return ""


def delete_journal(
    repo_root: str,
    session_id: str,
    state: WorkflowState = None,
) -> None:
    """Remove the journal content (cleanup on session close).

    Deprecated: File-based operations are no longer used directly.
    When state is provided, clears state.journal_entries instead.
    """
    # DEPRECATED: File-based journal deletion is replaced by WorkflowState fields.
    # The StateManager persists state to .lean_ai/state/{session_id}.json.

    if state is not None:
        state.journal_entries = []
        logger.info("Journal cleared on WorkflowState for session %s", session_id)
        return

    path = journal_path(repo_root, session_id)
    if path.is_file():
        path.unlink()
        logger.info("Journal deleted: %s", path)

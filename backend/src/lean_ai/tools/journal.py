"""Per-session append-only journal for recording milestones and findings.

File-based state: .lean_ai/journals/{session_id}.md in the target project.
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
) -> ToolResult:
    """Append a single entry to the session journal.

    Each entry is timestamped. The journal is never overwritten —
    only appended to. Enforces the total journal budget.
    """
    max_chars = _max_journal_chars()
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

    timestamp = datetime.now(timezone.utc).strftime("%H:%M")
    entry = f"\n- [{timestamp}] {content.strip()}"

    new_content = existing + entry
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
        truncated = entry[:available]
        last_nl = truncated.rfind("\n")
        if last_nl > 0:
            entry = truncated[:last_nl]
        else:
            entry = truncated
        new_content = existing + entry
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


def read_journal(repo_root: str, session_id: str) -> str:
    """Read the full journal content. Returns empty string if absent."""
    path = journal_path(repo_root, session_id)
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        logger.warning("Failed to read journal at %s", path, exc_info=True)
        return ""


def delete_journal(repo_root: str, session_id: str) -> None:
    """Remove the journal file (cleanup on session close)."""
    path = journal_path(repo_root, session_id)
    if path.is_file():
        path.unlink()
        logger.info("Journal deleted: %s", path)

"""Session history query functions for chat tools.

Provides the logic behind chat-mode session history tools:
listing recent sessions, summarizing specific sessions, and
searching past work. Keeps the chat router's tool executor clean.
"""

import json
import logging
from typing import TYPE_CHECKING

from lean_ai.db import (
    get_commits_for_session,
    get_conversation_log,
    get_db,
    get_session_raw,
    list_sessions,
    search_sessions,
)
from lean_ai.memory.index import search_memories

if TYPE_CHECKING:
    from lean_ai.llm.facade import LLMClient

logger = logging.getLogger(__name__)

# Hard cap for auto-injected context (chars)
_ACTIVITY_CONTEXT_MAX = 800


async def list_recent_sessions(repo_root: str, limit: int = 5) -> str:
    """List recent sessions with task, status, and date.

    Returns a formatted text block for the LLM.
    """
    db = await get_db(repo_root)
    try:
        sessions = await list_sessions(db)
    finally:
        await db.close()

    if not sessions:
        return "No previous sessions found for this workspace."

    lines = [f"Recent sessions ({min(limit, len(sessions))} of {len(sessions)}):"]
    for s in sessions[:limit]:
        title = s.get("title") or "(no description)"
        status = s.get("session_status", "unknown")
        date = (s.get("created_at") or "")[:10]
        sid = s.get("session_id", "")
        lines.append(f"  [{sid}] {title} — {status} ({date})")

    return "\n".join(lines)


async def get_session_summary(
    repo_root: str,
    session_id: str,
    llm: "LLMClient | None" = None,
) -> str:
    """Get a detailed summary of a specific session.

    If a worker LLM is provided, includes a narrative summary of the
    conversation. Otherwise returns structured metadata only.
    """
    db = await get_db(repo_root)
    try:
        session = await get_session_raw(db, session_id)
        if not session:
            return f"Session {session_id} not found."

        # Tool stats
        cursor = await db.execute(
            "SELECT tool_name, COUNT(*) as cnt, "
            "SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failures "
            "FROM tool_logs WHERE session_id = ? GROUP BY tool_name",
            (session_id,),
        )
        tool_rows = await cursor.fetchall()
        tool_stats = {row[0]: (row[1], row[2]) for row in tool_rows}

        # Files modified
        cursor = await db.execute(
            "SELECT DISTINCT parameters FROM tool_logs "
            "WHERE session_id = ? AND tool_name IN ('create_file', 'edit_file')",
            (session_id,),
        )
        files: set[str] = set()
        for row in await cursor.fetchall():
            try:
                params = json.loads(row[0]) if row[0] else {}
                if "path" in params:
                    files.add(params["path"])
            except (json.JSONDecodeError, TypeError):
                pass

        # Commits
        commits = await get_commits_for_session(db, session_id)

        # Conversation log (for worker summarization)
        conversation = await get_conversation_log(db, session_id) if llm else []
    finally:
        await db.close()

    # Build structured output
    task = session.get("task", "(no task)")
    status = session.get("status", "unknown")
    created = (session.get("created_at") or "")[:19]
    completed = (session.get("completed_at") or "")[:19]
    branch = session.get("branch_name") or "(none)"

    parts = [
        f"Session: {session_id}",
        f"Task: {task}",
        f"Status: {status}",
        f"Created: {created}",
        f"Completed: {completed}",
        f"Branch: {branch}",
    ]

    if files:
        parts.append(f"Files modified ({len(files)}):")
        for f in sorted(files):
            parts.append(f"  {f}")

    if tool_stats:
        total_calls = sum(cnt for cnt, _ in tool_stats.values())
        total_failures = sum(fail for _, fail in tool_stats.values())
        parts.append(f"Tool calls: {total_calls} (failures: {total_failures})")
        for name, (cnt, fail) in sorted(tool_stats.items()):
            suffix = f" ({fail} failed)" if fail else ""
            parts.append(f"  {name}: {cnt}{suffix}")

    if commits:
        parts.append(f"Commits ({len(commits)}):")
        for c in commits:
            msg = (c.get("message") or "")[:60]
            parts.append(f"  {c['commit_sha'][:8]} {msg}")

    # Worker model narrative summary
    if llm and conversation:
        narrative = await _summarize_conversation(llm, task, conversation)
        if narrative:
            parts.append(f"\nSummary:\n{narrative}")

    return "\n".join(parts)


async def _summarize_conversation(
    llm: "LLMClient",
    task: str,
    conversation: list[dict],
) -> str:
    """Use the worker model to produce a narrative summary of a session."""
    try:
        from lean_ai.llm.prompt_registry import registry

        # Build a truncated conversation transcript
        transcript_parts = []
        budget = 4000  # chars for the conversation excerpt
        for entry in conversation:
            role = entry.get("role", "")
            content = entry.get("content", "")
            tool_name = entry.get("tool_name")

            if tool_name:
                line = f"[{role}] tool:{tool_name} → {content[:200]}"
            elif role == "assistant":
                line = f"[assistant] {content[:300]}"
            else:
                continue  # skip system messages etc.

            if len("\n".join(transcript_parts)) + len(line) > budget:
                transcript_parts.append("... (conversation truncated)")
                break
            transcript_parts.append(line)

        if not transcript_parts:
            return ""

        session_data = f"TASK: {task}\n\nCONVERSATION:\n" + "\n".join(transcript_parts)
        prompt_text, version_id = registry.get_with_version("memory.session_summary")
        user_msg = prompt_text.format(session_data=session_data)

        result = await llm.chat_raw(
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=512,
        )
        return result.strip() if result else ""
    except Exception:
        logger.debug("Session summarization failed (non-fatal)", exc_info=True)
        return ""


async def search_session_history(repo_root: str, query: str) -> str:
    """Search past sessions by keyword.

    Returns matching sessions formatted for the LLM.
    """
    db = await get_db(repo_root)
    try:
        results = await search_sessions(db, query=query)
    finally:
        await db.close()

    if not results:
        return f"No sessions found matching '{query}'."

    lines = [f"Sessions matching '{query}' ({len(results)} results):"]
    for s in results[:10]:
        title = s.get("title") or "(no description)"
        status = s.get("session_status", "unknown")
        date = (s.get("created_at") or "")[:10]
        sid = s.get("session_id", "")
        lines.append(f"  [{sid}] {title} — {status} ({date})")

    return "\n".join(lines)


def search_workspace_memories(repo_root: str, query: str) -> str:
    """Search extracted memories from past sessions.

    Returns matching memories formatted for the LLM.
    """
    results = search_memories(repo_root, query, limit=10)

    if not results:
        return f"No memories found matching '{query}'."

    lines = [f"Workspace memories matching '{query}' ({len(results)} results):"]
    for r in results:
        category = r.get("category") or "general"
        content = r["content"]
        task = r.get("source_task")
        line = f"  [{category}] {content}"
        if task:
            line += f" (from: {task[:50]})"
        lines.append(line)

    return "\n".join(lines)


async def get_recent_activity_context(
    repo_root: str,
    user_message: str,
) -> str:
    """Build a compact recent-activity string for system prompt injection.

    Returns a small text block (~300-600 chars) showing recent sessions
    and relevant memories. Returns empty string if nothing found.
    """
    parts: list[str] = []
    used = 0

    # Recent sessions (last 3, one line each)
    try:
        db = await get_db(repo_root)
        try:
            sessions = await list_sessions(db)
        finally:
            await db.close()

        if sessions:
            for s in sessions[:3]:
                title = s.get("title") or "(no description)"
                status = s.get("session_status", "unknown")
                date = (s.get("created_at") or "")[:10]
                line = f"- {title} [{status}] ({date})"
                if used + len(line) + 1 > _ACTIVITY_CONTEXT_MAX:
                    break
                parts.append(line)
                used += len(line) + 1
    except Exception:
        logger.debug("Recent sessions lookup failed (non-fatal)", exc_info=True)

    # Relevant memories (top 3 matching user message)
    if user_message and len(user_message) > 5:
        try:
            memories = search_memories(repo_root, user_message, limit=3)
            for m in memories:
                category = m.get("category") or "general"
                content = m["content"]
                line = f"- [{category}] {content}"
                if used + len(line) + 1 > _ACTIVITY_CONTEXT_MAX:
                    break
                parts.append(line)
                used += len(line) + 1
        except Exception:
            logger.debug("Memory search failed (non-fatal)", exc_info=True)

    return "\n".join(parts)

"""Whoosh-based full-text search index for session memories.

Index lives at .lean_ai/memory_index/ within the workspace. Follows the
notes_index.py pattern but is per-workspace rather than global.
"""

import logging
import os

from whoosh.fields import ID, TEXT, Schema
from whoosh.index import create_in, exists_in, open_dir
from whoosh.qparser import MultifieldParser

logger = logging.getLogger(__name__)

MEMORY_SCHEMA = Schema(
    memory_id=ID(stored=True, unique=True),
    content=TEXT(stored=True),
    category=ID(stored=True),
    tags=TEXT(stored=True),
    source_task=TEXT(stored=True),
)

# Characters with special meaning in Whoosh query syntax.
_WHOOSH_SPECIAL_CHARS = set('/:*?\\<>|"^~')


def _index_dir(repo_root: str) -> str:
    """Per-workspace memory index directory."""
    return os.path.join(repo_root, ".lean_ai", "memory_index")


def _safe_query(q: str) -> str:
    """Escape special Whoosh characters in a user query."""
    return "".join(
        f"\\{c}" if c in _WHOOSH_SPECIAL_CHARS else c for c in q
    ).strip()


def _ensure_index(repo_root: str):
    """Open or create the memory Whoosh index."""
    idx_dir = _index_dir(repo_root)
    if exists_in(idx_dir):
        return open_dir(idx_dir)
    os.makedirs(idx_dir, exist_ok=True)
    return create_in(idx_dir, MEMORY_SCHEMA)


def index_memory(
    repo_root: str,
    memory_id: str,
    content: str,
    category: str = "",
    tags: list[str] | None = None,
    source_task: str | None = None,
) -> None:
    """Add or update a memory in the search index."""
    ix = _ensure_index(repo_root)
    writer = ix.writer()
    writer.update_document(
        memory_id=memory_id,
        content=content,
        category=category,
        tags=" ".join(tags) if tags else "",
        source_task=source_task or "",
    )
    writer.commit()


def remove_memory(repo_root: str, memory_id: str) -> None:
    """Remove a memory from the search index."""
    idx_dir = _index_dir(repo_root)
    if not exists_in(idx_dir):
        return
    ix = open_dir(idx_dir)
    writer = ix.writer()
    writer.delete_by_term("memory_id", memory_id)
    writer.commit()


def search_memories(
    repo_root: str,
    query: str,
    limit: int = 5,
) -> list[dict]:
    """Search memories by content, tags, or source task.

    Returns matching memories sorted by relevance score.
    """
    idx_dir = _index_dir(repo_root)
    if not exists_in(idx_dir):
        return []

    try:
        ix = open_dir(idx_dir)
    except Exception as e:
        logger.warning("Cannot open memory index: %s", e)
        return []

    parser = MultifieldParser(
        ["content", "tags", "source_task"],
        schema=ix.schema,
    )

    safe = _safe_query(query)
    if not safe:
        return []

    try:
        parsed = parser.parse(safe)
    except Exception as e:
        logger.debug("Failed to parse memory query %r: %s", query, e)
        return []

    results: list[dict] = []
    with ix.searcher() as searcher:
        hits = searcher.search(parsed, limit=limit)
        for hit in hits:
            results.append({
                "memory_id": hit["memory_id"],
                "content": hit["content"],
                "category": hit["category"] or None,
                "tags": hit["tags"],
                "source_task": hit["source_task"] or None,
                "score": hit.score,
            })

    return results

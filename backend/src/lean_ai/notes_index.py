"""Whoosh-based full-text search index for global notes.

Index lives at ~/.lean_ai/notes/notes_index/. Follows the reference
library indexer pattern but simplified — notes are individually indexed
on create/update/delete rather than batch-processed.
"""

import logging
import os

from whoosh.fields import ID, TEXT, Schema
from whoosh.index import create_in, exists_in, open_dir
from whoosh.qparser import MultifieldParser

logger = logging.getLogger(__name__)

_NOTES_INDEX_DIR = os.path.join(os.path.expanduser("~"), ".lean_ai", "notes", "notes_index")

NOTES_SCHEMA = Schema(
    note_id=ID(stored=True, unique=True),
    content=TEXT(stored=True),
    project=ID(stored=True),
    tags=TEXT(stored=True),
)

# Characters with special meaning in Whoosh query syntax.
_WHOOSH_SPECIAL_CHARS = set('/:*?\\<>|"^~')


def _safe_query(q: str) -> str:
    """Escape special Whoosh characters in a user query."""
    return "".join(
        f"\\{c}" if c in _WHOOSH_SPECIAL_CHARS else c for c in q
    ).strip()


def _ensure_index():
    """Open or create the notes Whoosh index."""
    if exists_in(_NOTES_INDEX_DIR):
        return open_dir(_NOTES_INDEX_DIR)
    os.makedirs(_NOTES_INDEX_DIR, exist_ok=True)
    return create_in(_NOTES_INDEX_DIR, NOTES_SCHEMA)


def index_note(
    note_id: str,
    content: str,
    project: str | None = None,
    tags: list[str] | None = None,
) -> None:
    """Add or update a note in the search index."""
    ix = _ensure_index()
    writer = ix.writer()
    writer.update_document(
        note_id=note_id,
        content=content,
        project=project or "",
        tags=" ".join(tags) if tags else "",
    )
    writer.commit()


def remove_note(note_id: str) -> None:
    """Remove a note from the search index."""
    if not exists_in(_NOTES_INDEX_DIR):
        return
    ix = open_dir(_NOTES_INDEX_DIR)
    writer = ix.writer()
    writer.delete_by_term("note_id", note_id)
    writer.commit()


def search_notes(query: str, limit: int = 20) -> list[dict]:
    """Search notes by content, project, or tags.

    Returns matching notes sorted by relevance score.
    """
    if not exists_in(_NOTES_INDEX_DIR):
        return []

    try:
        ix = open_dir(_NOTES_INDEX_DIR)
    except Exception as e:
        logger.warning("Cannot open notes index: %s", e)
        return []

    parser = MultifieldParser(
        ["content", "project", "tags"],
        schema=ix.schema,
    )

    safe = _safe_query(query)
    if not safe:
        return []

    try:
        parsed = parser.parse(safe)
    except Exception as e:
        logger.debug("Failed to parse notes query %r: %s", query, e)
        return []

    results: list[dict] = []
    with ix.searcher() as searcher:
        hits = searcher.search(parsed, limit=limit)
        for hit in hits:
            results.append({
                "note_id": hit["note_id"],
                "content": hit["content"],
                "project": hit["project"] or None,
                "tags": hit["tags"],
                "score": hit.score,
            })

    return results

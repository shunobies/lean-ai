"""Whoosh-based knowledge document index.

Manages a separate Whoosh index for knowledge documents (EPUBs, PDFs,
Word docs, plain text, HTML, Markdown) stored in the knowledge directory
(default: ``.lean_ai/knowledge/``).

The knowledge index lives in its own directory (default:
``.lean_ai_knowledge_index/``) so it is completely independent of the
code index.  Both indexes are queried when assembling plan context.

Incremental updates
~~~~~~~~~~~~~~~~~~~
The same SHA-256 manifest pattern used by the code indexer is reused
here.  On each run ``index_knowledge()`` hashes every file in the
knowledge directory, compares against the saved manifest, and only
re-processes added or modified documents.  Deleted documents are removed
from the Whoosh index automatically.
"""

import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from whoosh.fields import ID, NUMERIC, TEXT, Schema
from whoosh.index import create_in, exists_in, open_dir
from whoosh.qparser import MultifieldParser

from lean_ai.config import settings
from lean_ai.indexer.manifest import (
    FileRecord,
    Manifest,
    compute_diff,
    hash_file_content,
    load_manifest,
    save_manifest,
)

logger = logging.getLogger(__name__)

# Whoosh schema for knowledge chunks.
# ``doc_title`` and ``section`` are included in full-text search so that
# queries like "chapter 3 configuration" or "ACME product spec" surface
# the right sections without requiring exact content match.
KNOWLEDGE_SCHEMA = Schema(
    chunk_id=ID(stored=True, unique=True),      # "rel_path:chunk_index"
    doc_path=ID(stored=True),                    # relative path in knowledge dir
    doc_title=TEXT(stored=True),                 # document title
    section=TEXT(stored=True),                   # chapter / heading / "Page N"
    content=TEXT(stored=True),                   # plain-text content
    format=ID(stored=True),                      # epub|pdf|docx|md|html|txt|rst
    chunk_index=NUMERIC(stored=True),
)

# Characters that have special meaning in Whoosh query syntax but are
# unlikely to be intentional in natural-language questions.
_WHOOSH_SPECIAL_CHARS = set('/:*?\\<>|"^~')


def knowledge_index_dir(repo_root: str) -> str:
    """Absolute path to the knowledge Whoosh index for *repo_root*."""
    return os.path.join(repo_root, settings.knowledge_index_dir)


def knowledge_dir_path(repo_root: str) -> Path:
    """Absolute path to the knowledge documents directory for *repo_root*."""
    return Path(repo_root) / settings.knowledge_dir


def is_knowledge_available(repo_root: str) -> bool:
    """Return ``True`` when a non-empty knowledge index exists."""
    idx_dir = knowledge_index_dir(repo_root)
    return exists_in(idx_dir)


def _list_knowledge_files(knowledge_dir: Path) -> list[tuple[str, Path]]:
    """Recursively list all readable knowledge files.

    Returns a list of ``(rel_path, full_path)`` tuples where *rel_path*
    is relative to *knowledge_dir*.  Only files with extensions supported
    by the reader registry are included.
    """
    from lean_ai.knowledge.readers.registry import supported_extensions

    exts = set(supported_extensions())
    results: list[tuple[str, Path]] = []

    for full_path in sorted(knowledge_dir.rglob("*")):
        if not full_path.is_file():
            continue
        if full_path.suffix.lower() not in exts:
            continue
        rel = full_path.relative_to(knowledge_dir)
        results.append((str(rel).replace("\\", "/"), full_path))

    return results


def index_knowledge(repo_root: str) -> dict:
    """Index all knowledge documents in the knowledge directory.

    Decides between a full re-index and an incremental update based on
    whether a valid manifest and Whoosh index already exist.

    This is a **synchronous** operation — call via ``asyncio.to_thread``
    in async contexts.

    Returns a stats dict with keys:
        ``status``, ``mode``, ``doc_count``, ``chunk_count``,
        ``added``, ``modified``, ``deleted``, ``unchanged``,
        ``indexed_at``.
    """
    kdir = knowledge_dir_path(repo_root)

    if not kdir.is_dir():
        logger.debug("Knowledge dir not found at %s — skipping", kdir)
        return {"status": "no_knowledge_dir", "doc_count": 0, "chunk_count": 0}

    files = _list_knowledge_files(kdir)
    if not files:
        logger.debug("Knowledge dir %s is empty — skipping", kdir)
        return {"status": "empty", "doc_count": 0, "chunk_count": 0}

    idx_path = knowledge_index_dir(repo_root)

    # Hash every file for incremental comparison.
    current_hashes: dict[str, str] = {}
    for rel_path, full_path in files:
        try:
            current_hashes[rel_path] = hash_file_content(full_path)
        except (OSError, PermissionError) as e:
            logger.warning("Cannot hash knowledge file %s: %s", rel_path, e)

    old_manifest = load_manifest(Path(idx_path))

    if old_manifest is not None and exists_in(idx_path):
        return _incremental_knowledge_index(
            kdir=kdir,
            idx_path=idx_path,
            files=files,
            current_hashes=current_hashes,
            old_manifest=old_manifest,
        )

    return _full_knowledge_index(
        kdir=kdir,
        idx_path=idx_path,
        files=files,
        current_hashes=current_hashes,
    )


def _read_and_chunk(kdir: Path, rel_path: str, full_path: Path) -> list:
    """Read a document and return its KnowledgeChunks (or empty list)."""
    from lean_ai.knowledge.readers.registry import read_document
    return read_document(full_path, rel_path)


def _full_knowledge_index(
    *,
    kdir: Path,
    idx_path: str,
    files: list[tuple[str, Path]],
    current_hashes: dict[str, str],
) -> dict:
    """Full re-index: wipe and rebuild from scratch."""
    if os.path.exists(idx_path):
        shutil.rmtree(idx_path)
    os.makedirs(idx_path, exist_ok=True)

    ix = create_in(idx_path, KNOWLEDGE_SCHEMA)
    writer = ix.writer()

    manifest = Manifest(
        version=1,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    total_chunks = 0

    for rel_path, full_path in files:
        chunks = _read_and_chunk(kdir, rel_path, full_path)
        for chunk in chunks:
            chunk_id = f"{rel_path}:{chunk.chunk_index}"
            writer.add_document(
                chunk_id=chunk_id,
                doc_path=chunk.doc_path,
                doc_title=chunk.doc_title,
                section=chunk.section,
                content=chunk.content,
                format=chunk.format,
                chunk_index=chunk.chunk_index,
            )
            total_chunks += 1

        manifest.files[rel_path] = FileRecord(
            sha256=current_hashes.get(rel_path, ""),
            chunk_count=len(chunks),
        )
        logger.debug("Indexed knowledge doc %s → %d chunks", rel_path, len(chunks))

    writer.commit()
    save_manifest(Path(idx_path), manifest)

    stats = {
        "status": "indexed",
        "mode": "full",
        "doc_count": len(files),
        "chunk_count": total_chunks,
        "added": len(files),
        "modified": 0,
        "deleted": 0,
        "unchanged": 0,
        "indexed_at": datetime.now(timezone.utc).isoformat(),
    }
    logger.info(
        "Knowledge full index: %d docs (%d chunks) in %s",
        len(files), total_chunks, kdir,
    )
    return stats


def _incremental_knowledge_index(
    *,
    kdir: Path,
    idx_path: str,
    files: list[tuple[str, Path]],
    current_hashes: dict[str, str],
    old_manifest: Manifest,
) -> dict:
    """Incremental index: update only added/modified/deleted documents."""
    # lean_ai's compute_diff signature: (current_files, old_manifest)
    diff = compute_diff(current_hashes, old_manifest)

    if not diff.added and not diff.modified and not diff.deleted:
        total_chunks = sum(r.chunk_count for r in old_manifest.files.values())
        logger.info("Knowledge index: no changes detected in %s", kdir)
        return {
            "status": "already_indexed",
            "mode": "incremental",
            "doc_count": len(files),
            "chunk_count": total_chunks,
            "added": 0,
            "modified": 0,
            "deleted": 0,
            "unchanged": len(diff.unchanged),
            "indexed_at": datetime.now(timezone.utc).isoformat(),
        }

    ix = open_dir(idx_path)
    writer = ix.writer()

    # Delete chunks for removed documents.
    for rel_path in diff.deleted:
        old_count = old_manifest.files[rel_path].chunk_count
        for i in range(old_count):
            writer.delete_by_term("chunk_id", f"{rel_path}:{i}")

    # Delete + re-add modified documents.
    new_chunk_counts: dict[str, int] = {}
    for rel_path in diff.modified:
        old_count = old_manifest.files[rel_path].chunk_count
        for i in range(old_count):
            writer.delete_by_term("chunk_id", f"{rel_path}:{i}")

        full_path = kdir / rel_path
        chunks = _read_and_chunk(kdir, rel_path, full_path)
        for chunk in chunks:
            writer.add_document(
                chunk_id=f"{rel_path}:{chunk.chunk_index}",
                doc_path=chunk.doc_path,
                doc_title=chunk.doc_title,
                section=chunk.section,
                content=chunk.content,
                format=chunk.format,
                chunk_index=chunk.chunk_index,
            )
        new_chunk_counts[rel_path] = len(chunks)

    # Add new documents.
    for rel_path in diff.added:
        full_path = kdir / rel_path
        chunks = _read_and_chunk(kdir, rel_path, full_path)
        for chunk in chunks:
            writer.add_document(
                chunk_id=f"{rel_path}:{chunk.chunk_index}",
                doc_path=chunk.doc_path,
                doc_title=chunk.doc_title,
                section=chunk.section,
                content=chunk.content,
                format=chunk.format,
                chunk_index=chunk.chunk_index,
            )
        new_chunk_counts[rel_path] = len(chunks)

    writer.commit()

    # Build updated manifest.
    new_manifest = Manifest(
        version=1,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    for rel_path in diff.unchanged:
        new_manifest.files[rel_path] = old_manifest.files[rel_path]
    for rel_path in diff.modified:
        new_manifest.files[rel_path] = FileRecord(
            sha256=current_hashes[rel_path],
            chunk_count=new_chunk_counts.get(rel_path, 0),
        )
    for rel_path in diff.added:
        new_manifest.files[rel_path] = FileRecord(
            sha256=current_hashes[rel_path],
            chunk_count=new_chunk_counts.get(rel_path, 0),
        )

    save_manifest(Path(idx_path), new_manifest)

    total_chunks = sum(r.chunk_count for r in new_manifest.files.values())
    stats = {
        "status": "indexed",
        "mode": "incremental",
        "doc_count": len(new_manifest.files),
        "chunk_count": total_chunks,
        "added": len(diff.added),
        "modified": len(diff.modified),
        "deleted": len(diff.deleted),
        "unchanged": len(diff.unchanged),
        "indexed_at": datetime.now(timezone.utc).isoformat(),
    }
    logger.info(
        "Knowledge incremental index: +%d ~%d -%d =%d in %s",
        len(diff.added), len(diff.modified),
        len(diff.deleted), len(diff.unchanged),
        kdir,
    )
    return stats


def search_knowledge(
    repo_root: str,
    query: str,
    limit: int = 10,
) -> list[dict]:
    """Search the knowledge index with BM25F full-text search.

    Searches across ``content``, ``doc_title``, and ``section`` fields.
    Returns matching chunks sorted by relevance score.

    Returns an empty list when no knowledge index exists or the query
    produces no results.
    """
    idx_path = knowledge_index_dir(repo_root)
    if not exists_in(idx_path):
        return []

    try:
        ix = open_dir(idx_path)
    except Exception as e:
        logger.warning("Cannot open knowledge index at %s: %s", idx_path, e)
        return []

    parser = MultifieldParser(
        ["content", "doc_title", "section"],
        schema=ix.schema,
    )

    # Escape special Whoosh characters that may appear in natural language
    # queries (e.g. hyphens, slashes, colons).
    safe_query = _safe_query(query)
    if not safe_query:
        return []

    try:
        parsed = parser.parse(safe_query)
    except Exception as e:
        logger.debug("Failed to parse knowledge query %r: %s", query, e)
        return []

    results: list[dict] = []
    try:
        with ix.searcher() as searcher:
            hits = searcher.search(parsed, limit=limit)
            for hit in hits:
                results.append({
                    "chunk_id": hit["chunk_id"],
                    "doc_path": hit["doc_path"],
                    "doc_title": hit["doc_title"],
                    "section": hit["section"],
                    "content": hit["content"],
                    "format": hit["format"],
                    "chunk_index": hit["chunk_index"],
                    "score": hit.score,
                })
    except Exception as e:
        logger.warning("Knowledge search failed for %r: %s", query, e)

    return results


def _safe_query(query: str) -> str:
    """Escape characters that confuse the Whoosh query parser.

    Replaces Whoosh special characters with spaces, then collapses
    whitespace.  No regex used.
    """
    cleaned = []
    for ch in query:
        if ch in _WHOOSH_SPECIAL_CHARS:
            cleaned.append(" ")
        else:
            cleaned.append(ch)
    return " ".join("".join(cleaned).split())

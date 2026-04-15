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
        logger.info("Knowledge dir not found at %s — skipping", kdir)
        return {"status": "no_knowledge_dir", "doc_count": 0, "chunk_count": 0}

    files = _list_knowledge_files(kdir)
    if not files:
        # Check if files exist but aren't supported (missing optional deps).
        all_files = [p for p in kdir.rglob("*") if p.is_file()]
        if all_files:
            from lean_ai.knowledge.readers.registry import supported_extensions
            exts = set(supported_extensions())
            unsupported = sorted({
                p.suffix.lower() for p in all_files
                if p.suffix.lower() and p.suffix.lower() not in exts
            })
            logger.warning(
                "Knowledge dir %s has %d file(s) but none match supported "
                "extensions %s. Found extensions: %s. "
                "Install optional deps: pip install 'lean-ai[knowledge]'",
                kdir, len(all_files), sorted(exts), unsupported,
            )
            return {
                "status": "unsupported_files",
                "doc_count": 0,
                "chunk_count": 0,
                "total_files_found": len(all_files),
                "skipped_extensions": unsupported,
            }
        logger.info("Knowledge dir %s is empty — skipping", kdir)
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
    skipped = 0

    try:
        for rel_path, full_path in files:
            try:
                chunks = _read_and_chunk(kdir, rel_path, full_path)
            except Exception:
                logger.warning("Skipping unreadable knowledge doc: %s", rel_path, exc_info=True)
                skipped += 1
                continue

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
    except Exception:
        writer.cancel()
        raise
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

    try:
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
            try:
                chunks = _read_and_chunk(kdir, rel_path, full_path)
            except Exception:
                logger.warning("Skipping unreadable knowledge doc: %s", rel_path, exc_info=True)
                continue

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
            try:
                chunks = _read_and_chunk(kdir, rel_path, full_path)
            except Exception:
                logger.warning("Skipping unreadable knowledge doc: %s", rel_path, exc_info=True)
                continue

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
    except Exception:
        writer.cancel()
        raise

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


async def generate_knowledge_embeddings(
    repo_root: str,
    llm_client,
    batch_size: int = 32,
) -> int:
    """Generate embeddings for knowledge chunks, skipping unchanged ones.

    Uses a content hash per chunk stored in the embedding index to avoid
    re-embedding chunks whose text has not changed since the last run.
    """
    import hashlib

    from lean_ai.indexer.embeddings import EmbeddingStore

    idx_path = knowledge_index_dir(repo_root)
    if not exists_in(idx_path):
        return 0

    store = EmbeddingStore(idx_path)
    existing_index = store.get_index()

    ix = open_dir(idx_path)
    reader = ix.reader()

    all_chunks: list[tuple[str, str]] = []
    for doc_num in reader.all_doc_ids():
        stored = reader.stored_fields(doc_num)
        chunk_id = stored["chunk_id"]
        # Richer embedding text: title + section give context to the chunk.
        title = stored.get("doc_title", "")
        section = stored.get("section", "")
        content = stored.get("content", "")
        header = f"{title} — {section}" if section else title
        text = f"{header}\n{content}" if header else content
        all_chunks.append((chunk_id, text))

    reader.close()

    if not all_chunks:
        return 0

    # Drop orphaned embeddings (chunks deleted from Whoosh).
    current_ids = {cid for cid, _ in all_chunks}
    orphaned = set(existing_index.keys()) - current_ids
    if orphaned:
        store.remove_chunks(orphaned)
        logger.info(
            "Removed %d orphaned knowledge embeddings", len(orphaned),
        )

    # Find chunks needing embedding (new or content changed).
    to_embed: list[tuple[str, str, str]] = []
    for chunk_id, text in all_chunks:
        content_hash = hashlib.sha256(
            text.encode(),
        ).hexdigest()[:16]
        existing = existing_index.get(chunk_id)
        if existing and existing.get("content_hash") == content_hash:
            continue
        to_embed.append((chunk_id, text, content_hash))

    if not to_embed:
        store.flush_index()
        logger.info(
            "Knowledge embeddings up to date — %d chunks unchanged",
            len(all_chunks),
        )
        return 0

    total = 0
    for i in range(0, len(to_embed), batch_size):
        batch = to_embed[i : i + batch_size]
        batch_ids = [cid for cid, _, _ in batch]
        batch_texts = [t for _, t, _ in batch]
        batch_hashes = [h for _, _, h in batch]

        try:
            embeddings = await llm_client.embed(batch_texts)
            store.save_batch(batch_ids, embeddings, batch_hashes)
            total += len(embeddings)
        except Exception as e:
            logger.warning(
                "Knowledge embedding batch %d failed: %s",
                i // batch_size, e,
            )

    store.flush_index()
    logger.info(
        "Generated %d knowledge embeddings "
        "(%d unchanged, %d orphaned removed)",
        total,
        len(all_chunks) - len(to_embed),
        len(orphaned),
    )
    return total


def search_knowledge(
    repo_root: str,
    query: str,
    limit: int = 10,
    query_embedding: list[float] | None = None,
) -> list[dict]:
    """Search the knowledge index with BM25F full-text search.

    Searches across ``content``, ``doc_title``, and ``section`` fields.
    Returns matching chunks sorted by relevance score.  When
    *query_embedding* is provided and an embedding store exists,
    results are re-ranked using Reciprocal Rank Fusion (BM25 + semantic).

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

    # RRF re-ranking when embeddings are available.
    if query_embedding and results:
        try:
            from lean_ai.indexer.embeddings import EmbeddingStore, semantic_rerank
            store = EmbeddingStore(idx_path)
            if store.get_all_embeddings():
                results = semantic_rerank(results, query_embedding, store)
        except Exception as e:
            logger.debug("Knowledge RRF re-ranking skipped: %s", e)

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

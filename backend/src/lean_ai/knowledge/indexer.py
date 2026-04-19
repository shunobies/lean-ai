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

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from whoosh.fields import ID, NUMERIC, TEXT, Schema
from whoosh.index import create_in, exists_in, open_dir
from whoosh.qparser import MultifieldParser
from whoosh.query import And, NumericRange, Term

if TYPE_CHECKING:
    from lean_ai.indexer.indexer import EmbeddingRunStats

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

# Schema version for the chunk-config sentinel.  Bump when the chunker's
# semantics change in a way that requires a rebuild beyond size-only
# differences (e.g. different paragraph-splitting rules).
_CHUNK_CONFIG_SCHEMA_VERSION = 1
_CHUNK_CONFIG_FILENAME = "chunk_config.json"


def _chunk_config_path(idx_path: str) -> Path:
    """Path to the sentinel file tracking chunk-generation settings."""
    return Path(idx_path) / _CHUNK_CONFIG_FILENAME


def _current_chunk_config() -> dict:
    """Config fingerprint that, if changed, requires a full rebuild."""
    return {
        "kb_chunk_chars": int(settings.kb_chunk_chars),
        "schema_version": _CHUNK_CONFIG_SCHEMA_VERSION,
    }


def _read_chunk_config(idx_path: str) -> dict | None:
    """Return stored chunk config or ``None`` if missing/unreadable."""
    path = _chunk_config_path(idx_path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Cannot read %s: %s", path, e)
        return None


def _write_chunk_config(idx_path: str) -> None:
    """Persist the current chunk config alongside the index."""
    path = _chunk_config_path(idx_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(_current_chunk_config(), indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("Cannot write %s: %s", path, e)


def is_chunk_config_stale(repo_root: str) -> bool:
    """Return ``True`` when the on-disk chunk config differs from current settings.

    When no index or sentinel exists, returns ``False`` — a missing index
    is not "stale", it simply hasn't been built yet.
    """
    idx_path = knowledge_index_dir(repo_root)
    if not exists_in(idx_path):
        return False
    stored = _read_chunk_config(idx_path)
    if stored is None:
        # Legacy index with no sentinel — treat as stale so the new chunk
        # size takes effect on next indexing run.
        return True
    return stored != _current_chunk_config()


def requires_kb_rebuild(repo_root: str) -> dict:
    """Diagnostic: return details when the KB index is stale vs current settings."""
    idx_path = knowledge_index_dir(repo_root)
    if not exists_in(idx_path):
        return {"stale": False, "reason": "no_index"}
    stored = _read_chunk_config(idx_path)
    current = _current_chunk_config()
    if stored is None:
        return {
            "stale": True,
            "reason": "missing_sentinel",
            "current": current,
        }
    if stored != current:
        return {
            "stale": True,
            "reason": "config_changed",
            "stored": stored,
            "current": current,
        }
    return {"stale": False, "reason": "up_to_date"}


def _reset_for_rebuild(idx_path: str, reason: str) -> None:
    """Wipe the Whoosh index, embedding store, and manifest to force a full rebuild."""
    logger.info(
        "Knowledge index rebuild required (%s) — wiping %s",
        reason, idx_path,
    )
    # Remove the whole index directory — the Whoosh files, the manifest
    # (written by lean_ai.indexer.manifest), the embedding store, and the
    # chunk-config sentinel all live inside it.
    if os.path.isdir(idx_path):
        shutil.rmtree(idx_path, ignore_errors=True)


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

    # If the chunk configuration has changed since the index was built,
    # a full rebuild is required — incremental updates would leave old
    # chunks at old sizes mixed with new ones at new sizes.
    if exists_in(idx_path):
        stale = _read_chunk_config(idx_path)
        current = _current_chunk_config()
        if stale is None:
            _reset_for_rebuild(idx_path, "missing chunk config sentinel")
        elif stale != current:
            _reset_for_rebuild(
                idx_path,
                f"chunk config changed ({stale} → {current})",
            )

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
    _write_chunk_config(idx_path)

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
    _write_chunk_config(idx_path)

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
    batch_size: int = 0,
) -> "EmbeddingRunStats":
    """Generate embeddings for knowledge chunks, skipping unchanged ones.

    Uses a content hash per chunk stored in the embedding index to avoid
    re-embedding chunks whose text has not changed since the last run.

    A producer-consumer pipeline overlaps Ollama compute with disk I/O.

    Registers ``embeddings.knowledge`` on the runtime-state busy set
    while running so extension-side health monitors treat slow
    ``/api/health`` responses during this call as expected rather than
    as a dead backend.

    Returns:
        EmbeddingRunStats with a breakdown of embedded, unchanged,
        orphaned-removed, and failed-batch counts. Shares the code-side
        dataclass from ``lean_ai.indexer.indexer`` so the caller can
        format either result uniformly.
    """
    from lean_ai.runtime_state import busy

    with busy("embeddings.knowledge"):
        return await _generate_knowledge_embeddings_inner(
            repo_root, llm_client, batch_size,
        )


async def _generate_knowledge_embeddings_inner(
    repo_root: str,
    llm_client,
    batch_size: int,
) -> "EmbeddingRunStats":
    import asyncio
    import hashlib
    import time

    from lean_ai.indexer.embeddings import EmbeddingStore
    from lean_ai.indexer.indexer import EmbeddingRunStats

    stats = EmbeddingRunStats()

    logger.info("[knowledge embed] ENTER for %s", repo_root)

    idx_path = knowledge_index_dir(repo_root)
    if not exists_in(idx_path):
        logger.info(
            "[knowledge embed] no knowledge index at %s — nothing to embed",
            idx_path,
        )
        return stats

    def _sync_setup():
        """Whoosh iteration + existing-index load — off the event loop.

        For a 5k+-chunk knowledge index these sync reads add up to
        seconds of blocked event-loop time; running them in a thread
        keeps ``/api/health`` responsive.
        """
        t0 = time.perf_counter()
        store = EmbeddingStore(idx_path)
        existing = store.get_index()
        t1 = time.perf_counter()
        logger.info(
            "[knowledge embed] loaded existing-index (%d entries) in %.2fs",
            len(existing), t1 - t0,
        )

        ix = open_dir(idx_path)
        reader = ix.reader()
        chunks: list[tuple[str, str]] = []
        try:
            for doc_num in reader.all_doc_ids():
                s = reader.stored_fields(doc_num)
                chunk_id = s["chunk_id"]
                title = s.get("doc_title", "")
                section = s.get("section", "")
                content = s.get("content", "")
                header = f"{title} — {section}" if section else title
                text = f"{header}\n{content}" if header else content
                chunks.append((chunk_id, text))
        finally:
            reader.close()
        t2 = time.perf_counter()
        logger.info(
            "[knowledge embed] read %d Whoosh chunks in %.2fs",
            len(chunks), t2 - t1,
        )
        return chunks, existing, store

    all_chunks, existing_index, store = await asyncio.to_thread(_sync_setup)

    if not all_chunks:
        logger.info("[knowledge embed] knowledge index is empty")
        return stats

    def _sync_diff() -> tuple[list[tuple[str, str, str]], set[str], int]:
        """Hash + diff — off the event loop for large indexes."""
        t0 = time.perf_counter()
        current_ids = {cid for cid, _ in all_chunks}
        orphans = set(existing_index.keys()) - current_ids

        to_embed_local: list[tuple[str, str, str]] = []
        for chunk_id, text in all_chunks:
            content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
            existing = existing_index.get(chunk_id)
            if existing and existing.get("content_hash") == content_hash:
                continue
            to_embed_local.append((chunk_id, text, content_hash))
        t1 = time.perf_counter()
        logger.info(
            "[knowledge embed] hashed+diffed %d chunks in %.2fs "
            "(%d need embedding, %d orphans)",
            len(all_chunks), t1 - t0, len(to_embed_local), len(orphans),
        )
        return to_embed_local, orphans, len(all_chunks)

    to_embed, orphaned, all_count = await asyncio.to_thread(_sync_diff)

    if orphaned:
        logger.info(
            "[knowledge embed] removing %d orphaned entries from index…",
            len(orphaned),
        )
        await asyncio.to_thread(store.remove_chunks, orphaned)
        logger.info(
            "[knowledge embed] orphan index entries removed, compacting…",
        )
        await asyncio.to_thread(store.compact)
        logger.info("[knowledge embed] compact complete")
    stats.orphaned_removed = len(orphaned)
    stats.unchanged = all_count - len(to_embed)

    if not to_embed:
        await asyncio.to_thread(store.flush_index)
        logger.info(
            "[knowledge embed] up to date — %d unchanged, %d orphans removed "
            "(no embed calls made)",
            stats.unchanged, stats.orphaned_removed,
        )
        return stats

    # Resolve batch size: explicit > adaptive > fallback.
    if batch_size <= 0:
        logger.info("[knowledge embed] computing adaptive batch size")
        batch_size = await llm_client.compute_embedding_batch_size(to_embed)

    total_to_embed = len(to_embed)
    stats.total_batches = (total_to_embed + batch_size - 1) // batch_size
    logger.info(
        "[knowledge embed] calling Ollama: %d chunks, batch_size=%d, "
        "%d unchanged, %d orphans removed",
        total_to_embed, batch_size, stats.unchanged, stats.orphaned_removed,
    )

    # Producer-consumer: overlap Ollama compute with disk I/O.
    queue: asyncio.Queue = asyncio.Queue(maxsize=2)
    total = 0

    async def _producer():
        for i in range(0, total_to_embed, batch_size):
            # Yield to the event loop so /api/health and other handlers
            # get scheduled between batches — keeps the extension's
            # health probe responsive during long embedding runs.
            await asyncio.sleep(0)
            batch = to_embed[i : i + batch_size]
            batch_ids = [cid for cid, _, _ in batch]
            batch_texts = [t for _, t, _ in batch]
            batch_hashes = [h for _, _, h in batch]
            batch_num = i // batch_size
            t0 = time.perf_counter()
            logger.info(
                "[knowledge embed] batch %d/%d → Ollama (%d texts)",
                batch_num + 1, stats.total_batches, len(batch_texts),
            )
            try:
                embeddings = await llm_client.embed(batch_texts)
                t1 = time.perf_counter()
                logger.info(
                    "[knowledge embed] batch %d returned in %.1fs",
                    batch_num + 1, t1 - t0,
                )
                await queue.put((batch_ids, embeddings, batch_hashes))
            except Exception as e:
                stats.failed_batches += 1
                t1 = time.perf_counter()
                logger.warning(
                    "[knowledge embed] batch %d FAILED after %.1fs: %s",
                    batch_num + 1, t1 - t0, e,
                )
        await queue.put(None)  # sentinel

    async def _consumer():
        nonlocal total
        while True:
            item = await queue.get()
            if item is None:
                break
            batch_ids, embeddings, batch_hashes = item
            await asyncio.to_thread(
                store.save_batch, batch_ids, embeddings, batch_hashes,
            )
            total += len(embeddings)
            logger.info(
                "Embedding progress: %d/%d knowledge chunks (%.0f%%)",
                total, total_to_embed, (total / total_to_embed) * 100,
            )

    producer_task = asyncio.create_task(_producer())
    await _consumer()
    await producer_task

    store.flush_index()
    stats.embedded = total
    logger.info(
        "Generated %d knowledge embeddings "
        "(%d unchanged, %d orphaned removed, %d/%d batches failed)",
        stats.embedded, stats.unchanged, stats.orphaned_removed,
        stats.failed_batches, stats.total_batches,
    )
    return stats


def search_knowledge(
    repo_root: str,
    query: str,
    limit: int = 10,
    query_embedding: list[float] | None = None,
    expand: bool = True,
    document: str | None = None,
) -> list[dict]:
    """Search the knowledge index with BM25F full-text search.

    Searches across ``content``, ``doc_title``, and ``section`` fields.
    Returns matching chunks sorted by relevance score.  When
    *query_embedding* is provided and an embedding store exists,
    results are re-ranked using Reciprocal Rank Fusion (BM25 + semantic).

    When ``document`` is provided, results are restricted to a single
    document. The value is matched against ``doc_path`` first (exact
    match — for paths copied from ``list_documents()``), then against
    ``doc_title`` (substring) as a fallback so the LLM can pass a
    human-readable title from prior search results.

    When ``expand`` is ``True`` and ``settings.kb_neighbor_window > 0``,
    each hit is expanded with ``±kb_neighbor_window`` adjacent chunks
    from the same document and contiguous ranges are merged into single
    passage dicts.  Pass ``expand=False`` to get raw unmerged chunks
    (useful for debugging).

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

    # Build optional document filter: doc_path exact OR doc_title substring.
    # When the caller asked for a specific document but nothing matched,
    # short-circuit to empty results instead of falling back to unfiltered
    # search — that would silently return hits from other documents.
    doc_filter = None
    if document:
        doc_filter = _build_document_filter(ix, document)
        if doc_filter is None:
            return []

    results: list[dict] = []
    try:
        with ix.searcher() as searcher:
            if doc_filter is not None:
                hits = searcher.search(parsed, limit=limit, filter=doc_filter)
            else:
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

    # Small-to-big expansion: replace point hits with merged multi-chunk
    # passages for richer LLM context.
    window = max(0, int(settings.kb_neighbor_window))
    if expand and window > 0 and results:
        try:
            return _expand_with_neighbors(idx_path, results, window)
        except Exception as e:
            logger.warning(
                "Neighbor expansion failed for %r: %s — returning raw hits",
                query, e,
            )

    return results


def _expand_with_neighbors(
    idx_path: str,
    hits: list[dict],
    window: int,
) -> list[dict]:
    """Merge each hit with its ±window neighbors into contiguous passages.

    Groups hits by ``doc_path``, merges overlapping ``[idx-W, idx+W]``
    intervals per document, then fetches each merged range from Whoosh.
    Each output dict represents one merged passage:

    - ``doc_path``, ``doc_title``, ``format``
    - ``chunk_index_start``, ``chunk_index_end`` — the merged range
    - ``section`` — first non-empty section in the range (or ``""``)
    - ``sections`` — ordered de-duplicated list of section titles
    - ``content`` — chunks joined with ``\\n\\n`` in ascending order
    - ``score`` — max score among constituent hit chunks
    - ``hit_chunk_indices`` — which chunks in the range were original hits
    """
    # Group hit chunk_index + score per doc_path.
    by_doc: dict[str, list[dict]] = {}
    for h in hits:
        by_doc.setdefault(h["doc_path"], []).append(h)

    ix = open_dir(idx_path)
    passages: list[dict] = []

    try:
        with ix.searcher() as searcher:
            for doc_path, doc_hits in by_doc.items():
                # Build intervals and merge overlaps.
                intervals: list[tuple[int, int, float, set[int]]] = []
                for h in doc_hits:
                    idx = int(h["chunk_index"])
                    lo = max(0, idx - window)
                    hi = idx + window
                    intervals.append((lo, hi, h.get("score", 0.0) or 0.0, {idx}))
                intervals.sort(key=lambda t: t[0])

                merged: list[list] = []
                for lo, hi, score, hit_idx in intervals:
                    if merged and lo <= merged[-1][1] + 1:
                        # Overlapping or adjacent — extend previous.
                        merged[-1][1] = max(merged[-1][1], hi)
                        merged[-1][2] = max(merged[-1][2], score)
                        merged[-1][3] |= hit_idx
                    else:
                        merged.append([lo, hi, score, set(hit_idx)])

                # Fetch chunks for each merged range.
                for lo, hi, score, hit_idx in merged:
                    q = And([
                        Term("doc_path", doc_path),
                        NumericRange("chunk_index", lo, hi),
                    ])
                    range_hits = searcher.search(q, limit=None)
                    fetched: list[dict] = []
                    for rh in range_hits:
                        fetched.append({
                            "chunk_index": int(rh["chunk_index"]),
                            "section": rh.get("section", "") or "",
                            "content": rh.get("content", "") or "",
                            "doc_title": rh.get("doc_title", "") or "",
                            "format": rh.get("format", "") or "",
                        })
                    if not fetched:
                        continue
                    fetched.sort(key=lambda c: c["chunk_index"])

                    start_idx = fetched[0]["chunk_index"]
                    end_idx = fetched[-1]["chunk_index"]
                    joined = "\n\n".join(c["content"] for c in fetched if c["content"])
                    if not joined.strip():
                        continue

                    # Dedupe section titles in order (first occurrence wins).
                    seen_sections: set[str] = set()
                    sections_ordered: list[str] = []
                    for c in fetched:
                        sec = c["section"]
                        if sec and sec not in seen_sections:
                            seen_sections.add(sec)
                            sections_ordered.append(sec)

                    passages.append({
                        "doc_path": doc_path,
                        "doc_title": fetched[0]["doc_title"],
                        "format": fetched[0]["format"],
                        "section": sections_ordered[0] if sections_ordered else "",
                        "sections": sections_ordered,
                        "chunk_index_start": start_idx,
                        "chunk_index_end": end_idx,
                        "content": joined,
                        "score": float(score),
                        "hit_chunk_indices": sorted(hit_idx),
                    })
    finally:
        ix.close()

    passages.sort(key=lambda p: p["score"], reverse=True)
    return passages


def _build_document_filter(ix, document: str):
    """Return a Whoosh query restricting hits to a single document.

    The value is matched in this priority order:

    1. Exact ``doc_path`` match — for paths copied verbatim from
       :func:`list_documents`.
    2. Exact ``doc_title`` match (any chunk's title equals the value).
    3. ``doc_title`` substring match — parsed via the title field's
       analyzer so the LLM can pass a fragment of a remembered title.

    Returns ``None`` when the value yields no matchable filter (which
    will be treated by the caller as "no filter" and return all results).
    """
    from whoosh.qparser import QueryParser

    value = document.strip()
    if not value:
        return None

    # 1. Exact doc_path match
    with ix.searcher() as searcher:
        path_hits = searcher.search(Term("doc_path", value), limit=1)
        if path_hits:
            return Term("doc_path", value)

        # 2. Exact doc_title — collect matching paths so we can OR them.
        title_parser = QueryParser("doc_title", schema=ix.schema)
        try:
            title_q = title_parser.parse(f'"{_safe_query(value)}"')
        except Exception:
            title_q = None
        if title_q is not None:
            title_hits = searcher.search(title_q, limit=None)
            paths = sorted({h["doc_path"] for h in title_hits})
            if paths:
                if len(paths) == 1:
                    return Term("doc_path", paths[0])
                from whoosh.query import Or
                return Or([Term("doc_path", p) for p in paths])

    return None


def list_documents(
    repo_root: str,
    name_filter: str = "",
) -> list[dict]:
    """Return one entry per indexed knowledge document.

    Each entry has ``doc_title``, ``doc_path``, ``format``, and
    ``chunk_count``.  Sorted alphabetically by title.

    *name_filter* is a case-insensitive substring matched against
    ``doc_title``; pass an empty string for the full list.

    Returns an empty list when no knowledge index exists.
    """
    idx_path = knowledge_index_dir(repo_root)
    if not exists_in(idx_path):
        return []

    try:
        ix = open_dir(idx_path)
    except Exception as e:
        logger.warning("Cannot open knowledge index at %s: %s", idx_path, e)
        return []

    by_doc: dict[str, dict] = {}
    needle = name_filter.strip().lower()
    try:
        with ix.searcher() as searcher:
            for stored in searcher.documents():
                doc_path = stored.get("doc_path", "") or ""
                if not doc_path:
                    continue
                entry = by_doc.get(doc_path)
                if entry is None:
                    title = stored.get("doc_title", "") or ""
                    if needle and needle not in title.lower():
                        continue
                    entry = {
                        "doc_title": title,
                        "doc_path": doc_path,
                        "format": stored.get("format", "") or "",
                        "chunk_count": 0,
                    }
                    by_doc[doc_path] = entry
                entry["chunk_count"] += 1
    finally:
        ix.close()

    return sorted(by_doc.values(), key=lambda d: (d["doc_title"].lower(), d["doc_path"]))


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

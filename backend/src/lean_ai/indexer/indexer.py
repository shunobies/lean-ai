"""Whoosh BM25F search index with full and incremental indexing."""

import logging
from pathlib import Path

from whoosh.fields import ID, NUMERIC, TEXT, Schema
from whoosh.index import create_in, exists_in, open_dir
from whoosh.qparser import MultifieldParser

from lean_ai.config import settings
from lean_ai.indexer.chunker import chunk_file
from lean_ai.indexer.embeddings import EmbeddingStore, semantic_rerank
from lean_ai.indexer.manifest import (
    FileRecord,
    Manifest,
    compute_diff,
    hash_file_content,
    load_manifest,
    save_manifest,
)
from lean_ai.indexer.tree import list_repo_tree

logger = logging.getLogger(__name__)

INDEX_SCHEMA = Schema(
    chunk_id=ID(stored=True, unique=True),
    file_path=ID(stored=True),
    content=TEXT(stored=True),
    language=ID(stored=True),
    start_line=NUMERIC(stored=True),
    end_line=NUMERIC(stored=True),
)


def _index_dir(repo_root: str) -> Path:
    return Path(repo_root) / settings.index_dir


def _get_head_commit(repo_root: str) -> str:
    try:
        head = Path(repo_root) / ".git" / "HEAD"
        if head.exists():
            ref = head.read_text().strip()
            if ref.startswith("ref:"):
                ref_path = Path(repo_root) / ".git" / ref.split(" ", 1)[1]
                if ref_path.exists():
                    return ref_path.read_text().strip()[:12]
            return ref[:12]
    except Exception:
        pass
    return ""


def _hash_all_files(repo_root: str, entries=None) -> dict[str, str]:
    """Build {rel_path: sha256} dict for all indexable files."""
    root = Path(repo_root)
    if entries is None:
        entries = list_repo_tree(repo_root)
    return {e.path: hash_file_content(root / e.path) for e in entries}


def index_workspace(repo_root: str, force: bool = False) -> tuple[int, int]:
    """Index the workspace. Returns ``(file_count, chunk_count)``.

    Uses incremental indexing if a valid manifest exists, otherwise full.
    Walks the repo tree once and passes entries to avoid redundant traversals.
    """
    idx_dir = _index_dir(repo_root)
    idx_dir.mkdir(parents=True, exist_ok=True)

    # Walk repo tree once and reuse for both hashing and indexing
    entries = list_repo_tree(repo_root)

    if force or not exists_in(str(idx_dir)):
        return _full_index(repo_root, idx_dir, entries=entries)

    old_manifest = load_manifest(idx_dir)
    if old_manifest is None:
        return _full_index(repo_root, idx_dir, entries=entries)

    return _incremental_index(repo_root, idx_dir, old_manifest, entries=entries)


def _full_index(repo_root: str, idx_dir: Path, entries=None) -> tuple[int, int]:
    """Wipe and rebuild the index from scratch."""
    logger.info("Full index of %s", repo_root)
    root = Path(repo_root)

    # Clear embedding store
    EmbeddingStore(str(idx_dir)).clear()

    ix = create_in(str(idx_dir), INDEX_SCHEMA)
    writer = ix.writer()
    if entries is None:
        entries = list_repo_tree(repo_root)

    manifest = Manifest(commit_hash=_get_head_commit(repo_root))
    total_chunks = 0

    for entry in entries:
        file_path = root / entry.path
        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        chunks = chunk_file(content, entry.path)
        sha = hash_file_content(file_path)

        for i, chunk in enumerate(chunks):
            chunk_id = f"{entry.path}:{i}"
            writer.add_document(
                chunk_id=chunk_id,
                file_path=entry.path,
                content=chunk["content"],
                language=chunk["language"],
                start_line=chunk["start_line"],
                end_line=chunk["end_line"],
            )

        manifest.files[entry.path] = FileRecord(sha256=sha, chunk_count=len(chunks))
        total_chunks += len(chunks)

    writer.commit()
    save_manifest(idx_dir, manifest)
    logger.info("Full index complete: %d files, %d chunks", len(entries), total_chunks)
    return len(entries), total_chunks


def _incremental_index(
    repo_root: str, idx_dir: Path, old_manifest: Manifest, entries=None,
) -> tuple[int, int]:
    """Update only changed files in the index."""
    root = Path(repo_root)
    current_hashes = _hash_all_files(repo_root, entries=entries)
    diff = compute_diff(current_hashes, old_manifest)

    if not diff.added and not diff.modified and not diff.deleted:
        logger.info("No changes detected, skipping incremental index")
        return len(old_manifest.files), sum(r.chunk_count for r in old_manifest.files.values())

    logger.info(
        "Incremental index: +%d ~%d -%d",
        len(diff.added), len(diff.modified), len(diff.deleted),
    )

    ix = open_dir(str(idx_dir))
    writer = ix.writer()

    # Remove chunks for modified and deleted files
    for path in diff.modified + diff.deleted:
        old_count = old_manifest.files.get(path, FileRecord("")).chunk_count
        for i in range(old_count):
            writer.delete_by_term("chunk_id", f"{path}:{i}")

    # New manifest
    manifest = Manifest(commit_hash=_get_head_commit(repo_root))
    total_chunks = 0

    # Copy unchanged files to new manifest
    for path in diff.unchanged:
        if path in old_manifest.files:
            manifest.files[path] = old_manifest.files[path]
            total_chunks += old_manifest.files[path].chunk_count

    # Index added and modified files
    for path in diff.added + diff.modified:
        file_path = root / path
        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        chunks = chunk_file(content, path)
        for i, chunk in enumerate(chunks):
            chunk_id = f"{path}:{i}"
            writer.add_document(
                chunk_id=chunk_id,
                file_path=path,
                content=chunk["content"],
                language=chunk["language"],
                start_line=chunk["start_line"],
                end_line=chunk["end_line"],
            )

        manifest.files[path] = FileRecord(
            sha256=current_hashes[path], chunk_count=len(chunks),
        )
        total_chunks += len(chunks)

    # Remove deleted files from manifest (already handled by not copying)
    writer.commit()
    save_manifest(idx_dir, manifest)
    file_count = len(manifest.files)
    logger.info("Incremental index complete: %d files, %d chunks", file_count, total_chunks)
    return file_count, total_chunks


async def generate_embeddings(
    repo_root: str,
    llm_client,
    batch_size: int = 0,
) -> int:
    """Generate embeddings for indexed chunks, skipping unchanged ones.

    Uses a content hash per chunk stored in the embedding index to avoid
    re-embedding chunks whose text has not changed since the last run.

    A producer-consumer pipeline overlaps Ollama compute with disk I/O:
    the producer sends batches to Ollama while the consumer writes
    completed embeddings to the ``EmbeddingStore``.

    Args:
        batch_size: Chunks per Ollama embed call.  ``0`` (default) uses
            adaptive sizing via ``llm_client.compute_embedding_batch_size``.
    """
    import asyncio
    import hashlib

    idx_dir = _index_dir(repo_root)
    if not exists_in(str(idx_dir)):
        return 0

    store = EmbeddingStore(str(idx_dir))
    existing_index = store.get_index()

    ix = open_dir(str(idx_dir))
    reader = ix.reader()

    all_chunks: list[tuple[str, str]] = []
    for doc_num in reader.all_doc_ids():
        stored = reader.stored_fields(doc_num)
        all_chunks.append((stored["chunk_id"], stored["content"]))

    reader.close()

    if not all_chunks:
        return 0

    # Drop orphaned embeddings (chunks deleted from Whoosh).
    current_ids = {cid for cid, _ in all_chunks}
    orphaned = set(existing_index.keys()) - current_ids
    if orphaned:
        store.remove_chunks(orphaned)
        store.compact()
        logger.info("Removed %d orphaned embeddings", len(orphaned))

    # Find chunks needing embedding (new or content changed).
    to_embed: list[tuple[str, str, str]] = []
    for chunk_id, content in all_chunks:
        content_hash = hashlib.sha256(
            content.encode(),
        ).hexdigest()[:16]
        existing = existing_index.get(chunk_id)
        if existing and existing.get("content_hash") == content_hash:
            continue
        to_embed.append((chunk_id, content, content_hash))

    if not to_embed:
        store.flush_index()
        logger.info(
            "Embeddings up to date — %d chunks unchanged",
            len(all_chunks),
        )
        return 0

    # Resolve batch size: explicit > adaptive > fallback.
    if batch_size <= 0:
        batch_size = await llm_client.compute_embedding_batch_size(to_embed)

    total_to_embed = len(to_embed)
    logger.info(
        "Generating embeddings for %d code chunks (batch_size=%d)",
        total_to_embed, batch_size,
    )

    # Producer-consumer: overlap Ollama compute with disk I/O.
    queue: asyncio.Queue = asyncio.Queue(maxsize=2)
    total = 0

    async def _producer():
        for i in range(0, total_to_embed, batch_size):
            batch = to_embed[i : i + batch_size]
            batch_ids = [cid for cid, _, _ in batch]
            batch_texts = [t for _, t, _ in batch]
            batch_hashes = [h for _, _, h in batch]
            try:
                embeddings = await llm_client.embed(batch_texts)
                await queue.put((batch_ids, embeddings, batch_hashes))
            except Exception as e:
                logger.warning(
                    "Embedding batch %d failed: %s", i // batch_size, e,
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
                "Embedding progress: %d/%d code chunks (%.0f%%)",
                total, total_to_embed, (total / total_to_embed) * 100,
            )

    producer_task = asyncio.create_task(_producer())
    await _consumer()
    await producer_task

    store.flush_index()
    logger.info(
        "Generated %d embeddings (%d unchanged, %d orphaned removed)",
        total,
        len(all_chunks) - len(to_embed),
        len(orphaned),
    )
    return total


def search_index(
    repo_root: str,
    query: str,
    limit: int = 20,
    query_embedding: list[float] | None = None,
) -> list[dict]:
    """Search the index using BM25F, optionally with RRF re-ranking."""
    idx_dir = _index_dir(repo_root)
    if not exists_in(str(idx_dir)):
        return []

    ix = open_dir(str(idx_dir))

    with ix.searcher() as searcher:
        parser = MultifieldParser(["content", "file_path"], schema=ix.schema)
        parsed = parser.parse(query)
        results = searcher.search(parsed, limit=limit)

        hits: list[dict] = []
        for hit in results:
            hits.append({
                "chunk_id": hit["chunk_id"],
                "file_path": hit["file_path"],
                "content": hit["content"],
                "language": hit["language"],
                "start_line": hit["start_line"],
                "end_line": hit["end_line"],
                "score": hit.score,
            })

    # Optional RRF re-ranking with embeddings
    if query_embedding and hits:
        store = EmbeddingStore(str(idx_dir))
        hits = semantic_rerank(hits, query_embedding, store)

    return hits

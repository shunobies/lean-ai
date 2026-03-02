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


def _hash_all_files(repo_root: str) -> dict[str, str]:
    """Build {rel_path: sha256} dict for all indexable files."""
    root = Path(repo_root)
    entries = list_repo_tree(repo_root)
    return {e.path: hash_file_content(root / e.path) for e in entries}


def index_workspace(repo_root: str, force: bool = False) -> int:
    """Index the workspace. Returns total chunk count.

    Uses incremental indexing if a valid manifest exists, otherwise full.
    """
    idx_dir = _index_dir(repo_root)
    idx_dir.mkdir(parents=True, exist_ok=True)

    if force or not exists_in(str(idx_dir)):
        return _full_index(repo_root, idx_dir)

    old_manifest = load_manifest(idx_dir)
    if old_manifest is None:
        return _full_index(repo_root, idx_dir)

    return _incremental_index(repo_root, idx_dir, old_manifest)


def _full_index(repo_root: str, idx_dir: Path) -> int:
    """Wipe and rebuild the index from scratch."""
    logger.info("Full index of %s", repo_root)
    root = Path(repo_root)

    # Clear embedding store
    EmbeddingStore(str(idx_dir)).clear()

    ix = create_in(str(idx_dir), INDEX_SCHEMA)
    writer = ix.writer()
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
    return total_chunks


def _incremental_index(
    repo_root: str, idx_dir: Path, old_manifest: Manifest,
) -> int:
    """Update only changed files in the index."""
    root = Path(repo_root)
    current_hashes = _hash_all_files(repo_root)
    diff = compute_diff(current_hashes, old_manifest)

    if not diff.added and not diff.modified and not diff.deleted:
        logger.info("No changes detected, skipping incremental index")
        return sum(r.chunk_count for r in old_manifest.files.values())

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
    logger.info("Incremental index complete: %d total chunks", total_chunks)
    return total_chunks


async def generate_embeddings(
    repo_root: str,
    llm_client,
    batch_size: int = 32,
) -> int:
    """Generate embeddings for all indexed chunks."""
    idx_dir = _index_dir(repo_root)
    if not exists_in(str(idx_dir)):
        return 0

    store = EmbeddingStore(str(idx_dir))
    store.clear()

    ix = open_dir(str(idx_dir))
    reader = ix.reader()

    chunk_ids: list[str] = []
    texts: list[str] = []

    for doc_num in reader.all_doc_ids():
        stored = reader.stored_fields(doc_num)
        chunk_ids.append(stored["chunk_id"])
        texts.append(stored["content"])

    reader.close()

    if not texts:
        return 0

    total = 0
    for i in range(0, len(texts), batch_size):
        batch_ids = chunk_ids[i : i + batch_size]
        batch_texts = texts[i : i + batch_size]

        try:
            embeddings = await llm_client.embed(batch_texts)
            store.save_batch(batch_ids, embeddings)
            total += len(embeddings)
        except Exception as e:
            logger.warning("Embedding batch %d failed: %s", i // batch_size, e)

    store.flush_index()
    logger.info("Generated %d embeddings", total)
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

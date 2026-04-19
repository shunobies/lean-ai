"""Embedding storage and semantic search via Reciprocal Rank Fusion."""

import json
import logging
import math
import struct
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


class EmbeddingStore:
    """Stores embeddings as binary file + JSON sidecar for fast lookup."""

    def __init__(self, index_dir: str):
        self._dir = Path(index_dir)
        self._bin_path = self._dir / ".embeddings.bin"
        self._idx_path = self._dir / ".embeddings_index.json"
        self._lock = threading.Lock()
        self._index: dict[str, dict] | None = None

    def _load_index(self) -> dict[str, dict]:
        if self._index is not None:
            return self._index
        if self._idx_path.exists():
            try:
                self._index = json.loads(self._idx_path.read_text())
                return self._index
            except Exception:
                pass
        self._index = {}
        return self._index

    def get_index(self) -> dict[str, dict]:
        """Return the in-memory index for external diffing."""
        return dict(self._load_index())

    def remove_chunks(self, chunk_ids: set[str]) -> None:
        """Drop entries from the index (binary data left as orphans)."""
        with self._lock:
            index = self._load_index()
            for cid in chunk_ids:
                index.pop(cid, None)

    def save_batch(
        self,
        chunk_ids: list[str],
        embeddings: list[list[float]],
        content_hashes: list[str] | None = None,
    ) -> None:
        """Append a batch of embeddings to storage."""
        if not chunk_ids or not embeddings:
            return

        dim = len(embeddings[0])
        with self._lock:
            index = self._load_index()
            with open(self._bin_path, "ab") as f:
                for i, (chunk_id, vec) in enumerate(
                    zip(chunk_ids, embeddings),
                ):
                    offset = f.tell()
                    f.write(struct.pack(f"{dim}f", *vec))
                    entry: dict = {"offset": offset, "dim": dim}
                    if content_hashes:
                        entry["content_hash"] = content_hashes[i]
                    index[chunk_id] = entry
            self._index = index

    def flush_index(self) -> None:
        """Write the JSON index to disk."""
        with self._lock:
            if self._index:
                self._idx_path.write_text(json.dumps(self._index))

    def get_embedding(self, chunk_id: str) -> list[float] | None:
        """Read a single embedding by chunk ID."""
        index = self._load_index()
        entry = index.get(chunk_id)
        if entry is None:
            return None
        try:
            with open(self._bin_path, "rb") as f:
                f.seek(entry["offset"])
                data = f.read(entry["dim"] * 4)
                return list(struct.unpack(f"{entry['dim']}f", data))
        except Exception:
            return None

    def get_all_embeddings(self) -> dict[str, list[float]]:
        """Load all embeddings into memory."""
        index = self._load_index()
        result: dict[str, list[float]] = {}
        if not index or not self._bin_path.exists():
            return result
        try:
            with open(self._bin_path, "rb") as f:
                for chunk_id, entry in index.items():
                    f.seek(entry["offset"])
                    data = f.read(entry["dim"] * 4)
                    result[chunk_id] = list(struct.unpack(f"{entry['dim']}f", data))
        except Exception as e:
            logger.warning("Failed to load embeddings: %s", e)
        return result

    def compact(self) -> int:
        """Rewrite the binary file, removing orphaned vector data.

        Returns the number of bytes reclaimed.  Safe to call even when
        there are no orphans (returns 0 immediately).

        NOTE: Not called in the normal ``/init`` flow anymore — the
        rewrite is O(total-entries × vector-bytes) and wedged /init on
        slow disks for days. ``remove_chunks`` + ``flush_index`` is
        enough for correctness (orphan metadata is dropped from the
        JSON sidecar), and the stranded binary bytes get reclaimed
        wholesale on ``/init --force`` (which clears the store). This
        method is retained for an explicit maintenance endpoint or
        future manual-cleanup tooling.
        """
        import time

        t0 = time.perf_counter()
        logger.info("[embed store] compact: waiting for lock on %s", self._bin_path)
        with self._lock:
            t_lock = time.perf_counter()
            logger.info(
                "[embed store] compact: lock acquired in %.2fs", t_lock - t0,
            )
            index = self._load_index()
            if not index or not self._bin_path.exists():
                logger.info("[embed store] compact: nothing to do (empty)")
                return 0

            old_size = self._bin_path.stat().st_size
            tmp_path = self._bin_path.with_suffix(".bin.tmp")
            logger.info(
                "[embed store] compact: rewriting %d entries (%d bytes) "
                "→ %s", len(index), old_size, tmp_path,
            )

            try:
                with open(self._bin_path, "rb") as src, open(tmp_path, "wb") as dst:
                    for entry in index.values():
                        src.seek(entry["offset"])
                        data = src.read(entry["dim"] * 4)
                        entry["offset"] = dst.tell()
                        dst.write(data)

                tmp_path.replace(self._bin_path)
            except Exception:
                # Clean up temp file on failure; index offsets may be stale
                # but a subsequent generate_embeddings run will fix them.
                if tmp_path.exists():
                    tmp_path.unlink()
                raise

            new_size = self._bin_path.stat().st_size
            saved = old_size - new_size
            t_done = time.perf_counter()
            if saved > 0:
                self.flush_index()
                logger.info(
                    "[embed store] compact: done in %.2fs, saved %d bytes",
                    t_done - t_lock, saved,
                )
            else:
                logger.info(
                    "[embed store] compact: done in %.2fs, no bytes reclaimed",
                    t_done - t_lock,
                )
            return saved

    def clear(self) -> None:
        """Remove all embedding data."""
        with self._lock:
            self._index = {}
            if self._bin_path.exists():
                self._bin_path.unlink()
            if self._idx_path.exists():
                self._idx_path.unlink()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def semantic_rerank(
    bm25_results: list[dict],
    query_embedding: list[float],
    store: EmbeddingStore,
    k: int = 60,
    w_bm25: float = 1.0,
    w_sem: float = 1.0,
) -> list[dict]:
    """Re-rank BM25 results using Reciprocal Rank Fusion with embeddings."""
    all_embeddings = store.get_all_embeddings()
    if not all_embeddings:
        return bm25_results

    # BM25 ranks (1-indexed)
    bm25_ranks: dict[str, int] = {}
    for rank, r in enumerate(bm25_results, 1):
        bm25_ranks[r["chunk_id"]] = rank

    # Semantic scores for all indexed chunks
    sem_scores: list[tuple[str, float]] = []
    for chunk_id, emb in all_embeddings.items():
        sim = cosine_similarity(query_embedding, emb)
        sem_scores.append((chunk_id, sim))
    sem_scores.sort(key=lambda x: x[1], reverse=True)

    sem_ranks: dict[str, int] = {}
    for rank, (chunk_id, _) in enumerate(sem_scores, 1):
        sem_ranks[chunk_id] = rank

    # RRF fusion
    all_chunk_ids = set(bm25_ranks.keys()) | set(sem_ranks.keys())
    rrf_scores: list[tuple[str, float]] = []
    for chunk_id in all_chunk_ids:
        bm25_rank = bm25_ranks.get(chunk_id, len(bm25_results) + 100)
        sem_rank = sem_ranks.get(chunk_id, len(sem_scores) + 100)
        score = w_bm25 / (k + bm25_rank) + w_sem / (k + sem_rank)
        rrf_scores.append((chunk_id, score))

    rrf_scores.sort(key=lambda x: x[1], reverse=True)

    # Build result list from BM25 results (preserving metadata)
    result_lookup = {r["chunk_id"]: r for r in bm25_results}
    reranked: list[dict] = []
    for chunk_id, score in rrf_scores[: len(bm25_results)]:
        if chunk_id in result_lookup:
            entry = dict(result_lookup[chunk_id])
            entry["rrf_score"] = score
            reranked.append(entry)

    return reranked

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

    def save_batch(
        self, chunk_ids: list[str], embeddings: list[list[float]],
    ) -> None:
        """Append a batch of embeddings to storage."""
        if not chunk_ids or not embeddings:
            return

        dim = len(embeddings[0])
        with self._lock:
            index = self._load_index()
            with open(self._bin_path, "ab") as f:
                for chunk_id, vec in zip(chunk_ids, embeddings):
                    offset = f.tell()
                    f.write(struct.pack(f"{dim}f", *vec))
                    index[chunk_id] = {"offset": offset, "dim": dim}
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

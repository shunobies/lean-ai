"""Tests for embedding store persistence edge cases."""

import json
import threading

import pytest

from lean_ai.indexer.embeddings import EmbeddingStore


def test_save_batch_rejects_mismatched_lengths(tmp_path):
    store = EmbeddingStore(str(tmp_path))

    with pytest.raises(ValueError, match="same length"):
        store.save_batch(["a", "b"], [[1.0, 2.0]])

    assert not (tmp_path / ".embeddings.bin").exists()


def test_flush_index_persists_empty_index_after_removal(tmp_path):
    store = EmbeddingStore(str(tmp_path))
    store.save_batch(["a"], [[1.0, 2.0]], ["hash-a"])
    store.remove_chunks({"a"})
    store.flush_index()

    assert json.loads((tmp_path / ".embeddings_index.json").read_text()) == {}


def test_compact_rewrites_orphans_without_deadlock(tmp_path):
    store = EmbeddingStore(str(tmp_path))
    store.save_batch(["a", "b"], [[1.0, 2.0], [3.0, 4.0]])
    store.flush_index()
    store.remove_chunks({"a"})

    result: dict[str, int] = {}

    def _compact() -> None:
        result["saved"] = store.compact()

    thread = threading.Thread(target=_compact, daemon=True)
    thread.start()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert result["saved"] > 0
    assert set(json.loads((tmp_path / ".embeddings_index.json").read_text())) == {"b"}

"""Tests for neighbor expansion in reference library search."""

import os

import pytest
from whoosh.index import create_in

from lean_ai.reference.indexer import (
    REFERENCE_SCHEMA,
    _expand_with_neighbors,
    is_chunk_config_stale,
    list_documents,
    requires_reference_rebuild,
    search_reference,
)


@pytest.fixture
def reference_index(tmp_path, monkeypatch):
    """Build a tiny Whoosh reference index at a temp path.

    Two documents:
      - ``book.epub`` with 10 chunks (chunk_index 0–9), each tagged with
        its own section to make it easy to verify the merged passage
        picked up neighbors.
      - ``manual.pdf`` with 5 chunks (chunk_index 0–4).
    """
    repo_root = tmp_path
    idx_dirname = "reference_index_test"
    monkeypatch.setattr(
        "lean_ai.config.settings.reference_index_dir",
        idx_dirname,
        raising=False,
    )
    idx_path = os.path.join(str(repo_root), idx_dirname)
    os.makedirs(idx_path, exist_ok=True)

    ix = create_in(idx_path, REFERENCE_SCHEMA)
    writer = ix.writer()

    for i in range(10):
        writer.add_document(
            chunk_id=f"book.epub:{i}",
            doc_path="book.epub",
            doc_title="My Book",
            section=f"Chapter {i // 3 + 1}",
            content=f"BookChunk{i} content about topic {i} keyword_{'alpha' if i == 4 else 'beta'}",
            format="epub",
            chunk_index=i,
        )

    for i in range(5):
        writer.add_document(
            chunk_id=f"manual.pdf:{i}",
            doc_path="manual.pdf",
            doc_title="Manual",
            section=f"Page {i + 1}",
            content=f"ManualChunk{i} content covering manual_topic_{i}",
            format="pdf",
            chunk_index=i,
        )

    writer.commit()
    return str(repo_root), idx_path


class TestNeighborExpansion:
    def test_window_zero_returns_point_hits(self, reference_index, monkeypatch):
        repo_root, _ = reference_index
        monkeypatch.setattr(
            "lean_ai.config.settings.reference_neighbor_window",
            0,
            raising=False,
        )
        results = search_reference(repo_root, "keyword_alpha", limit=5)
        assert results
        # No expansion: result should look like a raw hit dict, not a passage.
        assert "chunk_index" in results[0]
        assert "chunk_index_start" not in results[0]

    def test_single_hit_expands_to_2w_plus_1(self, reference_index, monkeypatch):
        repo_root, _ = reference_index
        monkeypatch.setattr(
            "lean_ai.config.settings.reference_neighbor_window",
            2,
            raising=False,
        )
        results = search_reference(repo_root, "keyword_alpha", limit=5)
        assert len(results) == 1
        passage = results[0]
        assert passage["doc_path"] == "book.epub"
        assert passage["chunk_index_start"] == 2  # 4 - 2
        assert passage["chunk_index_end"] == 6  # 4 + 2
        # The joined content must contain the hit chunk and its neighbors.
        for i in range(2, 7):
            assert f"BookChunk{i}" in passage["content"]

    def test_start_index_clamped_at_zero(self, reference_index, monkeypatch):
        """A hit at chunk 0 must not produce a negative start index."""
        repo_root, idx_path = reference_index
        # Manually add a hit at index 0 with a unique keyword
        ix = __import__("whoosh.index", fromlist=["open_dir"]).open_dir(idx_path)
        writer = ix.writer()
        writer.delete_by_term("chunk_id", "book.epub:0")
        writer.add_document(
            chunk_id="book.epub:0",
            doc_path="book.epub",
            doc_title="My Book",
            section="Chapter 1",
            content="BookChunk0 unique_edge_marker",
            format="epub",
            chunk_index=0,
        )
        writer.commit()

        monkeypatch.setattr(
            "lean_ai.config.settings.reference_neighbor_window",
            3,
            raising=False,
        )
        results = search_reference(repo_root, "unique_edge_marker", limit=5)
        assert len(results) == 1
        assert results[0]["chunk_index_start"] == 0
        assert results[0]["chunk_index_end"] == 3

    def test_contiguous_hits_merged(self, reference_index, monkeypatch):
        """Two hits close enough that their windows overlap merge to one passage."""
        repo_root, idx_path = reference_index
        # Mark chunks 3 and 5 with the same query token so both rank.
        ix = __import__("whoosh.index", fromlist=["open_dir"]).open_dir(idx_path)
        writer = ix.writer()
        for i in (3, 5):
            writer.delete_by_term("chunk_id", f"book.epub:{i}")
            writer.add_document(
                chunk_id=f"book.epub:{i}",
                doc_path="book.epub",
                doc_title="My Book",
                section=f"Chapter {i // 3 + 1}",
                content=f"BookChunk{i} joint_merge_keyword",
                format="epub",
                chunk_index=i,
            )
        writer.commit()

        monkeypatch.setattr(
            "lean_ai.config.settings.reference_neighbor_window",
            2,
            raising=False,
        )
        results = search_reference(repo_root, "joint_merge_keyword", limit=5)
        # Windows [1..5] and [3..7] overlap → single merged passage 1..7.
        assert len(results) == 1
        passage = results[0]
        assert passage["chunk_index_start"] == 1
        assert passage["chunk_index_end"] == 7
        # Both original hit indices should be recorded.
        assert 3 in passage["hit_chunk_indices"]
        assert 5 in passage["hit_chunk_indices"]

    def test_separate_docs_produce_separate_passages(self, reference_index, monkeypatch):
        repo_root, idx_path = reference_index
        # Plant the same keyword in one book chunk and one manual chunk.
        ix = __import__("whoosh.index", fromlist=["open_dir"]).open_dir(idx_path)
        writer = ix.writer()
        writer.delete_by_term("chunk_id", "book.epub:2")
        writer.add_document(
            chunk_id="book.epub:2",
            doc_path="book.epub",
            doc_title="My Book",
            section="Chapter 1",
            content="BookChunk2 cross_doc_token",
            format="epub",
            chunk_index=2,
        )
        writer.delete_by_term("chunk_id", "manual.pdf:2")
        writer.add_document(
            chunk_id="manual.pdf:2",
            doc_path="manual.pdf",
            doc_title="Manual",
            section="Page 3",
            content="ManualChunk2 cross_doc_token",
            format="pdf",
            chunk_index=2,
        )
        writer.commit()

        monkeypatch.setattr(
            "lean_ai.config.settings.reference_neighbor_window",
            1,
            raising=False,
        )
        results = search_reference(repo_root, "cross_doc_token", limit=5)
        assert len(results) == 2
        doc_paths = {r["doc_path"] for r in results}
        assert doc_paths == {"book.epub", "manual.pdf"}

    def test_expand_false_bypasses_merging(self, reference_index, monkeypatch):
        repo_root, _ = reference_index
        monkeypatch.setattr(
            "lean_ai.config.settings.reference_neighbor_window",
            2,
            raising=False,
        )
        results = search_reference(
            repo_root,
            "keyword_alpha",
            limit=5,
            expand=False,
        )
        assert results
        assert "chunk_index" in results[0]
        assert "chunk_index_start" not in results[0]

    def test_expand_helper_direct(self, reference_index, monkeypatch):
        """Direct unit check of the merge algorithm bypassing search."""
        _, idx_path = reference_index
        monkeypatch.setattr(
            "lean_ai.config.settings.reference_neighbor_window",
            2,
            raising=False,
        )
        fake_hits = [
            {
                "chunk_id": "book.epub:4",
                "doc_path": "book.epub",
                "doc_title": "My Book",
                "section": "Chapter 2",
                "content": "...",
                "format": "epub",
                "chunk_index": 4,
                "score": 1.5,
            },
        ]
        passages = _expand_with_neighbors(idx_path, fake_hits, window=2)
        assert len(passages) == 1
        assert passages[0]["chunk_index_start"] == 2
        assert passages[0]["chunk_index_end"] == 6
        assert passages[0]["score"] == pytest.approx(1.5)


class TestListDocuments:
    def test_lists_each_indexed_document_once(self, reference_index):
        repo_root, _ = reference_index
        docs = list_documents(repo_root)
        assert len(docs) == 2
        by_path = {d["doc_path"]: d for d in docs}
        assert by_path["book.epub"]["doc_title"] == "My Book"
        assert by_path["book.epub"]["format"] == "epub"
        assert by_path["book.epub"]["chunk_count"] == 10
        assert by_path["manual.pdf"]["doc_title"] == "Manual"
        assert by_path["manual.pdf"]["format"] == "pdf"
        assert by_path["manual.pdf"]["chunk_count"] == 5

    def test_sorted_by_title(self, reference_index):
        repo_root, _ = reference_index
        titles = [d["doc_title"] for d in list_documents(repo_root)]
        assert titles == sorted(titles, key=str.lower)

    def test_name_filter_substring_case_insensitive(self, reference_index):
        repo_root, _ = reference_index
        docs = list_documents(repo_root, name_filter="MAN")
        assert [d["doc_path"] for d in docs] == ["manual.pdf"]

    def test_name_filter_no_match_returns_empty(self, reference_index):
        repo_root, _ = reference_index
        assert list_documents(repo_root, name_filter="nonexistent") == []

    def test_no_index_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "lean_ai.config.settings.reference_index_dir",
            "missing_index",
            raising=False,
        )
        assert list_documents(str(tmp_path)) == []


class TestDocumentFilteredSearch:
    """Search restricted to a single document via the ``document`` parameter."""

    def test_filter_by_exact_doc_path(self, reference_index, monkeypatch):
        """Only chunks from the specified doc_path are returned."""
        repo_root, idx_path = reference_index
        # Plant the same keyword in both documents.
        ix = __import__("whoosh.index", fromlist=["open_dir"]).open_dir(idx_path)
        writer = ix.writer()
        writer.delete_by_term("chunk_id", "book.epub:7")
        writer.add_document(
            chunk_id="book.epub:7",
            doc_path="book.epub",
            doc_title="My Book",
            section="Chapter 3",
            content="BookChunk7 shared_filter_token",
            format="epub",
            chunk_index=7,
        )
        writer.delete_by_term("chunk_id", "manual.pdf:1")
        writer.add_document(
            chunk_id="manual.pdf:1",
            doc_path="manual.pdf",
            doc_title="Manual",
            section="Page 2",
            content="ManualChunk1 shared_filter_token",
            format="pdf",
            chunk_index=1,
        )
        writer.commit()

        monkeypatch.setattr(
            "lean_ai.config.settings.reference_neighbor_window",
            0,
            raising=False,
        )

        # Without filter — both docs hit.
        unfiltered = search_reference(repo_root, "shared_filter_token", limit=5)
        assert {r["doc_path"] for r in unfiltered} == {"book.epub", "manual.pdf"}

        # With doc_path filter — only the manual.
        filtered = search_reference(
            repo_root,
            "shared_filter_token",
            limit=5,
            document="manual.pdf",
        )
        assert filtered
        assert {r["doc_path"] for r in filtered} == {"manual.pdf"}

    def test_filter_by_doc_title_substring(self, reference_index, monkeypatch):
        """A title fragment from prior results restricts the search."""
        repo_root, idx_path = reference_index
        ix = __import__("whoosh.index", fromlist=["open_dir"]).open_dir(idx_path)
        writer = ix.writer()
        writer.delete_by_term("chunk_id", "book.epub:1")
        writer.add_document(
            chunk_id="book.epub:1",
            doc_path="book.epub",
            doc_title="My Book",
            section="Chapter 1",
            content="BookChunk1 title_filter_token",
            format="epub",
            chunk_index=1,
        )
        writer.delete_by_term("chunk_id", "manual.pdf:0")
        writer.add_document(
            chunk_id="manual.pdf:0",
            doc_path="manual.pdf",
            doc_title="Manual",
            section="Page 1",
            content="ManualChunk0 title_filter_token",
            format="pdf",
            chunk_index=0,
        )
        writer.commit()

        monkeypatch.setattr(
            "lean_ai.config.settings.reference_neighbor_window",
            0,
            raising=False,
        )

        # Title substring — "Book" matches "My Book".
        filtered = search_reference(
            repo_root,
            "title_filter_token",
            limit=5,
            document="Book",
        )
        assert filtered
        assert {r["doc_path"] for r in filtered} == {"book.epub"}

    def test_filter_no_match_returns_empty(self, reference_index, monkeypatch):
        """Filter that matches no document yields empty results, not all hits."""
        repo_root, _ = reference_index
        monkeypatch.setattr(
            "lean_ai.config.settings.reference_neighbor_window",
            0,
            raising=False,
        )
        # 'keyword_alpha' would normally hit; restrict to a doc that doesn't exist.
        results = search_reference(
            repo_root,
            "keyword_alpha",
            limit=5,
            document="nonexistent.pdf",
        )
        assert results == []


class TestChunkConfigSentinel:
    def test_no_index_is_not_stale(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "lean_ai.config.settings.reference_index_dir",
            "nope_index",
            raising=False,
        )
        assert is_chunk_config_stale(str(tmp_path)) is False
        assert requires_reference_rebuild(str(tmp_path))["stale"] is False

    def test_legacy_index_without_sentinel_is_stale(self, reference_index, monkeypatch):
        """An index built before this feature existed has no sentinel — treat as stale."""
        repo_root, idx_path = reference_index
        sentinel = os.path.join(idx_path, "chunk_config.json")
        if os.path.exists(sentinel):
            os.remove(sentinel)
        assert is_chunk_config_stale(repo_root) is True
        info = requires_reference_rebuild(repo_root)
        assert info["stale"] is True
        assert info["reason"] == "missing_sentinel"

    def test_mismatched_sentinel_is_stale(self, reference_index, monkeypatch, tmp_path):
        repo_root, idx_path = reference_index
        sentinel = os.path.join(idx_path, "chunk_config.json")
        with open(sentinel, "w") as f:
            f.write('{"reference_chunk_chars": 9999, "schema_version": 1}')

        monkeypatch.setattr(
            "lean_ai.config.settings.reference_chunk_chars",
            1800,
            raising=False,
        )
        assert is_chunk_config_stale(repo_root) is True
        assert requires_reference_rebuild(repo_root)["reason"] == "config_changed"

    def test_matching_sentinel_is_fresh(self, reference_index, monkeypatch):
        repo_root, idx_path = reference_index
        sentinel = os.path.join(idx_path, "chunk_config.json")
        monkeypatch.setattr(
            "lean_ai.config.settings.reference_chunk_chars",
            1800,
            raising=False,
        )
        with open(sentinel, "w") as f:
            f.write('{"reference_chunk_chars": 1800, "schema_version": 1}')
        assert is_chunk_config_stale(repo_root) is False
        assert requires_reference_rebuild(repo_root)["stale"] is False

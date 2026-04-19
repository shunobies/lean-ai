"""Tests for the prose chunker used by the reference library readers."""

from lean_ai.reference.chunker import chunk_prose, chunk_prose_configured


def _make_paragraphs(n: int, chars: int) -> str:
    """Build *n* paragraphs of roughly *chars* characters each, blank-line separated."""
    sentence = "The quick brown fox jumps over the lazy dog. " * 5  # ~225 chars
    para = (sentence * ((chars // len(sentence)) + 1))[:chars]
    return "\n\n".join(f"Paragraph {i}: {para}" for i in range(n))


class TestChunkProse:
    def test_empty_text_returns_empty_list(self):
        assert chunk_prose("") == []
        assert chunk_prose("   \n\n   ") == []

    def test_small_text_single_chunk(self):
        text = "Just one short paragraph."
        chunks = chunk_prose(text, target_chars=800, overlap_chars=150)
        assert len(chunks) == 1
        assert "Just one short paragraph." in chunks[0]

    def test_respects_larger_target(self):
        """With a 1800-char target the chunker must produce chunks clearly larger
        than the old 800-char default."""
        text = _make_paragraphs(40, 400)  # ~16k characters
        chunks = chunk_prose(text, target_chars=1800, overlap_chars=300)
        # Every full chunk should be notably larger than the old 800-char size;
        # the final chunk may be smaller as it holds the leftover tail.
        assert len(chunks) > 1
        full_sizes = [len(c) for c in chunks[:-1]]
        assert all(size > 800 for size in full_sizes), full_sizes
        # Average is near the target (within a paragraph of slack).
        assert sum(full_sizes) / len(full_sizes) >= 1500

    def test_overlap_preserved_between_consecutive_chunks(self):
        # Paragraphs must fit inside the overlap budget to be carried over.
        text = _make_paragraphs(40, 200)
        chunks = chunk_prose(text, target_chars=1800, overlap_chars=600)
        assert len(chunks) >= 2
        # The last paragraph of chunk N should reappear at the start of chunk N+1.
        first_end = chunks[0].rsplit("\n\n", 1)[-1]
        assert first_end in chunks[1]

    def test_single_giant_paragraph_is_line_split(self):
        giant = "\n".join(f"line {i} content " * 20 for i in range(200))
        chunks = chunk_prose(giant, target_chars=800, overlap_chars=150)
        assert len(chunks) > 1
        assert all(len(c) < 2000 for c in chunks)

    def test_scales_overlap_down_when_requested(self):
        text = _make_paragraphs(20, 300)
        no_overlap = chunk_prose(text, target_chars=1200, overlap_chars=0)
        with_overlap = chunk_prose(text, target_chars=1200, overlap_chars=400)
        # More overlap → more total characters across chunks.
        assert sum(len(c) for c in with_overlap) >= sum(len(c) for c in no_overlap)


class TestChunkProseConfigured:
    def test_reads_settings(self, monkeypatch):
        from lean_ai.config import settings

        text = _make_paragraphs(30, 400)

        monkeypatch.setattr(settings, "reference_chunk_chars", 600, raising=False)
        small = chunk_prose_configured(text)

        monkeypatch.setattr(settings, "reference_chunk_chars", 2400, raising=False)
        big = chunk_prose_configured(text)

        assert len(small) > len(big)

    def test_rejects_absurdly_small_target(self, monkeypatch):
        """Values below 100 are clamped so we don't produce one-word chunks."""
        from lean_ai.config import settings

        monkeypatch.setattr(settings, "reference_chunk_chars", 10, raising=False)
        chunks = chunk_prose_configured(_make_paragraphs(5, 300))
        # Floor of 100 chars means chunks are real paragraphs, not tokens.
        assert all(len(c) >= 50 for c in chunks)

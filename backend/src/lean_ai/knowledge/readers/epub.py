"""EPUB document reader.

Optional dependency: ``ebooklib>=0.18`` and ``beautifulsoup4`` (already
required).  Install with:

    pip install "lean-ai[knowledge]"

or manually:

    pip install ebooklib

Each chapter (EPUB document item) becomes one or more chunks.
The chapter's first heading is used as the section title.
"""

import logging
from pathlib import Path

from lean_ai.knowledge.chunker import chunk_prose
from lean_ai.knowledge.readers.base import DocumentReader, KnowledgeChunk

logger = logging.getLogger(__name__)


class EpubReader(DocumentReader):
    """Reader for ``.epub`` files (requires ``ebooklib``)."""

    @property
    def extensions(self) -> list[str]:
        return [".epub"]

    def read(self, path: Path, rel_path: str) -> list[KnowledgeChunk]:
        try:
            import ebooklib
            from bs4 import BeautifulSoup
            from ebooklib import epub
        except ImportError as e:
            logger.warning(
                "Cannot read EPUB %s — missing optional dependency: %s. "
                "Install with: pip install ebooklib",
                path, e,
            )
            return []

        try:
            book = epub.read_epub(str(path), options={"ignore_ncx": True})
        except Exception as e:
            logger.warning("Failed to open EPUB %s: %s", path, e)
            return []

        # Book title from metadata.
        doc_title = path.stem.replace("_", " ").replace("-", " ")
        try:
            titles = book.get_metadata("DC", "title")
            if titles:
                doc_title = titles[0][0]
        except Exception:
            pass

        chunks: list[KnowledgeChunk] = []
        chunk_index = 0

        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            try:
                raw_html = item.get_content().decode("utf-8", errors="replace")
            except Exception:
                continue

            soup = BeautifulSoup(raw_html, "html.parser")

            # Remove navigation noise.
            for tag in soup(["script", "style", "nav"]):
                tag.decompose()

            # Determine chapter title from the first heading.
            section_title = ""
            for heading in soup.find_all(["h1", "h2", "h3"]):
                candidate = heading.get_text(separator=" ", strip=True)
                if candidate:
                    section_title = candidate
                    break

            text = soup.get_text(separator="\n", strip=True)
            if not text.strip():
                continue

            raw_chunks = chunk_prose(text)
            for chunk_text in raw_chunks:
                if not chunk_text.strip():
                    continue
                chunks.append(KnowledgeChunk(
                    doc_path=rel_path,
                    doc_title=doc_title,
                    section=section_title,
                    content=chunk_text,
                    chunk_index=chunk_index,
                    format="epub",
                ))
                chunk_index += 1

        return chunks

"""PDF document reader.

Optional dependency: ``pypdf>=6.7.5``.  Install with:

    pip install "lean-ai[reference]"

or manually:

    pip install pypdf

Each page becomes a section ("Page N").  Pages with no extractable text
(e.g. scanned images without OCR) are skipped silently.
"""

import logging
from pathlib import Path

from lean_ai.reference.chunker import chunk_prose_configured
from lean_ai.reference.readers.base import DocumentReader, ReferenceChunk

logger = logging.getLogger(__name__)


class PdfReader(DocumentReader):
    """Reader for ``.pdf`` files (requires ``pypdf``)."""

    @property
    def extensions(self) -> list[str]:
        return [".pdf"]

    def read(self, path: Path, rel_path: str) -> list[ReferenceChunk]:
        try:
            from pypdf import PdfReader as _PdfReader
        except ImportError as e:
            logger.warning(
                "Cannot read PDF %s — missing optional dependency: %s. "
                "Install with: pip install pypdf",
                path,
                e,
            )
            return []

        try:
            reader = _PdfReader(str(path))
        except Exception as e:
            logger.warning("Failed to open PDF %s: %s", path, e)
            return []

        # Document title from metadata.
        doc_title = path.stem.replace("_", " ").replace("-", " ")
        try:
            meta = reader.metadata
            if meta and meta.title:
                doc_title = meta.title.strip() or doc_title
        except Exception:
            pass

        chunks: list[ReferenceChunk] = []
        chunk_index = 0

        for page_num, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as e:
                logger.debug("Failed to extract text from page %d of %s: %s", page_num, path, e)
                continue

            if not text.strip():
                continue

            section = f"Page {page_num}"
            raw_chunks = chunk_prose_configured(text)
            for chunk_text in raw_chunks:
                if not chunk_text.strip():
                    continue
                chunks.append(
                    ReferenceChunk(
                        doc_path=rel_path,
                        doc_title=doc_title,
                        section=section,
                        content=chunk_text,
                        chunk_index=chunk_index,
                        format="pdf",
                    )
                )
                chunk_index += 1

        return chunks

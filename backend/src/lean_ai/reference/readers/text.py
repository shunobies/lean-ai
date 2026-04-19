"""Plain-text and reStructuredText reader.

No external dependencies required.  Treats the entire file as prose,
using the filename (without extension) as the document title and
splitting on blank lines for paragraph-aware chunking.
"""

import logging
from pathlib import Path

from lean_ai.reference.chunker import chunk_prose_configured
from lean_ai.reference.readers.base import DocumentReader, ReferenceChunk

logger = logging.getLogger(__name__)


class TextReader(DocumentReader):
    """Reader for ``.txt`` and ``.rst`` plain-text files."""

    @property
    def extensions(self) -> list[str]:
        return [".txt", ".rst"]

    def read(self, path: Path, rel_path: str) -> list[ReferenceChunk]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError) as e:
            logger.warning("Cannot read text file %s: %s", path, e)
            return []

        if not text.strip():
            return []

        doc_title = path.stem.replace("_", " ").replace("-", " ")
        fmt = "rst" if path.suffix.lower() == ".rst" else "txt"

        # Treat the whole file as a single section.
        raw_chunks = chunk_prose_configured(text)
        return [
            ReferenceChunk(
                doc_path=rel_path,
                doc_title=doc_title,
                section="",
                content=chunk_text,
                chunk_index=i,
                format=fmt,
            )
            for i, chunk_text in enumerate(raw_chunks)
        ]

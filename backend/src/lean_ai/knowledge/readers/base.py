"""Abstract base class for knowledge document readers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class KnowledgeChunk:
    """A single searchable chunk extracted from a knowledge document."""

    doc_path: str     # relative path within the knowledge directory
    doc_title: str    # human-readable document title
    section: str      # chapter name, heading text, or "Page N"
    content: str      # plain-text content of this chunk
    chunk_index: int  # sequential index within the document
    format: str       # "epub" | "pdf" | "docx" | "md" | "html" | "txt" | "rst"


class DocumentReader(ABC):
    """Base class for all knowledge document readers.

    Subclasses declare which file extensions they handle and implement
    ``read()`` to extract :class:`KnowledgeChunk` objects from a file.
    Optional dependencies (ebooklib, pypdf, python-docx) should be
    imported inside ``read()`` so their absence only breaks that reader,
    not the whole system.
    """

    @property
    @abstractmethod
    def extensions(self) -> list[str]:
        """Lowercase file extensions handled by this reader (with leading dot).

        Example: ``[".epub"]``
        """

    def can_read(self, path: Path) -> bool:
        """Return ``True`` when this reader handles *path*'s extension."""
        return path.suffix.lower() in self.extensions

    @abstractmethod
    def read(self, path: Path, rel_path: str) -> list[KnowledgeChunk]:
        """Extract chunks from a knowledge document.

        Args:
            path: Absolute filesystem path to the document.
            rel_path: Path relative to the knowledge directory root.
                      Used as the ``doc_path`` on returned chunks.

        Returns:
            A list of :class:`KnowledgeChunk` objects — empty if the
            file cannot be read or contains no extractable text.
        """

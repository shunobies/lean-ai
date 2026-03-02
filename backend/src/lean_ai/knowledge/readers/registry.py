"""Reader registry — maps file extensions to document readers.

Readers whose optional dependencies are not installed are silently skipped
so the rest of the system continues to work.  Call
:func:`supported_extensions` to see which formats are available at runtime.

Adding a new reader
~~~~~~~~~~~~~~~~~~~
1. Create ``readers/my_format.py`` implementing :class:`DocumentReader`.
2. Add a ``_try_register`` call below — wrap in ``try/except ImportError``
   if the reader has optional dependencies.
"""

import logging
from pathlib import Path

from lean_ai.knowledge.readers.base import DocumentReader, KnowledgeChunk

logger = logging.getLogger(__name__)

# Ordered list of registered readers.  First match wins.
_readers: list[DocumentReader] = []


def _try_register() -> None:
    """Register readers, skipping those with missing optional deps."""
    # Always available (no optional deps beyond beautifulsoup4).
    from lean_ai.knowledge.readers.html import HtmlReader
    from lean_ai.knowledge.readers.markdown import MarkdownReader
    from lean_ai.knowledge.readers.text import TextReader

    _readers.extend([MarkdownReader(), HtmlReader(), TextReader()])

    # EPUB — requires ebooklib.
    try:
        import ebooklib  # noqa: F401

        from lean_ai.knowledge.readers.epub import EpubReader
        _readers.append(EpubReader())
        logger.debug("Knowledge: EPUB reader registered (ebooklib available)")
    except ImportError:
        logger.debug(
            "Knowledge: EPUB reader not registered — install ebooklib: "
            "pip install ebooklib"
        )

    # PDF — requires pypdf.
    try:
        import pypdf  # noqa: F401

        from lean_ai.knowledge.readers.pdf import PdfReader
        _readers.append(PdfReader())
        logger.debug("Knowledge: PDF reader registered (pypdf available)")
    except ImportError:
        logger.debug(
            "Knowledge: PDF reader not registered — install pypdf: "
            "pip install pypdf"
        )

    # Word — requires python-docx.
    try:
        import docx  # noqa: F401

        from lean_ai.knowledge.readers.docx import DocxReader
        _readers.append(DocxReader())
        logger.debug("Knowledge: DOCX reader registered (python-docx available)")
    except ImportError:
        logger.debug(
            "Knowledge: DOCX reader not registered — install python-docx: "
            "pip install python-docx"
        )


_try_register()


def get_reader(path: Path) -> DocumentReader | None:
    """Return the first reader that handles *path*'s extension, or ``None``."""
    for reader in _readers:
        if reader.can_read(path):
            return reader
    return None


def supported_extensions() -> list[str]:
    """Return sorted list of all file extensions supported at runtime."""
    exts: set[str] = set()
    for reader in _readers:
        exts.update(reader.extensions)
    return sorted(exts)


def read_document(path: Path, rel_path: str) -> list[KnowledgeChunk]:
    """Read a document using the appropriate registered reader.

    Returns an empty list when no reader handles *path*'s extension or
    when reading fails.  Errors are logged at WARNING level.
    """
    reader = get_reader(path)
    if reader is None:
        logger.debug(
            "No reader for extension %s (supported: %s)",
            path.suffix, supported_extensions(),
        )
        return []

    try:
        return reader.read(path, rel_path)
    except Exception as e:
        logger.warning("Reader %s failed on %s: %s", type(reader).__name__, path, e)
        return []

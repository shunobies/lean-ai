"""Markdown document reader.

No external dependencies required.  Splits the document on ATX headings
(``#``, ``##``, etc.) to preserve section structure, then applies
paragraph-aware chunking within each section.
"""

import logging
from pathlib import Path

from lean_ai.knowledge.chunker import chunk_prose
from lean_ai.knowledge.readers.base import DocumentReader, KnowledgeChunk

logger = logging.getLogger(__name__)


class MarkdownReader(DocumentReader):
    """Reader for ``.md`` and ``.markdown`` files."""

    @property
    def extensions(self) -> list[str]:
        return [".md", ".markdown"]

    def read(self, path: Path, rel_path: str) -> list[KnowledgeChunk]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError) as e:
            logger.warning("Cannot read markdown file %s: %s", path, e)
            return []

        if not text.strip():
            return []

        doc_title = path.stem.replace("_", " ").replace("-", " ")

        # If the first heading is an H1, use it as the document title.
        for line in text.strip().splitlines():
            stripped = line.strip()
            if stripped.startswith("# ") and not stripped.startswith("## "):
                doc_title = stripped[2:].strip()
                break

        sections = _split_by_headings(text)
        chunks: list[KnowledgeChunk] = []
        chunk_index = 0

        for section_title, section_text in sections:
            raw_chunks = chunk_prose(section_text)
            for chunk_text in raw_chunks:
                chunks.append(KnowledgeChunk(
                    doc_path=rel_path,
                    doc_title=doc_title,
                    section=section_title,
                    content=chunk_text,
                    chunk_index=chunk_index,
                    format="md",
                ))
                chunk_index += 1

        return chunks


def _is_atx_heading(line: str) -> tuple[int, str] | None:
    """Check if a line is an ATX heading (# to ######).

    Returns (level, heading_text) or None.
    """
    stripped = line.strip()
    if not stripped.startswith("#"):
        return None

    # Count leading # characters
    level = 0
    for ch in stripped:
        if ch == "#":
            level += 1
        else:
            break

    if level > 6:
        return None

    # Must be followed by a space (or be just "#"s at end of line)
    rest = stripped[level:]
    if not rest:
        return None
    if rest[0] != " ":
        return None

    heading_text = rest.strip()
    if not heading_text:
        return None

    return level, heading_text


def _split_by_headings(text: str) -> list[tuple[str, str]]:
    """Split a Markdown document into (heading_title, body_text) sections.

    The text before the first heading is returned as a section with an
    empty title.  Consecutive headings without body text are merged into
    the following section.
    """
    lines = text.splitlines()

    # Find all heading positions: (line_index, heading_text)
    heading_positions: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        parsed = _is_atx_heading(line)
        if parsed is not None:
            _, heading_text = parsed
            heading_positions.append((i, heading_text))

    if not heading_positions:
        # No headings — single section with the full text.
        return [("", text.strip())]

    sections: list[tuple[str, str]] = []

    # Text before the first heading.
    first_heading_line = heading_positions[0][0]
    preamble = "\n".join(lines[:first_heading_line]).strip()
    if preamble:
        sections.append(("", preamble))

    for idx, (line_num, heading) in enumerate(heading_positions):
        # Body extends from the line after this heading to the line before
        # the next heading (or end of file).
        body_start = line_num + 1
        if idx + 1 < len(heading_positions):
            body_end = heading_positions[idx + 1][0]
        else:
            body_end = len(lines)

        body = "\n".join(lines[body_start:body_end]).strip()
        sections.append((heading, body))

    return sections

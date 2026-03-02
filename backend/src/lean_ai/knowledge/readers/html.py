"""HTML document reader.

Uses BeautifulSoup4 (already a required dependency) to extract readable
text from HTML files — useful for saved web pages, wiki HTML exports,
Confluence page dumps, etc.

Splits on ``<h1>``–``<h4>`` elements to preserve section structure.
"""

import logging
from pathlib import Path

from lean_ai.knowledge.chunker import chunk_prose
from lean_ai.knowledge.readers.base import DocumentReader, KnowledgeChunk

logger = logging.getLogger(__name__)


class HtmlReader(DocumentReader):
    """Reader for ``.html`` and ``.htm`` files."""

    @property
    def extensions(self) -> list[str]:
        return [".html", ".htm"]

    def read(self, path: Path, rel_path: str) -> list[KnowledgeChunk]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logger.warning("beautifulsoup4 not available; skipping %s", path)
            return []

        try:
            raw = path.read_bytes()
        except (OSError, PermissionError) as e:
            logger.warning("Cannot read HTML file %s: %s", path, e)
            return []

        soup = BeautifulSoup(raw, "html.parser")

        # Remove script/style noise.
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        # Document title from <title> or first <h1>.
        doc_title = path.stem.replace("_", " ").replace("-", " ")
        title_tag = soup.find("title")
        if title_tag and title_tag.get_text(strip=True):
            doc_title = title_tag.get_text(strip=True)
        else:
            h1 = soup.find("h1")
            if h1 and h1.get_text(strip=True):
                doc_title = h1.get_text(strip=True)

        # Find the main content area (prefer semantic tags).
        content_root = (
            soup.find("article")
            or soup.find("main")
            or soup.find(id="content")
            or soup.find(class_="content")
            or soup.find("body")
            or soup
        )

        sections = _split_html_by_headings(content_root)
        chunks: list[KnowledgeChunk] = []
        chunk_index = 0

        for section_title, section_text in sections:
            raw_chunks = chunk_prose(section_text)
            for chunk_text in raw_chunks:
                if not chunk_text.strip():
                    continue
                chunks.append(KnowledgeChunk(
                    doc_path=rel_path,
                    doc_title=doc_title,
                    section=section_title,
                    content=chunk_text,
                    chunk_index=chunk_index,
                    format="html",
                ))
                chunk_index += 1

        return chunks


def _split_html_by_headings(root) -> list[tuple[str, str]]:
    """Walk the DOM and split on h1-h4 boundaries.

    Returns a list of ``(heading_text, body_text)`` tuples.  Text nodes
    that appear before the first heading are grouped under an empty
    section title.
    """
    heading_tags = {"h1", "h2", "h3", "h4"}

    sections: list[tuple[str, str]] = []
    current_heading = ""
    current_paragraphs: list[str] = []

    for element in root.children:
        tag_name = getattr(element, "name", None)
        if tag_name in heading_tags:
            # Flush the current section.
            if current_paragraphs:
                sections.append((current_heading, "\n\n".join(current_paragraphs)))
                current_paragraphs = []
            current_heading = element.get_text(separator=" ", strip=True)
        elif tag_name in ("p", "li", "td", "th", "dt", "dd", "blockquote"):
            text = element.get_text(separator=" ", strip=True)
            if text:
                current_paragraphs.append(text)
        elif tag_name in ("div", "section", "article", "aside"):
            # Recurse into container elements.
            sub_sections = _split_html_by_headings(element)
            if sub_sections:
                if current_paragraphs:
                    sections.append((current_heading, "\n\n".join(current_paragraphs)))
                    current_paragraphs = []
                sections.extend(sub_sections)
        else:
            # Bare text nodes, spans, etc.
            text = getattr(element, "get_text", lambda **_: str(element))(
                separator=" ", strip=True
            ) if hasattr(element, "get_text") else str(element).strip()
            if text:
                current_paragraphs.append(text)

    # Flush the last section.
    if current_paragraphs:
        sections.append((current_heading, "\n\n".join(current_paragraphs)))

    # If nothing was extracted, fall back to all-text extraction.
    if not sections:
        text = root.get_text(separator="\n", strip=True) if hasattr(root, "get_text") else ""
        if text:
            sections = [("", text)]

    return sections

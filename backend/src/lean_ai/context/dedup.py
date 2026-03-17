"""Mechanical section reorganization for Markdown documents.

Used by both project context generation and framework guide generation
to merge duplicate ``##`` sections and drop exact-match lines.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def reorganize_sections(doc: str, *, log_prefix: str = "Reorg") -> str:
    """Merge duplicate ``##`` sections and drop exact-match lines.

    1. Parse the document into sections delimited by ``## `` headings.
    2. When the same heading appears more than once, merge content blocks
       in document order under a single heading.
    3. Within each merged section, drop lines that are exact duplicates
       of an earlier line in the same section (first occurrence kept).
    4. Preserve preamble (content before the first heading).
    5. Preserve heading order (first-occurrence order).
    6. Collapse triple+ blank lines to double.
    """
    lines = doc.split("\n")

    # ── Parse into sections: (heading, content_lines) ──
    preamble: list[str] = []
    # Ordered dict: heading → list of content-line lists (one per occurrence)
    sections: dict[str, list[list[str]]] = {}
    heading_order: list[str] = []

    current_heading: str | None = None
    current_lines: list[str] = []

    for line in lines:
        if line.lstrip().startswith("## "):
            # Flush previous
            if current_heading is None:
                preamble = current_lines
            else:
                sections.setdefault(current_heading, []).append(current_lines)
            heading = line.strip()
            if heading not in sections:
                heading_order.append(heading)
                sections[heading] = []
            current_heading = heading
            current_lines = []
        else:
            current_lines.append(line)

    # Flush last section
    if current_heading is None:
        # No headings at all — return unchanged
        return doc
    sections.setdefault(current_heading, []).append(current_lines)

    if not heading_order:
        return doc

    # ── Merge + deduplicate lines per section ──
    merged_count = 0
    result_lines: list[str] = list(preamble)

    for heading in heading_order:
        blocks = sections[heading]
        if len(blocks) > 1:
            merged_count += len(blocks) - 1

        result_lines.append(heading)

        # Collect all content lines across occurrences
        seen: set[str] = set()
        for block in blocks:
            for line in block:
                stripped = line.strip()
                # Keep blank lines (don't deduplicate them)
                # Deduplicate only non-empty lines
                if not stripped:
                    result_lines.append(line)
                elif stripped not in seen:
                    seen.add(stripped)
                    result_lines.append(line)

    # ── Collapse triple+ blank lines to double ──
    cleaned: list[str] = []
    blank_count = 0
    for line in result_lines:
        if not line.strip():
            blank_count += 1
            if blank_count <= 2:
                cleaned.append(line)
        else:
            blank_count = 0
            cleaned.append(line)

    if merged_count:
        logger.info(
            "%s: merged %d duplicate section occurrence(s)",
            log_prefix, merged_count,
        )

    return "\n".join(cleaned)

"""Merge utility for combining additions-only LLM outputs into the base document.

Used by the iterative file-by-file generation loop when the document grows
too large for the context budget and the LLM switches to headings-only mode.
"""

import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


def _merge_additions_into_doc(base_doc: str, additions_list: list[str]) -> str:
    """Merge multiple additions-only outputs into the base document.

    Each additions output contains ``## `` headings with new content.
    For each heading found in the additions, the new content is appended
    at the end of the corresponding section in the base document (just
    before the next ``## `` heading, or at the document end).

    Handles:
    - Multiple additions referencing the same heading (all get appended).
    - Headings in additions that don't exist in base (logged, skipped).
    - Empty additions (skipped).
    """
    if not additions_list:
        return base_doc

    # Parse additions into heading -> [content_blocks].
    heading_additions: dict[str, list[str]] = defaultdict(list)
    for additions_text in additions_list:
        if not additions_text.strip():
            continue
        current_heading = ""
        current_lines: list[str] = []
        for line in additions_text.split("\n"):
            if line.strip().startswith("## "):
                if current_heading and current_lines:
                    content = "\n".join(current_lines).strip()
                    if content:
                        heading_additions[current_heading].append(content)
                current_heading = line.strip()
                current_lines = []
            elif current_heading:
                current_lines.append(line)
        # Flush last section.
        if current_heading and current_lines:
            content = "\n".join(current_lines).strip()
            if content:
                heading_additions[current_heading].append(content)

    if not heading_additions:
        return base_doc

    # Parse base document into sections: (heading, start_line, end_line).
    lines = base_doc.split("\n")
    sections: list[tuple[str, int, int]] = []
    for i, line in enumerate(lines):
        if line.strip().startswith("## "):
            sections.append((line.strip(), i, -1))
            if len(sections) > 1:
                sections[-2] = (sections[-2][0], sections[-2][1], i)
    if sections:
        sections[-1] = (sections[-1][0], sections[-1][1], len(lines))

    # Build heading -> section end line mapping.
    heading_end: dict[str, int] = {heading: end for heading, _start, end in sections}

    # Insert additions at the end of each matching section.
    # Process in reverse document order so line indices stay valid.
    for heading in reversed(list(heading_end.keys())):
        if heading not in heading_additions:
            continue
        end_line = heading_end[heading]
        # Strip trailing blank lines from the section.
        while end_line > 0 and not lines[end_line - 1].strip():
            end_line -= 1
        combined = "\n\n".join(heading_additions[heading])
        insert_lines = ["", combined, ""]
        lines[end_line:end_line] = insert_lines

    # Log any unknown headings.
    for heading in heading_additions:
        if heading not in heading_end:
            logger.info(
                "merge: heading %r not in base document, skipping", heading,
            )

    return "\n".join(lines)

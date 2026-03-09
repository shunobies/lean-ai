"""Shared LLM-based section deduplication for Markdown documents.

Used by both project context generation and framework guide generation
to remove semantically duplicate ``##`` sections.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lean_ai.llm.client import LLMClient

logger = logging.getLogger(__name__)


async def deduplicate_sections_llm(
    doc: str,
    llm_client: LLMClient,
    *,
    log_prefix: str = "Dedup",
) -> str:
    """Remove duplicate or overlapping ``##`` sections using the LLM.

    Sends a compact summary (heading + first ~200 chars) of each section
    to the LLM, which identifies redundant sections by number.  Python
    then mechanically removes those sections.

    Falls back gracefully — returns the original document unchanged on
    any failure.
    """
    try:
        lines = doc.split("\n")

        # Build (heading, start_line, end_line) tuples
        sections: list[tuple[str, int, int]] = []
        for i, line in enumerate(lines):
            if line.lstrip().startswith("## "):
                sections.append((line.strip(), i, -1))
                if len(sections) > 1:
                    sections[-2] = (
                        sections[-2][0],
                        sections[-2][1],
                        i,
                    )
        if sections:
            sections[-1] = (
                sections[-1][0],
                sections[-1][1],
                len(lines),
            )

        if len(sections) < 2:
            return doc

        # Build numbered list with content previews
        numbered: list[str] = []
        for idx, (heading, start, end) in enumerate(sections, 1):
            content_lines = lines[start + 1:end]
            preview = " ".join(
                ln.strip() for ln in content_lines if ln.strip()
            )[:200]
            numbered.append(f"{idx}. {heading}\n   \"{preview}\"")

        prompt = (
            f"The following Markdown document has {len(sections)} sections. "
            "Review for duplicate or overlapping sections that cover the "
            "same topic.\n\n"
            "Sections:\n"
            + "\n\n".join(numbered)
            + "\n\n"
            "For each group of duplicates, keep the BEST version (most "
            "complete and accurate content). Return ONLY the section "
            "numbers to REMOVE, one per line. If no duplicates exist, "
            "respond with: NONE"
        )

        response = await llm_client.chat_raw(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=64,
        )

        if "NONE" in response.upper().split():
            logger.info("%s: LLM found no duplicate sections", log_prefix)
            return doc

        # Parse section numbers to remove
        to_remove: list[int] = []
        for token in response.split():
            clean = token.strip(".,;:()[]")
            if clean.isdigit():
                idx = int(clean)
                if 1 <= idx <= len(sections) and idx not in to_remove:
                    to_remove.append(idx)

        if not to_remove:
            logger.info(
                "%s: LLM dedup returned no valid numbers, "
                "no sections removed",
                log_prefix,
            )
            return doc

        # Mechanically remove identified sections
        remove_set = set(to_remove)
        preamble_end = sections[0][1]
        result_lines = list(lines[:preamble_end])
        for idx, (_heading, start, end) in enumerate(sections, 1):
            if idx not in remove_set:
                result_lines.extend(lines[start:end])

        # Collapse triple+ blank lines to double
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

        logger.info(
            "%s: LLM identified %d duplicate section(s) to remove: %s",
            log_prefix,
            len(to_remove),
            ", ".join(str(n) for n in to_remove),
        )
        return "\n".join(cleaned)
    except Exception as exc:
        logger.info(
            "%s: LLM dedup failed, no sections removed: %s",
            log_prefix, exc,
        )
        return doc

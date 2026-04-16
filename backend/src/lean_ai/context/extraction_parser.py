"""Parse LLM extraction output into structured (section, file_path, content) tuples.

The extraction prompt produces markdown in this format::

    ## <heading>
    - `<symbol>` — <fact> (`<file_path>`)

This parser handles variations: missing backticks, path in different
positions, multi-line entries, etc.
"""

import logging

logger = logging.getLogger(__name__)

_VALID_SECTIONS = frozenset({
    "Architecture Overview",
    "Module Map",
    "Key Abstractions",
    "API Surface",
    "Integration Points",
    "Data Flow",
    "Conventions",
})


def parse_extraction_output(
    text: str,
    fallback_file_path: str = "",
) -> list[tuple[str, str, str]]:
    """Parse extraction markdown into ``(section, file_path, content)`` tuples.

    Args:
        text: Raw LLM output (markdown with ``## `` headings and ``- ``
            bullet entries).
        fallback_file_path: File path to use when the LLM omits it from
            the entry.  For single-file extraction this is the source
            file being processed.

    Returns:
        List of ``(section_name, file_path, content_line)`` tuples.
    """
    if not text or not text.strip():
        return []

    results: list[tuple[str, str, str]] = []
    current_section = ""

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        # Detect section headings.
        if stripped.startswith("## "):
            heading = stripped[3:].strip()
            heading = _normalize_heading(heading)
            if heading in _VALID_SECTIONS:
                current_section = heading
            else:
                logger.debug("Unknown section heading: %r", heading)
                current_section = ""
            continue

        # Only process bullet points under a valid section.
        if not current_section:
            continue
        if not stripped.startswith("- "):
            continue

        entry_text = stripped[2:].strip()
        if not entry_text:
            continue

        file_path = _extract_file_path(entry_text) or fallback_file_path
        results.append((current_section, file_path, entry_text))

    return results


def parse_skeleton_output(
    text: str,
) -> list[tuple[str, str, str, str]]:
    """Parse a deterministic skeleton into DB entry tuples.

    Returns ``(section, file_path, content, "skeleton")`` tuples.
    Handles both ``- `` bullets and ``### sub/`` subsection headings
    (Module Map directories are treated as entries with the directory
    as the file_path).
    """
    if not text or not text.strip():
        return []

    results: list[tuple[str, str, str, str]] = []
    current_section = ""
    current_subsection_path = ""

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        # Top-level heading.
        if stripped.startswith("# ") and not stripped.startswith("## "):
            continue

        # Section heading.
        if stripped.startswith("## "):
            heading = stripped[3:].strip()
            heading = _normalize_heading(heading)
            if heading in _VALID_SECTIONS:
                current_section = heading
            else:
                current_section = ""
            current_subsection_path = ""
            continue

        if not current_section:
            continue

        # Subsection heading (e.g. ``### src/lean_ai/``).
        if stripped.startswith("### "):
            current_subsection_path = stripped[4:].strip().rstrip("/")
            continue

        # Skip placeholder lines.
        if stripped in ("No data extracted yet.", "... (truncated)"):
            continue

        # Bullet entries.
        if stripped.startswith("- "):
            entry_text = stripped[2:].strip()
            if not entry_text:
                continue
            file_path = _extract_file_path(entry_text) or current_subsection_path
            results.append((current_section, file_path, entry_text, "skeleton"))
            continue

        # Non-bullet content (e.g. "Entry points: `main.py`").
        if current_section and stripped:
            results.append((current_section, "", stripped, "skeleton"))

    return results


def _normalize_heading(heading: str) -> str:
    """Strip trailing parenthetical qualifiers from headings.

    ``"Key Abstractions (Updated)"`` → ``"Key Abstractions"``
    """
    paren_idx = heading.rfind(" (")
    if paren_idx > 0 and heading.endswith(")"):
        return heading[:paren_idx].strip()
    return heading


def _extract_file_path(entry: str) -> str:
    """Extract a file path from an entry line.

    Looks for patterns like ``(`path/to/file.py`)``, ``(path/to/file.py)``,
    or backtick-delimited paths.
    """
    # Pattern: (`` `path/to/file.ext` ``) or ``(path/to/file.ext)``
    last_open = entry.rfind("(")
    if last_open >= 0 and entry.endswith(")"):
        inner = entry[last_open + 1 : -1].strip().strip("`")
        if _looks_like_path(inner):
            return inner

    # Pattern: backtick-delimited path anywhere in the entry.
    parts = entry.split("`")
    for part in reversed(parts):
        part = part.strip()
        if _looks_like_path(part):
            return part

    return ""


def _looks_like_path(text: str) -> bool:
    """Heuristic: does this string look like a file path?"""
    if not text:
        return False
    if "/" not in text and "\\" not in text:
        return False
    # Must have a file extension in the last component.
    last_part = text.split("/")[-1].split("\\")[-1]
    return "." in last_part

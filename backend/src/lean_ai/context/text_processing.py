"""Pure text processing utilities for project context documents.

Repetition detection, truncation heuristics, and section deduplication.
No LLM calls — all functions are pure string transformations.

No regex — all text processing uses simple string operations.
"""


def _truncate_repetition(text: str, *, max_repeats: int = 5) -> str:
    """Detect and truncate degenerate repetition in LLM output.

    Handles both line-level repetition (same line repeated) and
    intra-line repetition (same phrase repeated on a single line).

    No regex — uses simple string comparison.
    """
    # ── Line-level repetition ──
    out_lines: list[str] = []
    prev_line = None
    repeat_count = 0

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped == prev_line and stripped:
            repeat_count += 1
            if repeat_count <= max_repeats:
                out_lines.append(line)
            elif repeat_count == max_repeats + 1:
                out_lines.append("... (repetition truncated)")
        else:
            out_lines.append(line)
            prev_line = stripped
            repeat_count = 1

    result = "\n".join(out_lines)

    # ── Intra-line repetition ──
    # Look for repeated substrings within long lines.
    def _truncate_inline(line: str) -> str:
        if len(line) < 500:
            return line
        # Search for repeated phrases of length 15-80 chars
        for phrase_len in range(15, 80):
            for start in range(0, min(len(line) - phrase_len * 3, 500)):
                phrase = line[start : start + phrase_len]
                if not phrase.strip():
                    continue
                count = 0
                pos = start
                while pos <= len(line) - phrase_len:
                    if line[pos : pos + phrase_len] == phrase:
                        count += 1
                        pos += phrase_len
                    else:
                        break
                if count > max_repeats:
                    kept = phrase * max_repeats
                    return line[:start] + kept + " ... (repetition truncated)"
        return line

    final_lines = []
    for line in result.split("\n"):
        final_lines.append(_truncate_inline(line))
    return "\n".join(final_lines)


def _appears_truncated(text: str) -> bool:
    """Check if text appears to be truncated by a token limit.

    Heuristic: if the text does not end with a complete line
    (newline, period, or Markdown closing), it was likely cut off.

    No regex — checks the last non-whitespace character.
    """
    stripped = text.rstrip()
    if not stripped:
        return False
    last_char = stripped[-1]
    # Normal endings: newline, period, backtick (code block end),
    # dash (list item end), closing paren/bracket
    return last_char not in ("\n", ".", "`", "-", ")", "]", "}")


# Section headings that expansion rounds sometimes produce
_EXPANSION_ARTIFACT_HEADINGS: frozenset[str] = frozenset(
    {
        "## Additional Information from Additional Files",
        "## Additional Files",
        "## New Classes and Functions",
        "## Updated Module Map",
        "## Additional Information",
        "## Additional Context",
        "## Additional Details",
    }
)


def _normalize_h2(heading: str) -> str:
    """Strip parenthetical qualifiers from a ## heading for deduplication.

    ``"## Key Abstractions (Updated)"`` -> ``"## Key Abstractions"``

    No regex — scans for trailing `` (...)`` pattern.
    """
    stripped = heading.rstrip()
    if not stripped.endswith(")"):
        return stripped

    # Find the opening paren that matches the trailing close paren.
    # Walk backwards from the second-to-last character.
    paren_start = stripped.rfind(" (")
    if paren_start < 0:
        return stripped

    # Verify no unclosed parens between paren_start and end
    candidate = stripped[paren_start + 2 : -1]
    if "(" in candidate:
        return stripped

    return stripped[:paren_start].rstrip()


def _deduplicate_sections(doc: str) -> str:
    """Remove duplicate top-level (##) sections and known expansion artifacts.

    Multi-round expansion can produce:
    - Identical ``## Heading`` appearing more than once (keep first).
    - Headings with parenthetical qualifiers that are semantically duplicate.
    - Generic additive headings (always removed).

    Sub-sections (###) are not touched.
    """
    lines = doc.split("\n")
    seen_h2: set[str] = set()
    result: list[str] = []
    skipping = False

    for line in lines:
        if line.startswith("## "):
            heading = line.rstrip()
            normalized = _normalize_h2(heading)
            if heading in _EXPANSION_ARTIFACT_HEADINGS or normalized in seen_h2:
                skipping = True
            else:
                seen_h2.add(normalized)
                skipping = False
                result.append(line)
        elif skipping:
            pass
        else:
            result.append(line)

    return "\n".join(result)


def _deduplicate_subsections(doc: str) -> str:
    """Remove duplicate ### sub-sections within each ## section.

    If the same ### heading appears multiple times under the same
    ## parent, only the first occurrence is kept.

    No regex — uses simple string operations.
    """
    lines = doc.split("\n")
    result: list[str] = []
    # Track seen ### headings per current ## section
    seen_h3: set[str] = set()
    skipping_h3 = False

    for line in lines:
        if line.startswith("## "):
            # New top-level section — reset h3 tracking
            seen_h3 = set()
            skipping_h3 = False
            result.append(line)
        elif line.startswith("### "):
            heading = line.strip()
            if heading in seen_h3:
                skipping_h3 = True
            else:
                seen_h3.add(heading)
                skipping_h3 = False
                result.append(line)
        elif skipping_h3:
            # Skip content under a duplicate ### heading.
            # Stop skipping when we hit a new ### or ## heading (handled above).
            pass
        else:
            result.append(line)

    return "\n".join(result)

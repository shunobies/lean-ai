"""Prose-aware text chunker for knowledge documents.

Unlike the code chunker (which aligns splits on function/class boundaries),
the prose chunker splits on *paragraph boundaries* (blank lines), accumulating
paragraphs until a target character budget is reached and then carrying the
last few paragraphs into the next chunk as overlap.

Typical settings:
    target_chars = 800  → ~200 tokens per chunk (dense prose)
    overlap_chars = 150 → ~38 tokens overlap (keeps consecutive chunks
                          contextually connected)
"""

_TARGET_CHARS = 800
_OVERLAP_CHARS = 150


def chunk_prose(
    text: str,
    target_chars: int = _TARGET_CHARS,
    overlap_chars: int = _OVERLAP_CHARS,
) -> list[str]:
    """Split prose text into overlapping chunks on paragraph boundaries.

    Algorithm:
    1. Split on blank lines (``\\n\\n``) → paragraph list.
    2. Accumulate paragraphs until *target_chars* is reached.
    3. When the budget is full, close the current chunk.
    4. Carry the trailing paragraph(s) that fit within *overlap_chars*
       into the opening of the next chunk.

    Single paragraphs that exceed 2× *target_chars* are hard-split at
    line boundaries rather than left as one giant chunk.

    Args:
        text: Plain-text content to split.
        target_chars: Approximate character count per chunk.
        overlap_chars: Character budget for carried-over overlap.

    Returns:
        A list of chunk strings.  Returns a single-element list if the
        text is shorter than *target_chars*.
    """
    if not text or not text.strip():
        return []

    # Normalise line endings and split on blank lines.
    paragraphs = [p.strip() for p in text.replace("\r\n", "\n").split("\n\n")]
    paragraphs = [p for p in paragraphs if p]

    if not paragraphs:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para)

        # Para too large to fit whole — hard-split on newlines first.
        if para_len > target_chars * 2:
            # Flush the current accumulation before tackling the big para.
            if current:
                chunks.append("\n\n".join(current))
                current = _overlap_tail(current, overlap_chars)
                current_len = sum(len(p) for p in current)

            for line in para.splitlines():
                line = line.strip()
                if not line:
                    continue
                if current_len + len(line) > target_chars and current:
                    chunks.append("\n\n".join(current))
                    current = _overlap_tail(current, overlap_chars)
                    current_len = sum(len(p) for p in current)
                current.append(line)
                current_len += len(line)
            continue

        # Would adding this paragraph overflow the budget?
        if current_len + para_len > target_chars and current:
            chunks.append("\n\n".join(current))
            current = _overlap_tail(current, overlap_chars)
            current_len = sum(len(p) for p in current)

        current.append(para)
        current_len += para_len

    # Flush any remaining content.
    if current:
        chunks.append("\n\n".join(current))

    return chunks if chunks else [text[: target_chars * 2]]


def _overlap_tail(paragraphs: list[str], overlap_chars: int) -> list[str]:
    """Return trailing paragraphs that fit within *overlap_chars*.

    Iterates from the end of *paragraphs* backwards, accumulating
    paragraphs until the budget is exhausted.  The result is in the
    original order (earliest paragraph first).
    """
    tail: list[str] = []
    total = 0
    for para in reversed(paragraphs):
        if total + len(para) > overlap_chars:
            break
        tail.insert(0, para)
        total += len(para)
    return tail

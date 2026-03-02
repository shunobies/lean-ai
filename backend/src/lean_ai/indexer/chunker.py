"""AST-aware file chunking using tree-sitter.

Splits source files at function/class boundaries using tree-sitter AST,
with line-based fallback for non-code files.
"""

import logging

from lean_ai.config import settings
from lean_ai.languages.extractor import get_definition_nodes
from lean_ai.languages.registry import get_registry

logger = logging.getLogger(__name__)


def chunk_file(
    content: str,
    file_path: str,
    max_lines: int | None = None,
    overlap_lines: int | None = None,
) -> list[dict]:
    """Split a file into chunks, preferring AST boundaries for code files.

    Returns list of dicts with keys: content, start_line, end_line, language.
    """
    max_lines = max_lines or settings.chunk_max_lines
    overlap_lines = overlap_lines or settings.chunk_overlap_lines
    lines = content.splitlines()

    if not lines:
        return []

    # Determine language
    ext = ""
    if "." in file_path:
        ext = "." + file_path.rsplit(".", 1)[-1].lower()

    registry = get_registry()
    lang = registry.get_language(ext)
    language_name = lang.name if lang else "text"

    # Try AST-aware chunking for known languages
    if lang and lang.ts_grammar:
        boundaries = get_definition_nodes(content, lang)
        if boundaries:
            return _chunk_by_boundaries(
                lines, boundaries, max_lines, overlap_lines, language_name,
            )

    # Fallback: line-based chunking
    return _chunk_by_lines(lines, max_lines, overlap_lines, language_name)


def _chunk_by_boundaries(
    lines: list[str],
    boundaries: list[tuple[int, int, str]],
    max_lines: int,
    overlap_lines: int,
    language: str,
) -> list[dict]:
    """Split file at AST definition boundaries."""
    chunks: list[dict] = []
    total = len(lines)

    # Sort boundaries by start line
    boundaries.sort(key=lambda b: b[0])

    # Group consecutive definitions into chunks that fit within max_lines
    chunk_start = 1  # 1-based
    chunk_end = 0
    i = 0

    while i < len(boundaries):
        def_start, def_end, _name = boundaries[i]

        if chunk_end == 0:
            # Starting a new chunk — include any preamble before first definition
            chunk_start = max(1, def_start - overlap_lines)
            chunk_end = def_end
            i += 1
            continue

        # Would adding this definition exceed max_lines?
        if def_end - chunk_start + 1 > max_lines:
            # Emit current chunk
            chunks.append({
                "content": "\n".join(lines[chunk_start - 1 : chunk_end]),
                "start_line": chunk_start,
                "end_line": chunk_end,
                "language": language,
            })
            chunk_start = max(1, def_start - overlap_lines)
            chunk_end = def_end
        else:
            chunk_end = def_end

        i += 1

    # Emit final chunk (includes any trailing content)
    if chunk_end > 0:
        final_end = min(total, max(chunk_end, total))
        chunks.append({
            "content": "\n".join(lines[chunk_start - 1 : final_end]),
            "start_line": chunk_start,
            "end_line": final_end,
            "language": language,
        })

    # If the file had content before the first definition or gaps between defs,
    # the above handles it. If somehow we missed the start, add a preamble chunk.
    if chunks and chunks[0]["start_line"] > 1:
        preamble_end = chunks[0]["start_line"] - 1
        if preamble_end > overlap_lines:
            chunks.insert(0, {
                "content": "\n".join(lines[0:preamble_end]),
                "start_line": 1,
                "end_line": preamble_end,
                "language": language,
            })

    return chunks if chunks else _chunk_by_lines(lines, max_lines, overlap_lines, language)


def _chunk_by_lines(
    lines: list[str],
    max_lines: int,
    overlap_lines: int,
    language: str,
) -> list[dict]:
    """Simple line-based chunking with overlap."""
    chunks: list[dict] = []
    total = len(lines)
    start = 0

    while start < total:
        end = min(start + max_lines, total)
        chunks.append({
            "content": "\n".join(lines[start:end]),
            "start_line": start + 1,
            "end_line": end,
            "language": language,
        })
        start = end - overlap_lines if end < total else total

    return chunks

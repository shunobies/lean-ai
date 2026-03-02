"""File operations: create, edit, read."""

import difflib
import logging
from pathlib import Path

from lean_ai.tools.executor import ToolResult

logger = logging.getLogger(__name__)


async def create_file(path: str, content: str, repo_root: str) -> ToolResult:
    """Create a new file with the given content."""
    file_path = Path(repo_root) / path
    file_path.parent.mkdir(parents=True, exist_ok=True)

    original = ""
    if file_path.exists():
        original = file_path.read_text(encoding="utf-8")

    file_path.write_text(content, encoding="utf-8")
    diff = _generate_diff(original, content, path)

    return ToolResult(
        success=True,
        output=f"Wrote {len(content)} bytes to {path}",
        metadata={"file_path": path, "diff": diff},
    )


async def edit_file(
    path: str, search: str, replace: str, repo_root: str,
) -> ToolResult:
    """Apply a targeted SEARCH/REPLACE edit to an existing file.

    Falls back to whitespace-tolerant matching if exact match fails.
    """
    file_path = Path(repo_root) / path

    if not file_path.exists():
        return ToolResult(
            success=False,
            error=f"Cannot edit non-existent file: {path}. Use create_file for new files.",
        )

    original = file_path.read_text(encoding="utf-8")

    # Exact match
    if search in original:
        modified = original.replace(search, replace, 1)
    else:
        # Fuzzy match with whitespace normalization
        modified = _fuzzy_search_replace(original, search, replace)
        if modified is None:
            return ToolResult(
                success=False,
                error=(
                    f"SEARCH block not found in {path}. "
                    f"The search text must match the file exactly."
                ),
            )

    file_path.write_text(modified, encoding="utf-8")
    diff = _generate_diff(original, modified, path)

    return ToolResult(
        success=True,
        output=f"Edited {path} (search/replace: {len(search)} -> {len(replace)} chars)",
        metadata={"file_path": path, "diff": diff},
    )


async def read_file(
    path: str,
    repo_root: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> ToolResult:
    """Read a file with line numbers. Auto-truncates at 500 lines."""
    file_path = Path(repo_root) / path

    if not file_path.exists():
        return ToolResult(success=False, error=f"File not found: {path}")

    try:
        text = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ToolResult(success=False, error=f"Cannot read binary file: {path}")

    lines = text.splitlines()
    total = len(lines)

    # Apply line range if specified
    start = (start_line - 1) if start_line and start_line > 0 else 0
    end = end_line if end_line and end_line <= total else total

    selected = lines[start:end]
    max_display = 500
    truncated = len(selected) > max_display

    if truncated:
        selected = selected[:max_display]

    numbered = [f"{start + i + 1:>4} | {line}" for i, line in enumerate(selected)]
    output = "\n".join(numbered)

    if truncated:
        output += (
            f"\n\n[FILE TRUNCATED at {max_display} lines — "
            f"total {total} lines. Use start_line/end_line to read more.]"
        )

    return ToolResult(success=True, output=output)


def _fuzzy_search_replace(
    original: str, search: str, replace: str,
) -> str | None:
    """Match search with whitespace normalization.

    Pass 1: trailing-whitespace (.rstrip())
    Pass 2: full-strip (.strip()) with indentation re-application
    """
    orig_lines = original.split("\n")
    search_lines = search.split("\n")

    if not search_lines:
        return None

    # Pass 1: trailing whitespace normalization
    norm_search = [line.rstrip() for line in search_lines]
    for i in range(len(orig_lines) - len(search_lines) + 1):
        window = [orig_lines[i + j].rstrip() for j in range(len(search_lines))]
        if window == norm_search:
            replace_lines = replace.split("\n")
            result_lines = orig_lines[:i] + replace_lines + orig_lines[i + len(search_lines) :]
            return "\n".join(result_lines)

    # Pass 2: full strip with re-indentation
    stripped_search = [line.strip() for line in search_lines]
    if all(s == "" for s in stripped_search):
        return None

    for i in range(len(orig_lines) - len(search_lines) + 1):
        window = [orig_lines[i + j].strip() for j in range(len(search_lines))]
        if window == stripped_search:
            replace_lines = replace.split("\n")
            re_indented = _reindent_replacement(
                orig_lines[i : i + len(search_lines)], search_lines, replace_lines,
            )
            result_lines = orig_lines[:i] + re_indented + orig_lines[i + len(search_lines) :]
            return "\n".join(result_lines)

    return None


def _reindent_replacement(
    orig_matched: list[str], search_lines: list[str], replace_lines: list[str],
) -> list[str]:
    """Re-indent replace_lines to match original file's indentation."""

    def _leading_ws(line: str) -> str:
        return line[: len(line) - len(line.lstrip())]

    offset = 0
    for orig_line, search_line in zip(orig_matched, search_lines):
        if orig_line.strip() and search_line.strip():
            offset = len(_leading_ws(orig_line)) - len(_leading_ws(search_line))
            break

    if offset == 0:
        return replace_lines

    result = []
    for line in replace_lines:
        if not line.strip():
            result.append(line)
        elif offset > 0:
            result.append(" " * offset + line)
        else:
            current_indent = len(_leading_ws(line))
            trim = min(current_indent, abs(offset))
            result.append(line[trim:])
    return result


def _generate_diff(original: str, modified: str, file_path: str) -> str:
    """Generate a unified diff."""
    original_lines = original.splitlines(keepends=True)
    modified_lines = modified.splitlines(keepends=True)
    diff_lines = difflib.unified_diff(
        original_lines, modified_lines,
        fromfile=f"a/{file_path}", tofile=f"b/{file_path}",
    )
    return "".join(diff_lines)

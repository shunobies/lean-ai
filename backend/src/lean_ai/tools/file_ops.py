"""File operations: create, edit, read, grep."""

import difflib
import fnmatch
import logging
from pathlib import Path

from lean_ai.config import settings
from lean_ai.indexer.tree import list_repo_tree
from lean_ai.tools.executor import ToolResult

logger = logging.getLogger(__name__)

# Maximum file size for read_file (2 MB)
_MAX_READ_BYTES = 2 * 1024 * 1024


def _safe_resolve(
    path: str, repo_root: str, allow_external: bool = False,
) -> Path | None:
    """Resolve *path* under *repo_root* and verify it doesn't escape.

    Returns the resolved ``Path`` or ``None`` if the path escapes the
    repository root (e.g. via ``../`` traversal or symlinks).

    When *allow_external* is ``True``, paths outside the repo root are
    permitted (caller is responsible for gating this behind user approval).
    """
    resolved = (Path(repo_root) / path).resolve()
    if not resolved.is_relative_to(Path(repo_root).resolve()):
        if allow_external:
            return resolved
        return None
    return resolved


async def create_file(
    path: str, content: str, repo_root: str, allow_external: bool = False,
) -> ToolResult:
    """Create a new file with the given content."""
    file_path = _safe_resolve(path, repo_root, allow_external=allow_external)
    if file_path is None:
        return ToolResult(
            success=False,
            error=f"Path escapes repository root: {path}",
        )
    file_path.parent.mkdir(parents=True, exist_ok=True)

    original = ""
    overwritten = False
    if file_path.exists():
        original = file_path.read_text(encoding="utf-8")
        overwritten = True

    file_path.write_text(content, encoding="utf-8")
    diff = _generate_diff(original, content, path)

    output = f"Wrote {len(content)} bytes to {path}"
    if overwritten:
        output = (
            f"WARNING: Overwrote existing file {path} "
            f"({len(original)} bytes replaced). "
            f"Use edit_file for targeted changes to existing files.\n"
            f"{output}"
        )

    return ToolResult(
        success=True,
        output=output,
        metadata={"file_path": path, "diff": diff},
    )


async def edit_file(
    path: str, search: str, replace: str, repo_root: str,
    allow_external: bool = False,
) -> ToolResult:
    """Apply a targeted SEARCH/REPLACE edit to an existing file.

    Falls back to whitespace-tolerant matching if exact match fails.
    """
    file_path = _safe_resolve(path, repo_root, allow_external=allow_external)
    if file_path is None:
        return ToolResult(
            success=False,
            error=f"Path escapes repository root: {path}",
        )

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
            diagnostic = _find_closest_match(original, search)
            hint = (
                f"SEARCH block not found in {path}. "
                f"The file may have changed from a previous edit. "
                f"Re-read it with read_file before retrying."
            )
            if diagnostic:
                hint += f"\n\nClosest match in {path}:\n{diagnostic}"
            return ToolResult(success=False, error=hint)

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
    allow_external: bool = False,
) -> ToolResult:
    """Read a file with line numbers. Auto-truncates at 500 lines."""
    file_path = _safe_resolve(path, repo_root, allow_external=allow_external)
    if file_path is None:
        return ToolResult(
            success=False,
            error=f"Path escapes repository root: {path}",
        )

    if not file_path.exists():
        return ToolResult(success=False, error=f"File not found: {path}")

    try:
        size = file_path.stat().st_size
    except OSError:
        return ToolResult(success=False, error=f"Cannot stat file: {path}")

    if size > _MAX_READ_BYTES:
        return ToolResult(
            success=False,
            error=(
                f"File too large ({size:,} bytes, limit {_MAX_READ_BYTES:,}). "
                f"Use start_line/end_line to read a portion."
            ),
        )

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
    # Scale display limit with context window: 500 at 128k, ~123 at 32k
    max_display = max(100, min(500, settings._active_context_window // 260))
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


def _find_closest_match(original: str, search: str) -> str | None:
    """Find the closest matching region in a file for a failed search block.

    Uses the first substantive line of the search block as an anchor,
    scores candidate positions by how many surrounding lines also match,
    and returns a formatted snippet with line numbers (capped at 15 lines).
    """
    orig_lines = original.split("\n")
    search_lines = search.split("\n")

    # Find the first non-blank search line to use as anchor
    anchor_idx = None
    anchor_stripped = ""
    for i, line in enumerate(search_lines):
        if line.strip():
            anchor_idx = i
            anchor_stripped = line.strip().lower()
            break

    if anchor_idx is None:
        return None  # All-blank search block

    # Find all positions where the anchor line appears (stripped, case-insensitive)
    candidates: list[tuple[int, int]] = []  # (block_start, score)
    for i, orig_line in enumerate(orig_lines):
        if orig_line.strip().lower() == anchor_stripped:
            block_start = i - anchor_idx
            score = 0
            for j, s_line in enumerate(search_lines):
                file_idx = block_start + j
                if 0 <= file_idx < len(orig_lines):
                    if s_line.strip().lower() == orig_lines[file_idx].strip().lower():
                        score += 1
            candidates.append((block_start, score))

    # Fallback: substring match on anchor
    if not candidates:
        for i, orig_line in enumerate(orig_lines):
            if anchor_stripped in orig_line.strip().lower():
                candidates.append((i - anchor_idx, 1))

    if not candidates:
        return None

    best_start, _ = max(candidates, key=lambda c: c[1])

    # Build a display window around the best match
    display_start = max(0, best_start - 2)
    display_end = min(len(orig_lines), best_start + len(search_lines) + 2)

    snippet = []
    for i in range(display_start, display_end):
        snippet.append(f"{i + 1:>4} | {orig_lines[i]}")

    if len(snippet) > 15:
        snippet = snippet[:15]
        snippet.append("     | ...")

    return "\n".join(snippet)


def _generate_diff(original: str, modified: str, file_path: str) -> str:
    """Generate a unified diff."""
    original_lines = original.splitlines(keepends=True)
    modified_lines = modified.splitlines(keepends=True)
    diff_lines = difflib.unified_diff(
        original_lines, modified_lines,
        fromfile=f"a/{file_path}", tofile=f"b/{file_path}",
    )
    return "".join(diff_lines)


async def grep_files(
    pattern: str,
    repo_root: str,
    file_glob: str | None = None,
    max_results: int | None = None,
    context_lines: int = 1,
) -> ToolResult:
    """Search for a text pattern across all repository files.

    Returns matching file paths with line numbers and surrounding context.
    Uses the gitignore-aware tree walker so results stay relevant.
    """
    if not pattern:
        return ToolResult(success=False, error="Search pattern cannot be empty.")

    # Scale max_results with context window: 100 at 128k, ~24 at 32k
    if max_results is None:
        max_results = max(20, min(100, settings._active_context_window // 1300))

    pattern_lower = pattern.lower()
    entries = list_repo_tree(repo_root)

    # Optional glob filter (e.g. "*.php", "*.blade.php")
    if file_glob:
        entries = [e for e in entries if fnmatch.fnmatch(e.path, file_glob)]

    matches: list[str] = []
    files_with_matches = 0

    for entry in entries:
        file_path = Path(repo_root) / entry.path
        try:
            text = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        lines = text.splitlines()
        file_matches: list[str] = []
        for i, line in enumerate(lines):
            if pattern_lower in line.lower():
                # Gather context window
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                for j in range(start, end):
                    marker = ">" if j == i else " "
                    file_matches.append(f"  {marker} {j + 1:>4} | {lines[j]}")

        if file_matches:
            files_with_matches += 1
            matches.append(f"{entry.path}:")
            matches.extend(file_matches)
            matches.append("")

        if files_with_matches >= max_results:
            matches.append(
                f"[TRUNCATED — showing first {max_results} files. "
                f"Use a more specific pattern or file_glob to narrow results.]"
            )
            break

    if not matches:
        return ToolResult(
            success=True,
            output=f"No matches found for '{pattern}'.",
        )

    header = f"Found matches in {files_with_matches} file(s) for '{pattern}':\n\n"
    return ToolResult(success=True, output=header + "\n".join(matches))

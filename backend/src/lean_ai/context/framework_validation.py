"""File path extraction, validation, and surgical correction for framework guides."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lean_ai.llm.client import LLMClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FILE_EXTENSIONS = frozenset({
    ".php", ".js", ".ts", ".tsx", ".jsx", ".py", ".rb",
    ".java", ".go", ".rs", ".c", ".h", ".cpp", ".cs",
    ".vue", ".svelte", ".blade.php", ".erb", ".html",
    ".css", ".scss", ".json", ".yaml", ".yml", ".toml",
    ".xml", ".sql", ".sh", ".env",
})

# PHP vendor namespace prefixes — these are framework/library namespaces
# that should NOT be validated against the project tree.
_PHP_STDLIB_PREFIXES = frozenset({
    "Illuminate", "Symfony", "Carbon", "Doctrine", "League",
    "Monolog", "Psr", "GuzzleHttp", "Ramsey", "Faker",
    "PHPUnit", "Mockery", "Composer",
})


# ---------------------------------------------------------------------------
# Path extraction
# ---------------------------------------------------------------------------

def extract_file_paths(text: str) -> set[str]:
    """Extract potential file paths from markdown text.

    Looks for substrings containing ``/`` that end with a known
    file extension.  Strips surrounding punctuation (backticks,
    parens, quotes).  Ignores URLs.
    """
    paths: set[str] = set()
    for word in text.split():
        clean = word.strip("`()[]{}\"',:;")
        if "/" not in clean or clean.startswith(("http://", "https://")):
            continue
        # Handle .blade.php (compound extension)
        if clean.endswith(".blade.php"):
            paths.add(clean)
            continue
        # Check single extensions
        dot = clean.rfind(".")
        if dot != -1 and clean[dot:] in _FILE_EXTENSIONS:
            paths.add(clean)
    return paths


def extract_php_class_refs(text: str) -> dict[str, str]:
    """Extract PHP namespace references and convert to file paths.

    Finds backslash-separated tokens starting with an uppercase letter
    (e.g., ``App\\Http\\Kernel``) and converts them to file paths using
    PSR-4 convention: replace ``\\`` with ``/``, lowercase the first
    segment, append ``.php``.

    Skips vendor/framework namespaces (Illuminate, Symfony, etc.).

    Returns ``{converted_path: original_namespace}`` so callers can
    search for the original string in the text when removing blocks.
    """
    refs: dict[str, str] = {}
    for word in text.split():
        clean = word.strip("`()[]{}\"',:;")
        if "\\" not in clean:
            continue
        parts = clean.split("\\")
        if len(parts) < 2:
            continue
        # Must start with an uppercase letter (namespace convention)
        if not parts[0] or not parts[0][0].isupper():
            continue
        # Skip vendor/framework namespaces
        if parts[0] in _PHP_STDLIB_PREFIXES:
            continue
        # Convert to file path: App\Http\Kernel → app/Http/Kernel.php
        path_parts = list(parts)
        path_parts[0] = path_parts[0].lower()
        converted = "/".join(path_parts) + ".php"
        refs[converted] = clean
    return refs


def get_project_paths(repo_root: str) -> set[str]:
    """Return a set of all file paths in the project (relative to root)."""
    from lean_ai.indexer.tree import list_repo_tree

    return {e.path for e in list_repo_tree(repo_root)}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def check_invalid_paths(
    text: str,
    project_paths: set[str],
    project_top_dirs: set[str],
) -> set[str]:
    """Return referenced strings whose file paths don't exist in the project.

    Checks both explicit file paths (with ``/``) and PHP namespace
    references (with ``\\``) converted to paths via PSR-4.

    Returns the original text strings (not converted paths) so that
    block removal can find them in the source text.
    """
    # Explicit file paths — the string in the text IS the file path
    referenced = extract_file_paths(text)
    invalid: set[str] = set()
    for path in referenced:
        top_dir = path.split("/")[0]
        if top_dir in project_top_dirs and path not in project_paths:
            invalid.add(path)

    # PHP namespace references — convert to path, validate, but return
    # the original namespace string for block matching
    php_refs = extract_php_class_refs(text)
    for converted_path, original_namespace in php_refs.items():
        top_dir = converted_path.split("/")[0]
        if top_dir in project_top_dirs and converted_path not in project_paths:
            invalid.add(original_namespace)

    return invalid


# ---------------------------------------------------------------------------
# Block boundary detection and removal
# ---------------------------------------------------------------------------

def find_block_boundaries(
    lines: list[str],
    path: str,
) -> list[tuple[int, int]]:
    """Find line ranges of Markdown blocks that reference *path*.

    Returns ``(start_inclusive, end_exclusive)`` tuples.  A "block" is
    a fenced code block, a list item, or a contiguous paragraph.
    """
    blocks: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].lstrip()
        # ── Fenced code block ──
        if stripped.startswith("```"):
            fence_start = i
            fence_has_path = path in lines[i]
            i += 1
            while i < len(lines):
                if path in lines[i]:
                    fence_has_path = True
                if lines[i].lstrip().startswith("```"):
                    i += 1
                    break
                i += 1
            if fence_has_path:
                blocks.append((fence_start, i))
            continue
        # ── Check if this line references the path ──
        if path not in lines[i]:
            i += 1
            continue
        # ── List item ──
        if stripped.startswith(("- ", "* ")) or (
            stripped[:1].isdigit() and ". " in stripped[:4]
        ):
            block_start = i
            i += 1
            while i < len(lines):
                s = lines[i].lstrip()
                if not lines[i].strip():
                    break
                if s.startswith(("- ", "* ")) or (
                    s[:1].isdigit() and ". " in s[:4]
                ):
                    break
                i += 1
            blocks.append((block_start, i))
            continue
        # ── Paragraph ──
        block_start = i
        while block_start > 0 and lines[block_start - 1].strip():
            if lines[block_start - 1].lstrip().startswith("#"):
                break
            block_start -= 1
        i += 1
        while i < len(lines) and lines[i].strip():
            if lines[i].lstrip().startswith("#"):
                break
            i += 1
        blocks.append((block_start, i))
    return blocks


def remove_blocks(text: str, invalid_paths: set[str]) -> str:
    """Remove Markdown blocks referencing any *invalid_paths*.

    Uses line-level operations only — no LLM.
    """
    lines = text.split("\n")

    remove_ranges: list[tuple[int, int]] = []
    for path in invalid_paths:
        remove_ranges.extend(find_block_boundaries(lines, path))

    if not remove_ranges:
        return text

    # Merge overlapping ranges
    remove_ranges.sort()
    merged: list[tuple[int, int]] = [remove_ranges[0]]
    for start, end in remove_ranges[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    remove_lines: set[int] = set()
    for start, end in merged:
        remove_lines.update(range(start, end))

    kept = [line for i, line in enumerate(lines) if i not in remove_lines]

    # Collapse triple+ blank lines to double
    cleaned: list[str] = []
    blank_count = 0
    for line in kept:
        if not line.strip():
            blank_count += 1
            if blank_count <= 2:
                cleaned.append(line)
        else:
            blank_count = 0
            cleaned.append(line)

    return "\n".join(cleaned)


async def surgical_llm_fix(
    guide: str,
    invalid_paths: set[str],
    llm_client: LLMClient,
    system_prompt: str,
    max_llm_calls: int = 5,
) -> str:
    """Fix invalid path references by correcting individual blocks."""
    lines = guide.split("\n")

    # Collect all blocks referencing any invalid path
    all_ranges: list[tuple[int, int]] = []
    for path in invalid_paths:
        all_ranges.extend(find_block_boundaries(lines, path))

    if not all_ranges:
        return guide

    # Merge overlapping ranges
    all_ranges.sort()
    merged: list[tuple[int, int]] = [all_ranges[0]]
    for start, end in all_ranges[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    # Cap at max_llm_calls by merging closest blocks
    while len(merged) > max_llm_calls and len(merged) > 1:
        min_gap = float("inf")
        min_idx = 0
        for idx in range(len(merged) - 1):
            gap = merged[idx + 1][0] - merged[idx][1]
            if gap < min_gap:
                min_gap = gap
                min_idx = idx
        merged[min_idx] = (merged[min_idx][0], merged[min_idx + 1][1])
        del merged[min_idx + 1]

    # Process in reverse order to preserve line indices
    result_lines = list(lines)
    for start, end in reversed(merged):
        block_text = "\n".join(lines[start:end])
        block_invalid = {p for p in invalid_paths if p in block_text}

        prompt = (
            "The following file paths do NOT exist in this project:\n"
            + "\n".join(f"- `{p}`" for p in sorted(block_invalid))
            + "\n\n"
            "Rewrite ONLY the following Markdown excerpt, correcting "
            "or removing references to the non-existent paths. "
            "If a code block references a non-existent file and you "
            "cannot determine the correct replacement, remove the "
            "entire code block. Output ONLY the corrected excerpt, "
            "nothing else.\n\n"
            "EXCERPT:\n" + block_text
        )

        try:
            fixed = await llm_client.chat_raw(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt[:8000]},
                ],
                max_tokens=1024,
            )
            fixed = fixed.strip()
            if fixed:
                result_lines[start:end] = fixed.split("\n")
        except Exception as exc:
            logger.info(
                "Framework guide: surgical fix lines %d-%d failed: %s",
                start, end, exc,
            )

    return "\n".join(result_lines)

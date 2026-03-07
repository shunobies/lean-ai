"""File content collection and prompt assembly for project context generation.

Reads key files (docs, entry points) and fan-in ranked source files,
and assembles the generation prompt that is sent to the LLM.
"""

import logging
from collections import defaultdict
from pathlib import Path

from .constants import (
    _MAX_DOC_FILE_CHARS,
    _MAX_FILE_CHARS,
    _MAX_IMPORT_GRAPH_CHARS,
    _MAX_INDEX_CHARS,
    _MAX_SAMPLE_CHARS,
    _get_entry_points,
    _get_key_files,
    _get_source_exts,
)
from .metadata import _is_test_file, extract_metadata_cached

logger = logging.getLogger(__name__)


def _read_file_safe(path: Path, max_chars: int = _MAX_FILE_CHARS) -> str:
    """Read a text file, returning empty string on any error."""
    try:
        if not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n... (truncated at {max_chars} chars)"
        return text
    except Exception:
        return ""


def _build_file_tree_summary(repo_root: str, entries=None) -> str:
    """Build a compact file tree for the LLM prompt."""
    if entries is None:
        try:
            from lean_ai.indexer.tree import list_repo_tree
            entries = list_repo_tree(repo_root)
        except Exception:
            logger.debug("Failed to list repo tree for project context", exc_info=True)
            return "(could not read file tree)"

    if not entries:
        return "(empty repository)"

    groups: dict[str, list[str]] = defaultdict(list)
    for entry in entries:
        parent = str(Path(entry.path).parent).replace("\\", "/")
        if parent == ".":
            parent = "(root)"
        groups[parent].append(Path(entry.path).name)

    lines: list[str] = []
    for dir_name in sorted(groups.keys()):
        filenames = sorted(groups[dir_name])
        count = len(filenames)
        if count <= 12:
            lines.append(f"{dir_name}/: {', '.join(filenames)}")
        else:
            shown = filenames[:10]
            lines.append(
                f"{dir_name}/ ({count} files): {', '.join(shown)}, "
                f"... +{count - 10} more"
            )

    return "\n".join(lines)


def _collect_key_file_contents(
    repo_root: str,
    entries=None,
    fan_in: dict[str, int] | None = None,
    max_sample_chars: int = _MAX_SAMPLE_CHARS,
    max_file_chars: int = _MAX_FILE_CHARS,
    max_doc_file_chars: int = _MAX_DOC_FILE_CHARS,
    max_sampled_files: int = 15,
) -> str:
    """Read key files and sample source files ranked by architectural importance.

    Sampling strategy:
    1. Documentation files (README, CLAUDE.md, pyproject.toml).
    2. Entry points (main.py, app.py, index.ts) from each directory.
    3. Source files ranked by import fan-in (most-imported first).
    """
    root = Path(repo_root)
    parts: list[str] = []
    total_chars = 0
    sampled_files: set[str] = set()

    # 1. Read well-known doc/config files
    for filename in _get_key_files():
        full = root / filename
        is_doc = filename.endswith(".md")
        cap = max_doc_file_chars if is_doc else max_file_chars
        content = _read_file_safe(full, max_chars=cap)
        if content:
            block = f"--- {filename} ---\n```\n{content}\n```"
            if total_chars + len(block) > max_sample_chars:
                break
            parts.append(block)
            total_chars += len(block)
            sampled_files.add(filename)

    # 2. Entry points from major directories
    if entries is None:
        try:
            from lean_ai.indexer.tree import list_repo_tree
            entries = list_repo_tree(repo_root)
        except Exception:
            entries = []

    entry_point_names = _get_entry_points()
    sampled_dirs: set[str] = set()

    for entry in entries:
        name = Path(entry.path).name
        if name in entry_point_names:
            parent = str(Path(entry.path).parent).replace("\\", "/")
            if parent in sampled_dirs:
                continue
            content = _read_file_safe(root / entry.path, max_chars=max_file_chars)
            if content:
                block = f"--- {entry.path} (entry point) ---\n```\n{content}\n```"
                if total_chars + len(block) > max_sample_chars:
                    break
                parts.append(block)
                total_chars += len(block)
                sampled_dirs.add(parent)
                sampled_files.add(entry.path)

    # 3. Remaining files ranked by import fan-in (most-imported first)
    if fan_in is None:
        fan_in = {}

    candidates: list[tuple[str, int]] = []
    for entry in entries:
        if entry.path in sampled_files:
            continue
        if entry.path.endswith("__init__.py"):
            continue
        if "/tests/" in entry.path or "\\tests\\" in entry.path:
            continue
        ext = Path(entry.path).suffix.lower()
        if ext not in _get_source_exts():
            continue
        score = fan_in.get(entry.path, 0)
        candidates.append((entry.path, score))

    candidates.sort(key=lambda x: (-x[1], x[0]))

    files_added = 0
    for file_path, score in candidates:
        if files_added >= max_sampled_files:
            break
        content = _read_file_safe(root / file_path, max_chars=max_file_chars)
        if content:
            label = f"sample, imported by {score} files" if score > 0 else "sample"
            block = f"--- {file_path} ({label}) ---\n```\n{content}\n```"
            if total_chars + len(block) > max_sample_chars:
                break
            parts.append(block)
            total_chars += len(block)
            sampled_files.add(file_path)
            files_added += 1

    return "\n\n".join(parts) if parts else "(no files could be read)"


def build_generation_prompt(
    repo_root: str,
    section_caps: dict[str, int] | None = None,
) -> str:
    """Build the user-message prompt for the LLM context generation call.

    Assembles the file tree, class/function index, import graph, API
    endpoints, and key file contents into a single prompt string.
    """
    caps = section_caps or {
        "index":              _MAX_INDEX_CHARS,
        "import_graph":       _MAX_IMPORT_GRAPH_CHARS,
        "sample":             _MAX_SAMPLE_CHARS,
        "max_file_chars":     _MAX_FILE_CHARS,
        "max_doc_file_chars": _MAX_DOC_FILE_CHARS,
        "max_sampled_files":  15,
    }

    try:
        from lean_ai.indexer.tree import list_repo_tree
        entries = list_repo_tree(repo_root)
    except Exception:
        entries = None

    metadata = extract_metadata_cached(repo_root, entries=entries)

    tree = _build_file_tree_summary(repo_root, entries=entries)
    class_index = metadata.format_class_index(max_chars=caps["index"])
    import_graph = metadata.format_import_graph(max_chars=caps["import_graph"])
    api_endpoints = metadata.format_api_endpoints(max_chars=caps.get("api_endpoints", 8000))
    file_contents = _collect_key_file_contents(
        repo_root, entries=entries, fan_in=metadata.fan_in,
        max_sample_chars=caps["sample"],
        max_file_chars=caps.get("max_file_chars", _MAX_FILE_CHARS),
        max_doc_file_chars=caps.get("max_doc_file_chars", _MAX_DOC_FILE_CHARS),
        max_sampled_files=caps.get("max_sampled_files", 15),
    )

    return (
        "Analyze this repository and produce the project context document.\n\n"
        "=== FILE TREE ===\n"
        f"{tree}\n\n"
        "=== CLASS AND FUNCTION INDEX ===\n"
        "These are the ACTUAL class and function definitions found in the source code. "
        "Use ONLY these names in your document — do not invent others.\n\n"
        f"{class_index}\n\n"
        "=== IMPORT GRAPH ===\n"
        "These are the ACTUAL import relationships between modules. "
        "Use this to describe how modules connect — do not guess connections.\n\n"
        f"{import_graph}\n\n"
        "=== API ENDPOINTS ===\n"
        "These are the ACTUAL REST and WebSocket endpoint routes defined in "
        "the source code. Include ALL of these in your API Surface section — "
        "do not invent endpoints that are not listed here.\n\n"
        f"{api_endpoints}\n\n"
        "=== KEY FILE CONTENTS ===\n"
        f"{file_contents}\n\n"
        "Now write the project context document. Remember: ONLY reference "
        "class names, function names, and files that appear above. "
        "Do NOT invent or generalize."
    )


def _collect_priority_file_contents(
    repo_root: str,
    entries,
    max_file_chars: int = _MAX_FILE_CHARS,
    max_doc_file_chars: int = _MAX_DOC_FILE_CHARS,
) -> tuple[str, set[str]]:
    """Read key doc/config files and entry points for the first generation round.

    Returns (content_string, set_of_sampled_relative_paths).
    """
    root = Path(repo_root)
    parts: list[str] = []
    sampled: set[str] = set()

    for filename in _get_key_files():
        full = root / filename
        is_doc = filename.endswith(".md")
        cap = max_doc_file_chars if is_doc else max_file_chars
        content = _read_file_safe(full, max_chars=cap)
        if content:
            parts.append(f"--- {filename} ---\n```\n{content}\n```")
            sampled.add(filename)

    if entries is None:
        try:
            from lean_ai.indexer.tree import list_repo_tree
            entries = list_repo_tree(repo_root)
        except Exception:
            entries = []

    entry_point_names = _get_entry_points()
    sampled_dirs: set[str] = set()

    for entry in entries:
        name = Path(entry.path).name
        if name not in entry_point_names:
            continue
        parent = str(Path(entry.path).parent).replace("\\", "/")
        if parent in sampled_dirs:
            continue
        norm = entry.path.replace("\\", "/")
        if norm in sampled or name in sampled:
            continue
        content = _read_file_safe(root / entry.path, max_chars=max_file_chars)
        if content:
            parts.append(f"--- {entry.path} (entry point) ---\n```\n{content}\n```")
            sampled.add(norm)
            sampled_dirs.add(parent)

    return "\n\n".join(parts) if parts else "(no key files found)", sampled


def _collect_all_ranked_candidates(
    repo_root: str,
    entries,
    fan_in: dict[str, int],
    exclude_paths: set[str],
    max_file_chars: int = _MAX_FILE_CHARS,
) -> list[tuple[str, str]]:
    """Collect all fan-in ranked source files not already included in round 1.

    Returns a list of ``(relative_path, file_content)`` tuples sorted by
    fan-in descending.
    """
    root = Path(repo_root)
    source_exts = _get_source_exts()
    normalised_excludes = {p.replace("\\", "/") for p in exclude_paths}
    bare_excludes = {Path(p).name for p in normalised_excludes}

    candidates: list[tuple[str, str, int]] = []

    for entry in entries:
        norm = entry.path.replace("\\", "/")
        if norm in normalised_excludes:
            continue
        if Path(norm).name in bare_excludes:
            continue
        if norm.endswith("__init__.py"):
            continue
        if "/tests/" in norm or "\\tests\\" in norm:
            continue
        if _is_test_file(norm):
            continue
        ext = Path(norm).suffix.lower()
        if ext not in source_exts:
            continue
        content = _read_file_safe(root / entry.path, max_chars=max_file_chars)
        if content:
            score = fan_in.get(norm, 0)
            candidates.append((norm, content, score))

    candidates.sort(key=lambda x: (-x[2], x[0]))
    return [(path, content) for path, content, _ in candidates]


def _batch_file_contents(
    candidates: list[tuple[str, str]],
    batch_budget_chars: int,
) -> list[str]:
    """Group ``(path, content)`` candidates into batches <= *batch_budget_chars* each."""
    batches: list[str] = []
    current_parts: list[str] = []
    current_chars = 0

    for file_path, content in candidates:
        block = f"--- {file_path} ---\n```\n{content}\n```"
        if current_chars + len(block) > batch_budget_chars and current_parts:
            batches.append("\n\n".join(current_parts))
            current_parts = []
            current_chars = 0
        current_parts.append(block)
        current_chars += len(block)

    if current_parts:
        batches.append("\n\n".join(current_parts))

    return batches


def _build_expansion_prompt(
    existing_context: str,
    file_contents: str,
    round_num: int,
    total_rounds: int,
) -> str:
    """Build the user-message prompt for an expansion round (round 2+)."""
    return (
        f"=== EXISTING PROJECT CONTEXT (round {round_num} of {total_rounds}) ===\n"
        f"{existing_context}\n\n"
        "=== ADDITIONAL FILE CONTENTS ===\n"
        f"Expand the document above using the following source files "
        f"(round {round_num} of {total_rounds}). "
        "Do NOT repeat content already in the document — only add what is new:\n\n"
        f"{file_contents}\n\n"
        "Now write the COMPLETE updated project context document. "
        "Include ALL existing content and add what you learn from the files above. "
        "ONLY reference class names, function names, and file paths visible in "
        "the data you have been given."
    )


def _extract_section_headings(doc: str) -> list[str]:
    """Extract ## heading lines from a context document."""
    return [
        line.rstrip()
        for line in doc.split("\n")
        if line.startswith("## ")
    ]


def _extract_covered_names(doc: str) -> str:
    """Extract a compact list of class/function names already mentioned in the doc.

    Scans for backtick-wrapped names (``ClassName``, ``function_name()``).
    No regex — simple character scanning.
    """
    names: list[str] = []
    i = 0
    while i < len(doc):
        if doc[i] == "`" and i + 1 < len(doc) and doc[i + 1] != "`":
            # Found opening backtick — find closing
            end = doc.find("`", i + 1)
            if end > i and end - i < 120:
                name = doc[i + 1:end].strip()
                if name and not name.startswith("/") and " " not in name:
                    names.append(name)
                i = end + 1
            else:
                i += 1
        else:
            i += 1

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            unique.append(n)

    return ", ".join(unique)


def build_additive_expansion_prompt(
    existing_doc: str,
    file_batch: str,
) -> str:
    """Build the user prompt for an additive expansion round.

    Sends section headings + already-covered names (compact) + full file batch.
    This keeps the prompt lean, leaving maximum input budget for new source files.
    """
    headings = _extract_section_headings(existing_doc)
    heading_list = "\n".join(headings)

    covered = _extract_covered_names(existing_doc)
    # Cap the covered names to avoid bloating the prompt
    if len(covered) > 8000:
        covered = covered[:8000] + " ..."

    return (
        "=== SECTION HEADINGS (existing document) ===\n"
        f"{heading_list}\n\n"
        "=== ALREADY COVERED (do not repeat these) ===\n"
        f"{covered}\n\n"
        "=== SOURCE FILES (not yet in the document) ===\n"
        f"{file_batch}\n\n"
        "Produce ONLY new entries to add to the sections listed above. "
        "Do NOT reproduce the existing document. "
        "ONLY reference names visible in the source files provided."
    )

"""File content collection and prompt assembly for project context generation.

Reads fan-in ranked source files and assembles the generation prompts
(skeleton, single-file update, headings-only) sent to the LLM.
"""

import logging
from collections import defaultdict
from pathlib import Path

from .constants import (
    _MAX_DOC_FILE_CHARS,
    _MAX_FILE_CHARS,
    _MAX_IMPORT_GRAPH_CHARS,
    _MAX_INDEX_CHARS,
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


def build_additive_expansion_prompt(
    existing_doc: str,
    file_batch: str,
) -> str:
    """Build the user prompt for an additive expansion round.

    Sends the full existing document + new source files so the LLM can
    see all current content and avoid duplicates.  The LLM returns the
    complete updated document.
    """
    return (
        "=== EXISTING DOCUMENT ===\n"
        f"{existing_doc}\n\n"
        "=== SOURCE FILES (not yet in the document) ===\n"
        f"{file_batch}\n\n"
        "Update the existing document by adding new data from the source "
        "files above. Return the complete updated document. "
        "ONLY reference names visible in the source files provided."
    )


def extract_section_headings(doc: str) -> list[str]:
    """Extract ``## `` headings from a Markdown document."""
    return [
        line.strip()
        for line in doc.split("\n")
        if line.strip().startswith("## ")
    ]


def build_skeleton_generation_prompt(
    repo_root: str,
    section_caps: dict[str, int] | None = None,
) -> str:
    """Build the user-message prompt for the skeleton generation call.

    Assembles the file tree, class/function index, import graph, and API
    endpoints — but NO source file contents.  The skeleton call produces
    the initial structural overview that is later enriched file-by-file.
    """
    caps = section_caps or {
        "index":              _MAX_INDEX_CHARS,
        "import_graph":       _MAX_IMPORT_GRAPH_CHARS,
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
    class_index = metadata.format_class_index(
        max_chars=caps["index"],
    )
    import_graph = metadata.format_import_graph(
        max_chars=caps["import_graph"],
    )
    api_endpoints = metadata.format_api_endpoints(
        max_chars=caps.get("api_endpoints", 8000),
    )

    return (
        "Analyze this repository and produce a structural overview "
        "document. Source file details will be added in later passes.\n\n"
        "=== FILE TREE ===\n"
        f"{tree}\n\n"
        "=== CLASS AND FUNCTION INDEX ===\n"
        "These are the ACTUAL class and function definitions found in "
        "the source code. Use ONLY these names in your document — "
        "do not invent others.\n\n"
        f"{class_index}\n\n"
        "=== IMPORT GRAPH ===\n"
        "These are the ACTUAL import relationships between modules. "
        "Use this to describe how modules connect — do not guess "
        "connections.\n\n"
        f"{import_graph}\n\n"
        "=== API ENDPOINTS ===\n"
        "These are the ACTUAL REST and WebSocket endpoint routes "
        "defined in the source code.\n\n"
        f"{api_endpoints}\n\n"
        "Now write the structural overview document. Remember: ONLY "
        "reference class names, function names, and files that appear "
        "above. Do NOT invent or generalize."
    )


def build_single_file_update_prompt(
    existing_doc: str,
    file_path: str,
    file_content: str,
) -> str:
    """Build the user prompt for a single-file update round.

    Sends the full existing document + one source file so the LLM can
    see all current content and avoid duplicates.  The LLM returns the
    complete updated document.
    """
    return (
        "=== EXISTING DOCUMENT ===\n"
        f"{existing_doc}\n\n"
        f"=== SOURCE FILE: {file_path} ===\n"
        f"```\n{file_content}\n```\n\n"
        "Update the existing document by incorporating any new "
        "information from this source file. Return the complete "
        "updated document. ONLY reference names visible in the "
        "source file provided."
    )


def build_single_file_headings_prompt(
    section_headings: list[str],
    file_path: str,
    file_content: str,
) -> str:
    """Build a headings-only prompt for a single-file update round.

    Used when the existing document is too large to fit in the context
    budget alongside the source file.  Sends only the section headings
    instead of the full document.  The LLM returns ONLY new entries
    organized by heading (merged back programmatically).
    """
    headings_list = "\n".join(f"- {h}" for h in section_headings)
    return (
        "=== EXISTING DOCUMENT HEADINGS ===\n"
        f"{headings_list}\n\n"
        f"=== SOURCE FILE: {file_path} ===\n"
        f"```\n{file_content}\n```\n\n"
        "Extract new entries from this source file and place each "
        "entry under the correct heading. Output ONLY the new entries, "
        "organized by heading. Skip headings where the file adds "
        "nothing new."
    )



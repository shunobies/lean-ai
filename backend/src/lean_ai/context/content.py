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
        "=== FILE TREE ===\n"
        f"{tree}\n\n"
        "=== CLASS AND FUNCTION INDEX ===\n"
        f"{class_index}\n\n"
        "=== IMPORT GRAPH ===\n"
        f"{import_graph}\n\n"
        "=== API ENDPOINTS ===\n"
        f"{api_endpoints}\n\n"
        "Write the structural overview document using only the data above."
    )


def build_deterministic_skeleton(
    repo_root: str,
    section_caps: dict[str, int] | None = None,
) -> str:
    """Build a project context skeleton deterministically — no LLM call.

    Produces a Markdown document from tree-sitter metadata (class/function
    index, import graph, file tree) with all structural data pre-populated.
    Narrative sections (Architecture Overview, Data Flow, Conventions) are
    left as placeholders for Phase 2 file-by-file enrichment.
    """
    caps = section_caps or {
        "index": _MAX_INDEX_CHARS,
        "import_graph": _MAX_IMPORT_GRAPH_CHARS,
    }

    try:
        from lean_ai.indexer.tree import list_repo_tree
        entries = list_repo_tree(repo_root)
    except Exception:
        entries = None

    metadata = extract_metadata_cached(repo_root, entries=entries)

    # ── Detect entry points and key files ──
    entry_points = _get_entry_points()
    key_file_names = set(_get_key_files())
    found_entry_points: list[str] = []
    found_key_files: list[str] = []
    all_file_paths: set[str] = set()
    if entries:
        for entry in entries:
            norm = entry.path.replace("\\", "/")
            all_file_paths.add(norm)
            name = Path(norm).name
            if name in entry_points:
                found_entry_points.append(norm)
            if name in key_file_names:
                found_key_files.append(norm)

    sections: list[str] = ["# Project Context\n"]

    # ── Architecture Overview (placeholder with entry points) ──
    arch_lines = ["## Architecture Overview\n"]
    if found_entry_points:
        arch_lines.append(
            "Entry points: "
            + ", ".join(f"`{p}`" for p in sorted(found_entry_points))
        )
    if found_key_files:
        arch_lines.append(
            "Key files: "
            + ", ".join(f"`{p}`" for p in sorted(found_key_files))
        )
    if not found_entry_points and not found_key_files:
        arch_lines.append("No data extracted yet.")
    sections.append("\n".join(arch_lines))

    # ── Module Map (directories with files + defs) ──
    module_lines = ["## Module Map\n"]
    dir_files: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)
    for fpath in sorted(metadata.files):
        parent = str(Path(fpath).parent).replace("\\", "/")
        if parent == ".":
            parent = "(root)"
        defs = metadata.files[fpath].class_function_defs
        dir_files[parent].append((Path(fpath).name, defs))

    max_index = caps.get("index", _MAX_INDEX_CHARS)
    total_chars = 0
    for dir_name in sorted(dir_files):
        dir_block = [f"### {dir_name}/"]
        for filename, defs in dir_files[dir_name]:
            if defs:
                # Show up to 5 defs per file to keep it compact.
                shown = defs[:5]
                suffix = f", ... +{len(defs) - 5} more" if len(defs) > 5 else ""
                dir_block.append(
                    f"- `{filename}` — {', '.join(f'`{d}`' for d in shown)}{suffix}"
                )
            else:
                dir_block.append(f"- `{filename}`")
        block_text = "\n".join(dir_block)
        total_chars += len(block_text)
        if total_chars > max_index:
            module_lines.append("... (truncated)")
            break
        module_lines.append(block_text)

    if len(module_lines) == 1:
        module_lines.append("No data extracted yet.")
    sections.append("\n\n".join(module_lines))

    # ── Key Abstractions (classes sorted by fan-in) ──
    abs_lines = ["## Key Abstractions\n"]
    class_entries: list[tuple[str, str, int]] = []
    for fpath, fmeta in metadata.files.items():
        fan = metadata.fan_in.get(fpath, 0)
        for defn in fmeta.class_function_defs:
            if defn.startswith("class "):
                class_entries.append((defn, fpath, fan))
    class_entries.sort(key=lambda x: (-x[2], x[0]))
    for defn, fpath, fan in class_entries[:50]:
        fan_note = f" — fan-in: {fan}" if fan > 0 else ""
        abs_lines.append(f"- `{defn}` (`{fpath}`){fan_note}")
    if len(abs_lines) == 1:
        abs_lines.append("No data extracted yet.")
    sections.append("\n".join(abs_lines))

    # ── Data Flow (placeholder) ──
    sections.append("## Data Flow\n\nNo data extracted yet.")

    # ── Conventions (placeholder) ──
    sections.append("## Conventions\n\nNo data extracted yet.")

    # ── Integration Points (directory-level import summary) ──
    int_lines = ["## Integration Points\n"]
    dir_imports: dict[str, set[str]] = defaultdict(set)
    max_graph = caps.get("import_graph", _MAX_IMPORT_GRAPH_CHARS)
    for fpath, fmeta in metadata.files.items():
        src_dir = str(Path(fpath).parent).replace("\\", "/")
        for imp in fmeta.imports:
            # Extract the top-level module from the import statement.
            parts = imp.strip().split(".")
            target = parts[0].strip()
            if target and target != src_dir:
                dir_imports[src_dir].add(target)

    graph_chars = 0
    for src_dir in sorted(dir_imports):
        targets = sorted(dir_imports[src_dir])
        if targets:
            line = f"- `{src_dir}/` → {', '.join(f'`{t}`' for t in targets)}"
            graph_chars += len(line)
            if graph_chars > max_graph:
                int_lines.append("... (truncated)")
                break
            int_lines.append(line)
    if len(int_lines) == 1:
        int_lines.append("No data extracted yet.")
    sections.append("\n".join(int_lines))

    # ── API Surface ──
    api_text = metadata.format_api_endpoints(
        max_chars=caps.get("api_endpoints", 8000),
    )
    if api_text and "(no API endpoints found)" not in api_text:
        sections.append(f"## API Surface\n\n{api_text}")
    else:
        sections.append("## API Surface\n\nNo data extracted yet.")

    return "\n\n".join(sections) + "\n"


def build_single_file_update_prompt(
    existing_doc: str,
    section_headings: list[str],
    file_path: str,
    file_content: str,
) -> str:
    """Build the user prompt for a single-file update round.

    Sends section headings + full existing document + one source file.
    The LLM returns ONLY net-new additions organized by heading
    (merged back programmatically).
    """
    headings_list = "\n".join(f"- {h}" for h in section_headings)
    return (
        "=== EXISTING SECTION HEADINGS ===\n"
        f"{headings_list}\n\n"
        "=== EXISTING CONTEXT TEXT ===\n"
        f"{existing_doc}\n\n"
        f"=== SOURCE FILE: {file_path} ===\n"
        f"```\n{file_content}\n```"
    )


def build_single_file_headings_prompt(
    section_headings: list[str],
    file_path: str,
    file_content: str,
) -> str:
    """Build a headings-only prompt for a single-file update round.

    Used when the existing document is too large to fit in the context
    budget alongside the source file.  Sends only the section headings
    (no document text).  The LLM returns ONLY net-new additions
    organized by heading (merged back programmatically).
    """
    headings_list = "\n".join(f"- {h}" for h in section_headings)
    return (
        "=== EXISTING SECTION HEADINGS ===\n"
        f"{headings_list}\n\n"
        f"=== SOURCE FILE: {file_path} ===\n"
        f"```\n{file_content}\n```"
    )



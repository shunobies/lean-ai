"""Repository metadata extraction and disk-level caching.

Single-pass metadata extraction reads each source file once to collect
class/function definitions, import graphs, API endpoints, and fan-in
rankings.  Results are cached to ``.lean_ai/metadata_cache.json``
with per-file mtime manifest for incremental updates.

Uses tree-sitter AST extraction instead of regex.
"""

import json as _json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from lean_ai.languages.definitions import FileMetadata as _FileMetadata
from lean_ai.languages.extractor import extract_file_metadata as _ts_extract
from lean_ai.languages.registry import get_registry as _get_registry

from .constants import (
    _MAX_IMPORT_GRAPH_CHARS,
    _MAX_INDEX_CHARS,
    _get_source_exts,
)

logger = logging.getLogger(__name__)


@dataclass
class _RepoMetadata:
    """All metadata extracted from the repository in a single pass."""
    files: dict[str, _FileMetadata] = field(default_factory=dict)
    fan_in: dict[str, int] = field(default_factory=dict)

    def format_class_index(self, max_chars: int = _MAX_INDEX_CHARS) -> str:
        """Format class/function index section."""
        lines: list[str] = []
        total = 0
        for fpath in sorted(self.files):
            defs = self.files[fpath].class_function_defs
            if not defs:
                continue
            block = f"{fpath}:\n" + "\n".join(defs)
            if total + len(block) > max_chars:
                lines.append("... (truncated)")
                break
            lines.append(block)
            total += len(block)
        return "\n\n".join(lines) if lines else "(no classes or functions found)"

    def format_import_graph(self, max_chars: int = _MAX_IMPORT_GRAPH_CHARS) -> str:
        """Format import graph section."""
        lines: list[str] = []
        total = 0
        for fpath in sorted(self.files):
            imports = self.files[fpath].imports
            if not imports:
                continue
            block = f"{fpath} imports:\n" + "\n".join(imports)
            if total + len(block) > max_chars:
                lines.append("... (truncated)")
                break
            lines.append(block)
            total += len(block)
        return "\n\n".join(lines) if lines else "(no imports found)"

    def format_api_endpoints(self, max_chars: int = _MAX_INDEX_CHARS) -> str:
        """Format API endpoints section."""
        lines: list[str] = []
        total = 0
        for fpath in sorted(self.files):
            endpoints = self.files[fpath].endpoints
            if not endpoints:
                continue
            block = f"{fpath}:\n" + "\n".join(endpoints)
            if total + len(block) > max_chars:
                lines.append("... (truncated)")
                break
            lines.append(block)
            total += len(block)
        return "\n\n".join(lines) if lines else "(no API endpoints found)"


def _extract_file_metadata(text: str, ext: str) -> _FileMetadata:
    """Extract class/function defs, imports, and API endpoints from one file.

    Delegates to the tree-sitter extraction engine.
    """
    lang = _get_registry().get_language(ext)
    if lang is None:
        return _FileMetadata()
    return _ts_extract(text, lang)


def _is_test_file(path: str) -> bool:
    """Return True for test files/directories."""
    try:
        return _get_registry().is_test_file(path)
    except Exception:
        norm = path.replace("\\", "/")
        return (
            "/tests/" in norm
            or norm.startswith("tests/")
            or "/test_" in norm
            or norm.startswith("test_")
        )


def _discover_source_prefixes(file_paths: set[str]) -> list[str]:
    """Discover directory prefixes that contain package source files.

    Scans the file tree for package marker files (e.g., ``__init__.py``
    for Python) and derives the prefix to strip when resolving module paths.
    """
    try:
        markers = _get_registry().all_package_markers()
    except Exception:
        markers = {"__init__.py"}

    prefixes: set[str] = {""}
    for fpath in file_paths:
        matched_marker = False
        for marker in markers:
            if fpath.endswith("/" + marker):
                matched_marker = True
                break
        if not matched_marker:
            continue
        pkg_dir = fpath.rsplit("/", 1)[0]
        parts = pkg_dir.split("/")
        for i in range(len(parts)):
            candidate_prefix = "/".join(parts[:i]) + "/" if i > 0 else ""
            prefixes.add(candidate_prefix)
    return sorted(prefixes, key=len, reverse=True)


def _resolve_fan_in(
    metadata: "_RepoMetadata",
    file_paths: set[str],
    source_prefixes: list[str],
) -> dict[str, int]:
    """Resolve import fan-in counts using language-aware strategies."""
    registry = _get_registry()
    fan_in: dict[str, int] = defaultdict(int)

    for fpath, fmeta in metadata.files.items():
        if not fmeta.imported_modules:
            continue
        ext = Path(fpath).suffix.lower()
        lang = registry.get_language(ext)
        if not lang or not lang.fan_in or lang.fan_in.strategy == "none":
            continue

        for module in fmeta.imported_modules:
            if lang.fan_in.strategy == "dot_to_slash":
                suffix = lang.fan_in.suffix or ".py"
                for base in source_prefixes:
                    candidate = base + module.replace(".", "/") + suffix
                    candidate = candidate.replace("\\", "/")
                    if candidate in file_paths:
                        fan_in[candidate] += 1
                        break

    return dict(fan_in)


def _extract_all_metadata(repo_root: str, entries=None) -> _RepoMetadata:
    """Extract metadata from the repository in a single pass over source files."""
    if entries is None:
        try:
            from lean_ai.indexer.tree import list_repo_tree
            entries = list_repo_tree(repo_root)
        except Exception:
            return _RepoMetadata()

    root = Path(repo_root)
    file_paths = {e.path.replace("\\", "/") for e in entries}
    metadata = _RepoMetadata()
    source_exts = _get_source_exts()

    for entry in sorted(entries, key=lambda e: e.path):
        ext = Path(entry.path).suffix.lower()
        if ext not in source_exts:
            continue
        if _is_test_file(entry.path):
            continue

        try:
            text = (root / entry.path).read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        file_meta = _extract_file_metadata(text, ext)
        metadata.files[entry.path] = file_meta

    source_prefixes = _discover_source_prefixes(file_paths)
    metadata.fan_in = _resolve_fan_in(metadata, file_paths, source_prefixes)

    logger.info(
        "Single-pass metadata: %d files, %d with defs, %d with imports, %d with endpoints",
        len(metadata.files),
        sum(1 for f in metadata.files.values() if f.class_function_defs),
        sum(1 for f in metadata.files.values() if f.imports),
        sum(1 for f in metadata.files.values() if f.endpoints),
    )

    return metadata


# ---------------------------------------------------------------------------
# Metadata disk cache
# ---------------------------------------------------------------------------

_METADATA_CACHE_FILE = "metadata_cache.json"


def _load_metadata_cache(repo_root: str) -> dict | None:
    """Load cached metadata from ``.lean_ai/metadata_cache.json``."""
    cache_path = Path(repo_root) / ".lean_ai" / _METADATA_CACHE_FILE
    try:
        if not cache_path.is_file():
            return None
        return _json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_metadata_cache(
    repo_root: str,
    metadata: _RepoMetadata,
    file_mtimes: dict[str, float],
) -> None:
    """Persist extracted metadata to disk for incremental updates."""
    cache_dir = Path(repo_root) / ".lean_ai"
    cache_dir.mkdir(parents=True, exist_ok=True)

    data = {
        "version": 2,
        "manifest": file_mtimes,
        "files": {},
        "fan_in": metadata.fan_in,
    }
    for fpath, fmeta in metadata.files.items():
        data["files"][fpath] = {
            "class_function_defs": fmeta.class_function_defs,
            "imports": fmeta.imports,
            "endpoints": fmeta.endpoints,
            "imported_modules": fmeta.imported_modules,
        }

    (cache_dir / _METADATA_CACHE_FILE).write_text(
        _json.dumps(data, indent=1),
        encoding="utf-8",
    )
    logger.debug("Metadata cache saved: %d files", len(data["files"]))


def _get_file_mtimes(repo_root: str, entries) -> dict[str, float]:
    """Build a mapping of source file paths to their modification times."""
    root = Path(repo_root)
    source_exts = _get_source_exts()
    mtimes: dict[str, float] = {}
    for entry in entries:
        ext = Path(entry.path).suffix.lower()
        if ext not in source_exts:
            continue
        if _is_test_file(entry.path):
            continue
        try:
            mtimes[entry.path] = os.path.getmtime(root / entry.path)
        except OSError:
            pass
    return mtimes


def extract_metadata_cached(repo_root: str, entries=None) -> _RepoMetadata:
    """Extract repository metadata with disk-level caching.

    On first run, performs a full single-pass extraction and caches the
    result.  On subsequent runs, only re-extracts metadata for files
    whose mtime has changed since the last cache write.
    """
    if entries is None:
        try:
            from lean_ai.indexer.tree import list_repo_tree
            entries = list_repo_tree(repo_root)
        except Exception:
            return _RepoMetadata()

    current_mtimes = _get_file_mtimes(repo_root, entries)
    cache = _load_metadata_cache(repo_root)

    if cache and cache.get("version") == 2:
        cached_manifest = cache.get("manifest", {})
        cached_files = cache.get("files", {})

        changed_files: set[str] = set()
        for fpath, mtime in current_mtimes.items():
            if fpath not in cached_manifest or cached_manifest[fpath] != mtime:
                changed_files.add(fpath)

        deleted_files = set(cached_manifest) - set(current_mtimes)

        if not changed_files and not deleted_files:
            logger.info("Metadata cache HIT: all %d files unchanged", len(cached_files))
            metadata = _RepoMetadata()
            for fpath, fdata in cached_files.items():
                metadata.files[fpath] = _FileMetadata(
                    class_function_defs=fdata.get("class_function_defs", []),
                    imports=fdata.get("imports", []),
                    endpoints=fdata.get("endpoints", []),
                    imported_modules=fdata.get("imported_modules", []),
                )
            metadata.fan_in = cache.get("fan_in", {})
            return metadata

        logger.info(
            "Metadata cache PARTIAL: %d changed, %d deleted, %d cached",
            len(changed_files), len(deleted_files), len(cached_files) - len(deleted_files),
        )

        root = Path(repo_root)
        file_paths = {e.path.replace("\\", "/") for e in entries}
        metadata = _RepoMetadata()

        for fpath, fdata in cached_files.items():
            if fpath in deleted_files:
                continue
            if fpath in changed_files:
                continue
            metadata.files[fpath] = _FileMetadata(
                class_function_defs=fdata.get("class_function_defs", []),
                imports=fdata.get("imports", []),
                endpoints=fdata.get("endpoints", []),
                imported_modules=fdata.get("imported_modules", []),
            )

        for fpath in changed_files:
            ext = Path(fpath).suffix.lower()
            try:
                text = (root / fpath).read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            metadata.files[fpath] = _extract_file_metadata(text, ext)

        source_prefixes = _discover_source_prefixes(file_paths)
        metadata.fan_in = _resolve_fan_in(metadata, file_paths, source_prefixes)

        _save_metadata_cache(repo_root, metadata, current_mtimes)
        return metadata

    logger.info("Metadata cache MISS: full extraction for %d files", len(current_mtimes))
    metadata = _extract_all_metadata(repo_root, entries=entries)
    _save_metadata_cache(repo_root, metadata, current_mtimes)
    return metadata

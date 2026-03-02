"""Gitignore-aware repository tree listing."""

import logging
from dataclasses import dataclass
from pathlib import Path

import pathspec

logger = logging.getLogger(__name__)


@dataclass
class FileEntry:
    path: str  # Relative to repo root
    size: int
    extension: str


SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".lean_ai_index", ".lean_ai_knowledge_index", ".lean_ai",
    "dist", "build", ".next", ".nuxt", "target",
    ".idea", ".vscode",
})

BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
    ".mp3", ".mp4", ".avi", ".mov", ".wav", ".flac",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a",
    ".pyc", ".pyo", ".class", ".wasm",
    ".db", ".sqlite", ".sqlite3",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".bin", ".dat", ".pkl", ".npy", ".npz",
    ".lock",
})

SKIP_FILES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
})


def _load_gitignore(repo_root: Path) -> pathspec.PathSpec | None:
    gitignore = repo_root / ".gitignore"
    if gitignore.exists():
        try:
            return pathspec.PathSpec.from_lines(
                "gitwildmatch", gitignore.read_text().splitlines(),
            )
        except Exception:
            pass
    return None


def list_repo_tree(repo_root: str) -> list[FileEntry]:
    """Walk a repository and return all indexable source files."""
    root = Path(repo_root)
    spec = _load_gitignore(root)
    entries: list[FileEntry] = []

    for item in root.rglob("*"):
        if not item.is_file():
            continue

        rel = item.relative_to(root)
        parts = rel.parts

        # Skip excluded directories
        if any(p in SKIP_DIRS for p in parts):
            continue

        rel_str = str(rel)

        # Skip gitignored files
        if spec and spec.match_file(rel_str):
            continue

        # Skip binary files
        ext = item.suffix.lower()
        if ext in BINARY_EXTENSIONS:
            continue

        # Skip specific files
        if item.name in SKIP_FILES:
            continue

        try:
            size = item.stat().st_size
        except OSError:
            continue

        entries.append(FileEntry(path=rel_str, size=size, extension=ext))

    return entries

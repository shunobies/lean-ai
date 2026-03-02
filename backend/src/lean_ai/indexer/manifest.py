"""File hash manifest for incremental indexing."""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class FileRecord:
    sha256: str
    chunk_count: int = 0


@dataclass
class Manifest:
    version: int = 1
    created_at: str = ""
    commit_hash: str = ""
    files: dict[str, FileRecord] = field(default_factory=dict)


@dataclass
class DiffResult:
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


def hash_file_content(file_path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def load_manifest(index_dir: Path) -> Manifest | None:
    """Load manifest from index directory, return None if missing/corrupt."""
    manifest_path = index_dir / "_manifest.json"
    if not manifest_path.exists():
        return None
    try:
        data = json.loads(manifest_path.read_text())
        files = {}
        for path, record in data.get("files", {}).items():
            files[path] = FileRecord(
                sha256=record.get("sha256", ""),
                chunk_count=record.get("chunk_count", 0),
            )
        return Manifest(
            version=data.get("version", 1),
            created_at=data.get("created_at", ""),
            commit_hash=data.get("commit_hash", ""),
            files=files,
        )
    except Exception as e:
        logger.warning("Failed to load manifest: %s", e)
        return None


def save_manifest(
    index_dir: Path,
    manifest: Manifest,
) -> None:
    """Save manifest to index directory."""
    manifest_path = index_dir / "_manifest.json"
    data = {
        "version": manifest.version,
        "created_at": manifest.created_at or datetime.now(timezone.utc).isoformat(),
        "commit_hash": manifest.commit_hash,
        "files": {
            path: {"sha256": rec.sha256, "chunk_count": rec.chunk_count}
            for path, rec in manifest.files.items()
        },
    }
    manifest_path.write_text(json.dumps(data, indent=2))


def compute_diff(
    current_files: dict[str, str],  # path -> sha256
    old_manifest: Manifest,
) -> DiffResult:
    """Compare current file hashes against a stored manifest."""
    result = DiffResult()

    for path, sha in current_files.items():
        old = old_manifest.files.get(path)
        if old is None:
            result.added.append(path)
        elif old.sha256 != sha:
            result.modified.append(path)
        else:
            result.unchanged.append(path)

    for path in old_manifest.files:
        if path not in current_files:
            result.deleted.append(path)

    return result

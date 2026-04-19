"""Per-session structured file observations for Phase 2 exploration.

File-based state: ``.lean_ai/observations/{session_id}.json`` in the target
project. Session-scoped: persists until ``/approve`` or ``/reject`` closes
the session. Survives crashes for recovery.

The exploration model calls ``record_file_observation`` whenever it decides
a file is relevant. The synthesis step at the end of Phase 2 reads the
accumulated observations to produce a validated ``FileSummary``. This
replaces the prior design where the model had to transcribe file content
into free-form prose output — retention stops depending on the model's
memory.

Unlike the scratchpad (overwrite-based volatile memory) or the journal
(append-only prose), observations are structured records upserted by
``file_path``: a second observation for the same path replaces the first.
"""

import json
import logging
from pathlib import Path

from lean_ai.tools.executor import ToolResult

logger = logging.getLogger(__name__)


def observations_path(repo_root: str, session_id: str) -> Path:
    """Return the absolute path to the per-session observations file."""
    return (
        Path(repo_root) / ".lean_ai" / "observations" / f"{session_id}.json"
    )


def _load_all(path: Path) -> list[dict]:
    """Load the observations list, returning [] if absent or unreadable."""
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        logger.warning(
            "Observations file %s is not a list — discarding", path,
        )
        return []
    except Exception:
        logger.warning(
            "Failed to read observations at %s", path, exc_info=True,
        )
        return []


async def record_observation(
    *,
    file_path: str,
    role: str,
    reason: str,
    relevant_sections: str = "",
    key_snippets: list[str] | None = None,
    repo_root: str,
    session_id: str,
) -> ToolResult:
    """Upsert a single file observation by ``file_path``.

    If an observation already exists for this path, the new observation
    replaces it in place (preserving list order). Otherwise the new
    observation is appended.
    """
    file_path = (file_path or "").strip()
    if not file_path:
        return ToolResult(
            success=False,
            error="record_file_observation requires a non-empty file_path.",
        )
    allowed_roles = {"modify", "create", "reference", "missing"}
    if role not in allowed_roles:
        return ToolResult(
            success=False,
            error=(
                f"role must be one of {sorted(allowed_roles)}, got {role!r}."
            ),
        )
    if not (reason or "").strip():
        return ToolResult(
            success=False,
            error="record_file_observation requires a non-empty reason.",
        )

    path = observations_path(repo_root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    observations = _load_all(path)
    new_entry = {
        "file_path": file_path,
        "role": role,
        "reason": reason.strip(),
        "relevant_sections": (relevant_sections or "").strip(),
        "key_snippets": list(key_snippets or []),
    }

    replaced = False
    for i, existing in enumerate(observations):
        if existing.get("file_path") == file_path:
            observations[i] = new_entry
            replaced = True
            break
    if not replaced:
        observations.append(new_entry)

    path.write_text(
        json.dumps(observations, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    action = "Replaced" if replaced else "Recorded"
    logger.info(
        "%s observation: %s (role=%s) [%d total]",
        action, file_path, role, len(observations),
    )
    return ToolResult(
        success=True,
        output=(
            f"{action} observation for {file_path} (role={role}). "
            f"{len(observations)} observation(s) recorded so far."
        ),
    )


def read_observations(repo_root: str, session_id: str) -> list[dict]:
    """Return the current list of observations. Empty list if none."""
    return _load_all(observations_path(repo_root, session_id))


def delete_observations(repo_root: str, session_id: str) -> None:
    """Remove the observations file (cleanup on session close)."""
    path = observations_path(repo_root, session_id)
    if path.is_file():
        path.unlink()
        logger.info("Observations deleted: %s", path)

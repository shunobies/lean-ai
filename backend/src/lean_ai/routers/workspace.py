"""Workspace-level utility endpoints.

Houses small, synchronous REST operations that don't fit the
LLM-driven generation/chat/workflow routers.  Current contents:

* ``POST /workspace/convert-docx`` — deterministic ``.docx`` → markdown
  conversion.  Used by the ``/interview-prep`` slash command to produce a
  faithful markdown copy of a user's resume before the request model is
  invoked for research and tailoring.
* ``POST /workspace/log-applied`` — deterministic application logging.
  Appends a row to ``applications.md`` (the job-search scaffold's
  tracker) and optionally commits the per-application folder to git.
"""

from __future__ import annotations

import logging
import subprocess
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

workspace_router = APIRouter()


class ConvertDocxRequest(BaseModel):
    repo_root: str
    source_path: str
    output_filename: str
    overwrite: bool = False


class ConvertDocxResponse(BaseModel):
    success: bool
    output_path: str
    content_length: int
    line_count: int


def _resolve_output_path(repo_root: Path, output_filename: str) -> Path:
    """Resolve ``output_filename`` inside *repo_root*, rejecting traversal."""
    if not output_filename:
        raise HTTPException(status_code=400, detail="output_filename is required.")
    resolved = (repo_root / output_filename).resolve()
    if not resolved.is_relative_to(repo_root.resolve()):
        raise HTTPException(
            status_code=400,
            detail=f"output_filename escapes repo_root: {output_filename}",
        )
    return resolved


def _resolve_source_path(source_path: str) -> Path:
    """Resolve a docx source path.

    Accepts absolute paths (resumes commonly live outside the workspace
    — e.g. the user's ``Documents`` folder).  The caller is expected to
    have obtained this path from an authenticated local user action
    (e.g. the VS Code file picker).
    """
    if not source_path:
        raise HTTPException(status_code=400, detail="source_path is required.")
    resolved = Path(source_path).expanduser().resolve()
    if not resolved.exists():
        raise HTTPException(
            status_code=404,
            detail=f"source_path does not exist: {source_path}",
        )
    if not resolved.is_file():
        raise HTTPException(
            status_code=400,
            detail=f"source_path is not a file: {source_path}",
        )
    if resolved.suffix.lower() != ".docx":
        raise HTTPException(
            status_code=400,
            detail=(
                "Only .docx is supported. For legacy .doc files, convert "
                "first with: libreoffice --headless --convert-to docx <file>"
            ),
        )
    return resolved


@workspace_router.post("/workspace/convert-docx", response_model=ConvertDocxResponse)
async def convert_docx(req: ConvertDocxRequest) -> ConvertDocxResponse:
    """Convert a ``.docx`` file to markdown and write it into the workspace.

    Deterministic — no LLM involvement.  Output is byte-identical across
    runs for the same input.
    """
    repo_root = Path(req.repo_root).expanduser().resolve()
    if not repo_root.exists() or not repo_root.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"repo_root is not a directory: {req.repo_root}",
        )

    source = _resolve_source_path(req.source_path)
    output = _resolve_output_path(repo_root, req.output_filename)

    if output.exists() and not req.overwrite:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "exists",
                "output_path": str(output),
                "message": (
                    f"{req.output_filename} already exists. "
                    "Retry with overwrite=true or pick a different name."
                ),
            },
        )

    try:
        from lean_ai.reference.readers.docx import docx_to_markdown
    except ImportError as e:  # pragma: no cover — exercised via tests that monkeypatch
        raise HTTPException(
            status_code=415,
            detail=(
                "Cannot read .docx: python-docx not installed. "
                "Install with: pip install 'lean-ai[reference]'"
            ),
        ) from e

    try:
        markdown = docx_to_markdown(source)
    except ImportError as e:
        raise HTTPException(
            status_code=415,
            detail=(
                "Cannot read .docx: python-docx not installed. "
                "Install with: pip install 'lean-ai[reference]'"
            ),
        ) from e
    except Exception as e:
        logger.warning("docx_to_markdown failed for %s: %s", source, e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to parse .docx: {e}",
        ) from e

    output.parent.mkdir(parents=True, exist_ok=True)
    # Explicit newline="\n" so Windows runs still produce LF-only output
    # (git-friendly, matches the extension's expectation).
    output.write_text(markdown, encoding="utf-8", newline="\n")

    line_count = markdown.count("\n") + (1 if markdown and not markdown.endswith("\n") else 0)
    return ConvertDocxResponse(
        success=True,
        output_path=str(output),
        content_length=len(markdown),
        line_count=line_count,
    )


# ── POST /workspace/log-applied ──────────────────────────────────────


class LogAppliedRequest(BaseModel):
    repo_root: str
    slug: str
    company: str
    role: str
    source: str = "other"
    next_action: str = "Follow up in 7 days"


class LogAppliedResponse(BaseModel):
    success: bool
    tracker_updated: bool
    tracker_path: str | None
    commit_attempted: bool
    commit_sha: str | None
    commit_error: str | None


_TRACKER_ROW_PREFIX = "| "


def _append_tracker_row(tracker: Path, row: str) -> None:
    """Append *row* to the applications-tracker markdown table.

    Inserts after the last existing table row so trailing prose stays
    below the table.  If no table rows exist, appends at EOF.
    """
    original = tracker.read_text(encoding="utf-8")
    lines = original.splitlines()
    last_row_idx = -1
    for i, line in enumerate(lines):
        if line.startswith(_TRACKER_ROW_PREFIX):
            last_row_idx = i

    if last_row_idx == -1:
        # No table found — append at EOF with a leading newline if needed.
        sep = "" if original.endswith("\n") or not original else "\n"
        tracker.write_text(original + sep + row + "\n", encoding="utf-8", newline="\n")
        return

    new_lines = [*lines[: last_row_idx + 1], row, *lines[last_row_idx + 1 :]]
    tracker.write_text("\n".join(new_lines) + "\n", encoding="utf-8", newline="\n")


def _git_commit_application(
    repo_root: Path, slug: str, company: str, role: str,
) -> tuple[str | None, str | None]:
    """Stage the application folder + tracker, commit, return (sha, error).

    Skips silently when the workspace is not a git repo.  Any other
    failure is returned as the error string so the caller can surface
    it without aborting the tracker update.
    """
    if not (repo_root / ".git").exists():
        return None, "not a git repository"

    # Only pass paths that exist — git add errors on missing pathspecs.
    paths_to_add: list[str] = []
    if (repo_root / "applications" / slug).exists():
        paths_to_add.append(f"applications/{slug}")
    if (repo_root / "applications.md").exists():
        paths_to_add.append("applications.md")
    if not paths_to_add:
        return None, "nothing to stage"

    try:
        subprocess.run(
            ["git", "-C", str(repo_root), "add", *paths_to_add],
            check=True, capture_output=True, timeout=15,
        )
        result = subprocess.run(
            ["git", "-C", str(repo_root), "diff", "--cached", "--quiet"],
            timeout=15,
        )
        if result.returncode == 0:
            return None, "nothing staged to commit"

        subprocess.run(
            [
                "git", "-C", str(repo_root), "commit",
                "-m", f"Applied: {company} — {role}",
            ],
            check=True, capture_output=True, timeout=15,
        )
        sha = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=True, capture_output=True, timeout=15, text=True,
        ).stdout.strip()
        return sha, None
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode("utf-8", errors="replace").strip()
        return None, stderr or str(e)
    except subprocess.TimeoutExpired:
        return None, "git command timed out"


@workspace_router.post("/workspace/log-applied", response_model=LogAppliedResponse)
async def log_applied(req: LogAppliedRequest) -> LogAppliedResponse:
    """Append a tracker row + commit the application folder to git.

    Deterministic — no LLM calls.  Intended to be called from the
    ``/log-applied`` extension command (or from the ``/interview-prep``
    workflow in a future revision).
    """
    repo_root = Path(req.repo_root).expanduser().resolve()
    if not repo_root.exists() or not repo_root.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"repo_root is not a directory: {req.repo_root}",
        )

    slug = req.slug.strip()
    if not slug or "/" in slug or ".." in slug.split("/"):
        raise HTTPException(
            status_code=400,
            detail="slug must be a single path component (no slashes, no traversal).",
        )

    app_dir = (repo_root / "applications" / slug).resolve()
    if not app_dir.is_relative_to(repo_root.resolve()):
        raise HTTPException(status_code=400, detail="slug resolves outside repo_root.")
    if not app_dir.exists() or not app_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail=(
                f"applications/{slug}/ does not exist in repo_root. "
                "Run /interview-prep first to create the application folder."
            ),
        )

    tracker = repo_root / "applications.md"
    tracker_updated = False
    if tracker.exists():
        today = date.today().isoformat()
        row = (
            f"| {today} | {req.company} | {req.role} | {req.source} | "
            f"applied | — | {req.next_action} | "
            f"[{slug}](applications/{slug}/) |"
        )
        try:
            _append_tracker_row(tracker, row)
            tracker_updated = True
        except Exception as e:
            logger.warning("Failed to append tracker row: %s", e)

    commit_sha, commit_error = _git_commit_application(
        repo_root, slug, req.company, req.role,
    )

    return LogAppliedResponse(
        success=True,
        tracker_updated=tracker_updated,
        tracker_path=str(tracker) if tracker.exists() else None,
        commit_attempted=True,
        commit_sha=commit_sha,
        commit_error=commit_error,
    )

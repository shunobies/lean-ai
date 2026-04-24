"""Workspace-level utility endpoints.

Houses small, synchronous REST operations that don't fit the
LLM-driven generation/chat/workflow routers.  Current contents:

* ``POST /workspace/convert-docx`` — deterministic ``.docx`` → markdown
  conversion.  Used by the ``/interview-prep`` slash command to produce a
  faithful markdown copy of a user's resume before the request model is
  invoked for research and tailoring.
"""

from __future__ import annotations

import logging
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

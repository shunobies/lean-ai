"""Shared HTML stripping and content pagination utilities.

Used by both internet.py (URL fetching) and wiki.py (MediaWiki pages)
to avoid duplicating these functions.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from bs4 import BeautifulSoup


def strip_html(raw: str) -> str:
    """Remove HTML tags, extract text content."""
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def save_and_paginate(
    text: str,
    identifier: str,
    repo_root: str,
    max_lines: int = 500,
) -> str:
    """Save long content to a file and return the first page.

    Short content (<= *max_lines*) is returned as-is.  Long content is
    written to ``.lean_ai/fetched/{hash}.txt`` and only the first
    *max_lines* lines are returned, with an instruction telling the
    LLM how to read the rest via ``read_file``.
    """
    lines = text.splitlines()
    total = len(lines)

    if total <= max_lines:
        return text

    id_hash = hashlib.sha256(identifier.encode()).hexdigest()[:12]
    fetched_dir = Path(repo_root) / ".lean_ai" / "fetched"
    fetched_dir.mkdir(parents=True, exist_ok=True)
    rel_path = f".lean_ai/fetched/{id_hash}.txt"
    file_path = Path(repo_root) / rel_path
    file_path.write_text(text, encoding="utf-8")

    preview = "\n".join(lines[:max_lines])
    next_start = max_lines + 1
    next_end = min(max_lines + 500, total)
    return (
        f"{preview}\n\n"
        f"[Showing lines 1-{max_lines} of {total}. "
        f"Remaining content saved to {rel_path}.\n"
        f"To continue reading, call: "
        f'read_file path="{rel_path}" start_line={next_start} end_line={next_end}]'
    )

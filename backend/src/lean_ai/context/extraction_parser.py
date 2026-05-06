"""Structured output schema for LLM per-file extraction + the deterministic
skeleton parser.

The LLM returns a ``ContextExtractionResult`` (Pydantic) via
``chat_structured()``; Python converts entries into DB tuples directly —
no regex or heuristic parsing of markdown.

The deterministic Phase 0 skeleton still produces markdown (built from
tree-sitter metadata in ``content.py``) and is parsed back into tuples by
``parse_skeleton_output()`` here.
"""

import hashlib
import logging
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_VALID_SECTIONS = frozenset(
    {
        "Architecture Overview",
        "Module Map",
        "Key Abstractions",
        "API Surface",
        "Integration Points",
        "Data Flow",
        "Conventions",
    }
)


SectionName = Literal[
    "Architecture Overview",
    "Module Map",
    "Key Abstractions",
    "API Surface",
    "Integration Points",
    "Data Flow",
    "Conventions",
]


class ContextExtractionEntry(BaseModel):
    """A single extracted fact about a source file."""

    section: SectionName = Field(
        description="Which of the 7 sections this fact belongs to.",
    )
    symbol: str = Field(
        description=(
            "Exact symbol, module name, or identifier copied verbatim from the source file."
        ),
    )
    description: str = Field(
        description="One concise sentence stating the fact.",
    )
    file_path: str = Field(
        description="Relative path of the source file this fact came from.",
    )


class ContextExtractionResult(BaseModel):
    """LLM response schema for single-file context extraction."""

    entries: list[ContextExtractionEntry] = Field(
        default_factory=list,
        description=(
            "All notable facts extracted from the file. Empty list if the "
            "file has nothing worth recording."
        ),
    )


def parse_skeleton_output(
    text: str,
) -> list[tuple[str, str, str, str, str]]:
    """Parse a deterministic skeleton into DB entry tuples.

    Returns ``(section, file_path, content, "skeleton")`` tuples.
    Handles both ``- `` bullets and ``### sub/`` subsection headings
    (Module Map directories are treated as entries with the directory
    as the file_path).
    """
    if not text or not text.strip():
        return []

    results: list[tuple[str, str, str, str, str]] = []
    current_section = ""
    current_subsection_path = ""

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        # Top-level heading.
        if stripped.startswith("# ") and not stripped.startswith("## "):
            continue

        # Section heading.
        if stripped.startswith("## "):
            heading = stripped[3:].strip()
            heading = _normalize_heading(heading)
            if heading in _VALID_SECTIONS:
                current_section = heading
            else:
                current_section = ""
            current_subsection_path = ""
            continue

        if not current_section:
            continue

        # Subsection heading (e.g. ``### src/lean_ai/``).
        if stripped.startswith("### "):
            current_subsection_path = stripped[4:].strip().rstrip("/")
            continue

        # Skip placeholder lines.
        if stripped in ("No data extracted yet.", "... (truncated)"):
            continue

        # Bullet entries.
        if stripped.startswith("- "):
            entry_text = stripped[2:].strip()
            if not entry_text:
                continue
            file_path = _extract_file_path(entry_text) or current_subsection_path
            skeleton_hash = hashlib.sha256(
                f"{current_section}:{file_path}:{entry_text}".encode("utf-8")
            ).hexdigest()
            results.append((current_section, file_path, entry_text, "skeleton", skeleton_hash))
            continue

        # Non-bullet content (e.g. "Entry points: `main.py`").
        if current_section and stripped:
            non_bullet_hash = hashlib.sha256(
                f"{current_section}:{}:{stripped}".encode("utf-8")
            ).hexdigest()
            results.append((current_section, "", stripped, "skeleton", non_bullet_hash))

    return results


def _normalize_heading(heading: str) -> str:
    """Strip trailing parenthetical qualifiers from headings.

    ``"Key Abstractions (Updated)"`` → ``"Key Abstractions"``
    """
    paren_idx = heading.rfind(" (")
    if paren_idx > 0 and heading.endswith(")"):
        return heading[:paren_idx].strip()
    return heading


def _extract_file_path(entry: str) -> str:
    """Extract a file path from a skeleton bullet line.

    Used only by ``parse_skeleton_output`` — the LLM extraction path
    uses the structured schema and has no need for this heuristic.
    """
    # Pattern: trailing ``(path/to/file.ext)`` or ``(`path/to/file.ext`)``.
    last_open = entry.rfind("(")
    if last_open >= 0 and entry.endswith(")"):
        inner = entry[last_open + 1 : -1].strip().strip("`")
        if _looks_like_path(inner):
            return inner

    # Pattern: backtick-delimited path anywhere in the entry.
    parts = entry.split("`")
    for part in reversed(parts):
        part = part.strip()
        if _looks_like_path(part):
            return part

    return ""


def _looks_like_path(text: str) -> bool:
    """Heuristic: does this string look like a file path?"""
    if not text:
        return False
    if "/" not in text and "\\" not in text:
        return False
    # Must have a file extension in the last component.
    last_part = text.split("/")[-1].split("\\")[-1]
    return "." in last_part

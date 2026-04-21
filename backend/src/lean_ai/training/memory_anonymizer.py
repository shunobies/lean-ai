"""Workspace-identifier anonymization for cross-workspace memory export.

Memories often reference project-specific file paths, module names, and
symbols that wouldn't generalize across workspaces. Before a memory is
exported via ``/api/export/memories``, this pass:

1. Builds a **symbol table** from the workspace (imports scanned in
   ``tool_logs`` / ``conversation_logs`` + directory listing).
2. Rewrites each symbol-table hit in the memory content with a generic
   placeholder (``<WORKSPACE_FILE>`` / ``<WORKSPACE_MODULE>`` / etc.).
3. Computes an ``anonymization_ratio`` — the fraction of memory content
   that was redacted.
4. Optionally drops memories where the ratio exceeds
   ``settings.memory_export_drop_threshold`` (default 40%), since
   heavily-redacted memories are usually too workspace-specific to
   generalize.

The scrubber in :mod:`lean_ai.training.scrubber` handles the
security-critical PII/secret patterns; this module handles the weaker
cross-workspace-bleed concern.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

import aiosqlite

from lean_ai.config import settings

logger = logging.getLogger(__name__)


# Tokens that look like Python / TS module paths.  Matches ``foo.bar.baz``,
# ``src/foo``, ``./foo``, backtick-wrapped identifiers, etc.
_PATH_TOKEN = re.compile(
    r"(?:\.\.?/)?(?:[A-Za-z0-9_\-]+/)+[A-Za-z0-9_.\-]+"
)
# Absolute paths: must start at a non-word boundary so we don't chop
# inside relative paths like ``src/auth/token.py`` (where the ``/auth/...``
# substring would otherwise match).
_ABS_PATH = re.compile(r"(?<![A-Za-z0-9_])(?:/[A-Za-z0-9_\-.]+){2,}")
_IDENT = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


@dataclass(frozen=True)
class SymbolTable:
    """Workspace-specific identifiers discovered via tool logs + files."""

    file_paths: frozenset[str] = field(default_factory=frozenset)
    modules: frozenset[str] = field(default_factory=frozenset)
    top_dirs: frozenset[str] = field(default_factory=frozenset)
    symbols: frozenset[str] = field(default_factory=frozenset)


async def build_symbol_table(
    db: aiosqlite.Connection,
    *,
    repo_root: str,
) -> SymbolTable:
    """Scan the workspace DB to build a rough symbol-table.

    Cheap: reads tool_logs and the top-level directory listing. The goal
    isn't precision — it's breadth, so the anonymizer can replace things
    like ``src/auth/token.py`` or ``MyProjectSpecificClass``.
    """
    file_paths: set[str] = set()
    modules: set[str] = set()
    symbols: set[str] = set()
    top_dirs: set[str] = set()

    # Top-level directory names (quick scan, not recursive)
    try:
        for entry in os.listdir(repo_root):
            full = os.path.join(repo_root, entry)
            if os.path.isdir(full) and not entry.startswith("."):
                top_dirs.add(entry)
    except OSError:
        pass

    # File paths from tool_logs parameters
    try:
        cursor = await db.execute(
            "SELECT parameters FROM tool_logs "
            "WHERE tool_name IN ('read_file', 'edit_file', 'create_file', "
            "'grep_files', 'list_directory') LIMIT 500"
        )
        rows = await cursor.fetchall()
        for (raw,) in rows:
            if not raw:
                continue
            # Best-effort path extraction
            for match in _PATH_TOKEN.finditer(raw):
                token = match.group(0).strip('"\'`,;')
                if token and 1 <= len(token) <= 200:
                    file_paths.add(token)
    except Exception:
        logger.debug("symbol-table tool_logs scan failed", exc_info=True)

    # Module names from conversation_logs (import statements)
    try:
        cursor = await db.execute(
            "SELECT content FROM conversation_logs "
            "WHERE role = 'tool_result' LIMIT 200"
        )
        rows = await cursor.fetchall()
        for (raw,) in rows:
            if not raw:
                continue
            for m in re.finditer(
                r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))",
                raw,
                flags=re.MULTILINE,
            ):
                modname = m.group(1) or m.group(2)
                if modname and "." in modname:
                    # Only keep workspace-specific modules; skip stdlib-looking ones
                    head = modname.split(".")[0]
                    if head in top_dirs or head in file_paths:
                        modules.add(modname)
    except Exception:
        logger.debug("symbol-table conversation_logs scan failed", exc_info=True)

    # Simple heuristic: treat CamelCase/snake_case terms that appear in
    # file paths as workspace-specific symbols worth rewriting.
    for path in list(file_paths)[:300]:
        stem = os.path.splitext(os.path.basename(path))[0]
        for part in stem.split("_"):
            if len(part) >= 4 and part.isalpha():
                symbols.add(part)

    return SymbolTable(
        file_paths=frozenset(file_paths),
        modules=frozenset(modules),
        top_dirs=frozenset(top_dirs),
        symbols=frozenset(symbols),
    )


@dataclass
class AnonymizedMemory:
    """Result of running a memory through the anonymizer."""

    content: str
    ratio: float
    dropped: bool


def anonymize_memory_content(
    content: str, table: SymbolTable,
    *, drop_threshold: float | None = None,
) -> AnonymizedMemory:
    """Rewrite workspace-specific identifiers inside *content*."""
    if not content:
        return AnonymizedMemory(content=content, ratio=0.0, dropped=False)

    original_len = len(content)
    redacted_chars = 0
    out = content

    # Absolute paths — always stripped
    def _sub_abs(m: re.Match[str]) -> str:
        nonlocal redacted_chars
        redacted_chars += len(m.group(0))
        return "<WORKSPACE_PATH>"
    out = _ABS_PATH.sub(_sub_abs, out)

    # File paths from the symbol table — longest first so more specific
    # paths match before their prefixes
    sorted_paths = sorted(table.file_paths, key=len, reverse=True)
    for path in sorted_paths:
        if not path:
            continue
        if path in out:
            redacted_chars += out.count(path) * len(path)
            out = out.replace(path, "<WORKSPACE_FILE>")

    # Modules
    sorted_mods = sorted(table.modules, key=len, reverse=True)
    for mod in sorted_mods:
        if not mod:
            continue
        pattern = re.compile(rf"\b{re.escape(mod)}\b")
        matches = pattern.findall(out)
        if matches:
            redacted_chars += sum(len(m) for m in matches)
            out = pattern.sub("<WORKSPACE_MODULE>", out)

    # Symbols — only when adjacent to code-like framing (backticks,
    # "the", "class", "function") to avoid redacting English words.
    # Match case-insensitively so "The Foo class" and "the foo class" both
    # count as framed, but keep the symbol itself case-sensitive.
    framing = (r"(?:`|the\s+|class\s+|function\s+|module\s+|file\s+)")
    for sym in sorted(table.symbols, key=len, reverse=True):
        if len(sym) < 4:
            continue
        pattern = re.compile(
            rf"((?i:\b{framing}))({re.escape(sym)})\b",
        )
        for m in pattern.finditer(out):
            redacted_chars += len(m.group(2))
        out = pattern.sub(r"\1<WORKSPACE_SYMBOL>", out)

    # Top-level directory names when followed by a slash
    for d in sorted(table.top_dirs, key=len, reverse=True):
        if not d:
            continue
        pattern = re.compile(rf"\b{re.escape(d)}/")
        matches = pattern.findall(out)
        if matches:
            redacted_chars += sum(len(m) for m in matches)
            out = pattern.sub("<WORKSPACE_PATH>/", out)

    ratio = (redacted_chars / original_len) if original_len else 0.0
    threshold = (
        drop_threshold if drop_threshold is not None
        else getattr(settings, "memory_export_drop_threshold", 0.40)
    )
    return AnonymizedMemory(content=out, ratio=ratio, dropped=ratio > threshold)


def anonymize_memories(
    rows: Iterable[dict],
    *,
    table: SymbolTable,
    drop_threshold: float | None = None,
) -> Iterable[dict]:
    """Apply :func:`anonymize_memory_content` to each row.

    Dropped memories are skipped (generator yields nothing for them).
    Each surviving row is returned with anonymized content and an
    added ``anonymization_ratio`` field.
    """
    for row in rows:
        content = row.get("content") or ""
        result = anonymize_memory_content(
            content, table, drop_threshold=drop_threshold,
        )
        if result.dropped:
            continue
        out = dict(row)
        out["content"] = result.content
        out["anonymization_ratio"] = round(result.ratio, 3)
        yield out

"""PII/secrets scrubber that runs before any trace enters the archive.

Two guarantees:

1. **Fail-closed by default** — if scrubbing raises, the caller drops
   the trace rather than persisting unscrubbed data. Toggle via
   ``settings.scrubbing_strict``; lenient mode writes with ``scrubbed=0``
   so exports can filter.
2. **Audit, never leak** — every match produces a ``redaction_audit``
   row with ``match_preview = sha256(match)[:12]``. The raw secret never
   lands in the audit log.

The scrubber walks recursively through dict / list / tuple / string
payloads.  Strings are rewritten in place; parsed JSON embedded in
tool-call ``arguments`` is descended into.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


class ScrubberError(Exception):
    """Raised when strict scrubbing cannot complete safely."""


@dataclass(frozen=True)
class _Pattern:
    name: str
    regex: re.Pattern[str]
    replacement: str
    """Static replacement, or "@entropy" to gate by Shannon entropy."""


def _shannon_entropy(s: str) -> float:
    """Bits/character Shannon entropy. ~4+ flags high-entropy tokens."""
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    length = len(s)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


_PATTERNS: tuple[_Pattern, ...] = (
    # More specific patterns must run first — anthropic keys start with
    # "sk-ant-" which the generic OpenAI regex would also match.
    _Pattern(
        name="anthropic_key",
        regex=re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
        replacement="<REDACTED:anthropic-key>",
    ),
    _Pattern(
        name="openai_key",
        regex=re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
        replacement="<REDACTED:openai-key>",
    ),
    _Pattern(
        name="slack_bot_token",
        regex=re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
        replacement="<REDACTED:slack>",
    ),
    _Pattern(
        name="github_token",
        regex=re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
        replacement="<REDACTED:github>",
    ),
    _Pattern(
        name="aws_access",
        regex=re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        replacement="<REDACTED:aws>",
    ),
    _Pattern(
        name="bearer_header",
        regex=re.compile(r"(?i)(authorization:\s*bearer\s+)\S+"),
        replacement=r"\1<REDACTED>",
    ),
    _Pattern(
        name="ssh_private",
        regex=re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?"
            r"-----END [A-Z ]*PRIVATE KEY-----",
        ),
        replacement="<REDACTED:ssh-key>",
    ),
    _Pattern(
        name="jwt",
        regex=re.compile(
            r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
        ),
        replacement="<REDACTED:jwt>",
    ),
    _Pattern(
        name="email",
        regex=re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
        replacement="<REDACTED:email>",
    ),
    _Pattern(
        name="lean_ai_env_key",
        regex=re.compile(
            r"(LEAN_AI_[A-Z_]*(?:KEY|TOKEN|PASSWORD|SECRET)\s*=\s*)"
            r"['\"]?\S+['\"]?",
        ),
        replacement=r"\1<REDACTED:env>",
    ),
    _Pattern(
        name="env_file_line",
        regex=re.compile(
            r"(?m)^([A-Z_][A-Z0-9_]*(?:KEY|TOKEN|PASSWORD|SECRET)\s*=\s*)"
            r"['\"]?[^\s'\"]{6,}['\"]?",
        ),
        replacement=r"\1<REDACTED:env>",
    ),
    _Pattern(
        name="generic_high_entropy",
        regex=re.compile(r"\b[A-Za-z0-9+/=_-]{32,}\b"),
        replacement="@entropy",
    ),
)


AuditCallback = Callable[[str, str, str], None]
"""Callback signature: (pattern_name, replacement, match_preview)."""


def _match_preview(match: str) -> str:
    """First 12 chars of sha256 — audit-safe, never the secret itself."""
    return hashlib.sha256(match.encode("utf-8", errors="replace")).hexdigest()[:12]


def _apply_pattern_to_string(
    pat: _Pattern,
    text: str,
    audit: AuditCallback | None,
) -> str:
    """Apply one pattern to a string. Handles the @entropy gate."""
    if pat.replacement == "@entropy":

        def _entropy_sub(m: re.Match[str]) -> str:
            token = m.group(0)
            if _shannon_entropy(token) < 4.0:
                return token
            if audit is not None:
                audit(pat.name, "<REDACTED:entropy>", _match_preview(token))
            return "<REDACTED:entropy>"

        return pat.regex.sub(_entropy_sub, text)

    def _static_sub(m: re.Match[str]) -> str:
        if audit is not None:
            audit(pat.name, pat.replacement, _match_preview(m.group(0)))
        # Expand backrefs (\1, etc.) against the match
        try:
            return m.expand(pat.replacement)
        except re.error:
            return pat.replacement

    return pat.regex.sub(_static_sub, text)


def scrub_string(text: str, audit: AuditCallback | None = None) -> str:
    """Apply every pattern to a single string."""
    if not text:
        return text
    out = text
    for pat in _PATTERNS:
        out = _apply_pattern_to_string(pat, out, audit)
    return out


def scrub_value(value: Any, audit: AuditCallback | None = None) -> Any:
    """Recursively scrub strings inside a dict / list / scalar payload.

    Non-string scalars pass through untouched.  Strings that happen to
    be JSON are parsed, scrubbed structurally, and re-serialised — so a
    tool-call ``arguments`` JSON blob embedded as a string is walked
    field-by-field rather than regex'd in place.
    """
    if isinstance(value, str):
        # If the string parses as a JSON object/array, descend into it
        stripped = value.lstrip()
        if stripped.startswith(("{", "[")):
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                return scrub_string(value, audit)
            scrubbed = scrub_value(parsed, audit)
            try:
                return json.dumps(scrubbed, ensure_ascii=False)
            except (TypeError, ValueError):
                return scrub_string(value, audit)
        return scrub_string(value, audit)
    if isinstance(value, dict):
        return {k: scrub_value(v, audit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        out = [scrub_value(v, audit) for v in value]
        return out if isinstance(value, list) else tuple(out)
    return value


def scrub_payload(
    payload: dict,
    audit: AuditCallback | None = None,
) -> dict:
    """Top-level scrubber used by :mod:`lean_ai.training.capture`.

    Returns a new dict with all string leaves scrubbed.  Any exception
    is re-raised as :class:`ScrubberError` so the caller can fail-closed.
    """
    try:
        return scrub_value(payload, audit)  # type: ignore[return-value]
    except Exception as exc:  # pragma: no cover — defence-in-depth
        raise ScrubberError(f"scrubber failed: {exc}") from exc


async def persist_audit_rows(
    db: aiosqlite.Connection,
    *,
    source_table: str,
    source_id: str,
    rows: list[tuple[str, str, str]],
) -> None:
    """Bulk-insert collected audit rows from a scrub pass."""
    if not rows:
        return
    from lean_ai.training.db import _now

    await db.executemany(
        "INSERT INTO redaction_audit ("
        "source_table, source_id, pattern_name, replacement, match_preview, "
        "created_at"
        ") VALUES (?, ?, ?, ?, ?, ?)",
        [
            (source_table, source_id, name, replacement, preview, _now())
            for (name, replacement, preview) in rows
        ],
    )
    await db.commit()

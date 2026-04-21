"""SQLite schema + helpers for the raw training archive.

Separate DB at ``.lean_ai/training.db`` (configurable via
``settings.training_db_path``) so retention/VACUUM never blocks workflow
writes and users can delete it independently of workspace state.

All writes are fire-and-forget from workflow hooks; all reads are from
the export API. The archive is append-only — rows are never mutated
after insert.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from lean_ai.config import settings

logger = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS training_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_uuid TEXT UNIQUE NOT NULL,
    session_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    model_name TEXT NOT NULL,
    provider TEXT NOT NULL,
    messages TEXT NOT NULL,           -- JSON array
    assistant_output TEXT NOT NULL,   -- JSON object
    outcome TEXT,
    pair_id TEXT,
    preference INTEGER,
    pair_kind TEXT,
    reward REAL,
    tokens_prompt INTEGER,
    tokens_completion INTEGER,
    latency_ms INTEGER,
    scrubbed INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tt_session ON training_traces(session_id);
CREATE INDEX IF NOT EXISTS idx_tt_pair ON training_traces(pair_id);
CREATE INDEX IF NOT EXISTS idx_tt_model_phase ON training_traces(model_name, phase);
CREATE INDEX IF NOT EXISTS idx_tt_outcome ON training_traces(outcome);
CREATE INDEX IF NOT EXISTS idx_tt_created ON training_traces(created_at);

CREATE TABLE IF NOT EXISTS plan_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    revision_count INTEGER NOT NULL DEFAULT 0,
    task TEXT NOT NULL,
    plan_before TEXT,                 -- JSON
    plan_after TEXT,                  -- JSON
    feedback TEXT,
    decision TEXT NOT NULL,           -- approved|rejected|cancelled
    trace_uuid TEXT,
    pair_trace_uuid TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pd_session ON plan_decisions(session_id);
CREATE INDEX IF NOT EXISTS idx_pd_decision ON plan_decisions(decision);

CREATE TABLE IF NOT EXISTS validation_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    attempt_num INTEGER NOT NULL,
    failures_before TEXT,             -- JSON
    diagnosis TEXT,
    fix_tool_calls TEXT,              -- JSON array
    failures_after TEXT,              -- JSON
    succeeded INTEGER NOT NULL DEFAULT 0,
    regression_failure INTEGER NOT NULL DEFAULT 0,
    trace_uuid TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_va_session ON validation_attempts(session_id);

CREATE TABLE IF NOT EXISTS workflow_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT,                     -- JSON
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_we_session ON workflow_events(session_id);
CREATE INDEX IF NOT EXISTS idx_we_type ON workflow_events(event_type);

CREATE TABLE IF NOT EXISTS redaction_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_table TEXT NOT NULL,
    source_id TEXT NOT NULL,
    pattern_name TEXT NOT NULL,
    replacement TEXT NOT NULL,
    match_preview TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ra_pattern ON redaction_audit(pattern_name);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path(repo_root: str) -> Path:
    """Resolve the per-workspace training DB path.

    Honours ``settings.training_db_path`` (default
    ``.lean_ai/training.db``).  Relative paths are taken relative to the
    workspace root; absolute paths are used as-is (useful for
    benchmarking or tests).
    """
    configured = settings.training_db_path or ".lean_ai/training.db"
    p = Path(configured)
    if not p.is_absolute():
        p = Path(repo_root) / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


async def _migrate(db: aiosqlite.Connection) -> None:
    """Apply idempotent migrations for pre-existing DBs.

    SQLite ``CREATE TABLE IF NOT EXISTS`` keeps old schemas as-is, so
    columns added after a DB was first created need explicit
    ``ALTER TABLE``. Each migration here is guarded by a PRAGMA column
    check so re-running is a no-op.
    """
    async def _has_column(table: str, column: str) -> bool:
        cursor = await db.execute(f"PRAGMA table_info({table})")
        rows = await cursor.fetchall()
        return any(row["name"] == column for row in rows)

    # Layer 7 — ``regression_failure`` column on validation_attempts.
    if not await _has_column("validation_attempts", "regression_failure"):
        await db.execute(
            "ALTER TABLE validation_attempts ADD COLUMN "
            "regression_failure INTEGER NOT NULL DEFAULT 0"
        )
        await db.commit()


async def get_training_db(repo_root: str) -> aiosqlite.Connection:
    """Open (or create) the training archive DB with WAL mode."""
    db = await aiosqlite.connect(str(_db_path(repo_root)))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode = WAL")
    await db.execute("PRAGMA busy_timeout = 5000")
    await db.executescript(_SCHEMA)
    await _migrate(db)
    await db.commit()
    return db


def new_trace_uuid() -> str:
    """Return a short UUID suitable for ``trace_uuid`` / ``pair_id``."""
    return uuid.uuid4().hex


def _json_dump(value: Any) -> str:
    """JSON-serialise with a fallback that survives unusual payloads."""
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps(str(value))


async def insert_training_trace(
    db: aiosqlite.Connection,
    *,
    trace_uuid: str,
    session_id: str,
    phase: str,
    model_name: str,
    provider: str,
    messages: list | dict,
    assistant_output: dict,
    outcome: str | None = None,
    pair_id: str | None = None,
    preference: int | None = None,
    pair_kind: str | None = None,
    reward: float | None = None,
    tokens_prompt: int | None = None,
    tokens_completion: int | None = None,
    latency_ms: int | None = None,
    scrubbed: bool = True,
) -> int:
    """Insert a row into ``training_traces`` and return the new id."""
    cursor = await db.execute(
        "INSERT INTO training_traces ("
        "trace_uuid, session_id, phase, model_name, provider, messages, "
        "assistant_output, outcome, pair_id, preference, pair_kind, reward, "
        "tokens_prompt, tokens_completion, latency_ms, scrubbed, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            trace_uuid, session_id, phase, model_name, provider,
            _json_dump(messages), _json_dump(assistant_output),
            outcome, pair_id, preference, pair_kind, reward,
            tokens_prompt, tokens_completion, latency_ms,
            1 if scrubbed else 0, _now(),
        ),
    )
    await db.commit()
    return cursor.lastrowid or 0


async def insert_plan_decision(
    db: aiosqlite.Connection,
    *,
    session_id: str,
    revision_count: int,
    task: str,
    plan_before: str | dict | None,
    plan_after: str | dict | None,
    feedback: str | None,
    decision: str,
    trace_uuid: str | None = None,
    pair_trace_uuid: str | None = None,
) -> int:
    cursor = await db.execute(
        "INSERT INTO plan_decisions ("
        "session_id, revision_count, task, plan_before, plan_after, "
        "feedback, decision, trace_uuid, pair_trace_uuid, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            session_id, revision_count, task,
            _json_dump(plan_before) if plan_before is not None else None,
            _json_dump(plan_after) if plan_after is not None else None,
            feedback, decision, trace_uuid, pair_trace_uuid, _now(),
        ),
    )
    await db.commit()
    return cursor.lastrowid or 0


async def insert_validation_attempt(
    db: aiosqlite.Connection,
    *,
    session_id: str,
    attempt_num: int,
    failures_before: dict | None,
    diagnosis: str | None,
    fix_tool_calls: list | None,
    failures_after: dict | None,
    succeeded: bool,
    trace_uuid: str | None = None,
    regression_failure: bool = False,
) -> int:
    cursor = await db.execute(
        "INSERT INTO validation_attempts ("
        "session_id, attempt_num, failures_before, diagnosis, fix_tool_calls, "
        "failures_after, succeeded, regression_failure, trace_uuid, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            session_id, attempt_num,
            _json_dump(failures_before) if failures_before is not None else None,
            diagnosis,
            _json_dump(fix_tool_calls) if fix_tool_calls is not None else None,
            _json_dump(failures_after) if failures_after is not None else None,
            1 if succeeded else 0,
            1 if regression_failure else 0,
            trace_uuid, _now(),
        ),
    )
    await db.commit()
    return cursor.lastrowid or 0


async def insert_workflow_event(
    db: aiosqlite.Connection,
    *,
    session_id: str,
    event_type: str,
    payload: dict | None,
) -> int:
    cursor = await db.execute(
        "INSERT INTO workflow_events ("
        "session_id, event_type, payload, created_at"
        ") VALUES (?, ?, ?, ?)",
        (
            session_id, event_type,
            _json_dump(payload) if payload is not None else None,
            _now(),
        ),
    )
    await db.commit()
    return cursor.lastrowid or 0


async def insert_redaction_audit(
    db: aiosqlite.Connection,
    *,
    source_table: str,
    source_id: str,
    pattern_name: str,
    replacement: str,
    match_preview: str,
) -> int:
    cursor = await db.execute(
        "INSERT INTO redaction_audit ("
        "source_table, source_id, pattern_name, replacement, match_preview, "
        "created_at"
        ") VALUES (?, ?, ?, ?, ?, ?)",
        (source_table, source_id, pattern_name, replacement, match_preview,
         _now()),
    )
    await db.commit()
    return cursor.lastrowid or 0


# ── Maintenance ──


async def prune_expired(
    db: aiosqlite.Connection,
    *,
    retention_days: int | None = None,
) -> dict:
    """Delete rows older than ``retention_days`` across all tables.

    Returns a counts dict keyed by table name.  Called opportunistically
    when the DB is opened for write by Phase D automation — never from
    the hot path.
    """
    days = (
        retention_days if retention_days is not None
        else settings.training_retention_days
    )
    if days <= 0:
        return {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    counts: dict[str, int] = {}
    for table in (
        "training_traces", "plan_decisions", "validation_attempts",
        "workflow_events", "redaction_audit",
    ):
        cursor = await db.execute(
            f"DELETE FROM {table} WHERE created_at < ?", (cutoff,),
        )
        counts[table] = cursor.rowcount or 0
    await db.commit()
    total = sum(counts.values())
    if total:
        logger.info(
            "Training archive prune: deleted %d rows older than %s",
            total, cutoff,
        )
    return counts


async def manifest_counts(db: aiosqlite.Connection) -> dict:
    """Produce aggregate counts for ``/api/export/manifest``.

    Returns a dict with per-model, per-phase, per-outcome breakdowns and
    date range.  Cheap: all queries are indexed.
    """
    out: dict[str, Any] = {
        "total_traces": 0,
        "by_model": {},
        "by_phase": {},
        "by_outcome": {},
        "scrubbed_count": 0,
        "oldest": None,
        "newest": None,
        "plan_decisions": 0,
        "validation_attempts": 0,
        "workflow_events": 0,
    }

    async def _fetch_one(sql: str, *params) -> tuple:
        cursor = await db.execute(sql, params)
        row = await cursor.fetchone()
        return tuple(row) if row else ()

    (total,) = await _fetch_one("SELECT COUNT(*) FROM training_traces") or (0,)
    out["total_traces"] = total or 0

    if total:
        (scrubbed,) = await _fetch_one(
            "SELECT COUNT(*) FROM training_traces WHERE scrubbed = 1",
        ) or (0,)
        out["scrubbed_count"] = scrubbed or 0

        (oldest, newest) = await _fetch_one(
            "SELECT MIN(created_at), MAX(created_at) FROM training_traces",
        ) or (None, None)
        out["oldest"] = oldest
        out["newest"] = newest

        async def _group(column: str) -> dict[str, int]:
            cursor = await db.execute(
                f"SELECT {column}, COUNT(*) FROM training_traces "
                f"WHERE {column} IS NOT NULL GROUP BY {column}",
            )
            rows = await cursor.fetchall()
            return {row[0]: row[1] for row in rows}

        out["by_model"] = await _group("model_name")
        out["by_phase"] = await _group("phase")
        out["by_outcome"] = await _group("outcome")

    for table in ("plan_decisions", "validation_attempts", "workflow_events"):
        (count,) = await _fetch_one(f"SELECT COUNT(*) FROM {table}") or (0,)
        out[table] = count or 0

    return out

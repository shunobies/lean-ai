"""Per-session append-only state ledger for deterministic recovery.

Stores JSONL events at ``.lean_ai/state/{session_id}.jsonl``.
This is machine-oriented state (typed events), complementary to:
- scratchpad: volatile overwrite notes
- journal: human-readable append-only notes
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


LEDGER_CONTEXT_PERCENT = 0.03  # keep summaries compact in refresh payloads


_ALLOWED_EVENT_TYPES = {
    "phase_transition",
    "context_refreshed",
    "tool_called",
    "tool_succeeded",
    "tool_failed",
    "checkpoint",
}


def ledger_path(repo_root: str, session_id: str) -> Path:
    return Path(repo_root) / ".lean_ai" / "state" / f"{session_id}.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_event(
    *,
    repo_root: str,
    session_id: str,
    event_type: str,
    payload: dict | None = None,
) -> None:
    """Append a typed event to the session ledger.

    Fail-open by design: never break workflow execution because telemetry failed.
    """
    if not repo_root or not session_id:
        return
    if event_type not in _ALLOWED_EVENT_TYPES:
        logger.debug("Ignoring unknown ledger event type: %s", event_type)
        return

    record = {
        "ts": _now_iso(),
        "event": event_type,
        "payload": payload or {},
    }

    path = ledger_path(repo_root, session_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        logger.warning("Failed to append state ledger event", exc_info=True)


def summarize_recent_events(repo_root: str, session_id: str, max_events: int = 25) -> str:
    """Return compact bullet summary of recent state events for refresh continuity."""
    path = ledger_path(repo_root, session_id)
    if not path.is_file():
        return ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        logger.warning("Failed to read state ledger", exc_info=True)
        return ""

    events: list[dict] = []
    for line in lines[-max_events:]:
        try:
            events.append(json.loads(line))
        except Exception:
            continue

    if not events:
        return ""

    bullets: list[str] = []
    for ev in events:
        et = ev.get("event", "?")
        payload = ev.get("payload") or {}
        if et in {"tool_called", "tool_succeeded", "tool_failed"}:
            tool = payload.get("tool", "?")
            target = payload.get("target", "")
            suffix = f" ({target})" if target else ""
            bullets.append(f"- {et}: {tool}{suffix}")
        elif et == "phase_transition":
            phase = payload.get("phase", "?")
            bullets.append(f"- phase_transition: {phase}")
        elif et == "context_refreshed":
            bullets.append("- context_refreshed")
        elif et == "checkpoint":
            msg = payload.get("message", "")
            bullets.append(f"- checkpoint: {msg}" if msg else "- checkpoint")

    return "\n".join(bullets[:max_events])

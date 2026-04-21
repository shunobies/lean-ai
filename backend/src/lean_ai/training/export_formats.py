"""Raw / SFT / DPO / KTO format converters for the training export API.

Rows from ``training_traces`` are stored with the full prompt + output
JSON preserved.  Exporters translate those into the standard JSONL
shapes expected by common trainers (Axolotl, Unsloth, TRL, HuggingFace
``datasets``).

Anonymization (workspace-id hashing, path rewriting) is applied by
:func:`anonymize_row` before any format converter runs, so all outputs
are safe to aggregate across workspaces.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from typing import Any

from lean_ai.config import settings

# ── Anonymization ────────────────────────────────────────────


def workspace_id(repo_root: str) -> str:
    """Compute the stable 16-char workspace_id for ``repo_root``."""
    salt = getattr(settings, "export_workspace_salt", "") or ""
    digest = hashlib.sha256(f"{salt}:{repo_root}".encode()).hexdigest()
    return digest[:16]


def _anonymize_text(
    text: str, repo_root: str, workspace_id_value: str,
) -> str:
    """Replace repo_root paths with a workspace-scoped placeholder."""
    if not text:
        return text
    placeholder = f"/workspace-{workspace_id_value}"
    cleaned = text.replace(repo_root, placeholder)
    basename = os.path.basename(repo_root)
    if basename and basename != repo_root:
        cleaned = cleaned.replace(
            f"/{basename}/", f"{placeholder}/",
        )
    return cleaned


def _walk(value: Any, fn) -> Any:
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, dict):
        return {k: _walk(v, fn) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk(v, fn) for v in value]
    if isinstance(value, tuple):
        return tuple(_walk(v, fn) for v in value)
    return value


def anonymize_row(row: dict, repo_root: str) -> dict:
    """Return a copy of *row* with workspace-scoped identifiers hashed.

    - ``session_id`` replaced with a sha256-prefix derived from salt.
    - ``repo_root`` occurrences stripped from all string leaves.
    - ``id`` (raw int primary key) removed.
    """
    ws_id = workspace_id(repo_root)
    session_salt = (getattr(settings, "export_workspace_salt", "") or "") + \
        ":session"
    out: dict[str, Any] = {}
    for key, value in row.items():
        if key == "id":
            continue
        if key == "session_id" and isinstance(value, str):
            out[key] = hashlib.sha256(
                f"{session_salt}:{value}".encode(),
            ).hexdigest()[:12]
            continue
        out[key] = _walk(
            value,
            lambda s: _anonymize_text(s, repo_root, ws_id),
        )
    out["workspace_id"] = ws_id
    return out


# ── JSON-column helpers ──────────────────────────────────────


def _parse_json_columns(row: dict, *columns: str) -> dict:
    """Parse stringly-stored JSON columns into Python objects."""
    parsed = dict(row)
    for col in columns:
        raw = parsed.get(col)
        if isinstance(raw, str):
            try:
                parsed[col] = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                parsed[col] = raw
    return parsed


def prepared_trace(row: dict, repo_root: str) -> dict:
    """Canonical training_traces row — anonymized, JSON cols parsed."""
    hydrated = _parse_json_columns(dict(row), "messages", "assistant_output")
    return anonymize_row(hydrated, repo_root)


# ── Format converters ────────────────────────────────────────


def to_raw_jsonl(rows: Iterable[dict], repo_root: str) -> Iterable[str]:
    """Emit one JSON object per row, anonymized but structurally intact."""
    for row in rows:
        yield json.dumps(prepared_trace(row, repo_root), ensure_ascii=False) + "\n"


def _assistant_message_for_sft(assistant_output: dict) -> dict:
    """Build an OpenAI-chat-format assistant message.

    Preserves content, thinking (in ``reasoning_content`` for the OpenAI
    harmony format used by gpt-oss / Qwen3 thinking mode), and tool calls.
    """
    msg: dict = {"role": "assistant"}
    content = assistant_output.get("content") or ""
    msg["content"] = content
    thinking = assistant_output.get("thinking")
    if thinking and getattr(settings, "capture_thinking", True):
        msg["reasoning_content"] = thinking
    tool_calls = assistant_output.get("tool_calls") or []
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def to_sft_jsonl(rows: Iterable[dict], repo_root: str) -> Iterable[str]:
    """Emit OpenAI-chat JSONL for SFT training.

    Only successful (``outcome='success'``) rows are emitted.  Each line
    is ``{"messages": [...], "tools": [...]}`` with the assistant output
    appended as the final message.  Rows without a usable messages list
    are skipped.
    """
    for row in rows:
        outcome = row.get("outcome")
        if outcome and outcome != "success":
            continue
        prepared = prepared_trace(row, repo_root)
        messages = prepared.get("messages")
        if not isinstance(messages, list):
            continue
        assistant = _assistant_message_for_sft(
            prepared.get("assistant_output") or {},
        )
        full_messages = [*list(messages), assistant]
        out: dict = {
            "messages": full_messages,
            "workspace_id": prepared.get("workspace_id"),
            "phase": prepared.get("phase"),
            "model_name": prepared.get("model_name"),
        }
        yield json.dumps(out, ensure_ascii=False) + "\n"


def to_kto_jsonl(rows: Iterable[dict], repo_root: str) -> Iterable[str]:
    """Emit KTO (binary-labeled) JSONL.

    ``preference >= 1`` → label True, ``<= -1`` → label False. Rows with
    ``preference is None`` or ``0`` are skipped (neutral carries no signal).
    """
    for row in rows:
        preference = row.get("preference")
        if preference is None or preference == 0:
            continue
        prepared = prepared_trace(row, repo_root)
        messages = prepared.get("messages") or []
        assistant = _assistant_message_for_sft(
            prepared.get("assistant_output") or {},
        )
        out = {
            "prompt": messages,
            "completion": assistant,
            "label": bool(preference >= 1),
            "workspace_id": prepared.get("workspace_id"),
            "phase": prepared.get("phase"),
            "model_name": prepared.get("model_name"),
            "pair_kind": prepared.get("pair_kind"),
        }
        yield json.dumps(out, ensure_ascii=False) + "\n"


def to_dpo_jsonl(rows: Iterable[dict], repo_root: str) -> Iterable[str]:
    """Emit DPO pair JSONL.

    Rows are grouped by ``pair_id``; each group must contain exactly one
    chosen (``preference >= 1``) and one rejected (``preference <= -1``)
    row to be emitted.  Incomplete pairs are skipped.
    """
    # Group first — DPO needs matched pairs, order of rows in the cursor
    # is undefined.
    groups: dict[str, dict[str, dict]] = {}
    for row in rows:
        pair_id = row.get("pair_id")
        preference = row.get("preference")
        if not pair_id or preference is None or preference == 0:
            continue
        bucket = groups.setdefault(pair_id, {})
        if preference >= 1:
            bucket.setdefault("chosen", row)
        elif preference <= -1:
            bucket.setdefault("rejected", row)

    for pair_id, bucket in groups.items():
        if "chosen" not in bucket or "rejected" not in bucket:
            continue
        chosen_prepared = prepared_trace(bucket["chosen"], repo_root)
        rejected_prepared = prepared_trace(bucket["rejected"], repo_root)
        prompt = chosen_prepared.get("messages") or \
            rejected_prepared.get("messages") or []
        chosen_msg = _assistant_message_for_sft(
            chosen_prepared.get("assistant_output") or {},
        )
        rejected_msg = _assistant_message_for_sft(
            rejected_prepared.get("assistant_output") or {},
        )
        out = {
            "prompt": prompt,
            "chosen": chosen_msg,
            "rejected": rejected_msg,
            "pair_id": pair_id,
            "pair_kind": chosen_prepared.get("pair_kind")
            or rejected_prepared.get("pair_kind"),
            "workspace_id": chosen_prepared.get("workspace_id"),
            "model_name": chosen_prepared.get("model_name"),
            "phase": chosen_prepared.get("phase"),
        }
        yield json.dumps(out, ensure_ascii=False) + "\n"


# Format dispatch table — used by the export router.
FORMATS = {
    "raw": to_raw_jsonl,
    "sft": to_sft_jsonl,
    "dpo": to_dpo_jsonl,
    "kto": to_kto_jsonl,
}

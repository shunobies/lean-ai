"""Tests for SFT / DPO / KTO / raw JSONL export converters."""

import json

from lean_ai.training.export_formats import (
    anonymize_row,
    to_dpo_jsonl,
    to_kto_jsonl,
    to_raw_jsonl,
    to_sft_jsonl,
    workspace_id,
)


def _row(**overrides):
    """Helper: base training_traces row dict (JSON cols still strings)."""
    base = {
        "id": 42,
        "trace_uuid": "t-1",
        "session_id": "sess-abc",
        "phase": "implementation",
        "model_name": "qwen3-coder:30b",
        "provider": "ollama",
        "messages": json.dumps(
            [
                {"role": "user", "content": "hello"},
            ]
        ),
        "assistant_output": json.dumps(
            {
                "content": "hi there",
                "thinking": "",
                "tool_calls": [],
            }
        ),
        "outcome": "success",
        "pair_id": None,
        "preference": None,
        "pair_kind": None,
        "reward": None,
        "tokens_prompt": 10,
        "tokens_completion": 5,
        "latency_ms": 120,
        "scrubbed": 1,
        "created_at": "2026-04-20T12:00:00Z",
    }
    base.update(overrides)
    return base


def _consume(gen):
    """Collect JSONL generator output into a list of parsed dicts."""
    return [json.loads(line) for line in gen]


def test_workspace_id_is_stable_16_chars(tmp_path):
    first = workspace_id(str(tmp_path))
    second = workspace_id(str(tmp_path))
    assert first == second
    assert len(first) == 16
    assert all(c in "0123456789abcdef" for c in first)


def test_workspace_id_differs_per_workspace(tmp_path):
    other = tmp_path.parent / "other-workspace"
    other.mkdir(exist_ok=True)
    assert workspace_id(str(tmp_path)) != workspace_id(str(other))


def test_anonymize_row_strips_raw_path(tmp_path):
    root = str(tmp_path)
    row = {
        "id": 1,
        "session_id": "sess-abc",
        "messages": json.dumps(
            [
                {"role": "user", "content": f"read file at {root}/secret/config.py"},
            ]
        ),
    }
    out = anonymize_row(row, root)
    assert "id" not in out
    assert root not in out["messages"]
    assert out["session_id"] != "sess-abc"
    assert "workspace-" in out["messages"]
    assert out["workspace_id"] == workspace_id(root)


def test_raw_jsonl_one_line_per_row(tmp_path):
    rows = [_row(trace_uuid="t-1"), _row(trace_uuid="t-2")]
    lines = list(to_raw_jsonl(rows, str(tmp_path)))
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["trace_uuid"] == "t-1"
    assert parsed[1]["trace_uuid"] == "t-2"
    # messages JSON is hydrated, not still a stringly value
    assert isinstance(parsed[0]["messages"], list)


def test_sft_only_emits_success_rows(tmp_path):
    rows = [
        _row(trace_uuid="good", outcome="success"),
        _row(trace_uuid="bad", outcome="rejected"),
    ]
    emitted = _consume(to_sft_jsonl(rows, str(tmp_path)))
    assert len(emitted) == 1
    assert emitted[0]["phase"] == "implementation"
    assert emitted[0]["messages"][-1]["role"] == "assistant"
    assert emitted[0]["messages"][-1]["content"] == "hi there"


def test_sft_preserves_thinking_when_enabled(tmp_path, monkeypatch):
    from lean_ai.config import settings as cfg

    monkeypatch.setattr(cfg, "capture_thinking", True)
    row = _row(
        assistant_output=json.dumps(
            {
                "content": "answer",
                "thinking": "step 1, step 2",
                "tool_calls": [],
            }
        ),
    )
    emitted = _consume(to_sft_jsonl([row], str(tmp_path)))
    assistant = emitted[0]["messages"][-1]
    assert assistant.get("reasoning_content") == "step 1, step 2"


def test_sft_strips_thinking_when_disabled(tmp_path, monkeypatch):
    from lean_ai.config import settings as cfg

    monkeypatch.setattr(cfg, "capture_thinking", False)
    row = _row(
        assistant_output=json.dumps(
            {
                "content": "answer",
                "thinking": "internal monologue",
                "tool_calls": [],
            }
        ),
    )
    emitted = _consume(to_sft_jsonl([row], str(tmp_path)))
    assistant = emitted[0]["messages"][-1]
    assert "reasoning_content" not in assistant


def test_kto_emits_binary_labels(tmp_path):
    rows = [
        _row(trace_uuid="chosen", preference=1),
        _row(trace_uuid="rejected", preference=-1),
        _row(trace_uuid="neutral", preference=0),  # skipped
        _row(trace_uuid="unknown", preference=None),  # skipped
    ]
    emitted = _consume(to_kto_jsonl(rows, str(tmp_path)))
    labels = {(e["completion"]["content"], e["label"]) for e in emitted}
    # Both chosen and rejected happen to have content "hi there"; label differs
    assert ("hi there", True) in labels
    assert ("hi there", False) in labels
    assert len(emitted) == 2


def test_dpo_emits_matched_pairs(tmp_path):
    chosen_output = json.dumps(
        {
            "content": "good answer",
            "thinking": "",
            "tool_calls": [],
        }
    )
    rejected_output = json.dumps(
        {
            "content": "bad answer",
            "thinking": "",
            "tool_calls": [],
        }
    )
    rows = [
        _row(trace_uuid="c1", pair_id="p-1", preference=1, assistant_output=chosen_output),
        _row(trace_uuid="r1", pair_id="p-1", preference=-1, assistant_output=rejected_output),
    ]
    emitted = _consume(to_dpo_jsonl(rows, str(tmp_path)))
    assert len(emitted) == 1
    pair = emitted[0]
    assert pair["chosen"]["content"] == "good answer"
    assert pair["rejected"]["content"] == "bad answer"
    assert pair["pair_id"] == "p-1"


def test_dpo_skips_incomplete_pairs(tmp_path):
    rows = [
        _row(trace_uuid="only-chosen", pair_id="orphan", preference=1),
        # no matching rejected — skip
    ]
    emitted = _consume(to_dpo_jsonl(rows, str(tmp_path)))
    assert emitted == []


def test_dpo_skips_rows_without_pair_id(tmp_path):
    rows = [
        _row(trace_uuid="a", pair_id=None, preference=1),
        _row(trace_uuid="b", pair_id=None, preference=-1),
    ]
    emitted = _consume(to_dpo_jsonl(rows, str(tmp_path)))
    assert emitted == []


def test_dpo_ignores_zero_preference(tmp_path):
    rows = [
        _row(trace_uuid="a", pair_id="p", preference=1),
        _row(trace_uuid="b", pair_id="p", preference=0),
    ]
    emitted = _consume(to_dpo_jsonl(rows, str(tmp_path)))
    assert emitted == []


def test_anonymize_row_handles_missing_columns(tmp_path):
    # Sparse row (all JSON cols absent) should not crash
    row = {"id": 1, "session_id": "x", "trace_uuid": "t-x"}
    out = anonymize_row(row, str(tmp_path))
    assert "workspace_id" in out
    assert out["trace_uuid"] == "t-x"


def test_session_id_is_hashed_consistently(tmp_path):
    a = anonymize_row({"id": 1, "session_id": "sess-abc"}, str(tmp_path))
    b = anonymize_row({"id": 2, "session_id": "sess-abc"}, str(tmp_path))
    assert a["session_id"] == b["session_id"]
    assert a["session_id"] != "sess-abc"
    # Different session, different hash
    c = anonymize_row({"id": 3, "session_id": "sess-xyz"}, str(tmp_path))
    assert a["session_id"] != c["session_id"]


def test_workspace_id_respects_salt(tmp_path, monkeypatch):
    from lean_ai.config import settings as cfg

    root = str(tmp_path)
    monkeypatch.setattr(cfg, "export_workspace_salt", "")
    unsalted = workspace_id(root)

    monkeypatch.setattr(cfg, "export_workspace_salt", "pepper")
    salted = workspace_id(root)
    assert unsalted != salted

# Training Pipeline

Lean AI captures every workflow decision, validation fix, and workflow
event to a **local** training archive (`.lean_ai/training.db`) from the
moment you first run a session.  Nothing leaves the machine until you
explicitly enable export by setting `LEAN_AI_EXPORT_API_KEY`.  This doc
covers:

1. What the archive contains and how to inspect it.
2. How to export SFT / DPO / KTO datasets for LoRA fine-tuning.
3. How to train a LoRA adapter and hot-swap it in vLLM.
4. How lean-ai-serve (or any coordinator) aggregates across workspaces.

## 1. Archive Contents

Every Lean AI workspace with `enable_training_capture=True` (default)
writes to `.lean_ai/training.db`, a SQLite file separate from the
workspace state DB.  Schema defined in [backend/src/lean_ai/training/db.py](../backend/src/lean_ai/training/db.py):

| table | what it holds | primary use |
|---|---|---|
| `training_traces` | one row per LLM turn with messages + assistant_output preserved | SFT / DPO / KTO sources |
| `plan_decisions` | each approve / reject / cancel with `plan_before`, `feedback`, `plan_after` | DPO pairs (reject→revised-approve) |
| `validation_attempts` | per-attempt `(failures_before, diagnosis, fix_tool_calls, failures_after)` | DPO pairs (fail→succeed) |
| `workflow_events` | cancellations, TDD disputes, execution-complete markers, plus four in-loop guardrail events and `session_start` model-layout fingerprints | KTO labels / behaviour analysis |
| `tool_executions` | one row per tool invocation (arguments + result preview + success/latency) | DPO pairs (failed-call → successful-call on same session + tool) |
| `tool_compressions` | worker-model tool-output summarisation pairs — populated only when the off-by-default compression feature is active | distillation training for a smaller worker model |
| `clarifications` | Phase 1 `request_clarification` Q/A pairs with outcome | SFT for the "when to ask vs proceed" behaviour |
| `phase2_syntheses` | raw observations + scratchpad + journal → validated `FileSummary` | SFT for structured-summary generation |
| `diff_decisions` | user accept/reject decision per proposed file edit | binary-labeled preference data |
| `redaction_audit` | every PII/secret match the scrubber caught | retroactive data-quality forensics |

### `training_traces` per-turn schema (v2)

Rows now carry a `role` field and `turn_index` alongside the existing
`phase`.  Every Tier-S/A wiring point populates them so training data
can be partitioned before fine-tuning:

| column | values | source |
|---|---|---|
| `phase` | `planning.phase1` · `planning.phase2` · `planning.phase3` · `implementation` · `validation_fix` · `fix` · `request` · `fix.investigate` · `tdd.write` · `tdd.review` · `tdd.implement` · `tdd.dispute` · `chat` | `telemetry_context['phase']` passed into `chat_with_tools` |
| `role` | `primary` · `expert` · `worker` · `request` | `telemetry_context['role']` — distinct from `phase` because the same phase can swap roles (e.g. expert escalation on final validation retry) |
| `turn_index` | 0-based index within the call | set automatically by `chat_with_tools` |

The opt-in `telemetry_context` dict threads through every call site
documented in [`incomplete.md`](../incomplete.md#training-archive-).
Callers that don't pass it retain the pre-Tier-S behaviour (no capture,
no events — the facade is a no-op on that path).

### `workflow_events` event taxonomy

| event_type | fires from | payload highlights |
|---|---|---|
| `session_start` | [`pipeline.py`](../backend/src/lean_ai/workflow/pipeline.py) top of `run_workflow` | `mode`, `primary_model`, `primary_provider`, `expert_model`, `expert_provider`, `request_model`, `context_window`, `tdd_enabled` |
| `loop_detected` | [`facade.py`](../backend/src/lean_ai/llm/facade.py) when N identical tool calls trip the loop detector | `tool_name`, `count`, `turn_index`, `phase`, `role` |
| `context_refresh` | [`facade.py`](../backend/src/lean_ai/llm/facade.py) when token budget crosses `LEAN_AI_REFRESH_THRESHOLD` | `turn_index`, `prompt_tokens_before`, `context_window`, `phase`, `role` |
| `reminder_injected` | [`facade.py`](../backend/src/lean_ai/llm/facade.py) on periodic task-reminder nudge | `turn_index`, `reminder_chars`, `phase`, `role` |
| `claim_unverified` | [`facade.py`](../backend/src/lean_ai/llm/facade.py) on repeated test failures + unverified-claim regex hit | `turn_index`, `recent_test_failures`, `phase`, `role` |
| `cancellation` | [`routers/workflow.py`](../backend/src/lean_ai/routers/workflow.py) on user cancel | `task`, `mode`, `tail_messages` (last 5 conversation_log rows, 800-char preview each) |
| `tdd_dispute` | [`tdd.py`](../backend/src/lean_ai/workflow/tdd.py) when expert evaluates a dispute | `test_file`, `test_function`, `decision` (accepted/rejected), `explanation` |
| `execution_complete` | [`executor.py`](../backend/src/lean_ai/workflow/executor.py) at the end of plan execution | `task`, `files_modified_count`, `validation_passed`, branch names |

Every in-loop event carries an optional `trace_uuid` linking it to the
exact `training_traces` row whose turn triggered it.

### Scrubbing guarantee

Before any row is inserted, the payload passes through
[`training/scrubber.py`](../backend/src/lean_ai/training/scrubber.py):

- Known secret shapes (`sk-proj-…`, `sk-ant-…`, `ghp_…`, `xoxb-…`,
  `AKIA…`, JWTs, SSH private keys, `LEAN_AI_*_KEY=…` lines).
- `Authorization: Bearer …` headers.
- High-entropy tokens (Shannon > 4 bits/char, ≥ 32 chars).
- Emails.

Matches produce a `redaction_audit` row with `match_preview =
sha256(match)[:12]` — the raw value is **never** stored in the audit
log.  Default mode is **fail-closed**: any scrubber exception drops the
trace rather than risk leaking unscrubbed data.  Set
`LEAN_AI_SCRUBBING_STRICT=false` to degrade to lenient mode (writes
with `scrubbed=0` so exports can filter).

### Retention

`training_retention_days` (default 365) bounds growth.  A retention
pass runs opportunistically at session end — throttled to once per
workspace per hour.  To reclaim disk immediately:

```python
from lean_ai.training.maintenance import run_retention_pass
await run_retention_pass(repo_root, force=True)
```

Or simply delete `.lean_ai/training.db` at any time — workspace state
is unaffected.

## 2. Exporting Data

### Enable the endpoint

```bash
# Add to backend/.env (or set as environment variable)
LEAN_AI_EXPORT_API_KEY=las-export-$(openssl rand -hex 24)
```

Without this key, all `/api/export/*` routes return `503 Service
Unavailable`.  With the key set, authenticated callers can pull:

| endpoint | purpose |
|---|---|
| `GET /api/export/workspace-id?repo_root=…` | deterministic 16-char hash for cross-workspace aggregation |
| `GET /api/export/manifest?repo_root=…` | counts by model, phase, outcome, role; memory curation breakdown; counts for every new table (cached 60s) |
| `GET /api/export/traces?repo_root=…&format=sft\|dpo\|kto\|raw` | per-turn traces; streams JSONL |
| `GET /api/export/memories?repo_root=…&curation_status=…` | anonymized curated memories |
| `GET /api/export/events?repo_root=…&event_type=…` | workflow events |
| `GET /api/export/tool-executions?repo_root=…&format=raw\|dpo_pairs&tool_name=…` | per-tool-call history; `dpo_pairs` heuristically pairs a failed call with the next successful call on the same session + tool |
| `GET /api/export/tool-compressions?repo_root=…&tool_name=…` | worker compression (raw, summary) pairs |
| `GET /api/export/clarifications?repo_root=…&outcome=…` | Phase 1 Q/A pairs |
| `GET /api/export/phase2-syntheses?repo_root=…` | exploration-to-structured-summary pairs |
| `GET /api/export/diff-decisions?repo_root=…&accepted=0\|1` | per-file accept/reject decisions |

A POST endpoint pairs with the WS diff message so the extension can
report the user's decision:

| endpoint | purpose |
|---|---|
| `POST /api/diffs/decision` | body: `{repo_root, session_id, file_path, accepted, diff_hash?, note?, trace_uuid?}` — writes a row to `diff_decisions`. Idempotent-safe (the archive is append-only; duplicate posts create duplicate rows so the extension should dedupe locally on `diff_hash`). |

The `diff` WebSocket message now includes a `diff_hash` field
(`sha256(diff)[:16]`) so the extension can pair its accept/reject
post with the exact diff the model proposed. See
[`ws_messages.py`](../backend/src/lean_ai/workflow/ws_messages.py).

### Quick peek

```bash
KEY=las-export-...
curl -s -H "Authorization: Bearer $KEY" \
  "http://localhost:8422/api/export/manifest?repo_root=$(pwd)" | jq .
```

### Build a dataset

```bash
# Supervised fine-tuning (only rows with outcome=success)
curl -s -H "Authorization: Bearer $KEY" \
  "http://localhost:8422/api/export/traces?repo_root=$(pwd)&format=sft&limit=10000" \
  > data/sft.jsonl

# Direct Preference Optimization (matched rejected→approved pairs)
curl -s -H "Authorization: Bearer $KEY" \
  "http://localhost:8422/api/export/traces?repo_root=$(pwd)&format=dpo&limit=10000" \
  > data/dpo.jsonl

# Kahneman-Tversky Optimization (binary labels; tolerates imbalance)
curl -s -H "Authorization: Bearer $KEY" \
  "http://localhost:8422/api/export/traces?repo_root=$(pwd)&format=kto&limit=10000" \
  > data/kto.jsonl
```

Format details in [`training/export_formats.py`](../backend/src/lean_ai/training/export_formats.py):

- **SFT**: one `{messages: [...], phase, model_name, workspace_id}` per
  line.  Assistant message includes `reasoning_content` (thinking
  blocks) when `LEAN_AI_CAPTURE_THINKING=true` — preserved for reasoning-
  model LoRA targeting gpt-oss or Qwen3 thinking mode.
- **DPO**: `{prompt, chosen, rejected, pair_id, pair_kind,
  workspace_id, model_name, phase}` — only emitted when both sides of a
  pair exist.
- **KTO**: `{prompt, completion, label: bool, workspace_id, phase,
  model_name, pair_kind}` — skips neutral `preference=0` rows.
- **Raw**: the underlying `training_traces` row with workspace_id
  hashed.

### Anonymization of exported data

Every exported row is rewritten before emission:

1. `session_id` → `sha256(salt:session_id)[:12]`
2. `repo_root` and its basename → `/workspace-<id>` wherever they appear
3. For `/api/export/memories`: a secondary pass (
   [`memory_anonymizer.py`](../backend/src/lean_ai/training/memory_anonymizer.py))
   builds a workspace symbol table from tool_logs + directory listing
   and rewrites matching file paths, module names, and symbols.
   Memories where >40% of content was redacted (configurable via
   `LEAN_AI_MEMORY_EXPORT_DROP_THRESHOLD`) are dropped entirely — they
   were too workspace-specific to generalize.

Set a stable salt if you want workspace_id stability across reboots or
across coordinators:

```bash
LEAN_AI_EXPORT_WORKSPACE_SALT=any-non-empty-string
```

## 3. LoRA Fine-Tuning Recipe

### Prerequisites

- A single H100-class GPU (LoRA on gpt-oss-120b fits in ~80 GB with
  4-bit base + 16-bit adapter).
- ~1 000+ high-quality examples per archetype.  Smaller datasets
  produce unstable adapters; favour **KTO over DPO** when label
  counts are imbalanced.
- Tooling: [Axolotl](https://github.com/axolotl-ai-cloud/axolotl),
  [Unsloth](https://github.com/unslothai/unsloth), or
  [TRL](https://github.com/huggingface/trl) — pick whichever your team
  already operates.

### Sketch (Axolotl DPO)

```yaml
# axolotl/gpt-oss-120b-dpo.yml
base_model: openai/gpt-oss-120b
load_in_4bit: true
adapter: lora
lora_r: 64
lora_alpha: 128
lora_target_modules: "all-linear"

rl: dpo
datasets:
  - path: data/dpo.jsonl
    type: chat_template.argilla_chat   # matches Lean AI's DPO JSONL shape

micro_batch_size: 1
gradient_accumulation_steps: 16
num_epochs: 1
learning_rate: 5e-6
save_steps: 100
output_dir: adapters/lean-ai-planner-v1
```

```bash
accelerate launch -m axolotl.cli.train axolotl/gpt-oss-120b-dpo.yml
```

### Sketch (TRL SFT on thinking-model data)

Lean AI's SFT JSONL preserves thinking traces in
`messages[-1].reasoning_content`.  TRL ≥ 0.12 accepts this shape
natively for gpt-oss / Qwen3 thinking-mode training — see
[TRL SFT trainer docs](https://huggingface.co/docs/trl/en/sft_trainer).

## 4. vLLM Adapter Hot-Swap

vLLM can load LoRA adapters into a running server without restart,
provided you launch with the right flags:

```bash
VLLM_ALLOW_RUNTIME_LORA_UPDATING=true vllm serve openai/gpt-oss-120b \
  --enable-lora \
  --max-loras 4 \
  --max-lora-rank 64 \
  --kv-cache-dtype turboquant_k8v4 \
  --speculative-config '{"method":"eagle3","model":"nvidia/gpt-oss-120b-Eagle3-v2","num_speculative_tokens":3}'
```

Push the trained adapter:

```bash
curl -X POST http://localhost:8420/v1/load_lora_adapter \
  -H "Content-Type: application/json" \
  -d '{
    "lora_name": "lean-ai-planner-v1",
    "lora_path": "/path/to/adapters/lean-ai-planner-v1"
  }'
```

Route Lean AI's expert phases (3–5) at the adapter by setting:

```bash
LEAN_AI_SERVE_EXPERT_MODEL=lean-ai-planner-v1
```

Lean AI's OpenAI-compatible provider will pass the adapter name as the
`model` field on every request — vLLM's `--enable-lora` machinery
resolves it to `(base=gpt-oss-120b, adapter=lean-ai-planner-v1)`
automatically.

Rollback: unload with `POST /v1/unload_lora_adapter` or just point
`LEAN_AI_SERVE_EXPERT_MODEL` back at the base model.

## 5. Aggregation Across Workspaces (lean-ai-serve side)

lean-ai-serve is a separate project, but the protocol between it and
this codebase is worth documenting here:

1. **Registration**: each workspace hits `GET /api/export/workspace-id`
   and posts the returned id to the coordinator alongside its backend
   URL + export key.
2. **Polling**: coordinator pulls `/api/export/manifest` on a schedule
   to discover new work; if counts differ from the previous snapshot,
   it pulls `/api/export/traces?cursor=<last-seen-id>` with a cursor
   for incremental fetches.
3. **Merge**: rows from different workspaces share no primary keys and
   are already anonymized — concatenation is safe.  The coordinator can
   group by `workspace_id` for per-user training eval, or pool for a
   cross-user model.
4. **Sharing back**: trained adapters are pushed to a shared S3-like
   store; workspaces opt in by setting `LEAN_AI_SERVE_EXPERT_MODEL` to
   the shared adapter name.

Cross-user aggregation shortens the "enough data for useful LoRA"
timeline from ~6 months/user to a few weeks across a team.  It also
surfaces gotchas faster: if three workspaces independently extract the
same `fix_pattern` memory, the extractor's auto-promotion logic (
[`training/maintenance.py`](../backend/src/lean_ai/training/maintenance.py))
flags it for cross-workspace broadcast.

## 6. Audit & Forensics

If a new secret format is disclosed that the scrubber doesn't catch:

```sql
-- Open .lean_ai/training.db
SELECT source_id, pattern_name, match_preview, created_at
FROM redaction_audit
WHERE pattern_name = 'generic_high_entropy';
```

The `match_preview` is a 12-char sha256 prefix — enough to correlate
with known-bad values without ever storing the raw secret.  If a row
matches a newly-disclosed leak, delete the traces referencing it:

```sql
DELETE FROM training_traces WHERE trace_uuid IN (
    SELECT source_id FROM redaction_audit
    WHERE match_preview = '<hash-of-the-leaked-token>'
);
```

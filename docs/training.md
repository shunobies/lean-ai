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
| `workflow_events` | cancellations, TDD disputes, execution-complete markers | KTO labels / behaviour analysis |
| `redaction_audit` | every PII/secret match the scrubber caught | retroactive data-quality forensics |

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
| `GET /api/export/manifest?repo_root=…` | counts by model, phase, outcome; memory curation breakdown (cached 60s) |
| `GET /api/export/traces?repo_root=…&format=sft\|dpo\|kto\|raw` | streams JSONL |
| `GET /api/export/memories?repo_root=…&curation_status=…` | anonymized curated memories |
| `GET /api/export/events?repo_root=…&event_type=…` | workflow events |

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

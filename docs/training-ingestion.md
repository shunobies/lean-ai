# Training Data Ingestion Guide for `lean_ai_serve`

**Audience:** engineers building the `lean_ai_serve` side of the
training loop — the coordinator that polls every registered Lean AI
workspace, pulls new training data, and assembles datasets for
fine-tuning.

**This doc is the contract between Lean AI (data producer) and
`lean_ai_serve` (data consumer).** It describes every exported shape
byte-for-byte, plus an end-to-end pull loop. For the upstream
capture-pipeline design, local DB schema, scrubbing guarantees, and
LoRA recipes, see [training.md](training.md).

## 1. Quick start (5-minute pull loop)

```python
import httpx
import asyncio
import json

API = "http://workstation.local:8422/api/export"
HEADERS = {"Authorization": "Bearer <LEAN_AI_EXPORT_API_KEY>"}
REPO_ROOT = "/home/alex/Code/lean_ai"   # the workspace the user registered

async def pull():
    async with httpx.AsyncClient(timeout=300.0) as client:
        # 1. Identify the workspace (stable across polls)
        r = await client.get(
            f"{API}/workspace-id",
            params={"repo_root": REPO_ROOT}, headers=HEADERS,
        )
        workspace_id = r.json()["workspace_id"]

        # 2. See what's new
        r = await client.get(
            f"{API}/manifest",
            params={"repo_root": REPO_ROOT}, headers=HEADERS,
        )
        manifest = r.json()
        # manifest example fields: total_traces, by_model, by_role,
        # tool_executions, tool_compressions, clarifications,
        # phase2_syntheses, diff_decisions, memories.total, ...

        # 3. Pull incremental traces — format=raw keeps everything
        #    the trainers need; translate once on your side.
        cursor = None  # store per-workspace in your coordinator DB
        while True:
            r = await client.get(
                f"{API}/traces",
                params={
                    "repo_root": REPO_ROOT,
                    "format": "raw",
                    "limit": 1000,
                    **({"cursor": cursor} if cursor else {}),
                },
                headers=HEADERS,
            )
            lines = [ln for ln in r.text.splitlines() if ln]
            if not lines:
                break
            for raw in lines:
                row = json.loads(raw)
                yield ("trace", workspace_id, row)
            cursor = str(row_id(lines[-1]))  # see note below

asyncio.run(list(pull()))
```

**Cursor semantics.** Every paginated endpoint uses `WHERE id > ?
ORDER BY id ASC LIMIT ?`. The `id` column is an auto-increment primary
key stripped from the exported row for anonymity, so consumers must
track the cursor client-side: remember the largest `id` you saw from
the raw DB, or query `/manifest` until the table's count exceeds the
previous snapshot, then re-pull from the known position. The simplest
approach: keep a per-`(workspace_id, table)` "last seen row_id" map in
the coordinator's own DB.

> **Note on row-id tracking.** Raw-format exports for `training_traces`
> echo back the DB `id` as part of the anonymized row so cursors work
> naturally. Other tables strip `id` by default; for those, use
> `since=<ISO8601 timestamp>` as the incremental filter.

## 2. Authentication

All `/api/export/*` endpoints require:

```http
Authorization: Bearer <LEAN_AI_EXPORT_API_KEY>
```

Behaviour:

- Missing key in workspace config → `503 Service Unavailable` (export
  is disabled by default — capture is on, export opts in explicitly).
- Present but wrong → `401`.
- Present and correct → 200 + streaming `application/x-ndjson`.

Tokens are compared via `hmac.compare_digest`, so length does not
matter as long as it matches exactly.

## 3. Anonymization

Every exported row is pre-processed before leaving the backend:

- `session_id` → `sha256(f"{salt}:session:{session_id}")[:12]`
- `repo_root` (absolute path) and its basename → `/workspace-<id>` —
  applied recursively to every string leaf in nested JSON columns
  (`messages`, `assistant_output`, `arguments`, `payload`,
  `plan_before`, `plan_after`, `file_summary`, etc.).
- DB row `id` is stripped (except for `training_traces` raw, where it
  is preserved in the `id` field for cursor use).
- `workspace_id` is injected on every row (16-char sha256 prefix).

The salt comes from `LEAN_AI_EXPORT_WORKSPACE_SALT`. To keep
`workspace_id` stable across coordinator reboots, set the same salt on
every workspace (e.g. a per-user secret distributed by your service).

Content-scrubbing happens upstream — by the time data reaches the
export layer, it's already been through the fail-closed scrubber
documented in [training.md#scrubbing-guarantee](training.md#scrubbing-guarantee).

## 4. Endpoint reference

### `/workspace-id`

```http
GET /api/export/workspace-id?repo_root=<path>
```

```json
{"workspace_id": "7f3c9e4a8b1d2f56"}
```

Deterministic given the same `(salt, repo_root)` pair. Use as the
partition key when joining data across workspaces.

### `/manifest`

```http
GET /api/export/manifest?repo_root=<path>
```

Cached 60s per workspace. Counts are the source of truth for what's
available; compare against your cached snapshot to detect when to pull
more.

```json
{
  "total_traces": 1423,
  "by_model": {"qwen3-coder:30b-a3b-q8_0": 988, "qwen3-coder-next:q8_0": 435},
  "by_phase": {"planning.phase1": 145, "planning.phase2": 287, "implementation": 712, ...},
  "by_role": {"primary": 1100, "expert": 253, "request": 42, "worker": 28},
  "by_outcome": {"success": 1380, "error": 43},
  "scrubbed_count": 1423,
  "oldest": "2026-03-01T09:12:03+00:00",
  "newest": "2026-04-23T14:05:41+00:00",
  "plan_decisions": 87,
  "validation_attempts": 201,
  "workflow_events": 342,
  "tool_executions": 9842,
  "tool_compressions": 0,
  "clarifications": 31,
  "phase2_syntheses": 87,
  "diff_decisions": 214,
  "memories": {"total": 64, "by_status": {"user_confirmed": 41, "auto": 23}},
  "workspace_id": "7f3c9e4a8b1d2f56"
}
```

### `/traces` (SFT / DPO / KTO / raw)

```http
GET /api/export/traces?repo_root=<path>&format=<raw|sft|dpo|kto>&limit=1000&cursor=<id>
     &model=<str>&phase=<str>&outcome=<str>&since=<iso8601>
```

| format | shape (one per line) |
|---|---|
| `raw` | full `training_traces` row, JSON columns parsed |
| `sft` | `{"messages": [...], "workspace_id", "phase", "model_name"}` — only `outcome='success'` rows; assistant message includes `reasoning_content` when `LEAN_AI_CAPTURE_THINKING=true` |
| `dpo` | `{"prompt", "chosen", "rejected", "pair_id", "pair_kind", "workspace_id", "model_name", "phase"}` — only emits pairs where both sides exist |
| `kto` | `{"prompt", "completion", "label": bool, "workspace_id", "phase", "model_name", "pair_kind"}` — skips `preference=0` rows |

For dataset assembly, combine all workspaces' outputs by `workspace_id`
partition or pool them — rows from different workspaces share no
primary keys after anonymization.

### `/tool-executions` (NEW)

```http
GET /api/export/tool-executions?repo_root=<path>
     &format=<raw|dpo_pairs>&tool_name=<str>&phase=<str>&success=<0|1>
     &since=<iso8601>&cursor=<id>&limit=<n>
```

**format=raw** (default):

```json
{
  "session_id": "a1b2c3d4e5f6",
  "trace_uuid": "6a4...",
  "phase": "implementation",
  "turn_index": 3,
  "tool_name": "edit_file",
  "arguments": {"path": "/workspace-7f3c9e4a8b1d2f56/src/foo.py", "search": "old", "replace": "new"},
  "result_preview": "Modified foo.py",
  "result_length": 15,
  "success": 1,
  "latency_ms": 22,
  "pair_id": null,
  "preference": null,
  "created_at": "2026-04-23T14:05:41+00:00",
  "workspace_id": "7f3c9e4a8b1d2f56"
}
```

**format=dpo_pairs**: the backend groups by `(session_id, tool_name)`,
pairs the most recent failure with the next success in the same group,
and emits one row per pair:

```json
{
  "prompt_hint": "edit_file",
  "session_id": "a1b2c3d4e5f6",
  "phase": "implementation",
  "rejected": {
    "arguments": {"path": "/workspace-.../foo.py", "search": "old", "replace": "new"},
    "result_preview": "ERROR: string 'old' not found"
  },
  "chosen": {
    "arguments": {"path": "/workspace-.../foo.py", "search": "oldvalue", "replace": "newvalue"},
    "result_preview": "Modified foo.py"
  },
  "workspace_id": "7f3c9e4a8b1d2f56"
}
```

**Training use:** fine-tune the model to call tools with argument
shapes that actually match reality. The heuristic pairing is weak on
purpose — if you need stricter pairing (e.g. same `path` argument, or
edit-distance threshold on arguments), pull `format=raw` and pair
downstream.

**Volume caveat:** tool_executions is the highest-volume table —
expect 5-50× more rows than training_traces. Use the `since` filter
plus a cursor to keep each pull bounded.

### `/tool-compressions` (NEW)

```http
GET /api/export/tool-compressions?repo_root=<path>&tool_name=<str>
     &since=<iso8601>&cursor=<id>&limit=<n>
```

```json
{
  "session_id": "a1b2c3d4e5f6",
  "phase": "planning.phase2",
  "tool_name": "read_file",
  "raw_output": "<full pre-compression text>",
  "raw_length": 8421,
  "compressed_output": "<worker summary>",
  "compressed_length": 612,
  "compression_ratio": 0.0727,
  "worker_model": "qwen2.5-coder:7b-instruct-q8_0",
  "worker_provider": "ollama",
  "followup_progress": null,
  "created_at": "...",
  "workspace_id": "..."
}
```

**Populated only when the user opts into worker compression** — off by
default per the design trade-off in
[`incomplete.md`](../incomplete.md#worker_implementation_unfinished---tool-output-compression-deferred).
Tables are created eagerly so activation produces data immediately;
expect 0 rows from most workspaces.

**Training use:** distill the primary model's ability to summarize
large tool outputs into a small worker. The pair `(raw_output,
compressed_output)` is directly usable as SFT.
`followup_progress` is reserved for a future enrichment pass that
correlates with the next primary turn — today it's always `null`.

### `/clarifications` (NEW)

```http
GET /api/export/clarifications?repo_root=<path>&outcome=<str>
     &since=<iso8601>&cursor=<id>&limit=<n>
```

```json
{
  "session_id": "...",
  "phase": "planning.phase1",
  "task": "add audit logging to the user service",
  "question": "should audit entries be persisted to the main DB or a dedicated audit DB?",
  "answer": "main DB for now — we can split later",
  "outcome": "answered",
  "trace_uuid": "6a4...",
  "created_at": "...",
  "workspace_id": "..."
}
```

`outcome` values: `answered`, `empty`, `cancelled`, `disconnected`,
`error`.

**Training use:** supervised data for "when the task is under-specified,
ask a specific clarifying question" — the triple
`(task, question, answer)` is directly usable as SFT. Filter to
`outcome=answered` for the cleanest signal.

### `/phase2-syntheses` (NEW)

```http
GET /api/export/phase2-syntheses?repo_root=<path>&since=<iso8601>&cursor=<id>&limit=<n>
```

```json
{
  "session_id": "...",
  "task": "add audit log",
  "scope": "<rendered scope markdown>",
  "observations": [
    {"file_path": "/workspace-.../a.py", "role": "modify", "reason": "...", "relevant_sections": "lines 120-180", "key_snippets": ["..."]},
    ...
  ],
  "scratchpad": "<scratchpad text>",
  "journal": "<journal text>",
  "exploration_output": "<prose from chat loop>",
  "file_summary": {
    "files_to_modify": ["/workspace-.../a.py"],
    "files_to_create": [],
    "files_read_for_context": [...],
    "missing_infrastructure": [],
    "verified_references": [...],
    "assumptions_resolved": [...],
    "notes": "..."
  },
  "trace_uuid": "...",
  "created_at": "...",
  "workspace_id": "..."
}
```

**Highest-quality supervised signal in the archive.** Each row is a
validated `(raw_evidence → structured_summary)` pair — exactly the
shape you want for fine-tuning the Phase 2 synthesis LLM call.
Payloads are large; the endpoint caps at 2000 rows per request.

### `/diff-decisions` (NEW)

```http
GET /api/export/diff-decisions?repo_root=<path>&accepted=<0|1>
     &since=<iso8601>&cursor=<id>&limit=<n>
```

```json
{
  "session_id": "...",
  "file_path": "/workspace-.../src/foo.py",
  "accepted": 0,
  "diff_hash": "abc123deadbeef45",
  "note": "introduces regression in the error handler",
  "trace_uuid": "...",
  "created_at": "...",
  "workspace_id": "..."
}
```

**Training use:** binary preference labels per proposed edit. Join by
`diff_hash` back to the `tool_executions` row whose `arguments.search`
and `arguments.replace` produced the diff (the extension echoes the
hash it received on the WS `diff` message). KTO is the natural
trainer here — the dataset will be imbalanced (most edits are
accepted).

### `/events`

```http
GET /api/export/events?repo_root=<path>&event_type=<str>&since=<iso8601>&cursor=<id>&limit=<n>
```

Payload is a JSON blob keyed by event_type. See
[training.md#workflow_events-event-taxonomy](training.md#workflow_events-event-taxonomy)
for the full enumeration. For training use specifically:

| event_type | training signal |
|---|---|
| `session_start` | model layout fingerprint — partition training data by `(primary_model, expert_model, request_model, context_window)` before fine-tuning |
| `loop_detected` | joined to `training_traces` by `trace_uuid`, gives you the turn where the model got stuck → a good DPO source when paired with the recovery turn |
| `context_refresh` | metadata for reasoning about when context management matters |
| `reminder_injected` | meta-signal for when the task-reminder guardrail was needed |
| `claim_unverified` | negative example: the model was about to assert something false; the next turn shows the correction |
| `cancellation` | negative signal — `tail_messages` (last 5 conversation entries) gives consumers the context the user gave up on |
| `tdd_dispute` | explicit user decision on a test-vs-code conflict; `decision=accepted` is a preference signal for the test, `rejected` for the code |
| `execution_complete` | workflow-level success/failure boundary |

### `/memories`

See [training.md#anonymization-of-exported-data](training.md#anonymization-of-exported-data)
— memories pass through a second anonymization pass (symbol table
built from workspace tool_logs) and drop entirely if more than 40% of
content was redacted.

## 5. Cross-workspace aggregation workflow

1. **Registration.** Workspace admin pastes the backend URL + export
   key into the coordinator UI. Coordinator calls
   `/workspace-id` once and stores
   `(workspace_id, backend_url, encrypted_api_key)` in its own DB.
   Never store the raw key in plaintext — Lean AI's export endpoint
   doesn't reveal the key on any other surface.

2. **Polling.** Every N minutes, the coordinator:
   1. Calls `/manifest` on each registered workspace (60s upstream
      cache means no point polling faster).
   2. Compares each table's count against the last pulled snapshot.
   3. For each table that grew, issues a paginated pull with the last
      known cursor (for `training_traces`) or `since=<iso8601>` (for
      other tables).
   4. Writes rows to the coordinator DB tagged with
      `(workspace_id, ingested_at)`. Rows are already anonymized and
      scrubbed upstream — no further processing required before
      training.

3. **Dataset materialisation.** On a separate schedule (daily,
   weekly) the coordinator assembles datasets:
   - **Planner SFT**: `SELECT ... FROM training_traces WHERE phase
     LIKE 'planning.%' AND outcome='success'` → convert to OpenAI
     chat format.
   - **Planner DPO**: `plan_decisions WHERE decision='approved' AND
     plan_before IS NOT NULL` → use `plan_before` as rejected,
     `plan_after` as chosen.
   - **Fix-loop KTO**: `validation_attempts` → label by `succeeded`.
   - **Tool-call DPO**: `tool_executions format=dpo_pairs` emitted
     directly, no post-processing needed.
   - **Worker distillation SFT**: `tool_compressions` →
     `(raw_output, compressed_output)` as `(prompt, completion)`.
   - **Phase 2 synthesis SFT**: `phase2_syntheses` → packed prompt
     of `(task, scope, observations, scratchpad, journal,
     exploration_output)` mapped to `file_summary` as structured
     completion.
   - **Diff preference KTO**: `diff_decisions` → label by `accepted`,
     use the associated `tool_executions` row (joined by `trace_uuid`
     or `diff_hash`) as the prompt+completion.
   - **Clarifier SFT**: `clarifications WHERE outcome='answered'` →
     `(task_context, question)` as prompt, `answer` as completion for
     learning the "should I ask?" signal.

4. **Sharing adapters back.** Trained adapters go to an S3-like blob
   store. Workspaces opt into an adapter by setting one of the `*_MODEL`
   env vars to match. The ingestion side has no role here — it's a
   plain config push on the backend side.

## 6. Protocol evolution

The shapes above are **append-only stable**. Additions rule:

- Adding a column to an existing table: safe. Consumers MUST parse
  JSONL defensively (ignore unknown keys).
- Adding a new value to an enum-like column (`phase`, `role`,
  `outcome`, `event_type`): safe. Consumers MUST fall through on
  unknown values rather than reject the row.
- Adding a new endpoint: safe. Manifest will expand correspondingly.

Removals or type changes are breaking and will require a
coordinated migration. None are planned.

## 7. Reference implementation checklist

When building the ingestion side of `lean_ai_serve`:

- [ ] Workspace registration form + encrypted key storage
- [ ] Manifest poller with configurable cadence per workspace
- [ ] Paginated pull with cursor persistence per `(workspace_id, table)`
- [ ] Dead-letter queue for rows that fail JSON parse (unlikely but
      possible if a future field holds binary — the upstream scrubber
      shouldn't allow this, but plan for it)
- [ ] Training-data versioning — tag each materialized dataset with the
      earliest and latest `created_at` timestamps across all source
      rows, so trainers can correlate adapter performance with the
      specific slice they saw
- [ ] Per-workspace eval set — hold out ~10% of each workspace's traces
      for the user's own eval, the rest pooled for the shared adapter
- [ ] Revocation story — if a user pulls their data, delete every row
      tagged with their `workspace_id` and re-train the next adapter
      without them

## 8. Source of truth

Every schema and shape in this doc is defined in:

- [`backend/src/lean_ai/training/db.py`](../backend/src/lean_ai/training/db.py)
  — DDL + insert helpers
- [`backend/src/lean_ai/training/capture.py`](../backend/src/lean_ai/training/capture.py)
  — high-level capture functions used by every workflow hook
- [`backend/src/lean_ai/routers/export.py`](../backend/src/lean_ai/routers/export.py)
  — HTTP endpoints and streaming
- [`backend/src/lean_ai/training/export_formats.py`](../backend/src/lean_ai/training/export_formats.py)
  — anonymisation + SFT/DPO/KTO converters for `training_traces`
- [`backend/tests/training/`](../backend/tests/training/) — worked
  round-trip examples of every capture + export path

If the doc and the code disagree, the code is right. Open an issue so
we can bring the doc back into line.

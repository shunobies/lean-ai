# API Reference

All endpoints are under the `/api` prefix. The backend runs on port 8422 by default.

## REST Endpoints

### Sessions

#### `POST /api/sessions`

Create a new workflow session.

**Request:**
```json
{
  "repo_root": "/path/to/project",
  "task": "Add user authentication"
}
```

**Response:**
```json
{
  "session_id": "uuid-string",
  "status": "active"
}
```

#### `GET /api/sessions?repo_root=/path/to/project`

List all sessions for a workspace.

#### `GET /api/sessions/{session_id}?repo_root=/path/to/project`

Get session details (status, task, branch, plan).

#### `DELETE /api/sessions/{session_id}?repo_root=/path/to/project`

Delete a session and all associated data.

#### `GET /api/sessions/{session_id}/conversation?repo_root=/path/to/project`

Get the full conversation log (chain-of-thought) for a session.

#### `GET /api/sessions/search?repo_root=/path&q=auth&commit=abc123`

Search sessions by task text, plan content, conversation, or commit SHA.

#### `POST /api/sessions/{session_id}/resume`

Prepare a session for resumption. Validates state and switches to the session's git branch.

**Request:**
```json
{
  "repo_root": "/path/to/project"
}
```

### Branch Operations

#### `POST /api/sessions/{session_id}/merge?repo_root=/path/to/project`

Merge the session's branch into the base branch, delete the work branch, and pop any stashed changes.

#### `POST /api/sessions/{session_id}/abandon?repo_root=/path/to/project`

Abandon the session's branch — checkout base branch, force-delete the work branch, and pop any stash.

### Chat

#### `POST /api/chat`

Lightweight read-only chat with workspace context. No tools, no database, no workflow — just a conversation with the LLM that has full context of your project.

**Request:**
```json
{
  "message": "How does the authentication system work?",
  "history": [
    {"role": "user", "content": "previous message"},
    {"role": "assistant", "content": "previous reply"}
  ],
  "workspace": {
    "workspace_name": "my-project",
    "workspace_root": "/path/to/project",
    "active_file": "src/auth.py",
    "active_language": "python",
    "active_selection": null
  }
}
```

**Response:**
```json
{
  "reply": "The authentication system uses...",
  "tokens_per_second": 42.5,
  "eval_count": 350,
  "refined": false,
  "privacy_redactions": 0
}
```

The chat endpoint automatically:
- Reads the project file tree and active file
- Searches the workspace index for relevant code
- Searches the web for current information
- Fetches any URLs included in the message
- Runs the [local refiner](reference-library.md#local-refiner) when using cloud providers

### Inline Predictions

#### `POST /api/predict`

Copilot-style inline completions. Always uses Ollama.

**Request:**
```json
{
  "file_path": "src/main.py",
  "language": "python",
  "prefix": "def calculate_total(",
  "suffix": "\n    return total",
  "cursor_line": 10,
  "cursor_character": 25
}
```

**Response:**
```json
{
  "completion": "items):\n    total = sum(item.price for item in items)",
  "confidence": 0.8
}
```

### Workspace & Context

#### `POST /api/init-workspace`

Index the workspace, generate project context, and trigger background embedding generation and reference library indexing.

**Request:**
```json
{
  "repo_root": "/path/to/project",
  "force_reindex": false
}
```

**Response:**
```json
{
  "index_status": "indexed",
  "index_file_count": 142,
  "index_chunk_count": 867
}
```

#### `POST /api/generate-project-context`

Generate or regenerate `.lean_ai/project_context.md`.

**Request:**
```json
{
  "repo_root": "/path/to/project",
  "skip_if_exists": true
}
```

#### `POST /api/generate-framework-guide`

Generate `.lean_ai/framework_guide.md` by detecting frameworks and generating best-practice guides.

#### `POST /api/generate-style-guide`

Generate `.lean_ai/context/style_guide.md` from CSS and template files.

### Reference Library

#### `POST /api/index-reference`

Index documents in the reference library directory for domain document retrieval.

**Request:**
```json
{
  "repo_root": "/path/to/project",
  "force_reindex": false
}
```

**Response:**
```json
{
  "status": "indexed",
  "doc_count": 5,
  "chunk_count": 234
}
```

### Prompts

#### `GET /api/prompts?repo_root=/path/to/project`

Return all registered prompts with defaults, current values, override status, and metadata.

**Response:**
```json
{
  "prompts": [
    {
      "key": "policy.tool",
      "category": "Core Policy",
      "name": "Tool Policy",
      "description": "Rules for how tools are called during implementation.",
      "default_text": "...",
      "current_text": "...",
      "is_overridden": false,
      "template_vars": [],
      "warning": ""
    }
  ],
  "categories": ["Core Policy", "Planning", "Execution", "Fix Mode", "Chat & Refinement", "Context Generation", "Framework Guide", "TDD & Vision", "Advanced"]
}
```

Prompt payloads now expose both global and scoped override metadata. A prompt can have:

- a global override
- one or more scoped overrides
- both at once

#### `PUT /api/prompts`

Save prompt overrides to `.lean_ai/prompts.yaml`. Validates that required template variables are preserved. Scoped overrides used by role tuning are stored in the same file under `_scoped_overrides`.

**Request:**
```json
{
  "repo_root": "/path/to/project",
  "overrides": {
    "policy.quality": "- No stubs...\n- Follow team conventions..."
  }
}
```

**Response:**
```json
{"status": "saved"}
```

Returns 422 if required template variables are missing from any override.

#### `POST /api/role-tuning/prewarm`

Prewarm sharable role-tuning artifacts for the active workspace. The backend tunes the active `primary`, `request`, and `expert` role assignments (using primary fallbacks when a dedicated request or expert client is not configured), skips any current profiles, and returns per-role status plus the written artifact paths.

**Request:**
```json
{
  "repo_root": "/path/to/project"
}
```

**Response:**
```json
{
  "results": [
    {
      "role": "primary",
      "model_id": "ollama:qwen3-coder:30b",
      "status": "tuned",
      "profile_path": "/path/to/project/.lean_ai/role_tuning/primary--ollama-qwen3-coder-30b.json",
      "prompts_path": "/path/to/project/.lean_ai/prompts.yaml",
      "warning": null
    }
  ]
}
```

#### `POST /api/prompts/reset`

Reset prompt overrides. Pass specific keys to reset individually, or omit `keys` to reset all.

**Request:**
```json
{
  "repo_root": "/path/to/project",
  "keys": ["policy.quality"]
}
```

**Response:**
```json
{"status": "reset"}
```

See the [Prompt Customization](prompt-customization.md) guide for details on the override system.

### Models

#### `GET /api/models`

List available LLM providers and models. Ollama models are queried live from the Ollama API.

**Response:**
```json
{
  "models": [
    {
      "provider": "ollama",
      "model": "qwen3-coder:30b",
      "display_name": "Ollama: qwen3-coder:30b",
      "is_default": true
    },
    {
      "provider": "openai",
      "model": "gpt-4o",
      "display_name": "OpenAI: gpt-4o",
      "is_default": false
    }
  ],
  "default_provider": "ollama",
  "default_model": "qwen3-coder:30b"
}
```

### Scaffolding

#### `GET /api/scaffold/list`

List all available scaffold templates.

#### `POST /api/scaffold`

Create a new project from a scaffold recipe.

**Request:**
```json
{
  "scaffold_name": "fastapi",
  "project_name": "my-api",
  "parent_dir": "/home/user/projects"
}
```

### Notes

#### `POST /api/notes`

Create a new note. The note is indexed for full-text search and queued for background LLM categorization (project, tags, TODOs).

**Request:**
```json
{
  "content": "Need to refactor the auth middleware to support JWT refresh tokens",
  "source_workspace": "my-project"
}
```

#### `GET /api/notes?project=my-project&limit=20&offset=0`

List notes, optionally filtered by project.

#### `GET /api/notes/search?q=auth&project=my-project`

Full-text search across all notes.

#### `GET /api/notes/projects`

List all unique project names that have notes.

#### `GET /api/notes/{note_id}`

Get a single note with its TODOs.

#### `PUT /api/notes/{note_id}`

Update a note's content, project, or tags.

#### `DELETE /api/notes/{note_id}`

Delete a note and its associated TODOs.

#### `GET /api/notes/todos?project=my-project&status=pending`

List TODOs across all notes, optionally filtered by project and status (`pending`, `done`).

#### `POST /api/notes/{note_id}/todos`

Add a TODO item to a note.

**Request:**
```json
{
  "text": "Implement JWT refresh token rotation",
  "status": "pending"
}
```

#### `PUT /api/notes/todos/{todo_id}`

Update a TODO's text or status.

#### `DELETE /api/notes/todos/{todo_id}`

Delete a TODO item.

### Memories

Cross-session memory is a per-workspace store of short lessons Lean AI
extracts after sessions — naming conventions, build gotchas, plan
rejections, validated fix patterns. The planner reads confirmed
memories back into future planning. See [Curated Memory](curated-memory.md)
for the concepts, [Configuration Reference — Curated Memory](configuration.md#curated-memory)
for the settings.

All endpoints are scoped to a workspace via `repo_root` (query param
or body field).

#### `GET /api/memories?repo_root=/path/to/project`

List memories for a workspace.

**Query parameters:**

| Name | Type | Default | Description |
|---|---|---|---|
| `repo_root` | string | *(required)* | Absolute path to the workspace. |
| `category` | string | *(none)* | Filter by category (`gotcha`, `convention`, `fix_pattern`, etc.). |
| `curation_status` | string | *(none)* | Comma-separated list, e.g. `user_confirmed,high_confidence_auto`. |
| `limit` | int | `100` | Max rows to return. |
| `include_expired` | bool | `false` | Include rows with `expires_at` in the past. |

**Response:**
```json
[
  {
    "id": "a1b2c3d4e5f6",
    "session_id": "xxxxxxxxxxxx",
    "category": "gotcha",
    "content": "When pytest fails with ModuleNotFoundError, check PYTHONPATH.",
    "tags": ["pytest", "python", "imports"],
    "source_task": "Fix failing test suite",
    "created_at": "2026-04-20T14:32:10+00:00",
    "curation_status": "user_confirmed",
    "confidence": 0.9,
    "expires_at": null,
    "source_phase": "plan_rejection",
    "model_name": "qwen3-coder:30b",
    "seen_count": 1,
    "last_seen_at": "2026-04-20T14:32:10+00:00"
  }
]
```

#### `GET /api/memories/{memory_id}?repo_root=/path`

Fetch a single memory by id. Returns `404` if not found.

#### `POST /api/memories`

Create a user-authored memory. These are marked `user_confirmed` with
`confidence=0.9` so they're immediately available to the planner.

**Request:**
```json
{
  "repo_root": "/path/to/project",
  "category": "convention",
  "content": "All API endpoints must accept repo_root as a query parameter.",
  "tags": ["api", "convention"],
  "source_task": null
}
```

Returns the created memory row (same shape as the list endpoint).

#### `POST /api/memories/{memory_id}/confirm`

Promote an `auto` memory to `user_confirmed`. Sets `confidence=0.9`.

**Request:**
```json
{"repo_root": "/path/to/project"}
```

Returns the updated memory row. `404` if the id doesn't exist.

#### `POST /api/memories/{memory_id}/reject`

Mark a memory as `user_rejected` so it's excluded from retrieval and
blocks future re-introduction. Sets `confidence=0.0`.

**Request:** same as `/confirm`. Returns the updated row.

#### `DELETE /api/memories/{memory_id}?repo_root=/path`

Permanently delete a memory and its Whoosh index entry.

**Response:**
```json
{"deleted": "a1b2c3d4e5f6"}
```

Returns `404` if the id doesn't exist.

### Integrations

Integration endpoints are available when `LEAN_AI_ENABLE_INTEGRATIONS=true`. Replace `{name}` with the integration name (`jira` or `servicenow`).

#### `GET /api/integrations`

List all registered integrations and their status.

**Response:**
```json
[
  {
    "name": "jira",
    "display_name": "Jira Cloud",
    "active": true
  }
]
```

#### `GET /api/integrations/{name}/health`

Check health of a specific integration.

**Response:**
```json
{"name": "jira", "healthy": true}
```

#### `GET /api/integrations/{name}/tasks`

Fetch tasks from an external service. Supports optional query parameters: `project`, `status` (`open`, `in_progress`, `done`, `blocked`), `assignee`, `limit` (default 50).

**Response:**
```json
[
  {
    "external_id": "PROJ-42",
    "title": "Fix login bug",
    "description": "Users cannot log in with SSO",
    "status": "in_progress",
    "priority": "high",
    "assignee": "Alice",
    "labels": ["bug"],
    "url": "https://yourorg.atlassian.net/browse/PROJ-42",
    "source": "jira",
    "created_at": "2024-01-15T10:30:00",
    "updated_at": "2024-01-16T14:20:00"
  }
]
```

#### `GET /api/integrations/{name}/tasks/{external_id}`

Fetch a single task by its external ID (e.g. `PROJ-42` for Jira, `INC0012345` for ServiceNow).

#### `GET /api/integrations/{name}/search?q=login+bug&limit=20`

Search tasks by text query.

#### `POST /api/integrations/{name}/push`

Push a session summary to an external task. For Jira, this creates a comment and optional worklog. For ServiceNow, this updates the work_notes field.

**Request:**
```json
{
  "repo_root": "/path/to/project",
  "session_id": "uuid-string",
  "external_id": "PROJ-42"
}
```

**Response:**
```json
{"success": true}
```

#### `POST /api/integrations/{name}/link`

Link an external task to a session or workspace.

**Request:**
```json
{
  "external_id": "PROJ-42",
  "session_id": "uuid-string",
  "workspace": "/path/to/project"
}
```

#### `POST /api/integrations/{name}/unlink`

Unlink an external task.

**Request:**
```json
{
  "link_id": "uuid-string"
}
```

#### `GET /api/integrations/linked/all`

Get all linked external tasks. Supports optional query parameters: `workspace`, `integration_name`.

#### `POST /api/integrations/{name}/webhook`

Receive a webhook event from an external service.

**Request:**
```json
{
  "event_type": "issue_updated",
  "external_id": "PROJ-42",
  "payload": {}
}
```

### Health

#### `GET /api/health`

Health check for extension-side monitors.

**Response:**
```json
{
  "status": "ok",
  "vision_available": true,
  "stt_available": false,
  "tts_available": false,
  "wake_word_available": false,
  "busy": ["embeddings.code"]
}
```

The `busy` field is a list of active long-running task tags from the
backend's `runtime_state` registry. Current tags:

- `embeddings.code` — code-index embedding generation in progress.
  Covers any cold-load wait for the embedding model in Ollama when the
  first batch of a fresh run is sent; can take minutes for large models.
- `embeddings.reference` — reference library embedding generation in progress.

`check_embedding_model` only performs an `ollama show` existence check
(instant, no load); it does not pre-warm the model. The first real
embed call inside `generate_embeddings` triggers any cold load — but
only when there is actual work to do. On an already-indexed workspace
with no changes, `generate_embeddings` returns 0 without contacting
Ollama, so the model is never loaded unnecessarily.

The extension's health monitor reads this to distinguish "backend is
slow because it's busy with real work" from "backend is crashed".
Auto-restart is only triggered by `ECONNREFUSED` / `ECONNRESET` (a
truly dead process); a slow/timeout probe never triggers restart.
After 3 minutes of continuous unresponsive probes, the monitor
surfaces a one-time notification with a manual "Restart Backend"
button so the user decides.

The voice availability fields (`stt_available`, `tts_available`,
`wake_word_available`) are omitted when the `voice` extras group is
not installed.

### Export

The export API exposes the raw training archive (`.lean_ai/training.db`)
and the workspace's curated memories to an external coordinator — for
example, the [lean-ai-serve](training.md#5-aggregation-across-workspaces-lean-ai-serve-side)
aggregator that merges training data from multiple users before LoRA
fine-tuning.

**All export endpoints require authentication.** If
`LEAN_AI_EXPORT_API_KEY` is unset, every export endpoint returns
`503 Service Unavailable`. When set, pass the token as a bearer header:

```
Authorization: Bearer <LEAN_AI_EXPORT_API_KEY>
```

A missing, malformed, or wrong header returns `401 Unauthorized`.

> **Security** — The export key grants read access to every training
> trace in the archive. Treat it like any secret: use
> `openssl rand -hex 24` to generate, store it in `backend/.env` (or
> pass as an environment variable), never commit it to git.

All endpoints are scoped to a workspace via `repo_root` (query param).
Every response is JSONL streamed via `StreamingResponse` (`application/x-ndjson`).

#### `GET /api/export/workspace-id?repo_root=/path`

Return the deterministic 16-character workspace identifier. Use it to
correlate rows across multiple export calls without revealing the
actual filesystem path.

**Response:**
```json
{"workspace_id": "a1b2c3d4e5f6a7b8"}
```

#### `GET /api/export/manifest?repo_root=/path`

Return trace counts per model, phase, and outcome, plus memory counts
by curation status. Cached 60 seconds per workspace.

**Response:**
```json
{
  "total_traces": 1234,
  "scrubbed_count": 1234,
  "oldest": "2026-01-01T00:00:00Z",
  "newest": "2026-04-20T14:32:10Z",
  "by_model": {"qwen3-coder:30b": 800, "qwen3-coder-next:q8_0": 434},
  "by_phase": {"implementation": 900, "phase3": 200, "validation_fix": 134},
  "by_outcome": {"success": 1100, "rejected": 120, "cancelled": 14},
  "plan_decisions": 50,
  "validation_attempts": 80,
  "workflow_events": 200,
  "memories": {
    "total": 42,
    "by_status": {"user_confirmed": 30, "auto": 10, "user_rejected": 2}
  },
  "workspace_id": "a1b2c3d4e5f6a7b8"
}
```

#### `GET /api/export/traces`

Stream JSONL of training traces in the requested format.

**Query parameters:**

| Name | Type | Default | Description |
|---|---|---|---|
| `repo_root` | string | *(required)* | Workspace path. |
| `format` | string | `raw` | One of `raw`, `sft`, `dpo`, `kto`. |
| `model` | string | *(none)* | Filter by `model_name`. |
| `phase` | string | *(none)* | Filter by `phase` (`implementation`, `phase3`, etc.). |
| `outcome` | string | *(none)* | Filter by `outcome` (`success`, `rejected`). |
| `since` | string | *(none)* | ISO-8601 timestamp; only rows created at/after. |
| `cursor` | int | *(none)* | Last-seen `id` — use for incremental pulls. |
| `limit` | int | `1000` | Max rows per call. Capped at 10000. |

**Format shapes:**

- **`raw`** — the full row (minus db id), anonymized. `session_id`
  hashed, `repo_root` stripped.
- **`sft`** — OpenAI chat JSONL. Only rows with `outcome='success'`.
  Each line: `{messages: [...], phase, model_name, workspace_id}`.
  The final assistant message carries `reasoning_content` (thinking
  trace) when `LEAN_AI_CAPTURE_THINKING=true`.
- **`dpo`** — matched pairs for direct preference optimization. Rows
  grouped by `pair_id`; incomplete pairs skipped. Each line:
  `{prompt, chosen, rejected, pair_id, pair_kind, workspace_id, model_name, phase}`.
- **`kto`** — binary-labeled for Kahneman-Tversky optimization. Each
  line: `{prompt, completion, label: bool, workspace_id, phase, model_name, pair_kind}`.

**Example — SFT pull with incremental cursor:**
```bash
curl -H "Authorization: Bearer $KEY" \
  "http://localhost:8422/api/export/traces?repo_root=/my/project&format=sft&limit=10000"
# pipe to a file, then on next run pass &cursor=<last id seen>
```

Returns `400 Bad Request` on unknown format or `limit > 10000`.

#### `GET /api/export/memories`

Stream JSONL of curated memories with **workspace-symbol anonymization**
on top of the normal `repo_root` stripping. File paths, module names,
and CamelCase symbols from the workspace are replaced with generic
placeholders so memories are safe to aggregate across users.

Memories where more than `LEAN_AI_MEMORY_EXPORT_DROP_THRESHOLD` of the
content had to be redacted (default 40%) are dropped entirely — too
project-specific to generalize.

**Query parameters:**

| Name | Type | Default | Description |
|---|---|---|---|
| `repo_root` | string | *(required)* | Workspace path. |
| `curation_status` | string | `user_confirmed,high_confidence_auto` | Comma-separated list of statuses to include. |
| `category` | string | *(none)* | Filter by memory category. |
| `limit` | int | `500` | Max rows. Capped at 5000. |

**Response format** (one JSON object per line):
```json
{
  "id": "a1b2c3d4e5f6",
  "category": "gotcha",
  "content": "When pytest fails in <WORKSPACE_FILE>, check PYTHONPATH.",
  "tags": ["pytest", "imports"],
  "curation_status": "user_confirmed",
  "confidence": 0.9,
  "anonymization_ratio": 0.12,
  "workspace_id": "a1b2c3d4e5f6a7b8"
}
```

The `anonymization_ratio` field (0.0–1.0) lets downstream consumers
prefer memories with less redaction.

#### `GET /api/export/events`

Stream JSONL of `workflow_events` rows (cancellations, TDD disputes,
execution-complete markers, etc.) with standard anonymization.

**Query parameters:**

| Name | Type | Default | Description |
|---|---|---|---|
| `repo_root` | string | *(required)* | Workspace path. |
| `event_type` | string | *(none)* | Filter (`cancellation`, `tdd_dispute`, `execution_complete`). |
| `since` | string | *(none)* | ISO-8601 timestamp. |
| `cursor` | int | *(none)* | Last-seen id. |
| `limit` | int | `1000` | Max rows. Capped at 10000. |

Each line is an anonymized `workflow_events` row:
```json
{
  "workspace_id": "a1b2c3d4e5f6a7b8",
  "session_id": "<hashed>",
  "event_type": "cancellation",
  "payload": {"task": "refactor auth", "mode": "plan"},
  "created_at": "2026-04-20T14:32:10Z"
}
```

## WebSocket Protocol

### Connection

Connect to `ws://localhost:8422/api/sessions/{session_id}/stream` for real-time workflow streaming.

### Client Messages

#### `user_message`

Start a workflow or respond to clarification questions.

```json
{
  "type": "user_message",
  "content": "Add JWT authentication to the API",
  "repo_root": "/path/to/project"
}
```

Prefix the content with `/fix ` to use fix mode instead of the planning pipeline.

#### `approve`

Approve the proposed plan.

```json
{"type": "approve"}
```

#### `resume`

Resume a previously started session.

```json
{
  "type": "resume",
  "repo_root": "/path/to/project"
}
```

#### `ping`

Keepalive message. Server responds with `{"type": "pong"}`.

### Server Messages

| Type | Description |
|---|---|
| `stage_change` | Workflow phase changed (`planning`, `implementing`) |
| `clarification_needed` | Questions that need user input before planning |
| `approval_required` | Plan ready for review. Payload includes `plan` (markdown), `user_summary` (plain-English approach), and `plan_validation_warnings` (`list[str]` of non-blocking warnings from Phase 4/5 post-generation validators — hallucinated paths, uncovered missing files, edit/create inconsistencies, test-path convention violations). The extension surfaces the warnings above the plan on the approval card. |
| `plan_rejected` | User sent feedback, plan is being revised |
| `plan_revision` | Revised plan ready (includes revision number) |
| `refiner_status` | Local refiner progress (`running`, `done`, `skipped`, `error`) |
| `stage_status` | Sub-stage progress (e.g. context update) |
| `checkpoint` | Step execution progress (step index, status) |
| `tool_progress` | Tool call status (`running`, `complete`, `error`) |
| `assistant_content` | LLM text output during execution |
| `metrics_update` | Context window usage stats |
| `diff` | File change diff |
| `test_result` | Test execution results |
| `branch_created` | Git branch created for the session |
| `context_refreshed` | Context window was refreshed |
| `memory_suggested` | Auto-extracted memory eligible for user confirmation — see [`memory_suggested`](#memory_suggested) below |
| `complete` | Workflow finished (includes summary and files modified) |
| `merge_complete` | Branch merged successfully |
| `error` | Error occurred (includes `recoverable` flag) |
| `pong` | Response to ping |

#### `memory_suggested`

Fired when a session hook (plan rejection, fix-loop success, or TDD
dispute) extracts a new memory. The extension renders it as an inline
chip in the chat stream so the user can confirm or dismiss without
leaving the workflow.

```json
{
  "type": "memory_suggested",
  "memory_id": "a1b2c3d4e5f6",
  "category": "gotcha",
  "content": "When pytest fails with ModuleNotFoundError, check that src/ is on PYTHONPATH.",
  "source_phase": "fix_loop",
  "tags": ["pytest", "python", "imports"]
}
```

Memory confirmation actions (Save / Dismiss) are currently sent via
REST rather than WebSocket — see
[Memories](#memories) endpoints above. The extension's chip
click handler calls `POST /api/memories/{id}/confirm` or
`POST /api/memories/{id}/reject`.

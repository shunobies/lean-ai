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
- Runs the [local refiner](knowledge-base.md#local-refiner) when using cloud providers

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

Index the workspace, generate project context, and trigger background embedding generation and knowledge indexing.

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

### Knowledge Base

#### `POST /api/index-knowledge`

Index documents in the knowledge directory for domain document retrieval.

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

#### `PUT /api/prompts`

Save prompt overrides to `.lean_ai/prompts.yaml`. Validates that required template variables are preserved.

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

### Health

#### `GET /api/health`

Returns `{"status": "ok"}` when the server is running.

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
| `approval_required` | Plan ready for review (includes Markdown plan) |
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
| `complete` | Workflow finished (includes summary and files modified) |
| `merge_complete` | Branch merged successfully |
| `error` | Error occurred (includes `recoverable` flag) |
| `pong` | Response to ping |

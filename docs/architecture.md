# Architecture

Lean AI is a FastAPI backend with two editor clients layered on top: the VS Code extension and a JetBrains plugin. The backend exposes REST endpoints for setup, chat, sessions, memories, prompts, integrations, and export, plus a WebSocket workflow channel for long-running agent sessions.

> For an end-to-end narrative walkthrough of a real session — from the first chat message through post-execution validation, with every guardrail called out — see [example-flow.md](example-flow.md). For the tool-and-state audit focused on persistence, see [workflow-flow.md](workflow-flow.md).

## System Overview

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   VS Code    │────▶│              │────▶│              │
│  Extension   │     │   FastAPI    │     │ LLM Clients  │
└──────────────┘     │   Backend    │     └──────────────┘
                     │              │        Ollama / OpenAI
┌──────────────┐     │              │        / Anthropic /
│ JetBrains    │────▶│              │        Gemini / Serve
│   Plugin     │     └──────────────┘
└──────────────┘
   REST / WS
```

Both clients communicate with the backend over HTTP for request/response APIs and WebSocket for streaming workflow execution. The backend routes LLM work through a facade with provider adapters, optional role-specific clients (primary, expert, request, worker), workspace-aware tools, SQLite persistence, indexing, and optional memory/training/integration subsystems.

## Workflow Modes

### Plan Mode (`/agent`)

The default mode for features and refactors. The backend enters a graph-driven workflow that still follows a mostly linear user experience:

```
clarify → plan → approve → execute
```

1. **Clarify** — The LLM assesses whether the task is clear enough. If not, it sends clarifying questions to the user via WebSocket and waits for answers before proceeding.

2. **Plan** — A 6-phase decomposed planning pipeline (see below) reads the codebase, designs changes, and produces a structured `ExecutionPlan` with numbered steps.

3. **Approve** — The plan is sent to the user for review over the workflow WebSocket. The user can approve, or send feedback to trigger a revision (up to 5 revision rounds).

4. **Execute** — Each plan step is executed sequentially. A constrained LLM translates each step's instruction into tool calls (file creation, edits, shell commands, tests, etc.). Progress, approvals, diffs, and validation status are streamed over WebSocket, and checkpoints are persisted so execution can resume.

### Fix Mode (`/fix`)

Skips planning entirely. The backend runs a direct tool-calling workflow that optionally begins with a read-only investigation phase, then hands off to implementation and post-validation. Best for bug fixes, small changes, and exploratory work.

```
implement (with tools) → done
```

Fix mode includes:
- **Scratchpad** — persistent memory across tool-calling turns. The agent records its progress so it can survive context window refreshes.
- **Task reminders** — periodically re-injected to keep the agent focused.
- **Context refresh** — when context usage hits 70% of the window, old messages are dropped and the system prompt is rebuilt from fresh disk state. The scratchpad bridges the gap.

### Request Mode (`/request` and `/skill`)

Uses the same direct-execution path as fix mode, but with a neutral request-oriented prompt and, when configured, a dedicated request model. It is intended for open-ended research, drafting, and skill-driven tasks where a planning gate is unnecessary.

## 6-Phase Planning Pipeline

The planner (`llm/planner.py`) uses decomposed LLM calls to produce high-quality plans:

| Phase | What it does |
|---|---|
| **1. Scope** | Determines what the task requires — new files, edits, tests, config changes |
| **2. File identification** | Finds which existing files need to be read and/or modified |
| **2.5. Compression** | Reads identified files and compresses them into a structured summary |
| **3. Change design** | Designs the specific changes using the compressed codebase context |
| **4. Risk check** | Identifies potential risks, side effects, and edge cases |
| **5. Plan assembly** | Produces the final structured `ExecutionPlan` with step-by-step instructions |

Each phase uses structured JSON output from the LLM. The planner has read-only tools (`read_file`, `list_directory`, `directory_tree`, `grep_files`) for codebase exploration during phases 1-2.

### Model routing through the planner

| Stage | Which model runs it | Why |
|---|---|---|
| `/chat` (conversational refinement before a task is dispatched) | **Request** model | Chatty, higher temperature — good for refining fuzzy ideas with the user. |
| Phase 1 (clarification / verification) | **Primary** model | Coder-tuned reads the codebase more precisely than a chatty general model. |
| Phase 2 (file identification / exploration) | **Primary** model | Same reason — plus the worker model automatically compresses tool outputs so the primary doesn't drown in file contents. |
| Phases 3–5 (design, plan assembly, verification) | **Expert** model (when configured; otherwise primary) | Reasoning-heavy structural work — the largest model available earns its keep here. |
| Implementation (per-step execution) | **Primary** model | Hands back to the coder-tuned model that now has a structured plan to follow. |
| Final validation fix retry | **Expert** model (when configured) | Escalation for stubborn failures. |
| Tool output compression, web summarization, memory extraction | **Worker** model | Small, fast — runs asynchronously so it never blocks the hot path. |

The **request** model participates in chat and `/request` mode only — it does not run any planner phase.

When using cloud providers, the [Local Refiner](reference-library.md#local-refiner) can enrich tasks with reference library context and strip sensitive data before planning begins.

## Tools

Lean AI exposes different tool sets depending on the execution surface:

- **Chat** uses read-heavy workspace and internet tools.
- **Planning** uses investigation tools plus structured planning helpers.
- **Fix/request execution** uses the full implementation tool set.

Core execution tools include:

| Tool | Description |
|---|---|
| `create_file` | Create a new file (fails if file exists) |
| `edit_file` | Find-and-replace edit on an existing file |
| `read_file` | Read file contents with line numbers |
| `run_command` | Execute a general shell command |
| `run_tests` | Execute a test command |
| `run_lint` | Execute a linting command |
| `format_code` | Execute a code formatter |
| `list_directory` | List directory contents |
| `directory_tree` | Recursive file tree view |
| `grep_files` | Search for patterns across the codebase |
| `web_search` / browser tools | Search the web and inspect fetched pages when enabled |
| `git_*` helpers | Diff, branch, commit, merge, and stash operations via backend wrappers |
| `save_note` / memory tools | Store notes, query prior sessions, and search curated memory |
| `record_architecture_decision` | Save durable architecture decisions in the workspace DB |
| `verify_web_ui` / `verify_desktop_ui` | Optional screenshot-based UI verification tools |
| `request_clarification` | Ask the user a blocking question through the workflow channel |
| `update_scratchpad` | Save progress notes (persistent across turns) |
| `task_complete` | Signal that all work is done |

Shell commands pass through a safety gate (`tools/command_safety.py`) and WebSocket-mediated approval flow for destructive or out-of-workspace operations.

## LLM Providers

The `LLMClient` facade (`llm/facade.py`) handles multi-turn tool-calling orchestration. It delegates single-turn calls to the active provider:

- **Ollama** (`llm/client.py`) — Local inference via the Ollama API. Handles native tool calling, FIM completions, and embeddings.
- **OpenAI** (`llm/provider_openai.py`) — OpenAI API and compatible providers (Together, Groq, vLLM via `LEAN_AI_OPENAI_BASE_URL`).
- **Anthropic** (`llm/provider_anthropic.py`) — Claude API with tool use support.
- **Gemini** (`llm/provider_gemini.py`) — Google Gemini API via the `google-genai` SDK. Supports large context windows (1M+ tokens).
- **Serve** (`llm/provider_openai.py` via `routers/client_factory.py`) — Lean AI Serve / vLLM using the OpenAI-compatible protocol.

Inline predictions (Copilot-style completions) and embeddings always use Ollama regardless of the active provider.

The backend can also create optional role-specific clients:

- **Primary** — default model for normal execution.
- **Expert** — heavier model for reasoning-heavy planning and final validation retries.
- **Request** — optional model for `/request` and chat-oriented drafting.
- **Worker** — lightweight auxiliary model for compression, summarization, and memory extraction.

### Context Management

There is no separate context-window manager object. Instead, the orchestration loop rebuilds prompts from durable state when message history grows too large:

- Context refresh triggers at 70% of the context window
- Old messages are dropped, the system prompt is rebuilt from fresh disk state
- Scratchpad and journal continuity are re-injected as recent-tail slices during refresh (not full-file replay) to reduce context refill overhead.
- A per-session state ledger (`.lean_ai/state/{session_id}.jsonl`) records typed workflow events (phase transitions, tool calls/results, refresh checkpoints) so refresh payloads can include deterministic machine-state summaries alongside prose notes.
- Scratchpad, journal, and the state ledger are the durable continuity mechanisms

## Indexer

The indexer (`indexer/`) builds a searchable index of the workspace:

- **Tree listing** — Gitignore-aware recursive file listing
- **Code chunking** — Tree-sitter AST-aware chunking (respects function/class boundaries)
- **Search** — Whoosh BM25F keyword search + optional embedding-based semantic search
- **Hybrid ranking** — Reciprocal Rank Fusion (RRF) combines keyword and semantic results
- **Incremental updates** — SHA-256 manifest tracks file changes for efficient re-indexing

## Context Generation

The context system generates project-specific documentation that helps the LLM understand the codebase:

- **`project_context.md`** — Auto-generated summary of the project architecture, key files, and patterns. Uses tree-sitter metadata extraction with disk caching.
- **`framework_guide.md`** — Detects frameworks from dependency files, web-searches for best practices, and generates an architecture/conventions guide.
- **`style_guide.md`** — Extracted from CSS/template files for frontend projects.
- **Custom steering** — Drop `.md` files into `.lean_ai/context/` for additional project-specific guidance.

Files are loaded in order: `project_context.md` → `framework_guide.md` → alphabetically sorted files from `context/`.

## Language Support

The language registry currently ships 13 YAML-backed tree-sitter definitions:

Python, JavaScript, TypeScript, Go, Rust, Java, C, C++, C#, Ruby, PHP, CSS, HTML

The language registry (`languages/`) defines extraction rules for classes, functions, and imports in YAML format. A generic extraction engine applies these rules to any supported language.

## Persistence

Workspace persistence uses SQLite via `aiosqlite` (`db.py`) at `.lean_ai/lean_ai.db`. The schema has grown beyond the original two-table core and now includes workflow, memory, prompt, and checkpoint state:

- **`sessions`** — Workflow sessions with task, status, branch metadata, and plan JSON
- **`tool_logs`** — Tool execution history with timestamps and results
- **`conversation_logs`** — Assistant/user/tool conversation log for a session
- **`session_commits`** — Git commits created during a session
- **`session_memories`** — Curated memory rows for later promotion/retrieval
- **`architecture_decisions`** — Durable architecture notes captured from workflows or chat
- **`checkpoints`** — Serialized workflow state for resume/branching
- **`prompt_versions`, `prompt_variants`, `ab_tests`** — Prompt-registry and experimentation state

No ORM. Raw SQL queries.

## Git Integration

When the target workspace is a Git repository, each workflow session runs on its own branch:

1. Stash uncommitted changes
2. Switch to the default branch (main/master)
3. Create a work branch (`lean-ai/{session_id}`)
4. Execute the plan
5. Auto-commit changes when there is something to commit
6. User approves → merge and clean up, or rejects → delete branch and restore stash

The backend also tracks session commits in the workspace DB and keeps Lean AI auto-stashes separate from user-created stash entries.

## Post-Execution Validation

After every workflow execution (both plan mode and fix mode), a three-layer quality system runs automatically:

### 1. Auto-Detection

During `/init`, Lean AI scans your project's dependency files to detect the correct lint, format, and test commands for your ecosystem. No manual configuration needed.

| Ecosystem | What it detects |
|---|---|
| PHP | `php -l .`, Pint, PHPUnit, `artisan test` |
| Python | ruff, black, flake8, pytest |
| Node/TS | ESLint, Prettier, Jest, Vitest (from `package.json` scripts or devDependencies) |
| Ruby | RuboCop, RSpec, `rails test` |
| Go | `go vet`, `gofmt`, `go test` |
| Rust | Clippy, `cargo fmt`, `cargo test` |
| Java | `mvn test`, `gradle test` |
| C# | `dotnet build`, `dotnet format`, `dotnet test` |

Detected commands are saved to `.lean_ai/commands.json`. Manual `LEAN_AI_POST_*` environment variables always take priority over auto-detected commands.

### 2. Deterministic Validation

After execution completes, the system runs a fixed pipeline:

```
format (auto-fix) → lint fix (auto-fix) → lint check → test
```

The first two stages (format, lint fix) are auto-fix — they modify files silently. Lint check and test are verification stages that report pass/fail. This runs without any LLM calls.

### 3. Validation-Resubmission Loop

If lint or tests fail, the failure output is fed back to the LLM in a fresh conversation with a **30-turn budget**. The user message gives a concise directive ("Validation failed. Workflow: re-run command → diagnose → fix → verify.") plus the full failure output — the detailed behavioral rules (minimal fix, scratchpad diagnosis, web search escalation) come from the system prompt via the canonical policy blocks, avoiding instruction duplication between the system and user messages. After each fix attempt, the full validation pipeline re-runs. This repeats up to `LEAN_AI_POST_VALIDATION_MAX_RETRIES` times (default 2).

On the **final retry**, the expert model is used if configured — this escalates complex failures to a larger reasoning model.

The resubmission loop uses a separate, minimal context (just the system prompt and failure output) so it cannot be interrupted by context window refreshes.

### 4. Conditional Test Writing

When a test command is available (auto-detected or manually configured), the LLM is instructed to write tests alongside code changes:

- **Fix mode** — the system prompt includes a test requirement directive
- **Plan mode** — the planner's final checklist requires test steps for new modules

This only activates when a test command exists — projects without tests are not forced into a testing pattern.

## Integrations

The integrations system (`integrations/`) provides two-way sync between Lean AI sessions and external task tracking services. It is gated by `LEAN_AI_ENABLE_INTEGRATIONS`.

### Architecture

- **`IntegrationProvider`** (`base.py`) — Abstract base class defining the provider contract: health check, pull (list/get/search tasks), push (session summaries, status updates), and webhook handling.
- **Registry** (`registry.py`) — `@register_integration` decorator registers providers at import time. Manages provider lifecycle (init, health check, shutdown).
- **Persistence** (`db.py`) — SQLite database at `~/.lean_ai/integrations/integrations.db` stores task links, sync logs, and configuration.
- **Summary builder** (`summary.py`) — Extracts duration, files changed, and commits from session data to build `SessionSummary` objects for push operations.
- **REST endpoints** (`routers/integrations.py`) — Full CRUD API for all integration operations.
- **Auto-push hook** — Fires in `workflow/pipeline.py` on session completion to push summaries to linked tasks.

### Providers

**Jira Cloud** (`integrations/jira.py`) — Connects to Jira Cloud REST API v3 with Basic Auth (email + API token). Pushes session summaries as ADF-formatted comments with worklogs for time tracking. Updates issue status via Jira's transition system.

**ServiceNow** (`integrations/servicenow.py`) — Connects to the ServiceNow Table API with Basic Auth. Pushes session summaries as work notes. Updates record state directly. Transparently handles both human-readable INC numbers and 32-character hex sys_ids.

Both providers auto-initialize at server startup when credentials are configured.

### Data Flow

```
Session completes → build_session_summary() → push to linked external tasks
                                              (comment/worklog or work_notes)

External service → webhook → handle_webhook() → process changes
```

## Scaffolding

19 YAML scaffold recipes (`scaffolds/`) for bootstrapping new projects: FastAPI, Next.js, Laravel, Rails, Django, Flask, Express, and more. Each recipe defines the setup commands and file structure.

## Self-Improvement Pipeline

Lean AI watches every session and learns from it. The pipeline has two
independent layers: a **curated memory** layer that feeds back into
planning right away, and a **training archive** that accumulates
slowly for future LoRA fine-tuning.

```
┌─────────────────────────────────────────────────────────────────┐
│                     One workflow session                       │
│                                                                 │
│  User task ──▶ Plan ──▶ Approve ──▶ Execute ──▶ Validate ──▶ Done│
│                 │         │           │           │             │
│                 │         ▼           │           ▼             │
│                 │    plan_decisions   │    validation_attempts  │
│                 │    (if revised)     │    (fix loop)           │
│                 ▼                     ▼                         │
│              workflow_events ◀────────┴── cancellation/TDD/etc  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                         │                          │
                         │ extract (worker LLM)     │ raw capture
                         ▼                          ▼
            ┌───────────────────────┐   ┌──────────────────────────┐
            │  session_memories     │   │  .lean_ai/training.db    │
            │  (.lean_ai/lean_ai.db)│   │  (append-only archive)   │
            │                       │   │                          │
            │  • auto (pending)     │   │  • training_traces       │
            │  • user_confirmed     │   │  • plan_decisions        │
            │  • high_confidence…   │   │  • validation_attempts   │
            │  • user_rejected      │   │  • workflow_events       │
            │                       │   │  • redaction_audit       │
            └───────────────────────┘   └──────────────────────────┘
                         │                          │
              injected back into                 exported to
              planning phases 1+3 +              lean-ai-serve
              validation fix loop                (requires API key)
```

### Layer 1 — Curated Memory (feedback into the next session)

After every session, a worker LLM reads a compact session summary and
writes up to five short lessons to the `session_memories` SQLite
table. Three extra extraction paths capture specific high-value
signals:

- **Plan rejection** (`schedule_plan_rejection_extraction`) — fires
  when the user rejects a plan and later approves a revised one. The
  `(original plan, feedback, revised plan)` triple becomes a
  `rejection` memory.
- **Fix success** (`schedule_fix_success_extraction`) — fires when
  the validation fix loop turns a failing command into a passing one.
  The `(failing command, diagnosis, fix approach)` triple becomes a
  `fix_pattern` memory.
- **TDD dispute** (`schedule_tdd_dispute_extraction`) — fires when
  the primary model challenges a test and the expert model rules on
  it. The ruling becomes a `gotcha` or `fix_pattern` memory.

Memories start in `curation_status='auto'` and are **not** visible to
the planner until a human confirms them (or auto-promotion sees the
same lesson three times across sessions). This prevents noisy
extractions from poisoning future plans.

Three planning phases read memories back:

| Phase | Categories retrieved | Purpose |
|---|---|---|
| **Phase 1 — Scope** | All | General context from similar past tasks. |
| **Phase 3 — Design** | `gotcha`, `convention`, `rejection` | Avoid known design mistakes. |
| **Fix loop** | `fix_pattern`, `gotcha` | Past fixes for similar failing commands. |

Each phase has its own 2% context-window budget so memory injection
never crowds out the actual prompt. See [Curated Memory](curated-memory.md)
for the full concept doc and the extension-side UI.

### Layer 2 — Training Archive (fuel for future LoRA)

In parallel with memory extraction, workflow hooks write raw traces
to `.lean_ai/training.db` — a **separate** SQLite file from the main
workspace DB so retention/VACUUM never blocks the hot path.

| Table | What it holds | Why it's there |
|---|---|---|
| `training_traces` | Full messages + assistant output per LLM turn, optional `pair_id` + `preference` | SFT / DPO / KTO export source |
| `plan_decisions` | Approve/reject/cancel with `plan_before`, `feedback`, `plan_after` | DPO pairs (rejected → revised-approved) |
| `validation_attempts` | Per-attempt `(failures_before, diagnosis, fix, failures_after)` | DPO pairs (failed → succeeded on same error) |
| `workflow_events` | Cancellation / TDD dispute / execution-complete markers | KTO labels and behavior analysis |
| `redaction_audit` | One row per scrubber match (sha256 prefix only — never the raw secret) | Forensics when a new leak class is discovered |

Before any row is inserted, the payload passes through a **fail-closed
scrubber** (`training/scrubber.py`) that matches OpenAI / Anthropic /
Slack / GitHub / AWS tokens, JWTs, SSH keys, bearer headers,
`LEAN_AI_*_KEY=…` env lines, and high-entropy generic tokens. Any
scrubber exception drops the trace rather than risk writing unscrubbed
data. Every match gets a `redaction_audit` row keyed by a 12-character
sha256 prefix of the matched string so you can retroactively identify
affected rows without the audit log itself containing secrets.

### Export & aggregation

The archive stays on your machine by default. Setting
`LEAN_AI_EXPORT_API_KEY` enables a set of authenticated
`/api/export/*` endpoints that stream JSONL in four formats:

- `raw` — anonymized rows with structure intact
- `sft` — OpenAI chat JSONL (successful turns only; preserves
  `reasoning_content` for thinking models)
- `dpo` — matched `{prompt, chosen, rejected}` pairs
- `kto` — binary-labeled `{prompt, completion, label}`

A coordinator like `lean-ai-serve` can pull from every registered
workspace and concatenate. Each row is anonymized twice:

1. **Always**: `session_id` → `sha256(salt:id)[:12]`, `repo_root` →
   `/workspace-<id>` placeholder.
2. **Memory-only**: file paths, module names, and CamelCase symbols
   from the workspace's symbol table are replaced with generic
   placeholders. Memories where more than 40% of characters had to be
   redacted are dropped entirely.

See [Training Pipeline](training.md) for the export API, LoRA recipe,
and vLLM adapter hot-swap walkthrough.

### Auto-promotion and bulk invalidation

`training/maintenance.py` adds two automation helpers on top of the
two layers:

- **`auto_promote_memory`** — when the memory extractor is about to
  insert a new row, it first checks whether a near-identical memory
  already exists. If so, the existing row's `seen_count` is bumped
  instead of inserting a duplicate. Once `seen_count` crosses
  `LEAN_AI_MEMORY_AUTOPROMOTE_THRESHOLD` (default 3), the memory
  promotes from `auto` to `high_confidence_auto`.
- **`bulk_invalidate_by_model`** — demote every memory produced by a
  named model in one call, useful when a model regression is
  discovered. Affected rows move to `superseded`.

Retention pruning (`run_retention_pass`) fires opportunistically at
session end, throttled to once per workspace per hour, and deletes
rows older than `LEAN_AI_TRAINING_RETENTION_DAYS` (default 365).

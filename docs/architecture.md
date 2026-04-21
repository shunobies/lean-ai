# Architecture

Lean AI is a linear pipeline — no state machine, no complex orchestration. Tasks flow through clear phases, and each component has one job.

## System Overview

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   VSCode     │────▶│   FastAPI     │────▶│  LLM Client  │
│  Extension   │◀────│   Backend    │◀────│  (Provider)  │
└──────────────┘     └──────────────┘     └──────────────┘
   WebSocket /         REST + WS            Ollama / OpenAI
   HTTP REST                                / Anthropic / Gemini
```

The extension communicates with the backend over HTTP (chat, sessions) and WebSocket (workflow streaming). The backend delegates LLM calls through a provider abstraction that supports Ollama, OpenAI, Anthropic, and Gemini.

## Workflow Modes

### Plan Mode (`/agent`)

The default mode for features and refactors. Tasks pass through four phases:

```
clarify → plan → approve → execute
```

1. **Clarify** — The LLM assesses whether the task is clear enough. If not, it sends clarifying questions to the user via WebSocket and waits for answers before proceeding.

2. **Plan** — A 6-phase decomposed planning pipeline (see below) reads the codebase, designs changes, and produces a structured `ExecutionPlan` with numbered steps.

3. **Approve** — The plan is sent to the user for review. The user can approve, or send feedback to trigger a revision (up to 5 revision rounds).

4. **Execute** — Each plan step is executed sequentially. A constrained LLM translates each step's instruction into tool calls (file creation, edits, tests, etc.). Progress is streamed via WebSocket checkpoints.

### Fix Mode (`/fix`)

Skips planning entirely. The LLM gets the full tool set and works autonomously until it decides the task is done. Best for bug fixes, small changes, and exploratory work.

```
implement (with tools) → done
```

Fix mode includes:
- **Scratchpad** — persistent memory across tool-calling turns. The agent records its progress so it can survive context window refreshes.
- **Task reminders** — periodically re-injected to keep the agent focused.
- **Context refresh** — when context usage hits 70% of the window, old messages are dropped and the system prompt is rebuilt from fresh disk state. The scratchpad bridges the gap.

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

When using cloud providers, the [Local Refiner](reference-library.md#local-refiner) can enrich tasks with reference library context and strip sensitive data before planning begins.

## Tools

The agent has access to these tools during execution:

| Tool | Description |
|---|---|
| `create_file` | Create a new file (fails if file exists) |
| `edit_file` | Find-and-replace edit on an existing file |
| `read_file` | Read file contents with line numbers |
| `run_tests` | Execute a test command |
| `run_lint` | Execute a linting command |
| `format_code` | Execute a code formatter |
| `list_directory` | List directory contents |
| `directory_tree` | Recursive file tree view |
| `grep_files` | Search for patterns across the codebase |
| `update_scratchpad` | Save progress notes (persistent across turns) |
| `task_complete` | Signal that all work is done |

Shell commands (`run_tests`, `run_lint`, `format_code`) pass through a safety gate (`tools/command_safety.py`) that blocks dangerous operations.

## LLM Providers

The `LLMClient` facade (`llm/facade.py`) handles multi-turn tool-calling orchestration. It delegates single-turn calls to the active provider:

- **Ollama** (`llm/client.py`) — Local inference via the Ollama API. Handles native tool calling, FIM completions, and embeddings.
- **OpenAI** (`llm/provider_openai.py`) — OpenAI API and compatible providers (Together, Groq, vLLM via `LEAN_AI_OPENAI_BASE_URL`).
- **Anthropic** (`llm/provider_anthropic.py`) — Claude API with tool use support.
- **Gemini** (`llm/provider_gemini.py`) — Google Gemini API via the `google-genai` SDK. Supports large context windows (1M+ tokens).

Inline predictions (Copilot-style completions) and embeddings always use Ollama regardless of the active provider.

### Context Management

No `ContextWindowManager`. Ollama manages its own KV cache. The system focuses on prompt quality:

- Context refresh triggers at 70% of the context window
- Old messages are dropped, the system prompt is rebuilt from fresh disk state
- The scratchpad provides continuity across refreshes
- No LLM summarization call — the scratchpad is the agent's persistent memory

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

13 languages with tree-sitter AST parsing — no regex patterns:

Python, JavaScript, TypeScript, Go, Rust, Java, C, C++, C#, Ruby, PHP, Swift, Kotlin

The language registry (`languages/`) defines extraction rules for classes, functions, and imports in YAML format. A generic extraction engine applies these rules to any supported language.

## Persistence

Minimal SQLite via aiosqlite (`db.py`). Two core tables:

- **`sessions`** — Workflow sessions with task, status, branch name, plan JSON
- **`tool_logs`** — Tool execution history with timestamps and results

No ORM. Raw SQL queries.

## Git Integration

Every workflow task runs on its own branch:

1. Stash uncommitted changes
2. Switch to the default branch (main/master)
3. Create a work branch (`lean-ai/{session_id}`)
4. Execute the plan
5. Auto-commit changes
6. User approves → merge and clean up, or rejects → delete branch and restore stash

This keeps the main branch clean and makes every change reversible.

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

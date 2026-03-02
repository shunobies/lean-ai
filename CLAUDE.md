# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Lean AI** — an agentic coding assistant that uses a single local LLM (via Ollama) with a simple philosophy: plan well, give the LLM tools, let it work. Python backend with FastAPI (REST + WebSocket), minimal SQLite persistence, and a TypeScript VSCode extension.

Extracted from single_ai — keeps what works (project context generation, decomposed planning, native tool calling, scaffolding, knowledge base), drops what doesn't (11-state FSM, regex-based parsing, ContextWindowManager, stagnation detection, rubric system).

## Build & Run Commands

```bash
# Install backend (from repo root)
cd backend && pip install -e ".[dev]"

# Install with optional knowledge base deps (EPUB, PDF, Word support)
cd backend && pip install -e ".[dev,knowledge]"

# Run the server
cd backend && uvicorn lean_ai.main:app --reload --port 8422

# Run all tests
cd backend && python -m pytest tests/ -v

# Lint
cd backend && ruff check src/ tests/

# VSCode extension (from repo root)
cd extension && npm install && npm run build
```

## Architecture (Linear Pipeline)

**No FSM.** The entire workflow is: `plan -> approve -> execute -> done`.

1. **LLM Client** (`llm/client.py`) — Async Ollama client with `chat_with_tools()` multi-turn loop. Native tool calling via Ollama's `tools=` parameter. Retry with backoff. No conversation trimming — Ollama manages its own context.

2. **Planning** (`llm/planner.py`) — 5-phase decomposed planning: scope -> file identification -> change design -> risk check -> plan assembly. Structured JSON output from Ollama. Plan template with worked examples (`llm/plan_template.md`).

3. **Tools** (`tools/`) — `create_file`, `edit_file`, `read_file`, `run_tests`, `run_lint`, `format_code`, `list_directory`, `directory_tree`. File ops produce diffs. Shell commands pass through a safety gate (`command_safety.py`). Internet search + URL fetching with HTML strip + LLM summary sanitization.

4. **Workflow** (`workflow/pipeline.py`) — Linear pipeline in one function. WebSocket-based progress streaming. No state machine library.

5. **Persistence** (`db.py`) — Minimal SQLite via `aiosqlite`. Two tables: `sessions` and `tool_logs`. No ORM.

6. **Indexer** (`indexer/`) — Gitignore-aware tree listing. Tree-sitter AST-aware code chunking. Whoosh BM25F search. Embedding store with RRF re-ranking. SHA-256 manifest for incremental updates.

7. **Context Generation** (`context/`) — Generates `.lean_ai/project_context.md` via single-pass or multi-round LLM calls. Tree-sitter metadata extraction with disk cache. Auto-scaling size caps proportional to context window.

8. **Language Registry** (`languages/`) — 13 language definitions in YAML. Tree-sitter AST parsing (no regex patterns). Generic extraction engine for classes, functions, imports.

9. **Knowledge Base** (`knowledge/`) — Domain document indexing (EPUB, PDF, Word, Markdown, HTML, text). Prose-aware paragraph chunker. Separate Whoosh index. Incremental updates via SHA-256 manifest.

10. **Scaffolding** (`scaffolds/`) — 19 YAML scaffold recipes for project bootstrapping.

## Key Design Decisions

- **No regex for source code analysis** — all extraction uses tree-sitter AST queries
- **No ContextWindowManager** — Ollama manages its own KV cache; we focus on prompt quality
- **No rubric system** — user approval is the sole quality gate
- **No stagnation detection** — trust the LLM to complete its work
- **No implementation review loop** — plan -> execute -> done
- **Tool naming**: `create_file` (not `write_file`) for clearer intent
- **Structured JSON output** from Ollama replaces regex-based plan/output parsing

## Technology Stack

| Concern | Library |
|---|---|
| Web framework | FastAPI (async, built-in WebSocket) |
| Database | aiosqlite (raw SQL, 2 tables) |
| Ollama SDK | ollama (official, async) |
| Search index | Whoosh |
| Source analysis | tree-sitter + 13 grammar packages |
| Internet search | duckduckgo-search |
| HTML sanitization | BeautifulSoup4 |
| Testing | pytest + pytest-asyncio |
| Linting | ruff |
| VSCode extension | Chat Participant API + InlineCompletionItemProvider |

## Configuration (Environment Variables)

All settings use the `LEAN_AI_` prefix, or via `backend/.env`. Defined in `backend/src/lean_ai/config.py`.

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_OLLAMA_URL` | `http://localhost:11434` | Ollama API endpoint |
| `LEAN_AI_OLLAMA_MODEL` | `qwen3-coder:30b` | Primary model |
| `LEAN_AI_OLLAMA_TEMPERATURE` | `0.0` | Sampling temperature |
| `LEAN_AI_OLLAMA_CONTEXT_WINDOW` | `131072` | Total context window (single source of truth) |
| `LEAN_AI_OLLAMA_MAX_TOKENS` | *(derived: 25% of context window)* | Max output tokens |
| `LEAN_AI_INLINE_MODEL` | *(empty)* | Separate model for inline predictions |
| `LEAN_AI_INLINE_OLLAMA_URL` | *(falls back to OLLAMA_URL)* | Ollama instance for inline model |
| `LEAN_AI_EMBEDDING_MODEL` | `qwen3-embedding:0.6b` | Embedding model for semantic search |
| `LEAN_AI_ENABLE_EMBEDDINGS` | `true` | Enable embedding generation + RRF hybrid search |
| `LEAN_AI_INDEX_DIR` | `.lean_ai_index` | Whoosh index directory name |
| `LEAN_AI_SEARCH_PROVIDER` | `duckduckgo` | Search provider (`duckduckgo` or `searxng`) |
| `LEAN_AI_KNOWLEDGE_DIR` | `.lean_ai/knowledge` | Knowledge documents directory |
| `LEAN_AI_IMPLEMENTATION_MAX_TURNS` | `50` | Max tool-calling turns per session |
| `LEAN_AI_IMPLEMENTATION_MAX_TOKENS` | *(derived: 25% of context window)* | Max tokens per LLM turn |
| `LEAN_AI_CHAT_TEMPERATURE` | `0.3` | Temperature for /chat endpoint |
| `LEAN_AI_PORT` | `8422` | Server port |

## WebSocket Protocol

Message types: `token`, `stage_change`, `approval_required`, `tool_progress`, `tool_approval_required`, `diff`, `test_result`, `error`, `complete`, `index_status`, `stage_status`, `clarification_needed`, `plan_rejected`, `pong`, `branch_created`, `checkpoint`, `merge_complete`.

## API Endpoints

All under `/api` prefix:

- `POST /sessions` — create session
- `WS /sessions/{id}/stream` — WebSocket for workflow execution
- `GET /sessions` — list sessions
- `GET /sessions/{id}` — session detail
- `POST /init-workspace` — index workspace + generate project context
- `POST /generate-project-context` — regenerate context
- `POST /index-knowledge` — index knowledge docs
- `POST /chat` — lightweight conversational endpoint (no tools, read-only)
- `POST /predict` — inline predictions
- `POST /scaffold/list` — list scaffold recipes
- `POST /scaffold` — create project from scaffold
- `GET /health` — health check

## LLM Prompt Authoring Standard

**Never assign a persona to the LLM in system prompts.** Use capability-first framing:
```
# Bad
"You are a senior software architect..."

# Good
"Use your knowledge of software architecture to..."
```

## Commit After Every Change

Always commit after completing a change without waiting to be asked. Each logical change gets its own commit.

## No Stubs Rule

Never create stubs, placeholder implementations, or skeleton code that is not fully functional. If a feature cannot be completed, document what is missing in `incomplete.md` and move on.

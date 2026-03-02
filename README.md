# Lean AI

Agentic coding assistant powered by a single local LLM via Ollama. Plan well, give the LLM tools, let it work.

## Philosophy

Lean AI extracts the proven ideas from [single_ai](../single_ai) into a clean, minimal codebase:

- **Linear pipeline** instead of an 11-state FSM: plan -> approve -> execute -> done
- **Tree-sitter AST parsing** instead of 117 regex patterns for source code analysis
- **Native tool calling** via Ollama — no text-based SEARCH/REPLACE parsing
- **Minimal persistence** — 2 SQLite tables instead of 11 ORM models
- **Trust the LLM** — no stagnation detection, no implementation review loops, no rubric scoring

## Quick Start

### Backend

```bash
cd backend
pip install -e ".[dev]"

# Optional: knowledge base format support (EPUB, PDF, Word)
pip install -e ".[dev,knowledge]"

# Start the server
uvicorn lean_ai.main:app --reload --port 8422
```

### VSCode Extension

```bash
cd extension
npm install
npm run build
```

Load the extension in VSCode (Run Extension from the extension directory).

## Configuration

All settings use the `LEAN_AI_` environment variable prefix. Create a `backend/.env` file:

```env
LEAN_AI_OLLAMA_MODEL=qwen3-coder:30b
LEAN_AI_OLLAMA_CONTEXT_WINDOW=131072
LEAN_AI_ENABLE_EMBEDDINGS=true
```

See `backend/src/lean_ai/config.py` for all available settings.

## How It Works

1. **User submits a task** via the VSCode extension or WebSocket API
2. **Planning phase** — 5-phase decomposed planning produces a structured plan
3. **User approval** — user reviews and approves (or provides revision feedback)
4. **Implementation** — LLM receives the plan + tools, works autonomously via multi-turn tool calling
5. **Done** — summary of changes sent back to the user

## Project Structure

```
lean_ai/
├── backend/
│   ├── pyproject.toml
│   └── src/lean_ai/
│       ├── main.py              # FastAPI entry point
│       ├── config.py            # Pydantic settings (LEAN_AI_ prefix)
│       ├── db.py                # Minimal SQLite (2 tables)
│       ├── router.py            # All API endpoints
│       ├── llm/                 # Ollama client, planner, tool definitions
│       ├── tools/               # File ops, git, shell, internet, scaffold
│       ├── languages/           # Tree-sitter language definitions (13 YAMLs)
│       ├── indexer/             # Whoosh BM25F search + embeddings
│       ├── context/             # Project context generation
│       ├── knowledge/           # Domain document indexing
│       ├── workflow/            # Linear pipeline + WebSocket handler
│       └── scaffolds/           # 19 YAML scaffold recipes
│
└── extension/                   # VSCode extension
    ├── package.json
    └── src/                     # Chat participant, sidebar, inline predictions
```

## What Changed from single_ai

| Concern | single_ai | lean_ai |
|---|---|---|
| State machine | 11-state python-statemachine FSM | Linear pipeline function |
| Source analysis | 117 regex patterns in YAML | tree-sitter AST parsing |
| LLM output parsing | Regex plan/implementation parsers | Structured JSON output |
| Persistence | SQLAlchemy 2.x, 11 ORM models | aiosqlite, 2 tables |
| Context management | ContextWindowManager with priority tiers | Removed (Ollama manages context) |
| Quality gate | 10 YAML rubrics with scoring | User approval only |
| Implementation | Step-by-step with retry loops | Full-plan with tool calling |
| Stagnation | SHA-256 hash detection | Removed (trust the LLM) |
| Tool naming | `write_file` | `create_file` |

## Requirements

- Python 3.10+
- Node.js 18+ (for the extension)
- Ollama running locally with a capable model (e.g. qwen3-coder:30b)

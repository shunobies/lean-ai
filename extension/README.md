# Lean AI

**Your codebase already has an architect. It just needs tools.**

Lean AI is an agentic coding assistant that reads your project, plans changes, and executes them — all inside your editor. Give it a task in plain English, review the plan, and watch it work.

Run it fully local with [Ollama](https://ollama.com), or connect to OpenAI and Anthropic when you need heavier reasoning. No cloud account required to get started.

## Features

- **Plan first, then execute** — a 6-phase planning pipeline reads your codebase, traces data flow, and produces a structured plan. You approve before anything changes.
- **Three workflow modes** — `/agent` for full planning, `/fix` for quick bug fixes, `/request` for open-ended research and documentation tasks.
- **Multi-provider** — Ollama (free, local), OpenAI, and Anthropic. Switch from the settings panel without restarting.
- **Dual-model pipeline** — use a fast local model for exploration and implementation, hand off to a cloud model for reasoning-heavy planning phases.
- **Built-in code quality** — auto-runs your linter and tests after every change, with LLM self-correction on failure.
- **Knowledge base** — drop internal docs (PDF, EPUB, Word, Markdown) into `.lean_ai/knowledge/` for context-aware plans.
- **Git-native** — every task runs on its own branch. Approve to merge, reject to discard.
- **19 scaffold recipes** — bootstrap new projects with `/scaffold`.

## Quick Start

### 1. Install the backend

```bash
cd backend && pip install -e ".[dev]"
```

### 2. Start the server

```bash
uvicorn lean_ai.main:app --reload --port 8422
```

### 3. Open a project and run `/init`

Type `/init` in the chat panel to index your workspace and generate project context. Then describe what you want built.

## Slash Commands

| Command | Description |
|---------|-------------|
| `/init` | Index workspace and generate project context |
| `/agent` | Full planning pipeline for features and refactors |
| `/fix` | Skip planning, fix directly with full tool access |
| `/request` | Open-ended tasks with internet search (guides, research) |
| `/approve` | Merge the agent's branch |
| `/reject` | Discard the agent's branch |
| `/scaffold` | Bootstrap a new project from a recipe |
| `/guide` | Regenerate framework guide |
| `/reboot` | Restart the backend server |

## Configuration

Open the settings panel (gear icon in the chat header) to configure:

- **LLM Provider** — Ollama, OpenAI, or Anthropic
- **Model selection** — primary, expert, and request models
- **Sampling parameters** — temperature, top-p, top-k, context window
- **Post-validation** — lint, test, and format commands
- **Search provider** — DuckDuckGo, SearXNG, Google, or Bing

API keys for OpenAI and Anthropic are stored securely in your OS keychain.

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) with a capable model (e.g., `qwen3-coder:30b`)
- The Lean AI backend server — see the [GitHub repository](https://github.com/shunobies/lean-ai) for installation instructions

## Links

- [GitHub Repository](https://github.com/shunobies/lean-ai) — source code, backend setup, and full documentation
- [Changelog](CHANGELOG.md) — release history and recent changes
- [Configuration Guide](https://github.com/shunobies/lean-ai/blob/main/docs/configuration.md) — all environment variables and settings

## License

MIT

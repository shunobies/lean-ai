# Lean AI

**Your codebase already has an architect. It just needs tools.**

Lean AI is an agentic coding assistant that reads your project, plans changes, and executes them — all inside your editor. Give it a task in plain English, review the plan, and watch it work.

Run it fully local with [Ollama](https://ollama.com), or connect to OpenAI and Anthropic when you need heavier reasoning. Switch between models mid-session from the UI. No cloud account required to get started.

![Example](Example.png)

## Why Lean AI?

- **Plan first, then execute** — a 6-phase planning pipeline reads your codebase, traces data flow across files, and produces a structured plan before touching any code. You approve (or revise) before anything changes. Thinking and content tokens stream in real-time during every planning phase so you can follow the agent's reasoning as it works.
- **Learns from your corrections** — every time you reject a plan, every time a test fix works, every TDD dispute, Lean AI extracts a short lesson and offers it to you as an inline chip in the chat ("Remember this?"). Confirmed lessons are read back into future planning so the tool stops repeating the same mistakes. [Curated Memory](docs/curated-memory.md)
- **Training archive for future fine-tuning** — every workflow decision is captured locally in an append-only archive behind a fail-closed secrets scrubber. Opt in to export via a bearer-token API when you're ready to ship a LoRA adapter for your expert model. [Training Pipeline](docs/training.md)
- **Multi-provider flexibility** — Ollama for free local inference, OpenAI for GPT-4o, Anthropic for Claude. Switch from the dropdown without restarting. Use cheap local models for small fixes, cloud models for hard problems.
- **Dual-model pipeline** — run a fast local model for codebase exploration and code execution, then automatically hand off to a cloud model (Claude, GPT-4o) for reasoning-heavy planning phases and complex fix attempts. Save cloud tokens for the decisions that matter.
- **Role tuning per model** — Lean AI can calibrate the `request`, `primary`, and `expert` roles to the exact model assigned to each one, persist the winning contract under `.lean_ai/role_tuning/`, and automatically reuse it on future runs. Tuned guidance is applied as a scoped prompt override, so one model can behave differently from another without replacing your global prompt customizations.
- **Local Refiner** — when using cloud providers, a local Ollama model pre-processes your prompts: enriches them with private reference library context, strips sensitive data, and structures vague requests into detailed specs. Your proprietary docs never leave your machine. [Learn more](docs/reference-library.md)
- **Zero prompt engineering** — chat mode helps you refine ideas into detailed tasks. Project context and framework guides teach the LLM your codebase conventions automatically.
- **Reference library** — drop your internal docs (PDF, EPUB, Word, Markdown) into `.lean_ai/reference/` and the agent uses them for better plans without leaking content to cloud APIs.
- **UI verification** — the agent can screenshot web pages or desktop GUIs (Tkinter, Qt, Swing, JavaFX, Electron) and analyse layout with a local vision model. Useful for planning UI changes, discussing layout with the agent, or diagnosing visual regressions that pass a type-check but look broken. [Learn more](docs/ui-verification.md)
- **Built-in code quality** — after every execution, Lean AI runs your project's linter and tests automatically. Failures are fed back to the LLM for self-correction. Lint, test, and format commands are auto-detected from your project files — zero configuration needed. When a test command is available, the agent writes tests alongside code changes.
- **Git-native workflow** — every task runs on its own branch. Approve to merge, reject to discard. Your main branch stays clean.
- **19 scaffold recipes** — bootstrap new projects (FastAPI, Next.js, Laravel, Rails, and more) with a single command.

## Quick Start

### 1. Install the extension

**VS Code / VSCodium:** Install from the [VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=lean-ai.lean-ai) or [OpenVSX](https://open-vsx.org/extension/lean-ai/lean-ai).

**JetBrains IDEs** (IntelliJ, PyCharm, WebStorm, CLion, etc.): See the [JetBrains Plugin guide](jetbrains-plugin/README.md) for build and install instructions.

On first activation, the extension automatically creates a Python virtual environment and installs the backend server — no manual setup required.

### 2. Install Ollama and pull a model

Download [Ollama](https://ollama.com) and pull a model:

```bash
ollama pull qwen3-coder:30b
```

> **Cloud models:** Ollama also supports cloud-hosted models. Pull one with `ollama pull model-name:cloud`, then run `ollama run model-name:cloud` and follow the link to connect your Ollama Cloud account. Once linked, select the cloud model from the Lean AI dropdown — it works the same as a local model.

### 3. Open a project and run `/init`

Type `/init` in the chat panel to index your workspace and generate project context. Then describe what you want built.

## Building from Source

If you prefer to build from source instead of installing from the marketplace:

### Backend

```bash
cd backend
pip install -e ".[dev]"
```

For voice features on Ubuntu 26.04 or any environment where `python3` is already 3.14+, create the backend virtualenv with Python 3.13 first. The current `voice` extra depends on `kokoro-onnx`, which does not yet support Python 3.14.

```bash
sudo apt install portaudio19-dev build-essential
cd backend
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[voice]"
```

If you're running the extension against that source checkout, set `lean-ai.backendDir` to the `backend/` directory and `lean-ai.pythonPath` to `backend/.venv/bin/python`.

Need cloud providers or reference library support? See [optional extras](docs/configuration.md#installation-extras).

Start the server manually:

```bash
uvicorn lean_ai.main:app --reload --port 8422
```

### Extension

```bash
cd extension && npm install && npm run build
npx vsce package --no-dependencies
```

Install the generated `.vsix` file: **Extensions** sidebar > `...` menu > **Install from VSIX...**

When building from source, set `lean-ai.backendDir` or `lean-ai.pythonPath` in the extension settings — the automatic installer is skipped when either setting is explicitly configured.

## How It Works

```
You: "Add user authentication with JWT tokens"
                    |
          [Chat refines the idea]
                    |
          [6-phase planning pipeline]
            scope -> files -> design -> risks -> plan
                    |
          [You review and approve]
                    |
          [Agent executes step-by-step]
            creates files, edits code, writes tests
                    |
          [Post-execution validation]
            auto-format -> lint fix -> lint check -> test
            failures fed back to LLM for self-correction
                    |
          [Changes committed on a branch]
            /approve to merge, /reject to discard
```

**Three common modes:**
- **`/agent`** — full planning pipeline for features and refactors
- **`/fix`** — skip planning, let the agent explore and fix directly
- **`/request`** — skip planning with a request-oriented role tuned for research, drafting, and open-ended tasks

See [Architecture](docs/architecture.md) for the full breakdown.

## Role Tuning

Lean AI can tune the three main model roles independently:

- **Request** — used by chat refinement and `/request`
- **Primary** — used by planning phases 1-2, normal execution, and primary-path fix work
- **Expert** — used by planning phases 3-5, expert-path fix work, plan revision, and final validation escalation

On the first run for a given `role + model`, Lean AI performs a lightweight calibration pass, selects the best role framing for that model, stores the result in `.lean_ai/role_tuning/`, and writes a model-scoped prompt override into `.lean_ai/prompts.yaml`.

If you want to prewarm those artifacts on one machine and share them with teammates, run `/tune-roles`. That command explicitly tunes the active `primary`, `request`, and `expert` role assignments (including primary fallbacks when a dedicated request/expert model is not configured), writes the same `.lean_ai/role_tuning/*.json` profiles plus `.lean_ai/prompts.yaml`, and lets you commit just those sharable files for reuse on other machines with the same provider/model IDs.

That tuned result is reused until one of the inputs changes:

- the role's work summary
- the relevant compiled base prompts
- the role-tuning composition version

Manual prompt customization still works. Global overrides remain the fallback layer, and a matching model-scoped override wins only for that specific role/model pair.

## Slash Commands

| Command | What it does |
|---|---|
| `/init` | Index workspace and generate project context |
| `/tune-roles` | Prewarm sharable role-tuning artifacts for the active role assignments |
| `/agent` | Send a task to the planning pipeline |
| `/fix` | Skip planning, fix directly with full tool access |
| `/approve` | Merge the agent's branch |
| `/reject` | Discard the agent's branch |
| `/style` | Generate style guide from CSS/templates |
| `/skill <name> <task>` | Load a local skill from `.lean_ai/skills/<name>/instructions.md` and apply it to the task |
| `/request <task>` | Skip planning, open-ended task with full tool access |
| `/resume [session_id]` | Resume a previous session |
| `/help` | Show this help |
| `/interview-prep` | Convert a .docx resume and tailor it for a specific role |
| `/batch-prep` | Tailor resumes + cover letters for many roles in one run |
| `/ats-check [slug]` | Keyword gap report comparing resume to the job description |
| `/thank-you [slug]` | Draft a post-interview thank-you note |
| `/recruiter-reply` | Draft a reply to a recruiter's cold outreach |
| `/negotiate [slug]` | Research market comp and build a negotiation brief |
| `/analyse-rejection [slug]` | Post-mortem a rejection with concrete takeaways |
| `/log-applied [slug]` | Append a tracker row and commit the application folder to git |
| `/mock-interview [slug]` | Interactive Q&A practice with rubric scoring |
| `/note` | Save a note (auto-categorized by project) |
| `/scaffold` | Bootstrap a new project |
| `/memories` | Manually trigger memory extraction from the last completed workflow session |
| `/reboot` | Restart the backend server |

## Configuration

Create a `backend/config.yaml` file (or use the extension's settings panel):

```yaml
# Provider: ollama, openai, or anthropic
llm_provider: ollama

# Local model (default)
ollama_model: "qwen3-coder:30b"
ollama_context_window: 128  # 128k — shorthand for 131072

# Cloud API keys — encrypt with: python -m lean_ai encrypt-key <key>
openai_api_key: "enc:gAAAAABf..."
anthropic_api_key: "enc:gAAAAABf..."
```

Environment variables (`LEAN_AI_*`) and legacy `.env` files also work. See [configuration priority](docs/configuration.md).

### Dual-model setup (save cloud tokens)

Use a local model for the bulk of the work and a cloud model only for planning and complex fixes:

```yaml
llm_provider: ollama
ollama_model: "qwen3-coder:30b"

# Expert: cloud model for planning phases 3-5 and final fix retry
expert_llm_provider: anthropic
anthropic_api_key: "enc:gAAAAABf..."
anthropic_expert_model: "claude-opus-4-6"
```

The expert model only runs for planning phases 3–5 (design + risk synthesis, plan assembly, verification) and the final validation fix retry. All codebase exploration, implementation, and routine tool calls use the primary local model.

> **Note:** the cloud provider's Python SDK must be installed even when the primary provider is Ollama — run `pip install -e ".[dev,anthropic]"` or `pip install -e ".[dev,openai]"` as appropriate, then restart the server.

### All-local three-model setup

Use three different local models matched to each role:

```yaml
ollama_model: "qwen3-coder:30b"

# Expert: larger model for planning and complex reasoning
ollama_model_expert: "qwen3-coder-next:80b"

# Request: smaller model for chat conversation and prompt building
request_llm_provider: openai
openai_base_url: "http://localhost:11434/v1"
openai_request_model: "gpt-oss:20b"
```

See the [full configuration reference](docs/configuration.md) for all options.

## Documentation

| Guide | Description |
|---|---|
| [Configuration](docs/configuration.md) | All environment variables, extension settings, and model setup |
| [Architecture](docs/architecture.md) | Planning pipeline, workflow modes, tools, and internals |
| [Example Flow](docs/example-flow.md) | End-to-end walkthrough of a real session with every guardrail called out |
| [Reference Library & Refiner](docs/reference-library.md) | Private docs, RAG enrichment, and cloud privacy |
| [UI Verification](docs/ui-verification.md) | Vision-backed screenshot analysis for web pages and desktop GUIs |
| [API Reference](docs/api-reference.md) | REST endpoints and WebSocket protocol |
| [Extension Guide](docs/extension.md) | VSCode/VSCodium setup, commands, and settings |
| [JetBrains Plugin](jetbrains-plugin/README.md) | IntelliJ, PyCharm, WebStorm, CLion, and all JetBrains IDEs |
| [Prompt Customization](docs/prompt-customization.md) | Global and model-scoped prompt overrides, including role tuning |
| [Skills Guide](docs/skills.md) | How `/skill` works and how to author high-quality skills |
| [Modelfile Guide](docs/modelfile.md) | Customizing Ollama models with persistent rules |
| [llama-server Guide](docs/llama-server.md) | Using llama.cpp as an alternative to Ollama |
| [Regulated Environments](docs/regulated-environments.md) | Self-hosted deployment for HIPAA, SOX, GDPR, ITAR, and air-gapped networks |

## Requirements

- Python 3.10-3.13 (Python 3.13 recommended; Python 3.14 is not supported)
- Node.js 18+ (for the extension)
- At least one LLM provider:
  - **Ollama** with a capable model (e.g., `qwen3-coder:30b`) — free, local, no account needed
  - **OpenAI** API key (GPT-4o, etc.)
  - **Anthropic** API key (Claude, etc.)

Ollama is always required for inline predictions and embeddings, even when using cloud providers.

## Technology Stack

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI, aiosqlite |
| LLM providers | Ollama, OpenAI, Anthropic |
| Code analysis | tree-sitter (13 languages) |
| Search | Whoosh BM25F + embedding RRF |
| VS Code Extension | TypeScript, VSCode API |
| JetBrains Plugin | Kotlin, IntelliJ Platform SDK, JCEF |

## License

MIT

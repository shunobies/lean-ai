# Extension Guide

The Lean AI extension provides a sidebar chat panel, inline predictions, session management, and workflow controls inside VSCode and VSCodium.

## Installation

### From Source

```bash
cd extension
npm install
npm run build
npx vsce package --no-dependencies
```

Then install the generated `.vsix` file: **Extensions** sidebar > `...` menu > **Install from VSIX...**

### Development

```bash
cd extension
npm run watch    # Rebuild on file changes
```

Press `F5` in VSCode to launch an Extension Development Host for testing.

## Features

### Sidebar Chat

The chat panel is the primary interface. It provides a conversational LLM experience with full workspace context:

- Your project's file tree, active file, and selected code are automatically included
- The workspace index is searched for relevant code snippets
- Web search runs in parallel for current information
- URLs in your message are fetched and included as context
- The [local refiner](knowledge-base.md#local-refiner) enriches prompts when using cloud providers

### Inline Predictions

Copilot-style completions powered by Ollama. As you type, the extension sends the surrounding code context (prefix + suffix) to the backend for fill-in-the-middle (FIM) completion.

Configure a separate, faster model for predictions via the settings panel or:

```env
LEAN_AI_INLINE_MODEL=qwen2.5-coder:7b
```

### Session Management

The Sessions view in the sidebar shows all workflow sessions with their status. Right-click a session for:

- **View** — See session details and conversation log
- **Merge** — Merge the agent's branch into your main branch
- **Abandon** — Discard the agent's branch
- **Delete** — Remove the session entirely

### Context Pills (Problems & Debug State)

The chat input area shows two optional context pills that inject live IDE data into your messages before they are sent to the agent.

**⚠ Problems (N)**

Shows the total number of errors and warnings currently in the VSCode Problems tab. Click to toggle it on (highlighted). When active, every message you send — in chat mode or agent mode — has the full diagnostics list appended:

```
---
Current Problems (VSCode diagnostics):
Errors (2):
  - src/api.ts:42:5 [typescript] Type 'string' is not assignable to type 'number'
  - src/utils.ts:8:1 [eslint] Missing semicolon
Warnings (1):
  - src/app.ts:101:3 [typescript] 'any' type used
---
```

The pill turns red when errors are present. The count updates live as diagnostics change.

**● Debug State** (appears only during an active debug session)

When a debug session is running, this pill becomes available. Toggle it on to include the current call stack (top 5 frames) and local variables from the top frame in your message:

```
---
Active Debug Session: "Launch Program" (node)
Stopped: exception — TypeError: Cannot read property 'foo' of undefined
Call Stack:
  [0] processData  src/utils.ts:42
  [1] handleRequest  src/api.ts:15
  [2] main  src/index.ts:8
Local Variables (processData):
  req = { method: 'GET', url: '/api/items' }
  result = undefined
  err = TypeError: Cannot read property 'foo' of undefined
---
```

Variables are fetched via the Debug Adapter Protocol and truncated at 120 characters. The pill disappears when the debug session ends.

**Automatic inclusion for `/fix`**

The `/fix` command always appends errors and warnings from the **currently open file** regardless of the Problems pill state. This gives the agent concrete file:line data without requiring manual toggling.

### Model Switching

The model dropdown in the chat panel lets you switch between configured providers and models at runtime. Ollama models are queried live, so pulling a new model makes it immediately available.

## Slash Commands

Type these in the chat panel:

| Command | What it does |
|---|---|
| `/init` | Index workspace, generate project context, and index knowledge base |
| `/agent <task>` | Send a task to the full planning pipeline |
| `/fix <task>` | Skip planning — the agent explores and fixes directly |
| `/approve` | Merge the current session's branch |
| `/reject` | Discard the current session's branch |
| `/guide` | Regenerate framework guide |
| `/style` | Generate style guide from CSS/template files |
| `/scaffold <name>` | Bootstrap a new project from a recipe |
| `/reboot` | Restart the backend server |

## Command Palette

All commands are also available from the VSCode command palette (`Ctrl+Shift+P` / `Cmd+Shift+P`):

| Command | Description |
|---|---|
| `Lean AI: Approve Plan` | Approve the current plan |
| `Lean AI: Reject Plan` | Reject the current plan |
| `Lean AI: Focus Chat Panel` | Focus the chat panel |
| `Lean AI: Open Settings` | Open the Lean AI settings panel |
| `Lean AI: Restart Backend Server` | Restart the Python backend |
| `Lean AI: Stop Backend Server` | Stop the Python backend |
| `Lean AI: Refresh Sessions` | Refresh the sessions list |
| `Lean AI: View Session Details` | View a session's details |
| `Lean AI: Merge Session Branch` | Merge a session's branch |
| `Lean AI: Abandon Session` | Abandon a session's branch |
| `Lean AI: Delete Session` | Delete a session |

## Settings

### Settings Panel (Recommended)

Click the **⚙ gear button** in the Lean AI sidebar header to open the guided settings panel. This is the recommended way to configure the extension, especially for new users — it uses radio buttons to enforce mutual exclusion between providers so you can't accidentally configure conflicting settings.

The panel is organised into sections:

- **Provider** — Radio group: Ollama / OpenAI / Anthropic. Only the fields relevant to your chosen provider are shown.
- **Expert Model** — Optional separate model for reasoning-heavy planning phases (phases 3–6). Can use a different provider than the main model — e.g. fast local Ollama for coding, cloud model for planning.
- **Post-Validation** — Commands for format/lint/test to run automatically after agent changes.
- **Advanced** — Inline prediction model, embedding model, search provider, context thresholds.

**API Key Security**

API keys for OpenAI and Anthropic are stored in the **OS keychain** (macOS Keychain / Windows Credential Manager / Linux libsecret) via VSCode's SecretStorage — not in `settings.json` or `.env`. The settings panel shows only whether a key is configured ("✓ Key configured"), never the value itself.

When the backend subprocess starts, the extension injects API keys from the OS keychain as environment variables. Keys you place directly in `backend/.env` also work — Pydantic reads them on startup.

### VSCode Native Settings

All settings are also available via VSCode's native settings UI (**Settings** > search "Lean AI"). Changes made there are synced to `backend/.env` automatically, and the extension will prompt to restart the backend.

#### Extension settings

| Setting | Default | Description |
|---|---|---|
| `lean-ai.backendUrl` | `http://localhost:8422` | Backend server URL |
| `lean-ai.enableInlinePredictions` | `true` | Enable Copilot-style inline predictions |
| `lean-ai.autoStartBackend` | `true` | Auto-start the Python backend when the extension activates |
| `lean-ai.pythonPath` | `python` | Path to Python interpreter (e.g. full path to a virtualenv) |
| `lean-ai.backendDir` | *(auto-detect)* | Path to the `backend/` directory containing `pyproject.toml` |
| `lean-ai.chatFontSize` | `13` | Font size (px) for chat messages (10–20, adjusts live) |

#### Backend settings (synced to `backend/.env`)

| Setting | Default | Description |
|---|---|---|
| `lean-ai.llmProvider` | `ollama` | LLM provider: `ollama`, `openai`, or `anthropic` |
| `lean-ai.ollamaUrl` | `http://localhost:11434` | Ollama API endpoint |
| `lean-ai.ollamaModel` | `qwen3-coder:30b` | Primary Ollama model |
| `lean-ai.ollamaContextWindow` | `131072` | Context window in tokens (accepts `128` = 128k) |
| `lean-ai.ollamaTemperature` | `0.7` | Sampling temperature |
| `lean-ai.ollamaTopP` | `0.8` | Nucleus sampling threshold |
| `lean-ai.ollamaTopK` | `20` | Top-k sampling |
| `lean-ai.ollamaRepeatPenalty` | `1.05` | Repetition penalty |
| `lean-ai.ollamaMaxTokens` | *(25% of ctx)* | Max output tokens |
| `lean-ai.ollamaModelExpert` | *(empty)* | Separate Ollama model for planning phases 3–6 |
| `lean-ai.ollamaExpertContextWindow` | *(inherit)* | Context window for the expert model |
| `lean-ai.expertLlmProvider` | *(auto)* | Provider for expert phases: `ollama`, `openai`, or `anthropic` |
| `lean-ai.openaiModel` | `gpt-4o` | OpenAI model name |
| `lean-ai.openaiBaseUrl` | *(empty)* | Custom base URL for OpenAI-compatible APIs (Groq, Together AI, vLLM) |
| `lean-ai.openaiTemperature` | `0.7` | OpenAI sampling temperature |
| `lean-ai.openaiContextWindow` | `128000` | OpenAI context window |
| `lean-ai.openaiExpertModel` | *(inherit)* | OpenAI model override for expert phases |
| `lean-ai.anthropicModel` | `claude-sonnet-4-20250514` | Anthropic model name |
| `lean-ai.anthropicTemperature` | `0.7` | Anthropic sampling temperature |
| `lean-ai.anthropicContextWindow` | `200000` | Anthropic context window |
| `lean-ai.anthropicExpertModel` | *(inherit)* | Anthropic model override for expert phases |
| `lean-ai.inlineModel` | *(empty)* | Separate Ollama model for inline predictions |
| `lean-ai.inlineOllamaUrl` | *(inherit)* | Ollama instance for the inline model |
| `lean-ai.embeddingModel` | `qwen3-embedding:0.6b` | Ollama model for semantic search |
| `lean-ai.enableEmbeddings` | `true` | Enable embedding generation and RRF hybrid search |
| `lean-ai.searchProvider` | `duckduckgo` | Web search provider: `duckduckgo`, `searxng`, `google`, `bing` |
| `lean-ai.searchDelay` | `2.0` | Minimum seconds between search requests |
| `lean-ai.enablePostValidation` | `true` | Run lint/test passes after agent changes |
| `lean-ai.postFormatCommand` | *(empty)* | Auto-format command (e.g. `ruff format src/`) |
| `lean-ai.postLintFixCommand` | *(empty)* | Auto-fix lint command (e.g. `ruff check --fix src/`) |
| `lean-ai.postLintCommand` | *(empty)* | Lint check command (e.g. `ruff check src/`) |
| `lean-ai.postTestCommand` | *(empty)* | Test command (e.g. `pytest tests/ -x -q`) |
| `lean-ai.postValidationMaxRetries` | `2` | Max LLM fix attempts on validation failure |
| `lean-ai.enableFrameworkGuide` | `true` | Generate `.lean_ai/framework_guide.md` on `/init` |
| `lean-ai.implementationMaxTurns` | `0` | Max tool-calling turns per session (`0` = unlimited) |
| `lean-ai.refreshThreshold` | `0.7` | Refresh context at this fraction of the context window |
| `lean-ai.debugPlanning` | `false` | Save planning phase outputs to `.lean_ai/plan_debug/` |

### Auto-Start Backend

When `lean-ai.autoStartBackend` is enabled, the extension automatically starts the Python backend server when it activates. Set `lean-ai.pythonPath` to point to your virtualenv's Python if the backend is installed in one:

```
lean-ai.pythonPath: /home/user/Code/lean_ai/backend/.venv/bin/python
```

### Remote Backend

To connect to a backend running on another machine:

```
lean-ai.backendUrl: http://192.168.1.100:8422
lean-ai.autoStartBackend: false
```

## Architecture

The extension is built with:

- **TypeScript** — Main extension code
- **VSCode API** — Sidebar webview, inline completions, commands, SecretStorage
- **WebSocket** — Real-time workflow streaming
- **esbuild** — Fast bundling

The sidebar uses a webview with inline HTML/CSS/JS (no framework). WebSocket connections are managed per-session for workflow streaming, while chat uses standard HTTP POST requests. The settings panel is a separate `WebviewPanel` that opens as an editor tab.

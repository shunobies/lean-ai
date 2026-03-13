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

Configure a separate, faster model for predictions:

```env
LEAN_AI_INLINE_MODEL=qwen2.5-coder:7b
```

### Session Management

The Sessions view in the sidebar shows all workflow sessions with their status. Right-click a session for:

- **View** — See session details and conversation log
- **Merge** — Merge the agent's branch into your main branch
- **Abandon** — Discard the agent's branch
- **Delete** — Remove the session entirely

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
| `Lean AI: Restart Backend Server` | Restart the Python backend |
| `Lean AI: Stop Backend Server` | Stop the Python backend |
| `Lean AI: Refresh Sessions` | Refresh the sessions list |
| `Lean AI: View Session Details` | View a session's details |
| `Lean AI: Merge Session Branch` | Merge a session's branch |
| `Lean AI: Abandon Session` | Abandon a session's branch |
| `Lean AI: Delete Session` | Delete a session |

## Settings

Configure the extension through VSCode's settings UI (**Settings** > search "Lean AI"):

| Setting | Default | Description |
|---|---|---|
| `lean-ai.backendUrl` | `http://localhost:8422` | Backend server URL |
| `lean-ai.enableInlinePredictions` | `true` | Enable Copilot-style inline predictions |
| `lean-ai.autoStartBackend` | `true` | Auto-start the Python backend when the extension activates |
| `lean-ai.pythonPath` | `python` | Path to Python interpreter (e.g. full path to a virtualenv) |
| `lean-ai.backendDir` | *(auto-detect)* | Path to the `backend/` directory containing `pyproject.toml` |
| `lean-ai.chatFontSize` | `13` | Font size (px) for chat messages (10-20, adjusts live) |

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
- **VSCode API** — Sidebar webview, inline completions, commands
- **WebSocket** — Real-time workflow streaming
- **esbuild** — Fast bundling

The sidebar uses a webview with inline HTML/CSS/JS (no framework). WebSocket connections are managed per-session for workflow streaming, while chat uses standard HTTP POST requests.

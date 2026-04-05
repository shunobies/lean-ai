# Lean AI — JetBrains Plugin

A JetBrains IDE plugin for [Lean AI](https://github.com/shunobies/lean-ai), providing the same agentic coding assistant experience as the VS Code extension. One plugin works across **all JetBrains IDEs**: IntelliJ IDEA, PyCharm, WebStorm, CLion, GoLand, PhpStorm, RubyMine, and Rider.

## Requirements

- **JetBrains IDE** 2024.1 or newer (any product)
- **Java 17+** (for building from source; the IDE ships its own JRE for running)
- **Python 3.10+** (for the backend server)
- **Ollama** with a pulled model (e.g. `ollama pull qwen3-coder:30b`) — or a cloud provider API key

Ollama is always required for inline predictions and embeddings, even when using cloud providers.

## Installation

### From JetBrains Marketplace (Recommended)

*Coming soon.* Once published, install directly from **Settings > Plugins > Marketplace** by searching for "Lean AI".

### From Source

#### 1. Clone the repository

```bash
git clone https://github.com/shunobies/lean-ai.git
cd lean-ai/jetbrains-plugin
```

#### 2. Install JDK 17+

The build requires JDK 17 or newer. Check if you already have it:

```bash
java -version
```

If not installed:

```bash
# Debian / Ubuntu
sudo apt install openjdk-17-jdk

# macOS (Homebrew)
brew install openjdk@17

# Windows (winget)
winget install Microsoft.OpenJDK.17
```

> **Note:** The JDK is only needed for building. The IDE ships its own JRE for running the plugin.

#### 3. Install the Gradle wrapper

The project uses the Gradle wrapper, which downloads the correct Gradle version automatically. If you don't have `gradlew` yet:

```bash
gradle wrapper --gradle-version 8.10
```

Or download it manually from [gradle.org](https://gradle.org/install/).

#### 4. Build the plugin

```bash
./gradlew buildPlugin
```

This does three things:
1. Copies the Python backend source from `../backend/` into the plugin's resource bundle
2. Compiles the Kotlin source against the IntelliJ Platform SDK
3. Produces a distributable `.zip` at `build/distributions/lean-ai-plugin-<version>.zip`

#### 5. Install the plugin

In your JetBrains IDE:
1. Go to **Settings > Plugins > ⚙️ (gear icon) > Install Plugin from Disk...**
2. Select the `.zip` file from `build/distributions/`
3. Restart the IDE

### Development Setup

For active development with hot-reload:

```bash
# Run the plugin in a sandboxed IDE instance
./gradlew runIde

# Run with a specific IDE (default: IntelliJ IDEA Community)
./gradlew runIde -PplatformType=PY    # PyCharm
./gradlew runIde -PplatformType=WS    # WebStorm
./gradlew runIde -PplatformType=CL    # CLion
```

The `runIde` task downloads a sandboxed IDE instance, installs the plugin, and launches it — no manual installation needed during development.

To rebuild on file changes:

```bash
./gradlew buildPlugin --continuous
```

Then restart the sandboxed IDE (or use the JetBrains plugin reload action) to pick up changes.

## First Run

On first activation, the plugin:
1. Detects a Python 3 interpreter on your PATH
2. Creates a virtual environment at `~/.cache/JetBrains/lean-ai/backend-venv/`
3. Installs the bundled backend via `pip install -e backend[dev]`
4. Starts the backend server (`uvicorn lean_ai.main:app --port 8422`)
5. Begins health monitoring (polls `/api/health` every 20 seconds)

This happens automatically — no manual backend setup required. The backend is shared across all open IDE windows; only the first window starts the process.

### Manual Backend Mode

If you prefer to manage the backend yourself (e.g., for development or custom deployments), configure these settings in **Settings > Tools > Lean AI**:

| Setting | Effect |
|---|---|
| **Python Path** | Path to your Python interpreter (skips auto-install) |
| **Backend Dir** | Path to your backend source directory (skips bundled extraction) |

When either setting is non-empty, the automatic installer is skipped entirely.

## Features

### Chat Sidebar

The **Lean AI** tool window (right sidebar) provides the full chat interface:
- Conversational LLM chat with workspace context
- Slash commands (`/agent`, `/fix`, `/request`, `/init`, etc.)
- Plan review with approve/reject buttons
- Tool execution progress with diff previews
- Destructive command gate (approve/deny shell commands)
- Context window metrics display
- Collapsible thinking blocks for reasoning models

### Inline Predictions

Copilot-style code completions powered by Ollama. As you type, the plugin sends surrounding code context (50 lines before, 20 lines after the cursor) to the backend for fill-in-the-middle completion.

Configure a separate, faster model for predictions in **Settings > Tools > Lean AI** or via environment variable:

```bash
LEAN_AI_INLINE_MODEL=qwen2.5-coder:7b
```

### Session History

The **Lean AI Sessions** tool window shows all workflow sessions with status indicators. Right-click for context actions: view details, merge branch, abandon, or delete.

### Slash Commands

| Command | What it does |
|---|---|
| `/init` | Index workspace and generate project context |
| `/agent <task>` | Send a task to the planning pipeline |
| `/fix <description>` | Skip planning, fix directly with full tool access |
| `/request <task>` | Open-ended task (request model, no planning) |
| `/approve [feedback]` | Approve the current plan |
| `/reject [feedback]` | Reject the plan with feedback |
| `/resume` | Resume a paused session |
| `/guide` | Regenerate framework guide |
| `/style` | Generate code style guide |
| `/scaffold` | Bootstrap a new project from a template |
| `/reboot` | Restart the backend server |

## Configuration

Settings are available at **Settings > Tools > Lean AI**. The native settings page covers the most common options (provider, model, port, feature toggles). All settings map to `LEAN_AI_*` environment variables — see the [full configuration reference](../docs/configuration.md).

### API Keys

API keys for cloud providers (OpenAI, Anthropic, Gemini) are stored securely in the OS keychain via JetBrains' `PasswordSafe` API. They are never written to disk in plaintext.

### Dual-Model Setup

Use a fast local model for implementation and a cloud model for planning:

```yaml
# In backend/config.yaml
llm_provider: ollama
ollama_model: "qwen3-coder:30b"

expert_llm_provider: anthropic
anthropic_expert_model: "claude-opus-4-6"
```

## Project Structure

```
jetbrains-plugin/
├── build.gradle.kts                 # Gradle build (IntelliJ Platform Plugin 2.x)
├── gradle.properties                # IDE version targets, plugin metadata
├── settings.gradle.kts
└── src/main/
    ├── kotlin/com/leanai/plugin/
    │   ├── LeanAiPlugin.kt         # Startup: install + launch backend
    │   ├── backend/
    │   │   ├── BackendInstaller.kt  # Venv, pip install, version tracking
    │   │   ├── BackendProcess.kt    # Subprocess, health monitor, auto-restart
    │   │   └── BackendClient.kt     # OkHttp REST + WebSocket client
    │   ├── ui/
    │   │   ├── ChatToolWindowFactory.kt   # JCEF chat sidebar
    │   │   ├── ChatBridge.kt              # Kotlin ↔ JS bridge
    │   │   └── SessionsToolWindowFactory.kt # Session tree panel
    │   ├── completion/
    │   │   └── LeanAiInlineCompletionProvider.kt  # FIM inline suggestions
    │   ├── actions/                 # Approve, Reject, Restart actions
    │   ├── settings/                # PersistentStateComponent + Configurable
    │   ├── notifications/           # IDE balloon notifications
    │   ├── ws/                      # WebSocket message types + handler
    │   └── util/                    # Python detection, settings sync
    └── resources/
        ├── META-INF/plugin.xml      # Plugin descriptor
        └── webview/                 # Chat UI (HTML/CSS/JS for JCEF)
```

## Architecture

The plugin is a thin UI layer that communicates with the **same Python backend** as the VS Code extension. All LLM orchestration, tool execution, planning, and code generation happen in the backend — the plugin handles display and user interaction.

```
┌──────────────────────────────────────┐
│ JetBrains Plugin (Kotlin)            │
│  • JCEF chat UI (HTML/CSS/JS)        │
│  • Inline completions                │
│  • Session tree                      │
│  • Settings + credential storage     │
└───────────────┬──────────────────────┘
                │ REST + WebSocket
                │ (http://localhost:8422)
┌───────────────┴──────────────────────┐
│ Lean AI Backend (Python/FastAPI)     │
│  • LLM orchestration (Ollama/cloud)  │
│  • Tool execution (file ops, shell)  │
│  • Planning pipeline                 │
│  • Post-validation (lint/test)       │
└──────────────────────────────────────┘
```

The backend is bundled inside the plugin JAR and auto-installed into a virtual environment on first run. No separate backend setup is needed.

## Troubleshooting

### Backend fails to start

Check that Python 3.10+ is on your PATH:

```bash
python3 --version
```

If the automatic installer fails, try manual mode: set **Python Path** in settings to your interpreter, and **Backend Dir** to the `backend/` directory in the repo.

### JCEF not available

The chat UI requires JCEF (JetBrains Chromium Embedded Framework). It ships with all JetBrains IDEs since 2020.2. If you see a JCEF error, ensure it's enabled:

1. Go to **Help > Find Action**
2. Search for "Registry"
3. Enable `ide.browser.jcef.enabled`
4. Restart the IDE

### Port conflict

If port 8422 is already in use, change it in **Settings > Tools > Lean AI > Backend Port**. The plugin will kill zombie processes on the configured port before starting.

### Health check failures

The plugin monitors the backend with a health check every 20 seconds. After 3 consecutive failures, it auto-restarts the backend. If restarts keep failing, check the IDE log (**Help > Show Log in Finder/Explorer**) for Python errors.

## Building for Distribution

```bash
# Build the plugin zip
./gradlew buildPlugin

# Run plugin verifier (checks API compatibility)
./gradlew verifyPlugin

# Publish to JetBrains Marketplace (requires token)
./gradlew publishPlugin -Pintellij.publish.token=YOUR_TOKEN
```

The `buildPlugin` task automatically copies the backend source into the plugin archive. No separate packaging step is needed.

## Compatibility

| IDE | Minimum Version |
|---|---|
| IntelliJ IDEA (Community/Ultimate) | 2024.1 |
| PyCharm (Community/Professional) | 2024.1 |
| WebStorm | 2024.1 |
| CLion | 2024.1 |
| GoLand | 2024.1 |
| PhpStorm | 2024.1 |
| RubyMine | 2024.1 |
| Rider | 2024.1 |

The minimum version (2024.1) is required for the `InlineCompletionProvider` API. All JetBrains IDEs built on the IntelliJ Platform are supported from a single plugin build.

## License

MIT — same as the main Lean AI project.

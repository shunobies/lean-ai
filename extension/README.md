# Lean AI

**Your codebase already has an architect. It just needs tools.**

Lean AI is an agentic coding assistant that reads your project, plans changes, and executes them — all inside your editor. Give it a task in plain English, review the plan, and watch it work.

Run it fully local with [Ollama](https://ollama.com), or connect to OpenAI, Anthropic, Gemini, or Lean AI Serve when you need heavier reasoning. No cloud account required to get started.

## Features

- **No prompt engineering needed** — describe what you want in plain English. A built-in chat assistant helps you refine your idea into a detailed task before the 6-phase planning pipeline takes over.
- **Plan first, then execute** — a 6-phase planning pipeline reads your codebase, traces data flow, and produces a structured plan. You approve before anything changes. Thinking and content tokens stream live during each phase so you can watch the agent reason in real-time.
- **Three workflow modes** — `/agent` for full planning, `/fix` for quick bug fixes, `/request` for open-ended research and documentation tasks.
- **Architecture review mode** — `/improve-codebase-architecture` reviews the current repo using code, project context, session history, memories, and recorded decisions to suggest high-leverage deepening opportunities.
- **Multi-provider** — Ollama (free, local), OpenAI, Anthropic, Gemini, and Lean AI Serve. Switch from the settings panel without restarting.
- **Multi-model roles** — assign independent models to primary, expert, request, worker, and inline prediction roles. Mix local and cloud providers per role — e.g., a fast Ollama model for coding and a cloud model for planning.
- **Built-in code quality** — auto-runs your linter and tests after every change, with LLM self-correction on failure.
- **Reference library** — drop internal docs (PDF, EPUB, Word, Markdown) into `.lean_ai/reference/` for context-aware plans.
- **MediaWiki integration** — connect to an internal wiki so the agent can search and read company documentation while working on tasks. Supports authenticated and public wikis.
- **Integrations** — two-way sync with GitHub, Jira, and ServiceNow for session summaries, task linking, and optional commit attribution.
- **Git-native** — every task runs on its own branch. Approve to merge, reject to discard.
- **19 scaffold recipes** — bootstrap new projects with `/scaffold`.
- **Notes & TODOs** — save notes from chat with `/note`, and the LLM can save notes and track project TODOs via built-in tools. Notes are auto-categorized by project with tags and searchable from the Notes panel.
- **Voice interaction** — optional Speech-to-Text, Text-to-Speech, and wake word detection for hands-free coding (see [Voice](#voice) below).
- **Vision support** — paste or drag-and-drop screenshots and images into the chat. A vision model describes visual content so the LLM can reason about UI mockups, error screenshots, and diagrams (see [Vision](#vision) below).

## Quick Start

### 1. Install the extension

Install from the [VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=lean-ai.lean-ai) or [OpenVSX](https://open-vsx.org/extension/lean-ai/lean-ai). On first activation, the extension automatically creates a Python virtual environment and installs the backend server — no manual setup required.

### 2. Install Ollama

Download [Ollama](https://ollama.com) and pull a model:

```bash
ollama pull qwen3-coder:30b
```

### 3. Open a project and run `/init`

Type `/init` in the chat panel to index your workspace and generate project context. Then describe what you want built.

### Manual backend setup (advanced)

If you prefer to manage the backend yourself, set `lean-ai.backendDir` or `lean-ai.pythonPath` in settings. The automatic installer is skipped when either setting is explicitly configured. See the [GitHub repository](https://github.com/shunobies/lean-ai) for details.

## Slash Commands

| Command | Description |
|---------|-------------|
| `/init` | Index workspace and generate project context |
| `/agent` | Full planning pipeline for features and refactors |
| `/fix` | Skip planning, fix directly with full tool access |
| `/request <task>` | Skip planning, open-ended task with full tool access |
| `/improve-codebase-architecture [focus]` | Review the codebase for high-leverage architecture improvements |
| `/style` | Generate a style guide for the current codebase |
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
| `/approve` | Merge the agent's branch |
| `/reject` | Discard the agent's branch |
| `/scaffold` | Bootstrap a new project from a recipe |
| `/note` | Save a note from the chat (auto-categorized by project) |
| `/memories` | Manually trigger memory extraction from the last completed workflow session |
| `/reboot` | Restart the backend server |

## Vision

Attach screenshots, UI mockups, error messages, or any image to the chat. A separate Ollama vision-language model describes the image so the main LLM can understand visual content without native vision support.

**Setup:**

1. Pull a vision model: `ollama pull qwen3-vl:8b`
2. Set `lean-ai.visionModel` to `qwen3-vl:8b` in the extension settings (or `LEAN_AI_VISION_MODEL` env var).

**Usage:** Paste an image with Ctrl+V, drag-and-drop a file onto the chat input, or use the attachment button. Images work in both chat conversations and agent workflows — the vision model processes each image in parallel and injects the description into the LLM context.

| Setting | Default | Description |
|---------|---------|-------------|
| `lean-ai.visionModel` | *(empty, disabled)* | Ollama vision model (e.g. `qwen3-vl:8b`) |
| `lean-ai.visionOllamaUrl` | *(falls back to main Ollama URL)* | Separate Ollama instance for vision |
| `LEAN_AI_VISION_MAX_TOKENS` | `1024` | Max tokens per image description |
| `LEAN_AI_VISION_TIMEOUT` | `60` | Timeout per image (seconds) |

## Voice

Optional voice interaction for hands-free coding: speak your requests, hear responses read aloud, and trigger recording with a wake word.

> **Ubuntu 26.04 / Python 3.14 note:** The `voice` extra currently needs Python **3.13 or earlier**. The TTS dependency `kokoro-onnx` does not yet support Python 3.14, so `pip install "lean-ai[voice]"` will fail if your backend is using the distro-default Python 3.14 interpreter. Create the backend virtualenv with `python3.13` (or another Python `<3.14`) and point Lean AI at that venv with `lean-ai.pythonPath` and `lean-ai.backendDir`.

**Setup:**

```bash
# Install voice dependencies (requires portaudio system library)
# Ubuntu 26.04 / Debian (recommended: pin the backend venv to Python 3.13)
sudo apt install portaudio19-dev build-essential
cd /path/to/lean_ai/backend
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[voice]"

# Then point the extension at the pinned backend:
# lean-ai.backendDir = /path/to/lean_ai/backend
# lean-ai.pythonPath = /path/to/lean_ai/backend/.venv/bin/python

# Other Ubuntu/Debian setups that already use Python 3.13 or earlier:
sudo apt install portaudio19-dev
pip install "lean-ai[voice]"

# macOS:
brew install portaudio

# Install Python voice extras
pip install "lean-ai[voice]"
```

Enable the features you want in the extension settings or via environment variables:

| Setting | Default | Description |
|---------|---------|-------------|
| `LEAN_AI_ENABLE_STT` | `false` | Enable Speech-to-Text (faster-whisper) |
| `LEAN_AI_ENABLE_TTS` | `false` | Enable Text-to-Speech (kokoro-onnx, 58 voices) |
| `LEAN_AI_ENABLE_WAKE_WORD` | `false` | Enable "Hey Jarvis" wake word detection (openWakeWord) |

When voice dependencies are missing but settings are enabled, the extension offers to install them automatically.

### Speech-to-Text (STT)

Click the mic button in the chat input to record, click again to stop. The audio is transcribed locally using [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (a CTranslate2-based Whisper implementation) — no audio leaves your machine.

| Setting | Default | Description |
|---------|---------|-------------|
| `LEAN_AI_STT_MODEL` | `turbo` | Whisper model: `tiny`, `base`, `small`, `medium`, `large-v3`, `turbo` |
| `LEAN_AI_STT_LANGUAGE` | *(auto-detect)* | ISO 639-1 language code (e.g. `en`, `fr`) |
| `LEAN_AI_STT_SILENCE_THRESHOLD` | `4.0` | Seconds of silence before auto-stop |
| `LEAN_AI_STT_BEAM_SIZE` | `1` | 1 = greedy (fastest), 5 = beam search (most accurate) |
| `LEAN_AI_STT_CPU_THREADS` | `6` | CPU threads for transcription |

### Text-to-Speech (TTS)

Toggle TTS in the chat voice controls. The LLM's responses are read aloud using [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) with 58 voices at 24kHz. Sentences stream as they arrive, so speech starts before the full response is generated. Code blocks are automatically stripped so the engine doesn't read code aloud.

TTS model files (~169MB for fp16) are downloaded automatically on first use.

| Setting | Default | Description |
|---------|---------|-------------|
| `LEAN_AI_TTS_VOICE` | `af_heart` | Voice ID (e.g. `af_heart`, `am_adam`, `bf_emma`) |
| `LEAN_AI_TTS_SPEED` | `1.0` | Playback speed (0.5 to 2.0) |
| `LEAN_AI_TTS_MODEL_QUALITY` | `fp16` | Model variant: `fp32` (~311MB), `fp16` (~169MB, 2x faster), `int8` (~88MB) |

### Wake Word

Enable wake word detection for hands-free activation. When the wake word is detected, STT recording starts automatically and the transcribed text is submitted to the chat.

The wake word listener runs on a background thread at 16kHz, using [openWakeWord](https://github.com/dscripka/openWakeWord). The default trigger phrase is **"Hey Jarvis"**. While STT is recording, the wake word listener pauses to avoid mic contention, then resumes when recording stops.

Set `lean-ai.wakeWordAutoSubmit` to `true` to automatically send the transcribed message after wake word activation (otherwise the text is placed in the input for you to review).

All voice processing runs on CPU only — GPU is reserved for the LLM.

## Configuration

Open the settings panel (gear icon in the chat header) to configure:

- **LLM Provider** — Ollama, OpenAI, Anthropic, Gemini, or Lean AI Serve
- **Model selection** — primary, expert, request, worker, and inline models with independent sampling parameters (temperature, top-p, top-k, repeat penalty, context window, max tokens) and thinking mode per model
- **Post-validation** — lint, test, and format commands
- **Search provider** — DuckDuckGo, SearXNG, Google, or Bing
- **MediaWiki** — connect to an internal wiki instance (URL, API path, optional authentication)
- **Integrations** — GitHub, Jira Cloud, and ServiceNow for two-way task sync and optional Lean AI co-author trailers
- **Vision model** — Ollama vision-language model for image understanding
- **Voice** — STT, TTS, and wake word settings

Settings are saved to `backend/config.yaml`. API keys and integration tokens are stored securely in your OS keychain — never written to config files. For standalone backend usage, secrets can be [encrypted](https://github.com/shunobies/lean-ai/blob/main/docs/configuration.md#encrypted-api-keys) in the YAML file.

## Requirements

- Python 3.10-3.13 (Python 3.13 recommended; Python 3.14 is not supported)
- [Ollama](https://ollama.com) with a capable model (e.g., `qwen3-coder:30b`) — or an OpenAI/Anthropic API key
- **For voice:** portaudio system library + `pip install "lean-ai[voice]"` (optional)
- **For vision:** an Ollama vision model like `qwen3-vl:8b` (optional)

## Links

- [GitHub Repository](https://github.com/shunobies/lean-ai) — source code, backend setup, and full documentation
- [Changelog](CHANGELOG.md) — release history and recent changes
- [Configuration Guide](https://github.com/shunobies/lean-ai/blob/main/docs/configuration.md) — all environment variables and settings

## License

MIT

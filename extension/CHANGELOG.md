# Changelog

All notable changes to the Lean AI extension will be documented in this file.

## [0.7.0] - 2026-04-02

### Added
- **MediaWiki integration** — new `search_wiki` and `fetch_wiki_page` tools let the agent search and read pages from an internal MediaWiki instance while working on tasks. Supports both authenticated (bot account login) and public wikis. Configure from the Advanced Settings tab: Wiki URL, API path, username, and password (stored in OS keychain). Tools are automatically available in all workflow modes (plan, fix, request) when a wiki URL is configured.

### Fixed
- **npm security vulnerabilities** — fixed lodash prototype pollution and brace-expansion ReDoS vulnerabilities

## [0.6.10] - 2026-04-01

### Added
- **Jira/ServiceNow settings UI** — configuration fields for Jira and ServiceNow added to the Advanced Settings tab (URL, credentials, table name). Passwords and API tokens stored securely in the OS keychain.

## [0.6.9] - 2026-04-01

### Added
- **Google Gemini provider** — use Gemini models (e.g., `gemini-2.5-flash`, `gemini-2.5-pro`) via the Google GenAI SDK. Supports primary, expert, and request model roles with context windows up to 1M+ tokens.

## [0.6.8] - 2026-04-01

### Added
- **Two-way integration framework** — generic framework for external task tracking services with provider ABC, registry, SQLite persistence, and REST endpoints. Ships with **Jira Cloud** and **ServiceNow** providers for session summaries, task linking, and search. Gated by `LEAN_AI_ENABLE_INTEGRATIONS`.
- **Lean AI Serve provider** — connect to a self-hosted lean-ai-serve (vLLM) server as an LLM provider. Fully OpenAI-compatible API with independent primary, expert, and request model configuration.

## [0.6.7] - 2026-04-01

### Added
- **Notes sidebar icon** — quick-access button in the sidebar header to open the Notes & TODOs panel

## [0.6.6] - 2026-03-31

### Changed
- **Chat code blocks** — the chat assistant now formats code in fenced code blocks for a more structured, developer-friendly experience while remaining TTS-friendly

## [0.6.5] - 2026-03-31

### Added
- **YAML configuration** — settings are now saved to `backend/config.yaml` instead of `backend/.env`. YAML format with field names (e.g. `ollama_url` instead of `LEAN_AI_OLLAMA_URL`). The `.env` file continues to work as a fallback.
- **Encrypted API keys** — API keys in `config.yaml` can be Fernet-encrypted with an `enc:` prefix so leaked config files don't expose raw credentials. CLI tool: `python -m lean_ai encrypt-key <key>`. Extension users are unaffected — API keys remain in the OS keychain.
- **CLI config tools** — `python -m lean_ai` commands for key encryption (`encrypt-key`), `.env` migration (`migrate-env`), and config template generation (`generate-config`).

## [0.6.4] - 2026-03-31

### Added
- **Notes & TODOs** — cross-project notes system with `/note` slash command. Notes are stored globally (`~/.lean_ai/notes/`), auto-categorized by project and tagged via background LLM analysis. The chat LLM has `save_note` and `list_project_todos` tools for in-conversation note-taking and TODO tracking. A dedicated Notes & TODOs webview panel provides full CRUD, search, and filtering.

### Changed
- **Chat prompt** — the chat assistant now acts as a general-purpose coding assistant by default, only entering prompt-building mode when explicitly asked. Responses are conversational rather than always producing a Suggested Agent Prompt.

## [0.6.3] - 2026-03-29

### Added
- **Customizable prompts** — override any built-in LLM prompt per project via `.lean_ai/prompts.yaml`. Edit from the UI with the "Edit Prompts" command. Useful when your model needs different instructions than the defaults.

## [0.6.2] - 2026-03-29

### Added
- **Centralized prompt registry** — all LLM prompts are managed through a registry with an **Edit Prompts** UI in the extension for viewing and customizing prompts
- **IaC framework guide** — `/init` now detects Infrastructure-as-Code frameworks (Terraform, Ansible, Pulumi, CloudFormation, etc.) and generates architecture guides for models without current IaC knowledge
- **Language-agnostic planning** — planning prompts no longer assume web/application code; IaC and infrastructure projects generate read_file-only plans when appropriate

## [0.6.1] - 2026-03-29

### Added
- **Chat file exploration** — the chat assistant now has read-only file tools (`read_file`, `list_directory`, `directory_tree`, `grep_files`) to explore your project and give better advice before producing a Suggested Agent Prompt

## [0.6.0] - 2026-03-29

### Changed
- **Prompt hardening** — improved instruction-following reliability with assumption verification step in chat prompts

## [0.5.10] - 2026-03-29

### Fixed
- **Pop-out window** — fixed a regression that broke the pop-out chat feature after planning prompt changes

## [0.5.9] - 2026-03-28

### Fixed
- **False clarification with Qwen3.5** — short non-question responses (like `-` or brief acknowledgments) no longer trigger the clarification loop
- **Phase-specific planning prompts** — fixed tool/capability mismatches where prompts referenced tools unavailable in that phase
- **Request model routing** — planning phases 1-2 and task clarification now correctly route to the request model
- **Planning context** — full context passed to expert phases; fixed step history truncation in later planning phases

## [0.5.8] - 2026-03-27

### Added
- **Python scaffold improvements** — Python scaffolds now include venv creation and dependency installation steps
- **Build tooling detection** — framework guide generation now detects Vite, Tailwind, and other build tools

### Fixed
- **/approve and /reject commands** — fixed session ID loss after workflow completion that prevented merge/discard
- **Fix-mode tools** — `/fix` now correctly exposes available tools to the LLM
- **Pop-out window** — fixed icon to merge chat back to sidebar

## [0.5.7] - 2026-03-27

### Added
- **TTS queue controls** — stop button kills entire TTS queue, replay button to re-hear the last response, configurable audio threads

### Fixed
- **Framework guide** — switched from coding model to request model for better quality; fixed deep-mode file injection and budget separation

## [0.5.6] - 2026-03-26

### Changed
- **TTS input cleaning** — strips URLs, emoji, arrows, HTML tags, and markdown formatting from TTS input for cleaner, more natural speech output

## [0.5.5] - 2026-03-26

### Changed
- **Real-time TTS** — incremental sentence streaming with gapless playback via raw PCM pipeline, replacing the previous batch-and-wait approach

## [0.5.4] - 2026-03-26

### Changed
- **Command safety gate** — `run_command` auto-executes safe commands (build, install, migrate) without user approval. Only destructive commands (rm, dd, chmod, git push, etc.) prompt for confirmation.

## [0.5.3] - 2026-03-26

### Fixed
- **Vision model timeout** — increased from 60s to 120s for multi-image processing and cold starts
- **Vision empty response** — gracefully handles cases where the vision model returns no description

## [0.5.2] - 2026-03-26

### Added
- **`run_command` tool** — general-purpose shell command execution for builds, migrations, code generators, and package installs. Separate from `run_tests`/`run_lint`/`format_code`.

### Changed
- **Naming consistency** — softened prescriptive naming rules to a consistency nudge, letting models follow existing project conventions

## [0.5.1] - 2026-03-26

### Fixed
- **Pop-out voice/stop** — voice controls and stop button now work in the detached chat window
- **Vision descriptions** — image descriptions from the vision model are now visible in the chat UI
- **TTS phoneme crash** — fixed crash when TTS encountered unsupported phoneme sequences

## [0.5.0] - 2026-03-25

### Changed
- **Canonical policy blocks** — consolidated all LLM system prompts into shared, composable policy blocks (`TOOL_POLICY`, `COMPLETION_CONTRACT`, `QUALITY_RULES`, etc.) for better small-model optimization. Prevents instruction duplication across execution modes.

### Fixed
- **picomatch ReDoS** — fixed high-severity regular expression denial-of-service vulnerability (GHSA-c2c7-rcm5-vvqj)

## [0.3.37] - 2026-03-25

### Fixed
- **Pop-out chat voice** — STT and TTS now work in the detached chat window (previously only worked in the sidebar)
- **Planning docs** — corrected "6 phases" to "5 phases" across all documentation

## [0.3.36] - 2026-03-25

### Fixed
- **New window chat** — fixed two regressions in the detached chat window feature

## [0.3.35] - 2026-03-25

### Fixed
- **Greeting TTS** — startup greeting now speaks via TTS when enabled
- **Greeting web search** — startup greeting no longer triggers an unnecessary web search
- **Settings zero values** — expert/request model parameters (temperature, top_p, etc.) no longer persist `0` to the backend when left empty; `0` is treated as "inherit from primary"

## [0.3.34] - 2026-03-25

### Fixed
- **Settings env vars** — fixed backend crash when zero-value settings were passed from the extension

## [0.3.33] - 2026-03-25

### Fixed
- **Settings panel** — fixed a bug that prevented the settings panel from saving correctly

## [0.3.32] - 2026-03-25

### Changed
- **Edge case hardening** — consolidated multi-monitor, copy/paste, send-to-terminal, and greeting warm-up improvements from recent releases

## [0.3.31] - 2026-03-25

### Added
- **Open Chat in New Window** — a pop-out button next to Search in the header opens the chat in a separate editor panel (`ViewColumn.Two`), ideal for multi-monitor setups. The sidebar and detached panel stay in sync — messages, tokens, and state are broadcast to both. Also available via the command palette: `Lean AI: Open Chat in New Window`.
- **Code block action buttons** — every code block in chat responses now shows a floating toolbar (top-right, on hover) with Copy to Clipboard and Send to Terminal buttons. The terminal button reuses the active VS Code terminal or creates a new one. Existing "Send to Agent" buttons on Suggested Agent Prompt blocks are unaffected.

### Changed
- **Refactored webview message handler** — extracted the inline `onDidReceiveMessage` switch into a shared `handleWebviewMessage()` method, enabling both the sidebar and detached panel to share the same message routing.

## [0.3.30] - 2026-03-25

### Added
- **First-boot quickstart guide** — new users see an in-chat setup guide with OS-specific Ollama install commands, model pull command, and links to ollama.com. Commands have copy-to-clipboard buttons. Shown automatically when the backend isn't reachable and setup hasn't been completed before.
- **LLM greeting on startup** — when the sidebar opens with a fresh chat, the LLM automatically greets the user and asks what they'd like to work on today, including the project name for context.
- **Personalized chat** — new `lean-ai.userName` setting lets the LLM address you by name in conversation.
- **STT model warm-up** — the speech-to-text model now pre-loads in the background when the extension starts (if STT is enabled), eliminating the delay on first voice use.

## [0.3.29] - 2026-03-24

### Fixed
- **Wake word detection** — completely rewritten SSE transport for wake word events. Replaced `fetch()` with `http.request()` + `socket.setTimeout(0)` to avoid Node/undici's 5-minute connection timeout that silently killed the event stream. Added auto-reconnect with 3-second backoff so the connection recovers from backend restarts.
- **Wake word error reporting** — the wake word listener now reports failures (model load errors, mic access failures, runtime crashes) back to the extension via SSE events. The chat shows an error message and the toggle resets automatically instead of silently appearing active while the listener is dead.
- **Wake word model loading** — updated for openWakeWord v0.4 API. Removed defunct `download_models()` call, switched to bundled model loading, and changed default wake word from "Hey Computer" (no longer bundled) to **"Hey Jarvis"**.
- **Wake word availability check** — `is_wake_word_available()` now verifies both `openwakeword` and `pyaudio` are installed (previously only checked `openwakeword`, reporting available when the mic couldn't actually open).
- **SSE heartbeat** — the voice events SSE endpoint now sends an immediate `:connected` flush on connection and `:heartbeat` every 30 seconds to keep the connection alive through proxies and detect stale connections.
- **Mic open crash** — the `pa.open()` call for the wake word microphone stream was unguarded. If it threw (e.g., no mic available, permission denied), the listener thread crashed silently. Now caught and reported.

### Changed
- **Chat agent** — no longer asks redundant questions already answered by the project context.
- **Chat URL handling** — skips redundant web search when the user's message already contains explicit URLs; fixed stale `summarize_threshold` parameter in URL fetching.
- **Inline predictions** — gracefully handles models that don't support FIM (Fill-in-the-Middle) instead of erroring.

## [0.3.28] - 2026-03-24

### Fixed
- **TTS playback in VSCode** — fixed TTS not working due to cross-site scripting detection in VSCode. Switched from data URIs to blob URLs and then to Web Audio API to bypass autoplay restrictions.

## [0.3.27] - 2026-03-24

### Fixed
- **TTS audio engine** — switched from URI-based to blob URL-based audio playback for better compatibility

## [0.3.26] - 2026-03-24

### Changed
- **CPU-only voice** — TTS and STT are hardcoded to use CPU, reserving GPU for the LLM. ALSA error suppression on Linux.

### Fixed
- **Wake word detection** — attempted fix for wake word not triggering correctly alongside STT

## [0.3.25] - 2026-03-24

### Fixed
- **Wake word detection** — fixed wake word not firing; improved STT auto-stop notification and silence threshold handling
- **TTS/STT optimization** — fp16 model default, sentence-level streaming, greedy decoding for faster transcription

## [0.3.24] - 2026-03-24

### Added
- **Vision model support** — attach images (paste or drag-and-drop) to chat and agent workflows. An optional Ollama vision-language model (e.g. `qwen3-vl:8b`) describes each image so the main LLM understands visual content — screenshots, UI mockups, error messages, terminal output. Multiple images are processed in parallel. Configure via `lean-ai.visionModel` in settings; disabled by default.
- **TTS sentence streaming** — TTS now speaks each sentence as it arrives during chat streaming instead of waiting for the full response. Reduces perceived delay from LLM output to audible speech.
- **TTS code block filtering** — code blocks and inline code are stripped before TTS synthesis so the engine doesn't read code aloud.
- **TTS model quality selection** — choose between fp32 (~311MB, highest quality), fp16 (~169MB, 2x faster, new default), or int8 (~88MB, smallest) via `LEAN_AI_TTS_MODEL_QUALITY`.
- **Conversational chat responses** — the request LLM now produces flowing sentences instead of bullet lists, making TTS output sound natural.

### Changed
- **TTS performance** — ONNX Runtime session optimization (graph fusion, CPU thread tuning, warmup inference) for faster audio synthesis.
- **STT performance** — faster-whisper now defaults to greedy decoding (`beam_size=1`) and 6 CPU threads for quicker transcription. Configurable via `LEAN_AI_STT_BEAM_SIZE` and `LEAN_AI_STT_CPU_THREADS`.

## [0.3.23] - 2026-03-24

### Added
- **Voice interaction** — optional Speech-to-Text, Text-to-Speech, and wake word detection. Enable via settings (`LEAN_AI_ENABLE_STT`, `LEAN_AI_ENABLE_TTS`, `LEAN_AI_ENABLE_WAKE_WORD`). Requires `pip install "lean-ai[voice]"` and portaudio for STT/wake word.
  - **Mic button** in the chat input for push-to-talk transcription (faster-whisper STT).
  - **TTS toggle** to have the LLM read responses aloud (kokoro-onnx, 58 voices, 24kHz audio). Voice and speed selectable from the chat controls.
  - **Wake word** ("Hey Computer") for hands-free activation via openWakeWord.
- **Auto-install voice dependencies** — when voice settings are enabled but dependencies are missing, the extension offers to install them automatically via a notification with "Install Now" and "Show Instructions" options.
- **TTS model auto-download with progress** — the ~310MB kokoro-onnx model files are downloaded automatically on first use with a visible progress notification. A confirmation message appears when download completes.
- **Python 3.13 support for TTS** — switched from `kokoro` (PyTorch-based, Python <3.13) to `kokoro-onnx` (ONNX Runtime, Python 3.10–3.13).

## [0.3.22] - 2026-03-24

### Fixed
- **Indexing** — fixed a bug where `/init --force` could not complete due to an undefined variable (`indexResult is not defined`)

## [0.3.21] - 2026-03-23

### Added
- **Vision model support** (initial) — image description via Ollama vision-language models; later refined in 0.3.24
- **Voice interaction** (initial) — STT, TTS, and wake word support; later refined in 0.3.23

### Changed
- **Thinking bubble UX** — expanded by default with improved text contrast
- **Chat routing** — `/chat` now routes through the request model when configured

## [0.3.20] - 2026-03-22

### Added
- **Planning phase indicator** — the status badge now shows "planning phase 1" through "planning phase 6" as each planning phase starts, instead of a static "planning" label. Also reflected in the thinking indicator and Chat Participant progress.

## [0.3.19] - 2026-03-21

### Fixed
- **Planning hallucinations** — fixed context not passing properly between planning phases, causing the expert model to hallucinate naming conventions, file listings, and codebase exploration results. Project context is now loaded from disk for expert phases.

## [0.3.18] - 2026-03-21

### Fixed
- **Settings save** — fixed extension settings not saving correctly; added error handling and wired missing `postValidationFixTurns` field
- **Backend restart on save** — the extension now offers to restart the backend server after settings are saved

## [0.3.15] - 2026-03-21

### Added
- **TDD mode** — when `LEAN_AI_ENABLE_TDD` is enabled, plan execution runs in three phases: (A) expert model writes all tests first, (B) primary model reviews and can dispute tests, (C) primary model implements code with test files protected. The dispute mechanism runs a tight expert session to evaluate whether a test is genuinely flawed.

## [0.3.14] - 2026-03-21

### Changed
- **Plan-to-execution consistency** — added name registry, cross-step context, and enriched instructions to reduce drift between planned and implemented code
- **Smart auto-scroll** — chat panel pauses auto-scroll when the user scrolls up, and resumes when scrolled back to bottom

## [0.3.13] - 2026-03-21

### Added
- **Fix-mode investigation phase** — `/fix` now enforces a read-only investigation phase before the LLM can edit files. The LLM reads code, reproduces errors, traces references, and records a diagnosis in the scratchpad before gaining write access. Controlled by `LEAN_AI_ENABLE_FIX_INVESTIGATION` (default: `true`).

## [0.3.12] - 2026-03-21

### Added
- **Stop button** — a red "Stop" button appears next to Send while a workflow is running. Clicking it immediately cancels the workflow at the next turn boundary, closing the WebSocket and resetting the UI. Works in plan mode, fix mode, and resume.
- **Mid-workflow message injection** — you can now type a message while the LLM is executing. The message is injected into the conversation as a `[USER INTERRUPT]` before the next LLM turn, allowing you to course-correct the agent without waiting for it to finish.
- **WebSocket message dispatcher** — new `WSMessageDispatcher` centralizes all WebSocket message routing during workflow execution, replacing direct `safe_receive()` calls. Enables concurrent handling of cancel, user interrupt, and tool approval messages.

## [0.3.11] - 2026-03-21

### Changed
- **Expert model for /fix mode** — bug-fix workflows now use the expert model (when configured) since diagnosis is reasoning-heavy. Previously only planning phases 3-5 and the final validation retry used the expert model.

### Fixed
- **Custom steering docs truncated** — `.lean_ai/context/` steering documents were being cut short during execution due to condensed context loading. Now loads full context for steering docs.
- **Completion nudge improvement** — the text-only response nudge is now completion-aware: when the LLM's response looks like a summary, it's nudged to call `task_complete` instead of being pushed into unnecessary extra work.

## [0.3.10] - 2026-03-20

### Fixed
- **Settings save overwriting inherit params** — expert/request model fields (temperature, top_p, top_k, repeat penalty, context window, max tokens) no longer persist `0` to `.env` when left empty. `0` is now treated as "inherit from primary model" for these fields, matching the "leave empty to inherit" UI hints. Stale `.env` entries are commented out when a setting is reset to default.

## [0.3.9] - 2026-03-20

### Added
- **Chat stream diagnostics** — backend now logs Ollama `done_reason` and token count after each chat stream, and warns when output is truncated due to context window overflow. Prompt size (estimated tokens vs `num_ctx`) is logged before streaming begins.
- **Truncation indicator** — the chat UI shows "(truncated)" in the metrics footer when the server stream ends without a proper completion signal, making silent truncation visible.
- **Session tree pause/resume** — session list auto-refresh is paused during active chat streaming to avoid unnecessary backend polling.

### Changed
- **Session tree refresh interval** — reduced from 30 seconds to 120 seconds to decrease output panel noise.

## [0.3.7] - 2026-03-20

### Added
- **Per-model sampling parameters** — Expert and Request Ollama models now have independent settings for temperature, top-p, top-k, repeat penalty, context window, and max tokens. Previously only model name and context window were configurable for expert, and only model name for request.
- **Per-model thinking toggle** — each Ollama model (primary, expert, request) has its own Enable Thinking checkbox. Use thinking-capable models (Qwen3, Qwen3.5) alongside non-thinking models without conflict.

## [0.3.6] - 2026-03-20

### Added
- **Streaming planning output** — all 6 planning phases now stream thinking and content tokens to the sidebar in real-time. Previously, planning ran silently with only stage status messages; now you can watch the LLM's reasoning as it analyzes scope, explores files, designs changes, assesses risks, and assembles the plan.
  - Phases 1, 3, 4 (scope, design, risks): thinking + content tokens stream live
  - Phase 2 (file identification): tool call/result progress indicators + per-turn content
  - Phases 5, 6 (plan assembly, verification): thinking tokens stream (JSON content hidden)
- **Planning tool activity** — Phase 2 codebase exploration now shows tool progress (file reads, greps) in the sidebar, matching the feedback level of execution mode.

## [0.3.2] - 2026-03-20

### Added
- **Clickable files in completion summary** — the "Files modified" list at task completion now renders each file as a clickable link. Clicking opens a side-by-side git diff (base branch vs working copy) using VS Code's built-in diff viewer. New files diff against empty; falls back to opening the file directly when no branch info is available.

## [0.3.1] - 2026-03-20

### Added
- **Thinking mode toggle** — new `LEAN_AI_ENABLE_THINKING` setting (default: on) passes `think=True` to Ollama so reasoning models like Qwen3 and Qwen3.5 properly separate thinking from content. Thinking traces now appear reliably in the collapsible "Thinking..." section. Toggle off in the settings panel for faster responses without deep reasoning.
- **Large file creation guidance** — system prompts now instruct the LLM to build files over ~200 lines incrementally (create skeleton, then edit_file per section) to avoid output truncation.

### Fixed
- **Model appearing "hung"** — Qwen3.5 generates thousands of thinking tokens by default, but without `think=True` they were silently consumed. The model now surfaces its reasoning and responds visibly faster.
- **Inline predictions unaffected** — inline completions always use `think=False` to stay fast regardless of the thinking mode setting.

## [0.3.0] - 2026-03-19

### Added
- **Automatic backend setup** — the extension now bundles the Python backend source and automatically creates a virtual environment, installs dependencies, and starts the server on first activation. No manual `pip install` or cloning required.
- **Post-install verification** — after installation, core imports (`lean_ai`, `tree_sitter`, `fastapi`, `uvicorn`, `ollama`) are verified. Missing packages surface clear errors with a "Reinstall" option.
- **Optional extras prompt** — on first install, offers to install cloud LLM support (OpenAI, Anthropic) and document indexing (PDF, EPUB, Word) via a guided quick pick.
- **Automatic backend updates** — when the extension is updated, the backend is automatically upgraded to match.
- **Reinstall Backend command** — `Lean AI: Reinstall Backend` resets the virtual environment and reinstalls from scratch.

### Changed
- **Backend resolution** — managed mode (automatic venv in `globalStorageUri`) is now the default. Manual mode is preserved when `lean-ai.backendDir` or `lean-ai.pythonPath` are explicitly set.
- **`.env` file location** — in managed mode, settings are written to `globalStorageUri/.env` instead of requiring a workspace `backend/` directory.

## [0.2.3] - 2026-03-19

### Added
- **Request Model** — configurable separate model for `/request` mode (open-ended tasks, research, documentation). Set via settings panel or `LEAN_AI_OLLAMA_MODEL_REQUEST`.
- **Thinking trace** — collapsible "Thinking..." sections in the chat show the LLM's reasoning process (for models that support it, e.g., Qwen3.5).
- **Request Model settings UI** — new section in the settings panel with provider selection and Ollama model combobox.
- **README** — extension now shows documentation in the extensions panel.
- **LICENSE** — MIT license included in the package.

### Changed
- **Conversation storage** — migrated from VSCode globalState (limited, caused 614KB warning) to disk-backed JSON file via `globalStorageUri`. Existing conversations are auto-migrated on first load.
- **Message limit removed** — conversations now store all messages (was capped at 100 per conversation).
- **fetch_url pagination** — long web pages (500+ lines) are saved to `.lean_ai/fetched/` and the LLM gets the first 500 lines with a `read_file` continuation instruction, instead of lossy LLM summarization.

### Fixed
- **Text-only response handling** — custom nudge for `/request` mode suggests specific tools (search_internet, directory_tree) instead of generic "call a tool now".
- **Truncation detection** — responses truncated by token limits (stop_reason=length) are no longer counted toward the 3-text-only exit limit.
- **stop_reason tracking** — all providers (Ollama, OpenAI, Anthropic) now surface the stop/finish reason in LLMMetrics.
- **TypeScript errors** — fixed 15 pre-existing type errors across 5 extension files.
- **Webview script parse error** — fixed JS syntax errors caused by escape sequence double-interpretation in the TypeScript template literal (apostrophes and newlines in `sidebarHtml.ts`).
- **retainContextWhenHidden** — properly set via `registerWebviewViewProvider` options instead of invalid `webviewView.options` assignment.

## [0.1.5] - 2026-03-19

### Added
- **Request Model settings section** in the VSCode extension settings panel.

## [0.1.4] - 2026-03-18

### Added
- Initial release with chat agent, inline predictions, planning pipeline, fix mode, and multi-provider support.

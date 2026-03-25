# Changelog

All notable changes to the Lean AI extension will be documented in this file.

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

## [0.3.20] - 2026-03-22

### Added
- **Planning phase indicator** — the status badge now shows "planning phase 1" through "planning phase 6" as each planning phase starts, instead of a static "planning" label. Also reflected in the thinking indicator and Chat Participant progress.

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
- **Expert model for /fix mode** — bug-fix workflows now use the expert model (when configured) since diagnosis is reasoning-heavy. Previously only planning phases 3-6 and the final validation retry used the expert model.

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

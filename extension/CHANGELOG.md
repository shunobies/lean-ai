# Changelog

All notable changes to the Lean AI extension will be documented in this file.

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

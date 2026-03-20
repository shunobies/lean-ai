# Changelog

All notable changes to the Lean AI extension will be documented in this file.

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

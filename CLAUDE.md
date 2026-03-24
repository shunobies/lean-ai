# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Lean AI** — an agentic coding assistant that uses a single local LLM (via Ollama) with a simple philosophy: plan well, give the LLM tools, let it work. Python backend with FastAPI (REST + WebSocket), minimal SQLite persistence, and a TypeScript VSCode extension.

Extracted from single_ai — keeps what works (project context generation, decomposed planning, native tool calling, scaffolding, knowledge base), drops what doesn't (11-state FSM, regex-based parsing, ContextWindowManager, stagnation detection, rubric system).

## Build & Run Commands

```bash
# Install backend (from repo root)
cd backend && pip install -e ".[dev]"

# Install with optional knowledge base deps (EPUB, PDF, Word support)
cd backend && pip install -e ".[dev,knowledge]"

# Install with OpenAI provider support
cd backend && pip install -e ".[dev,openai]"

# Install with Anthropic provider support
cd backend && pip install -e ".[dev,anthropic]"

# Install with Google search provider (requires Chrome installed)
cd backend && pip install -e ".[dev,google]"

# Install with voice interaction (STT, TTS, wake word — requires portaudio)
cd backend && pip install -e ".[dev,voice]"

# Run the server
cd backend && uvicorn lean_ai.main:app --reload --port 8422

# Run all tests
cd backend && python -m pytest tests/ -v

# Lint
cd backend && ruff check src/ tests/

# VSCode extension (from repo root)
cd extension && npm install && npm run build
```

## Architecture (Linear Pipeline)

**No FSM.** Three workflow modes: `plan -> approve -> execute -> done` (default), `fix -> done` (skip planning, bug-fix prompt), and `request -> done` (skip planning, neutral prompt for open-ended tasks like writing guides). All modes have access to internet search tools (`search_internet`, `fetch_url`).

1. **LLM Client** (`llm/`) — Multi-provider LLM abstraction. `LLMProvider` ABC (`base.py`) with implementations for Ollama (`client.py`), OpenAI (`provider_openai.py`), and Anthropic (`provider_anthropic.py`). `LLMClient` facade (`facade.py`) handles the multi-turn `chat_with_tools()` orchestration loop, delegates single-turn calls to the active provider. Optional shared `concurrency_semaphore` in `chat_raw()` throttles all concurrent LLM calls — set via `LEAN_AI_NUM_PARALLEL` to match `OLLAMA_NUM_PARALLEL`. Inline predictions (FIM) and embeddings always use Ollama. Context refresh at 70% threshold — drops old messages, re-reads context files from disk, injects scratchpad for continuity (no LLM summarization call). `chat_raw()` and `chat_structured()` accept optional `stream_callback` / `thinking_callback` for token-level streaming — used by the planner to stream progress during planning phases.

2. **Planning** (`llm/planner.py`) — 6-phase decomposed planning: scope → file identification (with tool-assisted codebase exploration) → change design (with naming convention extraction) → risk check → plan assembly → verification step generation. Supports dual-model routing: phases 1-2 use the standard (fast) model for scope and codebase exploration, phases 3-6 use the expert model (if configured) for change design, risk assessment, plan assembly, and verification. Phase 2 uses read-only tools (`read_file`, `grep_files`, `list_directory`, `directory_tree`) to trace all downstream consumers of modified entities and detect missing infrastructure. Phase 6 reviews the complete implementation plan and appends test file creation steps + a final `run_tests` step (only runs when a test command is available). In TDD mode, Phase 6 stores test steps separately in `plan.tdd_test_steps` (filtering out `run_tests`), and the prompt is enhanced to require comprehensive test documentation (module docstrings, per-test docstrings, descriptive assertion messages). The plan schema's `plan_to_markdown()` renders TDD plans with "TEST PHASE (Expert Model)" and "IMPLEMENTATION PHASE (Primary Model)" headers for user approval. Structured JSON output from Ollama. Plan template with worked examples (`llm/plan_template.md`). **Context flow across phases:** Phases 1-2 (primary model) receive the full `context` parameter (project_context.md + framework_guide.md + custom steering docs). Phases 3 and 5 (expert model) receive `project_context.md` directly (loaded from disk) so the expert has project architecture for design decisions. Phases 4 and 6 work from prior phase outputs only. All phases (3-4) include anti-hallucination instructions — the LLM must not simulate running commands, invent file listings, or fabricate file contents. Phase 3's naming convention extraction requires citing source filenames and falls back to "standard framework conventions" when the existing codebase has insufficient examples. **Streaming planning output:** all phases stream thinking and content tokens to the extension via callbacks — phases 1, 3, 4 stream both thinking and content tokens; phase 2 streams tool call/result progress + per-turn content; phases 5, 6 stream thinking tokens only (content is JSON). The pipeline creates planning-specific callbacks with a `streaming: True` flag to distinguish token-level updates from per-turn bulk content.

3. **Tools** (`tools/`) — `create_file`, `edit_file`, `read_file`, `run_tests`, `run_lint`, `format_code`, `list_directory`, `directory_tree`, `grep_files`, `update_scratchpad`, `search_internet`, `fetch_url`, `request_test_change` (TDD only). File ops produce diffs. Shell commands pass through a safety gate (`command_safety.py`). Internet search + URL fetching with HTML strip + LLM summary sanitization — available in all execution modes so the LLM can look up error messages and documentation when stuck. Test file detection utility (`tools/test_file_utils.py`) wraps the language registry's test patterns for TDD enforcement.

4. **Workflow** (`workflow/pipeline.py`) — Two modes: `plan` (clarify → plan → approve → execute) and `fix` (skip planning, direct tool execution). After execution (both modes), `_run_post_validation` runs deterministic lint/test/format passes — auto-fix passes (format, lint-fix) run sequentially since they modify files, then lint and test reporting passes run in parallel via `asyncio.gather`. On failure, `_run_validation_fix_loop` retries up to `post_validation_max_retries` times — each attempt gives the LLM a 30-turn budget with a structured verify-first workflow (re-run failing command → diagnose → fix → re-run to confirm). The expert model takes over on the final retry if configured. WebSocket-based progress streaming. No state machine library. Work branches always created from default branch (master/main). **TDD mode** (`workflow/tdd.py`): when `LEAN_AI_ENABLE_TDD` is enabled, plan execution runs three phases — (A) expert model writes all tests first from Phase 6 plan steps, (B) primary model reviews tests and can dispute upfront via `request_test_change`, (C) primary model implements code with test files protected (writes blocked, disputes routed to expert). The dispute mechanism (`evaluate_test_dispute`) runs a tight 10-turn expert session that either accepts (edits the test) or rejects (explains why the test is correct and suggests an implementation approach). Disputes are capped at `tdd_max_disputes_per_step` per step. The TDD test-file guard and dispute mechanism also apply in the post-validation fix loop.

5. **Persistence** (`db.py`) — Minimal SQLite via `aiosqlite`. Two tables: `sessions` and `tool_logs`. No ORM.

6. **Indexer** (`indexer/`) — Gitignore-aware tree listing. Tree-sitter AST-aware code chunking. Whoosh BM25F search. Embedding store with RRF re-ranking. SHA-256 manifest for incremental updates.

7. **Context Generation** (`context/`) — Generates `.lean_ai/project_context.md` via single-pass or multi-round LLM calls. Expansion batches fire concurrently via `asyncio.gather` using additions-only prompts (each batch receives section headings, returns only new entries, results merge programmatically). Tree-sitter metadata extraction with disk cache. Auto-scaling size caps proportional to context window. Optional framework guide generation (`.lean_ai/framework_guide.md`) — detects frameworks from dependency files, web-searches for best practices, and LLM-generates an architecture/conventions guide covering component relationships, CLI commands, and patterns. The extension runs project context and framework guide generation in parallel via `Promise.allSettled`. All concurrent LLM calls are throttled by the shared `chat_raw` semaphore (`LEAN_AI_NUM_PARALLEL`). Custom steering documents in `.lean_ai/context/` are loaded after the generated files (alphabetically) to provide additional project-specific guidance. Auto-detects lint/test/format commands from project dependency files (`command_detection.py`) during `/init-workspace` — saves to `.lean_ai/commands.json`, used as fallback when manual `LEAN_AI_POST_*` env vars are empty. Covers PHP, Python, Node/TS, Ruby, Go, Rust, Java, C#.

8. **Language Registry** (`languages/`) — 13 language definitions in YAML. Tree-sitter AST parsing (no regex patterns). Generic extraction engine for classes, functions, imports.

9. **Knowledge Base** (`knowledge/`) — Domain document indexing (EPUB, PDF, Word, Markdown, HTML, text). Prose-aware paragraph chunker. Separate Whoosh index. Incremental updates via SHA-256 manifest.

10. **Scaffolding** (`scaffolds/`) — 19 YAML scaffold recipes for project bootstrapping.

11. **Voice** (`voice/`) — Optional voice interaction: Speech-to-Text (faster-whisper), Text-to-Speech (kokoro-onnx), and wake word detection (openWakeWord). All voice services run on CPU only (GPU is reserved for the LLM). `AudioManager` singleton coordinates mic access — only one service (STT or wake word) captures at a time. Backend captures the mic directly via PyAudio (avoids VSCode webview audio restrictions). Extension UI has inline mic button, voice/speed controls, and TTS playback via HTML5 Audio with queuing. All deps in `voice` optional extras group. REST endpoints in `routers/voice.py`; SSE for wake word events and TTS streaming. TTS model files (~169MB fp16 default) auto-downloaded to `~/.cache/lean_ai/kokoro/` on first use. ALSA errors on Linux are suppressed via `alsa_suppression.py`.

## Key Design Decisions

- **No regex for source code analysis** — all extraction uses tree-sitter AST queries
- **No ContextWindowManager** — Ollama manages its own KV cache; we focus on prompt quality
- **No rubric system** — user approval is the sole quality gate
- **No stagnation detection** — trust the LLM to complete its work
- **No implementation review loop** — plan -> execute -> done
- **Tool naming**: `create_file` (not `write_file`) for clearer intent
- **Structured JSON output** from Ollama replaces regex-based plan/output parsing
- **Percentage-based token budgets** — internal limits (scratchpad, inline output, etc.) are computed as a percentage of the active context window, not hardcoded. This makes the system adaptive: smaller models get proportionally smaller budgets, larger models get more room. Convention: use `settings._active_context_window` and a named percentage constant (e.g. `SCRATCHPAD_CONTEXT_PERCENT = 0.05`)
- **Three-model pipeline** — **request model** (chatty, higher temperature) for the chat conversation and requirements gathering; **primary model** (tuned for coding) for planning phases 1-2 (codebase exploration) and implementation execution; **expert model** (large, reasoning-heavy) for planning phases 3-6 and the final validation fix retry (escalation only on last attempt). Any model falls back to the primary when not configured. All three can use any provider (Ollama, OpenAI, or Anthropic) independently — set `LEAN_AI_REQUEST_LLM_PROVIDER` / `LEAN_AI_EXPERT_LLM_PROVIDER` to select. Phases communicate through structured text/JSON outputs, not shared conversation history, making model switching seamless. The expert receives `project_context.md` directly in phases 3 and 5 for architectural awareness; framework_guide.md and style.md are deferred to the implementation phase.

## Technology Stack

| Concern | Library |
|---|---|
| Web framework | FastAPI (async, built-in WebSocket) |
| Database | aiosqlite (raw SQL, 2 tables) |
| Ollama SDK | ollama (official, async) |
| Search index | Whoosh |
| Source analysis | tree-sitter + 13 grammar packages |
| Internet search | duckduckgo-search, Selenium (optional Google/Bing provider with automatic fallback) |
| Voice STT | faster-whisper (CTranslate2-based Whisper) |
| Voice TTS | kokoro-onnx (ONNX Runtime, 58 voices, 24kHz PCM) |
| Wake word | openWakeWord (pre-trained `hey_computer` model) |
| Audio capture | PyAudio (requires portaudio system library) |
| HTML sanitization | BeautifulSoup4 |
| Testing | pytest + pytest-asyncio |
| Linting | ruff |
| VSCode extension | Chat Participant API + InlineCompletionItemProvider |

## Configuration (Environment Variables)

All settings use the `LEAN_AI_` prefix, or via `backend/.env`. Defined in `backend/src/lean_ai/config.py`.

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_LLM_PROVIDER` | `ollama` | LLM provider: `ollama`, `openai`, or `anthropic` |
| `LEAN_AI_OLLAMA_URL` | `http://localhost:11434` | Ollama API endpoint |
| `LEAN_AI_OLLAMA_MODEL` | `qwen3-coder:30b` | Primary model (when provider=ollama) |
| `LEAN_AI_OLLAMA_TEMPERATURE` | `0.7` | Sampling temperature (Qwen3 recommends 0.7) |
| `LEAN_AI_OLLAMA_TOP_P` | `0.8` | Nucleus sampling threshold |
| `LEAN_AI_OLLAMA_TOP_K` | `20` | Top-k sampling |
| `LEAN_AI_OLLAMA_REPEAT_PENALTY` | `1.05` | Repetition penalty |
| `LEAN_AI_OLLAMA_CONTEXT_WINDOW` | `131072` | Context window — accepts shorthand: `128` = 128k = 131072 |
| `LEAN_AI_OLLAMA_MAX_TOKENS` | *(derived: 25% of context window)* | Max output tokens |
| `LEAN_AI_OLLAMA_MODEL_EXPERT` | *(empty)* | Expert model for reasoning-heavy phases (Ollama only) |
| `LEAN_AI_OLLAMA_EXPERT_TEMPERATURE` | *(falls back to OLLAMA_TEMPERATURE)* | Expert model temperature |
| `LEAN_AI_OLLAMA_EXPERT_TOP_P` | *(falls back to OLLAMA_TOP_P)* | Expert model top-p |
| `LEAN_AI_OLLAMA_EXPERT_TOP_K` | *(falls back to OLLAMA_TOP_K)* | Expert model top-k |
| `LEAN_AI_OLLAMA_EXPERT_REPEAT_PENALTY` | *(falls back to OLLAMA_REPEAT_PENALTY)* | Expert model repetition penalty |
| `LEAN_AI_OLLAMA_EXPERT_CONTEXT_WINDOW` | *(falls back to OLLAMA_CONTEXT_WINDOW)* | Expert model context window (accepts shorthand) |
| `LEAN_AI_OLLAMA_EXPERT_MAX_TOKENS` | *(derived: 25% of expert context window)* | Expert model max output tokens |
| `LEAN_AI_EXPERT_LLM_PROVIDER` | *(empty)* | Provider for expert model: `ollama`, `openai`, or `anthropic`. Empty = auto-detect from `OLLAMA_MODEL_EXPERT` |
| `LEAN_AI_OPENAI_EXPERT_MODEL` | *(falls back to OPENAI_MODEL)* | OpenAI model for expert phases (e.g. `gpt-4o`) |
| `LEAN_AI_ANTHROPIC_EXPERT_MODEL` | *(falls back to ANTHROPIC_MODEL)* | Anthropic model for expert phases (e.g. `claude-opus-4-6`) |
| `LEAN_AI_REQUEST_LLM_PROVIDER` | *(empty)* | Provider for `/request` mode: `ollama`, `openai`, or `anthropic`. Empty = auto-detect |
| `LEAN_AI_OLLAMA_MODEL_REQUEST` | *(empty)* | Ollama model for `/request` mode (e.g. `qwen3.5:27b`). Empty = use primary model |
| `LEAN_AI_OLLAMA_REQUEST_TEMPERATURE` | *(falls back to OLLAMA_TEMPERATURE)* | Request model temperature |
| `LEAN_AI_OLLAMA_REQUEST_TOP_P` | *(falls back to OLLAMA_TOP_P)* | Request model top-p |
| `LEAN_AI_OLLAMA_REQUEST_TOP_K` | *(falls back to OLLAMA_TOP_K)* | Request model top-k |
| `LEAN_AI_OLLAMA_REQUEST_REPEAT_PENALTY` | *(falls back to OLLAMA_REPEAT_PENALTY)* | Request model repetition penalty |
| `LEAN_AI_OLLAMA_REQUEST_CONTEXT_WINDOW` | *(falls back to OLLAMA_CONTEXT_WINDOW)* | Request model context window (accepts shorthand) |
| `LEAN_AI_OLLAMA_REQUEST_MAX_TOKENS` | *(derived: 25% of request context window)* | Request model max output tokens |
| `LEAN_AI_OPENAI_REQUEST_MODEL` | *(empty)* | OpenAI model for `/request` mode |
| `LEAN_AI_ANTHROPIC_REQUEST_MODEL` | *(empty)* | Anthropic model for `/request` mode |
| `LEAN_AI_OPENAI_API_KEY` | *(empty)* | OpenAI API key (required when provider=openai) |
| `LEAN_AI_OPENAI_MODEL` | `gpt-4o` | OpenAI model name |
| `LEAN_AI_OPENAI_BASE_URL` | *(empty)* | Custom base URL for OpenAI-compatible APIs (Together, Groq, vLLM) |
| `LEAN_AI_OPENAI_TEMPERATURE` | `0.7` | OpenAI sampling temperature |
| `LEAN_AI_OPENAI_CONTEXT_WINDOW` | `128000` | OpenAI context window |
| `LEAN_AI_OPENAI_MAX_TOKENS` | *(derived: 25% of context window)* | OpenAI max output tokens |
| `LEAN_AI_ANTHROPIC_API_KEY` | *(empty)* | Anthropic API key (required when provider=anthropic) |
| `LEAN_AI_ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Anthropic model name |
| `LEAN_AI_ANTHROPIC_TEMPERATURE` | `0.7` | Anthropic sampling temperature |
| `LEAN_AI_ANTHROPIC_CONTEXT_WINDOW` | `200000` | Anthropic context window |
| `LEAN_AI_ANTHROPIC_MAX_TOKENS` | *(derived: 25% of context window)* | Anthropic max output tokens |
| `LEAN_AI_INLINE_MODEL` | *(empty)* | Separate model for inline predictions (always Ollama) |
| `LEAN_AI_INLINE_OLLAMA_URL` | *(falls back to OLLAMA_URL)* | Ollama instance for inline model |
| `LEAN_AI_EMBEDDING_MODEL` | `qwen3-embedding:0.6b` | Embedding model for semantic search (always Ollama) |
| `LEAN_AI_ENABLE_EMBEDDINGS` | `true` | Enable embedding generation + RRF hybrid search |
| `LEAN_AI_VISION_MODEL` | *(empty)* | Vision model for describing images (e.g. `qwen3-vl:8b`). Empty = vision disabled. Always Ollama |
| `LEAN_AI_VISION_OLLAMA_URL` | *(falls back to OLLAMA_URL)* | Ollama instance for vision model |
| `LEAN_AI_VISION_MAX_TOKENS` | `1024` | Max tokens for image description |
| `LEAN_AI_VISION_TIMEOUT` | `60.0` | Timeout per image description (seconds) |
| `LEAN_AI_ENABLE_STT` | `false` | Enable Speech-to-Text (faster-whisper). Requires voice extras + portaudio |
| `LEAN_AI_STT_MODEL` | `turbo` | Whisper model size: `tiny`, `base`, `small`, `medium`, `large-v3`, `turbo` |
| `LEAN_AI_STT_LANGUAGE` | *(empty)* | ISO 639-1 language code for STT. Empty = auto-detect |
| `LEAN_AI_STT_SILENCE_THRESHOLD` | `4.0` | Seconds of silence before auto-stopping recording |
| `LEAN_AI_STT_BEAM_SIZE` | `1` | Whisper beam size: 1=greedy (fastest), 5=beam search (most accurate) |
| `LEAN_AI_STT_CPU_THREADS` | `6` | CPU threads for faster-whisper model inference |
| `LEAN_AI_ENABLE_TTS` | `false` | Enable Text-to-Speech (kokoro-onnx). Requires voice extras. Default fp16 model ~169MB, auto-downloaded on first use |
| `LEAN_AI_TTS_VOICE` | `af_heart` | kokoro-onnx voice ID (e.g. `af_heart`, `am_adam`, `bf_emma`) |
| `LEAN_AI_TTS_SPEED` | `1.0` | TTS playback speed (0.5–2.0) |
| `LEAN_AI_TTS_MODEL_QUALITY` | `fp16` | ONNX model variant: `fp32` (~311MB), `fp16` (~169MB, 2x faster), `int8` (~88MB) |
| `LEAN_AI_ENABLE_WAKE_WORD` | `false` | Enable "Hey Computer" wake word detection (openWakeWord) |
| `LEAN_AI_INDEX_DIR` | `.lean_ai_index` | Whoosh index directory name |
| `LEAN_AI_SEARCH_PROVIDER` | `duckduckgo` | Search provider (`duckduckgo`, `searxng`, `google`, or `bing`). Google auto-falls back to Bing |
| `LEAN_AI_SEARCH_DELAY` | `2.0` | Min seconds between searches (all providers, with random jitter) |
| `LEAN_AI_KNOWLEDGE_DIR` | `.lean_ai/knowledge` | Knowledge documents directory |
| `LEAN_AI_ENABLE_FRAMEWORK_GUIDE` | `true` | Generate `.lean_ai/framework_guide.md` for detected frameworks |
| `LEAN_AI_NUM_PARALLEL` | `1` | Max concurrent LLM requests — set to match `OLLAMA_NUM_PARALLEL`. Controls parallelism for `/init` expansion batches, context+guide generation, and framework guide internals. `1` = fully sequential |
| `LEAN_AI_IMPLEMENTATION_MAX_TURNS` | `0` | Max tool-calling turns per session (`0` = unlimited) |
| `LEAN_AI_IMPLEMENTATION_MAX_TOKENS` | *(derived: 25% of context window)* | Max tokens per LLM turn |
| `LEAN_AI_REFRESH_THRESHOLD` | `0.7` | Refresh context at this % of context window |
| `LEAN_AI_ENABLE_FIX_INVESTIGATION` | `true` | Enforce read-only investigation phase in /fix mode before editing |
| `LEAN_AI_ENABLE_TDD` | `false` | TDD mode: expert writes tests first, primary implements. Requires expert model |
| `LEAN_AI_TDD_MAX_DISPUTES_PER_STEP` | `3` | Max test disputes per implementation step in TDD mode |
| `LEAN_AI_ENABLE_POST_VALIDATION` | `true` | Run deterministic lint/test after execution |
| `LEAN_AI_POST_FORMAT_COMMAND` | *(empty)* | Auto-fix formatting (e.g. `ruff format src/`) |
| `LEAN_AI_POST_LINT_FIX_COMMAND` | *(empty)* | Auto-fix lint issues (e.g. `ruff check --fix src/`) |
| `LEAN_AI_POST_LINT_COMMAND` | *(empty)* | Lint check (e.g. `ruff check src/`) |
| `LEAN_AI_POST_TEST_COMMAND` | *(empty)* | Test check (e.g. `pytest tests/ -x -q`) |
| `LEAN_AI_POST_VALIDATION_MAX_RETRIES` | `2` | Max LLM fix attempts for validation failures (`0` = no retries) |
| `LEAN_AI_POST_VALIDATION_FIX_TURNS` | `30` | Tool-calling turns per fix attempt |
| `LEAN_AI_ENABLE_THINKING` | `true` | Pass `think=True` to Ollama for reasoning models (Qwen3, Qwen3.5). Disable for faster responses without deep reasoning |
| `LEAN_AI_ENABLE_THINKING_EXPERT` | `true` | Enable thinking mode for expert model |
| `LEAN_AI_ENABLE_THINKING_REQUEST` | `true` | Enable thinking mode for request model |
| `LEAN_AI_DEBUG_PLANNING` | `false` | Save all planning phase outputs to `.lean_ai/plan_debug/{session_id}/` |
| `LEAN_AI_PORT` | `8422` | Server port |

**Post-validation auto-detection:** When `LEAN_AI_POST_*_COMMAND` variables are empty, the system falls back to commands auto-detected during `/init-workspace` (stored in `.lean_ai/commands.json`). Manual env vars always take priority. In fix mode, the LLM is instructed to write tests alongside code changes when a test command is available. In plan mode, test creation is handled by Phase 6 (verification step generation) which appends test file steps and a final `run_tests` step after all implementation steps. In TDD mode, Phase 6 produces test steps separately into `tdd_test_steps` (without `run_tests`) — these are executed first by the expert model, then the primary implements code with test files protected. **Validation fix loop:** when `_run_post_validation` detects failures, `_run_validation_fix_loop` retries up to `LEAN_AI_POST_VALIDATION_MAX_RETRIES` times. Each attempt uses a **hardcoded 30-turn budget** (independent of `LEAN_AI_IMPLEMENTATION_MAX_TURNS`) and instructs the LLM to: (1) re-run the failing command to confirm the error, (2) read relevant files to find the root cause, (3) record diagnosis in scratchpad, (4) make the minimal fix, (5) re-run to verify. On the **final retry**, the expert model is used if configured. In TDD mode, the fix loop also enforces the test-file guard and provides the `request_test_change` dispute tool so the primary model can escalate flawed tests to the expert rather than editing them directly.

## WebSocket Protocol

Client → server: `user_message` (start workflow or mid-workflow interrupt), `cancel` (stop running workflow), `approve` (approve plan), `approve_tool` / `deny_tool` (shell command gate), `ping`, `resume`.

Server → client: `token`, `stage_change`, `approval_required`, `tool_progress`, `tool_approval_required`, `diff`, `test_result`, `error`, `complete`, `cancelled`, `index_status`, `stage_status`, `clarification_needed`, `plan_rejected`, `pong`, `branch_created`, `checkpoint`, `merge_complete`, `context_refreshed`, `assistant_content`, `thinking_content`, `metrics_update`.

`assistant_content` and `thinking_content` support optional `streaming` (boolean, token-level updates during planning) and `done` (boolean, signals content finalization with full text for markdown formatting) fields.

**Workflow cancellation:** A `WSMessageDispatcher` (`workflow/ws_dispatcher.py`) runs a background listener on the WebSocket during workflow execution, routing messages to typed async queues. This enables receiving `cancel` and `user_message` (interrupt) messages while the pipeline is actively running. The dispatcher has two routing modes: during clarification/approval phases, `user_message` goes to the approval queue (responses); after `enter_execution_mode()`, they go to the interrupt queue (consumed by `chat_with_tools` between turns). Cancellation raises `WorkflowCancelledError`, caught in `routers/workflow.py` which sends `{"type": "cancelled"}` back to the client.

## API Endpoints

All under `/api` prefix:

- `POST /sessions` — create session
- `WS /sessions/{id}/stream` — WebSocket for workflow execution
- `GET /sessions` — list sessions
- `GET /sessions/{id}` — session detail
- `POST /init-workspace` — index workspace + generate project context
- `POST /generate-project-context` — regenerate context
- `POST /generate-framework-guide` — regenerate framework guide
- `POST /index-knowledge` — index knowledge docs
- `POST /chat` — chat endpoint (multi-turn conversation that gathers requirements, handles non-technical users by filling gaps with best practices, and produces a refined "Suggested Agent Prompt" for the planning pipeline). Uses the request model when configured; falls back to the primary model.
- `POST /predict` — inline predictions
- `POST /scaffold/list` — list scaffold recipes
- `POST /scaffold` — create project from scaffold
- `GET /health` — health check (includes voice availability)
- `POST /voice/stt/start` — start mic recording
- `POST /voice/stt/stop` — stop recording, return transcribed text
- `POST /voice/tts` — synthesize text to base64 WAV audio
- `POST /voice/tts/stream` — SSE: stream audio chunks for long text
- `GET /voice/tts/voices` — list available TTS voices
- `POST /voice/config` — update voice/speed at runtime
- `POST /voice/wakeword/start` — start wake word listener
- `POST /voice/wakeword/stop` — stop wake word listener
- `GET /voice/events` — SSE: wake word detection events
- `GET /voice/status` — voice feature availability + setup instructions

## LLM Prompt Authoring Standard

**Never assign a persona to the LLM in system prompts.** Use capability-first framing:
```
# Bad
"You are a senior software architect..."

# Good
"Use your knowledge of software architecture to..."
```

## Commit After Every Change

Always commit after completing a change without waiting to be asked. Each logical change gets its own commit.

## No Stubs Rule

Never create stubs, placeholder implementations, or skeleton code that is not fully functional. If a feature cannot be completed, document what is missing in `incomplete.md` and move on.

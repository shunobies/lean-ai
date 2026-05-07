# Lean AI — Complete System Specification

> **Purpose**: This document specifies every subsystem, API contract, data schema, workflow, and design decision in the Lean AI project with enough detail for an LLM to rebuild the entire application from scratch.

---

## Table of Contents

1. [Introduction & System Overview](#1-introduction--system-overview)
2. [Configuration System](#2-configuration-system)
3. [Database Schema & Persistence](#3-database-schema--persistence)
4. [LLM Abstraction Layer](#4-llm-abstraction-layer)
5. [Multi-Turn Tool Orchestration](#5-multi-turn-tool-orchestration)
6. [Three-Model Pipeline](#6-three-model-pipeline)
7. [Planning System (5-Phase)](#7-planning-system-5-phase)
8. [Workflow Modes](#8-workflow-modes)
9. [Plan Execution](#9-plan-execution)
10. [Post-Validation System](#10-post-validation-system)
11. [WebSocket Dispatcher](#11-websocket-dispatcher)
12. [Tool System](#12-tool-system)
13. [Router Layer & API Endpoints](#13-router-layer--api-endpoints)
14. [WebSocket Protocol](#14-websocket-protocol)
15. [Indexer System](#15-indexer-system)
16. [Context Generation](#16-context-generation)
17. [Language Registry](#17-language-registry)
18. [Reference Library](#18-reference-library)
19. [Scaffolding System](#19-scaffolding-system)
20. [Voice System](#20-voice-system)
21. [Prompt Library](#21-prompt-library)
22. [VS Code Extension](#22-vs-code-extension)
23. [Cross-Cutting Concerns](#23-cross-cutting-concerns)
24. [Appendices](#24-appendices)

---

## 1. Introduction & System Overview

### 1.1 Purpose and Philosophy

Lean AI is an agentic coding assistant that uses a single local LLM (via Ollama, or cloud providers OpenAI/Anthropic) with a simple philosophy: **plan well, give the LLM tools, let it work**.

Core design tenets:
- **No FSM** — linear pipeline, not a state machine
- **No rubric system** — user approval is the sole quality gate
- **No complex FSM or rubric-driven self-critique** — only lightweight guardrails for tool progress and recovery
- **No regex for source code analysis** — all extraction uses tree-sitter AST queries
- **No ContextWindowManager** — Ollama manages its own KV cache; we focus on prompt quality
- **No persona in system prompts** — use capability-first framing ("Use your knowledge of..." not "You are a...")
- **Percentage-based token budgets** — internal limits computed as a percentage of the active context window, not hardcoded

### 1.2 Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    VS Code Extension (TypeScript)                │
│                                                                  │
│  ┌──────────┐  ┌──────────┐  ┌────────┐  ┌──────────────────┐  │
│  │ Sidebar  │  │ Inline   │  │Session │  │ Settings Panel   │  │
│  │ Webview  │  │ Provider │  │TreeView│  │ (Secret Storage) │  │
│  └────┬─────┘  └────┬─────┘  └───┬────┘  └──────────────────┘  │
│       │              │            │                               │
│       │ WebSocket    │ REST       │ REST                         │
└───────┼──────────────┼────────────┼──────────────────────────────┘
        │              │            │
        ▼              ▼            ▼
┌─────────────────────────────────────────────────────────────────┐
│                  FastAPI Backend (Python, async)                  │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                    Router Layer (/api)                     │   │
│  │  Sessions │ Workflow(WS) │ Chat │ Voice │ Generation │ …  │   │
│  └──────────────────────┬───────────────────────────────────┘   │
│                         │                                        │
│  ┌──────────────────────▼───────────────────────────────────┐   │
│  │              Workflow Engine (pipeline.py)                 │   │
│  │  clarify → plan(5-phase) → approve → execute → validate  │   │
│  └──────────────────────┬───────────────────────────────────┘   │
│                         │                                        │
│  ┌──────────────────────▼───────────────────────────────────┐   │
│  │                LLM Client Facade                          │   │
│  │  chat_with_tools() loop │ chat_raw │ chat_structured      │   │
│  └──────┬──────────────┬───────────────────┬────────────────┘   │
│         │              │                   │                     │
│  ┌──────▼──┐    ┌──────▼──────┐    ┌──────▼────────┐           │
│  │ Ollama  │    │   OpenAI    │    │  Anthropic    │           │
│  │Provider │    │  Provider   │    │  Provider     │           │
│  └─────────┘    └─────────────┘    └───────────────┘           │
│                                                                  │
│  ┌───────────┐ ┌───────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │  Indexer  │ │  Context  │ │Reference │ │     Voice        │ │
│  │(Whoosh+  │ │Generation │ │  Library │ │(STT/TTS/WakeWord)│ │
│  │Embedding)│ │           │ │          │ │                  │ │
│  └───────────┘ └───────────┘ └──────────┘ └──────────────────┘ │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │               SQLite (aiosqlite, 4 tables)                │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 1.3 Technology Stack

| Concern | Library | Version Constraint |
|---|---|---|
| Web framework | FastAPI (async, built-in WebSocket) | >=0.115.0,<1.0 |
| ASGI server | uvicorn[standard] | >=0.34.0,<1.0 |
| Database | aiosqlite (raw SQL, no ORM) | >=0.20.0,<1.0 |
| Validation | Pydantic + pydantic-settings | >=2.10.0,<3.0 |
| Ollama SDK | ollama (official, async) | >=0.4.0,<1.0 |
| OpenAI SDK | openai (optional) | >=1.30.0,<2.0 |
| Anthropic SDK | anthropic (optional) | >=0.30.0,<1.0 |
| Search index | Whoosh (BM25F) | >=2.7.4,<3.0 |
| Source analysis | tree-sitter + 13 grammar packages | >=0.24.0,<1.0 |
| Internet search | duckduckgo-search | >=7.0.0,<8.0 |
| Browser search | Selenium (optional, Google/Bing) | >=4.25.0,<5.0 |
| Voice STT | faster-whisper (optional) | >=1.2.1 |
| Voice TTS | kokoro-onnx (optional) | >=0.4.0 |
| Wake word | openwakeword (optional) | latest |
| Audio capture | PyAudio (optional, requires portaudio) | latest |
| Audio output | soundfile (optional) | latest |
| HTML sanitization | BeautifulSoup4 | >=4.12.0,<5.0 |
| HTTP client | httpx | >=0.28.0,<1.0 |
| YAML parsing | ruamel.yaml | >=0.18.0,<0.19 |
| Gitignore patterns | pathspec | >=0.12.0,<2.0 |
| WebSocket client | websockets | >=13.0,<17.0 |
| Reference: EPUB | ebooklib (optional) | >=0.18,<1.0 |
| Reference: PDF | pypdf (optional) | >=6.7.5,<7.0 |
| Reference: Word | python-docx (optional) | >=1.1.0,<2.0 |
| Testing | pytest + pytest-asyncio | >=8.0.0,<10.0 |
| Linting | ruff | >=0.8.0,<1.0 |
| Build system | hatchling | latest |

**Optional dependency groups** (installed via `pip install -e ".[group]"`):
- `openai` — OpenAI provider support
- `anthropic` — Anthropic provider support
- `reference` — EPUB, PDF, Word document support
- `google` — Selenium-based Google/Bing search with automatic fallback
- `voice` — STT, TTS, wake word detection (requires portaudio system library)
- `dev` — pytest, ruff

### 1.4 Project Directory Structure

```
lean_ai/
├── backend/
│   ├── pyproject.toml                    # Package config, dependencies, extras groups
│   ├── src/lean_ai/
│   │   ├── __init__.py
│   │   ├── main.py                       # FastAPI app entry point, lifespan, CORS, router mount
│   │   ├── config.py                     # Settings class (100+ env vars), validators, derived fields
│   │   ├── db.py                         # SQLite schema (4 tables), all CRUD operations
│   │   ├── router.py                     # Router aggregator — includes all sub-routers at /api
│   │   │
│   │   ├── routers/                      # HTTP/WebSocket endpoint handlers
│   │   │   ├── sessions.py              # Session CRUD, search, resume
│   │   │   ├── workflow.py              # WebSocket workflow streaming, merge, abandon
│   │   │   ├── generation.py            # /init-workspace, context/guide/style generation
│   │   │   ├── chat.py                  # Multi-turn chat, streaming SSE, inline predictions
│   │   │   ├── info.py                  # /health, /models, /predict
│   │   │   ├── voice.py                 # STT, TTS, wake word REST + SSE endpoints
│   │   │   ├── scaffold_endpoints.py    # Scaffold list and creation
│   │   │   ├── reference_endpoints.py   # Reference library indexing
│   │   │   ├── models.py               # All Pydantic request/response models
│   │   │   ├── dependencies.py          # Provider factory, singleton LLM client creation
│   │   │   └── context_helpers.py       # Context loading, file tree, search, URL extraction
│   │   │
│   │   ├── llm/                          # LLM abstraction layer
│   │   │   ├── base.py                  # LLMProvider ABC, ToolCall/ToolCallInfo/LLMMetrics types
│   │   │   ├── client.py               # OllamaProvider — chat, structured output, tools, FIM, embeddings
│   │   │   ├── provider_openai.py       # OpenAIProvider — chat, structured output, tools
│   │   │   ├── provider_anthropic.py    # AnthropicProvider — chat, structured output, tools
│   │   │   ├── facade.py               # LLMClient facade — chat_with_tools orchestration loop
│   │   │   ├── planner.py              # 5-phase decomposed planning system
│   │   │   ├── plan_schema.py          # PlanStep, ExecutionPlan, VerificationPlan Pydantic models
│   │   │   ├── prompts.py              # Planning phase system prompts
│   │   │   ├── tool_definitions.py     # Tool JSON schemas for LLM function calling
│   │   │   ├── refiner.py              # PromptRefiner — local Ollama pre-processing for cloud
│   │   │   └── vision.py               # Vision model image description
│   │   │
│   │   ├── workflow/                     # Workflow orchestration
│   │   │   ├── pipeline.py             # Plan mode: clarify → plan → approve → execute → validate
│   │   │   ├── fix_mode.py             # Fix/request mode: investigate → implement → validate
│   │   │   ├── validation.py           # Post-validation: lint/test/format + fix loop
│   │   │   ├── tdd.py                  # TDD mode: expert tests → primary disputes → implements
│   │   │   ├── tool_executor.py        # Tool executor factory with TDD protection
│   │   │   ├── ws_dispatcher.py        # WebSocket message router (approval vs interrupt queues)
│   │   │   ├── ws_handler.py           # WebSocket message sending helpers
│   │   │   └── prompts.py              # Workflow system prompts (fix, request, step execution)
│   │   │
│   │   ├── tools/                        # Tool implementations
│   │   │   ├── file_ops.py             # create_file, edit_file, read_file with path safety
│   │   │   ├── executor.py             # Shell command execution with timeout
│   │   │   ├── shell.py                # Shell command helpers
│   │   │   ├── command_safety.py       # Command risk classification (SAFE/APPROVAL/BLOCK)
│   │   │   ├── git_ops.py             # Git operations (branch, commit, merge, stash, etc.)
│   │   │   ├── internet.py            # search_internet + fetch_url implementations
│   │   │   ├── browser_search.py      # Selenium-based Google/Bing search
│   │   │   ├── scratchpad.py          # Per-session scratchpad read/write
│   │   │   ├── scaffold.py            # Scaffold creation from YAML recipes
│   │   │   └── test_file_utils.py     # Test file detection for TDD enforcement
│   │   │
│   │   ├── indexer/                      # Code search indexing
│   │   │   ├── indexer.py              # Whoosh BM25F index, full + incremental
│   │   │   ├── chunker.py             # Tree-sitter AST-aware code chunking
│   │   │   ├── embeddings.py          # Binary embedding store + RRF hybrid search
│   │   │   ├── tree.py                # Gitignore-aware directory tree walker
│   │   │   └── manifest.py            # SHA-256 manifest for incremental updates
│   │   │
│   │   ├── context/                      # Project context generation
│   │   │   ├── generation.py           # LLM-based project_context.md generation
│   │   │   ├── content.py             # File collection, priority ranking, prompt building
│   │   │   ├── constants.py           # System prompts, size caps, templates
│   │   │   ├── metadata.py            # Tree-sitter AST metadata extraction with disk cache
│   │   │   ├── framework_guide.py     # Framework guide generation
│   │   │   ├── framework_detection.py # Framework detection from dependency files
│   │   │   ├── framework_search.py    # Web search for framework best practices
│   │   │   ├── framework_validation.py# File path validation against project tree
│   │   │   ├── command_detection.py   # Auto-detect lint/test/format commands (9 languages)
│   │   │   ├── style_guide.py         # CSS/template style extraction
│   │   │   ├── dedup.py               # Semantic deduplication of context sections
│   │   │   └── deprecations.py        # Backward-compat stubs
│   │   │
│   │   ├── languages/                    # Language registry (13 languages)
│   │   │   ├── registry.py            # Singleton loader, YAML → LanguageDefinition
│   │   │   ├── definitions.py         # LanguageDefinition, FanInConfig, FileTestPatterns
│   │   │   ├── extractor.py           # Generic tree-sitter AST extraction engine
│   │   │   └── *.yaml                 # Per-language definitions (python.yaml, etc.)
│   │   │
│   │   ├── reference/                   # Domain document indexing
│   │   │   ├── indexer.py             # Separate Whoosh index for reference docs
│   │   │   ├── chunker.py            # Prose-aware paragraph chunking
│   │   │   └── readers/              # Format-specific document readers
│   │   │       ├── base.py           # DocumentReader ABC
│   │   │       ├── text.py, markdown.py, html.py
│   │   │       ├── epub.py, pdf.py, docx.py
│   │   │       └── registry.py       # Reader factory by file extension
│   │   │
│   │   ├── scaffolds/                    # Project scaffold recipes (19 YAML files)
│   │   │   └── */scaffold.yaml        # Per-scaffold recipe
│   │   │
│   │   └── voice/                        # Voice interaction (all optional)
│   │       ├── audio_manager.py       # Singleton mic coordinator
│   │       ├── stt.py                # Speech-to-text (faster-whisper)
│   │       ├── tts.py                # Text-to-speech (kokoro-onnx)
│   │       ├── wake_word.py          # Wake word detection (openWakeWord)
│   │       ├── availability.py       # Feature detection
│   │       └── alsa_suppression.py   # Linux ALSA error suppression
│   │
│   └── tests/                            # pytest + pytest-asyncio tests
│
└── extension/
    ├── package.json                      # VSCode contributions, commands, settings, views
    └── src/
        ├── extension.ts                  # Entry point: activation, command registration
        ├── sidebarProvider.ts            # Main chat webview (chat + agent modes)
        ├── sidebarHtml.ts                # HTML/CSS/JS template for chat UI
        ├── backendClient.ts              # HTTP/WS client (custom no-timeout HTTP)
        ├── backendProcess.ts             # Backend auto-start/restart/stop
        ├── backendInstaller.ts           # First-run Python environment setup
        ├── chatParticipant.ts            # Chat participant WebSocket handler
        ├── streamHandler.ts              # Typed callback dispatcher for 25+ WS message types
        ├── wsHandler.ts                  # WebSocket message formatting helpers
        ├── inlineProvider.ts             # Copilot-style FIM inline completions
        ├── slashCommands.ts              # Slash command registry (11 commands)
        ├── sessionTreeProvider.ts        # Session list tree view
        ├── sessionDetailProvider.ts      # Session detail webview
        ├── settingsPanel.ts              # Settings UI webview
        ├── settingsPanelHtml.ts          # Settings HTML template
        ├── settingsSync.ts               # Settings → .env file sync
        ├── conversationManager.ts        # Chat history persistence
        ├── indexingService.ts            # Workspace indexing orchestration
        ├── notifications.ts              # VSCode notification integration
        ├── constants.ts                  # URLs, timeouts, context sizes
        └── types.ts                      # TypeScript interfaces for all message types
```

### 1.5 Design Constraints and Non-Goals

**Constraints:**
- All source code analysis uses tree-sitter AST queries — never regex
- Token budgets are percentage-based, computed from the active context window
- `create_file` is the tool name (not `write_file`) for clearer intent
- Structured JSON output from Ollama replaces regex-based plan/output parsing
- Never assign a persona to the LLM in system prompts — use capability-first framing

**Non-Goals:**
- No FSM library or state machine abstraction
- No ContextWindowManager — Ollama manages its own KV cache
- No rubric system for code quality evaluation
- No complex FSM or rubric-driven self-critique — only lightweight guardrails for tool progress and recovery

---

## 2. Configuration System

### 2.1 Settings Class

All configuration is managed by a single Pydantic `Settings` class using `pydantic-settings`. Environment variables use the `LEAN_AI_` prefix. An optional `.env` file is loaded automatically.

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LEAN_AI_", env_file=".env")
```

### 2.2 Complete Field Reference

#### LLM Provider Selection

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `llm_provider` | str | `"ollama"` | Primary provider: `"ollama"`, `"openai"`, or `"anthropic"` |

#### Ollama — Primary Model

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ollama_url` | str | `"http://localhost:11434"` | Ollama API endpoint |
| `ollama_model` | str | `"qwen3-coder:30b"` | Primary model name |
| `ollama_temperature` | float \| None | None | Sampling temperature. None = omit from Ollama options |
| `ollama_top_p` | float \| None | None | Nucleus sampling threshold. None = omit |
| `ollama_top_k` | int \| None | None | Top-k sampling. None = omit |
| `ollama_repeat_penalty` | float \| None | None | Repetition penalty. None = omit |
| `ollama_context_window` | int | `131072` | Context window (accepts shorthand) |
| `ollama_max_tokens` | int \| None | None | Derived: 25% of context_window |

#### Ollama — Expert Model (Reasoning-Heavy Phases)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ollama_model_expert` | str | `""` | Expert model name. Empty = use primary |
| `ollama_expert_temperature` | float \| None | None | None = omit for expert; does not inherit primary |
| `ollama_expert_top_p` | float \| None | None | None = omit for expert; does not inherit primary |
| `ollama_expert_top_k` | int \| None | None | None = omit for expert; does not inherit primary |
| `ollama_expert_repeat_penalty` | float \| None | None | None = omit for expert; does not inherit primary |
| `ollama_expert_context_window` | int \| None | None | Falls back to `ollama_context_window` |
| `ollama_expert_max_tokens` | int \| None | None | Derived: 25% of expert context_window |

#### Expert Model — Provider Selection

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `expert_llm_provider` | str | `""` | `"ollama"`, `"openai"`, `"anthropic"`, or `""` (auto-detect) |
| `openai_expert_model` | str | `""` | Falls back to `openai_model` |
| `anthropic_expert_model` | str | `""` | Falls back to `anthropic_model` |

#### Request Model — For /request Mode

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `request_llm_provider` | str | `""` | `"ollama"`, `"openai"`, `"anthropic"`, or `""` (auto-detect) |
| `ollama_model_request` | str | `""` | e.g. `"qwen3.5:27b"`. Empty = use primary |
| `ollama_request_temperature` | float \| None | None | None = omit for request; does not inherit primary |
| `ollama_request_top_p` | float \| None | None | None = omit for request; does not inherit primary |
| `ollama_request_top_k` | int \| None | None | None = omit for request; does not inherit primary |
| `ollama_request_repeat_penalty` | float \| None | None | None = omit for request; does not inherit primary |
| `ollama_request_context_window` | int \| None | None | Falls back to `ollama_context_window` |
| `ollama_request_max_tokens` | int \| None | None | Derived: 25% of request context_window |
| `openai_request_model` | str | `""` | OpenAI model for /request mode |
| `anthropic_request_model` | str | `""` | Anthropic model for /request mode |

#### OpenAI

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `openai_api_key` | str | `""` | Required when provider=openai |
| `openai_model` | str | `"gpt-4o"` | Model name |
| `openai_base_url` | str | `""` | Custom base URL for OpenAI-compatible APIs (Together, Groq, vLLM) |
| `openai_temperature` | float | `0.7` | Sampling temperature |
| `openai_context_window` | int | `128000` | Context window (accepts shorthand) |
| `openai_max_tokens` | int \| None | None | Derived: 25% of context_window |

#### Anthropic

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `anthropic_api_key` | str | `""` | Required when provider=anthropic |
| `anthropic_model` | str | `"claude-sonnet-4-20250514"` | Model name |
| `anthropic_temperature` | float | `0.7` | Sampling temperature |
| `anthropic_context_window` | int | `200000` | Context window (accepts shorthand) |
| `anthropic_max_tokens` | int \| None | None | Derived: 25% of context_window |

#### Thinking Mode

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enable_thinking` | bool | `True` | Pass `think=True` to Ollama for reasoning models (Qwen3, Qwen3.5) |
| `enable_thinking_expert` | bool | `True` | Independent thinking toggle for expert model |
| `enable_thinking_request` | bool | `True` | Independent thinking toggle for request model |

#### Inline Predictions (Always Ollama)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `inline_model` | str | `""` | Separate inline model. Empty = use primary |
| `inline_max_tokens` | int | `256` | Max tokens for completions |
| `inline_context_window` | int \| None | None | Derived: 12.5% of primary context_window |
| `inline_ollama_url` | str \| None | None | Falls back to `ollama_url` |

#### Embeddings (Always Ollama)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `embedding_model` | str | `"qwen3-embedding:0.6b"` | Embedding model for semantic search |
| `enable_embeddings` | bool | `True` | Enable embedding generation + RRF hybrid search |
| `embedding_ollama_url` | str \| None | None | Falls back to `ollama_url` |

#### Vision Model (Always Ollama, On-Demand)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `vision_model` | str | `""` | e.g. `"qwen3-vl:8b"`. Empty = vision disabled |
| `vision_ollama_url` | str \| None | None | Falls back to `ollama_url` |
| `vision_max_tokens` | int | `1024` | Max tokens for image description |
| `vision_timeout` | float | `60.0` | Timeout per image description (seconds) |

#### Voice — STT (faster-whisper)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enable_stt` | bool | `False` | Enable Speech-to-Text |
| `stt_model` | str | `"turbo"` | Model: `tiny\|base\|small\|medium\|large-v3\|turbo` |
| `stt_language` | str | `""` | ISO 639-1 code. Empty = auto-detect |
| `stt_silence_threshold` | float | `4.0` | Seconds of silence before auto-stop |
| `stt_beam_size` | int | `1` | 1=greedy (fastest), 5=beam search (most accurate) |
| `stt_cpu_threads` | int | `6` | CPU threads for inference |

#### Voice — TTS (kokoro-onnx)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enable_tts` | bool | `False` | Enable Text-to-Speech |
| `tts_voice` | str | `"af_heart"` | Voice ID (58 voices available) |
| `tts_speed` | float | `1.0` | Playback speed (0.5–2.0) |
| `tts_model_quality` | str | `"fp16"` | Model variant: `fp32` (~311MB), `fp16` (~169MB), `int8` (~88MB) |

#### Voice — Wake Word (openWakeWord)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enable_wake_word` | bool | `False` | Enable "Hey Computer" wake word detection |

#### Indexer

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `index_dir` | str | `".lean_ai_index"` | Whoosh index directory name |
| `chunk_max_lines` | int | `50` | Max lines per code chunk |
| `chunk_overlap_lines` | int | `10` | Overlap between adjacent chunks |

#### Internet / Search

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `search_provider` | str | `"duckduckgo"` | `"duckduckgo"`, `"searxng"`, `"google"`, or `"bing"` |
| `search_api_url` | str | `""` | SearXNG instance URL |
| `search_api_key` | str | `""` | SearXNG API key |
| `search_delay` | float | `2.0` | Min seconds between searches (with random jitter 0–100%) |
| `internet_timeout_seconds` | int | `30` | HTTP timeout for fetched pages |

#### Project Context

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enable_project_context` | bool | `True` | Generate `.lean_ai/project_context.md` |
| `enable_multi_round_context` | bool | `True` | Use multi-round expansion strategy |
| `enable_framework_guide` | bool | `True` | Generate `.lean_ai/framework_guide.md` |

#### Reference Library

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `reference_dir` | str | `".lean_ai/reference"` | Reference library documents directory |
| `reference_index_dir` | str | `".lean_ai_reference_index"` | Whoosh reference library index directory |

#### Local Refiner (Cloud Pre-Processing)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enable_refiner` | bool | `True` | Active only with cloud providers (openai, anthropic) |
| `refiner_ollama_url` | str \| None | None | Falls back to `ollama_url` |
| `refiner_model` | str \| None | None | Falls back to `ollama_model` |
| `refiner_timeout` | float | `30.0` | Max seconds for refinement pipeline |
| `refiner_enable_reference` | bool | `True` | Inject reference library context during refinement |
| `refiner_enable_privacy` | bool | `True` | Strip sensitive data before cloud transmission |
| `refiner_reference_chunks` | int | `5` | Max reference chunks to inject |

#### Implementation Control

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `implementation_max_tokens` | int \| None | None | Derived: 25% of active context_window |
| `implementation_max_turns` | int | `0` | Max tool-calling turns per session (0 = unlimited) |
| `reminder_interval` | int | `10` | Re-inject task reminder every N turns |
| `loop_detection_threshold` | int | `3` | Identical tool calls before warning (0 = disabled) |
| `refresh_threshold` | float | `0.7` | Refresh context at this % of context window |

#### Parallel LLM Requests

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `num_parallel` | int | `1` | Max concurrent LLM calls (match `OLLAMA_NUM_PARALLEL`) |

#### Fix Mode

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enable_fix_investigation` | bool | `True` | Read-only investigation phase before editing |

#### TDD Mode

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enable_tdd` | bool | `False` | Expert writes tests first, primary implements |
| `tdd_max_disputes_per_step` | int | `3` | Max test disputes per implementation step |

#### Post-Execution Validation

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enable_post_validation` | bool | `True` | Master switch |
| `post_format_command` | str | `""` | e.g. `"ruff format src/"` |
| `post_lint_fix_command` | str | `""` | e.g. `"ruff check --fix src/"` |
| `post_lint_command` | str | `""` | e.g. `"ruff check src/"` |
| `post_test_command` | str | `""` | e.g. `"pytest tests/ -x -q"` |
| `post_validation_max_retries` | int | `2` | Max LLM fix attempts (0 = no retries) |
| `post_validation_fix_turns` | int | `30` | Tool-calling turns per fix attempt |

#### Debug / Testing

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `debug_planning` | bool | `False` | Save planning phase outputs to `.lean_ai/plan_debug/{session_id}/` |

#### Tool Execution

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `tool_timeout_seconds` | int | `60` | Subprocess timeout |

#### LLM Retry

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `llm_retry_max` | int | `3` | Max retries for transient LLM errors |
| `llm_retry_base_delay` | float | `2.0` | Base delay for exponential backoff (seconds) |

#### Server

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `host` | str | `"127.0.0.1"` | Bind address |
| `port` | int | `8422` | Server port |

### 2.3 Context Window Shorthand Expansion

Context window fields accept compact notation. A `@model_validator(mode="before")` applies the `_expand_ctx()` function to all context window fields before Pydantic validation:

**Rules:**
- Values ending with `"k"` suffix: strip suffix, multiply by 1024. `"128k"` → 131072
- Integer values ≤ 10,000: treated as **k** (multiply by 1024). `128` → 131072
- Integer values > 10,000: used as-is. `131072` → 131072

**Fields affected:** `ollama_context_window`, `ollama_expert_context_window`, `ollama_request_context_window`, `openai_context_window`, `anthropic_context_window`, `inline_context_window`

### 2.4 Derived Field Logic

A `@model_validator(mode="after")` auto-computes token limits that weren't explicitly set:

```
ollama_max_tokens = ollama_context_window // 4          (25%)
openai_max_tokens = openai_context_window // 4          (25%)
anthropic_max_tokens = anthropic_context_window // 4    (25%)
inline_context_window = ollama_context_window // 8      (12.5%)
implementation_max_tokens = _active_context_window // 4 (25%)

# Expert (only if ollama_model_expert is set):
ollama_expert_context_window = ollama_context_window    (if None)
ollama_expert_max_tokens = ollama_expert_context_window // 4

# Request (only if ollama_model_request is set):
ollama_request_context_window = ollama_context_window   (if None)
ollama_request_max_tokens = ollama_request_context_window // 4
```

### 2.5 Effective Property Fallback Chains

Properties that resolve optional values through fallback chains:

| Property | Fallback Chain |
|----------|---------------|
| `_active_context_window` | `openai_context_window` if openai, `anthropic_context_window` if anthropic, else `ollama_context_window` |
| `effective_inline_url` | `inline_ollama_url` → `ollama_url` |
| `effective_embedding_url` | `embedding_ollama_url` → `ollama_url` |
| `effective_vision_url` | `vision_ollama_url` → `ollama_url` |
| `effective_expert_temperature` | `ollama_expert_temperature` only; None = omit |
| `effective_expert_top_p` | `ollama_expert_top_p` only; None = omit |
| `effective_expert_top_k` | `ollama_expert_top_k` only; None = omit |
| `effective_expert_repeat_penalty` | `ollama_expert_repeat_penalty` only; None = omit |
| `effective_expert_context_window` | Per-provider: openai→`openai_context_window`, anthropic→`anthropic_context_window`, else `ollama_expert_context_window` → `ollama_context_window` |
| `effective_expert_max_tokens` | Per-provider: openai→`openai_max_tokens`, anthropic→`anthropic_max_tokens`, else `ollama_expert_max_tokens` → derived |
| `effective_request_temperature` | `ollama_request_temperature` only; None = omit |
| `effective_request_top_p` | `ollama_request_top_p` only; None = omit |
| `effective_request_top_k` | `ollama_request_top_k` only; None = omit |
| `effective_request_repeat_penalty` | `ollama_request_repeat_penalty` only; None = omit |
| `effective_request_max_tokens` | Per-provider: openai→`openai_max_tokens`, anthropic→`anthropic_max_tokens`, else `ollama_request_max_tokens` → derived |
| `effective_refiner_url` | `refiner_ollama_url` → `ollama_url` |
| `effective_refiner_model` | `refiner_model` → `ollama_model` |

### 2.6 Validation

A `@model_validator(mode="after")` ensures:
- Positive integers: `ollama_context_window`, `openai_context_window`, `anthropic_context_window`, `tool_timeout_seconds`, `stt_cpu_threads`
- Positive floats: `stt_silence_threshold`, `tts_speed`, `refresh_threshold`

### 2.7 Singleton

The module exports a singleton: `settings = Settings()`

---

## 3. Database Schema & Persistence

### 3.1 Database Location and Initialization

The SQLite database lives at `.lean_ai/lean_ai.db` relative to the repository root. The `.lean_ai/` directory is created on first access.

```python
def _db_path(repo_root: str) -> Path:
    p = Path(repo_root) / ".lean_ai"
    p.mkdir(parents=True, exist_ok=True)
    return p / "lean_ai.db"
```

`get_db(repo_root)` opens the database, sets `row_factory = aiosqlite.Row`, runs the schema DDL, and calls `_ensure_columns()` for migration.

### 3.2 Table Schemas

```sql
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,                     -- UUID hex[:12]
    repo_root TEXT NOT NULL,                 -- Absolute path to repo
    task TEXT NOT NULL,                      -- User's task description
    plan TEXT,                               -- JSON plan (after planning)
    status TEXT NOT NULL DEFAULT 'active',   -- active|completed|merged|abandoned
    created_at TEXT NOT NULL,                -- ISO 8601 UTC timestamp
    completed_at TEXT,                       -- ISO 8601 UTC timestamp
    branch_name TEXT,                        -- Work branch name
    base_branch TEXT,                        -- Default branch (main/master)
    stashed INTEGER NOT NULL DEFAULT 0       -- 1 if working tree was stashed
);

CREATE TABLE IF NOT EXISTS tool_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    tool_name TEXT NOT NULL,                 -- e.g. "create_file", "edit_file"
    parameters TEXT,                         -- JSON-serialized arguments
    result TEXT,                             -- Tool output text
    success INTEGER NOT NULL DEFAULT 1,      -- 0=failure, 1=success
    created_at TEXT NOT NULL                  -- ISO 8601 UTC timestamp
);

CREATE TABLE IF NOT EXISTS conversation_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,                      -- "user", "assistant", "tool", "system"
    content TEXT NOT NULL,                   -- Message content
    tool_name TEXT,                          -- Tool name (for tool calls/results)
    tool_args TEXT,                          -- JSON tool arguments
    created_at TEXT NOT NULL                  -- ISO 8601 UTC timestamp
);

CREATE TABLE IF NOT EXISTS session_commits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    commit_sha TEXT NOT NULL,                -- Full commit SHA
    message TEXT,                            -- Commit message
    created_at TEXT NOT NULL                  -- ISO 8601 UTC timestamp
);
```

### 3.3 Schema Migration

`_ensure_columns(db)` adds columns that may be missing from older databases. It attempts `ALTER TABLE ADD COLUMN` for each column and silently catches exceptions if the column already exists:

| Table | Column | Type |
|-------|--------|------|
| sessions | branch_name | TEXT |
| sessions | base_branch | TEXT |
| sessions | stashed | INTEGER NOT NULL DEFAULT 0 |
| sessions | merge_commit_sha | TEXT |

### 3.4 CRUD Operations

#### Session Operations

| Function | Signature | Description |
|----------|-----------|-------------|
| `create_session` | `async (db, repo_root: str, task: str) → str` | Creates session with UUID hex[:12] ID, status='active'. Returns session_id |
| `update_session` | `async (db, session_id, *, plan?, status?, branch_name?, base_branch?, stashed?, merge_commit_sha?) → None` | Updates any combination of fields. Auto-sets `completed_at` when status is 'merged' or 'abandoned' |
| `get_session` | `async (db, session_id) → dict \| None` | Returns frontend-formatted session (field renaming via `_format_session`) |
| `get_session_raw` | `async (db, session_id) → dict \| None` | Returns raw DB row dict (for backend-internal operations) |
| `list_sessions` | `async (db) → list[dict]` | All sessions, newest first, frontend-formatted |
| `delete_session` | `async (db, session_id) → bool` | Deletes session + all associated data (commits, conversations, tool_logs). Returns True if found |
| `search_sessions` | `async (db, query="", commit_sha="") → list[dict]` | Search by task/plan text (LIKE), conversation content, or commit SHA prefix. Deduplicates results |

#### Frontend Data Formatting

`_format_session(row)` maps raw DB columns to the frontend `SessionSummary` shape:

```python
{
    "session_id": row["id"],
    "title": task[:80],
    "session_status": row["status"],
    "workflow_stage": "completed" if status == "completed" else "active",
    "task_track": None,
    "base_branch": row["base_branch"],
    "plan_branch": row["branch_name"],
    "merge_commit_sha": row["merge_commit_sha"],
    "created_at": row["created_at"],
    "updated_at": row["completed_at"] or row["created_at"],
}
```

#### Tool Log Operations

| Function | Signature | Description |
|----------|-----------|-------------|
| `log_tool_call` | `async (db, session_id, tool_name, parameters: dict, result: str, success: bool) → None` | Records tool invocation with JSON-serialized parameters |

#### Conversation Log Operations

| Function | Signature | Description |
|----------|-----------|-------------|
| `log_conversation_entry` | `async (db, session_id, role, content, tool_name?, tool_args?) → None` | Record + commit |
| `log_conversation_entry_nocommit` | `async (db, session_id, role, content, tool_name?, tool_args?) → None` | Record without commit (caller must flush) |
| `flush_conversation_log` | `async (db) → None` | Commit buffered entries |
| `get_conversation_log` | `async (db, session_id) → list[dict]` | Full log, oldest first. Returns role, content, tool_name, tool_args, created_at |

#### Commit Tracking

| Function | Signature | Description |
|----------|-----------|-------------|
| `log_commit` | `async (db, session_id, commit_sha, message="") → None` | Record commit associated with session |
| `get_commits_for_session` | `async (db, session_id) → list[dict]` | List commits (commit_sha, message, created_at) |
| `find_session_by_commit` | `async (db, sha_prefix) → dict \| None` | Find session by commit SHA prefix match |

---

## 4. LLM Abstraction Layer

### 4.1 Data Types

```python
# Type alias for streaming token callbacks
StreamCallback = Callable[[str], Awaitable[None]]

@dataclass
class ToolCall:
    """Record of an executed tool."""
    tool_name: str
    parameters: dict = field(default_factory=dict)
    description: str = ""

@dataclass
class ToolCallInfo:
    """Normalized tool call from provider response."""
    name: str
    arguments: dict = field(default_factory=dict)
    id: str | None = None  # Provider-specific ID for result correlation

@dataclass
class LLMMetrics:
    """Standardized metrics from any provider."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tokens_per_second: float | None = None
    stop_reason: str | None = None
    thinking: str | None = None  # Raw thinking/reasoning output
```

### 4.2 LLMProvider ABC

Abstract base class that all providers implement:

```python
class LLMProvider(ABC):
    # Properties
    @property
    def model_name(self) -> str: ...
    @property
    def context_window(self) -> int: ...
    @property
    def max_tokens(self) -> int: ...

    # Abstract methods
    async def chat_raw(self, messages, temperature=None, max_tokens=None,
                       *, stream_callback=None, thinking_callback=None
                       ) -> tuple[str, LLMMetrics]: ...

    async def chat_structured(self, messages, schema: type[BaseModel],
                              temperature=None, max_tokens=None,
                              *, thinking_callback=None
                              ) -> tuple[BaseModel, LLMMetrics]: ...

    async def chat_with_tools_single(self, messages, tools, max_tokens=None
                                     ) -> tuple[str, list[ToolCallInfo], LLMMetrics]: ...

    async def chat_stream(self, messages, temperature=None, max_tokens=None,
                          *, thinking_callback=None
                          ) -> AsyncIterator[str]: ...

    async def check_health(self) -> bool: ...

    # Concrete methods with defaults (overridden by Anthropic)
    def format_tool_result_messages(self, tool_call, content) -> list[dict]: ...
    def format_assistant_tool_message(self, content, tool_calls) -> dict: ...
```

### 4.3 Ollama Provider (`OllamaProvider`)

**Constructor parameters:** `ollama_url`, `embed_ollama_url`, `model`, `max_tokens`, `context_window`, `temperature`, `top_p`, `top_k`, `repeat_penalty`, `enable_thinking`

**Options dict** passed to every Ollama call:
```python
{
    "num_predict": int,   # max output tokens
    "num_ctx": int,       # context window
    # Optional sampling keys are present only when configured:
    "temperature": float,
    "top_p": float,
    "top_k": int,
    "repeat_penalty": float,
    "min_p": float,
    "presence_penalty": float,
}
```

**Metrics extraction** from Ollama response:
- `prompt_tokens` = `response["prompt_eval_count"]`
- `completion_tokens` = `response["eval_count"]`
- `tokens_per_second` = `eval_count / (eval_duration / 1_000_000_000)`
- `stop_reason` = `response["done_reason"]`

**Thinking mode:** When `enable_thinking=True`, passes `think=True` to Ollama. Reasoning models (Qwen3, Qwen3.5) produce thinking tokens separate from content tokens. Streaming separates them via callbacks.

**Retry logic** (`_retry_with_backoff`):
- Retries: `settings.llm_retry_max` (default 3)
- Transient errors: `ConnectionError`, `TimeoutError`, `OSError`
- Server errors: Ollama `ResponseError` with status >= 500
- Backoff: `base_delay * (2 ** attempt)` seconds

**Structured output:** Uses Ollama's `format=schema.model_json_schema()`. Retries once on validation failure.

**FIM completions** (`generate_completion`): Calls `_client.generate()` with suffix parameter. Disables FIM if model doesn't support it (`_fim_supported` flag).

**Embeddings** (`embed`): Calls `_embed_client.embed()` (may point to different Ollama instance).

**Message sanitization** (`_sanitize_messages`):
1. Merge consecutive assistant messages
2. For each assistant message with `tool_calls`: count following tool-result messages. If fewer results than calls → trim tool_calls to match. If zero results → drop assistant message entirely.

### 4.4 OpenAI Provider (`OpenAIProvider`)

**Constructor parameters:** `api_key`, `model`, `max_tokens`, `context_window`, `temperature`, `base_url` (for OpenAI-compatible APIs), `retry_max`, `retry_base_delay`

**Key differences from Ollama:**
- Tool call arguments are JSON strings that need `json.loads()` parsing
- Tool call IDs must be correlated in result messages (`tool_call_id` field)
- Structured output uses `response_format={"type": "json_schema", "json_schema": {...}}`
- Streaming requires `stream_options={"include_usage": True}` to get token counts
- Retry catches `APIConnectionError`, `InternalServerError`, `RateLimitError`
- Removes unsupported `"title"` key from JSON schemas

**Message formatting:**
```python
# Tool result messages include tool_call_id
[{"role": "tool", "tool_call_id": "call_abc123", "content": "result text"}]

# Assistant tool message uses tool_calls array
{
    "role": "assistant",
    "content": "thinking text",
    "tool_calls": [
        {"id": "call_abc123", "type": "function",
         "function": {"name": "create_file", "arguments": "{\"path\": ...}"}}
    ]
}
```

### 4.5 Anthropic Provider (`AnthropicProvider`)

**Constructor parameters:** `api_key`, `model`, `max_tokens`, `context_window`, `temperature`, `retry_max`, `retry_base_delay`

**Key differences from Ollama/OpenAI:**
- System prompt extracted to separate parameter (`_split_system`)
- Tools converted from OpenAI format to Anthropic format (`_convert_tools`): `input_schema` instead of nested `function.parameters`
- All chat calls stream internally via `messages.stream()`
- Structured output: injects JSON schema into system prompt + strips markdown fences from response
- Retry catches Anthropic-specific exceptions

**Message formatting (critical — different from OpenAI):**
```python
# Tool result messages are USER messages with tool_result content blocks
[{
    "role": "user",
    "content": [
        {"type": "tool_result", "tool_use_id": "toolu_abc123", "content": "result text"}
    ]
}]

# Assistant tool message uses content blocks (not tool_calls array)
{
    "role": "assistant",
    "content": [
        {"type": "text", "text": "thinking text"},
        {"type": "tool_use", "id": "toolu_abc123", "name": "create_file",
         "input": {"path": "...", "content": "..."}}
    ]
}
```

### 4.6 Provider Message Format Comparison

| Aspect | Ollama | OpenAI | Anthropic |
|--------|--------|--------|-----------|
| System prompt | In messages array | In messages array | Separate `system` parameter |
| Tool calls | `tool_calls` in response | `tool_calls` array with IDs | `tool_use` content blocks |
| Tool results | `role: "tool"` | `role: "tool"` + `tool_call_id` | `role: "user"` + `tool_result` block |
| Assistant + tools | `tool_calls` field on dict | `tool_calls` array with JSON args | Content blocks array |
| Structured output | `format=json_schema` param | `response_format` param | Schema injected in system prompt |
| Thinking tokens | `think=True` option | Not supported | Not supported |

### 4.7 LLMClient Facade

Unified interface that delegates to the active provider:

```python
class LLMClient:
    def __init__(self, provider=None, concurrency_semaphore=None):
        self._provider = provider or OllamaProvider()
        self._semaphore = concurrency_semaphore
        # Creates secondary Ollama provider for embed/inline if primary isn't Ollama
```

**Semaphore gating:** `chat_raw` acquires semaphore before calling provider (throttles concurrent calls).

**Always-Ollama methods:** `generate_completion` and `embed` always use Ollama, even when primary provider is OpenAI/Anthropic.

---

## 5. Multi-Turn Tool Orchestration

### 5.1 `chat_with_tools` — The Core Loop

```python
async def chat_with_tools(
    self,
    messages: list[dict],
    tools: list[dict],
    tool_executor_fn: Callable,       # async (name, arguments) -> str
    *,
    max_turns: int = 50,
    max_tokens: int | None = None,
    task_reminder: str | Callable[[], str] | None = None,
    reminder_interval: int = 10,
    loop_detection_threshold: int | None = None,
    text_only_nudge: str | None = None,
    on_tool_call: Callable | None,    # async (name, args)
    on_tool_result: Callable | None,  # async (name, result)
    on_content: Callable | None,      # async (text)
    on_thinking: Callable | None,     # async (text)
    on_metrics: Callable | None,      # async (prompt_tokens, context_window)
    on_context_refresh: Callable | None,
    dispatcher: WSMessageDispatcher | None = None,
) -> tuple[list[ToolCall], str]
```

**Returns:** `(executed_tools_list, final_explanation_text)`

### 5.2 Loop Algorithm

```
SANITIZE messages (remove orphaned tool calls, merge consecutive assistant)

FOR turn = 0 to max_turns:
    1. CHECK CANCELLATION via dispatcher.check_cancelled()
    2. CHECK USER INTERRUPT via dispatcher.get_pending_message()
       → If found: inject as user message "[USER INTERRUPT] {content}"

    3. CALL provider.chat_with_tools_single(messages, tools, max_tokens)
       → Returns (content, tool_calls, metrics)

    4. FORWARD metrics via on_metrics callback
    5. FORWARD thinking tokens via on_thinking callback

    6. IF NO TOOL CALLS (text-only response):
       - Append assistant message to history
    7. IF TOOL CALLS present:
       a. Reset text-only and truncation counters
       b. Check for task_complete → EXIT if found
       c. Build assistant message via provider.format_assistant_tool_message()
       d. FOR EACH tool call:
          - Execute via tool_executor_fn(name, arguments)
          - Append result via provider.format_tool_result_messages()
          - Fire on_tool_call and on_tool_result callbacks
          - Track tool hash for loop detection
       e. Check if any tool was task_complete → EXIT

    8. EVALUATE TURN via _evaluate_turn() — single decision point:
       Priority: EXIT > REFRESH > NUDGE > CONTINUE
       - EXIT if: 3+ text-only or 5+ truncated responses
       - REFRESH if: token usage >= 70% of context window (event-driven)
       - NUDGE if: text-only (generic nudge), truncated (truncation nudge),
         loop detected (N identical calls), or reminder interval reached
       - CONTINUE otherwise

    9. ACT on _evaluate_turn result:
       - EXIT: break loop
       - REFRESH: call _maybe_refresh_context()
       - NUDGE: inject message, skip to next turn for text-only

RETURN (executed_tools, joined_explanation_parts)
```

### 5.3 Exit Conditions

| Condition | Behavior |
|-----------|----------|
| `task_complete` tool called | Normal exit |
| 3 consecutive text-only responses | Exit (LLM not calling tools) |
| 5 consecutive truncated responses | Exit (output too long for tool calls) |
| `max_turns` reached | Exit |
| User cancellation via dispatcher | Raises `WorkflowCancelledError` |

### 5.4 Turn Supervisor

`_evaluate_turn()` makes one decision per turn after all tool calls are processed. It inspects `_TurnState` counters (text-only streak, truncation streak, loop detection hash) and returns a `TurnAction` with a `TurnVerdict` (CONTINUE, NUDGE, REFRESH, EXIT). Context refresh is event-driven by token threshold — no fixed turn spacing.

### 5.5 Loop Detection

- Hash: SHA-256 of `"{tool_name}:{json.dumps(args, sort_keys=True)}"`
- Threshold: `settings.loop_detection_threshold` (default 3, but internal uses 5 for checking)
- On detection: injects user message suggesting a different approach

### 5.6 Context Refresh

- Triggered at 70% of context window (configurable via `refresh_threshold`)
- Checked every 5 turns
- Callback rebuilds messages from fresh disk state (re-reads context files, injects scratchpad)
- No LLM summarization — pure message reconstruction
- Sends `context_refreshed` WebSocket notification

---

## 6. Three-Model Pipeline

### 6.1 Model Roles

| Role | Model | Used For |
|------|-------|----------|
| **Primary** | `llm_client` | Planning phases 1-2, plan execution, fix mode implementation |
| **Expert** | `expert_llm_client` | Planning phases 3-5, final validation fix retry, TDD test writing, TDD dispute evaluation |
| **Request** | `request_llm_client` | Chat endpoint, `/request` mode (open-ended tasks) |
| **Inline** | `_inline_client` | FIM inline completions (always Ollama) |

### 6.2 Provider Factory (`dependencies.py`)

```python
def _create_provider() -> LLMProvider:
    # Based on settings.llm_provider:
    if "ollama": return OllamaProvider(url, model, max_tokens, context_window, ...)
    if "openai": return OpenAIProvider(api_key, model, max_tokens, context_window, ...)
    if "anthropic": return AnthropicProvider(api_key, model, max_tokens, context_window, ...)
```

### 6.3 Singleton Creation (Module-Level)

```python
# Concurrency semaphore (if num_parallel > 1)
_llm_semaphore = asyncio.Semaphore(settings.num_parallel) if settings.num_parallel > 1 else None

# Primary LLM client
llm_client = LLMClient(provider=_create_provider(), concurrency_semaphore=_llm_semaphore)

# Inline client (always Ollama, separate model/URL)
_inline_client = LLMClient(provider=OllamaProvider(...), ...)

# Expert client (optional — requires explicit configuration)
expert_llm_client = LLMClient(provider=...) or None

# Request client (optional — for chat/request mode)
request_llm_client = LLMClient(provider=...) or None

# Refiner (active only with cloud providers + enable_refiner)
refiner = PromptRefiner(OllamaProvider(...)) if cloud_provider else None
```

### 6.4 Expert Model Auto-Detection

```python
if expert_llm_provider == "openai": use OpenAIProvider with openai_expert_model
elif expert_llm_provider == "anthropic": use AnthropicProvider with anthropic_expert_model
elif expert_llm_provider == "ollama" or (empty and primary is Ollama and ollama_model_expert set):
    use OllamaProvider with expert settings (temp, top_p, top_k, repeat_penalty, context_window)
else: expert_llm_client = None
```

### 6.5 Prompt Refiner

Active only when primary provider is OpenAI or Anthropic. Uses a local Ollama model to:

1. **Reference injection:** Query reference library, inject relevant context into prompts
2. **Privacy redaction:** Strip API keys, tokens, hostnames, emails, DB connections before cloud transmission
3. **Message refinement:** Add structure and technical specificity to user messages

```python
class PromptRefiner:
    async def refine_chat_message(user_message, repo_root, history) -> RefinerResult
    async def refine_task(task, repo_root, context) -> RefinerResult
    async def strip_privacy(text) -> tuple[str, list[str]]
```

**Guards:**
- Over-stripping: if sanitized < 60% of original length, keep original
- Over-refinement: if refined < 50% of original length, keep original
- Timeout: non-fatal, returns original on failure

---

## 7. Planning System (5-Phase)

### 7.1 Plan Schema

```python
class PlanStep(BaseModel):
    step_number: int          # Sequence number
    tool: str                 # "create_file", "edit_file", "run_tests", "run_lint", "format_code"
    file_path: str            # Target file (relative to repo root). Empty for run_tests/run_lint
    instruction: str          # Detailed natural-language instruction
    context: str              # Relevant file content from planner investigation

class VerificationPlan(BaseModel):
    steps: list[PlanStep]     # Verification/test steps only

class ExecutionPlan(BaseModel):
    scope: str                # Brief summary of what plan accomplishes
    user_summary: str         # Plain-English explanation (~1000 words) for user approval
    naming_conventions: str   # Naming patterns extracted from existing code
    name_registry: str        # Canonical name mapping for every new entity
    steps: list[PlanStep]     # Ordered implementation steps
    tdd_test_steps: list[PlanStep]  # Test steps (TDD only, executed before implementation)
    affected_files: list[str] # All files created or modified
    test_strategy: str        # How to verify changes work
```

**`plan_to_markdown(plan, include_context=False) -> str`** renders the plan as human-readable markdown with sections for scope, naming conventions, name registry, steps (TDD: separate test/implementation headers), affected files, and test strategy.

### 7.2 Clarity Assessment

```python
async def assess_clarity(task: str, llm_client: LLMClient, context: str = "") -> list[str] | None
```

- Uses `CLARIFICATION_SYSTEM_PROMPT`: respond with "CLEAR" or JSON array of 3-5 questions
- Returns `None` if task is clear
- Returns `list[str]` of questions if clarification needed
- Fallback: extract lines containing "?" characters

### 7.3 Phase 1: Scope Analysis

- **Model:** Request (or primary fallback)
- **Method:** `chat_with_tools()` with a restricted read-only tool subset
- **Tools available:** `grep_files`, `read_file`, `list_directory`, `query_project_context`, `search_reference`, `task_complete`
- **Budget:** `LEAN_AI_PLAN_PHASE1_MAX_TURNS` (default 5), `text_only_exit_count=1`
- **Input:** Task + codebase context + session memories (budget-gated at 2% of context window)
- **Output:** 8-section scope document:
  - `PROBLEM / PURPOSE` — restates the task + why it matters
  - `DELIVERABLES` — observable outcomes, not file changes
  - `IN SCOPE` — concrete greppable entities being created or modified
  - `OUT OF SCOPE` — tempting-adjacent areas explicitly excluded
  - `DOWNSTREAM CONSUMERS` — categories of files that reference modified entities
  - `ASSUMPTIONS (with verification hints)` — each paired with how Phase 2 can falsify it
  - `SUCCESS CRITERIA` — falsifiable done conditions
  - `RISKS` — scope-level misunderstandings the team should pressure-test
- **Prompt:** `planning.scope_system` + `planning.scope_user`, both with a `{PHASE1_MAX_TURNS}` template variable synced to the setting.
- **Streaming:** Content, thinking, and tool call/result events forwarded via callbacks

### 7.4 Phase 2: File Identification with Deterministic Capture

- **Model:** Request (or primary fallback)
- **Method:** `chat_with_tools()` followed by a `chat_structured` synthesis pass
- **Tools available (Phase-2-specific filter):** `read_file`, `grep_files`, `list_directory`, `directory_tree`, `query_project_context`, `search_internet`, `fetch_url`, `search_wiki*`, `fetch_wiki*`, `update_scratchpad`, `add_journal_entry`, `record_file_observation`, `task_complete`. Reference library tools (`search_reference`, `list_reference_documents`) are **dropped** from this phase — noise for file identification.
- **Input:** Task + scope + codebase context
- **Checklist opener:** Phase 2's user prompt starts with a strict ASSUMPTIONS checklist that walks every verification hint from Phase 1's scope before general exploration.
- **Deterministic capture:** The model calls `record_file_observation(file_path, role, reason, relevant_sections, key_snippets)` for every relevant file. Observations are upserted by `file_path` into `.lean_ai/observations/{session_id}.json`. No reliance on prose transcription.
- **Synthesis pass:** After the exploration loop exits, `_synthesize_file_summary` (prompt key `planning.exploration_synthesis_system`) coerces the accumulated observations + scratchpad + journal + prose into a validated `FileSummary` Pydantic model:
  - `files_to_modify`, `files_to_create`, `files_read_for_context` (each `list[FileObservation]`)
  - `missing_infrastructure` (`list[MissingItem]`)
  - `verified_references` (`list[VerifiedReference]`)
  - `assumptions_resolved` (`list[AssumptionStatus]`) — one per Phase 1 ASSUMPTION
  - `notes` (free-form catch-all)
- **Return value:** `(FileSummary | None, markdown, elapsed)` — the structured object propagates to Phase 4 validators; the markdown feeds the `{file_summary}` template variable in Phase 3/4. Parallel path returns `None` for the structured object; validators skip cleanly.
- **Key directives:**
  - Use `grep_files` to trace ALL downstream consumers of modified entities
  - Verify every Phase 1 assumption with its listed hint
  - Check existing state (table already exists? route registered?)
  - Read registration files (routes, DI config, middleware, `__init__.py`) and `record_file_observation` them as `role: reference`
- **Budget:** `settings.implementation_max_turns` or unlimited
- **Privacy pass:** If refiner active, strips sensitive data from file summary before sending to expert

### 7.5 Phase 3: Design + Risk Synthesis

- **Model:** Expert (or primary if no expert)
- **Method:** `chat_raw()` with `PLAN_SYSTEM_PROMPT` + project_context.md
- **Input:** Task + scope + file summary + project context
- **Two-pass flow:**
  1. **Pass 1** — `chat_with_tools` exploration/verification with `build_design_tools()` (search_internet, fetch_url, KB, wiki, task_complete), `max_turns=15`, `text_only_exit_count=1` (single-shots when FileSummary.verified_references already covers every external surface). Output is free-form prose.
  2. **Pass 2** — `chat_structured` synthesis via `_synthesize_design_and_risks` + `planning.design_synthesis_system` produces a validated `DesignAndRisks` Pydantic model: `naming_conventions` (list[NamingConvention]), `change_designs` (list[ChangeDesign], non-obvious files only), `missing_files` (list[MissingFile]), `dependency_order` (list[DependencyOrder]), `critical_risks` (list[CriticalRisk]), `citations` (list[VerifiedReference]), `notes`.
- `{missing_files}` for Phase 4 is derived deterministically from `DesignAndRisks.missing_files` via `_format_missing_files` (no secondary LLM call).
- **No scratchpad/journal injection** into Phase 3 — `FileSummary.key_snippets` from Phase 2 is the authoritative bridge and is called out as such in the system prompt.
- **Anti-hallucination:** Must not simulate commands, invent file listings, or fabricate contents

### 7.6 Phase 4: Plan Assembly (Structured JSON)

- **Model:** Expert
- **Method:** `chat_structured(schema=ExecutionPlan)`
- **Input:** Task + design and risk synthesis + file summary + scope + project context
- **Max tokens:** 40% of expert context window (`PLAN_OUTPUT_PERCENT`)
- **Output:** Complete `ExecutionPlan` JSON with all steps
- **Key rules:**
  - Steps 1-5: ONLY infrastructure/config/missing files from Phase 3's gap analysis
  - `name_registry`: canonical names for every new entity (class, namespace, import, table, file, route, test)
  - For `edit_file`: exact location, what currently exists, exact new code, surrounding context
  - For `create_file`: detailed spec (file type, namespace, imports, column types, method signatures), pattern file, 15+ line snippet
  - Dependency-first ordering
  - No run_tests/run_lint in middle of plan (appended by Phase 5)
- **Post-processing:**
  - Strip mid-plan run_tests/run_lint/format_code steps
  - Dedup steps for same file path (keep first)
  - Re-number steps sequentially
- **Structured fields on `ExecutionPlan`:**
  - `naming_conventions` is `list[NamingConvention]` (reuses Phase 3's schema)
  - `name_registry` is `list[NameRegistryEntry]` (typed per-entity rows with optional `model_class`, `module_namespace`, `import_stmt`, `db_table`, `file_path`, `route_endpoint`, `registered_in`, `test_file`)
  - `plan_validation_warnings: list[str]` surfaces post-generation validator warnings to the extension's approval UI via the `approval_required` WebSocket message
- **Post-generation validators (pure Python, no regex):**
  - `_check_hallucinated_paths` — any `step.file_path` not in the known-paths set built from FileSummary + DesignAndRisks.missing_files
  - `_uncovered_missing_files` — `DesignAndRisks.missing_files` entries with no covering step
  - `_check_edit_create_consistency` — `edit_file` on unknown-to-modify paths, `create_file` on unknown-to-create paths
- **Auto-revision:** When any uncovered missing file has `blocking=True`, Phase 4 fires a single `_revise_plan` round with synthesised feedback. Still-uncovered blocking files on the second pass fall through to warn-only (never hard-blocks the approval screen).
- **Formatters for executor compatibility:** `format_naming_conventions_for_prompt` and `format_name_registry_for_prompt` render the structured lists back to the text shapes `build_step_system_prompt` / `build_tdd_test_writing_prompt` / `build_tdd_step_system_prompt` already expect — per-step execution prompts are unchanged.

### 7.7 Phase 5: Verification Step Generation

- **Model:** Expert
- **Method:** `chat_structured(schema=VerificationPlan)` (only if `test_command` available)
- **Prompts (registry-backed, one per mode):**
  - `planning.verification_user_normal` — asks for test-file `create_file` steps + a final `run_tests` step
  - `planning.verification_user_tdd` — asks for test-file `create_file` steps only, explicitly forbids `run_tests`
  - `planning.verification_system` — shared, provides executor-model awareness + the common-LLM-defects checklist
- **Structured inputs:** Phase 5 consumes the Phase 2 `FileSummary` and Phase 3 `DesignAndRisks` objects (not just their markdown). Two pure-Python helpers feed prompt variables:
  - `_build_verification_targets(FileSummary, DesignAndRisks)` — bullet list of files needing coverage from `change_designs[].file_path` + `files_to_create[].file_path`
  - `_build_security_concerns(DesignAndRisks)` — bullet list of `critical_risks` with severity + mitigation
- **Input:** Task + complete plan + test command + file summary markdown + the two structured inputs above
- **Output:** Test file creation steps + (normal mode) final `run_tests` step
- **TDD mode:** Stores test steps in `plan.tdd_test_steps` (defensive `run_tests` filter kept as safety). Re-numbers implementation steps to run after the test steps.
- **Non-TDD:** Appends test steps + `run_tests` to `plan.steps`.
- **Post-generation validation:** `_check_test_path_conventions` flags `create_file` steps whose paths don't contain `test` or `spec` (case-insensitive) and don't match a directory prefix learned from Phase 2's `files_read_for_context`. Warnings append to `plan.plan_validation_warnings` via the same surfacing mechanism Phase 4 uses.

### 7.8 Plan Revision

When user provides feedback instead of approving:
- Builds `revision_context` = JSON dump of previous plan + user feedback
- Calls `create_plan()` again with `revision_context` parameter
- Expert makes targeted edits based on feedback
- Max `_MAX_REVISIONS = 5` rounds

### 7.9 Debug Output

When `settings.debug_planning = True`:
- Saves each phase output to `.lean_ai/plan_debug/{session_id}/{phase_name}.md`
- Saves final plan as JSON + markdown
- Saves `meta.json` with timing and statistics

---

## 8. Workflow Modes

### 8.1 Plan Mode (`run_workflow` with mode="plan")

**Sequence:**
```
User Task
    ↓
1. _clarify_task()
   ├─ assess_clarity() → questions?
   ├─ Send clarification_needed WS message
   ├─ Wait for user response
   └─ Augment task with answers
    ↓
2. create_plan()
   └─ 5-phase planning pipeline (Sections 7.3-7.7)
    ↓
3. _wait_for_approval()
   ├─ Send approval_required WS message with plan markdown
   ├─ Wait for user: approve or feedback
   ├─ On feedback: revise plan (max 5 rounds)
   └─ On approve: continue
    ↓
4. _execute_plan()
   ├─ Per-step execution (Section 9)
   ├─ TDD three-phase execution (if enabled)
   └─ Post-validation (Section 10)
    ↓
5. Complete
   ├─ Update project_context.md incrementally
   ├─ Send complete WS message
   └─ Return commit message
```

### 8.2 Fix Mode (`_run_fix` with mode="fix")

**Model selection:** Uses expert model (bug diagnosis is reasoning-heavy)

**Sequence:**
```
User Task
    ↓
1. Investigation Phase (if enable_fix_investigation=True)
   ├─ Tools: INVESTIGATION_TOOLS (read-only + run_tests + search + scratchpad)
   ├─ System prompt: FIX_INVESTIGATION_PROMPT
   ├─ Budget: 20 turns
   ├─ Diagnosis saved to scratchpad
   └─ Sends stage_change "investigating"
    ↓
2. Implementation Phase
   ├─ Tools: IMPLEMENTATION_TOOLS (full access)
   ├─ System prompt: FIX_SYSTEM_PROMPT + context
   ├─ Injects scratchpad from investigation
   ├─ Budget: unlimited (or implementation_max_turns)
   ├─ Supports task reminders (_build_fix_reminder)
   ├─ Supports context refresh (_build_context_refresh)
   └─ Sends stage_change "implementing"
    ↓
3. Post-Validation (Section 10)
    ↓
4. Complete
   └─ Commit message: "lean-ai(fix): {summary}"
```

### 8.3 Request Mode (`_run_fix` with mode="request")

**Model selection:** Uses request model (chatty, higher temperature)

**Differences from fix mode:**
- No investigation phase
- Neutral system prompt: `REQUEST_SYSTEM_PROMPT` (no bug-fix framing)
- Text-only nudge forces tool calling: "STOP generating text. You MUST call a tool now..."
- No test requirement instruction
- Commit message: `"lean-ai(request): {summary}"`

### 8.4 Context Refresh in Fix/Request Mode

`_build_context_refresh()` callback rebuilds messages from fresh disk state:
1. Reconstruct system prompt with fresh context
2. Build new message list: [system, user task]
3. If scratchpad exists: append scratchpad content as user message
4. Sends `context_refreshed` WebSocket notification

### 8.5 Task Reminder System

`_build_fix_reminder()` callable generates dynamic reminders:
- Returns original task text + current scratchpad content
- Injected as user message every `reminder_interval` turns

---

## 9. Plan Execution

### 9.1 Per-Step Execution

For each step in the plan:

1. **Build step-specific messages:**
   - System: `build_step_system_prompt(condensed_context, naming_conventions, name_registry)`
   - User: `build_step_user_message(step, completed_descriptions, total_steps, step_artifacts)`

2. **Fresh 2-message conversation** per step (system + user) — no cross-step message history

3. **Call `chat_with_tools()`** with:
   - `IMPLEMENTATION_TOOLS`
   - `max_turns` and `max_tokens` from settings
   - All callbacks (tool_call, tool_result, content, thinking, metrics)
   - Dispatcher for cancel/interrupt

4. **Collect artifacts:** Extract file content from create_file/edit_file results for cross-step context. Budget: 10% of active context window (in chars).

5. **Send checkpoint:** WebSocket message with step index, description, status, commit SHA

### 9.2 Step Prompt Construction

**System prompt** includes:
- Base `STEP_EXECUTION_SYSTEM_PROMPT`
- Project context (condensed, percentage-based budget)
- Naming conventions: "All new code MUST follow these conventions."
- Name registry: "Use EXACTLY these names for all new entities. Do NOT invent alternative names..."

**User message** includes:
- "STEP X OF Y" header
- Completed steps so far
- Tool, file_path, instruction from plan
- Context from planner investigation
- Files from previous steps (up to 8000 chars per file):
  - Searches for path matches in instruction/context
  - Falls back to last 3 created files
- Explicit directive based on tool type:
  - `run_tests/run_lint/format_code`: "Call {tool} with the command specified"
  - `edit_file`: "Read {file_path} first if context incomplete, then call edit_file"
  - `create_file`: "Call create_file to create {file_path} with the content"

### 9.3 TDD Three-Phase Execution

When `settings.enable_tdd=True` and expert model is configured:

**Phase A — Expert Writes Tests:**
- Expert model executes `plan.tdd_test_steps` using IMPLEMENTATION_TOOLS
- Full tool access (create_file, edit_file, etc.)

**Phase B — Primary Reviews Tests:**
- Primary model reviews all test files
- Uses `build_tdd_review_prompt()` with test file contents
- Tools: `build_tdd_implementation_tools()` (standard + `request_test_change`)
- Can dispute tests via `request_test_change(test_file, test_function, reason)`
- Each dispute triggers `evaluate_test_dispute()`:
  - Expert reads test, evaluates reason
  - Returns "ACCEPTED: {fix explanation}" or "REJECTED: {why test is correct}"
  - Capped at `tdd_max_disputes_per_step` per step

**Phase C — Primary Implements Code:**
- Primary model executes `plan.steps` with test files write-protected
- `tdd_protect_tests=True` blocks `create_file`/`edit_file` on test files
- No disputes — must adapt to tests as written (disputes are only in Phase B)
- Post-validation fix loop retains dispute capability as safety net

### 9.4 TDD Dispute Evaluation

```python
async def evaluate_test_dispute(
    *, test_file, test_function, reason, repo_root,
    expert_client, ws, session_id, dispatcher,
    plan_context, step_artifacts
) -> str
```

- Creates expert session with `DISPUTE_EVALUATION_PROMPT`
- 10-turn budget with IMPLEMENTATION_TOOLS
- Artifact context: up to 5 files, 4000 chars each
- Result: "DISPUTE ACCEPTED — {explanation}" or "DISPUTE REJECTED — {explanation}"

---

## 10. Post-Validation System

### 10.1 Command Detection and Resolution

`_effective_post_commands(repo_root)` resolves commands by priority:
1. Manual env vars (`LEAN_AI_POST_*_COMMAND`) — highest priority
2. Auto-detected from `.lean_ai/commands.json` — fallback
3. Empty string — disabled

Returns dict: `{"format": str, "lint_fix": str, "lint": str, "test": str}`

### 10.2 Deterministic Validation Pass

`_run_post_validation(repo_root, ws)`:

1. **Auto-fix passes** (sequential, modify files):
   - Format command (e.g., `ruff format src/`)
   - Lint-fix command (e.g., `ruff check --fix src/`)

2. **Reporting passes** (parallel via `asyncio.gather`, read-only):
   - Lint command (e.g., `ruff check src/`)
   - Test command (e.g., `pytest tests/ -x -q`)

3. **Result:** Dict with keys `format`, `lint_fix`, `lint`, `test` — each containing `{success: bool, output: str, full_output: str}`

### 10.3 Validation Fix Loop

`_run_validation_fix_loop(repo_root, ws, llm_client, context, validation_results, ...)`:

- Up to `post_validation_max_retries` attempts (default 2)
- Each attempt:
  1. Extract failed commands + output (tail last 80 lines if > 8000 chars)
  2. Build focused fix prompt: re-run failing command → diagnose → fix → re-run to verify
  3. Call `chat_with_tools()` with **hardcoded 30-turn budget** (independent of `implementation_max_turns`)
  4. Re-run `_run_post_validation()` to check fixes
- **File-scoped:** Tool executor restricts `edit_file` to the plan's `affected_files` (or fix mode's `files_modified`). New file creation is still allowed.
- **Expert escalation:** On the final retry, use expert model if configured
- **TDD support:** Test files remain protected, `request_test_change` available

---

## 11. WebSocket Dispatcher

### 11.1 WSMessageDispatcher Class

```python
class WSMessageDispatcher:
    def __init__(self, ws: WebSocket):
        self._cancel_event: asyncio.Event
        self._user_messages: asyncio.Queue[dict]   # Interrupt queue
        self._approval_queue: asyncio.Queue[dict]   # Approval/response queue
        self._listener_task: asyncio.Task | None
        self._execution_mode: bool = False

    async def start() -> None          # Spawn background listener
    async def stop() -> None           # Cancel listener
    def enter_execution_mode() -> None # Switch routing mode

    # Background listener
    async def _listen() -> None        # Routes messages to queues

    # Cancellation
    @property
    def is_cancelled -> bool
    def check_cancelled() -> None      # Raises WorkflowCancelledError

    # Approval
    async def wait_for_approval() -> dict | None

    # User interrupts
    def get_pending_message() -> str | None  # Non-blocking
```

### 11.2 Two Routing Modes

| Mode | `user_message` routes to | When active |
|------|--------------------------|-------------|
| **Approval** (default) | `_approval_queue` | During clarification, plan approval |
| **Execution** (after `enter_execution_mode()`) | `_user_messages` (interrupt queue) | During implementation, fix execution |

Other message types always go to `_approval_queue`: `approve`, `approve_tool`, `deny_tool`, etc.

### 11.3 Cancellation Flow

- `cancel` message → sets `_cancel_event` + puts cancel in approval queue
- `check_cancelled()` called at start of each tool loop turn
- Raises `WorkflowCancelledError` → caught in `routers/workflow.py`
- WebSocket disconnect also triggers cancel

---

## 12. Tool System

### 12.1 Tool Definitions (OpenAI-Compatible JSON Schema)

14 tools defined in `tool_definitions.py`:

| Tool | Parameters | Required | Description |
|------|-----------|----------|-------------|
| `create_file` | `path: str`, `content: str` | both | Create new file with content |
| `edit_file` | `path: str`, `search: str`, `replace: str` | all | Find-and-replace in existing file |
| `read_file` | `path: str`, `start_line?: int`, `end_line?: int` | path | Read file with line numbers (500-line default) |
| `run_tests` | `command: str` | command | Execute test command |
| `run_lint` | `command: str` | command | Execute lint command |
| `format_code` | `command: str` | command | Execute code formatter |
| `list_directory` | `path?: str`, `max_entries?: int` | none | List directory contents (default 100) |
| `directory_tree` | `path?: str`, `max_depth?: int` | none | Recursive file tree (default depth 3, max 200 entries) |
| `grep_files` | `pattern: str`, `file_glob?: str` | pattern | Regex search in files (gitignore-aware) |
| `update_scratchpad` | `content: str` | content | Save progress notes (max 2000 chars) |
| `search_internet` | `query: str` | query | Web search via configured provider |
| `fetch_url` | `url: str` | url | Fetch URL, strip HTML, paginate if large |
| `task_complete` | `summary: str` | summary | Signal task completion |
| `request_test_change` | `test_file: str`, `test_function: str`, `reason: str` | all | TDD: dispute a test (TDD mode only) |

### 12.2 Tool Subsets

| Subset | Tools | Used In |
|--------|-------|---------|
| `IMPLEMENTATION_TOOLS` | All 13 standard tools | Plan execution, fix implementation |
| `PLANNING_TOOLS` | read_file, list_directory, directory_tree, grep_files, task_complete | Planning Phase 2 |
| `INVESTIGATION_TOOLS` | read_file, list_directory, directory_tree, grep_files, run_tests, run_lint, search_internet, fetch_url, update_scratchpad, task_complete | Fix mode investigation |
| `build_tdd_implementation_tools()` | IMPLEMENTATION_TOOLS + request_test_change | TDD implementation phase |

### 12.3 Tool Executor Factory

```python
def make_tool_executor(
    repo_root, ws, session_id="", llm_client=None,
    dispatcher=None, tdd_protect_tests=False, on_test_dispute=None
) -> Callable[[str, dict], Awaitable[str]]
```

Returns async closure `execute(name, arguments) -> str`.

### 12.4 File Operations

**Path traversal protection** (`_safe_resolve`):
- Resolves path under `repo_root`
- Rejects `../` escape attempts
- Rejects symlinks pointing outside repo root
- Returns `None` on violation

**`create_file(path, content, repo_root)`:**
- Creates parent directories
- Generates diff (empty → new)
- Returns success message or error

**`edit_file(path, search, replace, repo_root)`:**
- Exact match first: `search in original`
- Fuzzy match fallback (`_fuzzy_search_replace`):
  - Pass 1: trailing whitespace normalization
  - Pass 2: full strip + re-indentation
- On failure: `_find_closest_match()` returns diagnostic snippet
- 2MB file size guard

**`read_file(path, repo_root, start_line?, end_line?)`:**
- 2MB size guard
- UTF-8 decoding (binary files rejected)
- Line numbers (4-char prefix)
- Auto-truncation at 500 lines

**`grep_files(pattern, repo_root, file_glob?, max_results=100, context_lines=1)`:**
- Gitignore-aware tree walk
- Case-insensitive regex matching
- Context lines before/after match
- Max 100 file results

### 12.5 Shell Command Execution

```python
async def _run_command(cmd: str, cwd: str) -> ToolResult:
    # asyncio.create_subprocess_shell()
    # Captures stdout + stderr combined
    # Timeout: settings.tool_timeout_seconds (default 60)
    # Kills process on timeout
```

Wrapper functions: `run_tests()`, `run_lint()`, `format_code()` — all delegate to `_run_command()`.

### 12.6 Command Safety Gate

```python
class CommandRisk(Enum):
    SAFE = "safe"
    REQUIRES_APPROVAL = "requires_approval"
    ALWAYS_BLOCK = "always_block"

def check_command(command: str) -> tuple[CommandRisk, str]
```

**ALWAYS_BLOCK** (refused outright):
- `rm -rf /`, `rm -fr /`, `rm -rf ~`, `rm -fr ~`
- `format c:`, `format d:`
- `dd of=/dev/sd`, `dd of=/dev/hd`, `dd of=/dev/nvme`
- `chmod 777 /`, `mkfs /dev/`

**REQUIRES_APPROVAL** (user must confirm):
- `rm `, `del `, `erase `, `rmdir `, `shred `, `truncate `
- `mkfs `, `dropdb `, `drop database`
- `chmod `, `chown `
- `killall `, `pkill `, `kill -9`
- `poweroff`, `reboot`, `shutdown`
- `git push`, `git reset --hard`, `git clean`
- `npm publish`, `pip uninstall`

### 12.7 Long Output Handling

- **Inline limit:** 2000 chars
- **Over limit:** Save to `.lean_ai/tool_output/{tool}_{timestamp}.txt`
- **Auto-cleanup:** Files older than 1 hour deleted on next save
- **Failure output:** Returns "FAILED" prefix + last 40 lines + file path for full output

### 12.8 Internet Search and URL Fetch

**`search_internet(query, llm_client)`:**
- Rate-limited with configurable delay + random jitter (0-100%)
- Browser providers (Google/Bing): 4x delay multiplier, floor 8s
- Dispatches to configured provider (duckduckgo, searxng, google, bing)
- Google auto-falls back to Bing on failure
- Sanitizes HTML from results

**`fetch_url(url, repo_root, llm_client)`:**
- HTTP GET with Mozilla User-Agent
- Max 500KB content
- Strips HTML via BeautifulSoup
- Large pages (500+ lines): saves to `.lean_ai/fetched/{hash}.txt`, returns preview

### 12.9 Scratchpad

Per-session state file at `.lean_ai/scratchpads/{session_id}.md`:

```python
SCRATCHPAD_CONTEXT_PERCENT = 0.05  # 5% of context window
_max_scratchpad_chars() = 5% of context_window × 3.5  # chars approximation

async def update_scratchpad(content, repo_root, session_id) -> ToolResult
def read_scratchpad(repo_root, session_id) -> str
def delete_scratchpad(repo_root, session_id) -> None
```

### 12.10 Git Operations

All async, implemented via `asyncio.create_subprocess_shell`:

| Function | Description |
|----------|-------------|
| `git_commit(message, files?, repo_root)` | Stage + commit |
| `git_status(repo_root)` | `git status --porcelain` |
| `git_diff(repo_root, staged?)` | `git diff` or `--cached` |
| `git_current_branch(repo_root)` | Current branch name |
| `git_current_sha(repo_root)` | Current commit SHA |
| `git_is_repo(repo_root)` | Check if git repo |
| `git_create_branch(name, repo_root, start_point?)` | Create + checkout branch |
| `git_default_branch(repo_root)` | Detect main/master (remote HEAD → local check → "main") |
| `git_checkout(name, repo_root)` | Checkout existing branch |
| `git_merge_branch(name, repo_root)` | Merge branch into current |
| `git_delete_branch(name, repo_root, force?)` | Delete branch (-d or -D) |
| `git_stash_push(repo_root)` | Stash including untracked. Returns True if stashed |
| `git_stash_pop(repo_root)` | Pop latest stash |
| `git_add_and_commit(message, repo_root)` | Stage all + commit |

---

## 13. Router Layer & API Endpoints

### 13.1 Router Aggregation

`router.py` aggregator includes all sub-routers. All mounted at `/api` prefix via `app.include_router(router, prefix="/api")`.

### 13.2 Request/Response Models

All defined in `routers/models.py` as Pydantic `BaseModel` classes:

```python
# Session Management
class CreateSessionRequest:     repo_root: str; task: str = ""
class CreateSessionResponse:    session_id: str; status: str
class ResumeSessionRequest:     repo_root: str

# Workspace
class InitWorkspaceRequest:     repo_root: str; force_reindex: bool = False
class InitWorkspaceResponse:    index_status: str; index_file_count: int | None; index_chunk_count: int | None; commands_detected: dict | None; num_parallel: int = 1

# Context Generation
class GenerateProjectContextRequest:   repo_root: str; skip_if_exists: bool = True
class GenerateProjectContextResponse:  path: str; chars: int; skipped: bool = False
class GenerateFrameworkGuideRequest:   repo_root: str; skip_if_exists: bool = False
class GenerateFrameworkGuideResponse:  path: str; chars: int; skipped: bool = False
class GenerateStyleGuideRequest:       repo_root: str; skip_if_exists: bool = False
class GenerateStyleGuideResponse:      path: str; chars: int; skipped: bool = False

# Chat
class WorkspaceContext:   workspace_name?: str; workspace_root?: str; active_file?: str; active_language?: str; active_selection?: str
class Attachment:         data: str; filename?: str; mime_type?: str
class ChatRequest:        message: str; history: list[dict] = []; workspace?: WorkspaceContext; attachments: list[Attachment] = []; user_name?: str; skip_web_search: bool = False
class ChatResponse:       reply: str; tokens_per_second?: float; eval_count?: int; refined: bool = False; privacy_redactions: int = 0

# Inline Prediction
class InlinePredictRequest:   file_path: str; language: str; prefix: str; suffix: str; cursor_line: int; cursor_character: int

# Scaffolding
class ScaffoldRequest:     scaffold_name: str; project_name: str; parent_dir: str
class ScaffoldResponse:    scaffold_name: str; project_dir: str; files_created: list[str]; command_output: str; message: str
class ScaffoldInfo:        name: str; display_name: str; description: str; language: str; framework?: str; aliases: list[str]; setup_type: str
class ScaffoldListResponse: scaffolds: list[ScaffoldInfo]

# Reference Library
class IndexReferenceRequest:   repo_root: str; force_reindex: bool = False
class IndexReferenceResponse:  status: str; doc_count: int = 0; chunk_count: int = 0

# Models
class ModelInfo:      provider: str; model: str; display_name: str; is_default: bool
class ModelsResponse: models: list[ModelInfo]; default_provider: str; default_model: str

# Voice
class STTStartRequest:       auto_stop: bool = False
class STTStopResponse:       text: str; language?: str; duration_seconds: float = 0.0
class TTSRequest:            text: str; voice: str = ""; speed: float = 0.0
class TTSResponse:           audio_base64: str; duration_seconds: float = 0.0
class VoiceConfigRequest:    voice: str = ""; speed: float = 0.0
class VoiceInfo:             id: str; name: str; language: str; gender?: str
class VoiceListResponse:     voices: list[VoiceInfo]
class EnsureModelsResponse:  downloaded: bool = False; already_cached: bool = False; size_mb: float = 0.0
class VoiceStatusResponse:   stt_available: bool = False; tts_available: bool = False; wake_word_available: bool = False; setup: dict = {}
```

### 13.3 Complete Endpoint Reference

| Router | Method | Path | Request | Response |
|--------|--------|------|---------|----------|
| Sessions | POST | `/sessions` | CreateSessionRequest | CreateSessionResponse |
| Sessions | GET | `/sessions` | query: repo_root | list[SessionSummary] |
| Sessions | GET | `/sessions/{id}` | query: repo_root | SessionSummary |
| Sessions | DELETE | `/sessions/{id}` | query: repo_root | {status, session_id} |
| Sessions | GET | `/sessions/{id}/conversation` | query: repo_root | {session_id, entries} |
| Sessions | GET | `/sessions/{id}/checkpoints` | — | [] (stub) |
| Sessions | GET | `/sessions/{id}/git-events` | — | [] (stub) |
| Sessions | GET | `/sessions/search` | query: repo_root, q, commit | list[SessionSummary] |
| Sessions | POST | `/sessions/{id}/resume` | ResumeSessionRequest | {status, session_id, branch_name, scratchpad_exists} |
| Workflow | WS | `/sessions/{id}/stream` | WebSocket | Streaming messages |
| Workflow | POST | `/sessions/{id}/merge` | query: repo_root | {status, merge_sha, branch_deleted} |
| Workflow | POST | `/sessions/{id}/abandon` | query: repo_root | {status, branch_deleted} |
| Generation | POST | `/init-workspace` | InitWorkspaceRequest | InitWorkspaceResponse |
| Generation | POST | `/generate-project-context` | GenerateProjectContextRequest | GenerateProjectContextResponse |
| Generation | POST | `/generate-framework-guide` | GenerateFrameworkGuideRequest | GenerateFrameworkGuideResponse |
| Generation | POST | `/generate-style-guide` | GenerateStyleGuideRequest | GenerateStyleGuideResponse |
| Chat | POST | `/chat` | ChatRequest | ChatResponse |
| Chat | POST | `/chat/stream` | ChatRequest | SSE (text/event-stream) |
| Info | GET | `/models` | — | ModelsResponse |
| Info | POST | `/predict` | InlinePredictRequest | {completion, confidence, error?} |
| Info | GET | `/health` | — | {status, vision/stt/tts/wake_word_available} |
| Voice | POST | `/voice/stt/start` | STTStartRequest | {status} |
| Voice | POST | `/voice/stt/stop` | — | STTStopResponse |
| Voice | POST | `/voice/stt/warmup` | — | {status} |
| Voice | POST | `/voice/tts` | TTSRequest | TTSResponse |
| Voice | POST | `/voice/tts/stream` | TTSRequest | SSE |
| Voice | GET | `/voice/tts/voices` | — | VoiceListResponse |
| Voice | POST | `/voice/tts/ensure-models` | — | EnsureModelsResponse |
| Voice | POST | `/voice/config` | VoiceConfigRequest | {voice, speed} |
| Voice | POST | `/voice/wakeword/start` | — | {status} |
| Voice | POST | `/voice/wakeword/stop` | — | {status} |
| Voice | GET | `/voice/events` | — | SSE |
| Voice | GET | `/voice/status` | — | VoiceStatusResponse |
| Scaffold | GET | `/scaffold/list` | — | ScaffoldListResponse |
| Scaffold | POST | `/scaffold` | ScaffoldRequest | ScaffoldResponse |
| Reference | POST | `/index-reference` | IndexReferenceRequest | IndexReferenceResponse |

### 13.4 WebSocket Workflow Handler Logic

The workflow WebSocket handler (`routers/workflow.py`) on receiving `user_message`:

1. **Git setup:** Stash uncommitted changes → checkout default branch → create `lean-ai/{session_id}` branch
2. **Vision processing:** If attachments contain images, call vision model to describe them
3. **Task refinement:** If refiner configured, run local Ollama refinement (privacy + reference library)
4. **Start dispatcher:** `WSMessageDispatcher` for cancel/interrupt routing
5. **Run workflow:** `run_workflow(task, mode, ...)` with context + LLM clients
6. **Auto-commit:** After completion, `git add + commit` all changes
7. **Log commit SHA + message** to database
8. **Error handling:** `WorkflowCancelledError` → send "cancelled"; other errors → send "error"

On `resume` message: Runs in fix mode (no re-planning) with existing branch.

### 13.5 Chat Router Context Gathering

The `/chat` endpoint gathers context in parallel via `asyncio.gather`:
1. File tree (50 files max)
2. Project context (project_context.md + framework_guide.md, 20K chars max)
3. Active file content (3K chars max)
4. Code search results (8 snippets via Whoosh)
5. Web search results (if applicable, keywords extracted from message)
6. URL content (first 3 URLs in message)
7. Reference library results (via refiner)
8. Image descriptions (via vision model)
9. Message refinement (via refiner)

---

## 14. WebSocket Protocol

### 14.1 Client-to-Server Messages

| Type | Fields | Description |
|------|--------|-------------|
| `user_message` | `content: str`, `repo_root: str`, `attachments?: [{data, mime_type}]` | Start workflow or provide feedback |
| `cancel` | — | Stop running workflow |
| `approve` | — | Approve pending plan |
| `approve_tool` | — | Approve pending shell command |
| `deny_tool` | — | Deny pending shell command |
| `ping` | — | Keepalive |
| `resume` | `repo_root: str` | Resume paused session |

### 14.2 Server-to-Client Messages

| Type | Key Fields | Description |
|------|-----------|-------------|
| `token` | `data: str` | Legacy streaming token |
| `stage_change` | `stage, previous_stage` | Workflow stage transition |
| `stage_status` | `stage, status ("running"\|"done"\|"needs_input"), summary?, model?, phase?` | Stage-level progress |
| `approval_required` | `artifact_id, artifact_type, content_preview` | Plan awaiting user approval |
| `clarification_needed` | `questions: str[], improved_prompt?` | LLM needs clarification |
| `plan_revision` | `review_feedback, revision_number` | Plan revised per feedback |
| `plan_rejected` | `feedback, stage` | Plan being revised |
| `tool_progress` | `tool_name, status ("started"\|"running"\|"completed"\|"failed"), error?` | Tool execution status |
| `tool_approval_required` | `tool_name, command, reason` | Shell command needs approval |
| `diff` | `file_path, diff` | File modification diff |
| `test_result` | `passed: bool, output` | Test run result |
| `error` | `message, recoverable: bool` | Error (closes WS if !recoverable) |
| `complete` | `summary, files_modified?, plan_branch?, base_branch?, merge_commit_sha?` | Workflow finished |
| `cancelled` | — | User cancelled |
| `index_status` | `status ("indexing"\|"ready"), progress?` | Indexing progress |
| `branch_created` | `branch_name, base_branch, base_commit_sha` | Git branch created |
| `checkpoint` | `step_index, step_description, status, head_commit_sha?` | Step completion |
| `merge_complete` | `merge_sha, branch_deleted` | Branch merged |
| `assistant_content` | `content, streaming?: bool, done?: bool` | LLM text output |
| `thinking_content` | `content, streaming?: bool, done?: bool` | LLM reasoning trace |
| `metrics_update` | `context_percent, prompt_tokens, context_window` | Context usage |
| `context_refreshed` | `message` | Context rebuilt |
| `pong` | — | Keepalive response |

**Streaming flags:**
- `streaming: true` — token-level update (append to current content)
- `done: true` — content finalization (full text for markdown rendering)

### 14.3 Plan Mode Lifecycle Sequence

```
Client                          Server
  |                               |
  |── user_message ──────────────>|
  |                               |── stage_change: CLARIFICATION
  |                               |── stage_status: planning/running
  |<────── clarification_needed ──| (if needed)
  |── user_message (answers) ────>|
  |                               |── stage_change: PLAN_CREATION
  |                               |── thinking_content (streaming)
  |                               |── assistant_content (streaming)
  |                               |── stage_status: planning phases
  |<────── approval_required ─────|
  |                               |
  |── approve ───────────────────>| (or user_message for feedback)
  |                               |── stage_change: IMPLEMENTATION
  |                               |── tool_progress (per tool)
  |                               |── diff (per file change)
  |                               |── checkpoint (per step)
  |                               |── metrics_update (periodic)
  |                               |── stage_status: post_validation
  |                               |── test_result (per test run)
  |<────── complete ──────────────|
```

### 14.4 Fix Mode Lifecycle Sequence

```
Client                          Server
  |                               |
  |── user_message ──────────────>|
  |                               |── branch_created
  |                               |── stage_change: investigating
  |                               |── tool_progress (read-only tools)
  |                               |── thinking_content
  |                               |── stage_change: implementing
  |                               |── tool_progress (full tools)
  |                               |── diff (per change)
  |                               |── test_result
  |<────── complete ──────────────|
```

---

## 15. Indexer System

### 15.1 Gitignore-Aware Tree Walker

```python
@dataclass
class FileEntry:
    path: str       # Relative to repo root
    size: int
    extension: str

def list_repo_tree(repo_root: str) -> list[FileEntry]
```

**Filters:**
- `SKIP_DIRS`: 30+ excluded directories (`.git`, `node_modules`, `.pytest_cache`, `__pycache__`, `venv`, `dist`, `.lean_ai_index`, etc.)
- `BINARY_EXTENSIONS`: 40+ binary extensions (`.png`, `.mp3`, `.exe`, `.pyc`, `.db`, `.woff`, etc.)
- `SKIP_FILES`: Lock files (`package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`)
- Gitignore patterns via `pathspec.PathSpec.from_lines("gitwildmatch", ...)` — cached by root + mtime

### 15.2 Tree-Sitter AST Chunking

```python
def chunk_file(content: str, file_path: str,
               max_lines: int = None,       # default: settings.chunk_max_lines (50)
               overlap_lines: int = None     # default: settings.chunk_overlap_lines (10)
              ) -> list[dict]
```

**Algorithm:**
1. Detect language from file extension via language registry
2. If AST parser available: extract function/class boundaries via `get_definition_nodes()`
3. If boundaries found: `_chunk_by_boundaries()` — groups consecutive definitions into chunks fitting max_lines, with overlap at boundaries
4. Fallback: `_chunk_by_lines()` — sliding window with configurable overlap

**Return format:** `[{content, start_line, end_line, language}]`

### 15.3 Whoosh BM25F Search Index

**Schema:**
```python
INDEX_SCHEMA = Schema(
    chunk_id=ID(stored=True, unique=True),
    file_path=TEXT(stored=True),
    content=TEXT(stored=True),
    language=ID(stored=True),
    start_line=NUMERIC(stored=True),
    end_line=NUMERIC(stored=True),
)
```

**Index location:** `{repo_root}/{settings.index_dir}` (default `.lean_ai_index`)

**Functions:**
```python
def index_workspace(repo_root, force=False) -> tuple[int, int]  # (file_count, chunk_count)
def search_index(repo_root, query, limit=20, query_embedding=None) -> list[dict]
async def generate_embeddings(repo_root, llm_client, batch_size=32) -> int
```

**Indexing modes:**
- **Full index** (`force=True` or no existing index): wipe + rebuild from scratch
- **Incremental index:** compare SHA-256 manifest, only process added/modified/deleted files

### 15.4 Embedding Store

Binary storage at `.embeddings.bin` + JSON sidecar `.embeddings_index.json`:

```python
class EmbeddingStore:
    def save_batch(self, chunk_ids: list[str], embeddings: list[list[float]]) -> None
    def flush_index(self) -> None
    def get_embedding(self, chunk_id: str) -> list[float] | None
    def get_all_embeddings(self) -> dict[str, list[float]]
    def clear(self) -> None
```

**Binary format:** Each vector stored as `struct.pack(f"{dim}f", *vec)`. Index maps chunk_id → `{offset, dim}`.

### 15.5 RRF Hybrid Re-Ranking

```python
def semantic_rerank(bm25_results, query_embedding, store, k=60, w_bm25=1.0, w_sem=1.0) -> list[dict]
```

**Algorithm (Reciprocal Rank Fusion):**
1. Build BM25 rank mapping from search results
2. Compute cosine similarity of all embeddings with query embedding
3. Build semantic rank mapping
4. RRF score = `w_bm25 / (k + bm25_rank) + w_sem / (k + sem_rank)`
5. Penalize unseen chunks: `k + len(results) + 100`
6. Return re-ranked results

### 15.6 SHA-256 Manifest

```python
@dataclass
class FileRecord:
    sha256: str
    chunk_count: int = 0

@dataclass
class Manifest:
    version: int = 1
    created_at: str = ""
    commit_hash: str = ""
    files: dict[str, FileRecord] = field(default_factory=dict)

@dataclass
class DiffResult:
    added: list[str]
    modified: list[str]
    deleted: list[str]
    unchanged: list[str]

def compute_diff(current_files: dict[str, str], old_manifest: Manifest) -> DiffResult
```

---

## 16. Context Generation

### 16.1 Project Context Generation

```python
async def generate_project_context(repo_root: str, llm_client: LLMClient) -> str
```

**Strategy selection:** Based on context window size — single-pass or multi-round.

**Single-pass:** One large prompt with priority files → one LLM call. Suitable for context >= 64K.

**Multi-round:**
1. Round 1: standard generation with priority files
2. Remaining rounds: additive expansion with batched files
3. Each round starts fresh (no conversation history)

**Post-processing:**
- Deduplication: removes duplicate `##` and `###` headings
- Repetition truncation: detects repeated lines/phrases
- Section normalization: strips parenthetical qualifiers from headings

### 16.2 Multi-Round Expansion

```python
async def _expand_project_context(base_doc, repo_root, llm_client, caps, max_out, ctx_window) -> str
```

- Identifies already-covered files from initial generation
- Collects remaining files ranked by fan-in (import frequency)
- Batches by character budget
- Fires all batches concurrently via `asyncio.gather` (throttled by `num_parallel` semaphore)
- Merges additions into base document by section heading matching

### 16.3 Framework Guide Generation

```python
async def generate_framework_guide(repo_root: str, llm_client: LLMClient, max_tokens: int = None) -> str
```

**6-step pipeline:**
1. Detect frameworks from dependency files
2. Generate search queries via LLM
3. Web search for best practices (sequential, rate-limited)
4. Fetch top URL per category (32KB total budget)
5. LLM generates guide from search results + project tree
6. Post-processing: repair code blocks, dedup sections, validate file paths (3-phase), renumber steps, fill empty subsections

### 16.4 Style Guide Generation

Extracts CSS/template patterns from the codebase for visual consistency documentation.

### 16.5 Command Detection

```python
def detect_commands(repo_root: str) -> dict[str, str]
```

**Detection order:** PHP → Python → Node/TS → Ruby → Go → Rust → Java → C#

Returns: `{"format": "...", "lint_fix": "...", "lint": "...", "test": "..."}`

**Language-specific detection:**
- **PHP:** `composer.json` → `phpunit`, `pint`, `php -l`
- **Python:** `pyproject.toml` → `ruff`, `black`, `pytest`
- **Node/TS:** `package.json` scripts or devDependencies → `eslint`, `prettier`, `jest/vitest/mocha`
- **Ruby:** `Gemfile` → `rubocop`, `rspec`, `rails test`
- **Go:** `go.mod` → `gofmt`, `go vet`, `go test`
- **Rust:** `Cargo.toml` → `cargo fmt`, `cargo clippy`, `cargo test`
- **Java:** `pom.xml`/`build.gradle` → `mvn test`/`gradle test`
- **C#:** `*.csproj` → `dotnet format`, `dotnet build`, `dotnet test`

Written to `.lean_ai/commands.json` for fallback when env vars empty.

### 16.6 Custom Steering Documents

`.lean_ai/context/*.md` files loaded alphabetically after generated files. Budget: 40% of condensed context allocation reserved for custom docs.

### 16.7 Incremental Context Update

```python
async def update_project_context(repo_root, modified_paths, llm_client) -> str | None
```

After workflow completion: reads modified files, calls LLM with additive expansion prompt, merges into existing `project_context.md`.

---

## 17. Language Registry

### 17.1 YAML Language Definitions

13 languages defined in YAML files:

```yaml
# Example: python.yaml
name: Python
extensions: [".py"]
ts_grammar: tree_sitter_python
ts_class_query: "(class_definition name: (identifier) @name) @def"
ts_function_query: "(function_definition name: (identifier) @name) @def"
ts_import_query: "(import_statement) @import"
stdlib_prefixes: ["os", "sys", "json", "pathlib", ...]
fan_in:
  strategy: dot_to_slash
  suffix: ".py"
  package_markers: ["__init__.py"]
test_patterns:
  directories: ["tests", "test"]
  file_prefixes: ["test_"]
  file_suffixes: ["_test.py"]
key_files: ["setup.py", "pyproject.toml", "requirements.txt"]
entry_points: ["main.py", "app.py", "__main__.py"]
```

**Languages:** Python, JavaScript, TypeScript, Java, Go, Rust, Ruby, C, C++, C#, PHP, CSS, HTML

### 17.2 Data Classes

```python
@dataclass
class FileTestPatterns:
    directories: list[str]      # e.g., ["tests", "test"]
    file_prefixes: list[str]    # e.g., ["test_"]
    file_suffixes: list[str]    # e.g., ["_test.py"]

@dataclass
class FanInConfig:
    strategy: str               # "dot_to_slash", "relative_path", "none"
    suffix: str                 # e.g., ".py"
    package_markers: list[str]  # e.g., ["__init__.py"]

@dataclass
class LanguageDefinition:
    name: str
    extensions: list[str]
    ts_grammar: str
    ts_class_query: str
    ts_function_query: str
    ts_import_query: str
    stdlib_prefixes: list[str]
    fan_in: FanInConfig
    test_patterns: FileTestPatterns
    key_files: list[str]
    entry_points: list[str]
```

### 17.3 Registry

```python
class LanguageRegistry:
    def get_language(self, ext: str) -> LanguageDefinition | None
    def all_source_extensions(self) -> set[str]
    def all_languages(self) -> list[LanguageDefinition]
    def is_test_file(self, path: str) -> bool

@lru_cache(maxsize=1)
def get_registry() -> LanguageRegistry  # Singleton
```

### 17.4 Test File Detection

```python
def is_test_file_path(path: str) -> bool
```

Checks language registry test patterns first. Fallback conventions:
- Directories: `tests/`, `test/`, `__tests__/`
- File prefixes: `test_`
- File suffixes: `_test.py`, `.test.ts/tsx/js/jsx`, `.spec.ts/tsx/js/jsx`

---

## 18. Reference Library

### 18.1 Document Readers

```python
class DocumentReader(ABC):
    @abstractmethod
    def read(self, path: Path) -> str: ...
```

**Implementations:**
- `TextReader` — plain `.txt` files
- `MarkdownReader` — `.md` files (passed through)
- `HtmlReader` — `.html` files (stripped to text via BeautifulSoup)
- `EpubReader` — `.epub` files (requires `ebooklib`)
- `PdfReader` — `.pdf` files (requires `pypdf`)
- `DocxReader` — `.docx` files (requires `python-docx`)

**Registry:** Maps file extensions to readers. Factory function returns appropriate reader.

### 18.2 Prose-Aware Paragraph Chunking

```python
def chunk_prose(text: str, target_chars: int = 800, overlap_chars: int = 150) -> list[str]
```

**Algorithm:**
1. Split on blank lines (`\n\n`) → paragraphs
2. Accumulate paragraphs until `target_chars` budget full
3. Emit chunk, carry trailing paragraphs fitting within `overlap_chars` to next chunk
4. Hard-split oversized paragraphs (> 2 × target) on line boundaries

### 18.3 Reference Library Indexer

```python
REFERENCE_SCHEMA = Schema(
    chunk_id=ID(stored=True, unique=True),
    doc_path=ID(stored=True),
    doc_title=TEXT(stored=True),
    section=TEXT(stored=True),
    content=TEXT(stored=True),
    format=ID(stored=True),
    chunk_index=NUMERIC(stored=True),
)

def index_reference(repo_root: str) -> dict  # Synchronous
def search_reference(repo_root: str, query: str, limit: int = 10) -> list[dict]
def is_reference_available(repo_root: str) -> bool
```

**Index location:** `{repo_root}/{settings.reference_index_dir}` (default `.lean_ai_reference_index`)
**Documents location:** `{repo_root}/{settings.reference_dir}` (default `.lean_ai/reference`)

**Supported formats:** `.epub`, `.pdf`, `.docx`, `.md`, `.html`, `.rst`, `.txt`

Uses same SHA-256 manifest pattern as code indexer for incremental updates.

---

## 19. Scaffolding System

### 19.1 YAML Recipe Format

```yaml
name: python-fastapi         # Unique identifier
display_name: FastAPI         # Human-readable name
description: "..."            # One-line description
language: python
framework: fastapi            # Optional
aliases: ["fastapi"]          # Alternative names
setup:
  type: files                 # "files" or "command"
  directories:                # Directories to create
    - src/{package_name}
    - tests
  files:                      # Files to create with content
    - path: src/{package_name}/main.py
      content: |
        from fastapi import FastAPI
        app = FastAPI(...)
```

### 19.2 Available Scaffolds

19 recipes covering: Python (FastAPI, Flask, Django, CLI, library), Node (Express, Next.js, React, Vue), Go, Rust, Java (Spring), C# (.NET), C++, C, PHP (Laravel), Ruby (Rails), Ansible.

### 19.3 Scaffold Execution

```python
async def create_scaffold(scaffold_name, project_name, parent_dir) -> ScaffoldResult
```

- **Files-based:** Creates directories and files from template, expanding `{package_name}`, `{project_name}` placeholders
- **Command-based:** Runs CLI tool (e.g., `npx create-next-app`, `cargo init`)

---

## 20. Voice System

### 20.1 Audio Manager Singleton

```python
class AudioManager:
    # Coordinates mic access between STT and wake word
    # Only one service holds the mic at a time

    async def start_stt(auto_stop: bool = False) -> None
    async def stop_stt() -> STTResult
    async def start_wake_word(callback: Callable) -> None
    async def stop_wake_word() -> None
    async def synthesize(text: str, voice: str = "", speed: float = 0.0) -> TTSResult
    def list_voices() -> list[VoiceInfo]
    def cleanup() -> None
```

**Mic coordination:** Pauses wake word when STT records, resumes after. Lock prevents simultaneous access.

### 20.2 STT (Speech-to-Text)

- **Engine:** faster-whisper (CTranslate2-based Whisper)
- **Audio:** 16kHz, 1 channel, 16-bit PCM via PyAudio
- **CPU only** (GPU reserved for LLM)
- **Models:** `tiny`, `base`, `small`, `medium`, `large-v3`, `turbo`
- **Silence detection:** RMS-based threshold (500), auto-stop after `stt_silence_threshold` seconds (default 4.0)
- **Beam search:** configurable beam size (1=greedy fastest, 5=most accurate)

### 20.3 TTS (Text-to-Speech)

- **Engine:** kokoro-onnx (ONNX Runtime)
- **CPU only**
- **58 voices** across 9 languages
- **Output:** 24kHz 16-bit PCM → base64-encoded WAV
- **Model variants:** fp32 (~311MB), fp16 (~169MB, default), int8 (~88MB)
- **Auto-download** to `~/.cache/lean_ai/kokoro/` on first use
- **SSE streaming:** Chunks per sentence for long text
- **Speed control:** 0.5–2.0x

### 20.4 Wake Word Detection

- **Engine:** openWakeWord with pre-trained `hey_computer` model
- **Background listener** fires callback on detection
- **SSE events:** `wake_word_detected`, `stt_auto_stopped`, `wake_word_error`

### 20.5 ALSA Suppression

Linux-specific: suppresses noisy ALSA error messages via ctypes error handler override.

---

## 21. Prompt Library

### 21.1 System Prompts

All prompts use capability-first framing (never "You are a..."):

| Prompt | Purpose | Key Directives |
|--------|---------|----------------|
| `SYSTEM_PROMPT` | General context | Produce structured plans, use tools |
| `PLAN_SYSTEM_PROMPT` | Planning phases | Analyze codebase, include scope/steps/risks |
| `IMPLEMENTATION_SYSTEM_PROMPT` | Implementation | Must call tools every response until done |
| `STEP_EXECUTION_SYSTEM_PROMPT` | Per-step executor | Execute EXACTLY as specified, use Name Registry names |
| `FIX_SYSTEM_PROMPT` | Bug fixes | Read files, diagnose, minimal fix, web search for unfamiliar errors |
| `FIX_INVESTIGATION_PROMPT` | Read-only investigation | Understand problem fully, record diagnosis in scratchpad |
| `REQUEST_SYSTEM_PROMPT` | Open-ended tasks | Must call tools, research with search, no test requirement |
| `CLARIFICATION_SYSTEM_PROMPT` | Clarity assessment | Respond "CLEAR" or JSON array of 3-5 questions |
| `CHAT_SYSTEM_PROMPT` | Chat/discussion | Voice-first (no markdown/lists), build "Suggested Agent Prompt" |
| `REFINER_CHAT_PROMPT` | Chat refinement | Preserve intent, add structure, incorporate reference library naturally |
| `REFINER_TASK_PROMPT` | Task refinement | Add technical specificity, don't expand scope |
| `PRIVACY_STRIP_PROMPT` | Sensitivity strip | Redact keys/tokens/hosts/emails/DB strings, keep code structure |
| `DISPUTE_EVALUATION_PROMPT` | TDD dispute | Read test, evaluate reason, ACCEPTED or REJECTED |

### 21.2 Anti-Hallucination Instructions

Used in planning phases 3-4:
- Do NOT simulate running commands
- Do NOT invent file listings
- Do NOT fabricate file contents
- If unsure about file content, say "not yet verified"

### 21.3 LLM Prompt Authoring Standard

```
# Bad
"You are a senior software architect..."

# Good
"Use your knowledge of software architecture to..."
```

---

## 22. VS Code Extension

### 22.1 Extension Activation

`activate()` in `extension.ts`:
1. Auto-start backend Python server (configurable via settings)
2. Register sidebar webview provider (`LeanAISidebarProvider`)
3. Register inline completion provider (`LeanAIInlineProvider`)
4. Register session tree view (`SessionTreeProvider`)
5. Register 13+ commands: approve, reject, focus, restart, session management, settings

### 22.2 package.json Contributions

- **Views container:** Activity bar icon for Lean AI
- **Views:** `chatView` (webview), `sessionsView` (tree)
- **Commands:** 15+ (lean-ai.approve, lean-ai.reject, lean-ai.focus, lean-ai.restartBackend, lean-ai.openSettings, lean-ai.mergeSession, lean-ai.abandonSession, lean-ai.deleteSession, lean-ai.viewSession, lean-ai.refreshSessions, lean-ai.newChat, lean-ai.openChatWindow)
- **Configuration:** 50+ settings mapped to backend env vars
- **Menus:** View title bar buttons, tree item context menus

### 22.3 Sidebar Webview Provider

`LeanAISidebarProvider` — main class coordinating the extension:

- **Two modes:** Chat mode (direct LLM) ↔ Agent mode (FSM with planning/approval)
- **WebSocket lifecycle:** One WS per session, managed by slash command or direct message
- **Chat history:** Persisted via `ConversationManager`
- **Slash command dispatch:** Factory-based registry (11 commands)
- **TTS integration:** Queues audio chunks, plays via HTML5 Audio
- **Context pills:** Problems panel diagnostics, debug console context
- **Detached window:** Can open chat in separate OS window for multi-monitor

### 22.4 Slash Command Registry

| Command | Description |
|---------|-------------|
| `/init` | Index workspace + generate project context + framework guide |
| `/scaffold` | Create new project from recipe |
| `/agent` | Route to full workflow (plan → approve → execute) |
| `/fix` | Skip planning, bug-fix mode |
| `/request` | Skip planning, open-ended task mode |
| `/guide` | Regenerate framework guide |
| `/style` | Generate CSS/template style guide |
| `/reboot` | Restart backend server |
| `/approve` | Approve pending plan (REST fallback if WS unavailable) |
| `/reject` | Reject plan with feedback |
| `/resume` | Resume session from checkpoint |

### 22.5 Backend Client

`BackendClient` singleton with custom HTTP implementation:

- **No timeout HTTP:** Uses raw `http`/`https` Node.js modules (bypasses undici's hardcoded 5-minute `headersTimeout` that kills long LLM calls)
- **WebSocket factory:** Creates WS connections for workflow streaming
- **REST methods:** All CRUD operations, chat, scaffold, voice, health checks
- **SSE support:** Voice events streaming

### 22.6 Inline Completion Provider

```typescript
class LeanAIInlineProvider implements InlineCompletionItemProvider {
    // Copilot-style fill-in-the-middle predictions
    // Debounced at 300ms
    // Captures 50 lines prefix + 20 lines suffix
    // Calls /api/predict endpoint
    // Returns InlineCompletionItem[] or empty on failure
}
```

### 22.7 Session Management

- **`SessionTreeProvider`:** TreeDataProvider showing sessions with status icons (active/completed/failed), checkpoints, git events
- **`SessionDetailProvider`:** Detailed webview for session inspection
- **Operations:** View, merge, abandon, delete, refresh

### 22.8 Settings Panel

- **`SettingsPanel`:** Singleton WebviewPanel for LLM provider configuration
- **`settingsSync.ts`:** Maps UI settings → backend `.env` file via `BACKEND_SETTING_MAP`
- **Secret storage:** API keys stored in OS keychain via VSCode `SecretStorage` API
- **Model discovery:** Queries Ollama `/api/tags` to list available models

### 22.9 Backend Process Management

- **`backendProcess.ts`:** Managed Python/pip/uvicorn process lifecycle
- **Auto-start:** On extension activation (configurable)
- **Restart:** Kills existing process, waits, starts new one
- **`backendInstaller.ts`:** First-run setup — detects Python, creates virtual environment, installs dependencies

### 22.10 Webview HTML

`sidebarHtml.ts` generates the chat UI HTML template including:
- Message rendering with markdown (marked.js), code highlighting
- Approval buttons (approve/reject with feedback)
- File link click handlers (opens in editor)
- Voice controls (mic button, TTS playback, speed control)
- Metrics display (tokens/s, context %)
- Tool approval inline cards
- Stage progress indicators

---

## 23. Cross-Cutting Concerns

### 23.1 Streaming Architecture

```
Provider (Ollama/OpenAI/Anthropic)
    ↓ stream_callback / thinking_callback
LLMClient Facade
    ↓ on_content / on_thinking callbacks
Workflow (pipeline.py / fix_mode.py)
    ↓ ws_send_nowait (fire-and-forget)
WebSocket (routers/workflow.py)
    ↓ JSON messages
Extension (streamHandler.ts)
    ↓ handleStreamMessage → typed callbacks
Sidebar Webview (postMessage)
    ↓ renders in HTML
```

**Streaming flags:**
- `streaming: true` — token-level update (append, for real-time display)
- `done: true` — content finalization (full text for markdown re-render)

### 23.2 Error Handling

- **Recoverable:** Tool failures, validation failures → retry or continue
- **Non-recoverable:** WebSocket disconnect, fatal LLM errors → send error + close
- **Graceful degradation:** Voice unavailable, refiner fails, reference library missing → log warning, continue without feature
- **WorkflowCancelledError:** Clean cancellation path from user cancel button

### 23.3 Concurrency Model

- **Semaphore:** `asyncio.Semaphore(num_parallel)` throttles all concurrent LLM calls (match `OLLAMA_NUM_PARALLEL`)
- **Parallel patterns via `asyncio.gather`:**
  - Lint + test reporting passes (both read-only)
  - Context + framework guide generation
  - Expansion batches in multi-round context generation
  - Chat context gathering (file tree, search, web, reference library)
  - Image descriptions (multiple attachments)
- **Sequential patterns:**
  - Auto-fix passes (format, lint_fix) — both modify files
  - Planning phases (each depends on previous output)
  - Plan steps (each may depend on previous step's files)

### 23.4 Security Boundaries

- **Path traversal protection:** `_safe_resolve()` rejects `../` escapes and symlinks outside repo root
- **Command safety gate:** Three-tier classification (SAFE/REQUIRES_APPROVAL/ALWAYS_BLOCK)
- **File size guard:** 2MB limit on `read_file`
- **Privacy redaction:** Strip sensitive data before cloud provider transmission
- **Search rate limiting:** Configurable delay with random jitter between searches

### 23.5 Git Branch Workflow

1. **Branch creation:** `lean-ai/{session_id}` from default branch (detected via remote HEAD → local master/main → "main")
2. **Stash management:** Auto-stash uncommitted changes before branching, pop after if needed
3. **Auto-commit:** After workflow completion, `git add . && git commit` with descriptive message
4. **Merge:** `/sessions/{id}/merge` merges work branch into base, deletes branch
5. **Abandon:** `/sessions/{id}/abandon` checks out base branch, deletes work branch (force if needed)
6. **Resume:** Validates session state, re-checks out work branch, runs in fix mode

---

## 24. Appendices

### 24.1 File Artifacts

| Path | Purpose |
|------|---------|
| `.lean_ai/` | Project-level Lean AI directory |
| `.lean_ai/lean_ai.db` | SQLite database |
| `.lean_ai/project_context.md` | Generated project architecture doc |
| `.lean_ai/framework_guide.md` | Generated framework best practices |
| `.lean_ai/context/*.md` | Custom steering documents (user-created) |
| `.lean_ai/context/style_guide.md` | Generated CSS/template style guide |
| `.lean_ai/commands.json` | Auto-detected lint/test/format commands |
| `.lean_ai/scratchpads/{session_id}.md` | Per-session scratchpad |
| `.lean_ai/tool_output/` | Long tool output files (auto-cleaned after 1hr) |
| `.lean_ai/fetched/` | Fetched URL content (paginated) |
| `.lean_ai/plan_debug/{session_id}/` | Debug output for planning phases |
| `.lean_ai/reference/` | Reference library documents |
| `.lean_ai_index/` | Whoosh BM25F search index |
| `.lean_ai_reference_index/` | Whoosh reference library document index |
| `.embeddings.bin` | Binary embedding vectors |
| `.embeddings_index.json` | Embedding index sidecar |
| `_manifest.json` | SHA-256 file hash manifest (in index dirs) |

### 24.2 Key Design Constants

| Constant | Value | Location | Purpose |
|----------|-------|----------|---------|
| `SCRATCHPAD_CONTEXT_PERCENT` | 0.05 | scratchpad.py | 5% of context window for scratchpad |
| `EXECUTION_CONTEXT_PERCENT` | 0.05 | context_helpers.py | 5% of context window for execution prompts |
| `CUSTOM_DOCS_SHARE` | 0.4 | context_helpers.py | 40% of budget for custom steering docs |
| `PLAN_OUTPUT_PERCENT` | 0.40 | planner.py | 40% of expert context for plan JSON output |
| `_MAX_REVISIONS` | 5 | pipeline.py | Max plan revision rounds |
| `_INLINE_LIMIT` | 2000 | tool_executor.py | Chars before output saved to file |
| `_MAX_AGE_SECONDS` | 3600 | tool_executor.py | Tool output file cleanup age |
| `_MAX_READ_BYTES` | 2MB | file_ops.py | File read size limit |
| `_ARTIFACT_PER_FILE_LIMIT` | 8000 | prompts.py | Max chars per file in step artifacts |
| `_TARGET_CHARS` | 800 | reference/chunker.py | Target reference library chunk size |
| `_OVERLAP_CHARS` | 150 | reference/chunker.py | Reference library chunk overlap |

### 24.3 WebSocket Message Quick Reference

**Client → Server:**
`user_message` | `cancel` | `approve` | `approve_tool` | `deny_tool` | `ping` | `resume`

**Server → Client:**
`token` | `stage_change` | `stage_status` | `approval_required` | `clarification_needed` | `plan_revision` | `plan_rejected` | `tool_progress` | `tool_approval_required` | `diff` | `test_result` | `error` | `complete` | `cancelled` | `index_status` | `branch_created` | `checkpoint` | `merge_complete` | `assistant_content` | `thinking_content` | `metrics_update` | `context_refreshed` | `pong`

# Configuration Reference

All Lean AI settings use the `LEAN_AI_` prefix and can be set via environment variables or in a `backend/.env` file. Settings are defined in `backend/src/lean_ai/config.py` using pydantic-settings.

### Context Window Shorthand

Context window values accept compact notation — enter `128` instead of `131072`:

| You write | Lean AI uses |
|---|---|
| `128` | 131072 (128 × 1024) |
| `128k` | 131072 |
| `256` | 262144 |
| `131072` | 131072 (values > 10000 are used as-is) |

This applies to `OLLAMA_CONTEXT_WINDOW`, `OPENAI_CONTEXT_WINDOW`, `ANTHROPIC_CONTEXT_WINDOW`, and `INLINE_CONTEXT_WINDOW`.

## Installation Extras

The base install covers Ollama-only usage. Add extras for additional features:

```bash
# Base install (Ollama only)
pip install -e ".[dev]"

# Cloud providers
pip install -e ".[dev,openai]"        # OpenAI (GPT-4o, etc.)
pip install -e ".[dev,anthropic]"     # Anthropic (Claude, etc.)

# Knowledge base document support (EPUB, PDF, Word)
pip install -e ".[dev,knowledge]"

# Google search provider (requires Chrome installed)
pip install -e ".[dev,google]"

# Everything
pip install -e ".[dev,openai,anthropic,knowledge,google]"
```

## LLM Provider

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_LLM_PROVIDER` | `ollama` | Active provider: `ollama`, `openai`, or `anthropic` |

Switch providers at any time by changing this value and restarting the server (or via the extension's model dropdown for runtime switching).

## Ollama

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_OLLAMA_URL` | `http://localhost:11434` | Ollama API endpoint |
| `LEAN_AI_OLLAMA_MODEL` | `qwen3-coder:30b` | Primary model for chat and planning |
| `LEAN_AI_OLLAMA_TEMPERATURE` | `0.7` | Sampling temperature (Qwen3 recommends 0.7 — avoid 0.0) |
| `LEAN_AI_OLLAMA_TOP_P` | `0.8` | Nucleus sampling threshold |
| `LEAN_AI_OLLAMA_TOP_K` | `20` | Top-k sampling |
| `LEAN_AI_OLLAMA_REPEAT_PENALTY` | `1.05` | Repetition penalty |
| `LEAN_AI_OLLAMA_CONTEXT_WINDOW` | `128` (131072) | Context window size — [shorthand](#context-window-shorthand) accepted |
| `LEAN_AI_OLLAMA_MAX_TOKENS` | *25% of context window* | Max output tokens per response |

Ollama is always required, even when using cloud providers — it handles inline predictions, embeddings, and the [local refiner](knowledge-base.md#local-refiner).

## Expert Model

An optional second model for reasoning-heavy tasks. When configured, the expert model handles planning phases 3–6 (change design, risk assessment, plan assembly, verification) and the final validation fix retry. Phases 1–2 and all implementation turns always use the primary model.

The expert model can be a different provider from the primary — for example, run a fast local Ollama model for exploration and implementation, then hand off to Claude or ChatGPT for planning and complex fixes.

### Provider Selection

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_EXPERT_LLM_PROVIDER` | *(empty)* | Provider for expert model: `ollama`, `openai`, or `anthropic`. Empty = auto-detect (Ollama expert when `OLLAMA_MODEL_EXPERT` is set and primary provider is Ollama) |
| `LEAN_AI_OPENAI_EXPERT_MODEL` | *(falls back to OPENAI_MODEL)* | OpenAI model for expert phases (e.g. `gpt-4o`) |
| `LEAN_AI_ANTHROPIC_EXPERT_MODEL` | *(falls back to ANTHROPIC_MODEL)* | Anthropic model for expert phases (e.g. `claude-opus-4-6`) |

When using OpenAI or Anthropic as the expert provider, the existing API key, temperature, context window, and max tokens settings for that provider are used. The relevant API key must be set even if the primary provider is Ollama.

> **Package requirement:** The provider's Python SDK must be installed. If your primary provider is Ollama but you want Anthropic or OpenAI as the expert, install the matching extra:
> ```bash
> pip install -e ".[dev,anthropic]"   # for Anthropic expert
> pip install -e ".[dev,openai]"      # for OpenAI expert
> ```
> Without this, the expert client will fail to initialise at startup and the server will log a warning.

### Ollama Expert Model

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_OLLAMA_MODEL_EXPERT` | *(empty — disabled)* | Ollama expert model name (e.g. `qwen3-coder:80b`) |
| `LEAN_AI_OLLAMA_EXPERT_TEMPERATURE` | *(falls back to OLLAMA_TEMPERATURE)* | Expert model temperature |
| `LEAN_AI_OLLAMA_EXPERT_TOP_P` | *(falls back to OLLAMA_TOP_P)* | Expert model top-p |
| `LEAN_AI_OLLAMA_EXPERT_TOP_K` | *(falls back to OLLAMA_TOP_K)* | Expert model top-k |
| `LEAN_AI_OLLAMA_EXPERT_REPEAT_PENALTY` | *(falls back to OLLAMA_REPEAT_PENALTY)* | Expert model repetition penalty |
| `LEAN_AI_OLLAMA_EXPERT_CONTEXT_WINDOW` | *(falls back to OLLAMA_CONTEXT_WINDOW)* | Expert model context window — [shorthand](#context-window-shorthand) accepted |
| `LEAN_AI_OLLAMA_EXPERT_MAX_TOKENS` | *(derived: 25% of expert context window)* | Expert model max output tokens |

Leave all expert settings empty to use the primary model for everything.

### Example: local primary + cloud expert

This is the recommended setup for saving cloud tokens while still getting strong reasoning for planning:

```env
# Primary: fast local model handles all exploration and implementation
LEAN_AI_LLM_PROVIDER=ollama
LEAN_AI_OLLAMA_MODEL=qwen3-coder:30b

# Expert: Claude for planning phases 3-6 and final validation fix retry
LEAN_AI_EXPERT_LLM_PROVIDER=anthropic
LEAN_AI_ANTHROPIC_API_KEY=sk-ant-...
LEAN_AI_ANTHROPIC_EXPERT_MODEL=claude-opus-4-6
```

With this configuration, cloud API calls only happen during planning (change design, risk assessment, plan assembly, verification steps) and on the final retry of any validation fix loop. All codebase exploration, code execution, and routine tool calls use the local model.

## OpenAI

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_OPENAI_API_KEY` | *(empty)* | API key (required to enable OpenAI) |
| `LEAN_AI_OPENAI_MODEL` | `gpt-4o` | Model name |
| `LEAN_AI_OPENAI_BASE_URL` | *(empty)* | Custom base URL for OpenAI-compatible APIs (Together, Groq, vLLM) |
| `LEAN_AI_OPENAI_TEMPERATURE` | `0.7` | Sampling temperature |
| `LEAN_AI_OPENAI_CONTEXT_WINDOW` | `128000` | Context window size — [shorthand](#context-window-shorthand) accepted |
| `LEAN_AI_OPENAI_MAX_TOKENS` | *25% of context window* | Max output tokens |

Set `LEAN_AI_OPENAI_BASE_URL` to use OpenAI-compatible providers like Together AI, Groq, a local vLLM instance, or [llama-server](llama-server.md).

## Anthropic

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_ANTHROPIC_API_KEY` | *(empty)* | API key (required to enable Anthropic) |
| `LEAN_AI_ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Model name |
| `LEAN_AI_ANTHROPIC_TEMPERATURE` | `0.7` | Sampling temperature |
| `LEAN_AI_ANTHROPIC_CONTEXT_WINDOW` | `200000` | Context window size — [shorthand](#context-window-shorthand) accepted |
| `LEAN_AI_ANTHROPIC_MAX_TOKENS` | *25% of context window* | Max output tokens |

## Inline Predictions

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_INLINE_MODEL` | *(empty — uses primary Ollama model)* | Separate model for Copilot-style completions |
| `LEAN_AI_INLINE_OLLAMA_URL` | *(falls back to OLLAMA_URL)* | Ollama instance for inline model |
| `LEAN_AI_INLINE_MAX_TOKENS` | `256` | Max tokens per inline completion |
| `LEAN_AI_INLINE_CONTEXT_WINDOW` | *12.5% of primary context window* | Context window for inline model — [shorthand](#context-window-shorthand) accepted |

Inline predictions always use Ollama regardless of the active provider. Use a smaller, faster model here for snappy completions (e.g. `qwen2.5-coder:7b`).

## Embeddings

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_EMBEDDING_MODEL` | `qwen3-embedding:0.6b` | Embedding model for semantic search |
| `LEAN_AI_ENABLE_EMBEDDINGS` | `true` | Enable embedding generation + RRF hybrid search |
| `LEAN_AI_EMBEDDING_OLLAMA_URL` | *(falls back to OLLAMA_URL)* | Ollama instance for embeddings |

Embeddings always use Ollama. The search index combines BM25F keyword search with embedding-based semantic search using Reciprocal Rank Fusion (RRF).

## Indexer

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_INDEX_DIR` | `.lean_ai_index` | Whoosh index directory name (relative to workspace root) |
| `LEAN_AI_CHUNK_MAX_LINES` | `50` | Max lines per code chunk |
| `LEAN_AI_CHUNK_OVERLAP_LINES` | `10` | Overlap between adjacent chunks |

## Internet Search

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_SEARCH_PROVIDER` | `duckduckgo` | Search provider: `duckduckgo`, `searxng`, `google`, or `bing` |
| `LEAN_AI_SEARCH_API_URL` | *(empty)* | SearXNG instance URL (when using `searxng` provider) |
| `LEAN_AI_SEARCH_API_KEY` | *(empty)* | API key for search provider (if applicable) |
| `LEAN_AI_SEARCH_DELAY` | `2.0` | Min seconds between searches (random jitter adds 0-100%) |
| `LEAN_AI_INTERNET_TIMEOUT_SECONDS` | `30` | Timeout for web fetches |

Google search uses headless Chrome (requires Chrome installed) and auto-falls back to Bing on failure.

## Project Context

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_ENABLE_PROJECT_CONTEXT` | `true` | Generate `.lean_ai/project_context.md` |
| `LEAN_AI_ENABLE_MULTI_ROUND_CONTEXT` | `true` | Use multi-round LLM calls for richer context |
| `LEAN_AI_ENABLE_FRAMEWORK_GUIDE` | `true` | Generate `.lean_ai/framework_guide.md` for detected frameworks |

## Knowledge Base

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_KNOWLEDGE_DIR` | `.lean_ai/knowledge` | Directory for domain documents |
| `LEAN_AI_KNOWLEDGE_INDEX_DIR` | `.lean_ai_knowledge_index` | Whoosh index directory for knowledge |

See [Knowledge Base & Refiner](knowledge-base.md) for details on document formats and RAG enrichment.

## Local Refiner

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_ENABLE_REFINER` | `true` | Enable local LLM pre-processing for cloud providers |
| `LEAN_AI_REFINER_OLLAMA_URL` | *(falls back to OLLAMA_URL)* | Ollama instance for the refiner |
| `LEAN_AI_REFINER_MODEL` | *(falls back to OLLAMA_MODEL)* | Model for refinement |
| `LEAN_AI_REFINER_TIMEOUT` | `30.0` | Max seconds for the refinement pipeline |
| `LEAN_AI_REFINER_ENABLE_KNOWLEDGE` | `true` | Inject knowledge base context during refinement |
| `LEAN_AI_REFINER_ENABLE_PRIVACY` | `true` | Strip sensitive data before cloud transmission |
| `LEAN_AI_REFINER_KNOWLEDGE_CHUNKS` | `5` | Max knowledge chunks to inject |

The refiner only activates when using cloud providers (OpenAI or Anthropic). See [Knowledge Base & Refiner](knowledge-base.md#local-refiner) for how it works.

## Implementation

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_IMPLEMENTATION_MAX_TOKENS` | *25% of active context window* | Max tokens per LLM turn during execution |
| `LEAN_AI_IMPLEMENTATION_MAX_TURNS` | `0` | Max tool-calling turns per session (`0` = unlimited) |
| `LEAN_AI_REMINDER_INTERVAL` | `10` | Re-inject task reminder every N tool-calling turns |
| `LEAN_AI_LOOP_DETECTION_THRESHOLD` | `3` | Consecutive identical tool calls before warning (`0` = off) |
| `LEAN_AI_REFRESH_THRESHOLD` | `0.7` | Refresh context at this fraction of context window usage |

## Tool Execution

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_TOOL_TIMEOUT_SECONDS` | `60` | Timeout for individual tool executions |

## Post-Execution Validation

After every workflow execution, Lean AI can automatically run your project's formatter, linter, and tests. Failures are fed back to the LLM for self-correction.

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_ENABLE_POST_VALIDATION` | `true` | Enable post-execution validation pipeline |
| `LEAN_AI_POST_FORMAT_COMMAND` | *(empty)* | Auto-fix formatting (e.g. `ruff format src/`) |
| `LEAN_AI_POST_LINT_FIX_COMMAND` | *(empty)* | Auto-fix lint issues (e.g. `ruff check --fix src/`) |
| `LEAN_AI_POST_LINT_COMMAND` | *(empty)* | Lint check — failures reported (e.g. `ruff check src/`) |
| `LEAN_AI_POST_TEST_COMMAND` | *(empty)* | Test check — failures reported (e.g. `pytest tests/ -x -q`) |
| `LEAN_AI_POST_VALIDATION_MAX_RETRIES` | `2` | Max LLM fix attempts for validation failures (`0` = no retries) |

### Auto-Detection

When these variables are empty, Lean AI falls back to commands auto-detected during `/init`. The system scans your project's dependency files (`composer.json`, `pyproject.toml`, `package.json`, `Gemfile`, `go.mod`, `Cargo.toml`, etc.) and saves detected commands to `.lean_ai/commands.json`.

Manual environment variables always take priority over auto-detected commands. Set a variable to an empty string to disable a specific stage.

### Validation Pipeline

The pipeline runs in order:

1. **Format** (auto-fix) — runs the formatter, modifies files silently
2. **Lint fix** (auto-fix) — runs the linter in fix mode, modifies files silently
3. **Lint check** — runs the linter in check mode, reports pass/fail
4. **Test** — runs the test suite, reports pass/fail

If lint check or tests fail, the failure output is fed back to the LLM for self-correction (up to `POST_VALIDATION_MAX_RETRIES` attempts). Each attempt gives the LLM a **30-turn budget** to re-run the failing command, diagnose the root cause, apply a fix, and verify the result. On the **final attempt**, the expert model is used if configured.

See [Architecture: Post-Execution Validation](architecture.md#post-execution-validation) for the full design.

## LLM Retry

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_LLM_RETRY_MAX` | `3` | Max retries on LLM call failure |
| `LEAN_AI_LLM_RETRY_BASE_DELAY` | `2.0` | Base delay between retries (exponential backoff) |

## Server

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_HOST` | `127.0.0.1` | Server bind address |
| `LEAN_AI_PORT` | `8422` | Server port |

## Derived Values

Several settings are automatically derived from the active provider's context window when not explicitly set:

- `*_max_tokens` = 25% of the provider's context window
- `inline_context_window` = 12.5% of the Ollama context window
- `implementation_max_tokens` = 25% of the active provider's context window

Override any derived value by setting it explicitly.

## Extension Settings

The VSCode/VSCodium extension has its own settings, configured through the editor's settings UI. See [Extension Guide](extension.md#settings) for details.

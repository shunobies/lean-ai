# Configuration Reference

Lean AI supports three configuration sources, applied in priority order:

1. **Environment variables** (`LEAN_AI_*`) — highest priority, always wins
2. **`backend/config.yaml`** — YAML format using field names (e.g. `ollama_url`)
3. **`backend/.env`** — legacy dotenv format using `LEAN_AI_*` names

Settings are defined in `backend/src/lean_ai/config.py` using pydantic-settings. The YAML file is the recommended configuration format; `.env` continues to work as a fallback.

### Configuration File Formats

**config.yaml** (recommended):
```yaml
llm_provider: ollama
ollama_url: "http://localhost:11434"
ollama_model: "qwen3-coder:30b"
ollama_context_window: 128

# API keys can be encrypted — see "Encrypted API Keys" below
openai_api_key: "enc:gAAAAABf..."
```

**.env** (legacy):
```env
LEAN_AI_LLM_PROVIDER=ollama
LEAN_AI_OLLAMA_URL=http://localhost:11434
LEAN_AI_OLLAMA_MODEL=qwen3-coder:30b
```

### Encrypted API Keys

API keys stored in `config.yaml` can be Fernet-encrypted so the file doesn't expose raw credentials if leaked. Encrypted values use an `enc:` prefix.

```bash
# Encrypt a key (auto-creates .lean_ai/.keyfile on first use)
python -m lean_ai encrypt-key sk-your-api-key-here
# Output: enc:gAAAAABf...

# Paste the output into config.yaml
```

The encryption key is stored in `.lean_ai/.keyfile` with owner-only permissions (0600). Plain-text keys in config.yaml still work — encryption is optional.

**CLI tools:**

| Command | Description |
|---|---|
| `python -m lean_ai encrypt-key <key>` | Encrypt an API key for config.yaml |
| `python -m lean_ai decrypt-key <value>` | Decrypt an encrypted value (debugging) |
| `python -m lean_ai migrate-env` | Convert existing `.env` to `config.yaml` with auto-encryption |
| `python -m lean_ai generate-config` | Generate a documented config.yaml template |

> **Extension users:** The VSCode extension stores API keys in your OS keychain (never in config files) and handles config.yaml automatically. The encryption feature is for standalone backend users who edit config files manually.

### Context Window Shorthand

Context window values accept compact notation — enter `128` instead of `131072`:

| You write | Lean AI uses |
|---|---|
| `128` | 131072 (128 × 1024) |
| `128k` | 131072 |
| `256` | 262144 |
| `131072` | 131072 (values > 10000 are used as-is) |

This applies to `OLLAMA_CONTEXT_WINDOW`, `OLLAMA_EXPERT_CONTEXT_WINDOW`, `OLLAMA_REQUEST_CONTEXT_WINDOW`, `OPENAI_CONTEXT_WINDOW`, `ANTHROPIC_CONTEXT_WINDOW`, `GEMINI_CONTEXT_WINDOW`, `SERVE_CONTEXT_WINDOW`, and `INLINE_CONTEXT_WINDOW`.

## Installation Extras

The base install covers Ollama-only usage. Add extras for additional features:

```bash
# Base install (Ollama only)
pip install -e ".[dev]"

# Cloud providers
pip install -e ".[dev,openai]"        # OpenAI (GPT-4o, etc.) or Lean AI Serve
pip install -e ".[dev,anthropic]"     # Anthropic (Claude, etc.)
pip install -e ".[dev,gemini]"        # Google Gemini (Gemini 2.5 Flash/Pro, etc.)

# Reference library document support (EPUB, PDF, Word)
pip install -e ".[dev,reference]"

# Google search provider (requires Chrome installed)
pip install -e ".[dev,google]"

# UI verification tools (verify_web_ui, verify_desktop_ui)
# Installs Playwright, Pillow, numpy, mss, plus per-platform window libs.
# Also requires running the separate Chromium install step — see docs/ui-verification.md
pip install -e ".[dev,ui-verification]"

# Everything
pip install -e ".[dev,openai,anthropic,gemini,reference,google,ui-verification]"
```

## Per-Model Capability Flags

Per-role booleans that let a vision- or audio-capable LLM handle
multimedia directly instead of round-tripping through the dedicated
`vision_model` or faster-whisper. Useful on VRAM-constrained hosts
where loading a separate vision model would force the primary to
unload and reload.

Image flags — set `true` on whichever roles run a vision-capable model.
At dispatch time the **active role for the current flow** is consulted
first; if unflagged, the legacy `vision_model` path (Ollama prose
describer) runs. With neither, images are dropped and a warning appears
in the assistant's reply.

| Variable | Default | Notes |
|---|---|---|
| `LEAN_AI_SUPPORTS_IMAGE_PRIMARY` | `false` | Active role for workflow user messages and the chat fallback |
| `LEAN_AI_SUPPORTS_IMAGE_REQUEST` | `false` | Preferred for chat endpoint when set (request overrides primary) |
| `LEAN_AI_SUPPORTS_IMAGE_EXPERT`  | `false` | Symmetric — expert doesn't see user images in current pipeline |
| `LEAN_AI_SUPPORTS_IMAGE_WORKER`  | `false` | Symmetric |
| `LEAN_AI_SUPPORTS_IMAGE_INLINE`  | `false` | Symmetric (FIM doesn't carry images) |

Audio flags — same idea, for STT transcription. Priority order:
`primary → request → worker → expert → inline`. Unflagged → faster-whisper.

| Variable | Default | Notes |
|---|---|---|
| `LEAN_AI_SUPPORTS_AUDIO_PRIMARY` | `false` | First in priority chain |
| `LEAN_AI_SUPPORTS_AUDIO_REQUEST` | `false` | Second |
| `LEAN_AI_SUPPORTS_AUDIO_WORKER`  | `false` | Third |
| `LEAN_AI_SUPPORTS_AUDIO_EXPERT`  | `false` | Fourth |
| `LEAN_AI_SUPPORTS_AUDIO_INLINE`  | `false` | Last |

**Provider capability matrix** (honest — settings UI also warns inline):

| Provider | Image | Audio |
|---|---|---|
| Ollama (multimodal: `qwen3-vl`, `llava`, `llama3.2-vision`, `bakllava`, `moondream`) | ✅ | ❌ |
| Ollama (text-only models) | ❌ | ❌ |
| OpenAI `gpt-4o*`, `gpt-4-vision` | ✅ | `gpt-4o-audio-preview` / `gpt-4o-realtime-preview` ✅ |
| Anthropic Claude 3+ / 4+ | ✅ | ❌ |
| Gemini 2.5 Pro/Flash, 1.5 | ✅ | ✅ |
| Lean AI Serve | depends on loaded vLLM model | depends on loaded model |

Flagging a capability the provider doesn't actually support (e.g.
Anthropic + audio) is non-blocking: at runtime the backend raises
`CapabilityError`, logs a warning, and falls back transparently. The
settings UI shows a red warning chip so you know to uncheck it.

## Per-Role Sampling & Thinking Retention

Three additional per-role knobs, all optional:

**`min_p`** (`LEAN_AI_OLLAMA_{role}_MIN_P`, blank by default) — minimum
probability cutoff for nucleus sampling. Tightens the candidate set at
each step; example value `0.05`. Blank means "omit from the Ollama
options dict" so text-only models that don't implement `min_p` aren't
confused.

**`presence_penalty`** (`LEAN_AI_OLLAMA_{role}_PRESENCE_PENALTY`, blank
by default) — penalty against tokens already present in the context,
encouraging topic breadth. Example value `1.5`. Same blank-= omit
semantics. Literal `0` is a valid explicit "no penalty" value distinct
from blank.

Both fields fall back to the primary value when a role's own is blank,
so you can set `LEAN_AI_OLLAMA_MIN_P=0.05` once and every role inherits.
`expert`/`request`/`worker` can override.

**`preserve_thinking`** (`LEAN_AI_PRESERVE_THINKING_{role}`, default
`false`) — retains the model's previous-turn chain-of-thought in the
rendered prompt so tool-loop iterations don't re-derive the same
reasoning. Improves KV-cache reuse.

### How preservation is delivered per provider

- **Ollama** — client-side fold. The backend prepends
  `<think>\n{thinking}\n</think>\n\n` to the assistant message's `content`
  before sending the next turn. Works regardless of Ollama's compiled
  `RENDERER` (qwen3.5, qwen3, etc.) because the think block rides on
  normal content tokens.
- **Lean AI Serve (vLLM)** — `extra_body={"chat_template_kwargs": {"preserve_thinking": true}}`.
  The Jinja chat template reads the kwarg. Requires a model whose template
  actually honors the flag (Qwen3.6+).
- **OpenAI / Anthropic / Gemini** — ignored silently. The settings UI
  warns when flagged on a provider that won't use it.

### Context-cost mitigation

The tool-loop keeps thinking on the **3 most recent** assistant turns and
drops it from older ones (both delivery strategies are handled). Prevents
a long loop from bloating the context window with stale reasoning.

### Optional: Route B — custom Modelfile for Ollama

If you'd rather have Ollama's renderer emit the think block natively
(instead of relying on the client-side fold, or if you want to also tune
sampling + template structure in one place), build a derived model with
a custom `TEMPLATE` that references `.Thinking`:

```
# Modelfile (save as e.g. Modelfile-qwen3.6-preserve)
FROM qwen3.6:27b-q8_0

TEMPLATE """{{ if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{ end }}{{ range .Messages }}<|im_start|>{{ .Role }}
{{ if and (eq .Role "assistant") .Thinking }}<think>
{{ .Thinking }}
</think>

{{ end }}{{ .Content }}<|im_end|>
{{ end }}<|im_start|>assistant
"""

PARAMETER min_p 0
PARAMETER presence_penalty 1.5
PARAMETER repeat_penalty 1
PARAMETER temperature 1
PARAMETER top_k 20
PARAMETER top_p 0.95
```

Create and point lean-ai at it:

```bash
ollama create qwen3.6-preserve -f Modelfile-qwen3.6-preserve
```

Set `LEAN_AI_OLLAMA_MODEL=qwen3.6-preserve` (or set the model in the
extension Settings panel). Then either:

- Leave `LEAN_AI_PRESERVE_THINKING_PRIMARY=false` — the custom template
  handles it all (the backend doesn't touch content).
- Or keep the flag on — the backend's fold AND the renderer both emit
  think blocks; not a correctness issue but redundant, so prefer one
  path or the other.

Route A (the default — client-side fold when the flag is on, no Modelfile
changes) is recommended for most users because it works across Ollama
versions and renderers. Route B is for teams who want to also customize
sampling parameters or chat-template structure in the same artifact.

## LLM Provider

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_LLM_PROVIDER` | `ollama` | Active provider: `ollama`, `openai`, `anthropic`, `gemini`, or `serve` |

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

Ollama is always required, even when using cloud providers — it handles inline predictions, embeddings, and the [local refiner](reference-library.md#local-refiner).

## Expert Model

An optional second model for reasoning-heavy tasks. When configured, the expert model handles planning phases 3–5 (design + risk synthesis, plan assembly, verification) and the final validation fix retry. Phases 1–2 and all implementation turns always use the primary model.

The expert model can be a different provider from the primary — for example, run a fast local Ollama model for exploration and implementation, then hand off to Claude or ChatGPT for planning and complex fixes.

### Provider Selection

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_EXPERT_LLM_PROVIDER` | *(empty)* | Provider for expert model: `ollama`, `openai`, `anthropic`, `gemini`, or `serve`. Empty = auto-detect (Ollama expert when `OLLAMA_MODEL_EXPERT` is set and primary provider is Ollama) |
| `LEAN_AI_OPENAI_EXPERT_MODEL` | *(falls back to OPENAI_MODEL)* | OpenAI model for expert phases (e.g. `gpt-4o`) |
| `LEAN_AI_ANTHROPIC_EXPERT_MODEL` | *(falls back to ANTHROPIC_MODEL)* | Anthropic model for expert phases (e.g. `claude-opus-4-6`) |
| `LEAN_AI_GEMINI_EXPERT_MODEL` | *(falls back to GEMINI_MODEL)* | Gemini model for expert phases (e.g. `gemini-2.5-pro`) |
| `LEAN_AI_SERVE_EXPERT_MODEL` | *(falls back to SERVE_MODEL)* | Lean AI Serve model for expert phases |

When using OpenAI, Anthropic, Gemini, or Lean AI Serve as the expert provider, the existing API key, temperature, context window, and max tokens settings for that provider are used. The relevant API key must be set even if the primary provider is Ollama.

> **Package requirement:** The provider's Python SDK must be installed. If your primary provider is Ollama but you want a cloud expert, install the matching extra:
> ```bash
> pip install -e ".[dev,anthropic]"   # for Anthropic expert
> pip install -e ".[dev,openai]"      # for OpenAI or Serve expert
> pip install -e ".[dev,gemini]"      # for Gemini expert
> ```
> Lean AI Serve uses the OpenAI SDK under the hood, so the `openai` extra is sufficient.
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

```yaml
# config.yaml
llm_provider: ollama
ollama_model: "qwen3-coder:30b"

# Expert: Claude for planning phases 3-5 and final validation fix retry
expert_llm_provider: anthropic
anthropic_api_key: "enc:gAAAAABf..."  # encrypt with: python -m lean_ai encrypt-key <key>
anthropic_expert_model: "claude-opus-4-6"
```

With this configuration, cloud API calls only happen during planning (change design, risk assessment, plan assembly, verification steps) and on the final retry of any validation fix loop. All codebase exploration, code execution, and routine tool calls use the local model.

### Example: all-local three-model setup

Use three different local models matched to each role's requirements:

```yaml
# config.yaml
llm_provider: ollama
ollama_model: "qwen3-coder:30b"

# Expert: larger model for planning phases 3-5, TDD disputes, final validation
ollama_model_expert: "qwen3-coder-next:80b"

# Request: smaller model for chat conversation and prompt building
request_llm_provider: openai
openai_base_url: "http://localhost:11434/v1"
openai_request_model: "gpt-oss:20b"
```

This keeps everything local while using the right model for each task — the 20B model handles conversational chat, the 30B handles code execution, and the 80B handles complex reasoning during planning.

### Recommended Models

Models tested and known to work well with Lean AI's prompt architecture:

| Role | Model | Size | Notes |
|---|---|---|---|
| **Primary** | `qwen3-coder:30b` | 30B | Default. Strong tool calling and code generation |
| **Primary** | `qwen3-coder:8b` | 8B | Lighter alternative for constrained hardware |
| **Expert** | `qwen3-coder-next:80b` | 80B | Recommended local expert. Handles rich planning prompts well |
| **Expert** | `claude-opus-4-6` | Cloud | Best cloud expert for planning and TDD disputes |
| **Expert** | `gpt-4o` | Cloud | Good cloud alternative for planning |
| **Request** | `gpt-oss:20b` | 20B | Tested for chat/prompt building. Benefits from shorter prompts |
| **Request** | `qwen3.5:27b` | 27B | Good conversational model for chat mode |
| **Embedding** | `qwen3-embedding:0.6b` | 0.6B | Default. Small and fast for semantic search |

The prompt architecture is optimized for this model range: prompts use canonical policy blocks to avoid instruction duplication, chat context is budget-gated for smaller request models, and guardrail nudges are kept short to avoid overriding system-level policy on smaller models.

## Request Model

An optional separate model for:

- The `/chat` endpoint — conversational refinement of fuzzy ideas into well-scoped tasks before they're dispatched to the planner.
- `/request` workflow mode — open-ended tasks like writing guides, research, and documentation.

The request model **does not** participate in the planner. Planning phases 1–2 (scope + codebase exploration) run on the primary model, and phases 3–5 (design synthesis, plan assembly, verification) run on the expert model. Routing a chatty general-purpose model through codebase exploration wastes its strengths and produces weaker `FileSummary` output, so those phases stay with the coder-tuned primary.

If not configured, the primary model handles chat and `/request` mode as well.

### Provider Selection

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_REQUEST_LLM_PROVIDER` | *(empty)* | Provider for request model: `ollama`, `openai`, `anthropic`, `gemini`, or `serve`. Empty = auto-detect |
| `LEAN_AI_OPENAI_REQUEST_MODEL` | *(falls back to OPENAI_MODEL)* | OpenAI model for /request mode |
| `LEAN_AI_ANTHROPIC_REQUEST_MODEL` | *(falls back to ANTHROPIC_MODEL)* | Anthropic model for /request mode |
| `LEAN_AI_GEMINI_REQUEST_MODEL` | *(falls back to GEMINI_MODEL)* | Gemini model for /request mode |
| `LEAN_AI_SERVE_REQUEST_MODEL` | *(falls back to SERVE_MODEL)* | Lean AI Serve model for /request mode |

### Ollama Request Model

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_OLLAMA_MODEL_REQUEST` | *(empty — disabled)* | Ollama request model name (e.g. `qwen3.5:27b`) |
| `LEAN_AI_OLLAMA_REQUEST_TEMPERATURE` | *(falls back to OLLAMA_TEMPERATURE)* | Request model temperature |
| `LEAN_AI_OLLAMA_REQUEST_TOP_P` | *(falls back to OLLAMA_TOP_P)* | Request model top-p |
| `LEAN_AI_OLLAMA_REQUEST_TOP_K` | *(falls back to OLLAMA_TOP_K)* | Request model top-k |
| `LEAN_AI_OLLAMA_REQUEST_REPEAT_PENALTY` | *(falls back to OLLAMA_REPEAT_PENALTY)* | Request model repetition penalty |
| `LEAN_AI_OLLAMA_REQUEST_CONTEXT_WINDOW` | *(falls back to OLLAMA_CONTEXT_WINDOW)* | Request model context window — [shorthand](#context-window-shorthand) accepted |
| `LEAN_AI_OLLAMA_REQUEST_MAX_TOKENS` | *(derived: 25% of request context window)* | Request model max output tokens |

All Ollama request settings inherit from the primary model when not explicitly set.

## Thinking Mode

Controls whether the LLM uses reasoning/thinking mode (relevant for models like Qwen3, Qwen3.5 that support `think=True`).

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_ENABLE_THINKING` | `true` | Enable thinking mode for the primary Ollama model |
| `LEAN_AI_ENABLE_THINKING_EXPERT` | `true` | Enable thinking mode for the expert model |
| `LEAN_AI_ENABLE_THINKING_REQUEST` | `true` | Enable thinking mode for the request model |

Each model has its own independent thinking toggle. Enable thinking for models that support it (e.g. Qwen3, Qwen3.5) and disable it for models that don't.

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

## Gemini

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_GEMINI_API_KEY` | *(empty)* | API key (required to enable Gemini) |
| `LEAN_AI_GEMINI_MODEL` | `gemini-2.5-flash` | Model name |
| `LEAN_AI_GEMINI_TEMPERATURE` | `0.7` | Sampling temperature |
| `LEAN_AI_GEMINI_CONTEXT_WINDOW` | `1048576` (~1M tokens) | Context window size — [shorthand](#context-window-shorthand) accepted |
| `LEAN_AI_GEMINI_MAX_TOKENS` | *25% of context window* | Max output tokens |

Gemini uses the `google-genai` SDK (unified Google GenAI SDK). Gemini models support very large context windows (1M+ tokens).

```bash
# Install with Gemini support
pip install -e ".[dev,gemini]"
```

## Lean AI Serve

Lean AI Serve is a separate vLLM inference wrapper that exposes a 100% OpenAI-compatible API. It uses API key authentication (Bearer token, keys prefixed `las-`) and defaults to port 8420. Since the API is OpenAI-compatible, it reuses the OpenAI provider internally — the `openai` Python extra must be installed.

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_SERVE_URL` | `http://localhost:8420` | Lean AI Serve API endpoint |
| `LEAN_AI_SERVE_API_KEY` | *(empty)* | API key (required to enable Serve) |
| `LEAN_AI_SERVE_MODEL` | *(empty)* | Model name (required to enable Serve) |
| `LEAN_AI_SERVE_TEMPERATURE` | `0.7` | Sampling temperature |
| `LEAN_AI_SERVE_CONTEXT_WINDOW` | `131072` | Context window size — [shorthand](#context-window-shorthand) accepted |
| `LEAN_AI_SERVE_MAX_TOKENS` | *25% of context window* | Max output tokens |

```bash
# Install with Serve support (uses the OpenAI SDK)
pip install -e ".[dev,openai]"
```

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
| `LEAN_AI_SEARCH_API_URL` | *(empty)* | SearXNG instance URL — required when `SEARCH_PROVIDER=searxng` |
| `LEAN_AI_SEARCH_API_KEY` | *(empty)* | API key for search provider (if applicable) |
| `LEAN_AI_SEARCH_DELAY` | `2.0` | Min seconds between searches (random jitter adds 0-100%) |
| `LEAN_AI_INTERNET_TIMEOUT_SECONDS` | `30` | Timeout for web fetches |

Google search uses headless Chrome (requires Chrome installed) and auto-falls back to Bing on failure.

SearXNG is a self-hosted meta-search engine with no rate limits. Set `LEAN_AI_SEARCH_API_URL` to your instance's search endpoint (e.g. `http://localhost:8888/search`). The extension settings panel shows a URL field automatically when SearXNG is selected as the provider.

## Project Context

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_ENABLE_PROJECT_CONTEXT` | `true` | Generate `.lean_ai/project_context.md` |
| `LEAN_AI_ENABLE_MULTI_ROUND_CONTEXT` | `true` | Use multi-round LLM calls for richer context |
| `LEAN_AI_ENABLE_FRAMEWORK_GUIDE` | `true` | Generate `.lean_ai/framework_guide.md` for detected frameworks |

## Reference Library

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_REFERENCE_DIR` | `.lean_ai/reference` | Directory for domain documents |
| `LEAN_AI_REFERENCE_INDEX_DIR` | `.lean_ai_reference_index` | Whoosh index directory for reference library |

See [Reference Library & Refiner](reference-library.md) for details on document formats and RAG enrichment.

## Local Refiner

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_ENABLE_REFINER` | `true` | Enable local LLM pre-processing for cloud providers |
| `LEAN_AI_REFINER_OLLAMA_URL` | *(falls back to OLLAMA_URL)* | Ollama instance for the refiner |
| `LEAN_AI_REFINER_MODEL` | *(falls back to OLLAMA_MODEL)* | Model for refinement |
| `LEAN_AI_REFINER_TIMEOUT` | `30.0` | Max seconds for the refinement pipeline |
| `LEAN_AI_REFINER_ENABLE_REFERENCE` | `true` | Inject reference library context during refinement |
| `LEAN_AI_REFINER_ENABLE_PRIVACY` | `true` | Strip sensitive data before cloud transmission |
| `LEAN_AI_REFINER_REFERENCE_CHUNKS` | `5` | Max reference chunks to inject |

The refiner only activates when using cloud providers (OpenAI, Anthropic, or Lean AI Serve). See [Reference Library & Refiner](reference-library.md#local-refiner) for how it works.

## Implementation

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_IMPLEMENTATION_MAX_TOKENS` | *25% of active context window* | Max tokens per LLM turn during execution |
| `LEAN_AI_IMPLEMENTATION_MAX_TURNS` | `0` | Max tool-calling turns per session (`0` = unlimited) |
| `LEAN_AI_REMINDER_INTERVAL` | `10` | Re-inject task reminder every N tool-calling turns |
| `LEAN_AI_LOOP_DETECTION_THRESHOLD` | `3` | Consecutive identical tool calls before warning (`0` = off) |
| `LEAN_AI_REFRESH_THRESHOLD` | `0.7` | Refresh context at this fraction of context window usage |

## Planning

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_PLAN_PHASE1_MAX_TURNS` | `5` | Max tool-calling turns for Phase 1 (scope analysis). Phase 1 uses `chat_with_tools` with a restricted read-only tool set (`grep_files`, `read_file`, `list_directory`, `query_project_context`, `search_reference`, `task_complete`). `text_only_exit_count=1` means crystal-clear tasks can exit with zero tool calls — the ceiling is only hit when the model genuinely needs to verify assumptions. Set to `0` to disable tool use and fall back to a single-turn scope call. |

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

## Integrations

External service integrations enable two-way sync between Lean AI sessions and task tracking systems. Gated by `LEAN_AI_ENABLE_INTEGRATIONS`.

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_ENABLE_INTEGRATIONS` | `false` | Enable external service integrations |
| `LEAN_AI_INTEGRATION_AUTO_PUSH` | `true` | Auto-push session summaries to linked tasks on completion |

### Jira Cloud

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_JIRA_URL` | *(empty)* | Jira instance URL (e.g. `https://yourorg.atlassian.net`) |
| `LEAN_AI_JIRA_EMAIL` | *(empty)* | Jira account email |
| `LEAN_AI_JIRA_API_TOKEN` | *(empty)* | Jira API token ([generate one](https://id.atlassian.com/manage-profile/security/api-tokens)) |

When all three Jira settings are configured and integrations are enabled, the Jira provider auto-initializes at startup. It uses Jira Cloud REST API v3 with Basic Auth.

Features: list/search/get issues, push session summaries as comments with worklogs, update issue status via transitions, receive webhooks.

### ServiceNow

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_SERVICENOW_URL` | *(empty)* | ServiceNow instance URL (e.g. `https://yourorg.service-now.com`) |
| `LEAN_AI_SERVICENOW_USERNAME` | *(empty)* | ServiceNow username |
| `LEAN_AI_SERVICENOW_PASSWORD` | *(empty)* | ServiceNow password |
| `LEAN_AI_SERVICENOW_TABLE` | `incident` | ServiceNow table name |

When all three ServiceNow credentials are configured and integrations are enabled, the ServiceNow provider auto-initializes at startup. It uses the ServiceNow Table API with Basic Auth.

Features: list/search/get records, push session summaries as work notes, update record state, receive webhooks. Transparently handles both INC numbers and 32-character hex sys_ids.

### MediaWiki

Connect the agent to an internal MediaWiki instance for real-time search during task execution. Unlike the Jira/ServiceNow integrations above, MediaWiki integration does **not** require `LEAN_AI_ENABLE_INTEGRATIONS` — it is gated solely by `LEAN_AI_WIKI_URL` being non-empty.

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_WIKI_URL` | *(empty)* | MediaWiki instance URL (e.g. `https://wiki.company.com`). Empty = wiki tools disabled |
| `LEAN_AI_WIKI_API_PATH` | `/w/api.php` | MediaWiki API endpoint path |
| `LEAN_AI_WIKI_USERNAME` | *(empty)* | Username for authenticated wikis (bot account). Leave empty for public wikis |
| `LEAN_AI_WIKI_PASSWORD` | *(empty)* | Bot password or user password (stored in OS keychain via extension) |

When `LEAN_AI_WIKI_URL` is set, two tools become available to the agent in all workflow modes:

- **`search_wiki`** — full-text search across wiki pages, returns titles, snippets, and URLs
- **`fetch_wiki_page`** — fetches the full content of a wiki page by title, strips HTML to plain text

Authentication is lazy — the agent logs in on the first wiki request using the MediaWiki Action API two-step login flow, then caches session cookies for the remainder of the session. No login is attempted when credentials are empty.

## Curated Memory

Lean AI saves short "memories" about things it learns during each session
(naming conventions, build gotchas, plan rejections, etc.) and reads them
back into future planning. See [Curated Memory](curated-memory.md) for
the full story — the settings below let you tune what the planner reads.

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_ENABLE_SESSION_MEMORY` | `true` | Master switch. Turn off to disable both extraction and retrieval of cross-session memories. Existing rows stay in the DB untouched. |
| `LEAN_AI_MEMORY_RETRIEVAL_STATUSES` | `user_confirmed,high_confidence_auto` | Comma-separated list of `curation_status` values the planner is allowed to read. Raw `auto` memories (unconfirmed) are excluded by default so bad extractions don't poison plans. |
| `LEAN_AI_MEMORY_AUTOPROMOTE_THRESHOLD` | `3` | How many times the same lesson must be extracted (across sessions) before it auto-promotes from `auto` to `high_confidence_auto`. Lower = faster promotion, higher = stricter. |
| `LEAN_AI_MEMORY_CONFIDENCE_TTL_DAYS` | `90` | Default time-to-live in days applied when a memory is given an explicit expiry. Evergreen memories have no expiry unless you set one. |
| `LEAN_AI_ENABLE_PHASE3_MEMORY` | `true` | Inject `gotcha` / `convention` / `rejection` memories into Phase 3 (design synthesis) of the planner. Turn off if design phases feel too constrained. |
| `LEAN_AI_ENABLE_FIX_LOOP_MEMORY` | `true` | Inject `fix_pattern` / `gotcha` memories into the validation fix-loop prompt. The fix loop retrieves using a signature built from the failing command name + the first line of its error output. |
| `LEAN_AI_PHASE3_MEMORY_BUDGET_PERCENT` | `0.02` | Fraction of the active context window used for memory injection in Phase 3. `0.02` = 2%. |
| `LEAN_AI_FIX_LOOP_MEMORY_BUDGET_PERCENT` | `0.02` | Same budget knob, but for the fix loop. |

### Tuning advice

- **Too few memories reach the planner.** Lower the autopromote
  threshold or add `auto` to the retrieval statuses (noisier but more
  context).
- **Bad memories keep appearing.** Open the Memories panel and click
  **Reject** — rejected content is never re-introduced, even if the
  LLM keeps extracting it. If a whole batch came from a bad model,
  use `bulk_invalidate_by_model` (see
  [Curated Memory → Bulk actions](curated-memory.md#bulk-actions)).
- **Disable retrieval for a specific phase.** Set
  `LEAN_AI_ENABLE_PHASE3_MEMORY=false` or
  `LEAN_AI_ENABLE_FIX_LOOP_MEMORY=false`. Phase 1 retrieval is always
  on when session memory is enabled.

## Training Archive & Export

Every workflow decision also gets written to a separate, append-only
archive at `.lean_ai/training.db` (configurable) so you can later
export a dataset for LoRA fine-tuning. See
[Training Pipeline](training.md) for the full story.

> **Important** — Local capture is on by default. The export API is
> off by default — it returns `503 Service Unavailable` until you set
> `LEAN_AI_EXPORT_API_KEY`. Nothing leaves your computer without that
> explicit opt-in.

### Capture settings

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_ENABLE_TRAINING_CAPTURE` | `true` | Master switch. When false, no rows are written to `.lean_ai/training.db`. |
| `LEAN_AI_TRAINING_DB_PATH` | `.lean_ai/training.db` | Where the archive lives. Relative paths are taken relative to the workspace root; absolute paths are used as-is. Safe to move between workspaces. |
| `LEAN_AI_TRAINING_RETENTION_DAYS` | `365` | Rows older than this are deleted during the retention pass (runs opportunistically at session end, throttled to once per hour per workspace). Set to `0` to disable pruning (keep forever). |
| `LEAN_AI_CAPTURE_THINKING` | `true` | Preserve the LLM's thinking/reasoning blocks in the archive. Needed for reasoning-model LoRA training (gpt-oss, Qwen3 thinking mode). Doubles archive size per turn roughly. |
| `LEAN_AI_SCRUBBING_STRICT` | `true` | Fail-closed mode. If the secret scrubber throws an exception on any input, drop the trace rather than risk writing unscrubbed data. Set to `false` to write with `scrubbed=0` instead. |

### Export settings

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_EXPORT_API_KEY` | *(empty)* | Bearer token required by all `/api/export/*` endpoints. Empty = export disabled entirely (503). Generate with e.g. `openssl rand -hex 24`. |
| `LEAN_AI_EXPORT_WORKSPACE_SALT` | *(empty)* | Optional string mixed into the `workspace_id` hash. Leave empty if a single coordinator sees your workspace; set a shared salt when multiple coordinators aggregate the same identity. |
| `LEAN_AI_MEMORY_EXPORT_DROP_THRESHOLD` | `0.40` | When exporting memories, skip any memory where more than this fraction of characters had to be anonymized away. `0.40` = drop memories >40% redacted. Lower = stricter (fewer memories leave), higher = looser. |

### Enabling export

```bash
# Generate a key and add it to backend/.env
echo "LEAN_AI_EXPORT_API_KEY=las-export-$(openssl rand -hex 24)" >> backend/.env

# Restart the backend so it picks up the new key
# Then a coordinator can call:
curl -H "Authorization: Bearer $LEAN_AI_EXPORT_API_KEY" \
    "http://localhost:8422/api/export/manifest?repo_root=$(pwd)"
```

> **Warning** — Treat the export API key like any other secret. Anyone
> who has it can read every training trace your workspace has
> captured. Don't commit it to git; don't share it in Slack.

## Extension Settings

The VSCode/VSCodium extension has its own settings, configured through the editor's settings UI. See [Extension Guide](extension.md#settings) for details.

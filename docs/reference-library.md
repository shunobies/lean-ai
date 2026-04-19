# Reference Library & Local Refiner

Lean AI includes two features that keep your proprietary information private while improving the quality of AI-generated plans:

1. **Reference Library** — Index your internal documentation for context-aware planning
2. **Local Refiner** — Pre-process prompts with a local LLM before sending to cloud providers

## Reference Library

Drop your internal documentation into `.lean_ai/reference/` and Lean AI indexes it for retrieval during planning and chat. The agent uses this context to generate better plans that follow your team's conventions.

### Supported Formats

- Markdown (`.md`)
- PDF (`.pdf`) — requires `reference` extra
- EPUB (`.epub`) — requires `reference` extra
- Word (`.docx`) — requires `reference` extra
- HTML (`.html`, `.htm`)
- Plain text (`.txt`)

### Setup

```bash
# Install with reference library document support
cd backend && pip install -e ".[dev,reference]"

# Add your docs
mkdir -p .lean_ai/reference
cp ~/docs/api-spec.md .lean_ai/reference/
cp ~/docs/architecture.pdf .lean_ai/reference/

# Index the documents
curl -X POST http://localhost:8422/api/index-reference \
  -H "Content-Type: application/json" \
  -d '{"repo_root": "/path/to/project"}'
```

Or use the `/init` slash command in the extension — it indexes both the codebase and reference library in one step.

### How It Works

1. Documents are parsed into prose-aware paragraphs (not code-style line chunks)
2. Paragraphs are indexed in a separate Whoosh search index
3. During planning and chat, the system queries the reference index for relevant context
4. Matching chunks are injected into the LLM prompt alongside codebase context
5. SHA-256 manifests track document changes for incremental re-indexing

### Configuration

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_REFERENCE_DIR` | `.lean_ai/reference` | Directory for domain documents |
| `LEAN_AI_REFERENCE_INDEX_DIR` | `.lean_ai_reference_index` | Whoosh index directory |

See [Configuration](configuration.md#reference-library) for all options.

### Migrating from an older install

Older installs used `.lean_ai/knowledge/` and `.lean_ai_knowledge_index/`. The backend auto-detects those directories on first use and renames them in place — no manual steps needed for the document files. Update any `LEAN_AI_KNOWLEDGE_*` / `LEAN_AI_KB_*` environment variables in your `.env` or `config.yaml` to the new `LEAN_AI_REFERENCE_*` names.

## Local Refiner

When using cloud providers (OpenAI or Anthropic), the local refiner uses your Ollama instance to pre-process prompts before they reach the cloud. This provides three benefits:

1. **Reference enrichment** — Injects relevant reference library context into prompts, so the cloud LLM gets domain-specific guidance without receiving raw proprietary documents
2. **Privacy stripping** — Redacts API keys, internal URLs, database connection strings, and other sensitive data before cloud transmission
3. **Prompt structuring** — Restructures vague requests into detailed, well-formed prompts

### How It Works

```
User prompt
    │
    ▼
┌──────────────────────────┐
│  Local Ollama (Refiner)  │  ◀── Reference library chunks
│                          │
│  1. Query reference lib  │
│  2. Enrich prompt        │
│  3. Strip sensitive data │
└──────────────────────────┘
    │
    ▼
Refined prompt ──▶ Cloud LLM (OpenAI / Anthropic)
```

The refiner runs at two integration points:

- **Chat endpoint** — Runs in parallel with web search and workspace context gathering. The refined message replaces the original for the cloud LLM call.
- **Workflow** — Before planning begins, the task description is refined. The planner also runs a privacy pass on compressed file summaries before phases 3-5 to prevent sensitive code content from leaking.

### Safety Guards

The refiner is designed to be non-fatal — all failures fall back to passing the original text through unchanged:

- **Timeout** — Configurable timeout (default 30s). On timeout, the original text is used.
- **Length guard** — If the refined text is less than 50% of the original length, the LLM likely mangled it. The original is kept.
- **Over-stripping guard** — If privacy stripping removes more than 40% of the text, the original is kept.
- **Connection failure** — If Ollama is unreachable, the refiner is silently disabled.

### Privacy Stripping

The privacy module uses a local LLM to identify and replace sensitive data with generic placeholders:

| Original | Replaced with |
|---|---|
| `postgres://user:pass@db.internal:5432/prod` | `<DB_CONNECTION>` |
| `sk-abc123def456` | `<REDACTED_KEY>` |
| `auth.internal.company.com` | `<INTERNAL_URL>` |
| `admin@company.com` | `<REDACTED_EMAIL>` |

The following are explicitly NOT redacted: public names, generic terms, project-relative file paths, open-source package names, and standard framework patterns.

The chat response includes `refined: true` and `privacy_redactions: N` fields so the UI can indicate when refinement was applied.

### Configuration

| Variable | Default | Description |
|---|---|---|
| `LEAN_AI_ENABLE_REFINER` | `true` | Enable/disable the refiner |
| `LEAN_AI_REFINER_OLLAMA_URL` | *(falls back to OLLAMA_URL)* | Separate Ollama instance for refinement |
| `LEAN_AI_REFINER_MODEL` | *(falls back to OLLAMA_MODEL)* | Model for refinement |
| `LEAN_AI_REFINER_TIMEOUT` | `30.0` | Max seconds per refinement |
| `LEAN_AI_REFINER_ENABLE_REFERENCE` | `true` | Enable reference library injection |
| `LEAN_AI_REFINER_ENABLE_PRIVACY` | `true` | Enable privacy stripping |
| `LEAN_AI_REFINER_REFERENCE_CHUNKS` | `5` | Max reference chunks to inject |

See [Configuration](configuration.md#local-refiner) for all options.

### When Is It Active?

The refiner only activates when **all** of these conditions are met:

1. `LEAN_AI_LLM_PROVIDER` is set to `openai` or `anthropic`
2. `LEAN_AI_ENABLE_REFINER` is `true`
3. Ollama is reachable at the configured URL

When using Ollama as the primary provider, the refiner is a no-op (there's no point in refining prompts that stay local).

### Using a Separate Model

You can point the refiner at a different Ollama model or instance than your primary one. This is useful if you want to run a smaller, faster model for refinement while using a larger model for primary inference:

```env
LEAN_AI_OLLAMA_MODEL=qwen3-coder:30b
LEAN_AI_REFINER_MODEL=qwen3-coder:8b
```

Or run the refiner on a separate Ollama instance entirely:

```env
LEAN_AI_REFINER_OLLAMA_URL=http://second-gpu:11434
LEAN_AI_REFINER_MODEL=qwen3-coder:8b
```

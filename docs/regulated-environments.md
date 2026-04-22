# AI-Assisted Development in Regulated Environments

*How Lean AI and Lean AI Serve enable AI-powered coding without cloud data exposure.*

## The Problem: AI Coding Tools and Regulatory Barriers

Commercial AI coding assistants send source code, prompts, and codebase context to third-party cloud infrastructure. For organizations subject to data handling regulations, this creates a compliance barrier that blocks adoption entirely.

Specific regulatory conflicts:

- **Financial services (SOX, PCI-DSS)** — Source code containing business logic, transaction processing rules, and internal API structures qualifies as confidential. Sending it to third-party clouds violates data handling requirements.
- **Healthcare (HIPAA)** — Codebases often embed schema definitions, data models, and logic referencing PHI structures. Cloud transmission risks inadvertent PHI exposure.
- **Government and defense (FedRAMP, ITAR)** — Classified or export-controlled codebases cannot leave approved infrastructure boundaries.
- **EU data residency (GDPR)** — Organizations processing EU citizen data must maintain data residency. Cloud AI providers may process data in jurisdictions without adequate protections.
- **Legal (attorney-client privilege)** — Law firm codebases and internal tooling contain privileged information that loses its protection when transmitted to third parties.
- **Data sovereignty** — Any organization with policies requiring all data processing to occur within controlled infrastructure.

The result: regulated organizations are locked out of the productivity gains that unregulated competitors adopt freely.

## The Solution: Self-Hosted AI Coding

Lean AI is an agentic coding assistant (VSCode extension + Python backend) designed to work with self-hosted LLM infrastructure. Combined with Lean AI Serve (a vLLM inference wrapper), the entire AI pipeline — from code analysis to plan generation to implementation — runs on your infrastructure with zero data leaving your network.

### What This Means in Practice

- Your source code never leaves your servers
- LLM inference happens on your GPU hardware
- No third-party API calls for core operation
- No telemetry, no usage tracking, no data collection
- Full audit trail of every tool call and LLM interaction (SQLite `tool_logs` table)

### Full Agent Capabilities

This is not a stripped-down local autocomplete. Lean AI with Lean AI Serve provides the same autonomous coding capabilities as cloud-based alternatives:

- 5-phase decomposed planning with tool-assisted codebase exploration
- Autonomous code generation, editing, and testing
- Post-execution validation (auto-runs lint, format, test)
- Git-native branch workflow — every change is isolated and reversible
- Reference library for indexing internal documentation (PDF, EPUB, Word, Markdown)
- Four-model pipeline: request model for chat / `/request` mode, primary model for planning exploration + implementation, expert model for reasoning-heavy planning phases, worker model for auxiliary compression/summarization
- Internet search is optional and can be disabled entirely or routed through a self-hosted SearXNG instance

## Data Flow Guarantees

Four deployment topologies, each with explicit data flow boundaries:

| Topology | LLM Inference | Embeddings | Internet Search | Data Leaves Network |
|---|---|---|---|---|
| Fully Local (Ollama) | Developer workstation | Developer workstation | Disabled | No |
| Self-Hosted GPU Server | Lean AI Serve on internal server | Ollama on workstation | Optional (SearXNG) | No |
| All-in-One | Same machine (Serve + Lean AI) | Same machine | Optional | No |
| Hybrid (with Refiner) | Cloud for expert planning only | Ollama on workstation | Optional | Sensitive data stripped before cloud transmission |

### The Hybrid Option: Cloud Expert with Privacy Stripping

For organizations that want cloud reasoning capability for planning phases only, Lean AI includes a Local Refiner — a local Ollama instance that pre-processes every prompt before it reaches any non-local provider:

- **Privacy stripping** — Redacts API keys, database connection strings, internal URLs, and email addresses before cloud transmission
- **Safety guards** — 40% max redaction threshold, 50% min quality threshold, 30-second timeout. All failures fall back to using the original text unchanged
- **Scoped exposure** — Only planning phases 3-5 (design synthesis, plan assembly, verification) use the cloud expert model. All code execution stays local
- **Opt-in** — The default configurations use no cloud services. Cloud expert is only activated when explicitly configured

## Compliance Alignment

| Requirement | Regulation(s) | Lean AI Feature |
|---|---|---|
| Data residency | GDPR, data sovereignty laws | All processing on your infrastructure (Serve + Ollama) |
| No third-party data sharing | HIPAA, SOX, PCI-DSS | Zero cloud API calls in local/Serve deployments |
| Audit trail | SOX, FedRAMP | SQLite `tool_logs` table records every tool call with timestamps |
| Access control | All | API key authentication on Lean AI Serve (`las-` prefixed Bearer tokens) |
| Network isolation | ITAR, classified environments | Air-gappable — no internet dependency for core operation |
| Encryption at rest | PCI-DSS, HIPAA | Fernet-encrypted API keys in config (`enc:` prefix) |
| Reversible changes | SOX (change management) | Git branch isolation — every change on a separate branch, user approval required |
| Destructive command blocking | All | Command safety gate blocks `rm -rf`, `git push --force`, etc. without user approval |

The `tool_logs` table provides a complete audit trail that compliance teams can export. Every tool invocation — file creation, edit, command execution — is logged with session ID, timestamp, tool name, arguments, and result.

## Key Differentiators

| Capability | Cloud AI Tools | Lean AI + Serve |
|---|---|---|
| Source code exposure | Sent to third-party servers | Never leaves your network |
| Model choice | Vendor-locked | Any model that runs on vLLM |
| Audit trail | Vendor-dependent | Full local SQLite log |
| Air-gap support | Not possible | Fully supported |
| Internal docs integration | Limited | Reference library (PDF, EPUB, Word, MD) indexed locally |
| Cost model | Per-token API pricing | Fixed infrastructure cost |

After GPU hardware acquisition, the marginal cost per query is electricity and compute time. No per-token billing, no usage caps, no vendor pricing changes.

---

# Technical Architecture Guide

## System Architecture

### Self-Hosted with GPU Server

```
Developer Workstation                    GPU Server (Internal Network)
┌─────────────────────────────┐         ┌──────────────────────────────┐
│  VSCode + Lean AI Extension │         │  Lean AI Serve (vLLM)        │
│         │                   │         │  ┌────────────────────────┐  │
│         ▼                   │         │  │  vLLM Engine           │  │
│  Lean AI Backend (FastAPI)  │────────▶│  │  ┌──────────────────┐  │  │
│  ┌───────────────────────┐  │  HTTP   │  │  │  Your Model(s)   │  │  │
│  │  LLM Client (Facade)  │  │ :8420   │  │  └──────────────────┘  │  │
│  │  Provider: Serve       │  │         │  │  OpenAI-compatible API │  │
│  └───────────────────────┘  │         │  └────────────────────────┘  │
│  ┌───────────────────────┐  │         └──────────────────────────────┘
│  │  Ollama (local)        │  │
│  │  - Embeddings          │  │
│  │  - Inline predictions  │  │
│  │  - Refiner (optional)  │  │
│  └───────────────────────┘  │
│  ┌───────────────────────┐  │
│  │  SQLite (tool_logs)    │  │
│  └───────────────────────┘  │
└─────────────────────────────┘
```

- **Lean AI Backend** — FastAPI server on the developer's workstation (port 8422). Handles planning, tool orchestration, and workflow management.
- **Lean AI Serve** — vLLM wrapper on a GPU server (port 8420). Exposes an OpenAI-compatible API with API key authentication. Handles all LLM inference for chat, planning, and code generation.
- **Ollama** — Always runs locally for embeddings (semantic search) and inline predictions (Copilot-style completions). These never touch the network.
- **SQLite** — Local database storing sessions and tool execution logs. Two tables, no ORM.

### Air-Gapped / All-in-One

```
Single Machine (Air-Gapped)
┌──────────────────────────────────────┐
│  VSCode + Lean AI Extension          │
│         │                            │
│         ▼                            │
│  Lean AI Backend ──▶ Lean AI Serve   │
│  (port 8422)        (port 8420)      │
│         │                            │
│         ▼                            │
│  Ollama (embeddings + inline)        │
│  (port 11434)                        │
│                                      │
│  No outbound network connections     │
└──────────────────────────────────────┘
```

## Deployment Guide

### Prerequisites

- GPU server with CUDA or ROCm support (for Lean AI Serve / vLLM)
- Python 3.10+ on developer workstations
- Ollama installed on developer workstations (for embeddings and inline predictions)
- Model files downloaded and available to vLLM

### Step 1: Set Up Lean AI Serve

See the Lean AI Serve documentation for installation and vLLM configuration. Key steps:

1. Load your chosen model into vLLM
2. Configure API keys (prefixed with `las-`)
3. Expose on your internal network (default port 8420)

### Step 2: Install Lean AI Backend

```bash
cd backend && pip install -e ".[dev,openai]"
```

The `openai` extra is required because Lean AI Serve uses the OpenAI SDK under the hood. Add `reference` for internal document indexing:

```bash
cd backend && pip install -e ".[dev,openai,reference]"
```

### Step 3: Configure the Provider

**config.yaml** (recommended):

```yaml
llm_provider: serve
serve_url: "http://gpu-server.internal:8420"
serve_api_key: "enc:gAAAAABf..."
serve_model: "qwen3-coder:30b"
serve_context_window: 128
```

Encrypt your API key:

```bash
python -m lean_ai encrypt-key las-your-key-here
# Output: enc:gAAAAABf...
# Paste the output into config.yaml
```

**.env** alternative:

```env
LEAN_AI_LLM_PROVIDER=serve
LEAN_AI_SERVE_URL=http://gpu-server.internal:8420
LEAN_AI_SERVE_API_KEY=las-your-key-here
LEAN_AI_SERVE_MODEL=qwen3-coder:30b
LEAN_AI_SERVE_CONTEXT_WINDOW=128
```

### Step 4: Configure Ollama

Ollama must be running locally for embeddings and inline predictions:

```bash
ollama pull qwen3-embedding:0.6b
```

Optionally configure a separate inline prediction model:

```yaml
embedding_model: "qwen3-embedding:0.6b"
inline_model: "qwen2.5-coder:7b"
```

### Step 5: Verify

```bash
# Start the backend
cd backend && uvicorn lean_ai.main:app --port 8422

# Health check
curl http://localhost:8422/api/health

# Then run /init in the VSCode extension to index your workspace
```

## Three-Model Pipeline Configuration

Lean AI supports three model roles. In a regulated environment, you can assign all three to Lean AI Serve, or mix local Ollama and Serve models.

| Role | What It Does | Typical Model Size |
|---|---|---|
| Request | Chat conversation, task scoping, planning phases 1-2 | Smaller, faster model |
| Primary | Code generation, tool calling, implementation | Strong coding model |
| Expert | Planning phases 3-5 (design, assembly, verification), final validation fix | Largest available model |

### All Three on Serve

```yaml
llm_provider: serve
serve_url: "http://gpu-server.internal:8420"
serve_api_key: "enc:gAAAAABf..."
serve_model: "qwen3-coder:30b"
serve_context_window: 128

expert_llm_provider: serve
serve_expert_model: "qwen3-coder-next:80b"

request_llm_provider: serve
serve_request_model: "qwen3-coder:8b"
```

### Mixed Local + Serve

Primary inference on Serve, embeddings and inline predictions always use local Ollama (this is the default behavior — no additional configuration needed):

```yaml
llm_provider: serve
serve_url: "http://gpu-server.internal:8420"
serve_api_key: "enc:gAAAAABf..."
serve_model: "qwen3-coder:30b"

# Ollama handles embeddings and inline predictions automatically
ollama_url: "http://localhost:11434"
embedding_model: "qwen3-embedding:0.6b"
inline_model: "qwen2.5-coder:7b"
```

See [Configuration Reference](configuration.md) for the full list of settings.

## Network Security

### Network Topology

```
┌─── Corporate Network Boundary ──────────────────────────┐
│                                                          │
│  Developer VLAN              GPU Server VLAN             │
│  ┌──────────────┐           ┌──────────────┐             │
│  │ Workstation   │──:8420──▶│ Lean AI Serve│             │
│  │ (Lean AI)     │          │ (vLLM)       │             │
│  │               │          └──────────────┘             │
│  │ Ollama :11434 │                                       │
│  │ Backend :8422 │                                       │
│  └──────────────┘                                        │
│                                                          │
│                        ╳ No outbound internet            │
└──────────────────────────────────────────────────────────┘
```

Firewall rules:

- **Workstation to GPU server** — Allow TCP 8420 (Lean AI Serve API)
- **Workstation internal** — Allow TCP 11434 (Ollama, localhost only), TCP 8422 (backend, localhost only)
- **Outbound internet** — Not required. Block entirely for air-gapped deployments
- **Inbound** — No inbound connections required

### Internet Search in Restricted Environments

The default search provider (DuckDuckGo) requires internet access. For restricted environments:

1. **Disable search entirely** — Leave the search provider unconfigured. The agent works without internet search using only local codebase context and the reference library.
2. **Self-hosted SearXNG** — Deploy a SearXNG instance on your internal network:

```yaml
search_provider: searxng
search_api_url: "http://searxng.internal:8888/search"
```

### API Key Authentication

- Lean AI Serve uses Bearer token authentication with `las-` prefixed keys
- Keys can be Fernet-encrypted in `config.yaml` using the `enc:` prefix
- CLI tool: `python -m lean_ai encrypt-key las-your-key-here`
- Encryption key stored in `.lean_ai/.keyfile` with `0600` permissions
- VSCode extension users: keys stored in the OS keychain via SecretStorage

## Security Controls

| Control | Description |
|---|---|
| Path traversal protection | `_safe_resolve()` rejects `../` escapes and symlinks outside the repo root |
| Command safety gate | Destructive commands (`rm`, `git push`, `chmod`, etc.) require user approval |
| File size guard | `read_file` tool has a 2MB size limit |
| Branch isolation | Every agent task runs on its own git branch — main branch is never modified directly |
| User approval gate | Plans require explicit user approval before execution |
| Encrypted credentials | Fernet encryption for API keys in config files |
| Subprocess timeout | Shell commands are killed on timeout to prevent orphan processes |
| No telemetry | Zero usage tracking, no phone-home, no crash reporting |

### Local Refiner (Hybrid Deployments)

When using any non-local provider (including Lean AI Serve on a remote server), the Local Refiner can optionally be enabled as defense-in-depth. A local Ollama instance identifies and replaces sensitive data with generic placeholders before transmission:

| Original | Replaced With |
|---|---|
| `postgres://user:pass@db.internal:5432/prod` | `<DB_CONNECTION>` |
| `sk-abc123def456` | `<REDACTED_KEY>` |
| `auth.internal.company.com` | `<INTERNAL_URL>` |
| `admin@company.com` | `<REDACTED_EMAIL>` |

When Lean AI Serve is on the same internal network, the refiner is typically unnecessary but can be enabled via `enable_refiner: true`. See [Reference Library & Refiner](reference-library.md#local-refiner) for details.

## Reference Library for Internal Documentation

Regulated organizations often have internal architecture docs, API specs, and coding standards that the AI should reference but must never send externally. Lean AI indexes these documents locally in a Whoosh search index.

Supported formats: Markdown, PDF, EPUB, Word, HTML, plain text.

```bash
pip install -e ".[dev,openai,reference]"
mkdir -p .lean_ai/reference
cp internal-api-spec.pdf .lean_ai/reference/
# Then run /init in the extension
```

During planning and chat, the reference index is queried and relevant chunks are injected into the LLM prompt alongside codebase context. All indexing and retrieval is local — documents never leave the workstation. SHA-256 manifests track changes for incremental re-indexing.

See [Reference Library & Refiner](reference-library.md) for full documentation.

## Air-Gapped Deployment Checklist

1. Pre-download all Python packages on a connected machine, transfer via approved media
2. Pre-download Ollama binary and model files
3. Pre-download vLLM and the target model for Lean AI Serve
4. Install the Lean AI backend with `pip install --no-index --find-links /path/to/packages -e ".[dev,openai]"`
5. Set `llm_provider: serve` (never `openai` or `anthropic`)
6. Disable internet search — do not configure a search provider, or use a network-isolated SearXNG
7. Set `enable_refiner: false` (it is a no-op with local providers, but explicitly documenting the intent)
8. Set `enable_framework_guide: false` if framework guide generation would trigger web searches
9. Verify no outbound connections with `ss -tuln` or equivalent network audit
10. Test with `/init` and a sample `/agent` task

Ollama model pulling requires internet access. In air-gapped environments, export models on a connected machine and transfer them to the target machine via approved media.

## Monitoring and Audit

The SQLite database records every tool invocation with session ID, timestamp, tool name, arguments, and result:

```bash
# List all file modifications in the last 7 days
sqlite3 .lean_ai/lean_ai.db \
  "SELECT timestamp, tool_name, json_extract(args, '$.path') \
   FROM tool_logs \
   WHERE tool_name IN ('create_file', 'edit_file') \
   AND timestamp > datetime('now', '-7 days')"
```

Git integration provides a second audit layer — every agent change runs on a named branch (`lean-ai/{session_id}`) with auto-committed changes. For regulated environments, consider backing up the SQLite database alongside your source control.

## Reference Configuration

Complete `config.yaml` for a regulated self-hosted deployment:

```yaml
# === Regulated Environment Configuration ===
# All LLM inference on internal GPU server via Lean AI Serve
# Zero external network dependencies

# Primary provider
llm_provider: serve
serve_url: "http://gpu-server.internal:8420"
serve_api_key: "enc:gAAAAABf..."
serve_model: "qwen3-coder:30b"
serve_context_window: 128

# Expert model (larger model on same Serve instance)
expert_llm_provider: serve
serve_expert_model: "qwen3-coder-next:80b"

# Request model (smaller model for chat)
request_llm_provider: serve
serve_request_model: "qwen3-coder:8b"

# Local Ollama for embeddings and inline predictions
ollama_url: "http://localhost:11434"
embedding_model: "qwen3-embedding:0.6b"
inline_model: "qwen2.5-coder:7b"

# Refiner disabled (all providers are internal)
enable_refiner: false

# Internet search disabled (leave unset or use internal SearXNG)
# search_provider: searxng
# search_api_url: "http://searxng.internal:8888/search"

# Post-validation
enable_post_validation: true
post_format_command: "ruff format src/"
post_lint_command: "ruff check src/"
post_test_command: "pytest tests/ -x -q"
```

See [Configuration Reference](configuration.md) for all environment variables and settings.

## Further Reading

- [Configuration Reference](configuration.md) — All environment variables and settings
- [Architecture](architecture.md) — System design and workflow modes
- [Reference Library & Refiner](reference-library.md) — Internal document indexing and privacy stripping
- [Extension Guide](extension.md) — VSCode extension setup and features

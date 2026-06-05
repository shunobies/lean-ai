# Lean AI Application Specification

This document specifies the current Lean AI application as implemented in this repository. It is intended to be sufficient for a future engineer or LLM agent to reproduce the whole application: backend, VS Code extension, JetBrains plugin, persistence, workflows, local files, optional subsystems, and build/deployment behavior.

Every substantive requirement below is grounded in source code. Source anchors are written as repository-relative paths; when a behavior is inferred across files, the relevant files are listed together.

## 1. Product Boundary

Lean AI is an editor-integrated agentic coding assistant. It consists of:

- A Python FastAPI backend exposed under `/api`, with REST endpoints and a workflow WebSocket.
- A VS Code extension that starts or connects to the backend, provides a chat webview, inline completions, settings, session history, notes, memories, prompt editing, observability, voice, UI verification commands, and slash-command driven workflows.
- A JetBrains plugin with a bundled backend, chat and sessions tool windows, inline completion, settings, and approve/reject/restart actions.
- Local workspace state stored primarily under each target workspace's `.lean_ai/` directory, plus Whoosh/embedding indexes in `.lean_ai_index` and `.lean_ai_reference_index`.

Source anchors:

- `README.md`
- `backend/src/lean_ai/main.py`
- `backend/src/lean_ai/router.py`
- `extension/package.json`
- `extension/src/extension.ts`
- `jetbrains-plugin/src/main/resources/META-INF/plugin.xml`

## 2. Repository Layout

The repository must be organized with these top-level areas:

```text
backend/             Python package and FastAPI server
extension/           VS Code extension
jetbrains-plugin/    JetBrains plugin
docs/                User and developer docs
skills/              Example local skills
.lean_ai/            This repo's own Lean AI state, prompts, memories, references
```

The backend package lives at `backend/src/lean_ai`. Important backend subpackages:

- `routers/`: REST and WebSocket endpoint implementations.
- `llm/`: provider abstraction, tool-calling facade, planner, prompt registry, role tuning, vision helpers.
- `workflow/`: workflow orchestration, graph primitives, execution, fix/request modes, validation, WebSocket protocol.
- `tools/`: file, shell, git, internet, wiki, scratchpad, journal, UI verification, and scaffold tools.
- `context/`: project context generation, command detection, metadata extraction, style guide generation, context DB.
- `indexer/`: code indexing, tree walking, chunking, embeddings, manifests.
- `reference/`: local document readers and reference index.
- `memory/`: cross-session memory extraction, storage, indexing.
- `training/`: local capture DB, scrubbing, export formats, trace spans, evaluations.
- `integrations/`: GitHub/Jira/ServiceNow integration providers.
- `voice/`: STT, TTS, wake word, audio manager.
- `languages/`: tree-sitter language definitions and YAML query configs.
- `scaffolds/`: scaffold recipes.

Source anchors:

- `backend/src/lean_ai`
- `backend/src/lean_ai/router.py`
- `backend/src/lean_ai/tools/scaffold.py`
- `backend/src/lean_ai/languages/*.yaml`

## 3. Backend Runtime and Dependencies

The backend is a Python package named `lean-ai`, requiring Python `>=3.10`, built with Hatchling. It exposes a console script:

```text
lean-ai = lean_ai.cli:main
```

Core dependencies:

- FastAPI and `uvicorn[standard]` for HTTP/WebSocket serving.
- `aiosqlite` for async SQLite.
- Pydantic and pydantic-settings for validation and configuration.
- `cryptography` for encrypted config secrets.
- Ollama SDK for local models and embeddings.
- Whoosh for BM25 indexing.
- BeautifulSoup4, httpx, ruamel.yaml, pathspec, websockets, duckduckgo-search.
- tree-sitter plus grammar packages for Python, JavaScript, TypeScript, Java, Go, Rust, Ruby, C, C++, C#, PHP, CSS, and HTML.

Optional extras:

- `openai`: OpenAI provider.
- `anthropic`: Anthropic provider.
- `gemini`: Google Gemini provider.
- `reference`: EPUB/PDF/DOCX document support.
- `google`: Selenium browser search.
- `voice`: faster-whisper, kokoro-onnx, soundfile, openwakeword, PyAudio.
- `ui-verification`: Playwright, mss, Pillow, numpy, and platform window packages.
- `dev`: pytest, pytest-asyncio, ruff.

Source anchors:

- `backend/pyproject.toml`
- `backend/src/lean_ai/cli.py`

## 4. Backend Application

The FastAPI app must:

- Be created in `lean_ai.main` with title `Lean AI`, version `0.1.0`, and description matching an agentic coding backend.
- Include permissive CORS for all origins, credentials, methods, and headers.
- Include the aggregated router under `/api`.
- Use an async lifespan that:
  - Logs startup and readiness.
  - Auto-initializes configured integrations only when `enable_integrations` is true.
  - On shutdown, closes active integrations, closes Selenium browser search, and cleans voice audio resources if available.

Source anchors:

- `backend/src/lean_ai/main.py`
- `backend/src/lean_ai/router.py`

## 5. Configuration System

Configuration is defined by `Settings` in `backend/src/lean_ai/config.py`.

Priority order:

1. Explicit init settings.
2. Environment variables with prefix `LEAN_AI_`.
3. `config.yaml` using field names such as `ollama_url`.
4. `.env` using `LEAN_AI_*` names.
5. File secrets/defaults.

Secrets in YAML can be encrypted with `enc:` prefixes and decrypted via a Fernet key stored at `.lean_ai/.keyfile`. The CLI must provide `encrypt-key`, `decrypt-key`, `migrate-env`, and `generate-config`.

Context window fields accept shorthand:

- Integer/string values ending in `k` are multiplied by 1024.
- Numeric values `<= 10000` are also treated as thousands of tokens.
- Larger values are used directly.

Important configuration groups:

- Primary LLM provider: `llm_provider`, supporting `ollama`, `openai`, `anthropic`, `gemini`, and `serve` in current code.
- Ollama primary/expert/request/worker models and per-role sampling.
- OpenAI, Anthropic, Gemini, and Lean AI Serve settings.
- Per-phase model roles: `scope_model_role`, `exploration_model_role`, `design_model_role`, `assembly_model_role`.
- Thinking controls: per-role `enable_thinking_*`, `preserve_thinking_*`, `reasoning_effort_*`, and `max_thinking_tokens`.
- Capability flags: per-role image/audio booleans.
- Inline prediction and embedding models, always Ollama-backed.
- Vision model and UI verification settings.
- Voice STT/TTS/wake word settings.
- Indexing, internet search, MediaWiki, project context, reference library, local refiner.
- Implementation/tool loop limits, planning phase limits, parallelism.
- Validation commands and retry counts.
- Session memory and training capture.
- Integrations for GitHub, Jira, ServiceNow.
- Tool timeout and server host/port.

Derived token limits:

- Provider `max_tokens` defaults to one quarter of the provider context window.
- Inline context defaults to one eighth of the Ollama context window.
- Implementation max tokens defaults to one quarter of the active provider context window.

Source anchors:

- `backend/src/lean_ai/config.py`
- `backend/src/lean_ai/crypto.py`
- `backend/src/lean_ai/cli.py`
- `docs/configuration.md`

## 6. LLM Provider Layer

All provider implementations must conform to `LLMProvider`, whose core responsibilities include:

- `chat`
- `chat_stream`
- `chat_structured`
- tool calling support where available
- fill-in-the-middle where implemented
- embeddings where implemented
- model capability errors and structured output validation

Provider adapters:

- `OllamaProvider` in `llm/client.py`.
- `OpenAIProvider` in `llm/provider_openai.py`.
- `AnthropicProvider` in `llm/provider_anthropic.py`.
- `GeminiProvider` in `llm/provider_gemini.py`.
- Lean AI Serve uses OpenAI-compatible client creation in `routers/client_factory.py`.

`LLMClient` in `llm/facade.py` wraps a provider and implements the tool-calling loop. It must support:

- Multiple tool turns.
- Callback hooks for content, thinking, tool call, tool result, metrics, and metrics reset.
- Loop detection for repeated tool calls.
- Context refresh when token usage exceeds `refresh_threshold`.
- Task reminders every configured number of turns.
- User interruption injection.
- Tool result compression hooks.
- Training capture hooks.
- Detection of unverified claims and in-loop guardrail events.

The provider factory must create role-specific clients for primary, expert, request, and worker roles. Missing role clients fall back to primary where the calling workflow allows it.

Source anchors:

- `backend/src/lean_ai/llm/base.py`
- `backend/src/lean_ai/llm/facade.py`
- `backend/src/lean_ai/llm/client.py`
- `backend/src/lean_ai/llm/provider_openai.py`
- `backend/src/lean_ai/llm/provider_anthropic.py`
- `backend/src/lean_ai/llm/provider_gemini.py`
- `backend/src/lean_ai/routers/client_factory.py`
- `backend/src/lean_ai/routers/dependencies.py`

## 7. Prompt Registry and Role Tuning

Prompts are registered in a prompt registry and are resolved by key. Prompt defaults are registered in code and can be overridden per workspace through `.lean_ai/prompts.yaml`.

Role tuning calibrates request, primary, and expert roles for specific provider/model identities. It must:

- Produce profiles under `.lean_ai/role_tuning/`.
- Calculate hashes over role work summaries and prompt versions.
- Store scoped prompt overrides through the prompt registry.
- Expose prewarm and apply-suggestions REST endpoints.
- Let chat/request workflows use request tuning, planning/execution use primary/expert tuning, and validation/fix loops retrieve relevant scoped prompt behavior.

Source anchors:

- `backend/src/lean_ai/llm/prompt_registry.py`
- `backend/src/lean_ai/llm/prompt_defaults.py`
- `backend/src/lean_ai/llm/prompts.py`
- `backend/src/lean_ai/llm/role_tuning.py`
- `backend/src/lean_ai/routers/prompts.py`
- `backend/src/lean_ai/routers/role_tuning.py`
- `.lean_ai/prompts.yaml`

## 8. REST API Surface

All endpoints are mounted under `/api`.

### 8.1 Session Endpoints

- `POST /sessions`: create a session with `repo_root`/`workspace_path` and `task`.
- `GET /sessions?repo_root=...`: list sessions.
- `GET /sessions/{session_id}?repo_root=...`: session detail.
- `DELETE /sessions/{session_id}?repo_root=...`: delete a session.
- `GET /sessions/{session_id}/conversation?repo_root=...`: conversation log.
- `GET /sessions/{session_id}/checkpoints?repo_root=...`: checkpoint tree.
- `POST /sessions/{session_id}/restore`: restore a checkpoint.
- `GET /sessions/{session_id}/git-events`: currently returns an empty list.
- `GET /sessions/search?repo_root=...&q=...&commit=...`: search by text or commit.
- `POST /sessions/{session_id}/resume`: validate resumability and check out the session branch.

Source anchor: `backend/src/lean_ai/routers/sessions.py`

### 8.2 Workflow Endpoints

- `WebSocket /sessions/{session_id}/stream`: long-running workflow channel.
- `POST /sessions/{session_id}/merge`: merge session branch back to base branch.
- `POST /sessions/{session_id}/abandon`: abandon/clean up session branch.

Source anchor: `backend/src/lean_ai/routers/workflow.py`

### 8.3 Chat Endpoints

- `POST /chat`: non-streaming chat with workspace context, attachments, optional web search skip, mode, and extended turn budget.
- `POST /chat/stream`: SSE streaming chat with content/thinking/tool progress.

Chat uses the request model when configured, otherwise the primary model. It has a read-only and knowledge-oriented tool executor, plus note/memory/session/architecture helpers when a workspace is present.

Source anchors:

- `backend/src/lean_ai/routers/chat.py`
- `backend/src/lean_ai/routers/models.py`

### 8.4 Workspace Generation Endpoints

- `POST /init-workspace`: index workspace, generate commands, index reference documents, generate embeddings when enabled.
- `POST /generate-project-context`: generate `.lean_ai/project_context.md`, optionally SSE streaming.
- `POST /generate-style-guide`: generate a style guide, optionally SSE streaming.
- `POST /index-reference`: index reference library.

Source anchors:

- `backend/src/lean_ai/routers/generation.py`
- `backend/src/lean_ai/routers/reference_endpoints.py`
- `backend/src/lean_ai/context/generation.py`
- `backend/src/lean_ai/context/style_guide.py`

### 8.5 Info and Prediction Endpoints

- `GET /models`: configured/default models.
- `POST /predict`: inline code prediction.
- `GET /health`: backend health.

Source anchor: `backend/src/lean_ai/routers/info.py`

### 8.6 Scaffolding Endpoints

- `GET /scaffold/list`: available scaffold recipes.
- `POST /scaffold`: instantiate a scaffold into a parent directory.

Source anchors:

- `backend/src/lean_ai/routers/scaffold_endpoints.py`
- `backend/src/lean_ai/tools/scaffold.py`
- `backend/src/lean_ai/scaffolds/*/scaffold.yaml`

### 8.7 Notes Endpoints

- `POST /notes`
- `GET /notes`
- `GET /notes/search`
- `GET /notes/projects`
- `GET /notes/todos`
- `GET /notes/{note_id}`
- `PUT /notes/{note_id}`
- `DELETE /notes/{note_id}`
- `POST /notes/{note_id}/todos`
- `PUT /notes/todos/{todo_id}`
- `DELETE /notes/todos/{todo_id}`

Source anchors:

- `backend/src/lean_ai/routers/notes.py`
- `backend/src/lean_ai/notes_db.py`
- `backend/src/lean_ai/notes_index.py`
- `backend/src/lean_ai/notes_llm.py`

### 8.8 Memories Endpoints

- `GET /memories`
- `GET /memories/{memory_id}`
- `POST /memories`
- `POST /memories/{memory_id}/confirm`
- `POST /memories/{memory_id}/reject`
- `DELETE /memories/{memory_id}`
- `POST /memories/extract`

Source anchors:

- `backend/src/lean_ai/routers/memories.py`
- `backend/src/lean_ai/memory/db.py`
- `backend/src/lean_ai/memory/extractor.py`
- `backend/src/lean_ai/memory/index.py`

### 8.9 Architecture Decision Endpoints

- `GET /decisions`
- `GET /decisions/{decision_id}`
- `POST /decisions`
- `POST /decisions/{decision_id}/status`

Source anchors:

- `backend/src/lean_ai/routers/architecture.py`
- `backend/src/lean_ai/architecture/decision_db.py`

### 8.10 Prompt and Role Tuning Endpoints

- `GET /prompts`
- `PUT /prompts`
- `POST /prompts/reset`
- `POST /role-tuning/prewarm`
- `POST /role-tuning/apply-suggestions`

Source anchors:

- `backend/src/lean_ai/routers/prompts.py`
- `backend/src/lean_ai/routers/role_tuning.py`

### 8.11 Voice Endpoints

- `POST /voice/stt/start`
- `POST /voice/stt/stop`
- `POST /voice/stt/warmup`
- `POST /voice/tts`
- `POST /voice/tts/stream`
- `POST /voice/tts/stream-pcm`
- `GET /voice/tts/voices`
- `POST /voice/tts/ensure-models`
- `POST /voice/config`
- `POST /voice/wakeword/start`
- `POST /voice/wakeword/stop`
- `GET /voice/events`
- `GET /voice/status`

Source anchors:

- `backend/src/lean_ai/routers/voice.py`
- `backend/src/lean_ai/voice/*`

### 8.12 UI Verification Endpoints

The UI verification router is mounted with prefix `/ui-verification`:

- `GET /ui-verification/status`
- `POST /ui-verification/install`
- `POST /ui-verification/test`

Source anchors:

- `backend/src/lean_ai/routers/ui_verification.py`
- `backend/src/lean_ai/tools/ui_capture_web.py`
- `backend/src/lean_ai/tools/ui_capture_desktop.py`
- `backend/src/lean_ai/tools/ui_analysis.py`
- `backend/src/lean_ai/tools/ui_verification.py`

### 8.13 Integrations Endpoints

The integrations router is mounted with prefix `/integrations`:

- `GET /integrations`
- `GET /integrations/{name}/health`
- `GET /integrations/{name}/tasks`
- `GET /integrations/{name}/tasks/{external_id}`
- `GET /integrations/{name}/search`
- `POST /integrations/{name}/push`
- `POST /integrations/{name}/link`
- `POST /integrations/{name}/unlink`
- `GET /integrations/linked/all`
- `POST /integrations/{name}/webhook`

Source anchors:

- `backend/src/lean_ai/routers/integrations.py`
- `backend/src/lean_ai/integrations/*`

### 8.14 Training Export and Observability Endpoints

Training export is mounted with prefix `/export`:

- `GET /export/workspace-id`
- `GET /export/manifest`
- `GET /export/traces`
- `GET /export/memories`
- `GET /export/events`
- `GET /export/tool-executions`
- `GET /export/tool-compressions`
- `GET /export/clarifications`
- `GET /export/phase2-syntheses`
- `GET /export/diff-decisions`

Observability:

- `GET /observability/sessions`
- `GET /observability/sessions/{session_id}`
- `GET /observability/traces/{span_uuid}`
- `GET /observability/traces/tree`
- `POST /observability/feedback`
- `GET /observability/feedback`
- `GET /observability/metrics/summary`
- `GET /observability/metrics/tokens`
- `GET /observability/metrics/latency`
- `GET /observability/metrics/tools`

Evaluation endpoints live in the sessions router:

- `POST /eval/datasets`
- `GET /eval/datasets`
- `POST /eval/run`
- `GET /eval/results/{run_id}`

Source anchors:

- `backend/src/lean_ai/routers/export.py`
- `backend/src/lean_ai/routers/observability.py`
- `backend/src/lean_ai/routers/sessions.py`
- `backend/src/lean_ai/training/db.py`
- `backend/src/lean_ai/training/capture.py`

### 8.15 Workspace Utility Endpoints

- `POST /workspace/convert-docx`
- `POST /workspace/log-applied`
- `POST /decision` for diff decisions.

Source anchors:

- `backend/src/lean_ai/routers/workspace.py`
- `backend/src/lean_ai/routers/diffs.py`

## 9. WebSocket Workflow Protocol

The backend workflow WebSocket is `/api/sessions/{session_id}/stream`.

Server-to-client message types include:

- `stage_change`
- `stage_status`
- `approval_required`
- `tool_approval_required`
- `clarification_needed`
- `plan_rejected`
- `plan_revision`
- `tool_progress`
- `diff`
- `test_result`
- `error`
- `complete`
- `cancelled`
- `execution_checkpoint`
- `execution_checklist`
- `context_refreshed`
- `assistant_content`
- `thinking_content`
- `metrics_update`
- `metrics_reset`
- `branch_created`
- `pong`
- `vision_description`
- `refiner_status`
- `memory_suggested`
- `scratchpad`
- `journal`
- `observations`

Client-to-server messages are normalized by the dispatcher and must support:

- Approval.
- Rejection/feedback.
- Cancellation.
- User messages/interruptions.
- Ping/pong style liveness.

The dispatcher separates approval waiting from execution/interruption mode so user feedback during execution can be injected rather than mistaken for plan approval.

Source anchors:

- `backend/src/lean_ai/workflow/ws_protocol.py`
- `backend/src/lean_ai/workflow/ws_dispatcher.py`
- `backend/src/lean_ai/workflow/ws_handler.py`
- `backend/src/lean_ai/workflow/ws_messages.py`
- `extension/src/types.ts`
- `extension/src/wsHandler.ts`
- `jetbrains-plugin/src/main/kotlin/com/leanai/plugin/ws/MessageTypes.kt`
- `jetbrains-plugin/src/main/kotlin/com/leanai/plugin/ws/WebSocketHandler.kt`

## 10. Workflow Modes

Lean AI has three main workflow modes:

- Plan mode, normally entered through `/agent` or by sending a suggested agent prompt.
- Fix mode, entered through `/fix`.
- Request mode, entered through `/request` or `/skill`.

Plan mode creates a branch, plans, asks for approval, executes, validates, checkpoints, and returns a complete message.

Fix mode skips planning and approval. It may perform a read-only investigation first, then executes with tools, validates, and completes.

Request mode shares the fix-mode execution path but uses a neutral request prompt and a request model when configured.

Source anchors:

- `backend/src/lean_ai/workflow/pipeline.py`
- `backend/src/lean_ai/workflow/fix_mode.py`
- `extension/src/slashCommands.ts`
- `extension/src/slashCommandsWorkspace.ts`
- `extension/src/sidebarProvider.ts`
- `jetbrains-plugin/src/main/kotlin/com/leanai/plugin/ui/ChatBridge.kt`

## 11. Planning Pipeline

The planner is implemented as a 4-phase decomposed pipeline in code, with a Phase 4a/TDD planning helper and a legacy Phase 5 verification helper folded into validation logic.

Required phases:

1. Scope/clarification:
   - Uses read-only tools and optional `request_clarification`.
   - Produces a validated `ScopeDocument`.
   - Scope document has required sections: problem, deliverables, in-scope, out-of-scope, downstream consumers, assumptions with verification hints, success criteria, risks.

2. Exploration:
   - Uses read-only tools to identify files, missing infrastructure, assumptions, references, and testing inventory.
   - Can run parallel or serial exploration depending on settings and implementation path.
   - Produces a validated `FileSummary`.

3. Design and risks:
   - Produces `DesignAndRisks` including change design, dependency order, naming conventions, risk assessment, missing files, core functionality tags, and name registry.
   - Can retrieve design memories.

4. Assembly:
   - Produces an `ExecutionPlan` made of `PlanStep` entries.
   - Validates hallucinated paths, create/edit consistency, missing files, success checks, test coverage, TDD contracts, full-suite command availability, and core functionality checks.
   - Can revise the plan based on validation warnings or user rejection.

The routing policy maps phases to roles:

- `scope_model_role`
- `exploration_model_role`
- `design_model_role`
- `assembly_model_role`

Default roles in settings are primary for scope, worker for exploration, expert for design, expert for assembly.

Source anchors:

- `backend/src/lean_ai/llm/planner.py`
- `backend/src/lean_ai/llm/planner_exploration.py`
- `backend/src/lean_ai/llm/planner_helpers.py`
- `backend/src/lean_ai/llm/plan_schema.py`
- `backend/src/lean_ai/llm/tool_definitions.py`
- `backend/src/lean_ai/config.py`

## 12. Plan Schema

The plan schema must include these major Pydantic models:

- `ScopeAssumption`
- `ScopeDocument`
- `FileObservation`
- `MissingItem`
- `VerifiedReference`
- `WebReference`
- `AssumptionStatus`
- `ExistingCoverage`
- `TestingInventory`
- `FileSummary`
- `NamingConvention`
- `ChangeDesign`
- `MissingFile`
- `DependencyOrder`
- `CriticalRisk`
- `CoreFunctionalityTag`
- `DesignAndRisks`
- `NameRegistryEntry`
- `StepInput`
- `StepChangeTarget`
- `SuccessCheck`
- `PlanStep`
- `VerificationPlan`
- `ExecutionPlan`

`ExecutionPlan` must render to markdown via `plan_to_markdown`. Plan steps must be bounded job contracts with allowed tools, inputs, may-change targets, success checks, and fallback/blocking behavior.

Source anchor: `backend/src/lean_ai/llm/plan_schema.py`

## 13. Plan Approval and Revision

After planning, the backend sends `approval_required` with:

- Markdown plan.
- User summary.
- Plan validation warnings.

The approval node waits for WebSocket approval. On rejection/feedback:

- It records a plan decision hook.
- It revises the plan.
- It sends a revision message.
- It allows up to five revision rounds.

On approval:

- It records plan decision data for training.
- It continues to execution.

Source anchors:

- `backend/src/lean_ai/workflow/pipeline.py`
- `backend/src/lean_ai/workflow/hooks.py`
- `backend/src/lean_ai/training/capture.py`

## 14. Execution Engine

Execution is sequential by default, with grouping logic that can identify independent steps while respecting barriers.

Barrier tools:

- `run_tests`
- `run_lint`
- `format_code`
- `run_command`

Mutation tools:

- `create_file`
- `edit_file`
- `run_command`
- `format_code`

Read-only tools:

- `read_file`
- `list_directory`
- `directory_tree`
- `grep_files`
- `query_project_context`
- `search_reference`
- `search_wiki`
- `fetch_wiki_page`
- `search_internet`
- `fetch_url`
- `update_scratchpad`
- `add_journal_entry`

Every step enforces file mutation boundaries:

- For `create_file` and `edit_file`, the target path must be in the step's `may_change` set.
- If a step does not declare `may_change`, file-write tools are blocked for that step.

Execution must send checklist/checkpoint/progress messages over WebSocket, invalidate metadata caches for changed paths, update project context after execution, and record state checkpoints.

Source anchors:

- `backend/src/lean_ai/workflow/executor.py`
- `backend/src/lean_ai/workflow/tool_executor.py`
- `backend/src/lean_ai/context/metadata.py`
- `backend/src/lean_ai/workflow/state.py`

## 15. Fix and Request Execution

Fix mode:

- Uses expert client when configured, otherwise primary.
- Can run a read-only investigation phase before implementation when `enable_fix_investigation` is true.
- Uses scratchpad, journal, and state ledger to survive refresh/resume.
- Loads condensed context.
- Builds a fix-specific system prompt.

Request mode:

- Uses request client when configured, otherwise primary.
- Uses request-specific tools and a neutral request system prompt.
- Skips the bug-investigation prompt path.

Both modes use post-validation and validation fix loops when enabled.

Source anchors:

- `backend/src/lean_ai/workflow/fix_mode.py`
- `backend/src/lean_ai/workflow/prompts.py`
- `backend/src/lean_ai/tools/scratchpad.py`
- `backend/src/lean_ai/tools/journal.py`
- `backend/src/lean_ai/tools/state_ledger.py`

## 16. Post-Execution Validation

Post-validation is controlled by:

- `enable_post_validation`
- `post_format_command`
- `post_lint_fix_command`
- `post_lint_command`
- `post_test_command`
- `post_validation_max_retries`
- `post_validation_fix_turns`

Effective commands are workspace-specific:

- `.lean_ai/commands.json` commands detected from project files are authoritative where present.
- Global config values fill gaps.

Validation order:

1. Format command.
2. Lint-fix command.
3. Lint command.
4. Test command.
5. If failures remain, run an LLM validation-fix loop up to retry limits.

Source anchors:

- `backend/src/lean_ai/workflow/validation.py`
- `backend/src/lean_ai/context/command_detection.py`
- `backend/src/lean_ai/workflow/executor.py`
- `backend/src/lean_ai/workflow/fix_mode.py`
- `docs/example-flow.md`

## 17. Tool System

Tool schemas are JSON-schema/OpenAI-compatible function definitions in `llm/tool_definitions.py`.

Implementation tools include at least:

- `create_file`
- `edit_file`
- `read_file`
- `run_tests`
- `run_lint`
- `format_code`
- `run_command`
- `list_directory`
- `directory_tree`
- `grep_files`
- `search_internet`
- `fetch_url`
- `search_reference`
- `query_project_context`
- `update_scratchpad`
- `add_journal_entry`
- `task_complete`

Conditional tools:

- Wiki tools only when wiki config is present.
- UI verification tools only when `enable_ui_verification` is true.

Tool executor requirements:

- Resolve paths relative to repo root.
- Detect external paths and request approval.
- Validate required parameters.
- Apply command safety classification.
- Save oversized command output to `.lean_ai/tool_output`.
- Compress tool output when configured and useful.
- Fire WebSocket progress and training capture events.
- Support diff decisions.

Source anchors:

- `backend/src/lean_ai/llm/tool_definitions.py`
- `backend/src/lean_ai/workflow/tool_executor.py`
- `backend/src/lean_ai/tools/file_ops.py`
- `backend/src/lean_ai/tools/shell.py`
- `backend/src/lean_ai/tools/git_ops.py`
- `backend/src/lean_ai/tools/internet.py`
- `backend/src/lean_ai/tools/wiki.py`
- `backend/src/lean_ai/tools/command_safety.py`

## 18. File Operations

File tools must:

- Safely resolve paths inside the workspace unless approval is granted for external paths.
- `read_file` returns line-numbered text and supports `start_line`/`end_line`.
- `read_file` can render `.docx` to markdown when `python-docx` is installed.
- `create_file` writes complete content for new files.
- `edit_file` uses search/replace, supports fuzzy matching and reindentation helpers, and returns diffs.
- `grep_files` searches files by pattern and optional file glob.

Source anchor: `backend/src/lean_ai/tools/file_ops.py`

## 19. Shell and Git Operations

Shell tools:

- `run_tests`
- `run_lint`
- `format_code`
- `run_command`

Commands run non-interactively with timeout `tool_timeout_seconds`.

Git operations must support:

- status, diff, current branch/SHA, repo detection.
- branch creation/checkouts/merge/delete.
- default branch detection.
- stash push/pop for resume safety.
- add-and-commit.
- optional GitHub co-author trailer controlled by config.

Source anchors:

- `backend/src/lean_ai/tools/shell.py`
- `backend/src/lean_ai/tools/subprocess_utils.py`
- `backend/src/lean_ai/tools/git_ops.py`
- `backend/src/lean_ai/config.py`

## 20. Persistence

### 20.1 Workspace State Database

The main database lives at:

```text
<repo_root>/.lean_ai/lean_ai.db
```

Tables:

- `sessions`
- `tool_logs`
- `conversation_logs`
- `session_commits`
- `session_memories`
- `architecture_decisions`
- `checkpoints`
- `prompt_versions`
- `prompt_variants`
- `ab_tests`

Migrations add columns for branch/merge/stash state and memory curation.

Source anchor: `backend/src/lean_ai/db.py`

### 20.2 Workflow State Files

Workflow state is persisted per session under `.lean_ai/state` and checkpoints under `.lean_ai/checkpoints`. `StateManager` manages active state, checkpoint listing, restore, scratchpad content, journal entries, observations, current phase, current plan, and session metadata.

Source anchor: `backend/src/lean_ai/workflow/state.py`

### 20.3 Context DB

The context DB stores extracted project-context entries in `context_entries` with section, file path, content, source, timestamps, and content hash. It supports upsert batches, delete-by-file, query, stats, and markdown export.

Source anchor: `backend/src/lean_ai/context/context_db.py`

### 20.4 Notes DB

Notes are global/local application state, not per-session workflow state. Tables:

- `notes`
- `note_todos`

Notes are indexed with Whoosh for search and can be auto-categorized by a background LLM worker.

Source anchors:

- `backend/src/lean_ai/notes_db.py`
- `backend/src/lean_ai/notes_index.py`
- `backend/src/lean_ai/notes_llm.py`

### 20.5 Integrations DB

Integration state uses:

- `task_links`
- `sync_log`
- `integration_config`

Source anchor: `backend/src/lean_ai/integrations/db.py`

### 20.6 Training DB

The training archive lives at `.lean_ai/training.db` by default. It must include:

- `training_traces`
- `plan_decisions`
- `validation_attempts`
- `workflow_events`
- `redaction_audit`
- `tool_executions`
- `tool_compressions`
- `clarifications`
- `phase2_syntheses`
- `diff_decisions`
- `trace_spans`
- `session_feedback`
- `evaluation_datasets`
- `evaluation_dataset_members`
- `evaluation_runs`
- `evaluation_results`

It has migrations for role, turn index, trace UUIDs, spans, feedback, evaluations, prompt version IDs, and validation attempt fields.

Source anchors:

- `backend/src/lean_ai/training/db.py`
- `backend/src/lean_ai/training/capture.py`
- `docs/training.md`

## 21. Indexing

Workspace indexing must:

- Walk repo files with gitignore-aware filtering.
- Chunk source files with tree-sitter-aware chunking where possible, falling back to line chunking.
- Store a Whoosh index under `index_dir` (default `.lean_ai_index`).
- Use a manifest for incremental indexing based on file hashes and head commit.
- Optionally generate Ollama embeddings and store them in a binary embedding store.
- Support search with BM25 and optional semantic reranking/RRF style behavior.

Source anchors:

- `backend/src/lean_ai/indexer/tree.py`
- `backend/src/lean_ai/indexer/chunker.py`
- `backend/src/lean_ai/indexer/indexer.py`
- `backend/src/lean_ai/indexer/manifest.py`
- `backend/src/lean_ai/indexer/embeddings.py`

## 22. Language Metadata

Language support is data-driven:

- Language definitions are loaded from YAML files in `backend/src/lean_ai/languages`.
- Each definition specifies extensions, tree-sitter grammar module, query patterns, test patterns, fan-in behavior, and metadata extraction rules.
- `extract_file_metadata` uses tree-sitter parsers and queries to extract definitions/imports.

Source anchors:

- `backend/src/lean_ai/languages/definitions.py`
- `backend/src/lean_ai/languages/registry.py`
- `backend/src/lean_ai/languages/extractor.py`
- `backend/src/lean_ai/languages/*.yaml`

## 23. Project Context Generation

`/init-workspace` and `/generate-project-context` must:

- Detect project commands and write `.lean_ai/commands.json`.
- Build deterministic file skeletons and ranked file candidates.
- Generate or update `.lean_ai/project_context.md`.
- Support multi-round context generation for larger repositories.
- Store structured context entries in the context DB.
- Skip generation when requested and the file already exists.
- Emit SSE progress and thinking when streaming.

Custom steering docs and architecture context are loaded from `.lean_ai` files where present.

Source anchors:

- `backend/src/lean_ai/routers/generation.py`
- `backend/src/lean_ai/context/generation.py`
- `backend/src/lean_ai/context/content.py`
- `backend/src/lean_ai/context/extraction_parser.py`
- `backend/src/lean_ai/context/context_db.py`
- `backend/src/lean_ai/routers/context_helpers.py`

## 24. Command Detection

Command detection must inspect common project files and infer:

- `format`
- `lint_fix`
- `lint`
- `test`

Supported ecosystems include Python, Node, Ruby, Go, Rust, Java, C#, and PHP.

Source anchor: `backend/src/lean_ai/context/command_detection.py`

## 25. Reference Library

The reference library lives under `.lean_ai/reference` by default. It must:

- Support Markdown, text, HTML, EPUB, PDF, and DOCX readers, with optional dependencies for EPUB/PDF/DOCX.
- Chunk prose with configurable chunk size and neighbor windows.
- Store a Whoosh reference index under `.lean_ai_reference_index`.
- Track chunk config and trigger rebuilds when config changes.
- Support incremental and full indexing.
- Optionally generate embeddings.
- Search reference docs and expand hits with neighboring chunks.

Source anchors:

- `backend/src/lean_ai/reference/indexer.py`
- `backend/src/lean_ai/reference/chunker.py`
- `backend/src/lean_ai/reference/readers/*`
- `docs/reference-library.md`

## 26. Local Refiner

The local refiner is used mainly before cloud provider calls. It must:

- Use local Ollama settings.
- Optionally inject reference-library context.
- Optionally strip sensitive data.
- Return refinement metadata including privacy redactions.
- Fail open within timeout rather than blocking core workflows.

Source anchors:

- `backend/src/lean_ai/llm/refiner.py`
- `backend/src/lean_ai/config.py`
- `backend/src/lean_ai/routers/chat.py`

## 27. Internet and Wiki Search

Internet search must support:

- DuckDuckGo.
- SearXNG.
- Google via Selenium browser search.
- Bing via Selenium browser search.

Search must enforce a configurable delay with jitter and fetch URLs with timeout. Long fetched content is summarized/paginated before returning to the LLM.

MediaWiki tools are optional and require `wiki_url`; authenticated wikis can use username/password.

Source anchors:

- `backend/src/lean_ai/tools/internet.py`
- `backend/src/lean_ai/tools/browser_search.py`
- `backend/src/lean_ai/tools/html_utils.py`
- `backend/src/lean_ai/tools/wiki.py`
- `backend/src/lean_ai/config.py`

## 28. UI Verification

UI verification is disabled by default and must only appear in tool lists when `enable_ui_verification` is true.

It provides:

- `verify_web_ui`: install/use workspace-local Chromium through Playwright, capture a URL, analyze screenshot.
- `verify_desktop_ui`: launch a desktop app, capture its window, analyze screenshot, and terminate the subprocess.

Analysis must include:

- A focused answer.
- Structured UI inventory.
- Text transcription.
- Pixel-sampled colors.
- Warnings.

Capture support:

- Web: Chromium path under `<workspace>/.lean_ai/browsers`.
- Desktop: Windows, macOS, Linux X11, partial Wayland/grim path depending on detected backend and deps.

Source anchors:

- `backend/src/lean_ai/tools/ui_capture_web.py`
- `backend/src/lean_ai/tools/ui_capture_desktop.py`
- `backend/src/lean_ai/tools/ui_analysis.py`
- `backend/src/lean_ai/tools/ui_verification.py`
- `backend/src/lean_ai/routers/ui_verification.py`
- `docs/ui-verification.md`

## 29. Voice System

Voice features are optional and controlled by settings.

STT:

- Uses faster-whisper.
- Supports start, stop, warmup, auto-stop, language, beam size, CPU threads.

TTS:

- Uses kokoro-onnx.
- Supports model quality, voice, speed, CPU threads.
- Can synthesize base64 audio, stream encoded audio, or stream PCM.
- Can ensure/download TTS models.

Wake word:

- Uses openWakeWord when installed.
- Emits SSE voice events.
- Integrates with STT auto-stop callbacks.

Source anchors:

- `backend/src/lean_ai/routers/voice.py`
- `backend/src/lean_ai/voice/stt.py`
- `backend/src/lean_ai/voice/tts.py`
- `backend/src/lean_ai/voice/wake_word.py`
- `backend/src/lean_ai/voice/audio_manager.py`
- `backend/src/lean_ai/voice/availability.py`
- `extension/src/backendVoiceClient.ts`
- `extension/src/sidebarVoice.ts`

## 30. Memory System

Session memory must:

- Store memory rows in `session_memories`.
- Include category, content, tags, source task, curation status, confidence, expiry, phase/model metadata, seen counts.
- Detect similar memories and bump seen counts.
- Support user confirmation, rejection, deletion, auto-promotion, TTL expiry.
- Index memories with Whoosh.
- Retrieve memories during planning Phase 3 and validation/fix loops according to configured curation statuses and budgets.
- Extract memories from session summaries, plan rejections, and fix successes.

Source anchors:

- `backend/src/lean_ai/memory/db.py`
- `backend/src/lean_ai/memory/extractor.py`
- `backend/src/lean_ai/memory/index.py`
- `backend/src/lean_ai/memory/session_tools.py`
- `backend/src/lean_ai/llm/planner_helpers.py`
- `backend/src/lean_ai/workflow/hooks.py`

## 31. Training Capture and Export

Training capture is local by default and export is gated by `export_api_key`.

Capture must support:

- LLM turns.
- Tool executions.
- Tool compressions.
- Clarifications.
- Phase 2 syntheses.
- Diff decisions.
- Plan decisions.
- Validation attempts.
- Workflow events.
- Feedback.
- Trace spans.

Scrubbing:

- Payloads pass through a strict scrubber.
- Redaction audits are persisted.
- Export formats can anonymize workspace IDs and memory content.

Export formats:

- Raw JSONL.
- SFT JSONL.
- KTO JSONL.
- DPO JSONL.

Source anchors:

- `backend/src/lean_ai/training/capture.py`
- `backend/src/lean_ai/training/db.py`
- `backend/src/lean_ai/training/scrubber.py`
- `backend/src/lean_ai/training/export_formats.py`
- `backend/src/lean_ai/training/memory_anonymizer.py`
- `backend/src/lean_ai/routers/export.py`

## 32. Observability

Observability must expose:

- Session summaries.
- Session detail.
- Trace spans and trace trees.
- Feedback creation/listing.
- Summary metrics.
- Token metrics.
- Latency metrics.
- Tool metrics.

Write access for feedback is protected by export key semantics.

Source anchors:

- `backend/src/lean_ai/routers/observability.py`
- `backend/src/lean_ai/training/span_context.py`
- `backend/src/lean_ai/training/db.py`
- `extension/src/observabilityPanel.ts`
- `extension/src/observabilityPanelHtml.ts`

## 33. Integrations

Integrations are optional and gated by `enable_integrations`.

Supported providers:

- GitHub.
- Jira Cloud.
- ServiceNow.

Provider interface requirements:

- Health checks.
- List/get/search external tasks.
- Push session summary.
- Link/unlink local sessions to external tasks.
- Receive webhooks.
- Shutdown cleanup.

Startup auto-initializes providers when config credentials are present.

Source anchors:

- `backend/src/lean_ai/main.py`
- `backend/src/lean_ai/integrations/base.py`
- `backend/src/lean_ai/integrations/github.py`
- `backend/src/lean_ai/integrations/jira.py`
- `backend/src/lean_ai/integrations/servicenow.py`
- `backend/src/lean_ai/integrations/registry.py`
- `backend/src/lean_ai/routers/integrations.py`

## 34. Scaffolding

Scaffold recipes live under `backend/src/lean_ai/scaffolds/<name>/scaffold.yaml`.

The scaffold registry must:

- Load all recipes.
- Expose display name, description, language, framework, aliases, setup type.
- Substitute variables such as project name/package name.
- Create files and optionally run setup commands.

Current recipe families include Ansible, C, C++, C#, Go, Java Spring, JavaScript/Express, PHP Laravel, Python/Django/FastAPI/Flask/basic, Ruby/Rails/basic, Rust, TypeScript/Express/Next.js/React/basic, and job-search.

Source anchors:

- `backend/src/lean_ai/tools/scaffold.py`
- `backend/src/lean_ai/scaffolds/*/scaffold.yaml`
- `backend/src/lean_ai/routers/scaffold_endpoints.py`

## 35. Job Assistant

The VS Code extension implements job-search slash commands using prompts and workspace utility endpoints:

- `/interview-prep`
- `/batch-prep`
- `/ats-check`
- `/thank-you`
- `/recruiter-reply`
- `/negotiate`
- `/analyse-rejection`
- `/log-applied`
- `/mock-interview`

Workspace conventions:

- Applications live under `applications/{slug}/`.
- Resume conversion uses the backend DOCX conversion endpoint.
- `/log-applied` updates `applications.md` and can git-commit the application folder.
- Mock interview runs in chat with extended turns.

Source anchors:

- `extension/src/jobSearchPrompts.ts`
- `extension/src/slashCommandsWorkspace.ts`
- `backend/src/lean_ai/routers/workspace.py`
- `docs/job-assistant.md`

## 36. VS Code Extension

The VS Code extension package:

- Name: `lean-ai`
- Publisher: `lean-ai`
- Current package version in code: `0.21.11`
- Engine: VS Code `^1.93.0`
- Main: `./dist/extension.js`
- Activation event: `onStartupFinished`

Contributed views:

- Activity bar container `lean-ai-sidebar`.
- Webview `lean-ai.chatView`.
- Tree view `lean-ai.sessionsView`.

Commands:

- Approve/reject/focus.
- Backend restart/stop/reinstall.
- Session refresh/view/restore/merge/abandon/delete.
- Open settings/prompts/notes/memories/observability/chat in new window.
- Install/test UI verification.

The extension must:

- Start or connect to the backend on activation.
- Register a webview chat provider with retained context.
- Register an inline completion provider for all files.
- Register a sessions tree provider.
- Initialize notifications.
- Register UI verification watcher.
- Persist conversations.
- Keep workflow WebSocket alive when the panel is disposed.
- Sync settings to backend config/env and store API keys in VS Code SecretStorage.

Source anchors:

- `extension/package.json`
- `extension/src/extension.ts`
- `extension/src/sidebarProvider.ts`
- `extension/src/backendProcess.ts`
- `extension/src/backendInstaller.ts`
- `extension/src/settingsSync.ts`
- `extension/src/conversationManager.ts`
- `extension/src/sessionTreeProvider.ts`
- `extension/src/notifications.ts`

## 37. VS Code Chat Behavior

All normal user input starts in chat mode unless a slash command redirects it.

The sidebar provider:

- Builds slash commands via `createSlashCommands`.
- Tracks chat history and workflow session ID.
- Supports context pills for Problems and Debug data.
- Can speak TTS, listen for STT, and wake word events.
- Shows first-boot setup when the backend is unreachable.
- Sends a greeting after voice/backend probe when appropriate.
- Routes WebSocket messages through `handleWsMessage`.

Slash commands include:

- `/init`
- `/style`
- `/scaffold`
- `/agent`
- `/fix`
- `/request`
- `/skill`
- `/tune-roles`
- `/approve`
- `/reject`
- `/resume`
- `/note`
- `/memories`
- `/help`
- job assistant commands listed above

Source anchors:

- `extension/src/sidebarProvider.ts`
- `extension/src/sidebarChat.ts`
- `extension/src/sidebarVoice.ts`
- `extension/src/slashCommands.ts`
- `extension/src/slashCommandsWorkspace.ts`
- `extension/src/wsHandler.ts`

## 38. VS Code Backend Client

The extension backend client must:

- Be a singleton.
- Resolve backend URL from `lean-ai.backendUrl`, defaulting to `http://localhost:8422`.
- Derive WebSocket URL by replacing `http` with `ws`.
- Use raw Node `http`/`https` no-timeout POST helpers for long LLM calls.
- Support SSE POST consumption.
- Delegate voice methods to `backendVoiceClient`.
- Delegate workspace/chat/prompt/note/memory/predict methods to `backendWorkspaceClient`.
- Support session CRUD, restore, merge, abandon, health, and WebSocket reconnect.

Source anchors:

- `extension/src/backendClient.ts`
- `extension/src/backendWorkspaceClient.ts`
- `extension/src/backendVoiceClient.ts`
- `extension/src/constants.ts`

## 39. VS Code Managed Backend Installation

Managed backend installation must:

- Copy bundled backend source into a managed target.
- Exclude caches, virtualenvs, node_modules, egg-info, pycache, and similar generated files.
- Require Python `>=3.10`.
- Create a `.venv`.
- Install backend package and optional extras.
- Verify imports: `lean_ai`, `tree_sitter`, `fastapi`, `uvicorn`, `ollama`.
- Cache managed Python/backend paths in global state.
- Support reinstall/reset and optional extras detection.

Backend startup:

- Honors `lean-ai.autoStartBackend`.
- Uses configured `backendDir`/`pythonPath` when set.
- Starts `uvicorn lean_ai.main:app` on configured host/port.
- Polls health.
- Monitors health and can notify/restart on down/unresponsive states.

Source anchors:

- `extension/src/backendInstaller.ts`
- `extension/src/backendProcess.ts`
- `extension/scripts/copy-backend.js`
- `extension/local_build.sh`

## 40. VS Code Settings

The extension contributes many `lean-ai.*` settings and syncs them to backend config:

- Backend URL/autostart/python/backend dir.
- Inline predictions, OS notifications, chat font size, username.
- Provider/model settings for Ollama/OpenAI/Anthropic/Gemini/Serve.
- Expert/request/worker roles.
- Inline, embedding, vision, voice, UI verification.
- Search provider and API URL/delay.
- Chat turn budgets.
- Integrations.
- Wiki.
- Post-validation commands/retries.
- Reference and context settings.
- Implementation and planning knobs.
- Thinking, preserve-thinking, reasoning effort, max thinking tokens.

Secret values are handled through VS Code SecretStorage, mapped by `settingsSync`.

Source anchors:

- `extension/package.json`
- `extension/src/settingsSync.ts`
- `extension/src/settingsPanel.ts`
- `extension/src/settingsPanelHtml.ts`

## 41. Inline Predictions

Inline predictions:

- Are provided by `LeanAIInlineProvider`.
- Are globally registered for all file patterns.
- Are enabled by `lean-ai.enableInlinePredictions`.
- Use context constants:
  - debounce 300 ms.
  - 50 prefix lines.
  - 20 suffix lines.
- Call backend `/api/predict` with file path, language, prefix, suffix, cursor line, cursor character.

Source anchors:

- `extension/src/inlineProvider.ts`
- `extension/src/constants.ts`
- `backend/src/lean_ai/routers/info.py`
- `backend/src/lean_ai/routers/models.py`

## 42. JetBrains Plugin

The JetBrains plugin:

- Uses Gradle Kotlin DSL.
- Uses Kotlin JVM `1.9.25`.
- Uses IntelliJ Platform Gradle plugin `2.1.0`.
- Targets JVM toolchain 17.
- Depends on OkHttp, Gson, and kotlinx-coroutines.
- Copies backend source into plugin resources before processing resources.

Plugin descriptor requirements:

- Plugin ID `com.leanai.plugin`.
- Tool windows:
  - `Lean AI` chat tool window.
  - `Lean AI Sessions`.
- Startup activity `LeanAiPlugin`.
- Inline completion provider `LeanAiInlineCompletionProvider`.
- Application service `LeanAiSettings`.
- Settings configurable under Tools > Lean AI.
- Notification group `Lean AI`.
- Actions: approve, reject, restart backend.

Source anchors:

- `jetbrains-plugin/build.gradle.kts`
- `jetbrains-plugin/gradle.properties`
- `jetbrains-plugin/src/main/resources/META-INF/plugin.xml`
- `jetbrains-plugin/src/main/kotlin/com/leanai/plugin/*`

## 43. JetBrains Backend and Client Behavior

JetBrains plugin must:

- Detect Python.
- Install/start backend from bundled resources.
- Sync settings.
- Use OkHttp for HTTP and WebSocket.
- Reconnect WebSocket with bounded attempts.
- Provide chat bridge to JCEF webview.
- Route parsed WebSocket messages to UI callbacks.
- Provide sessions panel and actions.
- Provide inline completions.

Source anchors:

- `jetbrains-plugin/src/main/kotlin/com/leanai/plugin/backend/BackendProcess.kt`
- `jetbrains-plugin/src/main/kotlin/com/leanai/plugin/backend/BackendInstaller.kt`
- `jetbrains-plugin/src/main/kotlin/com/leanai/plugin/backend/BackendClient.kt`
- `jetbrains-plugin/src/main/kotlin/com/leanai/plugin/ui/ChatBridge.kt`
- `jetbrains-plugin/src/main/kotlin/com/leanai/plugin/ws/WebSocketHandler.kt`
- `jetbrains-plugin/src/main/kotlin/com/leanai/plugin/completion/LeanAiInlineCompletionProvider.kt`
- `jetbrains-plugin/src/main/kotlin/com/leanai/plugin/settings/*`

## 44. Security and Safety

Required safety behaviors:

- Config secrets can be encrypted in backend YAML.
- VS Code stores API keys in SecretStorage, not plaintext config.
- File tools resolve workspace paths and reject traversal.
- External path access requires user approval through tool approval.
- Command safety classifies commands as safe, approval-required, or blocked.
- Workflow branch isolation keeps each task on a plan branch.
- Resume stashes current user changes before checking out a session branch, and restores stash on checkout failure.
- Training scrubber redacts secrets/PII and can fail closed.
- Internet/wiki/search are optional and configurable.
- UI verification is opt-in.

Source anchors:

- `backend/src/lean_ai/crypto.py`
- `extension/src/settingsSync.ts`
- `backend/src/lean_ai/tools/file_ops.py`
- `backend/src/lean_ai/workflow/tool_executor.py`
- `backend/src/lean_ai/tools/command_safety.py`
- `backend/src/lean_ai/tools/git_ops.py`
- `backend/src/lean_ai/routers/sessions.py`
- `backend/src/lean_ai/training/scrubber.py`
- `backend/src/lean_ai/config.py`

## 45. Build, Test, and Package

Backend:

```bash
cd backend
pip install -e ".[dev]"
uvicorn lean_ai.main:app --reload --port 8422
pytest
ruff check src tests
```

VS Code extension:

```bash
cd extension
npm install
npm run build
npx vsce package --no-dependencies
```

The extension package includes scripts for copying backend files into the extension bundle.

JetBrains plugin:

```bash
cd jetbrains-plugin
./gradlew buildPlugin
```

Source anchors:

- `README.md`
- `backend/pyproject.toml`
- `extension/package.json`
- `extension/scripts/copy-backend.js`
- `jetbrains-plugin/build.gradle.kts`
- `docs/Developer_Update.md`

## 46. Tests

Backend tests are pytest-based and cover:

- Config.
- LLM client/facade behavior.
- Tool execution.
- File/git ops.
- Browser search.
- UI verification.
- Role tuning.
- Prompt registry.
- Workflow graph/state/protocol/pipeline.
- Training capture/export/scrubbing/maintenance.
- Integrations.
- Observability.

VS Code tests are Jest-based and cover:

- WebSocket handler behavior.
- Slash command regex and edge cases.
- Assistant stream segmentation.
- Chat message regressions.
- Tune roles command.
- Mock interview prompt.

Source anchors:

- `backend/tests`
- `extension/src/__tests__`
- `extension/jest.config.js`
- `backend/pyproject.toml`

## 47. Reproduction Checklist

To reproduce the application from scratch:

1. Create a Python package `lean-ai` with the backend layout and dependencies described above.
2. Implement `Settings`, encrypted config, CLI config tools, and provider factories.
3. Implement FastAPI app, router aggregation, all REST routes, and WebSocket workflow route.
4. Implement SQLite schemas and migrations for workspace, notes, context, integrations, and training.
5. Implement LLM provider adapters and the `LLMClient` tool loop with callbacks, metrics, refresh, reminders, capture, and guardrails.
6. Implement prompt registry and role tuning with workspace-scoped overrides.
7. Implement tool schemas and tool executor, including file/shell/git/internet/wiki/reference/UI verification/scratchpad/journal tools.
8. Implement indexing, metadata extraction, project context generation, command detection, reference library, and memory retrieval.
9. Implement plan/fix/request workflows, plan approval/revision, execution, checkpoints, validation, and branch merge/abandon.
10. Implement training capture/export, scrubber, trace spans, observability, and optional integrations.
11. Implement voice and UI verification optional subsystems.
12. Implement scaffolds and job-assistant workspace utilities.
13. Build the VS Code extension with backend management, chat webview, slash commands, WebSocket handling, session tree, settings, prompt/notes/memories/observability panels, inline predictions, voice, UI verification commands, and tests.
14. Build the JetBrains plugin with backend management, settings, chat/sessions tool windows, WebSocket handling, actions, inline completions, and bundled backend copy task.
15. Verify with backend pytest/ruff, extension Jest/build, and JetBrains Gradle build.

## 48. Known Source-Backed Caveats

- The old uppercase `SPECIFICATION.md` is not authoritative for current code; it omits newer subsystems such as Gemini, Serve, UI verification, observability, and integrations.
- Some docs describe a "5-phase" or "6-phase" pipeline, while current planner code is organized as a 4-phase decomposed pipeline with additional Phase 4a/TDD and verification helpers. Reproduce the code behavior, not stale wording.
- The VS Code `CreateSessionResponse` interface in `extension/src/types.ts` has `stage`, while the backend response model returns `status`; actual client code should tolerate current backend shape.
- `GET /sessions/{session_id}/git-events` is implemented as a stub returning `[]`.

Source anchors:

- `SPECIFICATION.md`
- `docs/architecture.md`
- `docs/example-flow.md`
- `backend/src/lean_ai/llm/planner.py`
- `backend/src/lean_ai/routers/sessions.py`
- `extension/src/types.ts`

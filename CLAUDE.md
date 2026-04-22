# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Lean AI** — an agentic coding assistant that uses a single local LLM (via Ollama) with a simple philosophy: plan well, give the LLM tools, let it work. Python backend with FastAPI (REST + WebSocket), minimal SQLite persistence, and a TypeScript VSCode extension.

Extracted from single_ai — keeps what works (project context generation, decomposed planning, native tool calling, scaffolding, reference library), drops what doesn't (11-state FSM, regex-based parsing, ContextWindowManager, stagnation detection, rubric system).

## Banned Dependencies

**`lightllm` is banned.** Do not add it as a direct or indirect dependency. If it appears in the dependency tree (e.g. pulled in by another package), do not build, do not run — remove the offending dependency first. Known security risk.

## Build & Run Commands

```bash
# Install backend (from repo root)
cd backend && pip install -e ".[dev]"

# Install with optional reference library deps (EPUB, PDF, Word support)
cd backend && pip install -e ".[dev,reference]"

# Install with OpenAI provider support
cd backend && pip install -e ".[dev,openai]"

# Install with Anthropic provider support
cd backend && pip install -e ".[dev,anthropic]"

# Install with Gemini provider support
cd backend && pip install -e ".[dev,gemini]"

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

> For a verbose, narrative-style walkthrough of an entire session — from the first chat message through post-execution validation, with every guardrail called out — see [docs/example-flow.md](docs/example-flow.md). For the tool-and-state audit focused on persistence, see [docs/workflow-flow.md](docs/workflow-flow.md).

1. **LLM Client** (`llm/`) — Multi-provider LLM abstraction. `LLMProvider` ABC (`base.py`) with implementations for Ollama (`client.py`), OpenAI (`provider_openai.py`), Anthropic (`provider_anthropic.py`), Gemini (`provider_gemini.py`), and Lean AI Serve (reuses `OpenAIProvider` with custom `base_url` since lean-ai-serve is 100% OpenAI-compatible). `LLMClient` facade (`facade.py`) handles the multi-turn `chat_with_tools()` orchestration loop with a unified turn supervisor — after each turn, `_evaluate_turn()` makes ONE decision (continue, nudge, refresh, or exit) instead of running multiple overlapping control subsystems. Delegates single-turn calls to the active provider. Optional shared `concurrency_semaphore` in `chat_raw()` throttles all concurrent LLM calls — set via `LEAN_AI_NUM_PARALLEL` to match `OLLAMA_NUM_PARALLEL`. Inline predictions (FIM) and embeddings always use Ollama. Context refresh is event-driven by token threshold (70% of context window) — drops old messages, re-reads context files from disk, injects scratchpad and session journal for continuity (no LLM summarization call). `chat_raw()` and `chat_structured()` accept optional `stream_callback` / `thinking_callback` for token-level streaming — used by the planner to stream progress during planning phases.

2. **Planning** (`llm/planner.py`, `llm/planner_helpers.py`, `llm/planner_exploration.py`) — 5-phase decomposed planning. Every phase output is a Pydantic schema in `plan_schema.py`; structured objects flow from phase to phase and are rendered to markdown only at the prompt boundaries downstream prompts still consume. `planner.py` contains the `create_plan()` orchestrator, Phase 3 two-pass synthesis, Phase 4 validators, and Phase 5 verification. `planner_helpers.py` contains pure utilities (`_retrieve_session_memories`, `_save_debug_phase`, `_extract_file_paths`, `_compact_file_summary`), WebSocket stage helpers, `assess_clarity()`, and `_revise_plan()`. `planner_exploration.py` contains Phase 2's tool-assisted codebase exploration with parallel and serial paths. **Model routing**: phases 1-2 (scope + codebase exploration) run on the **primary** model so the coder-tuned model reads files and traces code during exploration; the worker model already compresses large tool outputs at the executor layer so the primary isn't on its own. Phases 3-5 (design synthesis, plan assembly, verification) run on the **expert** model when configured. Implementation (per-step execution) runs on the primary model. The **request** model is reserved for chat and `/request` mode — it does not participate in the planner.

    - **Phase 1 (clarification / verification)** — runs on the primary model with a small read-only tool budget (`LEAN_AI_PLAN_PHASE1_MAX_TURNS`, default 5) via `chat_with_tools`. Tool set: `grep_files`, `read_file`, `list_directory`, `query_project_context`, `search_reference`, `task_complete`, plus `request_clarification` — an LLM-invoked tool that sends a `clarification_needed` WebSocket message and blocks on the user's reply via the dispatcher's approval queue. Phase 1's job is to verify the task is specific enough and end with a short summary paragraph; it does NOT produce the scope document itself. There is NO automatic pre-step that asks questions for the LLM — the LLM decides when to ask by calling the tool. `text_only_exit_count=1` lets clear tasks exit with zero tool calls. Prompts are `planning.scope_system` + `planning.scope_user` (both template-vared on `{PHASE1_MAX_TURNS}`). Debug output saved as `phase_1_clarification.md`.
    - **Phase 1a (scope document generation)** — a single `chat_structured` call (`_synthesize_scope` → `planning.scope_synthesis_system`) that translates the task + Phase 1 summary + project context slice into a validated `ScopeDocument` Pydantic model with 8 required fields (`problem`, `deliverables`, `in_scope`, `out_of_scope`, `downstream_consumers`, `assumptions`, `success_criteria`, `risks`). Phase 1a is **non-interactive** — it never asks the user anything. **Phase 2 is guaranteed to receive an 8-section scope**: validation failure triggers one retry with a corrective payload; a double failure falls through to `_fallback_scope_document(task)` which wraps the task text verbatim in `problem` and records a single assumption + risk flagging the fallback so Phase 2 reconstructs missing sections during exploration. The fallback path never emits raw prose. `format_scope_document` renders the `ScopeDocument` to the historical 8-section markdown so Phase 2/3/4's `{scope}` contract is unchanged. Debug output saved as `phase_1a_scope.md`.
    - **Phase 2 (file identification)** — runs on the primary model via `chat_with_tools` with the planning tool set MINUS reference-library tools PLUS `record_file_observation`. The user prompt opens with a strict ASSUMPTIONS checklist that works through each of Phase 1's verification hints before general exploration. Retention is deterministic: the model calls `record_file_observation(file_path, role, reason, relevant_sections, key_snippets)` for every relevant file, and observations are upserted by `file_path` into `.lean_ai/observations/{session_id}.json`. After the exploration loop exits, a final `chat_structured` synthesis pass (`_synthesize_file_summary` → `planning.exploration_synthesis_system`) coerces the observations + scratchpad + journal + prose into a validated `FileSummary` Pydantic model (`files_to_modify`, `files_to_create`, `files_read_for_context`, `missing_infrastructure`, `verified_references`, `assumptions_resolved`, `notes`). The `FileSummary` is rendered back to markdown for Phase 3/4's `{file_summary}` template variable, and the Pydantic object itself is returned from `run_phase2_exploration` alongside the markdown so Phase 4 can run set-membership validation against it. Parallel path (`num_parallel>=2`) is not hardened — it produces free-form text and returns `None` for the structured object; validators skip cleanly. Deferral documented in `incomplete.md`.
    - **Phase 3 (design + risk synthesis)** — runs on the expert model in two passes. Pass 1 is a `chat_with_tools` exploration/verification pass with `build_design_tools()` (search_internet, fetch_url, reference library, wiki, task_complete); `text_only_exit_count=1` single-shots when FileSummary's VERIFIED REFERENCES already cover every external surface. Pass 2 is a `chat_structured` call (`_synthesize_design_and_risks` → `planning.design_synthesis_system`) that coerces the Pass 1 prose + inputs into a `DesignAndRisks` Pydantic model (`naming_conventions`, `change_designs`, `missing_files`, `dependency_order`, `critical_risks`, `citations`, `notes`). The system prompt (`planning.design_system`) calls out `FileSummary.key_snippets` as authoritative transcriptions; the model trusts them rather than re-deriving. The old `_extract_missing_files` secondary LLM call is gone — `DesignAndRisks.missing_files` is the structured source of truth; Phase 4 derives `{missing_files}` via `_format_missing_files`. Old scratchpad/journal injection into Phase 3 is also gone — `FileSummary` is the authoritative bridge.
    - **Phase 4 (plan assembly)** — single `chat_structured` call on the expert model producing an `ExecutionPlan`. `ExecutionPlan.naming_conventions` is `list[NamingConvention]` (reused from Phase 3's schema). `ExecutionPlan.name_registry` is `list[NameRegistryEntry]` (entity + optional model_class, module_namespace, import_stmt, db_table, file_path, route_endpoint, registered_in, test_file). Two formatter helpers (`format_naming_conventions_for_prompt`, `format_name_registry_for_prompt`) render the lists back to the text shapes `build_step_system_prompt` expects, so the executor's per-step prompt contract is unchanged. After the structured call returns, four pure-Python validators (`_collect_known_paths`, `_check_hallucinated_paths`, `_uncovered_missing_files`, `_check_edit_create_consistency`) run set-membership checks over the structured Phase 2 + Phase 3 outputs — no regex on LLM prose. Warnings log AND append to `plan.plan_validation_warnings` (a `list[str]` field on `ExecutionPlan`) which the `approval_required` WebSocket message carries through to the extension's approval UI for user-visible warning display. When Phase 3 marked a `missing_file` as `blocking=True` and no plan step covers it, Phase 4 auto-triggers a single `_revise_plan` round with synthesised feedback; still-uncovered blocking files on the second pass fall through to warn-only.
    - **Phase 5 (verification step generation)** — always runs (Layer 4). A `chat_structured` call on the expert model producing a `VerificationPlan`. User prompt is registry-backed: `planning.verification_user_normal` (asks for test files + final `run_tests` step when `test_command` is present) or `planning.verification_user_tdd` (test files only, explicitly no `run_tests`). When `test_command` is empty, Phase 5 still runs — test files are seeded to disk and the `run_tests` step is omitted. The **strict-test-contract policy block** (`policy.strict_test_contract`, gated by `enable_strict_test_contract`) mandates programmatic-only testing, E2E hooks in source code, regression-file conventions for bug fixes, and regression tests for every Layer 9 `core_functionality` tag. Input helpers (`_build_verification_targets`, `_build_security_concerns`, `_format_testing_inventory`, `_format_core_functionality`) feed structured Phase 2 + Phase 3 data into the prompt: files needing coverage from `DesignAndRisks.change_designs` + `FileSummary.files_to_create`, security concerns from `critical_risks`, testing infrastructure from `FileSummary.testing_inventory`, core-functionality tags from `plan.core_functionality`. After the call, three validators append non-blocking warnings to `plan.plan_validation_warnings`: `_check_test_path_conventions` (flags test steps outside the test-directory convention), `_check_affected_files_covered` (Layer 2 — flags executable affected files with no test step), and `_check_core_functionality_covered` (Layer 9 — flags core tags missing a regression-convention test step). A safety net injects a final `run_tests` step when the LLM omitted one AND `test_command` is non-empty. In TDD mode, `plan.tdd_test_steps` holds the test-creation steps (a defensive `run_tests` filter stays as safety) and the implementation steps are renumbered to run afterwards; the plan schema's `plan_to_markdown()` renders TDD plans with "TEST PHASE (Expert Model)" and "IMPLEMENTATION PHASE (Primary Model)" headers for user approval.

    **Context flow across phases:** Phases 1-2 (primary model) receive the full `context` parameter (project_context.md + custom steering docs). Phases 3-4 (expert model) receive `project_context.md` directly (loaded from disk) so the expert has project architecture for design decisions. Phase 5 works from prior phase outputs only (plus `test_command`). Phase 3 and Phase 4 include anti-hallucination instructions — the LLM must not simulate running commands, invent file listings, or fabricate file contents. **Streaming planning output:** phases 1-3 stream thinking and content tokens to the extension via callbacks (phase 2 also streams tool call/result progress); phases 4-5 stream thinking tokens only (content is JSON). The pipeline creates planning-specific callbacks with a `streaming: True` flag to distinguish token-level updates from per-turn bulk content.

3. **Tools** (`tools/`) — `create_file`, `edit_file`, `read_file`, `run_command`, `run_tests`, `run_lint`, `format_code`, `list_directory`, `directory_tree`, `grep_files`, `update_scratchpad`, `add_journal_entry`, `record_file_observation` (Phase 2 only), `search_internet`, `fetch_url`, `search_reference`, `list_reference_documents`, `search_wiki`, `fetch_wiki_page`, `request_test_change` (TDD only). **File observations** (`tools/observations.py`) — `record_file_observation(file_path, role, reason, relevant_sections, key_snippets)` writes structured findings to `.lean_ai/observations/{session_id}.json`, upserted by `file_path`. Used by Phase 2 exploration so retention is deterministic instead of relying on the model transcribing into prose output. Cleaned up on session close alongside the scratchpad + journal. File ops produce diffs and enforce path traversal protection via `_safe_resolve()` (rejects `../` escapes and symlinks outside repo root). `read_file` has a 2MB size guard. Shell commands pass through a safety gate (`command_safety.py`); subprocesses are killed on timeout to prevent orphans. `run_command` is a general-purpose shell tool for build commands, migrations, and code generators. Destructive commands (rm, dd, chmod, git push, etc.) require user approval; safe commands execute without prompting. **Session journal** (`tools/journal.py`) — `add_journal_entry` provides an append-only per-session log at `.lean_ai/journals/{session_id}.md`. Unlike the scratchpad (overwrite-based volatile memory), journal entries are never lost — they persist across context refreshes and survive crashes for session recovery. Budget-gated at 3% of context window (`JOURNAL_CONTEXT_PERCENT`). Cleaned up on session close. Internet search + URL fetching with HTML strip + LLM summary sanitization — available in all execution modes so the LLM can look up error messages and documentation when stuck. Reference library search — `search_reference` queries the local reference library index (Whoosh BM25F + optional RRF re-ranking with embeddings) for domain documents (books, PDFs, EPUBs, manuals). Available in all tool sets (chat, planning, implementation, design). Returns full chunk content with document title and section. Optional `document` parameter restricts the search to a single document — value is matched as an exact `doc_path` first, then as a `doc_title` substring fallback. When `document` is supplied but matches no indexed doc, returns empty (does NOT silently fall back to unfiltered results). `list_reference_documents(name_filter="")` enumerates indexed docs (title, path, format, chunk_count) so the LLM can discover what is available before targeting a follow-up search. MediaWiki search (`tools/wiki.py`) — `search_wiki` + `fetch_wiki_page` query an internal MediaWiki instance via the Action API; supports authenticated (bot login) and public wikis; gated by `LEAN_AI_WIKI_URL` being non-empty. **Tool progress descriptions** (`tools/descriptions.py`) — `humanize_tool_call()` produces concise, user-facing progress messages (e.g. "Reading file.py", "Searching for 'pattern'") used by both WebSocket workflow callbacks and chat SSE endpoint. Test file detection utility (`tools/test_file_utils.py`) wraps the language registry's test patterns for TDD enforcement. **Chat tools** (separate tool set for the `/chat` endpoint): `save_note`, `list_project_todos`, `list_recent_sessions`, `get_session_summary`, `search_workspace_memory` — lightweight conversational tools for note-taking, project awareness, and cross-session memory retrieval.

4. **Workflow** (`workflow/`) — Split across five modules for separation of concerns. `pipeline.py` contains the plan-mode orchestrator (`run_workflow`, `_clarify_task`, `_wait_for_approval`). `executor.py` contains plan execution (`execute_plan`, `_build_step_groups`, `_run_step`, `_run_tdd_execution`, `_update_project_context`). `hooks.py` contains fire-and-forget post-completion hooks (`auto_push_integration`, `auto_extract_session_memories`). `fix_mode.py` contains fix and request mode (`_run_fix`). `validation.py` contains post-execution validation (`_run_post_validation`, `_run_validation_fix_loop`, `_effective_post_commands`). `callbacks.py` builds streaming callback sets for WebSocket progress; the `streaming` flag distinguishes token-level planning updates from per-turn bulk content. After execution (all modes), `_run_post_validation` runs deterministic lint/test/format passes — auto-fix passes (format, lint-fix) run sequentially since they modify files, then lint and test reporting passes run in parallel via `asyncio.gather`. On failure, `_run_validation_fix_loop` retries up to `post_validation_max_retries` times — each attempt gives the LLM a 30-turn budget with a structured verify-first workflow (re-run failing command → diagnose → fix → re-run to confirm). The expert model takes over on the final retry if configured. WebSocket-based progress streaming. No state machine library. Work branches always created from default branch (master/main). **TDD mode** (`workflow/tdd.py`): when `LEAN_AI_ENABLE_TDD` is enabled, plan execution runs three phases — (A) expert model writes all tests first from Phase 5 plan steps, (B) primary model reviews tests and can dispute upfront via `request_test_change`, (C) primary model implements code with test files protected (writes blocked, no disputes — must adapt to tests). Disputes are only available in Phase B (review) to keep behavior predictable. The dispute mechanism (`evaluate_test_dispute`) runs a tight 10-turn expert session that either accepts (edits the test) or rejects (explains why the test is correct and suggests an implementation approach). The TDD test-file guard and dispute mechanism also apply in the post-validation fix loop as a safety net.

5. **Persistence** (`db.py`) — Minimal SQLite via `aiosqlite`. Tables: `sessions`, `tool_logs`, `conversation_logs`, `session_commits`, `session_memories`. No ORM.

6. **Indexer** (`indexer/`) — Gitignore-aware tree listing. Tree-sitter AST-aware code chunking. Whoosh BM25F search. Embedding store with RRF re-ranking. SHA-256 manifest for incremental updates.

7. **Context Generation** (`context/generation.py`, `context/expansion.py`, `context/text_processing.py`) — Generates `.lean_ai/project_context.md` via single-pass or multi-round LLM calls. `generation.py` contains the public API (`generate_project_context`, `write_project_context`, `update_project_context`). `expansion.py` contains multi-round expansion logic (`_generate_project_context_multi_round`, `_expand_project_context`, `_merge_additions_into_doc`). `text_processing.py` contains deduplication and truncation utilities (`_truncate_repetition`, `_deduplicate_sections`, `_deduplicate_subsections`). Expansion batches fire concurrently via `asyncio.gather` using additions-only prompts (each batch receives section headings, returns only new entries, results merge programmatically). Tree-sitter metadata extraction with disk cache. Auto-scaling size caps proportional to context window. All concurrent LLM calls are throttled by the shared `chat_raw` semaphore (`LEAN_AI_NUM_PARALLEL`). Custom steering documents in `.lean_ai/context/` are loaded after the generated files (alphabetically) to provide additional project-specific guidance. Auto-detects lint/test/format commands from project dependency files (`command_detection.py`) during `/init-workspace` — saves to `.lean_ai/commands.json`, used as fallback when manual `LEAN_AI_POST_*` env vars are empty. Covers PHP, Python, Node/TS, Ruby, Go, Rust, Java, C#.

8. **Language Registry** (`languages/`) — 13 language definitions in YAML. Tree-sitter AST parsing (no regex patterns). Generic extraction engine for classes, functions, imports.

9. **Reference Library** (`reference/`) — Domain document indexing (EPUB, PDF, Word, Markdown, HTML, text). Prose-aware paragraph chunker — default target chunk size is **1800 chars (~450 tokens)** with derived overlap (`chunk_chars // 6`), tuned for long-form prose Q&A. All readers go through `chunk_prose_configured()` which reads `settings.reference_chunk_chars` so the size can be retuned per workspace. Separate Whoosh index with a `chunk_config.json` sentinel — when `reference_chunk_chars` changes the index auto-rebuilds on next indexing run (mixing old and new chunk sizes would corrupt retrieval). Incremental updates via SHA-256 manifest. **Small-to-big retrieval** in `search_reference`: after BM25+RRF ranking each hit is expanded with `±reference_neighbor_window` adjacent chunks from the same document and contiguous hit ranges are merged into a single coherent passage. Disable by setting `LEAN_AI_REFERENCE_NEIGHBOR_WINDOW=0`. **MediaWiki integration** (`tools/wiki.py`) — complements the local reference library with real-time search of an internal MediaWiki instance. Two tools: `search_wiki` (full-text search via `action=query&list=search`) and `fetch_wiki_page` (page content via `action=parse`). Uses the MediaWiki Action API with optional bot-account authentication (lazy login, session cookies cached). HTML stripped to plain text; long pages paginated to disk like `fetch_url`. Gated by `LEAN_AI_WIKI_URL` — when empty, wiki tools are excluded from all tool lists. Configurable from the extension's Advanced Settings tab (URL, API path, username, password in OS keychain).

10. **Scaffolding** (`scaffolds/`) — 19 YAML scaffold recipes for project bootstrapping.

11. **Integrations** (`integrations/`) — Generic two-way sync framework for external task tracking services (Jira, ServiceNow, etc.). `IntegrationProvider` ABC (`base.py`) with pull (list/get/search tasks), push (session summaries, status updates), and webhook handling. Registry (`registry.py`) with `@register_integration` decorator manages lifecycle. SQLite persistence (`db.py`) at `~/.lean_ai/integrations/integrations.db` stores task links, sync log, and config. Session summary builder (`summary.py`) extracts duration, files changed, commits from existing session data. REST endpoints (`routers/integrations.py`) for all operations. Auto-push hook in `workflow/hooks.py` fires on session completion. Gated by `LEAN_AI_ENABLE_INTEGRATIONS`.

12. **Training Archive** (`training/`) — Self-improvement Phase B+. Append-only capture of workflow decisions to a separate SQLite DB at `.lean_ai/training.db` (configurable via `LEAN_AI_TRAINING_DB_PATH`), distinct from the main workspace DB so retention/VACUUM never blocks workflow writes. Tables: `training_traces` (per LLM turn with messages + assistant_output preserved, scrubbed flag, optional `pair_id`/`preference`/`pair_kind` for DPO pairs), `plan_decisions` (approve/reject/cancel with plan_before + feedback + plan_after), `validation_attempts` (per-attempt diagnosis + fix_tool_calls + pass/fail), `workflow_events` (cancellation, tdd_dispute, execution_complete), `redaction_audit` (one row per scrubber match with `match_preview = sha256(match)[:12]` — never the raw secret). `training/scrubber.py` runs fail-closed on every payload before write: matches known secret shapes (OpenAI/Anthropic/Slack/GitHub/AWS tokens, JWTs, SSH keys, bearer headers, `LEAN_AI_*_KEY` env lines) plus high-entropy tokens (Shannon > 4 bits/char). `training/capture.py` exposes `capture_turn`, `capture_plan_decision`, `capture_validation_attempt`, `capture_workflow_event`. `training/maintenance.py` provides `run_retention_pass` (throttled to once/hour per workspace, fires opportunistically from `executor.py` after session completion), `auto_promote_memory` (incoming extracted memories matching an existing row bump `seen_count` instead of inserting; threshold `memory_autopromote_threshold` promotes `auto` → `high_confidence_auto`), `supersede_user_rejected` (skip re-introducing memories the user has already rejected), `bulk_invalidate_by_model` (demote all memories from a regressed model). Hooks wired at `pipeline.py:220,242` (plan_decision), `validation.py:388` (validation_attempt), `routers/workflow.py:318` (cancellation), `tdd.py:110` (tdd_dispute), `executor.py` (execution_complete). In-loop events (loop_detected, context_refresh, reminder_injected, claim_unverified) are deferred — `fire_workflow_event` + `on_workflow_event` scaffolding is in place in `workflow/hooks.py` but not yet threaded through `chat_with_tools` (tracked in `incomplete.md`).

13. **Export API** (`routers/export.py` + `training/export_formats.py` + `training/memory_anonymizer.py`) — authenticated REST for cross-workspace aggregation via an external coordinator (e.g. lean-ai-serve). Auth: `Authorization: Bearer <LEAN_AI_EXPORT_API_KEY>` (empty key → 503, disabled by default). Endpoints: `GET /api/export/workspace-id` (deterministic 16-char sha256 prefix, salt via `LEAN_AI_EXPORT_WORKSPACE_SALT`), `GET /api/export/manifest` (trace/plan/validation/event counts + per-model/phase/outcome breakdowns + memory curation counts; cached 60s), `GET /api/export/traces?format=raw|sft|dpo|kto` (streaming JSONL via `StreamingResponse`), `GET /api/export/memories?curation_status=...` (memory anonymizer builds workspace symbol table from tool_logs + imports, redacts file paths / module names / CamelCase symbols adjacent to code framing, drops memories where >40% was redacted — threshold `LEAN_AI_MEMORY_EXPORT_DROP_THRESHOLD`), `GET /api/export/events`. Every exported row has `session_id` → `sha256(salt:session_id)[:12]`, `repo_root` and basename → `/workspace-<id>` placeholders. Format converters: `to_sft_jsonl` preserves `reasoning_content` (thinking) when `LEAN_AI_CAPTURE_THINKING=true`, only emits `outcome='success'` rows; `to_dpo_jsonl` groups by `pair_id` and emits `{prompt, chosen, rejected}` (skips incomplete pairs); `to_kto_jsonl` emits binary labels from `preference ≥ 1` vs `≤ -1`. See `docs/training.md` for LoRA recipes + vLLM adapter hot-swap.

14. **Memory** (`memory/`) — Cross-session memory system for persisting project-specific discoveries, gotchas, patterns, and conventions. `extractor.py` runs after session completion (triggered by `hooks.py`) — the worker (or primary) LLM extracts structured `MemoryItem` objects (category, content, tags) via `chat_structured()`, queued for background processing. Categories: `architecture`, `build`, `testing`, `pattern`, `gotcha`, `convention`, `discovery`, plus Phase-A additions `rejection`, `fix_pattern`, `success_pattern`. `db.py` provides CRUD for the `session_memories` SQLite table. `index.py` maintains a per-workspace Whoosh full-text index at `.lean_ai/memory_index/` for fast retrieval. `session_tools.py` implements the chat tool backends (`list_recent_sessions`, `get_session_summary`, `search_workspace_memory`). **Curated-memory Phase A:** every memory row carries `curation_status` (`auto` | `user_confirmed` | `user_rejected` | `superseded` | `high_confidence_auto`), `confidence`, `expires_at`, `source_phase`, `model_name`, `seen_count`, and `last_seen_at`. Retrieval (`_retrieve_memories_for_phase` in `planner_helpers.py`) filters by `settings.memory_retrieval_statuses` (default `user_confirmed,high_confidence_auto`) and excludes expired rows — raw `auto` memories never enter planning prompts until confirmed. Three retrieval entry points are wired: `_retrieve_session_memories` into Phase 1, `retrieve_design_memories` into Phase 3 (gotcha + convention + rejection, via `enable_phase3_memory`), and `retrieve_fix_pattern_memories` into the validation fix-loop prompt (fix_pattern + gotcha, via `enable_fix_loop_memory`). Each gets its own 2% context budget. **Phase-specific extraction:** `schedule_plan_rejection_extraction` (from the `on_plan_decision` hook at `pipeline.py:220,242` — fires only when a prior rejection preceded approval), `schedule_fix_success_extraction` (from `on_validation_attempt_complete` at `validation.py:388` after a successful fix), and `schedule_tdd_dispute_extraction`. Extraction writes `auto` memories and fires a `memory_suggested` WebSocket message so the extension can surface an inline confirm/dismiss chip. **User curation UI:** extension-side `MemoriesPanel` (`extension/src/memoriesPanel.ts` + `memoriesPanelHtml.ts`) singleton WebviewPanel with Pending Review / Confirmed / Archive tabs, plus a manual "save this" composer. The inline chip in the chat stream (`sidebarHtml.ts` → `saveSuggestedMemory` / `dismissSuggestedMemory`) routes through `sidebarProvider.ts`'s `confirmMemory` / `rejectMemory` cases to the REST endpoints in `routers/memories.py` (`POST /api/memories/{id}/confirm`, `/reject`, `DELETE /api/memories/{id}`, `POST /api/memories` for manual creation, `GET /api/memories?curation_status=...`).

15. **Voice** (`voice/`) — Optional voice interaction: Speech-to-Text (faster-whisper), Text-to-Speech (kokoro-onnx), and wake word detection (openWakeWord). All voice services run on CPU only (GPU is reserved for the LLM). `AudioManager` singleton coordinates mic access — only one service (STT or wake word) captures at a time. Backend captures the mic directly via PyAudio (avoids VSCode webview audio restrictions). Extension UI has inline mic button, voice/speed controls, and TTS playback via HTML5 Audio with queuing. All deps in `voice` optional extras group. REST endpoints in `routers/voice.py`; SSE for wake word events and TTS streaming. TTS model files (~169MB fp16 default) auto-downloaded to `~/.cache/lean_ai/kokoro/` on first use. ALSA errors on Linux are suppressed via `alsa_suppression.py`.

16. **Runtime State** (`runtime_state.py`) — Process-wide registry of long-running tasks exposed via `/api/health`'s `busy` field. Primitives: `mark_busy(task)`, `mark_idle(task)`, `current_busy()`, plus a `busy(task)` context manager (try/finally ensures `mark_idle` on exception). Tags in active use: `embeddings.code` (wraps `indexer.generate_embeddings`), `embeddings.reference` (wraps `reference.indexer.generate_reference_embeddings`). The first embed call inside `generate_embeddings` naturally triggers any cold load of the embedding model in Ollama — there is no separate warmup pass (a prior `check_embedding_model` warmup was removed because it loaded the model even when the workspace was already up-to-date and nothing needed re-embedding, wasting VRAM and causing the model to sit idle until Ollama's keep-alive unloaded it). `check_embedding_model` now only verifies existence via `ollama show` and returns instantly. The extension's health monitor never auto-restarts on slow/timeout probes — only on `ECONNREFUSED`/`ECONNRESET` — so the tags are visible on `/api/health` primarily for humans and diagnostics. `generate_embeddings` and `generate_reference_embeddings` also `await asyncio.sleep(0)` at the top of each producer-loop iteration so the event loop visits `/api/health` between batches. `compute_embedding_batch_size` is capped at 256 (was 1024) to keep worst-case batch duration bounded; users on fast hardware can override via `LEAN_AI_EMBEDDING_BATCH_SIZE`.

17. **UI Verification** (`tools/ui_capture_web.py` + `tools/ui_capture_desktop.py` + `tools/ui_analysis.py` + `tools/ui_verification.py` + `routers/ui_verification.py`) — Optional vision-backed screenshot analysis tools: `verify_web_ui` (headless Chromium via Playwright, workspace-isolated in `.lean_ai/browsers` via `PLAYWRIGHT_BROWSERS_PATH`) and `verify_desktop_ui` (launches a desktop app as a subprocess in its own process group, captures its window, kills the subprocess including children). Desktop adapter auto-detects platform: Windows (`pygetwindow` + `mss`), macOS (Quartz `CGWindowListCopyWindowInfo` + `screencapture -l` with Screen Recording permission detection — empty PNG after exit-0 indicates permission denial), Linux X11 (`wmctrl` + `xdotool getwindowgeometry --shell` + `mss`), Linux Wayland (`grim` full-screen — window-by-title isn't generally recoverable across Wayland compositors without per-compositor APIs; XDG Portal is a documented follow-up). Four-pass vision pipeline (`analyze_screenshot`): (1) schema-constrained inventory of regions + components on a 3×3 grid, (2) verbatim text transcription (dedicated pass — vision models hallucinate labels when asked holistically), (3) dominant-color palette via Pillow + NumPy k-means on a 100×100 downsampled copy (not from the LLM — hex codes it guesses are unreliable), (4) focused free-form answer synthesising the above via `describe_image` with a full system-prompt override. The three structured passes use `describe_image_structured` with temperature pinned to 0 and Ollama's `format=schema` enforcement. Tools are gated on `settings.enable_ui_verification` — when off, tool lists across all surfaces are byte-identical to today. When on, they appear in Phase 2 exploration, Phase 3 design, the implementation executor, chat, and fix-mode investigation; Phase 5 can emit plan steps that call them. Tool output is a single markdown string: focused answer first, supporting evidence (inventory, text, colors) second, warnings last. Outer timeout (`LEAN_AI_UI_VERIFICATION_TIMEOUT`, default 180s) wraps the whole flow via `asyncio.wait_for`. REST endpoints in `routers/ui_verification.py`: `GET /status`, `POST /install` (runs `python -m playwright install chromium`), `POST /test` (one-shot sanity-check capture). Extension surfaces config through the built-in Settings UI (`lean-ai.enableUiVerification`, viewport, wait, timeout), exposes `Install UI Verification (Chromium)` + `Test UI Verification Pipeline` commands, and runs a `onDidChangeConfiguration` watcher that prompts to install Chromium the first time the feature is toggled on. `ui-verification` extras group pins platform-conditional deps via PEP 508 environment markers: `pywin32` on win32, `pyobjc-framework-Quartz` on darwin, `dbus-next` on linux (reserved for future XDG Portal Wayland support). No visual regression gating in v1 — the LLM interprets the structured result, and hard assertions ("fail if answer mentions 'error message'") are a deliberate follow-up.

## Key Design Decisions

- **No regex for source code analysis** — all extraction uses tree-sitter AST queries
- **No ContextWindowManager** — Ollama manages its own KV cache; we focus on prompt quality
- **No rubric system** — user approval is the sole quality gate
- **No complex FSM or rubric-driven self-critique** — only lightweight guardrails for tool progress and recovery (see Guardrails section below)
- **Tool naming**: `create_file` (not `write_file`) for clearer intent
- **Structured JSON output** from Ollama replaces regex-based plan/output parsing
- **Percentage-based token budgets** — internal limits (scratchpad, journal, inline output, etc.) are computed as a percentage of the active context window, not hardcoded. This makes the system adaptive: smaller models get proportionally smaller budgets, larger models get more room. Convention: use `settings._active_context_window` and a named percentage constant (e.g. `SCRATCHPAD_CONTEXT_PERCENT = 0.05`, `JOURNAL_CONTEXT_PERCENT = 0.03`)
- **Canonical policy blocks** — all LLM system prompts are in `llm/prompts.py`. Shared rules (tool usage, completion signaling, quality, web search, scratchpad) are defined once as canonical policy blocks (`TOOL_POLICY`, `COMPLETION_CONTRACT`, `QUALITY_RULES`, `WEB_SEARCH_POLICY`, `SCRATCHPAD_POLICY`) and composed into mode-specific prompts via string concatenation. This prevents instruction duplication across execution modes — smaller models are sensitive to seeing the same rule from multiple voices. Guardrail nudges in `facade.py` are kept short and never introduce fresh policy; they reinforce the system prompt. Chat context injection in `routers/context_helpers.py` is budget-gated (`max_context_chars`) to avoid overwhelming smaller request models.
- **Four-model pipeline** — **request model** (chatty, higher temperature) for the chat conversation, requirements gathering, task clarification, and the `/request` workflow mode — it does NOT participate in the planner; **primary model** (tuned for coding) for planning phases 1-2 (scope + codebase exploration) and all implementation execution; **expert model** (large, reasoning-heavy) for planning phases 3-5 (design synthesis, plan assembly, verification) and the final validation fix retry (escalation only on last attempt); **worker model** (small, fast — e.g. `qwen3.5:2b-q8_0`) for auxiliary tasks: compressing large tool outputs before they enter the primary model's context, summarizing web content, and extracting cross-session memories. Any role falls back to the primary when not configured. All four can use any provider (Ollama, OpenAI, Anthropic, Gemini, or Lean AI Serve) independently — set `LEAN_AI_REQUEST_LLM_PROVIDER` / `LEAN_AI_EXPERT_LLM_PROVIDER` / `LEAN_AI_WORKER_LLM_PROVIDER` to select. Phases communicate through structured text/JSON outputs, not shared conversation history, making model switching seamless. The expert receives `project_context.md` directly in phases 3 and 4 for architectural awareness; custom steering docs are loaded via `load_execution_context()` for implementation. Planning phases use `load_planning_context()` (project_context + custom docs). At 32k context windows: TDD auto-disables, tool output limits scale down, file_summary is LLM-compacted, and Phase 4 drops redundant scope/context re-injection.
- **Parallel execution** — when `LEAN_AI_NUM_PARALLEL >= 2`: Phase 2 splits into fan-out (2a: broad scan) then merge (2b: parallel file reads); independent plan steps execute in parallel groups (dependency analysis via file path references and barrier tools). All parallelism is gated by the shared `concurrency_semaphore` in `chat_raw()`.

## Guardrails

Lightweight control mechanisms in the `chat_with_tools` orchestration loop. After each turn, `_evaluate_turn()` makes one decision — no overlapping subsystems.

| Mechanism | Trigger | Action |
|---|---|---|
| Text-only exit | 3 consecutive text-only responses | Exit loop |
| Truncation exit | 5 consecutive truncated responses | Exit loop |
| Text-only nudge | Text response without tool call | Inject "call task_complete if done, otherwise call your next tool" |
| Truncation nudge | Response cut off by max_tokens | Inject "output ONLY the tool call, nothing else" |
| Loop detection | N identical tool calls (default 3) | Inject "use a different approach" |
| Context refresh | Token usage crosses 70% of context window | Drop old messages, re-read from disk |
| Task reminder | Every N turns (default 10) | Re-inject task description |
| Cancel/interrupt | User sends cancel or new message | Raise error or inject interrupt |
| Claim verification | Tests/lint failed ≥2 times AND LLM claims something doesn't exist / is deprecated | Inject "search for current docs" nudge (resets counter) |

Post-execution controls:

| Mechanism | Scope | Limit |
|---|---|---|
| Deterministic validation | format, lint-fix, lint, test | One pass, auto-fix then report |
| Validation fix loop | File-scoped (plan's affected_files) | Up to N retries (default 2), 30 turns each |
| Expert escalation | Final validation retry only | Uses expert model if configured |
| TDD dispute | Phase B (review) only | Expert evaluates, accepts or rejects |
| Plan revision | Single targeted LLM call | Up to 5 rounds on user feedback |

## Technology Stack

| Concern | Library |
|---|---|
| Web framework | FastAPI (async, built-in WebSocket) |
| Database | aiosqlite (raw SQL, 2 tables) |
| Ollama SDK | ollama (official, async) |
| Gemini SDK | google-genai (unified Google GenAI SDK) |
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
| `LEAN_AI_LLM_PROVIDER` | `ollama` | LLM provider: `ollama`, `openai`, `anthropic`, `gemini`, or `serve` |
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
| `LEAN_AI_EXPERT_LLM_PROVIDER` | *(empty)* | Provider for expert model: `ollama`, `openai`, `anthropic`, `gemini`, or `serve`. Empty = auto-detect from `OLLAMA_MODEL_EXPERT` |
| `LEAN_AI_OPENAI_EXPERT_MODEL` | *(falls back to OPENAI_MODEL)* | OpenAI model for expert phases (e.g. `gpt-4o`) |
| `LEAN_AI_ANTHROPIC_EXPERT_MODEL` | *(falls back to ANTHROPIC_MODEL)* | Anthropic model for expert phases (e.g. `claude-opus-4-6`) |
| `LEAN_AI_GEMINI_EXPERT_MODEL` | *(falls back to GEMINI_MODEL)* | Gemini model for expert phases (e.g. `gemini-2.5-pro`) |
| `LEAN_AI_REQUEST_LLM_PROVIDER` | *(empty)* | Provider for `/request` mode: `ollama`, `openai`, `anthropic`, `gemini`, or `serve`. Empty = auto-detect |
| `LEAN_AI_OLLAMA_MODEL_REQUEST` | *(empty)* | Ollama model for `/request` mode (e.g. `qwen3.5:27b`). Empty = use primary model |
| `LEAN_AI_OLLAMA_REQUEST_TEMPERATURE` | *(falls back to OLLAMA_TEMPERATURE)* | Request model temperature |
| `LEAN_AI_OLLAMA_REQUEST_TOP_P` | *(falls back to OLLAMA_TOP_P)* | Request model top-p |
| `LEAN_AI_OLLAMA_REQUEST_TOP_K` | *(falls back to OLLAMA_TOP_K)* | Request model top-k |
| `LEAN_AI_OLLAMA_REQUEST_REPEAT_PENALTY` | *(falls back to OLLAMA_REPEAT_PENALTY)* | Request model repetition penalty |
| `LEAN_AI_OLLAMA_REQUEST_CONTEXT_WINDOW` | *(falls back to OLLAMA_CONTEXT_WINDOW)* | Request model context window (accepts shorthand) |
| `LEAN_AI_OLLAMA_REQUEST_MAX_TOKENS` | *(derived: 25% of request context window)* | Request model max output tokens |
| `LEAN_AI_OPENAI_REQUEST_MODEL` | *(empty)* | OpenAI model for `/request` mode |
| `LEAN_AI_ANTHROPIC_REQUEST_MODEL` | *(empty)* | Anthropic model for `/request` mode |
| `LEAN_AI_GEMINI_REQUEST_MODEL` | *(falls back to GEMINI_MODEL)* | Gemini model for /request mode |
| `LEAN_AI_WORKER_LLM_PROVIDER` | *(empty)* | Provider for worker model: `ollama`, `openai`, `anthropic`, `gemini`, or `serve`. Empty = auto-detect from `OLLAMA_MODEL_WORKER` |
| `LEAN_AI_OLLAMA_MODEL_WORKER` | *(empty)* | Ollama worker model for auxiliary tasks (e.g. `qwen3.5:2b-q8_0`). Empty = no worker model |
| `LEAN_AI_OLLAMA_WORKER_TEMPERATURE` | *(falls back to OLLAMA_TEMPERATURE)* | Worker model temperature |
| `LEAN_AI_OLLAMA_WORKER_CONTEXT_WINDOW` | *(falls back to OLLAMA_CONTEXT_WINDOW)* | Worker model context window (accepts shorthand) |
| `LEAN_AI_OLLAMA_WORKER_MAX_TOKENS` | *(derived: 25% of worker context window)* | Worker model max output tokens |
| `LEAN_AI_OPENAI_WORKER_MODEL` | *(falls back to OPENAI_MODEL)* | OpenAI model for worker tasks |
| `LEAN_AI_ANTHROPIC_WORKER_MODEL` | *(falls back to ANTHROPIC_MODEL)* | Anthropic model for worker tasks |
| `LEAN_AI_GEMINI_WORKER_MODEL` | *(falls back to GEMINI_MODEL)* | Gemini model for worker tasks |
| `LEAN_AI_SERVE_WORKER_MODEL` | *(falls back to SERVE_MODEL)* | Lean AI Serve model for worker tasks |
| `LEAN_AI_ENABLE_THINKING_WORKER` | `false` | Enable thinking mode for worker model (disabled by default for speed) |
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
| `LEAN_AI_GEMINI_API_KEY` | *(empty)* | Gemini API key (required when provider=gemini) |
| `LEAN_AI_GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model name |
| `LEAN_AI_GEMINI_TEMPERATURE` | `0.7` | Gemini sampling temperature |
| `LEAN_AI_GEMINI_CONTEXT_WINDOW` | `1048576` | Gemini context window (accepts shorthand, supports 1M+) |
| `LEAN_AI_GEMINI_MAX_TOKENS` | *(derived: 25% of context window)* | Gemini max output tokens |
| `LEAN_AI_GEMINI_EXPERT_MODEL` | *(falls back to GEMINI_MODEL)* | Gemini model for expert phases (e.g. `gemini-2.5-pro`) |
| `LEAN_AI_GEMINI_REQUEST_MODEL` | *(falls back to GEMINI_MODEL)* | Gemini model for /request mode |
| `LEAN_AI_SERVE_URL` | `http://localhost:8420` | Lean AI Serve server URL |
| `LEAN_AI_SERVE_API_KEY` | *(empty)* | Lean AI Serve API key (required when provider=serve) |
| `LEAN_AI_SERVE_MODEL` | *(empty)* | Lean AI Serve model name (must match vLLM loaded model) |
| `LEAN_AI_SERVE_TEMPERATURE` | `0.7` | Lean AI Serve sampling temperature |
| `LEAN_AI_SERVE_CONTEXT_WINDOW` | `131072` | Lean AI Serve context window (accepts shorthand) |
| `LEAN_AI_SERVE_MAX_TOKENS` | *(derived: 25% of context window)* | Lean AI Serve max output tokens |
| `LEAN_AI_SERVE_EXPERT_MODEL` | *(falls back to SERVE_MODEL)* | Lean AI Serve model for expert phases |
| `LEAN_AI_SERVE_REQUEST_MODEL` | *(falls back to SERVE_MODEL)* | Lean AI Serve model for /request mode |
| `LEAN_AI_INLINE_MODEL` | *(empty)* | Separate model for inline predictions (always Ollama) |
| `LEAN_AI_INLINE_OLLAMA_URL` | *(falls back to OLLAMA_URL)* | Ollama instance for inline model |
| `LEAN_AI_EMBEDDING_MODEL` | `qwen3-embedding:0.6b` | Embedding model for semantic search (always Ollama) |
| `LEAN_AI_ENABLE_EMBEDDINGS` | `true` | Enable embedding generation + RRF hybrid search |
| `LEAN_AI_EMBEDDING_CONTEXT_WINDOW` | `8192` | Context window used to size embedding batches (accepts shorthand). Defaults to 8k so `/init` never blocks on Ollama's `show` API. Raise for embedding models with larger windows (e.g. qwen3-embedding → `32`). Set `0` to re-enable auto-detect via `show` (a 5s timeout caps any hang) |
| `LEAN_AI_VISION_MODEL` | *(empty)* | Vision model for describing images (e.g. `qwen3-vl:8b`). Empty = vision disabled. Always Ollama |
| `LEAN_AI_VISION_OLLAMA_URL` | *(falls back to OLLAMA_URL)* | Ollama instance for vision model |
| `LEAN_AI_VISION_MAX_TOKENS` | `1024` | Max tokens for image description |
| `LEAN_AI_VISION_TIMEOUT` | `120.0` | Timeout per image description (seconds) |
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
| `LEAN_AI_ENABLE_UI_VERIFICATION` | `false` | Enable `verify_web_ui` + `verify_desktop_ui` tools (vision-backed screenshot analysis). Requires `[ui-verification]` extras + a configured vision model. Install Chromium via `Lean AI: Install UI Verification` command |
| `LEAN_AI_UI_VERIFICATION_TIMEOUT` | `180.0` | Outer timeout wrapping the whole `verify_*` tool call (seconds) — covers capture + 3 vision passes + color sampling + focused answer |
| `LEAN_AI_UI_VERIFICATION_VISION_TIMEOUT` | `180.0` | Per-pass vision timeout (overrides `LEAN_AI_VISION_TIMEOUT` for structured extraction passes on small local models) |
| `LEAN_AI_UI_VERIFICATION_WAIT_SECONDS` | `3.0` | Post-render settling time before capture fires (covers transitions, late-mounted content, font loading) |
| `LEAN_AI_UI_VERIFICATION_VIEWPORT` | `1280x800` | Default browser viewport for `verify_web_ui` as `WxH`. Examples: `1280x800`, `375x812` (mobile) |
| `LEAN_AI_UI_VERIFICATION_MAX_COLOR_SAMPLES` | `5` | Dominant colors extracted by Pillow/k-means and returned alongside inventory + text |
| `LEAN_AI_UI_VERIFICATION_CAPTURE_BACKEND_OVERRIDE` | *(empty — auto-detect)* | Force a specific desktop capture backend: `mss-win32`, `mac-screencapture`, `mss-x11`, or `grim` |
| `LEAN_AI_INDEX_DIR` | `.lean_ai_index` | Whoosh index directory name |
| `LEAN_AI_SEARCH_PROVIDER` | `duckduckgo` | Search provider (`duckduckgo`, `searxng`, `google`, or `bing`). Google auto-falls back to Bing |
| `LEAN_AI_SEARCH_DELAY` | `2.0` | Min seconds between searches (all providers, with random jitter) |
| `LEAN_AI_REFERENCE_DIR` | `.lean_ai/reference` | Reference library documents directory |
| `LEAN_AI_REFERENCE_CHUNK_CHARS` | `1800` | Target characters per prose chunk (~4 chars/token). Overlap is derived as `reference_chunk_chars // 6`. Changing this triggers a full reference library index rebuild on the next `/index-reference` run |
| `LEAN_AI_REFERENCE_NEIGHBOR_WINDOW` | `2` | `search_reference` expands each hit with ±N adjacent chunks from the same doc and merges overlapping ranges into a single passage. `0` disables expansion |
| `LEAN_AI_REFERENCE_SEARCH_DEFAULT_LIMIT` | `5` | Default `limit` for `search_reference` when the LLM doesn't pass one |
| `LEAN_AI_REFINER_REFERENCE_CHUNKS` | `5` | Max reference library chunks injected into refiner context (cloud providers only) |
| `LEAN_AI_ENABLE_REQUIRED_CITATIONS` | `true` | Mandate online documentation verification before using external frameworks/libraries/APIs |
| `LEAN_AI_NUM_PARALLEL` | `1` | Max concurrent LLM requests — set to match `OLLAMA_NUM_PARALLEL`. Controls parallelism for `/init` expansion batches and concurrent LLM calls. `1` = fully sequential |
| `LEAN_AI_IMPLEMENTATION_MAX_TURNS` | `0` | Max tool-calling turns per session (`0` = unlimited) |
| `LEAN_AI_PLAN_PHASE1_MAX_TURNS` | `5` | Max tool-calling turns for Phase 1 (scope analysis). The phase uses `chat_with_tools` with a restricted read-only tool set (`grep_files`, `read_file`, `list_directory`, `query_project_context`, `search_reference`, `task_complete`) and `text_only_exit_count=1` so crystal-clear tasks can exit without any tool calls. `0` disables tool use and falls back to a tool-less single-turn scope call. |
| `LEAN_AI_IMPLEMENTATION_MAX_TOKENS` | *(derived: 25% of context window)* | Max tokens per LLM turn |
| `LEAN_AI_REFRESH_THRESHOLD` | `0.7` | Refresh context at this % of context window |
| `LEAN_AI_ENABLE_FIX_INVESTIGATION` | `true` | Enforce read-only investigation phase in /fix mode before editing |
| `LEAN_AI_ENABLE_CLAIM_VERIFICATION` | `true` | Nudge LLM to verify external claims (doesn't exist, deprecated, future) via web search |
| `LEAN_AI_ENABLE_TDD` | `false` | TDD mode: expert writes tests first, primary implements. Requires expert model |
| `LEAN_AI_TDD_MAX_DISPUTES_PER_STEP` | `3` | Max test disputes per implementation step in TDD mode |
| `LEAN_AI_ENABLE_STRICT_TEST_CONTRACT` | `true` | Phase 5 strict testing policy: programmatic-only, E2E hooks required, regression awareness, core-functionality → regression. Feature flag for rollback — `false` restores previous-turn prompt shape without redeploying |
| `LEAN_AI_ENABLE_PHASE5_INVESTIGATION` | `false` | Opt-in Phase 5 tool-backed exploration turn (3-turn budget, read-only tools) when Phase 2's testing_inventory is thin. Adds cost/latency |
| `LEAN_AI_REGRESSION_FILE_PATTERN` | *(regex — path components `regression_*` or `/regression(s)/` dirs)* | Path regex identifying regression test files. Files matching this pattern become IMMUTABLE once the plan that created them completes |
| `LEAN_AI_ENABLE_CORE_FUNCTIONALITY_TAGGING` | `true` | Layer 9 — Phase 3 tags load-bearing entities; Phase 5 must emit regression tests for each tagged entity |
| `LEAN_AI_CORE_FUNCTIONALITY_MIN_CONFIDENCE` | `medium` | Minimum confidence (`low`/`medium`/`high`) to enforce mandatory regression coverage. Low-confidence tags are advisory unless user promotes via approval UI |
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
| `LEAN_AI_ENABLE_INTEGRATIONS` | `false` | Enable external service integrations (Jira, ServiceNow, etc.) |
| `LEAN_AI_INTEGRATION_AUTO_PUSH` | `true` | Auto-push session summaries to linked tasks on completion |
| `LEAN_AI_JIRA_URL` | *(empty)* | Jira Cloud instance URL (e.g. `https://yourcompany.atlassian.net`) |
| `LEAN_AI_JIRA_EMAIL` | *(empty)* | Jira account email for API authentication |
| `LEAN_AI_JIRA_API_TOKEN` | *(empty)* | Jira API token (stored in OS keychain via extension) |
| `LEAN_AI_SERVICENOW_URL` | *(empty)* | ServiceNow instance URL (e.g. `https://yourinstance.service-now.com`) |
| `LEAN_AI_SERVICENOW_USERNAME` | *(empty)* | ServiceNow username for API authentication |
| `LEAN_AI_SERVICENOW_PASSWORD` | *(empty)* | ServiceNow password (stored in OS keychain via extension) |
| `LEAN_AI_SERVICENOW_TABLE` | `incident` | ServiceNow table to query |
| `LEAN_AI_WIKI_URL` | *(empty)* | MediaWiki instance URL (e.g. `https://wiki.company.com`). Empty = wiki tools disabled |
| `LEAN_AI_WIKI_API_PATH` | `/w/api.php` | MediaWiki API endpoint path |
| `LEAN_AI_WIKI_USERNAME` | *(empty)* | MediaWiki username for authenticated wikis (bot account) |
| `LEAN_AI_WIKI_PASSWORD` | *(empty)* | MediaWiki password (stored in OS keychain via extension) |
| `LEAN_AI_ENABLE_SESSION_MEMORY` | `true` | Master switch for cross-session memory (extraction + retrieval). See `docs/curated-memory.md` |
| `LEAN_AI_MEMORY_RETRIEVAL_STATUSES` | `user_confirmed,high_confidence_auto` | Comma-separated curation statuses allowed in retrieval |
| `LEAN_AI_MEMORY_AUTOPROMOTE_THRESHOLD` | `3` | seen_count required to auto-promote `auto` → `high_confidence_auto` |
| `LEAN_AI_MEMORY_CONFIDENCE_TTL_DAYS` | `90` | Default TTL applied by `set_expiry_from_ttl` |
| `LEAN_AI_ENABLE_PHASE3_MEMORY` | `true` | Inject `gotcha`/`convention`/`rejection` memories into Phase 3 design |
| `LEAN_AI_ENABLE_FIX_LOOP_MEMORY` | `true` | Inject `fix_pattern`/`gotcha` memories into the validation fix-loop prompt |
| `LEAN_AI_PHASE3_MEMORY_BUDGET_PERCENT` | `0.02` | Context-window fraction for Phase 3 memory injection |
| `LEAN_AI_FIX_LOOP_MEMORY_BUDGET_PERCENT` | `0.02` | Context-window fraction for fix-loop memory injection |
| `LEAN_AI_ENABLE_TRAINING_CAPTURE` | `true` | Write workflow traces to `.lean_ai/training.db`. See `docs/training.md` |
| `LEAN_AI_TRAINING_DB_PATH` | `.lean_ai/training.db` | Training archive path (relative to workspace root or absolute) |
| `LEAN_AI_TRAINING_RETENTION_DAYS` | `365` | Prune archive rows older than this. `0` disables pruning |
| `LEAN_AI_CAPTURE_THINKING` | `true` | Preserve `<think>` blocks in archived traces (needed for reasoning-model LoRA) |
| `LEAN_AI_SCRUBBING_STRICT` | `true` | Fail-closed on scrubber exception: drop the trace rather than write unscrubbed data |
| `LEAN_AI_EXPORT_API_KEY` | *(empty)* | Bearer token for `/api/export/*`. Empty = endpoints return 503 |
| `LEAN_AI_EXPORT_WORKSPACE_SALT` | *(empty)* | Optional salt mixed into `workspace_id` hash |
| `LEAN_AI_MEMORY_EXPORT_DROP_THRESHOLD` | `0.40` | Drop exported memories with anonymization ratio above this |
| `LEAN_AI_PORT` | `8422` | Server port |

**Post-validation auto-detection:** When `LEAN_AI_POST_*_COMMAND` variables are empty, the system falls back to commands auto-detected during `/init-workspace` (stored in `.lean_ai/commands.json`). Manual env vars always take priority. In fix mode, the LLM is instructed to write tests alongside code changes when a test command is available. In plan mode, test creation is handled by Phase 5 (verification step generation) which appends test file steps and a final `run_tests` step after all implementation steps. In TDD mode, Phase 5 produces test steps separately into `tdd_test_steps` (without `run_tests`) — these are executed first by the expert model, then the primary implements code with test files protected. **Validation fix loop:** when `_run_post_validation` detects failures, `_run_validation_fix_loop` retries up to `LEAN_AI_POST_VALIDATION_MAX_RETRIES` times. Each attempt uses a **hardcoded 30-turn budget** (independent of `LEAN_AI_IMPLEMENTATION_MAX_TURNS`), is **file-scoped** to the plan's `affected_files` list (the tool executor blocks edits to files outside the whitelist), and instructs the LLM to: (1) re-run the failing command to confirm the error, (2) read relevant files to find the root cause, (3) record diagnosis in scratchpad, (4) make the minimal fix, (5) re-run to verify. On the **final retry**, the expert model is used if configured. In TDD mode, the fix loop also enforces the test-file guard and provides the `request_test_change` dispute tool so the primary model can escalate flawed tests to the expert rather than editing them directly.

## Current Model Layout (April 2026)

| Role | Model |
|---|---|
| Primary | `qwen3-coder:30b-a3b-q8_0` |
| Expert | `qwen3-coder-next:q8_0` |
| Request | `gpt-oss:20b` |
| Worker | `qwen2.5-coder:7b-instruct-q8_0` |
| Inline | `qwen2.5-coder:7b-instruct-q8_0` |
| Embedding | `qwen3-embedding:8b` |
| Vision | `qwen3.5:4b-q8_0` |

## WebSocket Protocol

Client → server: `user_message` (start workflow or mid-workflow interrupt), `cancel` (stop running workflow), `approve` (approve plan), `approve_tool` / `deny_tool` (shell command gate), `ping`, `resume`. Client message TypedDicts `confirm_memory`/`reject_memory`/`save_memory_manual` are defined in `ws_messages.py` for future use but not currently routed by the dispatcher — the extension uses REST (`/api/memories/*`) for memory curation actions.

Server → client: `token`, `stage_change`, `approval_required`, `tool_progress`, `tool_approval_required`, `diff`, `test_result`, `error`, `complete`, `cancelled`, `index_status`, `stage_status`, `clarification_needed`, `plan_rejected`, `pong`, `branch_created`, `checkpoint`, `merge_complete`, `context_refreshed`, `assistant_content`, `thinking_content`, `metrics_update`, `memory_suggested`.

`memory_suggested` fires from the memory extractor's `on_memory_created` callback when a new `auto` memory is written. Payload: `{memory_id, category, content, source_phase, tags}`. The extension renders it as an inline confirm/dismiss chip in the chat stream; clicks route through REST to `POST /api/memories/{id}/confirm` or `/reject`.

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
- `POST /index-reference` — index reference library docs
- `POST /chat` — chat endpoint. 20-turn tool budget (`_CHAT_MAX_TURNS`) with an always-explore default: every substantive reply MUST begin with at least one grounding tool call (read_file, grep_files, list_directory, directory_tree, search_internet, fetch_url, search_reference, list_reference_documents, query_project_context, search_wiki, fetch_wiki_page, plus chat-specific save_note / list_project_todos / list_recent_sessions / get_session_summary / search_workspace_memory). Pure social chatter (hi, thanks, ok) is the only exception. When the user asks for a prompt for the coding agent, the model runs a **strict two-round protocol**: Round 1 explores then ends with exactly 3-5 numbered clarifying questions; Round 2 (triggered when the user answers) does targeted verification then emits a `## Suggested Agent Prompt` fenced block with a `### References` section INSIDE the fence (code file:line, reference library docs, web URLs, wiki pages) so the planner receives the citations as part of the prompt payload. The extension's "Send to Agent ▶" button parses that fenced block and dispatches it to the planning pipeline. Uses the request model when configured; falls back to the primary model. The 20-turn budget surfaces to the LLM via a `{CHAT_MAX_TURNS}` template variable in the `chat.system` prompt kept in sync with `_CHAT_MAX_TURNS`.
- `POST /predict` — inline predictions
- `POST /scaffold/list` — list scaffold recipes
- `POST /scaffold` — create project from scaffold
- `GET /health` — health check. Returns `{"status": "ok", "vision_available": bool, "stt_available": bool, "tts_available": bool, "wake_word_available": bool, "busy": list[str]}`. The `busy` field lists active long-running tasks from the `runtime_state` registry (e.g. `"ollama.warmup"` during cold embedding-model load, `"embeddings.code"` during code embedding generation, `"embeddings.reference"` during reference library embedding generation). The extension's health monitor reads this to distinguish "slow because busy" from "crashed", and never auto-restarts the backend on slow/timeout probes — only on `ECONNREFUSED`/`ECONNRESET`. After 3 continuous minutes of unresponsive probes it surfaces a one-time notification with a manual Restart button.
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
- `GET /integrations` — list registered integrations and status
- `GET /integrations/{name}/health` — integration health check
- `GET /integrations/{name}/tasks` — list external tasks (pull)
- `GET /integrations/{name}/tasks/{external_id}` — get single external task
- `GET /integrations/{name}/search?q=...` — search external tasks
- `POST /integrations/{name}/push` — push session summary to external task
- `POST /integrations/{name}/link` — link external task to session/workspace
- `POST /integrations/{name}/unlink` — unlink external task
- `GET /integrations/linked/all` — list all linked tasks
- `POST /integrations/{name}/webhook` — receive webhook events
- `GET /memories?repo_root=...&category=...&curation_status=...&limit=...&include_expired=...` — list curated memories
- `GET /memories/{id}?repo_root=...` — fetch single memory
- `POST /memories` — user-authored memory (auto-set `curation_status=user_confirmed`)
- `POST /memories/{id}/confirm` — promote `auto` → `user_confirmed` (confidence=0.9)
- `POST /memories/{id}/reject` — mark `user_rejected` (confidence=0.0); blocks future re-introduction via `supersede_user_rejected`
- `DELETE /memories/{id}?repo_root=...` — permanent delete + Whoosh index removal
- `GET /export/workspace-id?repo_root=...` — returns 16-char sha256-derived workspace hash. All `/export/*` endpoints require `Authorization: Bearer $LEAN_AI_EXPORT_API_KEY` and return 503 when the key is unset
- `GET /export/manifest?repo_root=...` — training-archive counts by model/phase/outcome + memory counts by status; cached 60s
- `GET /export/traces?repo_root=...&format=raw|sft|dpo|kto&model=...&phase=...&outcome=...&since=...&cursor=...&limit=1000` — streaming JSONL
- `GET /export/memories?repo_root=...&curation_status=...&category=...&limit=500` — streaming JSONL of anonymized memories (second-pass symbol-table redaction + drop-threshold via `LEAN_AI_MEMORY_EXPORT_DROP_THRESHOLD`)
- `GET /export/events?repo_root=...&event_type=...&since=...&cursor=...&limit=1000` — streaming JSONL of anonymized workflow_events

## LLM Prompt Authoring Standard

**Never assign a persona to the LLM in system prompts.** Use capability-first framing:
```
# Bad
"You are a senior software architect..."

# Good
"Use your knowledge of software architecture to..."
```

## Commit After Every Change

Always commit after completing a change without waiting to be asked. Each logical change gets its own commit. When an approved plan's changes are complete and tests pass, commit immediately — do not wait for the user to ask.

## Test Modification Policy

Do not modify existing tests. Tests are only changed in two cases:

1. **Adding new tests** for new features.
2. **A feature change genuinely requires a test update** — in this case, stop and explain: "I think I need to modify this test because [reason tied to the feature change]." Wait for approval before touching the test.

If your code does not pass an existing test, fix your code — not the test.

## Regression Test Protection

**Regression tests are IMMUTABLE.** A regression test guards a previously-fixed bug or load-bearing core behavior from silently returning; editing it defeats its purpose.

- **Naming convention** (configurable via `LEAN_AI_REGRESSION_FILE_PATTERN`): path components beginning with `regression_` or files inside a `/regression/` or `/regressions/` directory. Examples: `regression_login_test.py`, `tests/regression/auth_test.py`, `spec/regression-bar.spec.ts`. The module `lean_ai.tools.regression_guard` exposes `is_regression_test_path()` for runtime checks.
- **Write protection**: the tool executor rejects `edit_file` on any regression-convention path with `REGRESSION_GUARD_ERROR`. In-plan refinement is allowed for files created during the CURRENT session (tracked in `session_created_regression_files`); once the plan completes, the file is finalized and unconditionally protected.
- **Creation**: `create_file` on a regression-convention path is always allowed — Phase 5 uses this for core-functionality regression tests, and `/fix` mode uses it when the task smells like a bug fix.
- **Deletion**: goes through the existing destructive-command approval gate (`rm`, `git rm`). No code-level special-case.
- **Fix-loop awareness**: `_run_validation_fix_loop` scans failing output for regression paths; when any are present, it prepends a `REGRESSION TEST FAILURES DETECTED` banner to the fix prompt. The training archive records a `regression_failure` flag on the `validation_attempt` row so exports can weight these traces.
- **Core-functionality tagging (Layer 9)**: Phase 3 auto-tags load-bearing entities (public APIs, Phase 1 deliverables, critical-risk-adjacent code, downstream-consumer dependencies) into `DesignAndRisks.core_functionality`. Phase 4 propagates the list onto `ExecutionPlan.core_functionality`. Phase 5 MUST emit a regression-convention test step for every tag whose `confidence` is ≥ `settings.core_functionality_min_confidence` (default `medium`).
- **Rename edge case**: renaming a core entity requires creating the new regression test at the new path and deleting the old one via `run_command rm ...` (destructive-command approval). No automation; document the rename in the plan approval.

When writing a plan or a fix that touches behavior: use your knowledge of software engineering to decide whether the entity is load-bearing. If in doubt, add a regression test — they are cheap insurance.

## No Stubs Rule

Never create stubs, placeholder implementations, or skeleton code that is not fully functional. If a feature cannot be completed, document what is missing in `incomplete.md` and move on.

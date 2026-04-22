# Example Flow: From User Request to Completed Plan

This document walks a developer through a complete Lean AI session — from the first message typed into the chat, through each planning phase, into execution, and finally to post-execution validation. It spells out what the harness around the LLM is actually doing at every step, what guardrails keep the LLM on task, and where field-observed issues have prompted specific strengthening (especially in Phase 5 testing).

The running example is a realistic mid-sized change: *"Add a password-reset flow to an existing Python FastAPI app."* Where the details of a phase change with language or project maturity, the text calls that out.

---

## 0. Before the First Message

Lean AI assumes the workspace has been initialized — either manually via `POST /api/init-workspace` or automatically the first time the extension connects. Initialization does three things that matter for every later phase:

- **Index the repo.** Tree-sitter pulls an AST-aware chunked representation of every source file; Whoosh builds a BM25F index; optional embeddings give a reciprocal-rank-fusion hybrid. Result: `grep_files`, `read_file`, `query_project_context`, and reference lookups all return fast, deterministic hits.
- **Generate `project_context.md`.** A single LLM pass (or multi-round expansion for large repos) writes `.lean_ai/project_context.md` — a structured description of the codebase's architecture, conventions, and key files. Every later phase loads this into its system prompt via `load_planning_context()` / `load_execution_context()`.
- **Auto-detect commands.** `context/command_detection.py` inspects dependency files (`pyproject.toml`, `package.json`, `Cargo.toml`, `composer.json`, `go.mod`, `Gemfile`, `build.gradle`, `.csproj`, …) and writes `.lean_ai/commands.json` with the project's `format`, `lint_fix`, `lint`, and `test` commands. **This per-project file is authoritative** — the global `LEAN_AI_POST_*_COMMAND` env vars only fill in fields commands.json left empty, so switching between a Python repo and a PHP or Rust repo does not require a settings edit.

If initialization has not happened, chat still works but every planning phase will warn the user that architecture awareness is thin.

---

## 1. First Interaction — Chat Mode

The user types something vague or exploratory: *"I want to add password-reset."* This arrives at `POST /api/chat`, not at the planning pipeline. Chat mode runs on the **request model** (chatty, higher temperature) with a dedicated tool set: `save_note`, `list_project_todos`, `list_recent_sessions`, `get_session_summary`, `search_workspace_memory`, plus the planning tools (`grep_files`, `read_file`, `list_directory`, `directory_tree`, `search_internet`, `fetch_url`, `search_reference`, `query_project_context`, …).

Two hard rules keep the chat grounded:

1. **Always explore first.** Every substantive reply must begin with at least one grounding tool call. Pure social chatter (hi / thanks / ok) is the only exception. This is enforced by the `chat.system` prompt and by a 20-turn tool budget (`_CHAT_MAX_TURNS`).
2. **Strict two-round protocol when the user asks for a prompt for the coding agent.**
   - *Round 1*: the LLM explores (reads files, greps for references), then ends with exactly 3–5 numbered clarifying questions.
   - *Round 2*: triggered when the user answers, the LLM does targeted verification and emits a single fenced block titled `## Suggested Agent Prompt`. Inside that fence, a `### References` section carries file:line citations, reference-library docs, fetched URLs, and wiki pages.

The extension's "Send to Agent ▶" button parses the fenced block and dispatches it to the planner. The payload is already citation-rich, so Phase 1 does not have to re-derive context.

**Guardrail**: if the LLM tries to produce a plan, write code, or skip to implementation inside the chat, the `chat.system` prompt and the two-round protocol cap it at exploration + clarifying questions.

---

## 2. Request → Planning Pipeline

When the user clicks "Send to Agent", `routers/workflow.py` creates a WebSocket-backed session and calls `run_workflow()` in `workflow/pipeline.py`. The pipeline picks one of three modes based on the user's command prefix or task text:

| Mode | Entry | When |
|---|---|---|
| `plan` (default) | `run_workflow` | Any substantive code change |
| `fix` | `_run_fix` in `workflow/fix_mode.py` | Task text is a bug report / exception / failing test |
| `request` | `_run_fix` (neutral-prompt branch) | Open-ended questions, writing guides, non-code work |

Plan mode runs Phases 1 → 1a → 2 → 3 → 4 → (approval) → execution → 5 was already folded in → post-validation. Fix and request modes skip planning and go straight to execution with a curated system prompt.

The rest of this document follows the default `plan` flow.

---

## 3. Phase 1 — Clarification / Verification

**Runs on**: primary model. **Tool budget**: `LEAN_AI_PLAN_PHASE1_MAX_TURNS` (default 5). **Prompts**: `planning.scope_system` + `planning.scope_user`.

The primary model gets a small read-only tool set (`grep_files`, `read_file`, `list_directory`, `query_project_context`, `search_reference`, `search_wiki`, `fetch_wiki_page`, `task_complete`) plus the opt-in `request_clarification` tool. Its job is to verify the task is specific enough to turn into a complete scope document in Phase 1a. It does **not** write the scope document itself.

Four guardrails keep Phase 1 tight:

- **No auto-pre-step asking.** Clarification is strictly opt-in via the `request_clarification` tool. The LLM decides when to ask. When it calls the tool, a `clarification_needed` WebSocket message blocks the pipeline until the user answers; the answer arrives through the dispatcher's approval queue.
- **`text_only_exit_count=1`.** If the LLM responds with text (no tool calls), exploration exits. A genuinely clear task ends with zero tool calls — the summary paragraph alone.
- **Turn cap.** 5 turns by default stops runaway exploration.
- **Summary-before-exit contract.** The system prompt explicitly requires a final summary paragraph capturing the task, verified facts, and answers to any clarifying questions.

For *"add password-reset flow"*, a competent primary model typically asks 2–3 questions (*"Which email provider? Rate-limit policy? Token TTL?"*), reads a few files (`auth_router.py`, `users.py`, `settings.py`), and exits with a summary that Phase 1a can deterministically translate into a scope document.

---

## 4. Phase 1a — Scope Synthesis

**Runs on**: primary model. **Tool budget**: zero (one `chat_structured` call). **Prompt**: `planning.scope_synthesis_system`.

Phase 1a is non-interactive — it never asks the user anything. It mechanically maps the task text plus Phase 1's summary into a validated `ScopeDocument` Pydantic model with eight required fields:

1. `problem` — 3–6 sentences on what and why.
2. `deliverables` — observable outcomes ("User can reset password via email"), not file changes.
3. `in_scope` — greppable entities (file paths, classes, routes, tables, env vars).
4. `out_of_scope` — explicit exclusions.
5. `downstream_consumers` — categories of files that reference modified entities (controllers, fixtures, configs, migrations, tests).
6. `assumptions` — falsifiable statements paired with `verify_hint` so Phase 2 can check them.
7. `success_criteria` — 3–6 falsifiable conditions Phase 5 will target.
8. `risks` — scope-level misunderstandings (distinct from Phase 3's implementation risks).

**Guardrail**: if validation fails, the pipeline retries once with a corrective payload. If it fails again, `_fallback_scope_document(task)` wraps the raw task text verbatim into `problem`, records an explicit `assumption` flagging the fallback, and moves on — Phase 2 gets an 8-section scope no matter what. Free-form prose never reaches Phase 2.

`format_scope_document()` renders the structured object back to the historical 8-section markdown so Phase 2 / 3 / 4's `{scope}` template variable stays stable.

---

## 5. Phase 2 — Codebase Exploration

**Runs on**: primary model. **Tool budget**: `LEAN_AI_IMPLEMENTATION_MAX_TURNS` (effectively unlimited for exploration). **Prompts**: `planning.exploration_system` + `planning.exploration_user` + `planning.exploration_synthesis_system` (for the post-loop structured pass). The worker model automatically compresses large tool outputs before they re-enter the primary's context (see `workflow/tool_executor.py`), so file reads during Phase 2 don't balloon the primary's window.

This is where most of the heavy lifting lives, because every later phase depends on the `FileSummary` that comes out the back. Phase 2 runs `chat_with_tools` with the planning tool set plus `record_file_observation` — a dedicated tool that upserts a structured observation into `.lean_ai/observations/{session_id}.json` keyed by `file_path`.

The user prompt opens with an **ASSUMPTIONS CHECKLIST**: every item from the scope's `assumptions` section must be explicitly verified before general exploration begins. An item on that checklist — added as part of the Phase 5 strengthening pass — asks the model to inventory the project's test infrastructure: framework, directory, assertion-style excerpt, existing regression files, and per-affected-file coverage. The synthesis step translates those findings into a `TestingInventory` object that rides along on the `FileSummary`.

**Retention is deterministic, not voluntary.** The LLM calls `record_file_observation(file_path, role, reason, relevant_sections, key_snippets)` for every relevant file. Prose is for reasoning; the structured observation is what reaches downstream phases. Observations are upserted by file_path — re-observing the same file simply replaces the prior entry.

After the exploration loop exits (via `task_complete` or turn budget), a final `chat_structured` synthesis pass coerces observations + scratchpad + journal + prose into a validated `FileSummary` with these fields:

- `files_to_modify`, `files_to_create`, `files_read_for_context` (per-observation buckets).
- `missing_infrastructure` (packages, frameworks, shared config flagged as absent).
- `verified_references` (external dependencies whose docs were read during this phase).
- `assumptions_resolved` (one entry per scope assumption: confirmed / falsified / unable_to_verify, with evidence).
- `testing_inventory` (the testing-infrastructure object described above).
- `notes` (cross-file invariants, TODOs for the executor).

Guardrails in Phase 2:

- **Scratchpad + journal.** `update_scratchpad` is overwrite-based volatile working memory. `add_journal_entry` is append-only durable — it survives context refresh and crashes. Both have token budgets (`SCRATCHPAD_CONTEXT_PERCENT = 0.05`, `JOURNAL_CONTEXT_PERCENT = 0.03`). Context refresh re-reads both from disk along with the files the plan touches, so the LLM does not forget what it learned.
- **Loop detection.** N identical tool calls in a row (default 3) injects a "use a different approach" nudge.
- **Truncation detection.** 5 consecutive max-tokens responses exits the loop; single truncations get "output ONLY the tool call" nudge.
- **Text-only nudge.** A text response without a tool call is followed by "call task_complete if done, otherwise call your next tool."
- **No fabrication.** The system prompt forbids simulating tool output, inventing file listings, or making up file contents.
- **Testing-environment awareness** (new). The LLM is expected to detect whether the project has a working test setup; if not, the `TESTING INVENTORY` in the synthesis output records that gap and Phase 5 will later plan the setup.

At the end, the pipeline writes debug artefacts (`phase_2_exploration_*.md`, `phase_2_file_summary.json`) to `.lean_ai/plan_debug/{session_id}/` when `LEAN_AI_DEBUG_PLANNING=true`.

---

## 6. Phase 3 — Design + Risk Synthesis

**Runs on**: expert model. **Tool budget**: small read-only (search_internet / fetch_url / reference library / wiki / task_complete). **Prompts**: `planning.design_system` + `planning.design_synthesis_system`.

Phase 3 has two passes:

**Pass 1 — exploration / verification.** The expert model receives `project_context.md` (loaded directly from disk), the full scope, and the rendered `FileSummary` (key_snippets included). Its job is to reason about design, verify any external dependencies not already covered by `VERIFIED REFERENCES`, and emit free-form prose. `text_only_exit_count=1` lets the expert single-shot when Phase 2 already covered everything.

The `planning.design_system` prompt calls out `FileSummary.key_snippets` as authoritative transcriptions — the expert trusts them rather than re-deriving. This single rule removes a whole class of hallucination: Phase 3 cannot "guess" a signature it already has the line-exact excerpt of.

**Pass 2 — structured synthesis.** A `chat_structured` call translates Pass 1's prose into a validated `DesignAndRisks` object:

- `naming_conventions` (typed list by category, with source_file examples).
- `change_designs` (prose per non-obvious file).
- `missing_files` (runtime-crash-if-absent, with `blocking: bool`).
- `dependency_order` (inter-file ordering constraints).
- `critical_risks` (scope-level issues with severity + mitigation).
- `citations` (external dependencies verified this phase).
- `core_functionality` (Layer 9 — entities flagged as load-bearing; see "Core Functionality" below).
- `notes` (architectural catch-all).

Guardrails in Phase 3:

- **Anti-fabrication.** The system prompt forbids simulating commands and inventing files. Every cited signature must come from `FileSummary.key_snippets`.
- **Memory injection.** When `enable_phase3_memory=True`, `retrieve_design_memories` fetches curated `gotcha` / `convention` / `rejection` memories and injects them into the design pass within a 2% context budget. Raw `auto` memories are filtered out by curation policy — only `user_confirmed` and `high_confidence_auto` appear.
- **Testability requirement (Phase 4, referenced here).** The design pass is aware that whatever it decides must include seams Phase 5 can drive (DI params, CLI flags, env-flag gates, pure-function cores).
- **Core-functionality detection**. A dedicated prompt section names four automatic signals — `phase1_deliverable`, `critical_risk_adjacent`, `public_api`, `downstream_consumer` — and explicitly excludes helpers / utilities / refactor-only changes. When in doubt, the model is told to tag with `confidence="low"` and let the user prune during approval.

---

## 7. Phase 4 — Plan Assembly

**Runs on**: expert model. **Tool budget**: zero (one `chat_structured` call). **Prompt**: `planning.assembly_system` + `planning.assembly_user`.

Phase 4 produces the `ExecutionPlan` — the structured plan the user approves. Its fields include:

- `scope`, `user_summary`.
- `steps: list[PlanStep]` — each step maps to exactly one tool call. Valid tools: `create_file`, `edit_file`, `read_file`, `run_command`, `run_tests`, `run_lint`, `format_code`.
- `naming_conventions: list[NamingConvention]` (propagated from Phase 3).
- `name_registry: list[NameRegistryEntry]` — one canonical-name row per NEW entity (entity / model_class / module_namespace / import_stmt / db_table / file_path / route_endpoint / registered_in / test_file). Injected into every step's system prompt during execution so naming does not drift.
- `affected_files: list[str]`.
- `test_strategy`.
- `plan_validation_warnings: list[str]` — non-blocking warnings from the post-generation validators.
- `core_functionality: list[CoreFunctionalityTag]` — copied from `DesignAndRisks.core_functionality` so the approval payload carries the list.
- `tdd_test_steps: list[PlanStep]` — populated only in TDD mode (see Phase 5 below).

Four set-membership validators run after the structured output lands:

1. `_check_hallucinated_paths` — every `file_path` in `plan.steps` must appear in `FileSummary.files_to_create` / `files_to_modify` / `files_read_for_context` (or be a test path Phase 5 will create).
2. `_uncovered_missing_files` — every `DesignAndRisks.missing_files` entry must have a `create_file` or `edit_file` step. When a missing_file is marked `blocking=True` and no step covers it, Phase 4 auto-revises once via `_revise_plan`.
3. `_check_edit_create_consistency` — a file cannot be both `create_file` and `edit_file` in the same plan; edits against non-existent files are flagged.
4. `_check_test_path_conventions` — test steps should live under directories learned from `FileSummary.files_read_for_context` or contain a test token.

Warnings append to `plan.plan_validation_warnings` and ride through to the approval UI so the user sees them alongside the plan.

The `TESTABILITY REQUIREMENT` policy block (composed in when `enable_strict_test_contract=True`) tells Phase 4 to describe the seam Phase 5 will use whenever a step introduces code that touches external state. This makes "E2E testing requires hooks" (Phase 5's rule) achievable in practice rather than aspirational.

---

## 8. Approval

Before anything executes, the pipeline sends `approval_required` over WebSocket. The extension renders the plan, the warnings, the core-functionality tags (when present), and the TDD test preview (when enabled) as a single approval card. The user has three buttons:

- **Approve** — execution begins.
- **Reject with feedback** — the pipeline calls `_revise_plan` with the feedback as context; up to 5 revision rounds are allowed before the loop gives up.
- **Cancel** — the session closes cleanly; no files are touched.

During approval, the workflow is responsive to mid-flight `user_message` and `cancel` messages via `WSMessageDispatcher` — the dispatcher routes mid-approval messages to the approval queue, and post-approval messages to the interrupt queue that `chat_with_tools` consumes between turns.

Two things the user can change during approval (extension UX, optional stretch): prune the core-functionality tag list, or promote a low-confidence tag.

---

## 9. Phase 5 — Verification Step Generation

**Runs on**: expert model. **Tool budget**: zero (one `chat_structured` call — plus an opt-in 3-turn exploration turn if `enable_phase5_investigation=True`). **Prompts**: `planning.verification_system` + `planning.verification_user_normal` or `planning.verification_user_tdd`.

Phase 5 **always runs**, even when no `test_command` is configured. In the no-runner case, test files are still seeded on disk and the final `run_tests` step is omitted — the plan leaves the workspace better-tested than it arrived.

### The strict test contract

When `enable_strict_test_contract=True` (the default), the system prompt adds six sections that reshape what the LLM produces:

1. **Programmatic testing only.** Tests must run under the project's test command with no human in the loop. Tests requiring observation of a window / game / animation / audio are forbidden. If the behavior is only verifiable by human observation, the LLM extracts a pure unit first.
2. **E2E testing requires hooks.** If an E2E or integration test is needed, the source code must expose programmatic seams (CLI flags, seeds, debug endpoints, env toggles, injectable clock / RNG, test-mode config, test-harness entry function). If hooks don't exist, Phase 5 adds `edit_file` steps to install them BEFORE the `create_file` step that uses them — or falls back to unit tests over a pure extract.
3. **Game / UI / real-time code.** Prefer testing pure logic (state machines, scoring, collision math) over "running" the application. Drive events through the programmatic API, not the rendered output.
4. **Regression testing.** A regression test prevents a fixed bug or core behavior from returning — IMMUTABLE once the plan completes. File-naming convention: `regression_*` or `/regression(s)/` directory. Prefix the step's `reason` with `REGRESSION:`.
5. **Core-functionality → regression tests.** Every `CoreFunctionalityTag` in the user prompt MUST receive a regression test step, not a regular test step.
6. **When unable to write a real test.** Write a framework-native `skip` with a TODO message. Never a fake always-passing test. Never sleep-and-hope.

### Testing environment awareness

A second policy block (composed into both Phase 2's exploration prompt and Phase 5's system prompt) covers the setup case:

- Detect whether the project already has a working test setup.
- If not, web-search "best practices for setting up {language} testing in {year}" — the LLM's training data may be stale.
- Include setup steps (init manifest, install dev dependencies, create config file, initialize a venv) BEFORE test creation. In TDD mode on a greenfield project, setup comes before Phase A.
- Record the chosen command in `.lean_ai/commands.json` so future phases (and future plans) find it.
- Adapt down for primitive-tooling languages — a C project may use `assert.h` in a main function, not a framework.

### Per-phase-5 validators

After the LLM returns, three deterministic checks run and append non-blocking warnings:

- `_check_test_path_conventions` — test-file paths should match test-directory conventions observed in Phase 2.
- `_check_affected_files_covered` (Layer 2) — every executable `affected_file` should be referenced by at least one test step.
- `_check_core_functionality_covered` (Layer 9) — every core-functionality tag above the min-confidence threshold should have a regression-convention test step.

### Normal vs TDD mode

| Normal mode | TDD mode |
|---|---|
| Phase 5 steps appended to `plan.steps` | Phase 5 steps go into `plan.tdd_test_steps`; implementation steps are renumbered to run afterwards |
| Final step is `run_tests` (safety-net injects one if the LLM omits it) | No `run_tests` step (filter is defensive) |
| Executed sequentially after implementation | Phase A: expert writes tests → Phase B: primary reviews, can dispute via `request_test_change` → Phase C: primary implements with tests LOCKED |

In TDD Phase C, the tool executor blocks `edit_file` on any test file (via `is_test_file_path`). Disputes are available only in Phase B so behavior is predictable during implementation. The post-validation fix loop also enforces the test-file guard and provides `request_test_change` as a safety net.

### Regression guard

Layer 7 adds a workflow-wide guard. Files matching the regression pattern:

- Cannot be edited via `edit_file` once the plan that created them completes (the tool executor rejects such edits with `REGRESSION_GUARD_ERROR`).
- Can be created via `create_file` at any time (Phase 5 and `/fix` mode use this).
- Are freely readable and runnable (frameworks discover them via normal suffixes like `_test.py`, `.spec.ts`).
- Remain editable in-plan for the session that created them — a shared `session_created_regression_files` set tracks this.

If a regression test fails during post-validation, the fix-loop prompt gets a REGRESSION-AWARE banner explaining that the tool executor will reject test edits and the only acceptable fix is an implementation change.

---

## 10. Execution

The approved plan hits `execute_plan()` in `workflow/executor.py`. Steps are grouped by dependency into `step_groups`: independent groups run in parallel (gated by `LEAN_AI_NUM_PARALLEL`) and dependent steps run sequentially within a group. Each step invokes `chat_with_tools` on the **primary model** with a per-step system prompt built by `build_step_system_prompt(context, naming_conventions, name_registry)`.

The step prompt is narrow and tool-gated:

- The executor sees *only one step at a time* in a fresh conversation — it does not have the planner's design reasoning, the full plan, or sibling steps' outputs.
- `naming_conventions` and `name_registry` render as explicit tables so the executor cannot invent alternate names even if they seem more natural.
- When a step depends on output from a previous step (artefact), the artefact is inlined into the user prompt up to `_artifact_per_file_limit()` characters.

Guardrails during execution:

- **Tool-executor guards** (in `workflow/tool_executor.py`):
  - `allowed_files` whitelist during the validation fix loop.
  - TDD test-file lock in Phase C.
  - Regression guard (rejects edits to finalized regression paths).
  - Destructive-command approval (rm / git push / etc. require user confirmation).
  - Path-traversal protection (`_safe_resolve` rejects `../` escapes and symlinks outside the repo root).
  - Shell-command safety gate (subprocesses killed on timeout).
- **Lightweight control loop** in `chat_with_tools` (`llm/facade.py`):

| Mechanism | Trigger | Action |
|---|---|---|
| Text-only exit | 3 consecutive text-only responses | Exit loop |
| Truncation exit | 5 consecutive truncated responses | Exit loop |
| Text-only nudge | Text response without a tool call | Inject "call task_complete or your next tool" |
| Truncation nudge | Response cut by max_tokens | Inject "output ONLY the tool call" |
| Loop detection | N identical tool calls | Inject "use a different approach" |
| Context refresh | Token usage crosses 70% of context window | Drop old messages, re-read from disk |
| Task reminder | Every 10 turns | Re-inject the task description |
| Cancel / interrupt | User sends cancel or new message | Raise error or inject interrupt |
| Claim verification | Tests / lint fail ≥2 times AND LLM claims something doesn't exist / is deprecated | Inject "search for current docs" nudge |

After each turn, a single `_evaluate_turn()` decision picks one of continue / nudge / refresh / exit — no overlapping subsystems.

---

## 11. Post-Execution Validation

`_run_post_validation` in `workflow/validation.py` runs once the plan's last step completes. Commands come from the per-project commands.json (env vars are fallback), as described at the top.

1. **Auto-fix passes** (format, lint-fix) run sequentially — they modify files.
2. **Reporting passes** (lint, test) run in parallel via `asyncio.gather` — they are read-only.
3. On any failure, `_run_validation_fix_loop` retries up to `post_validation_max_retries` times (default 2). Each attempt:
   - Uses a hardcoded 30-turn budget (independent of the plan's step budgets).
   - Is file-scoped to `plan.affected_files` (the tool executor rejects edits outside).
   - Injects a structured verify-first workflow ("re-run failing command → diagnose → fix → re-run").
   - On the **final** retry, the expert model takes over if configured — this is the only place where the expert escalates during execution.
   - When a regression test is among the failing output, a REGRESSION-AWARE banner is prepended to the fix prompt so the LLM knows the tool executor will reject edits to those paths.
   - Every attempt writes a `validation_attempt` row to `.lean_ai/training.db` with a `regression_failure` flag, so exports can weight fixes that correctly addressed regressions.

If the fix loop succeeds, a `fix_pattern` memory extraction is queued. Raw extractions have `curation_status=auto` and are NOT used in future retrieval — the user (or `seen_count>=3` auto-promotion) must confirm them first. This is why stricter tests do not pollute the training archive.

---

## 12. Completion

Successful completion fires three fire-and-forget hooks:

- **Auto-push integration** (`workflow/hooks.py::on_execution_complete`): Jira / ServiceNow / etc. get a session summary via the integration registry (when `LEAN_AI_ENABLE_INTEGRATIONS=true`).
- **Session-memory extraction**: the worker model (or primary as fallback) writes `architecture` / `build` / `testing` / `pattern` / `gotcha` / `convention` / `discovery` memories into `session_memories`. Phase-specific extractions (plan_rejection, fix_success, tdd_dispute) also fire, each writing to `curation_status=auto`.
- **Training archive**: the turn-level, plan-decision-level, and validation-attempt-level traces are already committed by this point; retention pruning (once per hour per workspace) may fire opportunistically.

The user sees a final "Task complete" message, a list of modified files (clickable diff links in the extension), and — when available — a suggested-memory chip for each newly-extracted memory. Clicking confirms or rejects via `POST /api/memories/{id}/{confirm|reject}` and the chip turns into a small inline note.

---

## 13. Guardrails at a Glance

| Layer | Mechanism | Where it lives |
|---|---|---|
| Chat | 20-turn tool budget, two-round "Suggested Agent Prompt" protocol, always-explore rule | `chat.system` prompt + `_CHAT_MAX_TURNS` |
| Phase 1 | `request_clarification` opt-in tool, 5-turn cap, text-only exit, summary-before-exit | `planning.scope_system` / `planning.scope_user` |
| Phase 1a | Pydantic validation + one retry + deterministic fallback to `_fallback_scope_document` | `planning.scope_synthesis_system` |
| Phase 2 | ASSUMPTIONS checklist, `record_file_observation` (deterministic retention), scratchpad + journal, loop / truncation / text-only detectors, testing-environment awareness | `planning.exploration_*` |
| Phase 3 | `FileSummary.key_snippets` is authoritative, memory injection (curated only), anti-fabrication, core-functionality detection | `planning.design_*` |
| Phase 4 | Four set-membership validators, single auto-revision for blocking missing_files, `testability_requirement` policy block, warning surfacing to approval UI | `planning.assembly_*` + `_run_plan_validations` |
| Approval | Reject-with-feedback × 5 rounds, cancel any time, dispatcher-routed mid-flight messages | `_wait_for_approval` + `WSMessageDispatcher` |
| Phase 5 | Strict-test-contract policy (programmatic-only, E2E hooks, game/UI, regression, core-functionality, skip-with-TODO), three validators, `run_tests` safety-net injection, TDD two-phase separation | `planning.verification_*` + `_run_phase5_verification` |
| Execution | Step prompt isolation, naming/name-registry tables, tool-executor guards (whitelist / TDD / regression / destructive / path-traversal / shell safety), `chat_with_tools` control loop | `executor.py` + `tool_executor.py` + `llm/facade.py` |
| Post-execution | Deterministic format + lint-fix + lint + test passes, file-scoped fix loop (2 × 30 turns), expert escalation on final retry, REGRESSION-AWARE banner | `validation.py` |
| Cross-session | Curated memory retrieval (only `user_confirmed` / `high_confidence_auto`), training archive with scrubber, regression_failure flag on validation attempts | `memory/` + `training/` |

---

## 14. Common Issues the Harness Absorbs

These are field-observed problems and the specific guardrail that addresses each.

**The LLM tries to write tests that require a human in the loop.** Phase 5 strict-test-contract forbids observation-based tests and demands a pure extract or an E2E hook. The `_check_test_path_conventions` validator also catches tests placed outside the project's detected test directory. *Observed on game / UI projects where tests tried to `click()` a button and assert the animation frame.*

**The LLM invents a test command that doesn't match the project's language.** The per-project `.lean_ai/commands.json` is authoritative; the global `LEAN_AI_POST_*_COMMAND` env vars only fill in blanks. The testing-environment awareness policy tells the LLM to web-search when it has no evidence of a setup, and to add setup steps (install deps, init config, record to commands.json) BEFORE writing tests. *Observed when the user switched from a Python project to a Rust project with a stale `LEAN_AI_POST_TEST_COMMAND=pytest` env var — which now loses to the per-project command.*

**The LLM edits a regression test to make the build green.** The regression guard in `tool_executor.py` rejects edits to any path matching `regression_*` or `/regression(s)/` — unless the file was created during the current session. The fix-loop prompt receives a REGRESSION-AWARE banner when a regression path shows up in failing output. *Observed before Layer 7 — a regression test for a previously-fixed race condition was silently modified to "fix" the build, defeating the guard.*

**The LLM writes a feature and then writes tests that essentially echo the implementation.** The strict-test-contract anti-patterns section explicitly forbids tautological tests, tests that mock the unit under test, tests that only assert truthiness, and tests that depend on ordering. Phase 5's per-test `reason` field must cite a Phase 3 change_design / Phase 2 new file / critical_risk — so every test maps to a plan artefact. *Observed on refactor tasks where the "test" was `assert new_function() == new_function()`.*

**Phase 5 produces zero test steps on a small plan.** The `verification_user_normal` prompt demands a non-empty `steps` list. A safety-net in `_run_phase5_verification` injects a final `run_tests` step when the LLM omits one (only when a test_command exists, to avoid phantom failures). *Observed before the safety-net landed — Phase 5 emitted an empty array and the plan shipped with no verification.*

**Core functionality is added in one plan and accidentally removed in the next.** Layer 9 auto-tags load-bearing entities in Phase 3 via four signals (phase1_deliverable, critical_risk_adjacent, public_api, downstream_consumer). Phase 5 must emit a regression-convention test step for every tag whose confidence is ≥ `core_functionality_min_confidence`. Those regression tests are immutable going forward. *Observed when a refactor task removed an unused-looking helper that was actually the public entry point for a downstream consumer.*

**The LLM hallucinates a function signature.** Phase 3's `design_system` prompt names `FileSummary.key_snippets` as authoritative transcriptions from source. `_check_hallucinated_paths` in Phase 4 catches file-path inventions post-plan. `LEAN_AI_ENABLE_CLAIM_VERIFICATION` (default on) nudges the LLM to web-search when it claims something doesn't exist or is deprecated. *Observed on library upgrades where the LLM's training data was a release or two behind.*

**The fix loop modifies the wrong file.** The fix loop is file-scoped to `plan.affected_files` via the tool executor's `allowed_files` parameter. Attempts to edit files outside the whitelist return a clear error. *Observed on large plans where the fix loop ran 30 turns hunting through unrelated code.*

**The validator warnings are ignored.** `plan.plan_validation_warnings` flows through the `approval_required` WebSocket message into the approval UI, where warnings render as a distinct list alongside the plan. The user can see them before clicking approve. *A deliberate design choice: warnings never BLOCK a plan — they inform it. Blocking gates happen only at hard failures (blocking missing_file triggers auto-revision; `_revise_plan` rounds cap at 5).*

**TDD tests pass without exercising the implementation.** Not yet fully mitigated — the runtime "TDD tests must fail first" check is listed in the plan's out-of-scope section. Today, the primary model's dispute mechanism in Phase B is the safety valve: when a test looks wrong, the primary calls `request_test_change` and the expert either rewrites the test or explains why it is correct. *Observed when the expert wrote a test using a fixture that the primary's implementation could trivially satisfy without exercising the contract.*

---

## 15. What the Harness Expects From the LLM

Even with everything above, the LLM still has to be competent. Specifically:

- **Capability-first framing**, not persona assignment. Prompts say "Use your knowledge of software architecture to..." — not "You are a senior software architect...". This is enforced at the prompt-registry level.
- **Structured output compliance**. Every phase after Phase 2's tool loop ends with a `chat_structured` call against a Pydantic schema. Models that can follow JSON-schema-constrained decoding work well; those that cannot get validation failures and retries.
- **Tool-use reliability**. The control loop nudges text-only / truncated / repeated / interrupt situations, but a model that habitually hallucinates tool names or malforms arguments will churn. The primary model's tool-calling strength is one reason `qwen3-coder:30b` is the default primary.
- **Web-search willingness**. The `policy.web_search` block tells the model its training data may be stale; the `policy.required_citations` block (default on) demands a citation for every external framework. A model that confidently emits an outdated API signature without searching will be caught by the `_check_claim_verification` nudge after two test failures — but the first two attempts will still burn turns.
- **Proportionate reasoning**. At 32 k context windows, TDD auto-disables, tool-output limits scale down, the `file_summary` is LLM-compacted, and Phase 4 drops redundant scope / context re-injection. Larger context models (128 k, 1 M) get proportionally larger budgets.

The pipeline is a harness, not a substitute for a capable model. The guardrails make a good model shine and stop a mediocre model from breaking the build; they do not turn a weak model into a strong one.

---

## 16. Known Limitations (and Where They're Tracked)

- **TDD "tests must fail first" enforcement**: deferred — `workflow/tdd.py` does not yet run the expert's tests before the primary implements. Tracked in the strengthening plan's out-of-scope section.
- **Rename of a regression-guarded entity** requires a manual two-step (create new regression test → `run_command rm` for the old, which goes through destructive-command approval). A dedicated `/rename-regression` command is out of scope for now.
- **The extension's post-validation settings UI** still exposes the global env vars directly. The per-project commands.json path is now authoritative at the backend, but the UI does not yet reflect the priority inversion — a future extension change.
- **Parallel Phase 2 path** (when `LEAN_AI_NUM_PARALLEL>=2`) returns free-form text without structured FileSummary synthesis; validators skip cleanly when the structured object is `None`. Documented in `incomplete.md`.
- **In-loop events** (loop_detected, context_refresh, reminder_injected, claim_unverified) are scaffolded but not yet written to the training archive — `fire_workflow_event` exists; it's not threaded through `chat_with_tools` yet.

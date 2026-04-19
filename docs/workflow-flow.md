# Lean AI Workflow Flow — Tool & State Audit

## Overview

This document traces the complete flow from user input to session completion,
documenting which tools are available at each stage, how persistent state
(scratchpad, journal, step artifacts) is managed, and what survives context
refresh.

---

## Entry Points

```
User Input
    │
    ├─── /chat endpoint (POST /api/chat)
    │       → Conversational mode, no session, no persistence
    │
    └─── WebSocket workflow (WS /api/sessions/{id}/stream)
            │
            ├─── plan mode (default): clarify → plan → approve → execute
            ├─── fix mode (/fix prefix): investigate → implement
            └─── request mode (/request prefix): implement (neutral prompt)
```

---

## 1. Chat Endpoint (`routers/chat.py`)

```
┌──────────────────────────────────────────────────────────────────┐
│  /chat  —  Stateless conversational mode                        │
│                                                                  │
│  Tools: CHAT_TOOLS                                               │
│    read_file, grep_files, list_directory, directory_tree         │
│    search_internet, fetch_url, search_wiki*, fetch_wiki_page*   │
│    save_note, list_project_todos                                 │
│    list_recent_sessions, get_session_summary                     │
│    search_workspace_memory                                       │
│                                                                  │
│  State: NONE (no session, no scratchpad, no journal)             │
│  Max turns: 20                                                   │
│  Exit: First text-only response (text_only_exit_count=1)         │
│  Output: "Suggested Agent Prompt" for workflow handoff           │
│                                                                  │
│  Context built from:                                             │
│    • File tree, active editor content                            │
│    • project_context.md                                          │
│    • Workspace search results                                    │
│    • Recent session activity (last 3 sessions, 800 char cap)     │
│    • Image descriptions (vision model)                           │
│    • Refiner-enhanced messages (reference library injection)     │
└──────────────────────────────────────────────────────────────────┘
```

**Potential issues:** None — chat is stateless by design.

---

## 2. Plan Mode — Full Pipeline

### Phase 0: Session Setup (`routers/workflow.py`)

```
┌──────────────────────────────────────────────────────────────────┐
│  Session Setup                                                   │
│                                                                  │
│  1. Create git branch: lean-ai/{session_id} from default branch │
│  2. Stash uncommitted changes                                    │
│  3. Create WSMessageDispatcher (cancel/interrupt handling)        │
│  4. Optional: refine task via PromptRefiner                      │
│  5. Log initial task to conversation_logs                        │
│                                                                  │
│  State initialized: nothing yet (scratchpad/journal don't exist) │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
```

### Phase 1: Clarification (`pipeline.py → _clarify_task`)

```
┌──────────────────────────────────────────────────────────────────┐
│  Phase 1: Clarify (optional)                                     │
│  Model: request (or primary fallback)                            │
│                                                                  │
│  Tools: NONE (single LLM call via chat_raw)                      │
│  State: NONE                                                     │
│                                                                  │
│  Input: task + context[:5000]                                    │
│  Output: list of questions OR None (task is clear)               │
│  If questions: send to user, wait for answers, augment task      │
│                                                                  │
│  ⚠ assess_clarity() is a single-shot call — no tool use         │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
```

### Phase 1 (Planning): Scope Analysis (`planner.py → create_plan`)

```
┌──────────────────────────────────────────────────────────────────┐
│  Planning Phase 1: Scope Analysis                                │
│  Model: request (or primary fallback)                            │
│                                                                  │
│  Tools: restricted read-only subset (via chat_with_tools)        │
│    grep_files, read_file, list_directory,                        │
│    query_project_context, search_reference, task_complete        │
│  Budget: LEAN_AI_PLAN_PHASE1_MAX_TURNS (default 5)               │
│  text_only_exit_count=1 — single text response exits loop        │
│                                                                  │
│  State: Memories injected (read-only, from memory index)         │
│                                                                  │
│  Input: task + full context + session memories (2% budget)       │
│  Output: 8-section scope document:                               │
│    PROBLEM / PURPOSE, DELIVERABLES, IN SCOPE, OUT OF SCOPE,      │
│    DOWNSTREAM CONSUMERS, ASSUMPTIONS (with verification hints),  │
│    SUCCESS CRITERIA, RISKS                                       │
│                                                                  │
│  Crystal-clear tasks exit with zero tool calls via the           │
│  text_only_exit_count=1 setting. Tools are used only to resolve  │
│  genuine ambiguity in the task description.                      │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
```

### Phase 2: Codebase Exploration (`planner_exploration.py`)

```
┌──────────────────────────────────────────────────────────────────┐
│  Planning Phase 2: File Identification + Exploration             │
│  Model: request (or primary fallback)                            │
│                                                                  │
│  ┌─ IF num_parallel >= 2 ─────────────────────────────────────┐  │
│  │  PARALLEL PATH                                              │  │
│  │                                                              │  │
│  │  Phase 2a: Broad scan (directory_tree, grep_files,          │  │
│  │            list_directory, task_complete)                    │  │
│  │    • No scratchpad/journal tools                            │  │
│  │    • No context refresh callback                            │  │
│  │    • Max 15 turns                                           │  │
│  │                                                              │  │
│  │  Phase 2b: Parallel deep-dive (read_file, grep_files,      │  │
│  │            task_complete)                                    │  │
│  │    • N workers read file subsets concurrently                │  │
│  │    • No scratchpad/journal tools                            │  │
│  │    • No context refresh callback                            │  │
│  │    • Max 30/N turns per worker                              │  │
│  │                                                              │  │
│  │  ⚠ FINDING: Parallel exploration has NO scratchpad,         │  │
│  │    NO journal, NO context refresh. Findings only survive     │  │
│  │    in the LLM output text. Long explorations at small        │  │
│  │    context windows could lose mid-exploration discoveries.   │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌─ IF num_parallel == 1 ─────────────────────────────────────┐  │
│  │  SERIAL PATH                                                │  │
│  │                                                              │  │
│  │  Tools: Phase-2-specific filter of                          │  │
│  │    build_planning_tools_with_scratchpad()                   │  │
│  │    • DROPPED: search_reference, list_reference_documents   │  │
│  │      (noise for file identification)                         │  │
│  │    • ADDED:   record_file_observation                        │  │
│  │    • KEPT:    read_file, grep_files, list_directory,        │  │
│  │               directory_tree, query_project_context,        │  │
│  │               search_internet, fetch_url,                    │  │
│  │               search_wiki*, fetch_wiki*,                    │  │
│  │               update_scratchpad, add_journal_entry,         │  │
│  │               task_complete                                  │  │
│  │                                                              │  │
│  │  User prompt opens with a STRICT ASSUMPTIONS checklist      │  │
│  │  walking every Phase 1 verification hint before general     │  │
│  │  exploration.                                                │  │
│  │                                                              │  │
│  │  State:                                                      │  │
│  │    ✅ Observations (.lean_ai/observations/{id}.json)        │  │
│  │       — upserted by record_file_observation, re-injected    │  │
│  │         on context refresh                                   │  │
│  │    ✅ Scratchpad — available, re-injected on refresh         │  │
│  │    ✅ Journal    — available, re-injected on refresh         │  │
│  │    ✅ Context refresh callback — rebuilds from disk          │  │
│  │    ✅ Existing pad/journal injected at start (recovery)      │  │
│  │                                                              │  │
│  │  SYNTHESIS PASS (after the loop exits):                     │  │
│  │    _synthesize_file_summary → chat_structured with          │  │
│  │    planning.exploration_synthesis_system coerces the        │  │
│  │    observations + scratchpad + journal + prose into a       │  │
│  │    validated FileSummary Pydantic model:                    │  │
│  │      files_to_modify, files_to_create,                      │  │
│  │      files_read_for_context, missing_infrastructure,        │  │
│  │      verified_references, assumptions_resolved, notes       │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  Output (serial): (FileSummary, markdown, elapsed) tuple         │
│    • FileSummary propagates to Phase 4 validators                 │
│    • markdown is what Phase 3/4 prompts see as {file_summary}     │
│  Output (parallel): (None, raw_text, elapsed) — validators skip   │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
```

### Phase 3: Design + Risk Synthesis (`planner.py`)

```
┌──────────────────────────────────────────────────────────────────┐
│  Planning Phase 3: Design Synthesis (TWO-PASS)                   │
│  Model: expert (or primary fallback)                             │
│                                                                  │
│  PASS 1 — chat_with_tools exploration/verification:              │
│    Tools: build_design_tools()                                   │
│      search_internet, fetch_url, search_reference,               │
│      list_reference_documents, search_wiki*, fetch_wiki*,        │
│      task_complete                                                │
│    max_turns=15, text_only_exit_count=1                          │
│                                                                  │
│    The prompt (planning.design_system) calls out                 │
│    FileSummary.key_snippets as AUTHORITATIVE transcriptions —    │
│    the expert trusts them rather than re-deriving signatures.    │
│    Tools are used only for verifying external dependencies NOT   │
│    already in FileSummary.verified_references.                   │
│                                                                  │
│    Output: free-form exploration prose.                          │
│                                                                  │
│  PASS 2 — chat_structured synthesis:                             │
│    _synthesize_design_and_risks →                                │
│      planning.design_synthesis_system coerces Pass 1 prose +     │
│      inputs into a DesignAndRisks Pydantic model:                │
│        naming_conventions (list[NamingConvention]),              │
│        change_designs (list[ChangeDesign]),                      │
│        missing_files (list[MissingFile]),                        │
│        dependency_order (list[DependencyOrder]),                 │
│        critical_risks (list[CriticalRisk]),                      │
│        citations (list[VerifiedReference]),                      │
│        notes                                                      │
│                                                                  │
│  State: NONE (no scratchpad/journal injection — removed)         │
│    FileSummary from Phase 2 is the authoritative bridge.         │
│                                                                  │
│  Input: task + scope + project_context + FileSummary markdown    │
│  Output: (DesignAndRisks, rendered markdown, missing_files text) │
│                                                                  │
│  The old _extract_missing_files secondary LLM call is gone —     │
│  {missing_files} for Phase 4 is derived deterministically from   │
│  DesignAndRisks.missing_files via _format_missing_files.         │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
```

### Phase 4: Plan Assembly (`planner.py`)

```
┌──────────────────────────────────────────────────────────────────┐
│  Planning Phase 4: Structured Plan Assembly                      │
│  Model: expert (or primary fallback)                             │
│                                                                  │
│  Tools: NONE (single structured output call via chat_structured) │
│  State: NONE                                                     │
│                                                                  │
│  Input: scope + FileSummary markdown + DesignAndRisks markdown + │
│         missing_files (bullet list) + project_context            │
│  Output: ExecutionPlan schema with:                              │
│    naming_conventions (list[NamingConvention] — structured now), │
│    name_registry (list[NameRegistryEntry] — structured now),     │
│    steps (list[PlanStep]),                                       │
│    plan_validation_warnings (list[str]),                         │
│    affected_files, test_strategy, etc.                           │
│                                                                  │
│  POST-GENERATION VALIDATION (pure Python, no regex):             │
│    _check_hallucinated_paths — step.file_path not in the         │
│      known-paths set built from FileSummary + DesignAndRisks     │
│    _uncovered_missing_files — DesignAndRisks.missing_files       │
│      entries not covered by any step                             │
│    _check_edit_create_consistency — edit_file on unknown-to-     │
│      modify paths, create_file on unknown-to-create paths        │
│                                                                  │
│  Warnings log AND append to plan.plan_validation_warnings,       │
│  which the approval_required WebSocket message carries through   │
│  to the extension's approval UI. BLOCKING uncovered missing_files│
│  trigger a single _revise_plan auto-revision round; any still    │
│  uncovered on the second pass fall through to warn-only.         │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
```

### Phase 5: Verification Step Generation (`planner.py`)

```
┌──────────────────────────────────────────────────────────────────┐
│  Planning Phase 5: Test/Verification Steps                       │
│  Model: expert (or primary fallback)                             │
│                                                                  │
│  Tools: NONE (single structured output call via chat_structured) │
│  State: NONE                                                     │
│                                                                  │
│  Prompt selection (registry-backed):                             │
│    - planning.verification_user_normal  → tests + run_tests step │
│    - planning.verification_user_tdd     → tests only, no run_tests
│                                                                  │
│  Structured prompt inputs (pure Python helpers):                 │
│    _build_verification_targets(FileSummary, DesignAndRisks)      │
│      → bullet list of files needing coverage derived from        │
│        DesignAndRisks.change_designs + FileSummary.files_to_create
│    _build_security_concerns(DesignAndRisks)                      │
│      → bullet list of critical_risks with severity + mitigation  │
│                                                                  │
│  Input: plan JSON + test_command + FileSummary object +          │
│         DesignAndRisks object + rendered file_summary markdown   │
│  Output: plan with appended test steps + run_tests               │
│          (TDD: separate tdd_test_steps list, no run_tests)       │
│                                                                  │
│  POST-GENERATION VALIDATION:                                     │
│    _check_test_path_conventions — flags create_file steps whose  │
│    paths don't contain a test token (test/spec, case-insensitive)│
│    AND don't match a directory prefix learned from Phase 2's     │
│    files_read_for_context. Warnings append to                    │
│    plan.plan_validation_warnings via the Phase 4 surfacing path. │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
```

### Approval Loop (`pipeline.py → _wait_for_approval`)

```
┌──────────────────────────────────────────────────────────────────┐
│  User Approval                                                   │
│                                                                  │
│  Tools: NONE (user interaction only)                             │
│  State: NONE                                                     │
│                                                                  │
│  Send plan markdown to user, wait for:                           │
│    • "approve" → proceed to execution                            │
│    • "user_message" → feedback → revise plan (up to 5 rounds)   │
│                                                                  │
│  Revision uses _revise_plan() (planner_helpers.py):              │
│    Single chat_structured call with previous plan + feedback     │
│    Model: expert (or primary fallback)                           │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
```

### Execution (`executor.py → execute_plan`)

```
┌──────────────────────────────────────────────────────────────────┐
│  Plan Execution                                                  │
│  Model: primary (standard) or TDD routing (see below)            │
│                                                                  │
│  Tools: IMPLEMENTATION_TOOLS                                     │
│    create_file, edit_file, read_file                             │
│    run_command, run_tests, run_lint, format_code                 │
│    list_directory, directory_tree, grep_files                    │
│    update_scratchpad, add_journal_entry                          │
│    search_internet, fetch_url, search_wiki*, fetch_wiki*         │
│    task_complete                                                  │
│                                                                  │
│  State per step:                                                 │
│    ✅ Scratchpad — available, re-injected on context refresh     │
│    ✅ Journal — available, re-injected on context refresh        │
│    ✅ Step artifacts — in-memory dict, survives refresh          │
│       (closure variable, not in message list)                    │
│    ✅ Completed descriptions — in-memory list, survives refresh  │
│    ✅ Context refresh callback rebuilds from disk                │
│                                                                  │
│  Step grouping:                                                  │
│    • Barrier tools (run_tests, run_lint, etc.) force sequential  │
│    • Same file → sequential                                      │
│    • Independent files → parallel (if num_parallel >= 2)         │
│                                                                  │
│  Per-step turns: up to implementation_max_turns                  │
│                                                                  │
│  ⚠ OBSERVATION: Step artifacts are capped at 10% of context     │
│    budget. Oldest entries evicted when over budget. This means   │
│    early file contents may be lost for later steps.              │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
```

### Post-Validation (`validation.py`)

```
┌──────────────────────────────────────────────────────────────────┐
│  Post-Validation                                                 │
│                                                                  │
│  Phase A: Deterministic auto-fix (sequential, no LLM)            │
│    1. format_code command (if configured)                        │
│    2. lint --fix command (if configured)                         │
│                                                                  │
│  Phase B: Reporting (parallel via asyncio.gather)                │
│    3. lint check                                                 │
│    4. test check                                                 │
│                                                                  │
│  If all pass → done                                              │
│  If failures → validation fix loop                               │
└──────────────────────────────────────────────────────────────────┘
         │ (on failure)
         ▼
```

### Validation Fix Loop (`validation.py → _run_validation_fix_loop`)

```
┌──────────────────────────────────────────────────────────────────┐
│  Validation Fix Loop                                             │
│  Model: primary (last retry: expert if configured)               │
│  Retries: up to post_validation_max_retries (default 2)          │
│                                                                  │
│  Tools: IMPLEMENTATION_TOOLS (or TDD_IMPLEMENTATION_TOOLS)       │
│  File scope: restricted to plan's affected_files                 │
│  Max turns: 30 (hardcoded, per attempt)                          │
│                                                                  │
│  State:                                                          │
│    ⚠ NO scratchpad re-injection                                 │
│    ⚠ NO journal re-injection                                    │
│    ⚠ NO context refresh callback                                │
│    ⚠ NO task reminder callback                                  │
│                                                                  │
│  The fix loop starts with a fresh message list each attempt:     │
│    system prompt + fix prompt (error output, verify-first steps) │
│                                                                  │
│  ⚠ FINDING: If a fix attempt runs long enough to trigger        │
│    context refresh (unlikely at 30 turns but possible with       │
│    many tool calls), there is no callback to rebuild state.      │
│    The default refresh behavior in facade.py would just drop     │
│    old messages without re-injecting scratchpad/journal.         │
│                                                                  │
│  ⚠ FINDING: Scratchpad/journal from the main execution phase    │
│    are not available to the fix loop. The LLM cannot see what    │
│    it previously recorded about its approach or findings.        │
│    This may limit its ability to reason about the root cause.    │
│                                                                  │
│  After each attempt: re-run full validation (including auto-fix) │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
```

### Completion (`executor.py`, `routers/workflow.py`)

```
┌──────────────────────────────────────────────────────────────────┐
│  Completion                                                      │
│                                                                  │
│  1. Update project_context.md (incremental, if files changed)    │
│  2. Auto-commit on branch                                        │
│  3. Fire hooks (async, best-effort):                             │
│     • auto_push_integration (push to Jira/ServiceNow)            │
│     • auto_extract_session_memories (LLM extracts memories)      │
│  4. Send "complete" WebSocket message                            │
│  5. User can merge (applies changes to default branch) or        │
│     abandon (deletes branch)                                     │
│                                                                  │
│  On merge/abandon:                                               │
│    ✅ delete_scratchpad(repo_root, session_id)                   │
│    ✅ delete_journal(repo_root, session_id)                      │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. Fix Mode Flow

```
┌──────────────────────────────────────────────────────────────────┐
│  Fix Mode (/fix prefix)                                          │
│                                                                  │
│  Phase 1: Investigation (if enable_fix_investigation=true)       │
│  Model: primary (or expert/request if configured)                │
│                                                                  │
│  Tools: INVESTIGATION_TOOLS                                      │
│    read_file, list_directory, directory_tree, grep_files         │
│    run_tests, run_lint (diagnostics only)                        │
│    search_internet, fetch_url, search_wiki*, fetch_wiki*         │
│    update_scratchpad, add_journal_entry                          │
│    task_complete                                                  │
│                                                                  │
│  State:                                                          │
│    ✅ Scratchpad — available                                     │
│    ✅ Journal — available                                        │
│    ✅ Existing pad/journal injected at start (recovery)          │
│    ⚠ NO context refresh callback during investigation           │
│      (investigation is typically short, but if context fills     │
│       up, scratchpad/journal won't be re-injected)              │
│                                                                  │
│  No write tools — forces read-first behavior                     │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼ (continues same message list — no reset)
┌──────────────────────────────────────────────────────────────────┐
│  Phase 2: Implementation                                         │
│  Model: primary (or expert/request)                              │
│                                                                  │
│  Tools: IMPLEMENTATION_TOOLS (full write + shell access)         │
│                                                                  │
│  State:                                                          │
│    ✅ Scratchpad — available, re-injected on context refresh     │
│    ✅ Journal — available, re-injected on context refresh        │
│    ✅ Context refresh callback — rebuilds from disk              │
│    ✅ Task reminder — periodic, includes scratchpad/journal hint │
│                                                                  │
│  Investigation findings flow via continued message list.         │
│  Scratchpad/journal persist across the mode transition.          │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
   Post-Validation → Fix Loop (same as plan mode)
```

---

## 4. Request Mode Flow

```
┌──────────────────────────────────────────────────────────────────┐
│  Request Mode (/request prefix)                                  │
│                                                                  │
│  Single Phase: Implementation (no investigation)                 │
│  Model: request (if configured, else primary)                    │
│                                                                  │
│  Tools: IMPLEMENTATION_TOOLS                                     │
│  State: Same as fix mode implementation phase                    │
│    ✅ Scratchpad, journal, context refresh, task reminder         │
│                                                                  │
│  Weaker text-only nudge (request model is chatty)                │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
   Post-Validation → Fix Loop (same as plan mode)
```

---

## 5. Resume Flow

```
┌──────────────────────────────────────────────────────────────────┐
│  Resume (/resume)                                                │
│                                                                  │
│  1. Checkout existing session branch                             │
│  2. Load execution context                                       │
│  3. Build resume task message:                                   │
│       ORIGINAL TASK: {task}                                      │
│       SESSION JOURNAL: {journal from disk}                       │
│       SESSION SCRATCHPAD: {scratchpad from disk}                 │
│       "Continue where you left off."                             │
│  4. Run in fix mode (direct execution, no re-planning)           │
│                                                                  │
│  ✅ Journal + scratchpad survive crash via disk persistence      │
└──────────────────────────────────────────────────────────────────┘
```

---

## 6. Context Refresh Mechanism (`facade.py`)

```
┌──────────────────────────────────────────────────────────────────┐
│  Context Refresh                                                 │
│  Trigger: token usage > 70% of context window                    │
│                                                                  │
│  What gets DROPPED:                                              │
│    • Old assistant messages, tool call/result pairs               │
│    • Investigation findings (unless in journal)                  │
│    • Previous step details (unless in scratchpad/journal)        │
│                                                                  │
│  What gets RE-INJECTED (if callback provided):                   │
│    ✅ Fresh system prompt (rebuilt from disk)                     │
│    ✅ Fresh user message with current task state                  │
│    ✅ Scratchpad (re-read from disk)                              │
│    ✅ Journal (re-read from disk)                                 │
│    ✅ "[CONTEXT REFRESHED]" marker                                │
│                                                                  │
│  What SURVIVES in memory (not in messages):                      │
│    ✅ step_artifacts dict (closure variable)                      │
│    ✅ completed_descriptions list (closure variable)              │
│    ✅ executed tool call history (closure variable)               │
│                                                                  │
│  ⚠ If NO callback provided: drops old messages, keeps system +  │
│    last user message only. No scratchpad/journal re-injection.   │
└──────────────────────────────────────────────────────────────────┘
```

---

## 7. State Lifecycle Summary

```
State Item          │ Created    │ Survives Refresh │ Survives Phases │ Cleaned Up
────────────────────┼────────────┼──────────────────┼─────────────────┼────────────
Scratchpad          │ On first   │ ✅ (if callback) │ ✅ (disk-based) │ merge/abandon
                    │ tool call  │                  │                 │
Journal             │ On first   │ ✅ (if callback) │ ✅ (disk-based) │ merge/abandon
                    │ tool call  │                  │                 │
Step artifacts      │ Per step   │ ✅ (in-memory)   │ ❌ (per-exec)   │ end of execute_plan
                    │ execution  │                  │                 │
Session memories    │ Post-      │ N/A              │ N/A (extracted  │ never (permanent)
                    │ completion │                  │ after session)  │
Planning output     │ Phase 1-5  │ N/A              │ Via text params │ not persisted
                    │            │                  │ between phases  │
```

---

## 8. Identified Issues & Observations

### Issue 1: Validation Fix Loop — No State Re-injection
**Location:** `validation.py:314` — `chat_with_tools()` call
**Severity:** Medium
**Description:** The validation fix loop does not pass `on_context_refresh`,
`task_reminder`, or inject scratchpad/journal into its messages. If the fix
attempt hits the context window limit (unlikely at 30 turns but possible with
verbose tool output), the default refresh behavior drops everything without
re-injecting state. More importantly, the LLM doesn't see what the main
execution recorded in the scratchpad (architecture decisions, patterns found)
which could help it reason about why validation failed.

### Issue 2: Parallel Phase 2 — No Scratchpad/Journal/Refresh/FileSummary
**Location:** `planner_exploration.py` parallel path
**Severity:** Low (deferred — see `incomplete.md`)
**Description:** Parallel Phase 2 (2a scan + 2b deep-dive) has no scratchpad,
journal, or context refresh callbacks AND does not run the
`_synthesize_file_summary` pass — it produces free-form text output and
returns `None` for the structured `FileSummary`. Downstream Phase 4
validators (`_check_hallucinated_paths`, `_check_edit_create_consistency`)
skip cleanly when the object is `None`. Parallel mode is deferred for
hardening because the primary-audience workflow runs `num_parallel=1`. See
`incomplete.md` for the parallel-path improvement punch list (regex-based
file-path extraction in 2a, non-sharing deep-dive workers, etc.).

### Issue 3: Fix Mode Investigation — No Context Refresh
**Location:** `fix_mode.py` — investigation phase
**Severity:** Low
**Description:** The investigation phase (read-only) does not set a context
refresh callback. Investigation typically runs short, but large codebases with
many grep results could fill context. The scratchpad and journal tools ARE
available, so findings can be persisted to disk — but they won't be
re-injected if context refreshes. The implementation phase that follows DOES
have a refresh callback and will pick up the disk state.

### Issue 4: Phase 2→3 Bridge — RESOLVED
**Location:** `planner.py` — transition between Phase 2 and Phase 3
**Severity:** Resolved
**Description:** Originally Phase 2 (serial) could write to scratchpad/journal
but Phase 3 did not read them. Resolved by the Phase 2/3 hardening: Phase 2
now produces a structured `FileSummary` (with `key_snippets` carrying
authoritative file transcriptions), which is rendered to markdown and passed
to Phase 3 as `{file_summary}` and also propagated as a Pydantic object for
Phase 4 validators. Phase 3 no longer needs scratchpad/journal access — the
`FileSummary` is the authoritative bridge. The old ad-hoc scratchpad/journal
injection into Phase 3's message list was removed in the Phase 3 hardening
pass.

### Issue 5: Step Artifact Eviction
**Location:** `executor.py:312-317`
**Severity:** Low
**Description:** Step artifacts (in-memory file contents from prior steps) use
an LRU-like eviction: oldest entries dropped when over 10% budget. For plans
with many files, early file contents may be evicted before later steps that
reference them. The LLM can always `read_file` again, but it costs a tool
turn and the LLM may not know the content was evicted.

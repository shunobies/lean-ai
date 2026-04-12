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
│    • Refiner-enhanced messages (knowledge injection)             │
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
│  Tools: NONE (single LLM call via chat_raw)                      │
│  State: Memories injected (read-only, from memory index)         │
│                                                                  │
│  Input: task + full context + session memories (2% budget)       │
│  Output: scope text (what to change, boundaries)                 │
│                                                                  │
│  ⚠ No tool use — memories are injected but scope analysis is    │
│    a single LLM call, not a multi-turn session                   │
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
│  │  Tools: PLANNING_TOOLS_WITH_SCRATCHPAD                      │  │
│  │    read_file, grep_files, list_directory, directory_tree     │  │
│  │    search_internet, fetch_url, search_wiki*, fetch_wiki*    │  │
│  │    update_scratchpad, add_journal_entry, task_complete       │  │
│  │                                                              │  │
│  │  State:                                                      │  │
│  │    ✅ Scratchpad — available, re-injected on refresh         │  │
│  │    ✅ Journal — available, re-injected on refresh            │  │
│  │    ✅ Context refresh callback — rebuilds from disk          │  │
│  │    ✅ Existing pad/journal injected at start (recovery)      │  │
│  │    ✅ Task reminder every 15 turns includes pad/journal hint │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  Output: file_identification text (file list + analysis)         │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
```

### Phase 3: Design + Risk Synthesis (`planner.py`)

```
┌──────────────────────────────────────────────────────────────────┐
│  Planning Phase 3: Design Synthesis                              │
│  Model: expert (or primary fallback)                             │
│                                                                  │
│  Tools: DESIGN_TOOLS                                             │
│    search_internet, fetch_url, search_wiki*, fetch_wiki*         │
│    task_complete                                                  │
│                                                                  │
│  State: NONE (no scratchpad/journal access)                      │
│                                                                  │
│  Input: scope + file_identification + project_context.md         │
│  Output: naming conventions + change design + gap analysis       │
│                                                                  │
│  ⚠ OBSERVATION: Phase 3 receives the Phase 2 output as text.   │
│    Scratchpad/journal written in Phase 2 are NOT injected here.  │
│    Phase 2 findings only flow via the text output parameter.     │
│    This is by design — expert model gets a clean context.        │
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
│  Input: scope + file_identification + design + project_context   │
│  Output: ExecutionPlan JSON (steps with tool/file/instruction)   │
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
│  Input: plan JSON + test_command                                 │
│  Output: plan with appended test steps + run_tests               │
│          (TDD: separate tdd_test_steps list)                     │
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

### Issue 2: Parallel Phase 2 — No Scratchpad/Journal/Refresh
**Location:** `planner_exploration.py:256-360`
**Severity:** Low (by design)
**Description:** Parallel Phase 2 (2a scan + 2b deep-dive) has no scratchpad,
journal, or context refresh callbacks. The serial path has all three. This is
intentional — parallel workers are short-lived and independent. But it means
parallel exploration at small context windows loses mid-exploration findings
if a worker hits the context limit.

### Issue 3: Fix Mode Investigation — No Context Refresh
**Location:** `fix_mode.py` — investigation phase
**Severity:** Low
**Description:** The investigation phase (read-only) does not set a context
refresh callback. Investigation typically runs short, but large codebases with
many grep results could fill context. The scratchpad and journal tools ARE
available, so findings can be persisted to disk — but they won't be
re-injected if context refreshes. The implementation phase that follows DOES
have a refresh callback and will pick up the disk state.

### Issue 4: Phase 2→3 Scratchpad Handoff
**Location:** `planner.py` — transition between Phase 2 and Phase 3
**Severity:** Informational
**Description:** Phase 2 (serial) can write to scratchpad/journal, but Phase 3
does not read them. Phase 3 only receives the Phase 2 text output as a
parameter. This is by design (expert model gets a clean context), but means
any nuanced observations the Phase 2 model recorded in the scratchpad that
didn't make it into the final output text are lost. The journal persists on
disk but is never read by phases 3-5.

### Issue 5: Step Artifact Eviction
**Location:** `executor.py:312-317`
**Severity:** Low
**Description:** Step artifacts (in-memory file contents from prior steps) use
an LRU-like eviction: oldest entries dropped when over 10% budget. For plans
with many files, early file contents may be evicted before later steps that
reference them. The LLM can always `read_file` again, but it costs a tool
turn and the LLM may not know the content was evicted.

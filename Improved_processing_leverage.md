# Improved Processing Leverage — Context Optimization for the Planner

## Context

The 5-phase planner passes raw, uncompressed output between phases. By Phase 5 (plan assembly), the prompt contains: task + scope + file_identification + change_design + risks — with file_identification potentially being tens of thousands of tokens from 50 turns of grep/read exploration. Research on the "Lost in the Middle" problem (Liu et al., TACL 2024) shows LLMs exhibit a U-shaped attention curve: information at the start and end of context is reliably used, but middle content degrades by 30%+. This means the largest blob (file_identification) lands in the worst attention zone, and critical planning instructions get buried.

Three categories of fix, all in `backend/src/lean_ai/llm/planner.py` and `backend/src/lean_ai/llm/client.py`:

## Changes

### 1. Compress Phase 2 output before passing downstream
**File:** `backend/src/lean_ai/llm/planner.py` (between Phase 2 and Phase 3)

Add a summarization step after Phase 2 completes. Use `chat_raw` to condense `file_identification` into a structured, compact format:
- File paths with one-line purpose descriptions
- Only the relevant code sections (not entire file dumps)
- Explicit list of consumer files discovered via grep

This replaces the raw `file_identification` blob (which is the concatenated LLM output from up to 50 tool-call turns) with a focused summary. Estimated compression: 60-80% token reduction.

```python
# Phase 2.5: Compress exploration results
await _send_stage(ws, "Compressing exploration results...")
logger.info("Planning Phase 2.5: Compressing file identification")
file_summary = await llm_client.chat_raw(
    messages=[
        {"role": "system", "content": PLAN_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"TASK: {task}\n\n"
                f"EXPLORATION RESULTS:\n{file_identification}\n\n"
                "Compress the exploration results into a structured summary. "
                "For each file that needs to be CREATED or MODIFIED:\n"
                "- File path\n"
                "- Why it needs changes (one line)\n"
                "- The specific code sections that will be modified "
                "(only the relevant lines, not the entire file)\n\n"
                "For files read for CONTEXT only:\n"
                "- File path and the pattern/structure to follow (compact)\n\n"
                "IMPORTANT: Preserve all file paths, line numbers, and code "
                "snippets needed to construct accurate edits. Drop narrative, "
                "tool call logs, and redundant file content."
            ),
        },
    ],
    max_tokens=phase_max_tokens,
)
```

Then pass `file_summary` (not `file_identification`) into Phases 3 and 5. Keep `file_identification` available as a fallback reference only for Phase 3 (change design), which needs the most detail.

### 2. Reorder Phase 5 prompt for U-curve alignment
**File:** `backend/src/lean_ai/llm/planner.py` (Phase 5 prompt construction)

Current order places file content in the dead middle:
```
TASK → SCOPE → FILES AND CONTENT → CHANGE DESIGN → RISKS → instructions
```

Reorder to put decision-critical information at the start and end (high-attention zones):
```
TASK → RISKS (with missing file gaps) → CHANGE DESIGN → FILES AND CONTENT → SCOPE → assembly instructions + echoed critical rules
```

Rationale:
- **Start**: Task + risks (including missing file coverage) — the most actionable information for plan assembly
- **Middle**: Change design + file content — reference material that can degrade slightly without breaking the plan
- **End**: Assembly instructions with echoed consumer-tracing mandate — ensures the structural rules are in the recency-bias zone

### 3. Echo critical instructions at end of Phase 5
**File:** `backend/src/lean_ai/llm/planner.py` (Phase 5 prompt, after all context)

Append a short recap at the end of Phase 5's prompt:
```python
"FINAL CHECKLIST — verify before producing the plan:\n"
"- Every file identified in the risk assessment as missing is included\n"
"- The plan covers the full data flow: model → controller → view\n"
"- Each edit_file step has specific line references and context\n"
"- Steps are ordered so dependencies come first\n"
"- Verification steps (run_tests/run_lint) follow groups of changes"
```

This exploits recency bias — the last tokens before generation starts get the strongest attention.

### 4. Add task reminder to Phase 2 exploration
**File:** `backend/src/lean_ai/llm/planner.py` (Phase 2 `chat_with_tools` call)

The `chat_with_tools` method already supports `task_reminder` injection at intervals (used during execution). Enable it for Phase 2 planning:

```python
tool_calls, file_identification = await llm_client.chat_with_tools(
    messages=phase2_messages,
    tools=PLANNING_TOOLS,
    tool_executor_fn=_read_only_executor,
    max_turns=50,
    max_tokens=phase_max_tokens,
    task_reminder=(
        f"REMINDER — You are exploring the codebase for this task: {task}\n\n"
        "Have you used grep_files to trace ALL consumers of modified entities? "
        "Do NOT finalize until you have searched for every model/class being "
        "changed and read every file that references it."
    ),
    reminder_interval=15,
)
```

This re-injects the consumer-tracing directive every 15 turns, counteracting Ollama's tendency to truncate the system prompt and original task from the beginning of the KV cache during long exploration sessions.

## Files to Modify

- `backend/src/lean_ai/llm/planner.py` — All 4 changes (compression step, reorder Phase 5, echo instructions, task reminder)

## Verification

- All existing tests pass (`cd backend && .venv/bin/python -m pytest tests/ -v`)
- Linter clean (`cd backend && .venv/bin/ruff check src/ tests/`)
- Manual test: run the planner on a task that modifies a model in a project with views/controllers. Verify that:
  - Phase 2.5 produces a compact summary (check logs for character count vs raw file_identification)
  - Phase 5 plan includes consumer files (controllers, views) not just data-layer files
  - The task reminder appears in logs at turn 15/30/45 during Phase 2

# Incomplete Items

Tracks work that was intentionally deferred or left partial. See CLAUDE.md's
"No Stubs Rule" — this file documents what is not yet complete rather than
leaving placeholder code in the codebase.

## Phase 2 Parallel Exploration (deferred)

**Status:** Functional today but not actively hardened. Findings recorded
here in case the design is revisited later.

The planner's Phase 2 has two paths, selected by `LEAN_AI_NUM_PARALLEL`:

- **Serial path** (`num_parallel=1`, default) — one `chat_with_tools` loop
  in [`_run_serial_exploration`](backend/src/lean_ai/llm/planner_exploration.py#L428)
  with the full planning tool set, scratchpad, journal, context refresh.
- **Parallel path** (`num_parallel>=2`) — Phase 2a broad scan with
  directory/grep-only tools and a 15-turn cap, followed by Phase 2b parallel
  deep-dive reads split across workers via `asyncio.gather`.

In a local-first single-worker deployment — the intended audience for Lean
AI — the parallel path is dead weight. A single modern LLM call is fast
enough that splitting the read phase across workers provides little wall-
clock benefit, and the parallel path has three known structural weaknesses
we chose not to fix:

1. **Regex-based file-path extraction** at
   [planner_helpers.py:101-140](backend/src/lean_ai/llm/planner_helpers.py#L101)
   parses the scan-phase text output with a regex. Fragile. If Phase 2a
   emitted JSON instead, the regex could be deleted.
2. **Deep-dive workers don't share discoveries.** Worker A finding a
   cross-file reference doesn't inform Worker B's reads, so some consumers
   get traced twice and some get missed.
3. **Phase 2a is a pure parallelism artifact** — it exists only to give
   workers a file list to divide. It does not sanitize, it does not help
   non-local handoff, and the serial path bypasses it entirely. If
   parallelism is ever dropped, Phase 2a and Phase 2b can be deleted
   outright and only `_run_serial_exploration` kept.

Not fixing now because: the primary-audience workflow never enables
`LEAN_AI_NUM_PARALLEL>=2`, Phase 2 hardening efforts are focused on the
serial path (structured file-observation capture, `chat_structured` JSON
output, checklist-driven assumption verification), and every improvement to
the serial path would need to be re-ported to the parallel path if it were
kept. Revisit if a heavy-machine user actually benefits from it.

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

## Training Archive — per-turn capture + in-loop events (resolved)

**Wired.** `chat_with_tools` now takes an optional `telemetry_context`
dict (`{repo_root, session_id, phase, role}`) threaded through every
high-value call site: Phase 1 / 2 / 3 in the planner, per-step
implementation execution, validation fix loop, fix/request modes, all
four TDD phases, and the chat endpoint. The 4 previously-deferred
events (`loop_detected`, `context_refresh`, `reminder_injected`,
`claim_unverified`) fire at their natural call sites and link back to
the `training_traces` row via `trace_uuid`. A `session_start` event
records the model layout fingerprint at the top of `run_workflow`.

Additional tables added alongside the wire-up: `tool_executions`,
`tool_compressions`, `clarifications`, `phase2_syntheses`,
`diff_decisions`. See [docs/training.md](docs/training.md) for the
expanded schema and [docs/training-ingestion.md](docs/training-ingestion.md)
for the lean_ai_serve consumer contract.

Parallel-path Phase 2 (`LEAN_AI_NUM_PARALLEL>=2`) is still not wired
for per-turn capture — it remains the deferred exploration path
documented above. Local-first users get full capture; heavy-machine
parallel users see `training_traces` rows only for the serial portions.

## worker_implementation_unfinished — Tool-output compression (deferred)

**Status:** Wired but dead. Machinery exists end-to-end; the activating
parameter is never supplied.

The [`worker_llm_client`](backend/src/lean_ai/routers/dependencies.py#L180)
singleton is instantiated at startup, and
[`tool_executor.py`](backend/src/lean_ai/workflow/tool_executor.py)
already contains:

- [`_COMPRESSIBLE_TOOLS`](backend/src/lean_ai/workflow/tool_executor.py#L140)
  — allowlist (`read_file`, `grep_files`, `run_tests`, `run_lint`,
  `run_command`, `search_internet`, `fetch_url`).
- [`_compress_with_worker`](backend/src/lean_ai/workflow/tool_executor.py#L166)
  — size-thresholded LLM summarization of a tool output. Threshold is
  5% of the active context window × 3.5.
- [`execute_with_compression`](backend/src/lean_ai/workflow/tool_executor.py#L858)
  — wraps `execute()` and routes through the worker when
  `worker_client is not None`.

**The wire is cut at the call sites.** Every `make_tool_executor(...)`
call in the codebase — plan execution, plan investigation, TDD writing,
TDD dispute, validation fix loop, fix mode — builds the executor without
passing `worker_client`. `execute_with_compression` early-returns and
raw output flows through unchanged.

Not enabling now because the risk of lossy summarization outweighs the
context-window win in most places:

1. **`edit_file` fidelity** — when a plan step uses `read_file` to set
   up a find-and-replace, the primary needs byte-exact content.
   Compression will make edits fail silently.
2. **`grep_files` line numbers** — Phase 2 observation recording
   depends on the `file:line:content` format being preserved
   verbatim. A worker paraphrase breaks `record_file_observation`.
3. **Test failure signatures** — validation fix-loop diagnosis hinges
   on the exact error line + stack-trace context. Summaries regularly
   drop the one line that matters.
4. **Current spill-to-disk behavior is already good.** Outputs >2000
   chars are saved to `.lean_ai/tool_output/` and the last 40 lines are
   returned with a "call `read_file` for the rest" hint (see
   `tool_executor.py:509-537` for `run_tests` / `run_lint` /
   `run_command`). For test failures specifically, the failing
   assertion is usually near the end — the tail is often exactly what
   the LLM needs and extra context is one tool call away on demand.

**If revisited later** — don't enable globally. Enable selectively
with per-phase and per-tool guardrails:

- **Where it's safe**: Phase 2 exploration and fix-mode investigation.
  Both build a mental map rather than write code, so lossy
  summarization of file reads and grep results is acceptable. Would
  meaningfully help primary during Phase 2 now that it handles
  exploration directly.
- **Where it's not**: plan-step execution, validation fix loop, TDD
  dispute evaluation — all need exact output for correctness.
- **Per-tool prompt tuning**: worker prompts must be tool-specific.
  `grep_files` → "preserve line numbers and file:line:content
  format"; `run_tests` → "keep the final assertion line and its
  5-line surrounding context verbatim"; `read_file` → "preserve
  imports, class signatures, and function boundaries."
- **Smallest useful change**: a new
  `LEAN_AI_COMPRESS_PHASE2_TOOL_OUTPUT` setting (default off) that
  gates `worker_client=worker_llm_client` at the two safe call sites
  only. No behavior change elsewhere.

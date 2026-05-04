# Workflow (Phase-Based, Mandatory)

Follow phases in order. Do not skip required report/scratch updates.

**Global state-event rule (applies to every phase):** whenever a phase starts/exits, a key decision is made, or control moves after a significant tool result, emit a typed event to `.lean_ai/state/{session_id}.jsonl` (phase/tool/checkpoint). The markdown file remains an auditable artifact, not the recovery source of truth.

## Phase 0: Intake

**Allowed tools:** user chat only.

**Actions:**

1. Ask for missing requirements:
   - vehicle type/body style
   - max budget
   - user location (city/ZIP)
   - search radius
   - mileage cap
   - title constraints
   - seller type preference
   - optional criteria
2. If user is silent, apply defaults:
   - radius: 250 miles from provided location
   - preserve existing hybrid/price/mileage defaults
3. Recap interpreted requirements and wait for confirmation.

**Required state event update:** emit phase/tool/checkpoint events for phase entry, key decisions, and phase exit.

| Event type | Required emissions in this phase |
| --- | --- |
| `phase_transition` | Emit on entry to Phase 0 and on exit from Phase 0. |
| `checkpoint` | Emit after each completed “max 2 external calls then record” cycle and after the intake recap/confirmation milestone. |
| `context_refreshed` | Emit whenever refresh/recovery context is rebuilt during intake. |

**Required report update:** none.

**Required scratch_pad update:** none.

**Exit condition:** user requirements are complete (or defaults applied) and recap is confirmed.

## Phase 1: Ledger Setup

**Allowed tools:** `read_file`, `create_file`, `scratch_pad`, `journal`.

**Actions:**

1. Check whether `hybrid_car_private_seller_research.md` exists.
2. Detect whether `.lean_ai/state/{session_id}.jsonl` already exists and can be read as prior session state.
3. If report is missing, create it from template; if present, do not recreate.
4. Summarize recent state events (most recent first) to identify the last completed phase/checkpoint.
5. Reconcile markdown report sections against recent events:
   - compare section counts to event-derived totals
   - compare pending queue rows to recent pending enqueue/review events
   - compare last source touched in report notes/tables to latest relevant event metadata
6. Choose continuation point: continue from the next incomplete phase instead of restarting discovery.
7. Record setup/recovery outcome in scratch_pad and journal.

**Required state event update:** emit phase/tool/checkpoint events for phase entry, key decisions, and phase exit.

| Event type | Required emissions in this phase |
| --- | --- |
| `phase_transition` | Emit on entry to Phase 1 and on exit from Phase 1. |
| `tool_called` | Emit for each file update operation attempt (`create_file`, `edit_file`, or equivalent ledger/scratch updates). |
| `tool_succeeded` / `tool_failed` | Emit outcome for each file update operation attempt recorded by `tool_called`. |
| `checkpoint` | Emit after each completed “max 2 external calls then record” cycle and at ledger-setup completion. |
| `context_refreshed` | Emit whenever refresh/recovery context is rebuilt during setup. |

**Required report update:** create file only if missing.

**Required scratch_pad update:** record report existence/creation, detected ledger status, reconciliation outcome, discrepancy notes (if any), and next phase.

**Exit condition:** report exists, recovery context is reconciled, and continuation phase is logged.

**Recovery discrepancy rule (mandatory):** if event history and markdown report disagree during setup/recovery, emit a `checkpoint` event that captures the mismatch and add a `Search Notes` entry describing the discrepancy before proceeding to the next phase.

## Phase 2: Source Discovery

**Allowed tools:** `search_internet`, `scratch_pad`.

**Actions:**

1. Run broad + source-specific searches.
2. Use known listing URL patterns to discover likely listing paths.
3. Do not stop just because first results lack direct listing URLs.

**Required state event update:** emit phase/tool/checkpoint events for phase entry, key decisions, and phase exit.

| Event type | Required emissions in this phase |
| --- | --- |
| `phase_transition` | Emit on entry to Phase 2 and on exit from Phase 2. |
| `tool_called` | Emit before each `search_internet` call. |
| `tool_succeeded` / `tool_failed` | Emit outcome after each `search_internet` call. |
| `checkpoint` | Emit after each completed “max 2 external calls then record” cycle and when source-candidate set is ready for fetch. |
| `context_refreshed` | Emit whenever refresh/recovery context is rebuilt during discovery. |

**Required report update:** none yet.

**Required scratch_pad update:** summarize discovered source candidates and next fetch target.

**Exit condition:** at least one fetchable search/result/source URL identified.

## Phase 3: Search URL and Crawl Tracking

**Allowed tools:** `fetch_url`, `edit_file`, `scratch_pad`.

**Actions:**

1. Fetch search/result/source pages.
2. Append useful entries to `Working Search URLs`.
3. Append each fetched page to `Crawled Pages`.
4. Append blocked/JS-only/limited findings to `Search Notes`.

**Required state event update:** emit phase/tool/checkpoint events for phase entry, key decisions, and phase exit.

| Event type | Required emissions in this phase |
| --- | --- |
| `phase_transition` | Emit on entry to Phase 3 and on exit from Phase 3. |
| `tool_called` | Emit before each `fetch_url` call and each file update operation attempt (`edit_file`). |
| `tool_succeeded` / `tool_failed` | Emit outcome after each `fetch_url` call and each `edit_file` update attempt. |
| `checkpoint` | Emit after each completed “max 2 external calls then record” cycle and when crawl tracking tables are reconciled for the current batch. |
| `context_refreshed` | Emit whenever refresh/recovery context is rebuilt during crawl tracking. |

**Required report update:** append rows/bullets in tracked sections.

**Required scratch_pad update:** counts and next crawl action.

**Exit condition:** at least one candidate listing URL discovered OR source is logged as limited/blocked.

## Phase 4: Pending Candidate Collection

**Allowed tools:** `fetch_url`, `edit_file`, `scratch_pad`.

**Actions:**

1. Append candidate listing URLs to `Pending Candidate URLs`.
2. Batch discovery before deep verification when many candidates exist.
3. If pending count exceeds limit, stop discovery and move to verification.

**Required state event update:** emit phase/tool/checkpoint events for phase entry, key decisions, and phase exit.

| Event type | Required emissions in this phase |
| --- | --- |
| `phase_transition` | Emit on entry to Phase 4 and on exit from Phase 4. |
| `tool_called` | Emit before each `fetch_url` call and each file update operation attempt (`edit_file`). |
| `tool_succeeded` / `tool_failed` | Emit outcome after each `fetch_url` call and each `edit_file` update attempt. |
| `checkpoint` | Emit after each completed “max 2 external calls then record” cycle and when pending-queue thresholds/milestones are evaluated. |
| `context_refreshed` | Emit whenever refresh/recovery context is rebuilt during candidate collection. |

**Required report update:** append to pending table.

**Required scratch_pad update:** pending count and verification queue.

**Exit condition:** pending queue is non-empty or all tested sources exhausted.

## Phase 5: Candidate Verification

**Allowed tools:** `fetch_url`, `edit_file`, `scratch_pad`, `journal`.

**Actions:**

1. Fetch listing URL.
2. Verify hybrid status with reliable source.
3. Classify accepted/rejected.
4. Append exactly one row to `Qualifying Listings` or `Rejected Listings`.
5. Mark pending row as reviewed per allowed rule.

**Required state event update:** emit phase/tool/checkpoint events for phase entry, key decisions, and phase exit.

| Event type | Required emissions in this phase |
| --- | --- |
| `phase_transition` | Emit on entry to Phase 5 and on exit from Phase 5. |
| `tool_called` | Emit before each `fetch_url` call and each file update operation attempt (`edit_file`). |
| `tool_succeeded` / `tool_failed` | Emit outcome after each `fetch_url` call and each `edit_file` update attempt. |
| `checkpoint` | Emit after each completed “max 2 external calls then record” cycle and after each classification reconciliation milestone (accepted/rejected + pending marker). |
| `context_refreshed` | Emit whenever refresh/recovery context is rebuilt during verification. |

**Required report update:** one classification row + pending cleanup marker.

**Required scratch_pad update:** decision rationale, updated counts, next candidate.

**Exit condition:** queue is empty OR acceptance target reached OR source budget reached.

## Phase 6: Reconciliation

**Allowed tools:** `edit_file`, `scratch_pad`, `journal`.

**Actions:**

1. Replace `Current Counts` numbers.
2. Ensure pending URLs reviewed/marked.
3. Update `Search Limitation` with concrete blockers or `None`.
4. Renumber accepted listings only if numbering drifted.

**Required state event update:** emit phase/tool/checkpoint events for phase entry, key decisions, and phase exit.

| Event type | Required emissions in this phase |
| --- | --- |
| `phase_transition` | Emit on entry to Phase 6 and on exit from Phase 6. |
| `tool_called` | Emit for each file update operation attempt (`edit_file`) used for counts/limitations/renumbering reconciliation. |
| `tool_succeeded` / `tool_failed` | Emit outcome for each reconciliation file update attempt. |
| `checkpoint` | Emit after each reconciliation milestone (counts synced, pending reviewed, limitations finalized, numbering verified). |
| `context_refreshed` | Emit whenever refresh/recovery context is rebuilt during reconciliation. |

**Required report update:** counts + final limitation text.

**Required scratch_pad update:** final totals and uncertainty.

**Exit condition:** report internally consistent.

## Phase 7: Final Response

**Allowed tools:** user chat, `journal`.

**Actions:**

1. Report qualifying listing count.
2. Report filename.
3. Report biggest uncertainty.
4. Report local-only vs nationwide expansion.
5. Report useful sources and blocked sources.
6. Report next steps.

**Required state event update:** emit phase/tool/checkpoint events for phase entry, key decisions, and phase exit.

| Event type | Required emissions in this phase |
| --- | --- |
| `phase_transition` | Emit on entry to Phase 7 and on exit from Phase 7. |
| `checkpoint` | Emit after each reconciliation milestone reflected in the final user summary and after final response delivery. |
| `context_refreshed` | Emit whenever refresh/recovery context is rebuilt during final response preparation. |

**Required report update:** none.

**Required scratch_pad update:** none.

**Exit condition:** user-facing summary delivered.

## One Tool Batch Then Record Rule

Never run long search/fetch loops without state updates.

- Maximum batch: 2 tool calls that gather external data (`search_internet` or `fetch_url`).
- After that batch, record updates in:
  - machine event ledger (`.lean_ai/state/{session_id}.jsonl`) with typed tool/checkpoint events
  - report ledger (`edit_file` on markdown) when any report-relevant fact was found
  - `scratch_pad` always
- If no useful data was found, still append a `Search Notes` bullet and scratch_pad note.

## Safe Targeting Rule for File Edits

When using `grep`/`find`/`edit_file` to update report sections:

1. Locate the exact section anchor comment (for example, `<!-- ANCHOR:QUALIFYING_LISTINGS -->`).
2. Apply the smallest possible edit under that anchor section only.
3. Do not target generic phrases like "Search Notes" that may appear in prose elsewhere.

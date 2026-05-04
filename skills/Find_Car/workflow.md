# Workflow (Phase-Based, Mandatory)

Follow phases in order. Do not skip required ledger/scratch updates.

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

**Required ledger update:** none.

**Required scratch_pad update:** none.

**Exit condition:** user requirements are complete (or defaults applied) and recap is confirmed.

## Phase 1: Ledger Setup

**Allowed tools:** `read_file`, `create_file`, `scratch_pad`, `journal`.

**Actions:**

1. Check whether `hybrid_car_private_seller_research.md` exists.
2. If missing, create it from template.
3. Record ledger setup in scratch_pad and journal.

**Required state event update:** emit phase/tool/checkpoint events for phase entry, key decisions, and phase exit.

**Required ledger update:** create file only if missing.

**Required scratch_pad update:** record file existence/creation and next phase.

**Exit condition:** ledger exists and setup is logged.

## Phase 2: Source Discovery

**Allowed tools:** `search_internet`, `scratch_pad`.

**Actions:**

1. Run broad + source-specific searches.
2. Use known listing URL patterns to discover likely listing paths.
3. Do not stop just because first results lack direct listing URLs.

**Required state event update:** emit phase/tool/checkpoint events for phase entry, key decisions, and phase exit.

**Required ledger update:** none yet.

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

**Required ledger update:** append rows/bullets in tracked sections.

**Required scratch_pad update:** counts and next crawl action.

**Exit condition:** at least one candidate listing URL discovered OR source is logged as limited/blocked.

## Phase 4: Pending Candidate Collection

**Allowed tools:** `fetch_url`, `edit_file`, `scratch_pad`.

**Actions:**

1. Append candidate listing URLs to `Pending Candidate URLs`.
2. Batch discovery before deep verification when many candidates exist.
3. If pending count exceeds limit, stop discovery and move to verification.

**Required state event update:** emit phase/tool/checkpoint events for phase entry, key decisions, and phase exit.

**Required ledger update:** append to pending table.

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

**Required ledger update:** one classification row + pending cleanup marker.

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

**Required ledger update:** counts + final limitation text.

**Required scratch_pad update:** final totals and uncertainty.

**Exit condition:** ledger internally consistent.

## Phase 7: Final Response

**Allowed tools:** user chat, `journal`.

**Actions:**

1. Report qualifying listing count.
2. Report ledger filename.
3. Report biggest uncertainty.
4. Report local-only vs nationwide expansion.
5. Report useful sources and blocked sources.
6. Report next steps.

**Required state event update:** emit phase/tool/checkpoint events for phase entry, key decisions, and phase exit.

**Required ledger update:** none.

**Required scratch_pad update:** none.

**Exit condition:** user-facing summary delivered.

## One Tool Batch Then Record Rule

Never run long search/fetch loops without state updates.

- Maximum batch: 2 tool calls that gather external data (`search_internet` or `fetch_url`).
- After that batch, record updates in:
  - machine state ledger (`.lean_ai/state/{session_id}.jsonl`) with typed tool/checkpoint events
  - reporting ledger (`edit_file` on markdown) when any report-relevant fact was found
  - `scratch_pad` always
- If no useful data was found, still append a `Search Notes` bullet and scratch_pad note.

## Safe Targeting Rule for File Edits

When using `grep`/`find`/`edit_file` to update ledger sections:

1. Locate the exact section anchor comment (for example, `<!-- ANCHOR:QUALIFYING_LISTINGS -->`).
2. Apply the smallest possible edit under that anchor section only.
3. Do not target generic phrases like "Search Notes" that may appear in prose elsewhere.

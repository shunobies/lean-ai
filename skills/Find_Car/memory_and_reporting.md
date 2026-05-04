# Memory and Reporting

## Append-Safe Report Update Rules

## Event Ledger vs Report Ledger

- **Event ledger** = machine recovery/state continuity in `.lean_ai/state/{session_id}.jsonl` (JSONL typed events).
- **Report ledger** = user-facing evidence/reporting in `hybrid_car_private_seller_research.md` (markdown).

To reduce wrong-line matches from `grep`/`find`/`edit_file`, use unique section anchors in the report file.
Match the exact anchor comment first, then edit the nearest table/bullet block.

Never rewrite the whole report for a single update. Target only one section per edit.

Allowed edits to `hybrid_car_private_seller_research.md`:

1. Append one row to an existing table.
2. Replace numeric counts under `## Current Counts`.
3. Append one bullet under `## Search Notes`.
4. Append one bullet under `## Requirements Change Log`.
5. Replace `## Search Limitation` during final reconciliation.

Forbidden unless user explicitly requests:

- Rewriting entire sections
- Reordering or deleting prior accepted/rejected rows
- Regenerating the full report file
- Changing heading structure

## Report Anchor Map (Use Exact Anchor Strings)

- `<!-- ANCHOR:CURRENT_COUNTS -->`
- `<!-- ANCHOR:QUALIFYING_LISTINGS -->`
- `<!-- ANCHOR:REJECTED_LISTINGS -->`
- `<!-- ANCHOR:PENDING_CANDIDATE_URLS -->`
- `<!-- ANCHOR:WORKING_SEARCH_URLS -->`
- `<!-- ANCHOR:CRAWLED_PAGES -->`
- `<!-- ANCHOR:REQUIREMENTS_CHANGE_LOG -->`
- `<!-- ANCHOR:SEARCH_NOTES -->`
- `<!-- ANCHOR:SEARCH_LIMITATION -->`

Editing rule:

1. Find anchor comment by exact string.
2. Edit only content directly under that anchor's section.
3. Never search by a generic phrase that appears in prose.

## Section-Specific Edit Rules

### Qualifying Listings

- Append exactly one new row at table end.
- Do not edit previous rows.
- Keep all required fields populated (`Not listed` / `Unclear` if needed).

### Rejected Listings

- Append exactly one new row at table end.
- Reason must be explicit and concise.
- Do not remove historical rejects.

### Pending Candidate URLs

- Append one row per new candidate URL.
- When reviewed, mark `Next Action` as `Reviewed -> Accepted` or `Reviewed -> Rejected`.
- Do not delete historical pending rows.

### Working Search URLs

- Append one row per useful search URL/filter pattern.
- Keep `Filters Confirmed` factual (only fetched-confirmed filters).

### Crawled Pages

- Append one row per fetched page.
- `Page Type` must match observed content (`Search result page`, `Listing page`, `Blocked/inaccessible page`, etc.).

### Search Notes

- Append one bullet per limitation, blocker, or decision note.
- Keep notes timestamped or source-tagged when practical.

### Requirements Change Log

- Append one bullet each time user changes constraints.
- Include old rule -> new rule summary.

### Current Counts

- Replace only numeric values.
- Must reflect report tables after reconciliation.

### Search Limitation

- Replace full section text only in reconciliation.
- Use concrete limitations, not vague statements.

## Correct vs Incorrect Report Edits

### Correct (append one qualifying row)

- Add one new row at end of `## Qualifying Listings` table.
- Leave every existing row unchanged.

### Incorrect (forbidden)

- Reformat entire table while adding one row.
- Renumber or rewrite prior rows without reconciliation need.

### Correct (append one rejected row)

- Add one new row with URL and explicit reject reason.

### Incorrect (forbidden)

- Delete pending row history instead of marking reviewed.

## scratch_pad (Short-Term State)

Update after:

- report creation/existence check
- each tool batch (max 2 discovery/fetch calls)
- candidate queue growth
- accept/reject decisions
- source switch
- local-to-nationwide expansion

Keep concise and action-oriented.

## journal (Durable Milestones)

Update after:

- report setup
- every 5 accepted listings
- every 10 rejected listings
- completion of a major source
- meaningful URL-pattern discovery
- search-radius expansion
- before final response

## Final Checklist Before Final Response

- Current counts match table reality.
- Every fetched page is represented in `Crawled Pages` or `Search Notes`.
- Pending queue rows are either still pending or marked reviewed.
- Search limitations are explicit.
- Dealer/private-seller rule reflects latest user-approved requirements.
- Biggest uncertainty is identified for user.

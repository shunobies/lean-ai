# Workflow

## Mandatory First Step
Before any web search:
1. Check whether `hybrid_car_private_seller_research.md` exists.
2. If missing, create it from `templates/hybrid_car_private_seller_research.template.md`.
3. Immediately record file creation in both `scratch_pad` and `journal`.

## Non-Negotiable Operating Loop
After every `fetch_url`, immediately record the outcome in the active ledger using `edit_file`.

Each fetched URL must map to exactly one action:
1. add to Qualifying Listings
2. add to Rejected Listings
3. add to Pending Candidate URLs
4. add to Working Search URLs
5. add to Crawled Pages
6. add a Search Notes entry

Do not perform more than 2 consecutive `search_url`/`fetch_url` calls without `edit_file` or `scratch_pad`.

### General loop
1. `search_url`
2. `scratch_pad`
3. `fetch_url`
4. `edit_file`
5. `scratch_pad`
6. `journal` only at milestones

### Candidate listing loop
1. `fetch_url` listing
2. `fetch_url` hybrid verification source
3. `edit_file` with accepted/rejected decision
4. `scratch_pad` count update and next action

### Search-result page loop
1. `fetch_url` search-result page
2. inspect URL pattern
3. `edit_file` (Working Search URLs / Crawled Pages / Pending URLs / Search Notes)
4. `scratch_pad` next candidates

## Pre-query checklist
Before each new search query, confirm:
- all fetched listings were recorded
- working URL patterns were captured
- crawled pages were captured
- scratch_pad has current counts/next action
- pending URLs were reviewed if too many

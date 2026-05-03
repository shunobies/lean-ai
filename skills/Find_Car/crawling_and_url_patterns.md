# Crawling and URL Pattern Discovery

## Crawling Scope

- Crawl only publicly accessible pages readable by `fetch_url`.
- If `fetch_url` is insufficient, use controlled `curl` fallback in `tools_and_fallbacks.md`.
- Do not bypass logins, CAPTCHAs, hidden APIs, anti-bot controls, paywalls, or robots restrictions.

## Controlled Crawl Budget

- Max 5 result/search pages per source before usefulness review
- Max 3 pagination pages per search URL
- Max 25 candidate listing URLs per source before switching to verification
- Max depth 2 (result page -> pagination/listing -> listing)
- If pending URLs exceed 15, pause discovery and verify pending first

## Do Not Give Up Search Ladder

If direct listing URLs are not immediately available, follow this ladder before declaring source failure:

1. Retry query with source operator (e.g., `site:source.com hybrid private seller`).
2. Use source-specific filters in search URL (`max_price`, `radius`, `mileage`).
3. Fetch source search results and extract listing-like links.
4. Follow pagination links up to budget.
5. Infer listing URL pattern and test one candidate via `fetch_url`.
6. If JS-only or blocked, log limitation and switch source.

Only stop discovery after either:

- pending queue limit reached,
- source budget exhausted, or
- source limitations logged with concrete evidence.

## Per-Source Loop

1. Find or construct a search URL.
2. Fetch and record in `Crawled Pages`.
3. Extract listing candidates and pagination links.
4. Add listing candidates to `Pending Candidate URLs`.
5. Record useful filters/patterns in `Working Search URLs`.
6. Review pending listings and classify accepted/rejected.

## Useful Link Patterns to Inspect

Listing-like patterns:

- `/cars/`, `/car/`, `/vehicle/`, `/vehicledetail/`, `/listing/`, `/d/`, `/cto/`, `/motors/`, `/used/`, `/inventory/`

Pagination/filter patterns:

- `page=`, `p=`, `offset=`, `start=`, `next`, `search_distance=`, `radius=`, `max_price=`, `price_max=`, `maxMileage=`, `mileageTo=`

## URL Pattern Verification Rules

- You may infer patterns, but must verify with `fetch_url`.
- Do not claim a filter works unless fetched output reflects that filter.
- If search pages are JS-only or blocked, append a `Search Notes` bullet and move to another source.

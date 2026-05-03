# Crawling and URL Pattern Discovery

## Crawling scope
Only crawl publicly accessible pages readable by `fetch_url`. If `fetch_url` is insufficient, use the controlled `curl` fallback in `tools_and_fallbacks.md` and log it.
Do not bypass logins, CAPTCHAs, hidden APIs, anti-bot controls, or access restrictions.

## Controlled crawl budget
- Max 5 result/search pages per source before evaluating usefulness
- Max 3 pagination pages per search URL
- Max 25 candidate listing URLs per source before switching to review
- Max depth 2 (result page -> pagination/listing -> listing)

If pending URLs exceed 15, stop crawling and review pending first.

## Per-source loop
1. Find/construct a search URL
2. Fetch and record in `Crawled Pages`
3. Extract listing candidates and pagination links
4. Add listing candidates to `Pending Candidate URLs`
5. Record useful filters/patterns in `Working Search URLs`
6. Review pending listings and classify accepted/rejected

## Useful link patterns to inspect
Listing-like patterns:
- `/cars/`, `/car/`, `/vehicle/`, `/vehicledetail/`, `/listing/`, `/d/`, `/cto/`, `/motors/`, `/used/`, `/inventory/`

Pagination/filter patterns:
- `page=`, `p=`, `offset=`, `start=`, `next`, `search_distance=`, `radius=`, `max_price=`, `price_max=`, `maxMileage=`, `mileageTo=`

## URL-pattern verification rules
You may infer patterns, but must verify with `fetch_url`.
Do not claim a filter works unless fetched output reflects that filter.

If search pages are JS-only or blocked, record limitation in Search Notes and move on.

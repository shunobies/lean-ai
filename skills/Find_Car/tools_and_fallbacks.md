# Tools and Fallbacks

## Primary tools
- `search_internet` for discovery
- `fetch_url` for reading public pages
- `edit_file` and `create_file` for ledger updates
- `scratch_pad` for short-term state
- `journal` for durable milestones

## Optional fallback: shell (`bash`) + `curl`
Use this fallback only when `fetch_url` is blocked, incomplete, or returns unusable output.

Allowed fallback behavior:
1. Use `curl` only for publicly accessible pages.
2. Use a normal browser-like user-agent and respect website access controls.
3. Do not bypass logins, CAPTCHAs, paywalls, robots restrictions, or anti-bot controls.
4. Save request output to a local evidence file so the retrieval attempt is auditable.
5. Immediately record in ledger Search Notes and/or Crawled Pages that `curl` fallback was used.

## Suggested evidence capture pattern
```bash
mkdir -p artifacts/find_car
curl -L --max-time 30 \
  -A "Mozilla/5.0" \
  "<URL>" \
  -o "artifacts/find_car/<safe_name>.html"
```

Then add an entry to:
- `Crawled Pages` (page type: `Blocked/inaccessible page` or `Search result page`)
- `Search Notes` (why fallback was needed and whether output was useful)

## When to avoid curl fallback
- If site clearly requires interactive JavaScript login flows and output is unreadable.
- If terms or protections indicate automated retrieval is not allowed.
- If repeated attempts are failing; record limitation and move to another source.

# Tools and Fallbacks

## Primary Tools

- `search_internet` for discovery
- `fetch_url` for public page retrieval
- `edit_file` and `create_file` for ledger updates
- `scratch_pad` for short-term state
- `journal` for durable milestones

## Source Failure Handling

When a source fails, do not stop the whole task. Use this sequence:

1. Record failure in `Crawled Pages` (if fetched) and `Search Notes`.
2. Classify failure type:
   - JS-only rendering
   - blocked / rate-limited
   - login/paywall required
   - no useful listing links
3. Try one alternative path:
   - different query
   - different source section
   - known listing URL pattern
4. If still blocked, move to next source and continue discovery ladder.

Stopping condition is budget or exhausted viable sources, not first failure.

## Optional Fallback: shell (`bash`) + `curl`

Use only when `fetch_url` is blocked, incomplete, or unusable.

Allowed behavior:

1. Use `curl` only for publicly accessible pages.
2. Use a normal browser-like user-agent.
3. Do not bypass protections or restrictions.
4. Save output to local evidence file for auditability.
5. Immediately log fallback usage in ledger.

Suggested pattern:

```bash
mkdir -p artifacts/find_car
curl -L --max-time 30 \
  -A "Mozilla/5.0" \
  "<URL>" \
  -o "artifacts/find_car/<safe_name>.html"
```

Then append:

- `Crawled Pages`: fetched URL + page type
- `Search Notes`: why fallback was used and whether it helped

## One Tool Batch Then Record

Hard rule:

- Run at most 2 consecutive discovery/fetch calls.
- Then perform recording steps:
  - ledger append/replace (allowed sections only)
  - scratch_pad state update
- Resume tooling only after recording.

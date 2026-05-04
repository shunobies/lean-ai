# Skill: Find_Car

Use this skill to find **private-seller hybrid vehicle listings** using a two-layer continuity model: machine state events for recovery plus an auditable markdown report artifact.

## Model Target

- Primary target model: `gpt-oss:20b`
- Reasoning mode: High
- Priority: correctness and auditability over speed

## Required Files

- `workflow.md`
- `criteria_and_validation.md`
- `crawling_and_url_patterns.md`
- `memory_and_reporting.md`
- `templates/hybrid_car_private_seller_research.template.md`
- `tools_and_fallbacks.md`

Read and follow all files above before starting the task.

## Core Rules

- Never fabricate listing details.
- Missing fields: `Not listed`.
- Uncertain fields: `Unclear`.
- Do not bypass logins, CAPTCHAs, paywalls, robots restrictions, or anti-bot controls.
- Default target is **private seller / sell-by-owner**.
- Dealer listings are excluded unless the user explicitly changes requirements.


## Two-Layer State Model

1. **Machine state of truth (continuity/recovery):** `.lean_ai/state/{session_id}.jsonl` as an append-safe event ledger with typed events (phase transitions, tool execution milestones, and checkpoints).
2. **Human/audit artifact (reporting):** `hybrid_car_private_seller_research.md` as the auditable report ledger for users/reviewers.

The markdown report must never be the sole recovery source; replayability and resume logic come from state events.

## Invocation Contract

When activated:

1. Load all required files.
2. Execute `workflow.md` phases in order.
3. Persist continuity/recovery state in `.lean_ai/state/{session_id}.jsonl` as typed phase/tool/checkpoint events.
4. Treat `hybrid_car_private_seller_research.md` as the human/audit report ledger artifact (append-safe edits per `memory_and_reporting.md`).

## Output Contract

Final response must include:

- number of qualifying listings found
- filename used
- biggest uncertainty
- whether search stayed local or expanded nationwide
- most useful sources
- blocked/limited sources
- recommended next steps

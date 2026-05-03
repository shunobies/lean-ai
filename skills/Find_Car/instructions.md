# Skill: Find_Car

Use this skill to find sell-by-owner hybrid vehicle listings under $19,000 and maintain a strict working ledger.

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

Read and follow all files above before starting the task. Use `tools_and_fallbacks.md` when `fetch_url` is insufficient.

## Requirements Intake (Mandatory Before Search)
Before any ledger creation or search/crawling, run a concise intake loop:
- Ask short clarifying questions for any missing constraints.
- Required intake fields: vehicle type/body style, max budget, user location (city/ZIP), search radius, mileage cap, title constraints, seller type preference, and optional criteria (year range, makes/models, fuel economy, drivetrain, seating, features, etc.).
- Defaults when user is silent: radius defaults to 250 miles around provided location; keep existing hybrid/price/mileage defaults only if the user does not provide alternatives.
- Confirm interpreted requirements in a short recap before proceeding.
- If requirements change mid-run, log a **Requirements Change** note in the ledger and update search strategy immediately.

## Invocation Contract
When activated:
1. Load all referenced files.
2. Execute the mandatory-first-step workflow exactly.
3. Treat the research markdown as a live ledger.
4. Never fabricate listing details.

## Output Contract
Final response must include:
- number of qualifying listings found
- filename created
- biggest uncertainty
- whether search stayed within 250 miles of Kansas City or expanded nationwide
- most useful sources
- blocked/limited sources
- recommended next steps

# Prompt Customization

Lean AI uses structured prompts to guide the LLM at every stage — planning, execution, fix mode, chat, context generation, and more. The prompt customization feature lets you override any of these prompts per project without modifying source code.

Overrides are stored in `.lean_ai/prompts.yaml` inside your project directory. Prompts you don't override continue using their compiled defaults.

Lean AI now supports two override scopes:

- **Global overrides** — the original behavior; apply to every model that uses a prompt
- **Scoped overrides** — apply only when `model + role + prompt key` match

Scoped overrides are what the role-tuning subsystem writes automatically for tuned `request`, `primary`, and `expert` models.

## Why Customize Prompts

- **Tune agent behavior** — adjust quality rules, tool usage patterns, or completion signaling for your specific workflow
- **Adapt planning** — modify how the agent scopes tasks, explores code, or assembles plans for your project's conventions
- **Change fix mode strategy** — customize how the agent investigates and diagnoses bugs
- **Adjust chat personality** — modify the chat system prompt for different interaction styles
- **Framework-specific guidance** — refine context generation prompts to produce better project summaries
- **Keep different models in different lanes** — let a chatty request model, a coder-tuned primary, and a reasoning-heavy expert each carry a different contract without forking the whole prompt set

## Override Resolution Order

When Lean AI resolves a prompt, it layers text in this order:

1. **Compiled default**
2. **Manual global override** from `.lean_ai/prompts.yaml`
3. **Scoped override** for the matching `model + role + prompt key`

That means:

- a manual global override still affects every model by default
- a scoped override wins only for its exact role/model pair
- role tuning can coexist with your global prompt edits instead of replacing them

## Role Tuning and Prompt Overrides

The role-tuning subsystem uses the same prompt registry and YAML file you do. On first use of a new `role + model` pair, Lean AI may:

1. Calibrate the model for that role
2. Save a profile under `.lean_ai/role_tuning/`
3. Write scoped overrides into `.lean_ai/prompts.yaml`

You can also generate those artifacts proactively with `/tune-roles`. That command prewarms the active `primary`, `request`, and `expert` role assignments and is useful when a team wants to tune once on a dedicated machine, commit the resulting `.lean_ai/role_tuning/*.json` profiles plus `.lean_ai/prompts.yaml`, and let other machines reuse the same tuned contracts without repeating the long calibration pass. Reuse only applies when the receiving machine has the same provider/model IDs and the saved profile still passes the normal freshness checks.

Those scoped overrides preserve the compiled prompt's required placeholders and base scaffolding, then prepend a constrained role contract chosen for that model.

Current tuned prompt surfaces are:

- **Request** — `chat.system`, `fix.request_system`
- **Primary** — `planning.scope_system`, `planning.exploration_system`, `execution.step_system`, `execution.implementation_system`, `fix.system`
- **Expert** — `planning.design_system`, `planning.assembly_system`, `planning.verification_system`, `fix.system`

## Opening the Editor

Open the Edit Prompts panel in one of two ways:

1. **Sidebar button** — click the pencil (edit) icon in the Lean AI sidebar header
2. **Command palette** — run `Lean AI: Edit Prompts` (`Ctrl+Shift+P` / `Cmd+Shift+P`)

The panel opens as an editor tab alongside your code.

## Understanding the UI

### Category Navigation

The left sidebar groups prompts into categories:

| Category | What it controls |
|----------|------------------|
| **Core Policy** | Tool usage rules, completion signaling, quality standards, web search behavior, scratchpad usage |
| **Planning** | All 5 planning phases (system + user message pairs), task clarity assessment, task reminders |
| **Execution** | System prompts for general, step-based, and multi-turn implementation |
| **Fix Mode** | System prompts for `/fix`, the investigation phase, and `/request` mode |
| **Chat & Refinement** | Chat system prompt, chat refiner, task refiner, privacy strip for cloud providers |
| **Context Generation** | Project context generation, expansion, and style guide prompts |
| **Framework Guide** | Per-section prompts for framework guide generation (populated when a framework is detected) |
| **TDD & Vision** | TDD dispute evaluation and vision model image description |
| **Advanced** | Guardrail nudge messages (text-only, truncation, loop detection, fix mode switch) |

Click **All** to see every prompt, or click a category to filter.

### Search

The search bar at the top filters prompts by name, key, or description in real time.

### Prompt Cards

Each prompt appears as a collapsible card showing:

- **Name** — human-readable label (e.g. "Phase 1: Scope Analysis")
- **Modified** badge (blue) — the prompt has been customized
- **Caution** badge (yellow) — modifying this prompt carries risk (see the warning inside)

Click a card to expand it and see:

- **Description** — what the prompt does and when it runs
- **Warning** (if any) — cautions about modifying this prompt (e.g. strict output format requirements)
- **Text editor** — the full prompt text in a monospace textarea
- **Template variable chips** — placeholders the system substitutes at runtime

### Template Variables

Some prompts contain template variables like `{task}`, `{context}`, or `{reference_section}`. These appear as chips below the textarea:

- **Normal chips** — the variable is present in your text
- **Red chips** — the variable is missing. Saving will fail if a required variable is removed

Template variables use Python's `{variable_name}` format. The system substitutes these at runtime with actual values (the task description, project context, etc.). You must keep all required variables in your override — you can move them around within the prompt, but don't delete them.

### Saving and Resetting

- **Save Changes** (footer) — saves all modified prompts to `.lean_ai/prompts.yaml`. Validates template variables before writing
- **Reset to Default** (per card) — resets a single prompt back to its compiled default
- **Reset All to Defaults** (footer) — removes all overrides and deletes the YAML file

The footer status line shows counts of overridden and unsaved prompts.

## Common Customization Scenarios

### Adjusting code quality rules

Override **policy.quality** (Core Policy) to add project-specific standards. For example, require docstrings on all new functions, enforce a naming convention, or mandate copyright headers.

### Changing web search behavior

Override **policy.web_search** (Core Policy) to make the agent search more or less aggressively — e.g. search after every error, fetch more results, or disable searching entirely.

### Customizing planning scope analysis (Phase 1)

Override **planning.scope_system** or **planning.scope_user** (Planning) to guide how the agent analyzes tasks for your specific project architecture. Phase 1 runs on the primary model with a small read-only tool budget (`LEAN_AI_PLAN_PHASE1_MAX_TURNS`, default 5) and produces an 8-section scope document. Both prompts have a `{PHASE1_MAX_TURNS}` template variable the registry fills in from the setting — **do not remove it** from an override; `registry.validate` will flag overrides missing required placeholders.

### Customizing Phase 2 exploration

Override **planning.exploration_system** (Planning) or **planning.exploration_user** (Planning) to change how the agent identifies files and records observations. The user prompt opens with a strict ASSUMPTIONS checklist that walks each Phase 1 verification hint. The separate **planning.exploration_synthesis_system** prompt governs the post-loop `chat_structured` pass that coerces recorded observations + scratchpad + journal + prose into the validated `FileSummary` schema.

### Customizing Phase 3 design synthesis

Override **planning.design_system** or **planning.design_user** (Planning) to tune Pass 1 (tool-enabled exploration/verification). The **planning.design_synthesis_system** prompt governs Pass 2 (coerces Pass 1 prose + inputs into a `DesignAndRisks` schema).

### Customizing Phase 4 plan assembly

Override **planning.assembly_system** or **planning.assembly_user** (Planning). Note that the `naming_conventions` and `name_registry` fields on `ExecutionPlan` are now typed lists (`list[NamingConvention]` and `list[NameRegistryEntry]`) rather than prose strings; the user prompt points the model at the structured schema rather than asking for text templates.

### Customizing Phase 5 verification steps

Two separate prompts per mode — override either without affecting the other:

- **planning.verification_user_normal** — used when a test command is configured and TDD is disabled. Asks for test-file `create_file` steps + a final `run_tests` step.
- **planning.verification_user_tdd** — used when `LEAN_AI_ENABLE_TDD` is enabled. Asks for test-file `create_file` steps only; explicitly forbids `run_tests`.

Both receive `{verification_targets}` (from `DesignAndRisks.change_designs` + `FileSummary.files_to_create`) and `{security_concerns}` (from `DesignAndRisks.critical_risks`) so the model targets tests precisely. The shared **planning.verification_system** provides executor-model awareness and the common-LLM-defects checklist.

### Adjusting fix mode investigation

Override **fix.investigation** (Fix Mode) to change how the agent explores your codebase when diagnosing bugs — e.g. always check specific log files or configuration first.

### Customizing the chat assistant

Override **chat.system** (Chat & Refinement) to change the tone, focus, or constraints of the chat conversation. The prompt carries a `{CHAT_MAX_TURNS}` template variable kept in sync with the backend's chat turn setting (`LEAN_AI_CHAT_MAX_TURNS`, default 20) — **do not remove it** from an override. The default prompt encodes an **always-explore** default (every substantive reply begins with at least one grounding tool call) and a **strict two-round protocol** for agent-prompt requests (Round 1: explore + ask exactly 3-5 numbered clarifying questions; Round 2: targeted verification + emit `## Suggested Agent Prompt` with a `### References` block inside the fence).

## YAML File Format

Overrides are stored in `.lean_ai/prompts.yaml` at your project root. You can edit this file directly instead of using the UI.

```yaml
_version: 2
policy.quality: |
  - No stubs, no TODOs, no placeholder implementations.
  - Follow our team conventions for naming and structure.
  - All public functions must have type annotations.
policy.web_search: |
  Search the internet after every error. Fetch the top 2 results.
_scoped_overrides:
  - prompt_key: chat.system
    model_id: openai:gpt-4o
    agent_role: request
    text: |
      MODEL-SPECIFIC REQUEST ROLE TUNING:
      - Active role framing: Requirements Analyst
      ...
```

Rules:

- The `_version` key is metadata — don't remove it
- Keys use dot notation matching the prompt registry (e.g. `policy.tool`, `planning.scope_system`, `fix.system`)
- Scoped entries live under `_scoped_overrides`
- Use YAML literal block scalars (`|`) for multiline text to preserve newlines
- Only include prompts you want to override — missing keys use compiled defaults
- Unknown keys are logged as warnings and ignored
- Template variables (`{variable_name}`) must be preserved for prompts that require them

You normally do not need to hand-edit `_scoped_overrides` unless you want to author or inspect model-specific behavior directly.

To find the exact key for any prompt, expand the card in the Edit Prompts UI — the key is shown in the card header.

## Resetting Prompts

### From the UI

- **Single prompt** — expand the card, click **Reset to Default**
- **All prompts** — click **Reset All to Defaults** in the footer

### Manually

Delete `.lean_ai/prompts.yaml` from your project directory to reset everything, or edit the file to remove specific keys.

## API Endpoints

The prompt system exposes REST endpoints for programmatic access:

- `GET /api/prompts?repo_root=/path` — all prompts with defaults, current values, and metadata
- The response metadata distinguishes global overrides, scoped overrides, and prompts that have both
- `PUT /api/prompts` — save overrides (validates template variables)
- `POST /api/prompts/reset` — reset specific keys or all overrides

See the [API Reference](api-reference.md#prompts) for request/response details.

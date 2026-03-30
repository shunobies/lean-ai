# Prompt Customization

Lean AI uses structured prompts to guide the LLM at every stage — planning, execution, fix mode, chat, context generation, and more. The prompt customization feature lets you override any of these prompts per project without modifying source code.

Overrides are stored in `.lean_ai/prompts.yaml` inside your project directory. Prompts you don't override continue using their compiled defaults.

## Why Customize Prompts

- **Tune agent behavior** — adjust quality rules, tool usage patterns, or completion signaling for your specific workflow
- **Adapt planning** — modify how the agent scopes tasks, explores code, or assembles plans for your project's conventions
- **Change fix mode strategy** — customize how the agent investigates and diagnoses bugs
- **Adjust chat personality** — modify the chat system prompt for different interaction styles
- **Framework-specific guidance** — refine context generation prompts to produce better project summaries

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

Some prompts contain template variables like `{task}`, `{context}`, or `{knowledge_section}`. These appear as chips below the textarea:

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

### Customizing planning scope analysis

Override **planning.scope_system** (Planning) to guide how the agent analyzes tasks for your specific project architecture — e.g. always consider certain directories, or prefer specific design patterns.

### Adjusting fix mode investigation

Override **fix.investigation** (Fix Mode) to change how the agent explores your codebase when diagnosing bugs — e.g. always check specific log files or configuration first.

### Customizing the chat assistant

Override **chat.system** (Chat & Refinement) to change the tone, focus, or constraints of the chat conversation.

## YAML File Format

Overrides are stored in `.lean_ai/prompts.yaml` at your project root. You can edit this file directly instead of using the UI.

```yaml
_version: 1
policy.quality: |
  - No stubs, no TODOs, no placeholder implementations.
  - Follow our team conventions for naming and structure.
  - All public functions must have type annotations.
policy.web_search: |
  Search the internet after every error. Fetch the top 2 results.
```

Rules:

- The `_version` key is metadata — don't remove it
- Keys use dot notation matching the prompt registry (e.g. `policy.tool`, `planning.scope_system`, `fix.system`)
- Use YAML literal block scalars (`|`) for multiline text to preserve newlines
- Only include prompts you want to override — missing keys use compiled defaults
- Unknown keys are logged as warnings and ignored
- Template variables (`{variable_name}`) must be preserved for prompts that require them

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
- `PUT /api/prompts` — save overrides (validates template variables)
- `POST /api/prompts/reset` — reset specific keys or all overrides

See the [API Reference](api-reference.md#prompts) for request/response details.

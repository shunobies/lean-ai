# Skills Guide

Lean AI supports a local skill system for repeatable workflows.

A skill is a folder in your repository at:

```text
.lean_ai/skills/<skill-name>/
```

The only required file is:

```text
instructions.md
```

You invoke a skill with:

```text
/skill <skill-name> <task>
```

Example:

```text
/skill api-review audit the new billing endpoints for auth, validation, and rate-limit coverage
```

> **Important:** the command is `/skill` (singular), not `/skills`.

## How `/skill` works

When you run `/skill <name> <task>`, Lean AI:

1. Loads `.lean_ai/skills/<name>/instructions.md`.
2. Scans that file for referenced local files (for example markdown links, inline file names, and explicit config file mentions).
3. Inlines those referenced file contents.
4. Sends the assembled skill context and your task to the agent.

This lets you keep reusable process knowledge in version-controlled files and apply it on demand.

## Recommended skill structure

```text
.lean_ai/skills/<skill-name>/
  instructions.md         # required
  checklist.md            # optional, referenced from instructions.md
  templates/              # optional task templates
  examples/               # optional examples of good outputs
```

Keep `instructions.md` focused and link to supporting docs when details are large.

## Skill authoring best practices

- **Start with role + objective.** Begin with one short paragraph defining what the skill is optimizing for.
- **Constrain output format.** Ask for concrete sections, checklists, or acceptance criteria.
- **Specify scope boundaries.** Explicitly state what to include and what to avoid.
- **Require verification.** Include expected test/lint/doc checks the agent should run.
- **Reference repo standards.** Link style guides, architecture notes, or domain docs your team already uses.
- **Prefer deterministic language.** Use phrases like “must”, “always”, and “do not” for critical rules.
- **Keep tasks composable.** One skill should solve one recurring workflow pattern well.

## Which model is used for a skill?

`/skill` does **not** force a separate model by itself. It uses the same model routing configured for your active Lean AI session:

- The primary model handles normal execution and tool use.
- If you configured an expert model, expert-only phases still use that expert model.

In practice, skills change the **instructions/context**, not the model selection policy.

See [Configuration](configuration.md) for model routing options.

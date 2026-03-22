"""Structured plan schema for plan-driven execution.

The planner produces an ExecutionPlan where each step maps to one tool call.
The executor iterates through steps, feeding each to a constrained LLM
that translates the detailed instruction into a single tool invocation.
"""

from pydantic import BaseModel


class PlanStep(BaseModel):
    """One discrete step in the execution plan.

    Each step maps to roughly one tool call.  The ``instruction`` field
    is detailed enough that a constrained LLM can translate it into the
    exact tool invocation without exploring the codebase.
    """

    step_number: int
    tool: str
    """Tool to call: ``create_file``, ``edit_file``, ``run_tests``,
    ``run_lint``, ``format_code``."""

    file_path: str
    """Target file path (relative to repo root).
    Empty string for ``run_tests`` / ``run_lint`` / ``format_code``."""

    instruction: str
    """Detailed natural-language instruction for this step.

    For ``edit_file``: which section of the file to modify, what to find,
    what to replace it with, line references, patterns to follow.

    For ``create_file``: what the file should contain, imports, structure,
    patterns to follow from existing files.

    For ``run_tests`` / ``run_lint``: the exact command to run.
    """

    context: str
    """Relevant file content the planner read during investigation.

    For ``edit_file``: the section of the file being modified (so the
    executor can construct accurate search blocks without re-reading).

    For ``create_file``: content from related files showing patterns
    to follow.

    Empty string for ``run_tests`` / ``run_lint`` / ``format_code``.
    """


class VerificationPlan(BaseModel):
    """Verification steps to append after implementation."""

    steps: list[PlanStep]


class ExecutionPlan(BaseModel):
    """Complete structured plan for task execution."""

    scope: str
    """Brief summary of what the plan accomplishes and what is out of scope."""

    user_summary: str = ""
    """Plain-English description (up to ~1000 words) of what this plan will accomplish,
    the key architectural decisions made, why specific structures are being changed,
    and any design trade-offs. Written for the user to make an informed approval
    decision — covers: problem being solved, approach taken, what load-bearing
    structures are being touched and why."""

    naming_conventions: str = ""
    """Naming conventions observed in existing code: variable casing,
    function/method naming, class naming, file naming patterns,
    import styles.  Extracted from files read during exploration."""

    name_registry: str = ""
    """Canonical name mapping for every new entity introduced by this plan.

    Each entity lists its exact names across the stack: class/model name,
    namespace/module, import path, table/collection name, file path,
    route/endpoint, registration files, test file.  Populated by Phase 5
    and injected into every step's system prompt to prevent naming
    inconsistencies."""

    steps: list[PlanStep]
    """Ordered list of steps to execute.  Each step is one tool call."""

    tdd_test_steps: list[PlanStep] = []
    """Test steps to execute BEFORE implementation (TDD mode only).

    When TDD mode is enabled, these steps are separated from ``steps``
    and executed first by the expert model.  Empty when TDD is disabled
    — test steps remain inline in ``steps``."""

    affected_files: list[str]
    """All file paths that will be created or modified."""

    test_strategy: str
    """How to verify the changes work (included in run_tests steps)."""


def plan_to_markdown(
    plan: ExecutionPlan, *, include_context: bool = False
) -> str:
    """Render an ExecutionPlan as human-readable markdown.

    Args:
        plan: The execution plan to render.
        include_context: If True, append each step's context field as a
            fenced code block.  Used by Phase 6 so the verification model
            can see design details.  The approval UI passes False (default)
            to keep the output concise.
    """
    parts: list[str] = []

    parts.append(f"## Scope\n\n{plan.scope}\n")

    if plan.naming_conventions:
        parts.append(f"## Naming Conventions\n\n{plan.naming_conventions}\n")

    if plan.name_registry:
        parts.append(f"## Name Registry\n\n{plan.name_registry}\n")

    if plan.tdd_test_steps:
        parts.append("## TEST PHASE (Expert Model)\n")
        for step in plan.tdd_test_steps:
            tool_label = step.tool.upper().replace("_", " ")
            if step.file_path:
                parts.append(
                    f"{step.step_number}. **{tool_label}** `{step.file_path}`"
                    f" — {step.instruction}"
                )
            else:
                parts.append(
                    f"{step.step_number}. **{tool_label}** — {step.instruction}"
                )
            if include_context and step.context:
                parts.append(f"   ```\n{step.context}\n   ```")
        parts.append("")
        parts.append("## IMPLEMENTATION PHASE (Primary Model)\n")
    else:
        parts.append("## Steps\n")

    for step in plan.steps:
        tool_label = step.tool.upper().replace("_", " ")
        if step.file_path:
            parts.append(
                f"{step.step_number}. **{tool_label}** `{step.file_path}`"
                f" — {step.instruction}"
            )
        else:
            parts.append(
                f"{step.step_number}. **{tool_label}** — {step.instruction}"
            )
        if include_context and step.context:
            parts.append(f"   ```\n{step.context}\n   ```")
    parts.append("")

    parts.append("## Affected Files\n")
    for f in plan.affected_files:
        parts.append(f"- `{f}`")
    parts.append("")

    parts.append(f"## Test Strategy\n\n{plan.test_strategy}")

    return "\n".join(parts)

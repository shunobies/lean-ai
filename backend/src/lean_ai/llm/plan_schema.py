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


class ExecutionPlan(BaseModel):
    """Complete structured plan for task execution."""

    scope: str
    """Brief summary of what the plan accomplishes and what is out of scope."""

    steps: list[PlanStep]
    """Ordered list of steps to execute.  Each step is one tool call."""

    affected_files: list[str]
    """All file paths that will be created or modified."""

    test_strategy: str
    """How to verify the changes work (included in run_tests steps)."""


def plan_to_markdown(plan: ExecutionPlan) -> str:
    """Render an ExecutionPlan as human-readable markdown for the approval UI."""
    parts: list[str] = []

    parts.append(f"## Scope\n\n{plan.scope}\n")

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
    parts.append("")

    parts.append("## Affected Files\n")
    for f in plan.affected_files:
        parts.append(f"- `{f}`")
    parts.append("")

    parts.append(f"## Test Strategy\n\n{plan.test_strategy}")

    return "\n".join(parts)

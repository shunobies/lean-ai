"""Prompt builders for workflow execution and fix mode."""

from lean_ai.llm.plan_schema import PlanStep
from lean_ai.llm.prompts import FIX_SYSTEM_PROMPT, STEP_EXECUTION_SYSTEM_PROMPT


def build_fix_system_prompt(context: str) -> str:
    """Build the system prompt for fix mode (no planning)."""
    if not context:
        return FIX_SYSTEM_PROMPT

    max_context = 3000
    ctx = context[:max_context]
    if len(context) > max_context:
        ctx += "\n... (condensed)"

    return (
        f"{FIX_SYSTEM_PROMPT}\n"
        f"## Project Context\n\n{ctx}"
    )


def build_step_system_prompt(context: str) -> str:
    """Build the system prompt for per-step execution."""
    if not context:
        return STEP_EXECUTION_SYSTEM_PROMPT

    # Include condensed project context so the executor knows patterns
    max_context = 3000
    ctx = context[:max_context]
    if len(context) > max_context:
        ctx += "\n... (condensed)"

    return (
        f"{STEP_EXECUTION_SYSTEM_PROMPT}\n"
        f"## Project Context\n\n{ctx}"
    )


def build_step_user_message(
    step: PlanStep,
    completed: list[str],
    total_steps: int,
) -> str:
    """Build the user message for a specific step execution."""
    parts: list[str] = []

    # Progress header
    parts.append(
        f"STEP {step.step_number} OF {total_steps}"
    )

    if completed:
        parts.append("\nCompleted so far:")
        for desc in completed:
            parts.append(f"  ✓ {desc}")
        parts.append("")

    # Step details
    parts.append(f"Tool: {step.tool}")
    if step.file_path:
        parts.append(f"File: {step.file_path}")
    parts.append(f"Instruction: {step.instruction}")

    if step.context:
        parts.append(
            "\nContext (file content from planner investigation):"
            f"\n```\n{step.context}\n```"
        )

    # Explicit directive
    if step.tool in ("run_tests", "run_lint", "format_code"):
        parts.append(
            f"\nCall {step.tool} with the command specified in the instruction."
        )
    elif step.tool == "edit_file":
        parts.append(
            f"\nRead {step.file_path} first if the context above seems "
            "incomplete, then call edit_file with accurate search/replace blocks."
        )
    elif step.tool == "create_file":
        parts.append(
            f"\nCall create_file to create {step.file_path} with the content "
            "described in the instruction. Produce complete, working code."
        )

    return "\n".join(parts)

"""Prompt builders for workflow execution, fix mode, and request mode."""

from lean_ai.llm.plan_schema import PlanStep
from lean_ai.llm.prompts import (
    FIX_SYSTEM_PROMPT,
    REQUEST_SYSTEM_PROMPT,
    STEP_EXECUTION_SYSTEM_PROMPT,
)


def build_fix_system_prompt(
    context: str,
    test_command: str = "",
) -> str:
    """Build the system prompt for fix mode (no planning).

    When *test_command* is provided, the LLM is instructed to write or
    update tests alongside code changes.
    """
    base = FIX_SYSTEM_PROMPT

    if test_command:
        base += (
            "\n\nTEST REQUIREMENT:\n"
            "When you create new functionality or fix a bug, write or "
            "update tests alongside your changes.\n"
            "Cover all applicable categories:\n"
            "  HAPPY PATH  — primary use case works end-to-end\n"
            "  EDGE CASES  — None/empty/zero/boundary/unicode inputs\n"
            "  ERROR PATHS — wrong inputs raise the correct exception "
            "type; assert the message text, not just the type\n"
            "  INTEGRATION — mock external I/O and verify caller contracts\n"
            "  SECURITY    — if the code handles file paths, shell "
            "commands, user-supplied strings, or auth: add one test per "
            "attack surface (path traversal, injection, unauthed access)\n"
            "Follow project test patterns (class-based, pytest.raises, "
            "fixture reuse). Tests run with: "
            f"{test_command}"
        )

    if not context:
        return base

    max_context = 3000
    ctx = context[:max_context]
    if len(context) > max_context:
        ctx += "\n... (condensed)"

    return f"{base}\n## Project Context\n\n{ctx}"


def build_request_system_prompt(
    context: str,
) -> str:
    """Build the system prompt for request mode (neutral framing, no test requirement)."""
    base = REQUEST_SYSTEM_PROMPT

    if not context:
        return base

    max_context = 3000
    ctx = context[:max_context]
    if len(context) > max_context:
        ctx += "\n... (condensed)"

    return f"{base}\n## Project Context\n\n{ctx}"


def build_step_system_prompt(
    context: str,
    naming_conventions: str = "",
) -> str:
    """Build the system prompt for per-step execution."""
    prompt = STEP_EXECUTION_SYSTEM_PROMPT

    if context:
        # Include condensed project context so the executor knows patterns
        max_context = 3000
        ctx = context[:max_context]
        if len(context) > max_context:
            ctx += "\n... (condensed)"
        prompt += f"\n## Project Context\n\n{ctx}"

    if naming_conventions:
        prompt += (
            f"\n\n## Naming Conventions\n\n{naming_conventions}\n"
            "All new code MUST follow these conventions."
        )

    return prompt


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

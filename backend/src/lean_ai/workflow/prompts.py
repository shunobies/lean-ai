"""Prompt builders for workflow execution, fix mode, and request mode."""

from lean_ai.config import settings
from lean_ai.llm.plan_schema import PlanStep
from lean_ai.llm.prompts import (
    FIX_INVESTIGATION_PROMPT,
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
            "\n\nTEST REQUIREMENT: Write or update tests alongside changes. "
            "Cover: happy path, edge cases, error paths, integration, security. "
            f"Run with: {test_command}"
        )

    if not context:
        return base

    return f"{base}\n## Project Context\n\n{context}"


def build_fix_investigation_prompt(
    context: str,
    test_command: str = "",
) -> str:
    """Build the system prompt for the investigation phase of fix mode."""
    base = FIX_INVESTIGATION_PROMPT

    if test_command:
        base += f"\n\nAvailable test command: {test_command}"

    if not context:
        return base

    return f"{base}\n## Project Context\n\n{context}"


def build_request_system_prompt(
    context: str,
) -> str:
    """Build the system prompt for request mode (neutral framing, no test requirement)."""
    base = REQUEST_SYSTEM_PROMPT

    if not context:
        return base

    return f"{base}\n## Project Context\n\n{context}"


def build_step_system_prompt(
    context: str,
    naming_conventions: str = "",
    name_registry: str = "",
) -> str:
    """Build the system prompt for per-step execution."""
    prompt = STEP_EXECUTION_SYSTEM_PROMPT

    if context:
        prompt += f"\n## Project Context\n\n{context}"

    if naming_conventions:
        prompt += (
            f"\n\n## Naming Conventions\n\n{naming_conventions}\n"
            "All new code MUST follow these conventions."
        )

    if name_registry:
        prompt += (
            f"\n\n## Name Registry\n\n{name_registry}\n"
            "Use EXACTLY these names for all new entities. Do NOT invent "
            "alternative names, even if they seem more natural."
        )

    return prompt


def build_tdd_test_writing_prompt(
    context: str,
    implementation_plan_md: str,
    naming_conventions: str = "",
    name_registry: str = "",
) -> str:
    """Build the system prompt for TDD Phase A — expert writes tests.

    Phase A is invoked BEFORE any implementation code exists. The expert
    model must design tests from the implementation plan (which is
    embedded here so it has the full design context), use create_file to
    write the test files, and not attempt to run tests (they would fail
    — no implementation yet).

    Tools listed in the prompt match the actual tool list passed via the
    ``tools=`` parameter so the model does not hallucinate tool names.
    """
    prompt = build_step_system_prompt(context, naming_conventions, name_registry)
    prompt += (
        "\n\nTDD MODE — TEST WRITING PHASE:\n"
        "You are writing TESTS BEFORE the implementation exists. The "
        "implementation files referenced by these tests have NOT been "
        "created yet — that is intentional. Design tests from the "
        "IMPLEMENTATION PLAN below, not from existing source files.\n\n"
        "RULES FOR THIS PHASE:\n"
        "- Use create_file to write each new test file in full.\n"
        "- Use read_file / grep_files / list_directory ONLY to look at "
        "existing fixtures, conftest.py, or shared test utilities — "
        "NOT to find implementation code (it does not exist yet).\n"
        "- Do NOT call run_tests, run_lint, format_code, or run_command "
        "in this phase. Tests will fail because the implementation is "
        "missing — that is the point of TDD. The pipeline will run the "
        "tests after implementation completes.\n"
        "- Do NOT call edit_file unless extending an existing test file "
        "(e.g. adding cases to a shared conftest).\n"
        "- Each test must assert PUBLIC behavior described in the plan: "
        "function signatures, return values, raised exceptions. Do NOT "
        "test internal implementation details.\n"
        "- Include a module-level docstring and a per-test-function "
        "docstring explaining the contract being verified.\n\n"
        "IMPLEMENTATION PLAN (the design these tests must verify):\n"
        "```\n"
        f"{implementation_plan_md}\n"
        "```\n"
    )
    return prompt


def build_tdd_step_system_prompt(
    context: str,
    naming_conventions: str = "",
    name_registry: str = "",
) -> str:
    """Build the system prompt for TDD implementation steps (primary model).

    Extends the standard step prompt with TDD implementation constraints:
    test files are read-only, and the implementation must adapt to the
    tests rather than the reverse. Test disputes were already handled in
    the review phase (Phase B); they are not available here, so the
    prompt does not mention ``request_test_change``.
    """
    prompt = build_step_system_prompt(context, naming_conventions, name_registry)
    prompt += (
        "\n\nTDD MODE — IMPLEMENTATION PHASE:\n"
        "- Tests have already been written and reviewed. Implement code "
        "to make them pass.\n"
        "- Test files are LOCKED — any edit_file/create_file targeting a "
        "test file will be rejected.\n"
        "- If a test seems wrong, your only option is to find an "
        "implementation that satisfies it. Disputes are not available in "
        "this phase.\n"
        "- Read the relevant test file(s) first with read_file to "
        "understand the contract before writing implementation code.\n"
    )
    return prompt


def build_tdd_review_prompt(
    context: str,
    test_files: list[str],
) -> str:
    """Build the system prompt for the TDD test review phase.

    The primary model reviews the expert's tests before starting
    implementation and can dispute any that are flawed.
    """
    prompt = STEP_EXECUTION_SYSTEM_PROMPT

    if context:
        prompt += f"\n## Project Context\n\n{context}"

    prompt += (
        "\n\nTDD TEST REVIEW PHASE:\n"
        "Review the test files created by the expert model. Check:\n"
        "- Imports and module paths correct per the plan?\n"
        "- Assertions test public contracts (not private internals)?\n"
        "- No impossible preconditions or contradictory assertions?\n\n"
        "ADDITIONAL TOOL AVAILABLE IN THIS PHASE:\n"
        "  request_test_change(test_file, test_function, reason)\n"
        "    test_file:     path to the test file containing the flawed test\n"
        "    test_function: name of the specific test function being disputed\n"
        "    reason:        specific, programmatic reason — the failing assertion, "
        "the contract it violates, and what the test should assert instead, "
        "all in this single string.\n\n"
        "If all tests look correct, call task_complete.\n\n"
        "Test files to review:\n"
    )
    for tf in test_files:
        prompt += f"  - {tf}\n"

    return prompt


def _artifact_per_file_limit() -> int:
    """Scale per-file artifact limit with context window: 8000 at 128k, ~2800 at 32k."""
    return max(2000, min(8000, int(settings._active_context_window * 0.025 * 3.5)))


def build_step_user_message(
    step: PlanStep,
    completed: list[str],
    total_steps: int,
    step_artifacts: dict[str, str] | None = None,
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
    if step.reason:
        parts.append(f"Reason: {step.reason}")

    if step.context:
        ctx_text = step.context
        # At small context windows, truncate and instruct to read_file instead
        if settings._active_context_window <= 32768 and len(ctx_text) > 1000:
            ctx_text = ctx_text[:1000] + "\n... (truncated — call read_file before editing)"
        parts.append(
            "\nContext (file content from planner investigation):"
            f"\n```\n{ctx_text}\n```"
        )

    # Include relevant artifacts from previous steps
    if step_artifacts:
        relevant: dict[str, str] = {}
        searchable = (
            (step.instruction or "")
            + " " + (step.context or "")
            + " " + (step.file_path or "")
        )
        relevant.update({
            path: content for path, content in step_artifacts.items()
            if path in searchable
        })

        # Also include last 3 created files as fallback (catches
        # implicit dependencies like model ↔ migration)
        if len(relevant) < 3:
            for path, content in reversed(list(step_artifacts.items())):
                if path not in relevant and len(relevant) < 3:
                    relevant[path] = content

        if relevant:
            parts.append(
                "\nFiles from previous steps (use exact "
                "names/structure for consistency):"
            )
            for path, content in relevant.items():
                truncated = content[:_artifact_per_file_limit()]
                if len(content) > _artifact_per_file_limit():
                    truncated += "\n... (truncated)"
                parts.append(f"\n--- {path} ---\n```\n{truncated}\n```")

    # Explicit directive
    if step.tool in ("run_tests", "run_lint", "format_code", "run_command"):
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

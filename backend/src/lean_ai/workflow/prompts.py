"""Prompt builders for workflow execution, fix mode, and request mode."""

from lean_ai.config import settings
from lean_ai.llm.plan_schema import PlanStep
from lean_ai.llm.prompt_registry import PromptScope, registry
from lean_ai.llm.prompts import (
    FIX_INVESTIGATION_PROMPT,
    resolve_prompt_text,
)

# Layer 8 — tokens that suggest a task is a bug fix / regression
# hunt. Presence of any token (case-insensitive, word-boundary aware
# where applicable) biases the fix-mode prompt to demand a regression
# test in the regression-file convention.
_BUG_FIX_TOKENS: tuple[str, ...] = (
    "bug",
    "regression",
    "broken",
    "failing",
    "reproduce",
    "crash",
    "error",
    "issue #",
    "gh-",
    "fixes #",
)


def _looks_like_bug_fix(task: str) -> bool:
    """Best-effort detection of bug-fix / regression tasks.

    Cheap substring scan with lowered input — the false-positive cost
    is adding a regression test to a non-bug-fix task, which is
    harmless. The false-negative cost is missing a regression test for
    a bug fix, which is the failure mode we want to prevent.
    """
    if not task:
        return False
    lowered = task.lower()
    return any(token in lowered for token in _BUG_FIX_TOKENS)


def build_fix_system_prompt(
    context: str,
    test_command: str = "",
    task: str = "",
    *,
    repo_root: str | None = None,
    prompt_scope: PromptScope | None = None,
) -> str:
    """Build the system prompt for fix mode (no planning).

    When *test_command* is provided, the LLM is instructed to write or
    update tests alongside code changes.

    When *task* looks like a bug fix / regression hunt (Layer 8), the
    prompt also demands a regression test using the regression-file
    convention so the bug cannot silently return.
    """
    if repo_root:
        registry.load(repo_root)
    base = resolve_prompt_text("fix.system", scope=prompt_scope)

    if test_command:
        base += (
            "\n\nTEST REQUIREMENT: Write or update tests alongside changes. "
            "Cover: happy path, edge cases, error paths, integration, security. "
            f"Run with: {test_command}"
        )

        if _looks_like_bug_fix(task):
            base += (
                "\n\nREGRESSION TEST REQUIREMENT: This task looks like "
                "a bug fix. Before declaring the fix complete, add a "
                "REGRESSION test in the project's regression directory "
                "(e.g. tests/regression/) with a filename matching "
                "`regression_<short_slug>_test.<ext>` or placed under "
                "a `/regression/` folder. The test MUST:\n"
                "- Fail against the pre-fix code (use your knowledge of "
                "the bug's symptom to pick an assertion that the buggy "
                "code would not satisfy).\n"
                "- Pass against the post-fix code (run it after the fix "
                "lands to confirm).\n"
                "- Reference the original issue / PR / ticket in a "
                "header comment so a future reader knows what the test "
                "is guarding against.\n"
                "Once the plan completes, this file becomes IMMUTABLE — "
                "the tool executor will reject future edits. If a later "
                "fix breaks this regression test, the implementation is "
                "wrong, not the test."
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
    *,
    repo_root: str | None = None,
    prompt_scope: PromptScope | None = None,
) -> str:
    """Build the system prompt for request mode (neutral framing, no test requirement)."""
    if repo_root:
        registry.load(repo_root)
    base = resolve_prompt_text("fix.request_system", scope=prompt_scope)

    if not context:
        return base

    return f"{base}\n## Project Context\n\n{context}"


def build_step_system_prompt(
    context: str,
    naming_conventions: str = "",
    name_registry: str = "",
    *,
    repo_root: str | None = None,
    prompt_scope: PromptScope | None = None,
) -> str:
    """Build the system prompt for per-step execution."""
    if repo_root:
        registry.load(repo_root)
    prompt = resolve_prompt_text("execution.step_system", scope=prompt_scope)

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
    *,
    repo_root: str | None = None,
    prompt_scope: PromptScope | None = None,
) -> str:
    """Build the system prompt for TDD Phase A — expert writes tests.

    Phase A is invoked before implementation changes. The expert model
    designs tests from the implementation plan and current public
    interfaces, then the executor runs the configured red gate.

    Tools listed in the prompt match the actual tool list passed via the
    ``tools=`` parameter so the model does not hallucinate tool names.
    """
    prompt = build_step_system_prompt(
        context,
        naming_conventions,
        name_registry,
        repo_root=repo_root,
        prompt_scope=prompt_scope,
    )
    prompt += (
        "\n\nTDD MODE — TEST WRITING PHASE:\n"
        "You are writing TESTS BEFORE implementation changes are made. "
        "For an existing module, inspect its public interfaces and the "
        "repository's test conventions before writing tests. For a new "
        "module, derive its public contract from the IMPLEMENTATION PLAN "
        "below.\n\n"
        "WHAT TDD MEANS IN THIS PHASE:\n"
        "You are writing an executable specification of intended "
        "behaviour — not testing existing code. Imagine the "
        "implementation exists: what would it be called, what inputs "
        "would it take, what would it return, what errors would it "
        "raise? That answer is your test. Passing the tests will "
        'define what "correct" means for the implementor that runs '
        "after you.\n\n"
        "PUBLIC BEHAVIOUR vs. IMPLEMENTATION DETAILS:\n"
        "- Public (test these): return values, raised exceptions, "
        "side effects on injected dependencies (e.g. what gets "
        "written via a repository passed as an argument), and "
        "anything the plan explicitly calls a contract, guarantee, "
        "or success criterion.\n"
        "- Implementation (do NOT test): private methods "
        "(underscore-prefixed by Python convention, or marked private "
        "in the plan), internal data structures, the ordering of "
        "steps inside a function, whether a specific helper was "
        "called, intermediate values, exact log-line wording (unless "
        "the plan specifies it).\n\n"
        "CONCRETE EXAMPLE:\n"
        "    # GOOD — tests the plan's contract:\n"
        "    def test_parse_version_splits_dotted_numeric():\n"
        '        """parse_version(\'1.2.3\') returns (1, 2, 3) per '
        'plan spec."""\n'
        '        result = parse_version("1.2.3")\n'
        '        assert result == (1, 2, 3), f"expected (1,2,3), '
        'got {result}"\n\n'
        "    def test_parse_version_rejects_non_numeric():\n"
        '        """parse_version raises ValueError on \'abc\' per '
        'plan spec."""\n'
        '        with pytest.raises(ValueError, match="not a '
        'version"):\n'
        '            parse_version("abc")\n\n'
        "    # BAD — tests HOW it works, not WHAT it does:\n"
        "    def test_parse_version_uses_split():\n"
        '        assert parse_version("1.2.3")._parts == ["1", '
        '"2", "3"]\n\n'
        "    # BAD — inlines/stubs the implementation. Never do "
        "this:\n"
        "    def parse_version(s):\n"
        '        return tuple(int(p) for p in s.split("."))\n\n'
        "FAILURE MODES TO AVOID:\n"
        "1. Do NOT define or stub implementation code in the test "
        "file. Tests import from the planned implementation module.\n"
        "2. Do NOT test anything the plan does not explicitly "
        'specify. If the plan does not say "result must be sorted," '
        "do not assert sortedness. Extra assertions over-constrain "
        "the implementor for no reason.\n"
        "3. Do NOT test private helpers or internals. If the plan "
        "lists `parse_version` as public but `_tokenise` as an "
        "internal helper, write tests for `parse_version` only.\n"
        "4. Do NOT assert on exact log wording, call counts on "
        "internal helpers, or the order of steps inside a function "
        "unless the plan explicitly calls that out as a contract.\n"
        "5. If you cannot determine the public contract from the plan "
        "and existing interfaces, report the ambiguity as a blocker. "
        "Do not freeze a guessed contract in an ASSUMPTION comment.\n"
        "6. Do NOT change test count, AST shape, sync/async form, mock "
        "structure, or internal call counts merely to satisfy a structural "
        "check. Those details matter only when the plan explicitly defines "
        "them as public behavior.\n\n"
        "RULES FOR THIS PHASE:\n"
        "- Use create_file to write each new test file in full.\n"
        "- Use read_file / grep_files / list_directory to inspect existing "
        "public interfaces, fixtures, shared test utilities, and nearby "
        "tests. Do not copy private implementation logic into assertions.\n"
        "- Do NOT call run_tests, run_lint, format_code, or run_command "
        "in this phase. The executor runs the authored tests immediately "
        "after creation and requires them to fail relative to a clean "
        "pre-change baseline before implementation can begin.\n"
        "- Do NOT call edit_file unless extending an existing test file "
        "(e.g. adding cases to a shared conftest).\n"
        "- Each test must assert PUBLIC behaviour described in the plan: "
        "function signatures, return values, raised exceptions. Do NOT "
        "test internal implementation details.\n"
        "- Include a module-level docstring and a per-test-function "
        "docstring explaining the contract being verified.\n"
        "- Include descriptive assertion messages so failures "
        "immediately tell the implementor what contract was violated.\n\n"
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
    *,
    repo_root: str | None = None,
    prompt_scope: PromptScope | None = None,
) -> str:
    """Build the system prompt for TDD implementation steps (primary model).

    Extends the standard step prompt with TDD implementation constraints:
    test files are read-only and the implementation must adapt to the
    tests rather than the reverse.
    """
    prompt = build_step_system_prompt(
        context,
        naming_conventions,
        name_registry,
        repo_root=repo_root,
        prompt_scope=prompt_scope,
    )
    prompt += (
        "\n\nTDD MODE — IMPLEMENTATION PHASE:\n"
        "- Tests have already been written and validated by the red gate. Your default "
        "job is to implement code that makes them pass — adapt to the "
        "tests, not the other way round.\n"
        "- Test files are LOCKED — edit_file / create_file targeting a "
        "test file will be rejected. Never try to work around this by "
        "editing a different file that changes what the test loads.\n"
        "- Read the relevant test file(s) first with read_file to "
        "understand the contract before writing implementation code.\n"
        "- Every configured run_tests check must pass before the step is "
        "complete. Do not substitute syntax, AST, grep, or inspection "
        "checks for behavioral tests.\n"
        "- If a locked test contradicts the approved plan or cannot be "
        "satisfied through a correct implementation, report the exact "
        "contract conflict as a blocker instead of modifying the test.\n"
    )
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
    parts.append(f"STEP {step.step_number} OF {total_steps}")

    if completed:
        parts.append("\nCompleted so far:")
        for desc in completed:
            parts.append(f"  ✓ {desc}")
        parts.append("")

    # Step details
    parts.append(f"Job: {step.job or step.instruction}")
    if step.reason:
        parts.append(f"Why this job matters: {step.reason}")
    if step.inputs:
        parts.append("\nInputs available for this job:")
        for inp in step.inputs:
            detail = f" — {inp.details}" if inp.details else ""
            parts.append(f"- {inp.source}{detail}")
    if step.may_change:
        parts.append("\nYou MAY change only these targets:")
        for target in step.may_change:
            detail = f" — {target.change}" if target.change else ""
            parts.append(f"- {target.path}{detail}")
    elif step.file_path:
        parts.append(f"\nPrimary target: {step.file_path}")
    if step.must_not_change:
        parts.append("\nYou MUST NOT change:")
        for item in step.must_not_change:
            parts.append(f"- {item}")
    if step.allowed_tools:
        parts.append(f"\nAllowed tools for this job: {', '.join(step.allowed_tools)}")
    if step.output_shape:
        parts.append(f"\nRequired output shape:\n{step.output_shape}")
    if step.success_checks:
        parts.append("\nSuccess checks to satisfy before task_complete:")
        for check in step.success_checks:
            rendered = f"- {check.description}"
            if check.tool:
                rendered += f" [tool: {check.tool}]"
            if check.command:
                rendered += f" [command: {check.command}]"
            if check.expected:
                rendered += f" [expected: {check.expected}]"
            parts.append(rendered)
    if step.blocked_protocol:
        parts.append(f"\nIf blocked:\n{step.blocked_protocol}")
    if step.reason:
        parts.append("")

    # Include relevant artifacts from previous steps
    if step_artifacts:
        relevant: dict[str, str] = {}
        inputs_text = " ".join(
            f"{inp.source} {inp.details}".strip()
            for inp in step.inputs
            if inp.source or inp.details
        )
        searchable = (
            (step.job or "")
            + " "
            + (step.instruction or "")
            + " "
            + inputs_text
            + " "
            + (step.output_shape or "")
            + " "
            + " ".join(check.description for check in step.success_checks)
            + " "
            + (step.file_path or "")
        )
        relevant.update(
            {path: content for path, content in step_artifacts.items() if path in searchable}
        )

        # Also include last 3 created files as fallback (catches
        # implicit dependencies like model ↔ migration)
        if len(relevant) < 3:
            for path, content in reversed(list(step_artifacts.items())):
                if path not in relevant and len(relevant) < 3:
                    relevant[path] = content

        if relevant:
            parts.append("\nFiles from previous steps (use exact names/structure for consistency):")
            for path, content in relevant.items():
                truncated = content[: _artifact_per_file_limit()]
                if len(content) > _artifact_per_file_limit():
                    truncated += "\n... (truncated)"
                parts.append(f"\n--- {path} ---\n```\n{truncated}\n```")

    # Explicit directive
    parts.append(
        "\nUse the allowed tools to complete this bounded job. Stay inside "
        "`may_change`, preserve everything listed under `must_not_change`, "
        "satisfy the success checks, then call task_complete. Do not stop at "
        "prose unless the blocked protocol applies."
    )

    return "\n".join(parts)

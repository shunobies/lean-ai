"""Prompt builders for workflow execution, fix mode, and request mode."""

from lean_ai.config import settings
from lean_ai.llm.plan_schema import PlanStep
from lean_ai.llm.prompts import (
    FIX_INVESTIGATION_PROMPT,
    FIX_SYSTEM_PROMPT,
    REQUEST_SYSTEM_PROMPT,
    STEP_EXECUTION_SYSTEM_PROMPT,
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
) -> str:
    """Build the system prompt for fix mode (no planning).

    When *test_command* is provided, the LLM is instructed to write or
    update tests alongside code changes.

    When *task* looks like a bug fix / regression hunt (Layer 8), the
    prompt also demands a regression test using the regression-file
    convention so the bug cannot silently return.
    """
    base = FIX_SYSTEM_PROMPT

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
        "file. Tests import from the implementation module; the "
        "import will fail with ImportError until the implementor "
        "creates the module, and that is fine — the pipeline runs "
        "tests AFTER implementation.\n"
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
        "5. If you cannot tell what a test should assert from the "
        "plan alone, the plan is ambiguous — write the "
        "best-interpretation test and add a `# ASSUMPTION:` comment "
        "on the assert line so the review phase can dispute it if "
        "wrong.\n\n"
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
) -> str:
    """Build the system prompt for TDD implementation steps (primary model).

    Extends the standard step prompt with TDD implementation constraints:
    test files are read-only and the implementation must adapt to the
    tests rather than the reverse. Disputes are available as a narrow
    escape hatch (for tests that are logically impossible to satisfy or
    that encode a wrong contract) — they route through the expert via
    ``request_test_change`` and never edit tests directly.
    """
    prompt = build_step_system_prompt(context, naming_conventions, name_registry)
    prompt += (
        "\n\nTDD MODE — IMPLEMENTATION PHASE:\n"
        "- Tests have already been written and reviewed. Your default "
        "job is to implement code that makes them pass — adapt to the "
        "tests, not the other way round.\n"
        "- Test files are LOCKED — edit_file / create_file targeting a "
        "test file will be rejected. Never try to work around this by "
        "editing a different file that changes what the test loads.\n"
        "- Read the relevant test file(s) first with read_file to "
        "understand the contract before writing implementation code.\n"
        "\nTest Modification Policy (TDD):\n"
        "- Default: DO NOT dispute. If a test is hard to satisfy, that "
        "is usually a signal your implementation is wrong, not the "
        "test.\n"
        "- Legitimate dispute reasons (the only ones that should reach "
        "request_test_change):\n"
        "  1. The test is logically impossible to satisfy with any "
        "correct implementation (e.g. contradicts another test, "
        "references a function that cannot exist, asserts behaviour "
        "the language doesn't support).\n"
        "  2. The test encodes an old contract that the current task "
        "explicitly changes — you must cite the task description "
        "section that requires the contract change.\n"
        "  3. The test is over-constrained on an internal detail the "
        "contract does not require (e.g. asserts exact log wording, "
        "pins a private method signature). Propose a narrower "
        "assertion.\n"
        "- How to dispute: call request_test_change(test_file, "
        "test_function, reason). Your reason must be one short "
        "paragraph with the specific technical justification — "
        '"this test is wrong" will be rejected. The expert evaluates '
        "and either edits the test or rejects the dispute with an "
        "implementation hint.\n"
        "- Regression tests are IMMUTABLE even via dispute. If a "
        "regression test is genuinely broken, cancel the session and "
        "open a new one with /fix.\n"
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
        "Review the test files created by the expert model. You are the "
        "last gate before implementation begins — a flawed test here "
        "forces the implementor into a corner they cannot code their "
        "way out of, so catching defects now saves wasted implementation "
        "effort later.\n\n"
        "WHAT A CORRECT TDD TEST LOOKS LIKE:\n"
        "- Imports the not-yet-existing implementation module (the "
        "import will fail right now — that is expected; the pipeline "
        "runs these tests AFTER implementation).\n"
        "- Asserts ONLY on public behaviour the plan specifies: return "
        "values, raised exceptions, or side effects on injected "
        "dependencies.\n"
        "- Uses a descriptive assert message so the implementor "
        "immediately knows what contract was violated when the test "
        "fails.\n"
        "- Has a docstring explaining the behaviour being tested and "
        "why it matters.\n"
        "- Does NOT define or stub the implementation inline.\n"
        "- Does NOT assert on private helpers, internal data "
        "structures, step ordering inside a function, or exact log "
        "wording (unless the plan explicitly calls that out).\n\n"
        "WHEN TO DISPUTE (call request_test_change):\n"
        "- The test asserts on an implementation detail instead of a "
        "contract — e.g. `assert obj._internal_cache == ...` or "
        "`assert helper_spy.call_count == 3`.\n"
        "- The import path does not match the plan's module "
        "structure.\n"
        "- The test file defines or stubs implementation code inline.\n"
        "- The test's precondition is impossible given the plan — "
        "e.g. asserts a function returns a list when the plan says it "
        "returns a generator.\n"
        "- The assertion contradicts the plan — e.g. plan says "
        '"raises ValueError on empty input," test asserts `None` is '
        "returned.\n"
        "- The test is missing docstrings or assertion messages that "
        "would tell the implementor what the contract is.\n\n"
        "DO NOT DISPUTE MERELY BECAUSE:\n"
        "- The test is hard to make pass — that is the point of TDD. "
        "Hard ≠ defective.\n"
        "- You would have written the test differently — stylistic "
        "differences are not defects.\n"
        "- You prefer a different assertion, name, or fixture style.\n"
        "- The test seems overly strict — if it matches the plan's "
        "contract, it is correct.\n\n"
        "ADDITIONAL TOOL AVAILABLE IN THIS PHASE:\n"
        "  request_test_change(test_file, test_function, reason)\n"
        "    test_file:     path to the test file containing the flawed test\n"
        "    test_function: name of the specific test function being disputed\n"
        "    reason:        specific, programmatic reason — the failing assertion, "
        "the contract it violates, and what the test should assert instead, "
        "all in this single string.\n\n"
        "Read each test file with read_file before deciding. If all "
        "tests look correct, call task_complete.\n\n"
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
    parts.append(f"STEP {step.step_number} OF {total_steps}")

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
        parts.append(f"\nContext (file content from planner investigation):\n```\n{ctx_text}\n```")

    # Include relevant artifacts from previous steps
    if step_artifacts:
        relevant: dict[str, str] = {}
        searchable = (
            (step.instruction or "") + " " + (step.context or "") + " " + (step.file_path or "")
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
    if step.tool in ("run_tests", "run_lint", "format_code", "run_command"):
        parts.append(f"\nCall {step.tool} with the command specified in the instruction.")
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

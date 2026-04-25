"""Structured plan schema for plan-driven execution.

The planner produces an ExecutionPlan where each step maps to one tool call.
The executor iterates through steps, feeding each to a constrained LLM
that translates the detailed instruction into a single tool invocation.
"""

import logging
from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

logger = logging.getLogger(__name__)


# ── Phase 1 scope schema ────────────────────────────────────────────────────
#
# ScopeDocument is the validated output of Phase 1 — produced by a final
# chat_structured synthesis pass that coerces the exploration-loop prose into
# the 8 required sections. Schema enforcement prevents the model from
# shortcutting to "ask clarifying questions" or skipping sections; every field
# is required. Rendered to markdown by format_scope_document so Phase 2/3/4
# keep their historical ``{scope}`` contract unchanged.


class ScopeAssumption(BaseModel):
    """One assumption the scope records with a falsifiable verification hint."""

    assumption: str
    """Short statement of what is being assumed."""

    verify_hint: str
    """Concrete hint Phase 2 can act on to confirm or falsify it (e.g.
    'grep celery in pyproject.toml', 'read app/models/user.py')."""


class ScopeDocument(BaseModel):
    """Validated Phase 1 output — the 8 required scope sections."""

    problem: str
    """3-6 sentences restating the task and WHY it matters."""

    deliverables: list[str] = []
    """Observable outcomes (Users can X / Endpoint Y returns Z)."""

    in_scope: list[str] = []
    """Concrete, greppable entities being created or modified — file paths,
    class names, function names, routes, tables, env vars."""

    out_of_scope: list[str] = []
    """Tempting-adjacent areas explicitly excluded."""

    downstream_consumers: list[str] = []
    """Categories of files that reference modified entities — controllers,
    tests, configs, migrations, etc."""

    assumptions: list[ScopeAssumption] = []
    """Every assumption paired with a falsifiable verification hint."""

    success_criteria: list[str] = []
    """3-6 falsifiable conditions Phase 5 can target for verification."""

    risks: list[str] = []
    """Scope-level risks — misunderstandings about the problem itself.
    Distinct from implementation risks (Phase 3 captures those)."""


# ── Phase 2 exploration schemas ─────────────────────────────────────────────
#
# FileObservation is written incrementally by the request model via the
# record_file_observation tool during Phase 2 exploration. FileSummary is
# produced by a final chat_structured synthesis pass that merges the
# observations, scratchpad, and journal into a validated shape that is then
# rendered to markdown and fed to downstream phases as {file_summary}.

FileRole = Literal["modify", "create", "reference", "missing"]
AssumptionOutcome = Literal["confirmed", "falsified", "unable_to_verify"]


class FileObservation(BaseModel):
    """One file the exploration model decided is relevant to the task."""

    file_path: str
    """Repo-relative path."""

    role: FileRole
    """Why this file matters: modify (changes needed), create (new file to
    write), reference (read for context / pattern), missing (expected but
    absent — should be in missing_infrastructure too)."""

    reason: str
    """One-line explanation of why this file is relevant."""

    relevant_sections: str = ""
    """Line ranges + brief description of the sections that matter."""

    key_snippets: list[str] = []
    """Short quoted excerpts (15-25 lines each) the planner should keep in
    hand for design and implementation."""


class MissingItem(BaseModel):
    """Infrastructure the task assumes but that was not found."""

    name: str
    reason: str
    blocking: bool = False


class VerifiedReference(BaseModel):
    """External dependency verified via web search during exploration."""

    dependency: str
    docs_url: str
    version: str = ""
    confirmed_patterns: str = ""


class AssumptionStatus(BaseModel):
    """Outcome of processing one ASSUMPTION from the Phase 1 scope checklist."""

    assumption: str
    """Echoed from the scope's ASSUMPTIONS section."""

    status: AssumptionOutcome
    """Result of running the verification hint."""

    evidence: str = ""
    """What the model found (e.g. 'grep for celery in pyproject.toml: no match')."""


class ExistingCoverage(BaseModel):
    """Per-source-file record of existing test coverage observed in the
    repo. Populated by Phase 2 so Phase 5 can avoid re-testing behavior
    that is already covered."""

    source_file: str
    """Repo-relative path of the source file under consideration."""

    test_files: list[str] = []
    """Repo-relative paths of test files that already exercise
    ``source_file``. Empty when coverage is missing."""

    coverage_notes: str = ""
    """Short prose on what the existing tests cover and what's still
    uncovered — used by Phase 5 to decide whether to add or skip."""


class TestingInventory(BaseModel):
    """Phase 2's test-infrastructure inventory, consumed by Phase 5.

    Gives Phase 5 structured knowledge of the project's test framework
    and existing coverage without needing its own tool budget.
    """

    test_framework: str = ""
    """e.g. ``pytest``, ``jest``, ``go test``, ``rspec``, ``junit``.
    Empty when Phase 2 could not confidently detect a framework."""

    test_directory: str = ""
    """e.g. ``tests/``, ``__tests__/``, ``spec/``."""

    test_file_pattern: str = ""
    """Filename pattern e.g. ``test_*.py``, ``*.spec.ts``, ``*_test.go``."""

    assertion_style_excerpt: str = ""
    """Short literal excerpt from an existing test (imports,
    setup/teardown, a representative assertion) so Phase 5 can mirror
    the project's style. Empty when the repo has no tests yet."""

    existing_regression_files: list[str] = []
    """Repo-relative paths matching the regression-file convention
    (see ``settings.regression_file_pattern``). These are IMMUTABLE —
    Phase 5 may reference them but never plan edits to them."""

    affected_files_existing_coverage: list[ExistingCoverage] = []
    """Per-affected-file coverage record so Phase 5 can skip already-
    covered behavior and focus on uncovered code paths."""

    notes: str = ""
    """Anything else Phase 5 should know about the test infrastructure
    — e.g. 'integration tests live under tests/integration and require
    a running Postgres'."""


class FileSummary(BaseModel):
    """Validated Phase 2 output — produced by the synthesis pass."""

    files_to_modify: list[FileObservation] = []
    files_to_create: list[FileObservation] = []
    files_read_for_context: list[FileObservation] = []
    missing_infrastructure: list[MissingItem] = []
    verified_references: list[VerifiedReference] = []
    assumptions_resolved: list[AssumptionStatus] = []
    testing_inventory: TestingInventory | None = None
    """Phase 2's test-infrastructure inventory (Layer 6). ``None`` when
    the project has no test footprint or Phase 2 (e.g. parallel path)
    did not produce one. Phase 5 renders this into its user prompt so
    the LLM can target existing coverage gaps precisely."""

    notes: str = ""
    """Free-form catch-all for cross-file references, tricky invariants, or
    anything the structured fields do not capture."""


# ── Phase 3 design + risk schemas ───────────────────────────────────────────
#
# DesignAndRisks is produced by a chat_structured synthesis pass at the end
# of Phase 3. Its fields replace the prior free-form 3-section text output
# and eliminate the secondary _extract_missing_files LLM call (missing_files
# becomes a direct field on the object).

NamingCategory = Literal[
    "variables",
    "functions",
    "classes",
    "files",
    "routes",
    "db_table",
    "db_column",
    "imports",
]
RiskSeverity = Literal["low", "medium", "high"]


class NamingConvention(BaseModel):
    """One naming pattern observed in the existing codebase."""

    category: NamingCategory
    pattern: str
    """The convention itself — e.g. 'snake_case' or 'UPPER_SNAKE_CASE'."""

    source_file: str
    """Repo-relative path of a file exemplifying the pattern, or the literal
    string 'standard framework conventions' when no codebase example applies."""


class ChangeDesign(BaseModel):
    """Design decisions for ONE non-obvious file."""

    file_path: str
    decisions: str
    """3-8 lines of prose on non-obvious choices: complex DB schemas,
    non-trivial method signatures, multi-component wiring, pattern
    deviations. Skip for straightforward files (simple CRUD, basic
    models, standard config)."""


class MissingFile(BaseModel):
    """A file that is required at runtime but absent from the plan."""

    file_path: str
    purpose: str
    blocking: bool = False


class DependencyOrder(BaseModel):
    """An ordering constraint between plan files."""

    file_path: str
    depends_on: str
    reason: str


class CriticalRisk(BaseModel):
    """A scope-level risk the plan must consciously account for."""

    risk: str
    severity: RiskSeverity
    mitigation: str


CoreFunctionalitySignal = Literal[
    "phase1_deliverable",
    "critical_risk_adjacent",
    "public_api",
    "downstream_consumer",
    "user_designated",
]
CoreFunctionalityConfidence = Literal["high", "medium", "low"]


class CoreFunctionalityTag(BaseModel):
    """One entity flagged as load-bearing core functionality (Layer 9).

    Phase 3 produces these tags based on deterministic signals; Phase 4
    propagates them into the ExecutionPlan so Phase 5 knows which
    entities MUST receive a regression test (as opposed to a regular
    test). The user may prune/add tags during plan approval.
    """

    entity: str
    """Function / class / module / route / CLI-command name. Short
    and greppable so Phase 5 can reference it in test steps."""

    file_path: str
    """Repo-relative path of the file containing the entity."""

    reason: str
    """Short prose on why the entity is core — what breaks if it
    regresses. Used by Phase 5 to write the regression test's
    description and by the approval UI to explain the tag."""

    source_signal: CoreFunctionalitySignal
    """How Phase 3 inferred the tag: ``phase1_deliverable`` (matches
    a Phase 1 deliverable), ``critical_risk_adjacent`` (co-located
    with a high-severity risk), ``public_api`` (exported / route /
    CLI surface), ``downstream_consumer`` (Phase 1 downstream
    consumer depends on it), ``user_designated`` (added via the
    approval UI)."""

    confidence: CoreFunctionalityConfidence = "medium"
    """``high`` / ``medium`` / ``low``. Phase 5 mandates regression
    coverage for confidence ≥ ``settings.core_functionality_min_confidence``."""


class DesignAndRisks(BaseModel):
    """Validated Phase 3 output — produced by the synthesis pass."""

    naming_conventions: list[NamingConvention] = []
    change_designs: list[ChangeDesign] = []
    missing_files: list[MissingFile] = []
    dependency_order: list[DependencyOrder] = []
    critical_risks: list[CriticalRisk] = []
    citations: list[VerifiedReference] = []
    """External dependencies the expert verified during Phase 3. Rendered
    alongside Phase 2's VERIFIED REFERENCES at the Phase 4 boundary (dedupe
    by docs_url)."""

    core_functionality: list[CoreFunctionalityTag] = []
    """Load-bearing entities that Phase 5 MUST guard with regression
    tests (Layer 9). Populated by Phase 3's detection rules; propagated
    into the ExecutionPlan by Phase 4. Empty when Phase 3 found no
    entities worth tagging or the feature flag is disabled."""

    notes: str = ""
    """Free-form catch-all for architectural invariants or edge cases that
    do not fit the structured fields."""


# ── Phase 4 plan assembly schemas ───────────────────────────────────────────
#
# NameRegistryEntry is one canonical-name row per NEW entity introduced by
# the plan. ExecutionPlan carries naming_conventions and name_registry as
# typed lists rather than free-form text so post-generation validation can
# reason over them and the assembly prompt can shrink.


class NameRegistryEntry(BaseModel):
    """Canonical names for ONE new entity introduced by the plan.

    Populated by Phase 4. Injected into every step's system prompt during
    execution via ``format_name_registry_for_prompt`` to prevent naming
    drift across files. Only ``entity`` is required — every other field
    defaults to empty and is only populated when applicable to the kind of
    entity this row represents (e.g. a plain data class has no route).
    """

    entity: str
    """Human-readable entity name (e.g. 'User Profile Page')."""

    model_class: str = ""
    """Exact class or type name (e.g. 'UserProfilePage')."""

    module_namespace: str = ""
    """Dotted module path (e.g. 'app.pages.user_profile')."""

    import_stmt: str = ""
    """Literal import statement other files should use."""

    db_table: str = ""
    """Table or collection name, if applicable."""

    file_path: str = ""
    """Repo-relative path to the file defining this entity."""

    route_endpoint: str = ""
    """HTTP route / endpoint, if applicable."""

    registered_in: list[str] = []
    """Files where this entity must be registered. Each entry here should
    have a corresponding ``edit_file`` step in the plan."""

    test_file: str = ""
    """Test file path, if applicable."""


# Tools valid in implementation plan steps (Phase 4 output).
# Includes read_file (executor reads before editing), run_tests, run_lint,
# format_code — only truly non-plan tools (list_directory, directory_tree,
# grep_files, search_internet, etc.) are filtered out.
IMPLEMENTATION_STEP_TOOLS = {
    "create_file",
    "edit_file",
    "run_command",
    "read_file",
    "run_tests",
    "run_lint",
    "format_code",
}

# Alias — all tools that may appear in any PlanStep.
ALL_VALID_STEP_TOOLS = IMPLEMENTATION_STEP_TOOLS


class PlanStep(BaseModel):
    """One discrete step in the execution plan.

    Each step maps to roughly one tool call.  The ``instruction`` field
    is detailed enough that a constrained LLM can translate it into the
    exact tool invocation without exploring the codebase.
    """

    step_number: int
    tool: str
    """Tool to call: ``create_file``, ``edit_file``, ``run_command``,
    ``run_tests``, ``run_lint``, ``format_code``."""

    @field_validator("tool")
    @classmethod
    def warn_non_standard_tool(cls, v: str) -> str:
        if v not in ALL_VALID_STEP_TOOLS:
            logger.warning(
                "PlanStep has non-standard tool '%s' — expected one of %s",
                v,
                ALL_VALID_STEP_TOOLS,
            )
        return v

    file_path: str
    """Target file path (relative to repo root).
    Empty string for ``run_command`` / ``run_tests`` / ``run_lint`` /
    ``format_code``."""

    instruction: str
    """Detailed natural-language instruction for this step.

    For ``edit_file``: which section of the file to modify, what to find,
    what to replace it with, line references, patterns to follow.

    For ``create_file``: what the file should contain, imports, structure,
    patterns to follow from existing files.

    For ``run_command``: the exact command to run and why.

    For ``run_tests`` / ``run_lint``: the exact command to run.
    """

    @field_validator("instruction")
    @classmethod
    def instruction_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("PlanStep instruction must not be empty")
        return v

    reason: str = ""
    """Why this change is needed — the problem it solves or the requirement
    it satisfies.  The executor uses this to make informed decisions when the
    exact instruction doesn't match the current file state."""

    context: str
    """Relevant file content the planner read during investigation.

    For ``edit_file``: the section of the file being modified (so the
    executor can construct accurate search blocks without re-reading).

    For ``create_file``: content from related files showing patterns
    to follow.

    Empty string for ``run_command`` / ``run_tests`` / ``run_lint`` /
    ``format_code``.
    """

    _FILE_TOOLS = frozenset({"create_file", "edit_file", "read_file"})

    @model_validator(mode="after")
    def warn_missing_file_path(self) -> "PlanStep":
        if self.tool in self._FILE_TOOLS and not self.file_path.strip():
            logger.warning(
                "PlanStep tool '%s' should have a file_path but got empty string (step %d)",
                self.tool,
                self.step_number,
            )
        return self


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

    naming_conventions: list[NamingConvention] = []
    """Naming conventions observed in existing code. Populated by Phase 4
    as a typed list (category / pattern / source_file). Rendered to text
    via ``format_naming_conventions_for_prompt`` when injected into
    step-execution system prompts."""

    name_registry: list[NameRegistryEntry] = []
    """Canonical name mapping for every NEW entity introduced by this plan.

    Each entry carries the entity's names across the stack (class,
    namespace, import path, table, file, route, registration files,
    test file). Populated by Phase 4 and rendered to text via
    ``format_name_registry_for_prompt`` when injected into per-step
    system prompts to prevent naming drift."""

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

    plan_validation_warnings: list[str] = []
    """Non-blocking warnings from post-generation plan validation
    (hallucinated paths, uncovered missing files, edit/create mismatches,
    etc.). Populated by the Phase 4 validators and surfaced on the
    extension approval screen so users can see them alongside the plan.
    Empty when the plan validated cleanly."""

    core_functionality: list[CoreFunctionalityTag] = []
    """Load-bearing entities Phase 5 must guard with regression tests
    (Layer 9). Copied from ``DesignAndRisks.core_functionality`` during
    Phase 4 synthesis. Rendered into the approval payload so users can
    prune/add before approval. Empty when Phase 3 found no entities or
    the feature flag is disabled."""


def format_naming_conventions_for_prompt(
    conventions: list[NamingConvention],
) -> str:
    """Render naming conventions as a prompt-friendly markdown table.

    Returns empty string when the list is empty so callers can skip the
    section cleanly.
    """
    if not conventions:
        return ""
    lines = ["| category | pattern | source_file |", "|---|---|---|"]
    for nc in conventions:
        lines.append(f"| {nc.category} | {nc.pattern} | {nc.source_file} |")
    return "\n".join(lines)


def format_name_registry_for_prompt(
    entries: list[NameRegistryEntry],
) -> str:
    """Render the name registry in the text shape per-step prompts expect.

    Matches the pre-structured template used by ``build_step_system_prompt``:

        Entity "<Name>":
          model/class: <...>
          namespace/module: <...>
          import: <...>
          ...

    Rows are included only when their field is populated — entities
    without a route or DB table simply omit those lines. Returns empty
    string when the list is empty.
    """
    if not entries:
        return ""
    blocks: list[str] = []
    for entry in entries:
        block = [f'Entity "{entry.entity}":']
        if entry.model_class:
            block.append(f"  model/class: {entry.model_class}")
        if entry.module_namespace:
            block.append(f"  namespace/module: {entry.module_namespace}")
        if entry.import_stmt:
            block.append(f"  import: {entry.import_stmt}")
        if entry.db_table:
            block.append(f"  table/collection: {entry.db_table}")
        if entry.file_path:
            block.append(f"  file: {entry.file_path}")
        if entry.route_endpoint:
            block.append(f"  route/endpoint: {entry.route_endpoint}")
        if entry.registered_in:
            block.append(f"  registered in: {', '.join(entry.registered_in)}")
        if entry.test_file:
            block.append(f"  test: {entry.test_file}")
        blocks.append("\n".join(block))
    return "\n\n".join(blocks)


def _render_step(parts: list[str], step: PlanStep, include_context: bool) -> None:
    """Render a single plan step as markdown lines."""
    tool_label = step.tool.upper().replace("_", " ")
    if step.file_path:
        parts.append(
            f"{step.step_number}. **{tool_label}** `{step.file_path}` — {step.instruction}"
        )
    else:
        parts.append(f"{step.step_number}. **{tool_label}** — {step.instruction}")
    if step.reason:
        parts.append(f"   **Reason:** {step.reason}")
    if include_context and step.context:
        parts.append(f"   ```\n{step.context}\n   ```")


def plan_to_markdown(plan: ExecutionPlan, *, include_context: bool = False) -> str:
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

    naming_text = format_naming_conventions_for_prompt(plan.naming_conventions)
    if naming_text:
        parts.append(f"## Naming Conventions\n\n{naming_text}\n")

    registry_text = format_name_registry_for_prompt(plan.name_registry)
    if registry_text:
        parts.append(f"## Name Registry\n\n{registry_text}\n")

    if plan.tdd_test_steps:
        parts.append("## TEST PHASE (Expert Model)\n")
        for step in plan.tdd_test_steps:
            _render_step(parts, step, include_context)
        parts.append("")
        parts.append("## IMPLEMENTATION PHASE (Primary Model)\n")
    else:
        parts.append("## Steps\n")

    for step in plan.steps:
        _render_step(parts, step, include_context)
    parts.append("")

    parts.append("## Affected Files\n")
    for f in plan.affected_files:
        parts.append(f"- `{f}`")
    parts.append("")

    parts.append(f"## Test Strategy\n\n{plan.test_strategy}")

    return "\n".join(parts)

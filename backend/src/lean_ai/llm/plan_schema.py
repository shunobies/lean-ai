"""Structured plan schema for plan-driven execution.

The planner produces an ExecutionPlan where each step is a bounded job
contract: what to do, which inputs to use, what may and may not change,
which tools may be used, what output shape is required, how success is
checked, and what to do when blocked.
"""

import logging
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

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

    def to_markdown(self) -> str:
        """Render the scope document to the 8-section markdown shape consumed
        by downstream Phase 2/3/4 prompts.

        Empty sections emit the heading with a placeholder bullet so
        downstream parsers see the shape. Assumptions use the special
        ``- Assumption: ... — verify: ...`` format.
        """
        lines: list[str] = []

        def _render_bullets(header: str, items: list[str]) -> None:
            lines.append(f"{header}:")
            if items:
                for item in items:
                    lines.append(f"- {item}")
            else:
                lines.append("- (none identified)")
            lines.append("")

        lines.append("PROBLEM / PURPOSE:")
        lines.append(self.problem.strip() or "(not specified)")
        lines.append("")

        _render_bullets("DELIVERABLES", self.deliverables)
        _render_bullets("IN SCOPE", self.in_scope)
        _render_bullets("OUT OF SCOPE", self.out_of_scope)
        _render_bullets("DOWNSTREAM CONSUMERS", self.downstream_consumers)

        lines.append("ASSUMPTIONS (with verification hints):")
        if self.assumptions:
            for a in self.assumptions:
                lines.append(f"- Assumption: {a.assumption} — verify: {a.verify_hint}")
        else:
            lines.append("- (none identified)")
        lines.append("")

        _render_bullets("SUCCESS CRITERIA", self.success_criteria)
        _render_bullets("RISKS", self.risks)

        return "\n".join(lines).rstrip() + "\n"


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


class WebReference(BaseModel):
    """External dependency verified via web search during exploration.

    Mirrors VerifiedReference so Phase 2 web-research findings can be
    captured through a distinct type while sharing the same shape.
    """

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
    """Phase 2's test-infrastructure inventory, consumed by TDD planning.

    Gives the Phase 4 TDD planner structured knowledge of the project's
    test framework and existing coverage without needing its own tool
    budget.
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
    """Per-affected-file coverage record so Phase 4 TDD planning can skip already-
    covered behavior and focus on uncovered code paths."""

    strategy_summary: str = ""
    """Compact prose summary of the repo's testing strategy, conventions,
    and constraints for direct prompt injection into Phase 4."""

    notes: str = ""
    """Anything else Phase 4 TDD planning should know about the test infrastructure
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
    did not produce one. Phase 4 renders this into its TDD-aware prompts
    so the LLM can target existing coverage gaps precisely."""

    notes: str = ""
    """Free-form catch-all for cross-file references, tricky invariants, or
    anything the structured fields do not capture."""

    def to_markdown(self) -> str:
        """Render the file summary to the 8-section markdown shape consumed
        by downstream Phase 3/4/5 prompts.

        Empty sections emit the heading with a placeholder bullet so
        downstream parsers see the shape. TestingInventory None case
        is handled gracefully with a fallback message.
        """
        lines: list[str] = []

        def _render_file_observations(header: str, items: list[FileObservation]) -> None:
            lines.append(f"{header}:")
            if items:
                for obs in items:
                    lines.append(f"- **{obs.file_path}** ({obs.role})")
                    lines.append(f"  - Reason: {obs.reason}")
                    if obs.relevant_sections:
                        lines.append(f"  - Relevant sections: {obs.relevant_sections}")
            else:
                lines.append("- (none identified)")
            lines.append("")

        def _render_missing_items(header: str, items: list[MissingItem]) -> None:
            lines.append(f"{header}:")
            if items:
                for item in items:
                    blocking_str = "BLOCKING" if item.blocking else "non-blocking"
                    lines.append(f"- **{item.name}** ({blocking_str})")
                    lines.append(f"  - Reason: {item.reason}")
            else:
                lines.append("- (none identified)")
            lines.append("")

        def _render_verified_refs(header: str, items: list[VerifiedReference]) -> None:
            lines.append(f"{header}:")
            if items:
                for ref in items:
                    lines.append(f"- **{ref.dependency}**")
                    if ref.docs_url:
                        lines.append(f"  - Docs: {ref.docs_url}")
                    if ref.version:
                        lines.append(f"  - Version: {ref.version}")
            else:
                lines.append("- (none identified)")
            lines.append("")

        def _render_assumptions(header: str, items: list[AssumptionStatus]) -> None:
            lines.append(f"{header}:")
            if items:
                for a in items:
                    lines.append(f"- **{a.assumption}** — status: {a.status}")
                    if a.evidence:
                        lines.append(f"  - Evidence: {a.evidence}")
            else:
                lines.append("- (none identified)")
            lines.append("")

        # Section 1: Files to Modify
        _render_file_observations("FILES TO MODIFY", self.files_to_modify)

        # Section 2: Files to Create
        _render_file_observations("FILES TO CREATE", self.files_to_create)

        # Section 3: Files Read for Context
        _render_file_observations("FILES READ FOR CONTEXT", self.files_read_for_context)

        # Section 4: Missing Infrastructure
        _render_missing_items("MISSING INFRASTRUCTURE", self.missing_infrastructure)

        # Section 5: Verified References
        _render_verified_refs("VERIFIED REFERENCES", self.verified_references)

        # Section 6: Assumptions Resolved
        _render_assumptions("ASSUMPTIONS RESOLVED", self.assumptions_resolved)

        # Section 7: Testing Inventory (handle None)
        lines.append("TESTING INVENTORY:")
        if self.testing_inventory is not None:
            ti = self.testing_inventory
            if ti.test_framework:
                lines.append(f"- Framework: {ti.test_framework}")
            if ti.test_directory:
                lines.append(f"- Test directory: {ti.test_directory}")
            if ti.test_file_pattern:
                lines.append(f"- File pattern: {ti.test_file_pattern}")
            if ti.assertion_style_excerpt:
                lines.append(f"- Assertion style excerpt: {ti.assertion_style_excerpt}")
            if ti.existing_regression_files:
                lines.append("- Existing regression files:")
                for rf in ti.existing_regression_files:
                    lines.append(f"  - {rf}")
            if ti.affected_files_existing_coverage:
                lines.append("- Affected files coverage:")
                for cov in ti.affected_files_existing_coverage:
                    lines.append(f"  - Source: {cov.source_file}, Tests: {cov.test_files}")
                    if cov.coverage_notes:
                        lines.append(f"    - Notes: {cov.coverage_notes}")
            if ti.strategy_summary:
                lines.append(f"- Strategy: {ti.strategy_summary}")
            if ti.notes:
                lines.append(f"- Notes: {ti.notes}")
            if not any([
                ti.test_framework, ti.test_directory, ti.test_file_pattern,
                ti.assertion_style_excerpt, ti.existing_regression_files,
                ti.affected_files_existing_coverage, ti.strategy_summary, ti.notes,
            ]):
                lines.append("- (none identified)")
        else:
            lines.append("- (none identified)")
        lines.append("")

        # Section 8: Notes
        lines.append("NOTES:")
        if self.notes:
            lines.append(self.notes.strip())
        else:
            lines.append("- (none identified)")
        lines.append("")

        return "\n".join(lines).rstrip() + "\n"


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


# Canonical primary tools for implementation plan steps (Phase 4 output).
# Read/search helpers may still appear in ``allowed_tools``; this set is used
# only for the legacy ``tool`` field and for filtering out pure non-step noise.
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

# Safe non-mutating helpers every implementation step may use even when the
# planner does not spell them out explicitly. This keeps step execution
# flexible without widening the mutation boundary.
DEFAULT_ALLOWED_READ_ONLY_STEP_TOOLS = (
    "read_file",
    "list_directory",
    "directory_tree",
    "grep_files",
    "query_project_context",
    "search_reference",
    "search_internet",
    "fetch_url",
)


def _dedupe_tool_names(*groups: list[str] | tuple[str, ...]) -> list[str]:
    """Merge tool names while preserving their first-seen order."""

    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for name in group:
            if not name or name in seen:
                continue
            merged.append(name)
            seen.add(name)
    return merged


class StepInput(BaseModel):
    """One piece of context the implementation model may rely on."""

    source: str
    """Repo-relative path, prior-step artifact, test command, external docs URL,
    or concise named input such as ``Phase 3 risk: auth bypass``."""

    details: str = ""
    """Brief details the executor needs from this input. Prefer facts,
    signatures, invariants, or line ranges over pasted implementation code."""


class StepChangeTarget(BaseModel):
    """One file or repo surface the step is allowed to mutate."""

    path: str
    """Repo-relative file path or scoped target such as ``package metadata``."""

    change: str = ""
    """The kind of change allowed at this target."""


class StepSuccessCheck(BaseModel):
    """A concrete check the executor should use before completing a step."""

    description: str
    """What must be true when this step is complete."""

    tool: str = ""
    """Optional tool that should be used to check success, e.g. ``run_tests``,
    ``run_lint``, ``run_command``, or ``read_file``. Empty means the check is
    judged from the completed work and task_complete summary."""

    command: str = ""
    """Exact command when ``tool`` is command-like."""

    expected: str = ""
    """Expected result or observable signal."""


class StepResult(BaseModel):
    """Result of executing a single plan step, including incomplete tracking.

    Used by the TDD retry mechanism to record why a step was marked
    incomplete after exhausting retries, allowing execution to continue.
    """

    step_number: int
    """The step this result corresponds to."""

    success: bool = True
    """Whether the step completed successfully."""

    incomplete_reason: str | None = None
    """Explanation when the step was marked incomplete after retries."""


class PlanStep(BaseModel):
    """One bounded job contract in the execution plan.

    The canonical fields describe a small implementation job, its inputs,
    mutation boundary, available tools, required output shape, success checks,
    and blocked protocol. Legacy ``tool`` / ``file_path`` / ``instruction``
    fields remain as compatibility hints for older plans and UI surfaces, but
    new plans should not rely on the one-step/one-tool recipe model.
    """

    step_number: int

    job: str = ""
    """What job this step is doing."""

    inputs: list[StepInput] = Field(default_factory=list)
    """Inputs available to the implementation model for this step."""

    may_change: list[StepChangeTarget] = Field(default_factory=list)
    """Files or scoped surfaces this step may mutate."""

    must_not_change: list[str] = Field(default_factory=list)
    """Files, tests, APIs, or behaviors this step must not mutate."""

    allowed_tools: list[str] = Field(default_factory=list)
    """Tools the implementation model may use while completing this job."""

    output_shape: str = ""
    """Required shape of the completed work, including any tests or contracts
    that should exist after the job."""

    success_checks: list[StepSuccessCheck] = Field(default_factory=list)
    """Checks the executor should satisfy before calling task_complete."""

    blocked_protocol: str = ""
    """What the implementation model should do when blocked."""

    # Compatibility hints for older stored plans and UI/progress surfaces.
    tool: str = ""
    """Legacy primary-tool hint. Prefer ``allowed_tools`` in new plans."""

    @field_validator("tool")
    @classmethod
    def warn_non_standard_tool(cls, v: str) -> str:
        if v and v not in ALL_VALID_STEP_TOOLS:
            logger.warning(
                "PlanStep has non-standard tool '%s' — expected one of %s",
                v,
                ALL_VALID_STEP_TOOLS,
            )
        return v

    file_path: str = ""
    """Legacy primary-file hint. Prefer ``may_change`` in new plans."""

    instruction: str = ""
    """Legacy instruction hint. Prefer ``job`` + ``output_shape`` in new plans."""

    reason: str = ""
    """Why this job is needed."""

    _FILE_TOOLS = frozenset({"create_file", "edit_file", "read_file"})
    _MUTATING_TOOLS = frozenset({"create_file", "edit_file", "run_command", "format_code"})

    @model_validator(mode="after")
    def normalize_contract(self) -> "PlanStep":
        if not self.job.strip() and self.instruction.strip():
            self.job = self.instruction.strip()
        if not self.instruction.strip() and self.job.strip():
            self.instruction = self.job.strip()
        if not self.job.strip() and not self.instruction.strip():
            raise ValueError("PlanStep instruction must not be empty unless job is provided")

        if self.file_path.strip() and not self.may_change and self.tool in self._MUTATING_TOOLS:
            self.may_change.append(
                StepChangeTarget(
                    path=self.file_path.strip(),
                    change=(self.reason or self.instruction or self.job).strip(),
                )
            )
        if not self.file_path.strip() and self.may_change:
            self.file_path = self.may_change[0].path

        allowed_tools = [name for name in self.allowed_tools if name]
        if not allowed_tools:
            if self.tool:
                allowed_tools = [self.tool]
            else:
                allowed_tools = ["edit_file", "create_file"]
        if self.tool:
            allowed_tools = _dedupe_tool_names([self.tool], allowed_tools)
        self.allowed_tools = _dedupe_tool_names(
            allowed_tools,
            DEFAULT_ALLOWED_READ_ONLY_STEP_TOOLS,
            ("task_complete",),
        )
        if not self.tool:
            for candidate in (
                "edit_file",
                "create_file",
                "run_command",
                "run_tests",
                "run_lint",
                "format_code",
                "read_file",
            ):
                if candidate in self.allowed_tools:
                    self.tool = candidate
                    break

        if not self.output_shape.strip():
            self.output_shape = "Complete the job described by this step and leave the workspace in a verifiable state."
        if not self.blocked_protocol.strip():
            self.blocked_protocol = (
                "Use read-only tools to gather missing context. If still blocked, "
                "record the blocker clearly and call task_complete with what is incomplete."
            )

        if self.tool in self._FILE_TOOLS and not self.file_path.strip():
            logger.warning(
                "PlanStep tool '%s' should have a file_path but got empty string (step %d)",
                self.tool,
                self.step_number,
            )
        return self


class VerificationPlan(BaseModel):
    """Legacy verification-step container.

    Phase 4 now carries verification requirements in each step's
    ``success_checks``. This model remains for older debug artifacts and
    targeted compatibility tests.
    """

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

    tdd_mode: bool = False
    """Whether this plan should execute in TDD mode.

    When true, ``tdd_test_steps`` must run before ``steps`` and the
    executor applies test-file immutability during implementation.
    """

    tdd_test_steps: list[PlanStep] = []
    """Pre-implementation TDD test steps.

    These are designed in Phase 4b and executed before the
    implementation steps in ``steps``. Implementation verification still
    belongs in per-step ``success_checks``.
    """

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


def _render_step(parts: list[str], step: PlanStep) -> None:
    """Render a single plan step as markdown lines."""
    tool_label = ", ".join(step.allowed_tools) if step.allowed_tools else step.tool
    target_label = ""
    if step.may_change:
        target_label = " " + ", ".join(f"`{target.path}`" for target in step.may_change)
    elif step.file_path:
        target_label = f" `{step.file_path}`"
    parts.append(f"{step.step_number}. **Job:** {step.job}{target_label}")
    if step.reason:
        parts.append(f"   **Why:** {step.reason}")
    if tool_label:
        parts.append(f"   **Tools:** {tool_label}")
    if step.inputs:
        rendered_inputs = "; ".join(
            f"{inp.source}: {inp.details}" if inp.details else inp.source for inp in step.inputs
        )
        parts.append(f"   **Inputs:** {rendered_inputs}")
    if step.may_change:
        rendered_changes = "; ".join(
            f"{target.path}: {target.change}" if target.change else target.path
            for target in step.may_change
        )
        parts.append(f"   **May change:** {rendered_changes}")
    if step.must_not_change:
        parts.append(f"   **Must not change:** {', '.join(step.must_not_change)}")
    if step.output_shape:
        parts.append(f"   **Output:** {step.output_shape}")
    if step.success_checks:
        checks = "; ".join(
            check.description
            + (f" ({check.tool}: {check.command})" if check.tool and check.command else "")
            for check in step.success_checks
        )
        parts.append(f"   **Success checks:** {checks}")
    if step.blocked_protocol:
        parts.append(f"   **If blocked:** {step.blocked_protocol}")


def plan_to_markdown(plan: ExecutionPlan) -> str:
    """Render an ExecutionPlan as human-readable markdown."""
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
            _render_step(parts, step)
        parts.append("")
        parts.append("## IMPLEMENTATION PHASE (Primary Model)\n")
    else:
        parts.append("## Steps\n")

    for step in plan.steps:
        _render_step(parts, step)
    parts.append("")

    parts.append("## Affected Files\n")
    for f in plan.affected_files:
        parts.append(f"- `{f}`")
    parts.append("")

    parts.append(f"## Test Strategy\n\n{plan.test_strategy}")

    return "\n".join(parts)

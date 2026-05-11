commit c4c822e07ffd7e29758173430a4bfd9134ee47f2
Author: Alex Autrey <shunobies@gmail.com>
Date:   Sun May 10 18:59:34 2026 -0500

    The planning pipeline has 12 identified data loss points. Fix ALL of them. This is a correctness-critical change — the planning pipeline is the core product. Every fix must preserve existing behavior for the non-affected paths.

diff --git a/extension/package-lock.json b/extension/package-lock.json
index 39cee1c..de8211e 100644
--- a/extension/package-lock.json
+++ b/extension/package-lock.json
@@ -1,12 +1,12 @@
 {
   "name": "lean-ai",
-  "version": "0.20.1",
+  "version": "0.20.2",
   "lockfileVersion": 3,
   "requires": true,
   "packages": {
     "": {
       "name": "lean-ai",
-      "version": "0.20.1",
+      "version": "0.20.2",
       "dependencies": {
         "ws": "^8.18.0"
       },
diff --git a/extension/package.json b/extension/package.json
index 75b0f3a..7e2a627 100755
--- a/extension/package.json
+++ b/extension/package.json
@@ -2,7 +2,7 @@
   "name": "lean-ai",
   "displayName": "Lean AI",
   "description": "Multi-provider LLM agentic coding assistant with phase-based planning, chat & inline predictions",
-  "version": "0.20.1",
+  "version": "0.20.2",
   "publisher": "lean-ai",
   "icon": "icon.png",
   "repository": {
diff --git a/jetbrains-plugin/gradle.properties b/jetbrains-plugin/gradle.properties
index b8b419b..f3c6bb7 100644
--- a/jetbrains-plugin/gradle.properties
+++ b/jetbrains-plugin/gradle.properties
@@ -1,7 +1,7 @@
 # IntelliJ Platform Plugin Configuration
 pluginGroup = com.leanai.plugin
 pluginName = Lean AI
-pluginVersion = 0.20.1
+pluginVersion = 0.20.2
 pluginSinceBuild = 241
 # Omit pluginUntilBuild for forward compatibility
 

commit 4804bae88c62495f30380962d539c60467981519
Author: Alex Autrey <shunobies@gmail.com>
Date:   Sun May 10 18:52:15 2026 -0500

    lean-ai: ## Task: Fix Data Loss in Lean AI Planning Pipeline  The planning pipeli
    
    Files modified: backend/src/lean_ai/llm/plan_schema.py, backend/src/lean_ai/llm/planner.py, backend/src/lean_ai/llm/planner_exploration.py, backend/src/lean_ai/llm/planner_helpers.py, backend/src/lean_ai/llm/prompt_defaults.py, backend/src/lean_ai/llm/tool_definitions.py, backend/src/lean_ai/workflow/executor.py, backend/tests/unit/test_phase5_coverage_validator.py
    
    Co-authored-by: LeanAI-bot <leanai@timcomp.com>

diff --git a/backend/src/lean_ai/llm/plan_schema.py b/backend/src/lean_ai/llm/plan_schema.py
index e13cc82..83484e7 100644
--- a/backend/src/lean_ai/llm/plan_schema.py
+++ b/backend/src/lean_ai/llm/plan_schema.py
@@ -5,387 +5,400 @@ contract: what to do, which inputs to use, what may and may not change,
 which tools may be used, what output shape is required, how success is
 checked, and what to do when blocked.
 """
-
-import logging
-from typing import Literal
-
+
+import logging
+from typing import Literal
+
 from pydantic import BaseModel, Field, field_validator, model_validator
-
-logger = logging.getLogger(__name__)
-
-
-# ── Phase 1 scope schema ────────────────────────────────────────────────────
-#
-# ScopeDocument is the validated output of Phase 1 — produced by a final
-# chat_structured synthesis pass that coerces the exploration-loop prose into
-# the 8 required sections. Schema enforcement prevents the model from
-# shortcutting to "ask clarifying questions" or skipping sections; every field
-# is required. Rendered to markdown by format_scope_document so Phase 2/3/4
-# keep their historical ``{scope}`` contract unchanged.
-
-
-class ScopeAssumption(BaseModel):
-    """One assumption the scope records with a falsifiable verification hint."""
-
-    assumption: str
-    """Short statement of what is being assumed."""
-
-    verify_hint: str
-    """Concrete hint Phase 2 can act on to confirm or falsify it (e.g.
-    'grep celery in pyproject.toml', 'read app/models/user.py')."""
-
-
-class ScopeDocument(BaseModel):
-    """Validated Phase 1 output — the 8 required scope sections."""
-
-    problem: str
-    """3-6 sentences restating the task and WHY it matters."""
-
-    deliverables: list[str] = []
-    """Observable outcomes (Users can X / Endpoint Y returns Z)."""
-
-    in_scope: list[str] = []
-    """Concrete, greppable entities being created or modified — file paths,
-    class names, function names, routes, tables, env vars."""
-
-    out_of_scope: list[str] = []
-    """Tempting-adjacent areas explicitly excluded."""
-
-    downstream_consumers: list[str] = []
-    """Categories of files that reference modified entities — controllers,
-    tests, configs, migrations, etc."""
-
-    assumptions: list[ScopeAssumption] = []
-    """Every assumption paired with a falsifiable verification hint."""
-
-    success_criteria: list[str] = []
-    """3-6 falsifiable conditions Phase 5 can target for verification."""
-
-    risks: list[str] = []
-    """Scope-level risks — misunderstandings about the problem itself.
-    Distinct from implementation risks (Phase 3 captures those)."""
-
-
-# ── Phase 2 exploration schemas ─────────────────────────────────────────────
-#
-# FileObservation is written incrementally by the request model via the
-# record_file_observation tool during Phase 2 exploration. FileSummary is
-# produced by a final chat_structured synthesis pass that merges the
-# observations, scratchpad, and journal into a validated shape that is then
-# rendered to markdown and fed to downstream phases as {file_summary}.
-
-FileRole = Literal["modify", "create", "reference", "missing"]
-AssumptionOutcome = Literal["confirmed", "falsified", "unable_to_verify"]
-
-
-class FileObservation(BaseModel):
-    """One file the exploration model decided is relevant to the task."""
-
-    file_path: str
-    """Repo-relative path."""
-
-    role: FileRole
-    """Why this file matters: modify (changes needed), create (new file to
-    write), reference (read for context / pattern), missing (expected but
-    absent — should be in missing_infrastructure too)."""
-
-    reason: str
-    """One-line explanation of why this file is relevant."""
-
-    relevant_sections: str = ""
-    """Line ranges + brief description of the sections that matter."""
-
-    key_snippets: list[str] = []
-    """Short quoted excerpts (15-25 lines each) the planner should keep in
-    hand for design and implementation."""
-
-
-class MissingItem(BaseModel):
-    """Infrastructure the task assumes but that was not found."""
-
-    name: str
-    reason: str
-    blocking: bool = False
-
-
-class VerifiedReference(BaseModel):
-    """External dependency verified via web search during exploration."""
-
-    dependency: str
-    docs_url: str
-    version: str = ""
-    confirmed_patterns: str = ""
-
-
-class AssumptionStatus(BaseModel):
-    """Outcome of processing one ASSUMPTION from the Phase 1 scope checklist."""
-
-    assumption: str
-    """Echoed from the scope's ASSUMPTIONS section."""
-
-    status: AssumptionOutcome
-    """Result of running the verification hint."""
-
-    evidence: str = ""
-    """What the model found (e.g. 'grep for celery in pyproject.toml: no match')."""
-
-
-class ExistingCoverage(BaseModel):
-    """Per-source-file record of existing test coverage observed in the
-    repo. Populated by Phase 2 so Phase 5 can avoid re-testing behavior
-    that is already covered."""
-
-    source_file: str
-    """Repo-relative path of the source file under consideration."""
-
-    test_files: list[str] = []
-    """Repo-relative paths of test files that already exercise
-    ``source_file``. Empty when coverage is missing."""
-
-    coverage_notes: str = ""
-    """Short prose on what the existing tests cover and what's still
-    uncovered — used by Phase 5 to decide whether to add or skip."""
-
-
-class TestingInventory(BaseModel):
-    """Phase 2's test-infrastructure inventory, consumed by Phase 5.
-
-    Gives Phase 5 structured knowledge of the project's test framework
-    and existing coverage without needing its own tool budget.
-    """
-
-    test_framework: str = ""
-    """e.g. ``pytest``, ``jest``, ``go test``, ``rspec``, ``junit``.
-    Empty when Phase 2 could not confidently detect a framework."""
-
-    test_directory: str = ""
-    """e.g. ``tests/``, ``__tests__/``, ``spec/``."""
-
-    test_file_pattern: str = ""
-    """Filename pattern e.g. ``test_*.py``, ``*.spec.ts``, ``*_test.go``."""
-
-    assertion_style_excerpt: str = ""
-    """Short literal excerpt from an existing test (imports,
-    setup/teardown, a representative assertion) so Phase 5 can mirror
-    the project's style. Empty when the repo has no tests yet."""
-
-    existing_regression_files: list[str] = []
-    """Repo-relative paths matching the regression-file convention
-    (see ``settings.regression_file_pattern``). These are IMMUTABLE —
-    Phase 5 may reference them but never plan edits to them."""
-
-    affected_files_existing_coverage: list[ExistingCoverage] = []
-    """Per-affected-file coverage record so Phase 5 can skip already-
-    covered behavior and focus on uncovered code paths."""
-
-    notes: str = ""
-    """Anything else Phase 5 should know about the test infrastructure
-    — e.g. 'integration tests live under tests/integration and require
-    a running Postgres'."""
-
-
-class FileSummary(BaseModel):
-    """Validated Phase 2 output — produced by the synthesis pass."""
-
-    files_to_modify: list[FileObservation] = []
-    files_to_create: list[FileObservation] = []
-    files_read_for_context: list[FileObservation] = []
-    missing_infrastructure: list[MissingItem] = []
-    verified_references: list[VerifiedReference] = []
-    assumptions_resolved: list[AssumptionStatus] = []
-    testing_inventory: TestingInventory | None = None
-    """Phase 2's test-infrastructure inventory (Layer 6). ``None`` when
-    the project has no test footprint or Phase 2 (e.g. parallel path)
-    did not produce one. Phase 5 renders this into its user prompt so
-    the LLM can target existing coverage gaps precisely."""
-
-    notes: str = ""
-    """Free-form catch-all for cross-file references, tricky invariants, or
-    anything the structured fields do not capture."""
-
-
-# ── Phase 3 design + risk schemas ───────────────────────────────────────────
-#
-# DesignAndRisks is produced by a chat_structured synthesis pass at the end
-# of Phase 3. Its fields replace the prior free-form 3-section text output
-# and eliminate the secondary _extract_missing_files LLM call (missing_files
-# becomes a direct field on the object).
-
-NamingCategory = Literal[
-    "variables",
-    "functions",
-    "classes",
-    "files",
-    "routes",
-    "db_table",
-    "db_column",
-    "imports",
-]
-RiskSeverity = Literal["low", "medium", "high"]
-
-
-class NamingConvention(BaseModel):
-    """One naming pattern observed in the existing codebase."""
-
-    category: NamingCategory
-    pattern: str
-    """The convention itself — e.g. 'snake_case' or 'UPPER_SNAKE_CASE'."""
-
-    source_file: str
-    """Repo-relative path of a file exemplifying the pattern, or the literal
-    string 'standard framework conventions' when no codebase example applies."""
-
-
-class ChangeDesign(BaseModel):
-    """Design decisions for ONE non-obvious file."""
-
-    file_path: str
-    decisions: str
-    """3-8 lines of prose on non-obvious choices: complex DB schemas,
-    non-trivial method signatures, multi-component wiring, pattern
-    deviations. Skip for straightforward files (simple CRUD, basic
-    models, standard config)."""
-
-
-class MissingFile(BaseModel):
-    """A file that is required at runtime but absent from the plan."""
-
-    file_path: str
-    purpose: str
-    blocking: bool = False
-
-
-class DependencyOrder(BaseModel):
-    """An ordering constraint between plan files."""
-
-    file_path: str
-    depends_on: str
-    reason: str
-
-
-class CriticalRisk(BaseModel):
-    """A scope-level risk the plan must consciously account for."""
-
-    risk: str
-    severity: RiskSeverity
-    mitigation: str
-
-
-CoreFunctionalitySignal = Literal[
-    "phase1_deliverable",
-    "critical_risk_adjacent",
-    "public_api",
-    "downstream_consumer",
-    "user_designated",
-]
-CoreFunctionalityConfidence = Literal["high", "medium", "low"]
-
-
-class CoreFunctionalityTag(BaseModel):
-    """One entity flagged as load-bearing core functionality (Layer 9).
-
-    Phase 3 produces these tags based on deterministic signals; Phase 4
-    propagates them into the ExecutionPlan so Phase 5 knows which
-    entities MUST receive a regression test (as opposed to a regular
-    test). The user may prune/add tags during plan approval.
-    """
-
-    entity: str
-    """Function / class / module / route / CLI-command name. Short
-    and greppable so Phase 5 can reference it in test steps."""
-
-    file_path: str
-    """Repo-relative path of the file containing the entity."""
-
-    reason: str
-    """Short prose on why the entity is core — what breaks if it
-    regresses. Used by Phase 5 to write the regression test's
-    description and by the approval UI to explain the tag."""
-
-    source_signal: CoreFunctionalitySignal
-    """How Phase 3 inferred the tag: ``phase1_deliverable`` (matches
-    a Phase 1 deliverable), ``critical_risk_adjacent`` (co-located
-    with a high-severity risk), ``public_api`` (exported / route /
-    CLI surface), ``downstream_consumer`` (Phase 1 downstream
-    consumer depends on it), ``user_designated`` (added via the
-    approval UI)."""
-
-    confidence: CoreFunctionalityConfidence = "medium"
-    """``high`` / ``medium`` / ``low``. Phase 5 mandates regression
-    coverage for confidence ≥ ``settings.core_functionality_min_confidence``."""
-
-
-class DesignAndRisks(BaseModel):
-    """Validated Phase 3 output — produced by the synthesis pass."""
-
-    naming_conventions: list[NamingConvention] = []
-    change_designs: list[ChangeDesign] = []
-    missing_files: list[MissingFile] = []
-    dependency_order: list[DependencyOrder] = []
-    critical_risks: list[CriticalRisk] = []
-    citations: list[VerifiedReference] = []
-    """External dependencies the expert verified during Phase 3. Rendered
-    alongside Phase 2's VERIFIED REFERENCES at the Phase 4 boundary (dedupe
-    by docs_url)."""
-
-    core_functionality: list[CoreFunctionalityTag] = []
-    """Load-bearing entities that Phase 5 MUST guard with regression
-    tests (Layer 9). Populated by Phase 3's detection rules; propagated
-    into the ExecutionPlan by Phase 4. Empty when Phase 3 found no
-    entities worth tagging or the feature flag is disabled."""
-
-    notes: str = ""
-    """Free-form catch-all for architectural invariants or edge cases that
-    do not fit the structured fields."""
-
-
-# ── Phase 4 plan assembly schemas ───────────────────────────────────────────
-#
-# NameRegistryEntry is one canonical-name row per NEW entity introduced by
-# the plan. ExecutionPlan carries naming_conventions and name_registry as
-# typed lists rather than free-form text so post-generation validation can
-# reason over them and the assembly prompt can shrink.
-
-
-class NameRegistryEntry(BaseModel):
-    """Canonical names for ONE new entity introduced by the plan.
-
-    Populated by Phase 4. Injected into every step's system prompt during
-    execution via ``format_name_registry_for_prompt`` to prevent naming
-    drift across files. Only ``entity`` is required — every other field
-    defaults to empty and is only populated when applicable to the kind of
-    entity this row represents (e.g. a plain data class has no route).
-    """
-
-    entity: str
-    """Human-readable entity name (e.g. 'User Profile Page')."""
-
-    model_class: str = ""
-    """Exact class or type name (e.g. 'UserProfilePage')."""
-
-    module_namespace: str = ""
-    """Dotted module path (e.g. 'app.pages.user_profile')."""
-
-    import_stmt: str = ""
-    """Literal import statement other files should use."""
-
-    db_table: str = ""
-    """Table or collection name, if applicable."""
-
-    file_path: str = ""
-    """Repo-relative path to the file defining this entity."""
-
-    route_endpoint: str = ""
-    """HTTP route / endpoint, if applicable."""
-
-    registered_in: list[str] = []
-    """Files where this entity must be registered. Each entry here should
-    have a corresponding ``edit_file`` step in the plan."""
-
-    test_file: str = ""
-    """Test file path, if applicable."""
-
-
+
+logger = logging.getLogger(__name__)
+
+
+# ── Phase 1 scope schema ────────────────────────────────────────────────────
+#
+# ScopeDocument is the validated output of Phase 1 — produced by a final
+# chat_structured synthesis pass that coerces the exploration-loop prose into
+# the 8 required sections. Schema enforcement prevents the model from
+# shortcutting to "ask clarifying questions" or skipping sections; every field
+# is required. Rendered to markdown by format_scope_document so Phase 2/3/4
+# keep their historical ``{scope}`` contract unchanged.
+
+
+class ScopeAssumption(BaseModel):
+    """One assumption the scope records with a falsifiable verification hint."""
+
+    assumption: str
+    """Short statement of what is being assumed."""
+
+    verify_hint: str
+    """Concrete hint Phase 2 can act on to confirm or falsify it (e.g.
+    'grep celery in pyproject.toml', 'read app/models/user.py')."""
+
+
+class ScopeDocument(BaseModel):
+    """Validated Phase 1 output — the 8 required scope sections."""
+
+    problem: str
+    """3-6 sentences restating the task and WHY it matters."""
+
+    deliverables: list[str] = []
+    """Observable outcomes (Users can X / Endpoint Y returns Z)."""
+
+    in_scope: list[str] = []
+    """Concrete, greppable entities being created or modified — file paths,
+    class names, function names, routes, tables, env vars."""
+
+    out_of_scope: list[str] = []
+    """Tempting-adjacent areas explicitly excluded."""
+
+    downstream_consumers: list[str] = []
+    """Categories of files that reference modified entities — controllers,
+    tests, configs, migrations, etc."""
+
+    assumptions: list[ScopeAssumption] = []
+    """Every assumption paired with a falsifiable verification hint."""
+
+    success_criteria: list[str] = []
+    """3-6 falsifiable conditions Phase 5 can target for verification."""
+
+    risks: list[str] = []
+    """Scope-level risks — misunderstandings about the problem itself.
+    Distinct from implementation risks (Phase 3 captures those)."""
+
+
+# ── Phase 2 exploration schemas ─────────────────────────────────────────────
+#
+# FileObservation is written incrementally by the request model via the
+# record_file_observation tool during Phase 2 exploration. FileSummary is
+# produced by a final chat_structured synthesis pass that merges the
+# observations, scratchpad, and journal into a validated shape that is then
+# rendered to markdown and fed to downstream phases as {file_summary}.
+
+FileRole = Literal["modify", "create", "reference", "missing"]
+AssumptionOutcome = Literal["confirmed", "falsified", "unable_to_verify"]
+
+
+class FileObservation(BaseModel):
+    """One file the exploration model decided is relevant to the task."""
+
+    file_path: str
+    """Repo-relative path."""
+
+    role: FileRole
+    """Why this file matters: modify (changes needed), create (new file to
+    write), reference (read for context / pattern), missing (expected but
+    absent — should be in missing_infrastructure too)."""
+
+    reason: str
+    """One-line explanation of why this file is relevant."""
+
+    relevant_sections: str = ""
+    """Line ranges + brief description of the sections that matter."""
+
+    key_snippets: list[str] = []
+    """Short quoted excerpts (15-25 lines each) the planner should keep in
+    hand for design and implementation."""
+
+
+class MissingItem(BaseModel):
+    """Infrastructure the task assumes but that was not found."""
+
+    name: str
+    reason: str
+    blocking: bool = False
+
+
+class VerifiedReference(BaseModel):
+    """External dependency verified via web search during exploration."""
+
+    dependency: str
+    docs_url: str
+    version: str = ""
+    confirmed_patterns: str = ""
+
+
+class WebReference(BaseModel):
+    """External dependency verified via web search during exploration.
+
+    Mirrors VerifiedReference so Phase 2 web-research findings can be
+    captured through a distinct type while sharing the same shape.
+    """
+
+    dependency: str
+    docs_url: str
+    version: str = ""
+    confirmed_patterns: str = ""
+
+
+class AssumptionStatus(BaseModel):
+    """Outcome of processing one ASSUMPTION from the Phase 1 scope checklist."""
+
+    assumption: str
+    """Echoed from the scope's ASSUMPTIONS section."""
+
+    status: AssumptionOutcome
+    """Result of running the verification hint."""
+
+    evidence: str = ""
+    """What the model found (e.g. 'grep for celery in pyproject.toml: no match')."""
+
+
+class ExistingCoverage(BaseModel):
+    """Per-source-file record of existing test coverage observed in the
+    repo. Populated by Phase 2 so Phase 5 can avoid re-testing behavior
+    that is already covered."""
+
+    source_file: str
+    """Repo-relative path of the source file under consideration."""
+
+    test_files: list[str] = []
+    """Repo-relative paths of test files that already exercise
+    ``source_file``. Empty when coverage is missing."""
+
+    coverage_notes: str = ""
+    """Short prose on what the existing tests cover and what's still
+    uncovered — used by Phase 5 to decide whether to add or skip."""
+
+
+class TestingInventory(BaseModel):
+    """Phase 2's test-infrastructure inventory, consumed by Phase 5.
+
+    Gives Phase 5 structured knowledge of the project's test framework
+    and existing coverage without needing its own tool budget.
+    """
+
+    test_framework: str = ""
+    """e.g. ``pytest``, ``jest``, ``go test``, ``rspec``, ``junit``.
+    Empty when Phase 2 could not confidently detect a framework."""
+
+    test_directory: str = ""
+    """e.g. ``tests/``, ``__tests__/``, ``spec/``."""
+
+    test_file_pattern: str = ""
+    """Filename pattern e.g. ``test_*.py``, ``*.spec.ts``, ``*_test.go``."""
+
+    assertion_style_excerpt: str = ""
+    """Short literal excerpt from an existing test (imports,
+    setup/teardown, a representative assertion) so Phase 5 can mirror
+    the project's style. Empty when the repo has no tests yet."""
+
+    existing_regression_files: list[str] = []
+    """Repo-relative paths matching the regression-file convention
+    (see ``settings.regression_file_pattern``). These are IMMUTABLE —
+    Phase 5 may reference them but never plan edits to them."""
+
+    affected_files_existing_coverage: list[ExistingCoverage] = []
+    """Per-affected-file coverage record so Phase 5 can skip already-
+    covered behavior and focus on uncovered code paths."""
+
+    notes: str = ""
+    """Anything else Phase 5 should know about the test infrastructure
+    — e.g. 'integration tests live under tests/integration and require
+    a running Postgres'."""
+
+
+class FileSummary(BaseModel):
+    """Validated Phase 2 output — produced by the synthesis pass."""
+
+    files_to_modify: list[FileObservation] = []
+    files_to_create: list[FileObservation] = []
+    files_read_for_context: list[FileObservation] = []
+    missing_infrastructure: list[MissingItem] = []
+    verified_references: list[VerifiedReference] = []
+    assumptions_resolved: list[AssumptionStatus] = []
+    testing_inventory: TestingInventory | None = None
+    """Phase 2's test-infrastructure inventory (Layer 6). ``None`` when
+    the project has no test footprint or Phase 2 (e.g. parallel path)
+    did not produce one. Phase 5 renders this into its user prompt so
+    the LLM can target existing coverage gaps precisely."""
+
+    notes: str = ""
+    """Free-form catch-all for cross-file references, tricky invariants, or
+    anything the structured fields do not capture."""
+
+
+# ── Phase 3 design + risk schemas ───────────────────────────────────────────
+#
+# DesignAndRisks is produced by a chat_structured synthesis pass at the end
+# of Phase 3. Its fields replace the prior free-form 3-section text output
+# and eliminate the secondary _extract_missing_files LLM call (missing_files
+# becomes a direct field on the object).
+
+NamingCategory = Literal[
+    "variables",
+    "functions",
+    "classes",
+    "files",
+    "routes",
+    "db_table",
+    "db_column",
+    "imports",
+]
+RiskSeverity = Literal["low", "medium", "high"]
+
+
+class NamingConvention(BaseModel):
+    """One naming pattern observed in the existing codebase."""
+
+    category: NamingCategory
+    pattern: str
+    """The convention itself — e.g. 'snake_case' or 'UPPER_SNAKE_CASE'."""
+
+    source_file: str
+    """Repo-relative path of a file exemplifying the pattern, or the literal
+    string 'standard framework conventions' when no codebase example applies."""
+
+
+class ChangeDesign(BaseModel):
+    """Design decisions for ONE non-obvious file."""
+
+    file_path: str
+    decisions: str
+    """3-8 lines of prose on non-obvious choices: complex DB schemas,
+    non-trivial method signatures, multi-component wiring, pattern
+    deviations. Skip for straightforward files (simple CRUD, basic
+    models, standard config)."""
+
+
+class MissingFile(BaseModel):
+    """A file that is required at runtime but absent from the plan."""
+
+    file_path: str
+    purpose: str
+    blocking: bool = False
+
+
+class DependencyOrder(BaseModel):
+    """An ordering constraint between plan files."""
+
+    file_path: str
+    depends_on: str
+    reason: str
+
+
+class CriticalRisk(BaseModel):
+    """A scope-level risk the plan must consciously account for."""
+
+    risk: str
+    severity: RiskSeverity
+    mitigation: str
+
+
+CoreFunctionalitySignal = Literal[
+    "phase1_deliverable",
+    "critical_risk_adjacent",
+    "public_api",
+    "downstream_consumer",
+    "user_designated",
+]
+CoreFunctionalityConfidence = Literal["high", "medium", "low"]
+
+
+class CoreFunctionalityTag(BaseModel):
+    """One entity flagged as load-bearing core functionality (Layer 9).
+
+    Phase 3 produces these tags based on deterministic signals; Phase 4
+    propagates them into the ExecutionPlan so Phase 5 knows which
+    entities MUST receive a regression test (as opposed to a regular
+    test). The user may prune/add tags during plan approval.
+    """
+
+    entity: str
+    """Function / class / module / route / CLI-command name. Short
+    and greppable so Phase 5 can reference it in test steps."""
+
+    file_path: str
+    """Repo-relative path of the file containing the entity."""
+
+    reason: str
+    """Short prose on why the entity is core — what breaks if it
+    regresses. Used by Phase 5 to write the regression test's
+    description and by the approval UI to explain the tag."""
+
+    source_signal: CoreFunctionalitySignal
+    """How Phase 3 inferred the tag: ``phase1_deliverable`` (matches
+    a Phase 1 deliverable), ``critical_risk_adjacent`` (co-located
+    with a high-severity risk), ``public_api`` (exported / route /
+    CLI surface), ``downstream_consumer`` (Phase 1 downstream
+    consumer depends on it), ``user_designated`` (added via the
+    approval UI)."""
+
+    confidence: CoreFunctionalityConfidence = "medium"
+    """``high`` / ``medium`` / ``low``. Phase 5 mandates regression
+    coverage for confidence ≥ ``settings.core_functionality_min_confidence``."""
+
+
+class DesignAndRisks(BaseModel):
+    """Validated Phase 3 output — produced by the synthesis pass."""
+
+    naming_conventions: list[NamingConvention] = []
+    change_designs: list[ChangeDesign] = []
+    missing_files: list[MissingFile] = []
+    dependency_order: list[DependencyOrder] = []
+    critical_risks: list[CriticalRisk] = []
+    citations: list[VerifiedReference] = []
+    """External dependencies the expert verified during Phase 3. Rendered
+    alongside Phase 2's VERIFIED REFERENCES at the Phase 4 boundary (dedupe
+    by docs_url)."""
+
+    core_functionality: list[CoreFunctionalityTag] = []
+    """Load-bearing entities that Phase 5 MUST guard with regression
+    tests (Layer 9). Populated by Phase 3's detection rules; propagated
+    into the ExecutionPlan by Phase 4. Empty when Phase 3 found no
+    entities worth tagging or the feature flag is disabled."""
+
+    notes: str = ""
+    """Free-form catch-all for architectural invariants or edge cases that
+    do not fit the structured fields."""
+
+
+# ── Phase 4 plan assembly schemas ───────────────────────────────────────────
+#
+# NameRegistryEntry is one canonical-name row per NEW entity introduced by
+# the plan. ExecutionPlan carries naming_conventions and name_registry as
+# typed lists rather than free-form text so post-generation validation can
+# reason over them and the assembly prompt can shrink.
+
+
+class NameRegistryEntry(BaseModel):
+    """Canonical names for ONE new entity introduced by the plan.
+
+    Populated by Phase 4. Injected into every step's system prompt during
+    execution via ``format_name_registry_for_prompt`` to prevent naming
+    drift across files. Only ``entity`` is required — every other field
+    defaults to empty and is only populated when applicable to the kind of
+    entity this row represents (e.g. a plain data class has no route).
+    """
+
+    entity: str
+    """Human-readable entity name (e.g. 'User Profile Page')."""
+
+    model_class: str = ""
+    """Exact class or type name (e.g. 'UserProfilePage')."""
+
+    module_namespace: str = ""
+    """Dotted module path (e.g. 'app.pages.user_profile')."""
+
+    import_stmt: str = ""
+    """Literal import statement other files should use."""
+
+    db_table: str = ""
+    """Table or collection name, if applicable."""
+
+    file_path: str = ""
+    """Repo-relative path to the file defining this entity."""
+
+    route_endpoint: str = ""
+    """HTTP route / endpoint, if applicable."""
+
+    registered_in: list[str] = []
+    """Files where this entity must be registered. Each entry here should
+    have a corresponding ``edit_file`` step in the plan."""
+
+    test_file: str = ""
+    """Test file path, if applicable."""
+
+
 # Canonical primary tools for implementation plan steps (Phase 4 output).
 # Read/search helpers may still appear in ``allowed_tools``; this set is used
 # only for the legacy ``tool`` field and for filtering out pure non-step noise.
@@ -394,11 +407,11 @@ IMPLEMENTATION_STEP_TOOLS = {
     "edit_file",
     "run_command",
     "read_file",
-    "run_tests",
-    "run_lint",
-    "format_code",
-}
-
+    "run_tests",
+    "run_lint",
+    "format_code",
+}
+
 # Alias — all tools that may appear in any PlanStep.
 ALL_VALID_STEP_TOOLS = IMPLEMENTATION_STEP_TOOLS
 
@@ -429,8 +442,8 @@ def _dedupe_tool_names(*groups: list[str] | tuple[str, ...]) -> list[str]:
             merged.append(name)
             seen.add(name)
     return merged
-
-
+
+
 class StepInput(BaseModel):
     """One piece of context the implementation model may rely on."""
 
@@ -533,9 +546,6 @@ class PlanStep(BaseModel):
     reason: str = ""
     """Why this job is needed."""
 
-    context: str = ""
-    """Legacy context hint. Prefer structured ``inputs`` in new plans."""
-
     _FILE_TOOLS = frozenset({"create_file", "edit_file", "read_file"})
     _MUTATING_TOOLS = frozenset({"create_file", "edit_file", "run_command", "format_code"})
 
@@ -609,125 +619,125 @@ class VerificationPlan(BaseModel):
     ``success_checks``. This model remains for older debug artifacts and
     targeted compatibility tests.
     """
-
-    steps: list[PlanStep]
-
-
-class ExecutionPlan(BaseModel):
-    """Complete structured plan for task execution."""
-
-    scope: str
-    """Brief summary of what the plan accomplishes and what is out of scope."""
-
-    user_summary: str = ""
-    """Plain-English description (up to ~1000 words) of what this plan will accomplish,
-    the key architectural decisions made, why specific structures are being changed,
-    and any design trade-offs. Written for the user to make an informed approval
-    decision — covers: problem being solved, approach taken, what load-bearing
-    structures are being touched and why."""
-
-    naming_conventions: list[NamingConvention] = []
-    """Naming conventions observed in existing code. Populated by Phase 4
-    as a typed list (category / pattern / source_file). Rendered to text
-    via ``format_naming_conventions_for_prompt`` when injected into
-    step-execution system prompts."""
-
-    name_registry: list[NameRegistryEntry] = []
-    """Canonical name mapping for every NEW entity introduced by this plan.
-
-    Each entry carries the entity's names across the stack (class,
-    namespace, import path, table, file, route, registration files,
-    test file). Populated by Phase 4 and rendered to text via
-    ``format_name_registry_for_prompt`` when injected into per-step
-    system prompts to prevent naming drift."""
-
-    steps: list[PlanStep]
-    """Ordered list of steps to execute.  Each step is one tool call."""
-
+
+    steps: list[PlanStep]
+
+
+class ExecutionPlan(BaseModel):
+    """Complete structured plan for task execution."""
+
+    scope: str
+    """Brief summary of what the plan accomplishes and what is out of scope."""
+
+    user_summary: str = ""
+    """Plain-English description (up to ~1000 words) of what this plan will accomplish,
+    the key architectural decisions made, why specific structures are being changed,
+    and any design trade-offs. Written for the user to make an informed approval
+    decision — covers: problem being solved, approach taken, what load-bearing
+    structures are being touched and why."""
+
+    naming_conventions: list[NamingConvention] = []
+    """Naming conventions observed in existing code. Populated by Phase 4
+    as a typed list (category / pattern / source_file). Rendered to text
+    via ``format_naming_conventions_for_prompt`` when injected into
+    step-execution system prompts."""
+
+    name_registry: list[NameRegistryEntry] = []
+    """Canonical name mapping for every NEW entity introduced by this plan.
+
+    Each entry carries the entity's names across the stack (class,
+    namespace, import path, table, file, route, registration files,
+    test file). Populated by Phase 4 and rendered to text via
+    ``format_name_registry_for_prompt`` when injected into per-step
+    system prompts to prevent naming drift."""
+
+    steps: list[PlanStep]
+    """Ordered list of steps to execute.  Each step is one tool call."""
+
     tdd_test_steps: list[PlanStep] = []
     """Legacy TDD test steps. New plans fold verification expectations into
     per-step ``success_checks`` instead of appending a separate Phase 5 plan."""
-
-    affected_files: list[str]
-    """All file paths that will be created or modified."""
-
-    test_strategy: str
-    """How to verify the changes work (included in run_tests steps)."""
-
-    plan_validation_warnings: list[str] = []
-    """Non-blocking warnings from post-generation plan validation
-    (hallucinated paths, uncovered missing files, edit/create mismatches,
-    etc.). Populated by the Phase 4 validators and surfaced on the
-    extension approval screen so users can see them alongside the plan.
-    Empty when the plan validated cleanly."""
-
-    core_functionality: list[CoreFunctionalityTag] = []
-    """Load-bearing entities Phase 5 must guard with regression tests
-    (Layer 9). Copied from ``DesignAndRisks.core_functionality`` during
-    Phase 4 synthesis. Rendered into the approval payload so users can
-    prune/add before approval. Empty when Phase 3 found no entities or
-    the feature flag is disabled."""
-
-
-def format_naming_conventions_for_prompt(
-    conventions: list[NamingConvention],
-) -> str:
-    """Render naming conventions as a prompt-friendly markdown table.
-
-    Returns empty string when the list is empty so callers can skip the
-    section cleanly.
-    """
-    if not conventions:
-        return ""
-    lines = ["| category | pattern | source_file |", "|---|---|---|"]
-    for nc in conventions:
-        lines.append(f"| {nc.category} | {nc.pattern} | {nc.source_file} |")
-    return "\n".join(lines)
-
-
-def format_name_registry_for_prompt(
-    entries: list[NameRegistryEntry],
-) -> str:
-    """Render the name registry in the text shape per-step prompts expect.
-
-    Matches the pre-structured template used by ``build_step_system_prompt``:
-
-        Entity "<Name>":
-          model/class: <...>
-          namespace/module: <...>
-          import: <...>
-          ...
-
-    Rows are included only when their field is populated — entities
-    without a route or DB table simply omit those lines. Returns empty
-    string when the list is empty.
-    """
-    if not entries:
-        return ""
-    blocks: list[str] = []
-    for entry in entries:
-        block = [f'Entity "{entry.entity}":']
-        if entry.model_class:
-            block.append(f"  model/class: {entry.model_class}")
-        if entry.module_namespace:
-            block.append(f"  namespace/module: {entry.module_namespace}")
-        if entry.import_stmt:
-            block.append(f"  import: {entry.import_stmt}")
-        if entry.db_table:
-            block.append(f"  table/collection: {entry.db_table}")
-        if entry.file_path:
-            block.append(f"  file: {entry.file_path}")
-        if entry.route_endpoint:
-            block.append(f"  route/endpoint: {entry.route_endpoint}")
-        if entry.registered_in:
-            block.append(f"  registered in: {', '.join(entry.registered_in)}")
-        if entry.test_file:
-            block.append(f"  test: {entry.test_file}")
-        blocks.append("\n".join(block))
-    return "\n\n".join(blocks)
-
-
-def _render_step(parts: list[str], step: PlanStep, include_context: bool) -> None:
+
+    affected_files: list[str]
+    """All file paths that will be created or modified."""
+
+    test_strategy: str
+    """How to verify the changes work (included in run_tests steps)."""
+
+    plan_validation_warnings: list[str] = []
+    """Non-blocking warnings from post-generation plan validation
+    (hallucinated paths, uncovered missing files, edit/create mismatches,
+    etc.). Populated by the Phase 4 validators and surfaced on the
+    extension approval screen so users can see them alongside the plan.
+    Empty when the plan validated cleanly."""
+
+    core_functionality: list[CoreFunctionalityTag] = []
+    """Load-bearing entities Phase 5 must guard with regression tests
+    (Layer 9). Copied from ``DesignAndRisks.core_functionality`` during
+    Phase 4 synthesis. Rendered into the approval payload so users can
+    prune/add before approval. Empty when Phase 3 found no entities or
+    the feature flag is disabled."""
+
+
+def format_naming_conventions_for_prompt(
+    conventions: list[NamingConvention],
+) -> str:
+    """Render naming conventions as a prompt-friendly markdown table.
+
+    Returns empty string when the list is empty so callers can skip the
+    section cleanly.
+    """
+    if not conventions:
+        return ""
+    lines = ["| category | pattern | source_file |", "|---|---|---|"]
+    for nc in conventions:
+        lines.append(f"| {nc.category} | {nc.pattern} | {nc.source_file} |")
+    return "\n".join(lines)
+
+
+def format_name_registry_for_prompt(
+    entries: list[NameRegistryEntry],
+) -> str:
+    """Render the name registry in the text shape per-step prompts expect.
+
+    Matches the pre-structured template used by ``build_step_system_prompt``:
+
+        Entity "<Name>":
+          model/class: <...>
+          namespace/module: <...>
+          import: <...>
+          ...
+
+    Rows are included only when their field is populated — entities
+    without a route or DB table simply omit those lines. Returns empty
+    string when the list is empty.
+    """
+    if not entries:
+        return ""
+    blocks: list[str] = []
+    for entry in entries:
+        block = [f'Entity "{entry.entity}":']
+        if entry.model_class:
+            block.append(f"  model/class: {entry.model_class}")
+        if entry.module_namespace:
+            block.append(f"  namespace/module: {entry.module_namespace}")
+        if entry.import_stmt:
+            block.append(f"  import: {entry.import_stmt}")
+        if entry.db_table:
+            block.append(f"  table/collection: {entry.db_table}")
+        if entry.file_path:
+            block.append(f"  file: {entry.file_path}")
+        if entry.route_endpoint:
+            block.append(f"  route/endpoint: {entry.route_endpoint}")
+        if entry.registered_in:
+            block.append(f"  registered in: {', '.join(entry.registered_in)}")
+        if entry.test_file:
+            block.append(f"  test: {entry.test_file}")
+        blocks.append("\n".join(block))
+    return "\n\n".join(blocks)
+
+
+def _render_step(parts: list[str], step: PlanStep) -> None:
     """Render a single plan step as markdown lines."""
     tool_label = ", ".join(step.allowed_tools) if step.allowed_tools else step.tool
     target_label = ""
@@ -764,50 +774,40 @@ def _render_step(parts: list[str], step: PlanStep, include_context: bool) -> Non
         parts.append(f"   **Success checks:** {checks}")
     if step.blocked_protocol:
         parts.append(f"   **If blocked:** {step.blocked_protocol}")
-    if include_context and step.context:
-        parts.append(f"   ```\n{step.context}\n   ```")
-
-
-def plan_to_markdown(plan: ExecutionPlan, *, include_context: bool = False) -> str:
-    """Render an ExecutionPlan as human-readable markdown.
-
-    Args:
-        plan: The execution plan to render.
-        include_context: If True, append each step's context field as a
-            fenced code block.  Used by Phase 6 so the verification model
-            can see design details.  The approval UI passes False (default)
-            to keep the output concise.
-    """
-    parts: list[str] = []
-
-    parts.append(f"## Scope\n\n{plan.scope}\n")
-
-    naming_text = format_naming_conventions_for_prompt(plan.naming_conventions)
-    if naming_text:
-        parts.append(f"## Naming Conventions\n\n{naming_text}\n")
-
-    registry_text = format_name_registry_for_prompt(plan.name_registry)
-    if registry_text:
-        parts.append(f"## Name Registry\n\n{registry_text}\n")
-
-    if plan.tdd_test_steps:
-        parts.append("## TEST PHASE (Expert Model)\n")
-        for step in plan.tdd_test_steps:
-            _render_step(parts, step, include_context)
-        parts.append("")
-        parts.append("## IMPLEMENTATION PHASE (Primary Model)\n")
-    else:
-        parts.append("## Steps\n")
-
-    for step in plan.steps:
-        _render_step(parts, step, include_context)
-    parts.append("")
-
-    parts.append("## Affected Files\n")
-    for f in plan.affected_files:
-        parts.append(f"- `{f}`")
-    parts.append("")
-
-    parts.append(f"## Test Strategy\n\n{plan.test_strategy}")
-
-    return "\n".join(parts)
+
+
+def plan_to_markdown(plan: ExecutionPlan) -> str:
+    """Render an ExecutionPlan as human-readable markdown."""
+    parts: list[str] = []
+
+    parts.append(f"## Scope\n\n{plan.scope}\n")
+
+    naming_text = format_naming_conventions_for_prompt(plan.naming_conventions)
+    if naming_text:
+        parts.append(f"## Naming Conventions\n\n{naming_text}\n")
+
+    registry_text = format_name_registry_for_prompt(plan.name_registry)
+    if registry_text:
+        parts.append(f"## Name Registry\n\n{registry_text}\n")
+
+    if plan.tdd_test_steps:
+        parts.append("## TEST PHASE (Expert Model)\n")
+        for step in plan.tdd_test_steps:
+            _render_step(parts, step)
+        parts.append("")
+        parts.append("## IMPLEMENTATION PHASE (Primary Model)\n")
+    else:
+        parts.append("## Steps\n")
+
+    for step in plan.steps:
+        _render_step(parts, step)
+    parts.append("")
+
+    parts.append("## Affected Files\n")
+    for f in plan.affected_files:
+        parts.append(f"- `{f}`")
+    parts.append("")
+
+    parts.append(f"## Test Strategy\n\n{plan.test_strategy}")
+
+    return "\n".join(parts)
diff --git a/backend/src/lean_ai/llm/planner.py b/backend/src/lean_ai/llm/planner.py
index 699afc3..ef69091 100644
--- a/backend/src/lean_ai/llm/planner.py
+++ b/backend/src/lean_ai/llm/planner.py
@@ -1,41 +1,41 @@
 """4-phase decomposed planning pipeline with structured output.
-
-Phase 1: Scope analysis
-Phase 2: File identification + content reading (with codebase exploration via tools)
-Phase 3: Design + risk synthesis (change design, naming conventions, gap analysis)
+
+Phase 1: Scope analysis
+Phase 2: File identification + content reading (with codebase exploration via tools)
+Phase 3: Design + risk synthesis (change design, naming conventions, gap analysis)
 Phase 4: Structured plan assembly (produces ExecutionPlan via chat_structured,
 including per-step success checks)
-
-Each phase is a focused LLM call. The planner uses read-only tools
-(read_file, list_directory, directory_tree, grep_files) during Phase 2
-to explore the codebase and read every file it plans to modify.
+
+Each phase is a focused LLM call. The planner uses read-only tools
+(read_file, list_directory, directory_tree, grep_files) during Phase 2
+to explore the codebase and read every file it plans to modify.
 Verification is folded into each Phase 4 step's success checks.
-"""
-
-import json
-import logging
-import time
-from collections.abc import Callable
-from pathlib import Path
-from typing import TYPE_CHECKING
-
-from fastapi import WebSocket
-
-from lean_ai.config import settings
-from lean_ai.llm.plan_schema import (
-    IMPLEMENTATION_STEP_TOOLS,
-    DesignAndRisks,
-    ExecutionPlan,
-    FileSummary,
-    MissingFile,
-    PlanStep,
-    VerificationPlan,
-    plan_to_markdown,
-)
-from lean_ai.llm.planner_exploration import (
-    _make_read_only_executor,
-    run_phase2_exploration,
-)
+"""
+
+import json
+import logging
+import time
+from collections.abc import Callable
+from pathlib import Path
+from typing import TYPE_CHECKING
+
+from fastapi import WebSocket
+
+from lean_ai.config import settings
+from lean_ai.llm.plan_schema import (
+    IMPLEMENTATION_STEP_TOOLS,
+    DesignAndRisks,
+    ExecutionPlan,
+    FileSummary,
+    MissingFile,
+    PlanStep,
+    VerificationPlan,
+    plan_to_markdown,
+)
+from lean_ai.llm.planner_exploration import (
+    _make_read_only_executor,
+    run_phase2_exploration,
+)
 from lean_ai.llm.planner_helpers import (
     PLAN_OUTPUT_PERCENT,
     _build_fallback_execution_plan,
@@ -43,32 +43,32 @@ from lean_ai.llm.planner_helpers import (
     _compact_file_summary,
     _retrieve_session_memories,
     _revise_plan,
-    _save_debug_phase,
-    _send_content_done,
-    _send_stage,
-    _send_stage_done,
-    _synthesize_scope,
-)
-from lean_ai.llm.prompt_registry import registry
-from lean_ai.llm.prompts import (
-    PLAN_ASSEMBLY_SYSTEM_PROMPT,
-    PLAN_DESIGN_SYSTEM_PROMPT,
-    PLAN_VERIFICATION_SYSTEM_PROMPT,
-)
-from lean_ai.llm.tool_definitions import (
-    REQUEST_CLARIFICATION_TOOL,
-    build_design_tools,
-    build_planning_tools,
-)
-
-if TYPE_CHECKING:
-    from lean_ai.llm.facade import LLMClient
-    from lean_ai.llm.refiner import PromptRefiner
-    from lean_ai.workflow.ws_dispatcher import WSMessageDispatcher
-
-logger = logging.getLogger(__name__)
-
-
+    _save_debug_phase,
+    _send_content_done,
+    _send_stage,
+    _send_stage_done,
+    _synthesize_scope,
+)
+from lean_ai.llm.prompt_registry import registry
+from lean_ai.llm.prompts import (
+    PLAN_ASSEMBLY_SYSTEM_PROMPT,
+    PLAN_DESIGN_SYSTEM_PROMPT,
+    PLAN_VERIFICATION_SYSTEM_PROMPT,
+)
+from lean_ai.llm.tool_definitions import (
+    REQUEST_CLARIFICATION_TOOL,
+    build_design_tools,
+    build_planning_tools,
+)
+
+if TYPE_CHECKING:
+    from lean_ai.llm.facade import LLMClient
+    from lean_ai.llm.refiner import PromptRefiner
+    from lean_ai.workflow.ws_dispatcher import WSMessageDispatcher
+
+logger = logging.getLogger(__name__)
+
+
 async def create_plan(
     task: str,
     repo_root: str,
@@ -78,11 +78,11 @@ async def create_plan(
     previous_plan: ExecutionPlan | None = None,
     ws: WebSocket | None = None,
     dispatcher: "WSMessageDispatcher | None" = None,
-    refiner: "PromptRefiner | None" = None,
-    test_command: str = "",
-    session_id: str = "",
-    expert_llm_client: "LLMClient | None" = None,
-    on_content: "Callable | None" = None,
+    refiner: "PromptRefiner | None" = None,
+    test_command: str = "",
+    session_id: str = "",
+    expert_llm_client: "LLMClient | None" = None,
+    on_content: "Callable | None" = None,
     on_thinking: "Callable | None" = None,
     on_tool_call: "Callable | None" = None,
     on_tool_result: "Callable | None" = None,
@@ -90,35 +90,35 @@ async def create_plan(
     on_metrics_reset: "Callable | None" = None,
 ) -> ExecutionPlan:
     """Create a plan using decomposed planning.
-
-    Phases 1–2 (scope + codebase exploration) run on the **primary** model
-    because exploration benefits from a coder-tuned model that can read
-    files precisely. The worker model already compresses large tool
-    outputs before they re-enter the primary's context (see
-    ``workflow/tool_executor.py``), so the primary isn't on its own.
+
+    Phases 1–2 (scope + codebase exploration) run on the **primary** model
+    because exploration benefits from a coder-tuned model that can read
+    files precisely. The worker model already compresses large tool
+    outputs before they re-enter the primary's context (see
+    ``workflow/tool_executor.py``), so the primary isn't on its own.
     Phases 3–4 (design + assembly) run on the **expert**
-    model when configured. The **request** model is reserved for chat
-    and ``/request`` mode — not planning.
-
-    Args:
-        task: The user's task description (may include clarification answers).
-        repo_root: Path to the repository root.
-        llm_client: Primary LLM client — runs phases 1–2 and implementation.
-        context: Pre-assembled context (project context, search results, etc.).
-        revision_context: If revising, the previous plan JSON + user feedback.
-        ws: Optional WebSocket for streaming stage progress.
-        refiner: Optional local refiner for privacy-stripping file summaries.
+    model when configured. The **request** model is reserved for chat
+    and ``/request`` mode — not planning.
+
+    Args:
+        task: The user's task description (may include clarification answers).
+        repo_root: Path to the repository root.
+        llm_client: Primary LLM client — runs phases 1–2 and implementation.
+        context: Pre-assembled context (project context, search results, etc.).
+        revision_context: If revising, the previous plan JSON + user feedback.
+        ws: Optional WebSocket for streaming stage progress.
+        refiner: Optional local refiner for privacy-stripping file summaries.
         test_command: If set, planner folds test commands into success checks.
         expert_llm_client: Optional expert model for phases 3–4 reasoning.
-        on_content: Streaming callback for content tokens.
-        on_thinking: Streaming callback for thinking tokens.
-        on_tool_call: Callback for tool call events (phase 2).
-        on_tool_result: Callback for tool result events (phase 2).
-        on_metrics: Callback for metrics updates (phase 2).
-
-    Returns:
-        Structured ExecutionPlan ready for per-step execution.
-    """
+        on_content: Streaming callback for content tokens.
+        on_thinking: Streaming callback for thinking tokens.
+        on_tool_call: Callback for tool call events (phase 2).
+        on_tool_result: Callback for tool result events (phase 2).
+        on_metrics: Callback for metrics updates (phase 2).
+
+    Returns:
+        Structured ExecutionPlan ready for per-step execution.
+    """
     if revision_context:
         return await _revise_plan(
             task,
@@ -132,24 +132,24 @@ async def create_plan(
             on_metrics=on_metrics,
             on_metrics_reset=on_metrics_reset,
         )
-
-    # Explorer for phases 1–2 is always the primary model. The request
-    # model is chat-only — routing it through codebase exploration wastes
-    # a chatty-tuned model on a task the coder-tuned primary does better.
-    explorer = llm_client
-    phase_max_tokens = settings.ollama_max_tokens
-
+
+    # Explorer for phases 1–2 is always the primary model. The request
+    # model is chat-only — routing it through codebase exploration wastes
+    # a chatty-tuned model on a task the coder-tuned primary does better.
+    explorer = llm_client
+    phase_max_tokens = settings.ollama_max_tokens
+
     # Expert client for reasoning-heavy phases (3-4), falls back to standard
-    expert = expert_llm_client or llm_client
-    expert_max_tokens = (
-        settings.effective_expert_max_tokens if expert_llm_client else phase_max_tokens
-    )
-
-    expert_ctx = (
-        settings.effective_expert_context_window
-        if expert_llm_client
-        else settings._active_context_window
-    )
+    expert = expert_llm_client or llm_client
+    expert_max_tokens = (
+        settings.effective_expert_max_tokens if expert_llm_client else phase_max_tokens
+    )
+
+    expert_ctx = (
+        settings.effective_expert_context_window
+        if expert_llm_client
+        else settings._active_context_window
+    )
     plan_assembly_max_tokens = max(
         expert_max_tokens,
         int(expert_ctx * PLAN_OUTPUT_PERCENT),
@@ -165,8 +165,8 @@ async def create_plan(
     # Reuse the context parameter (already loaded by the workflow router)
     # rather than re-reading from disk.
     project_context = context
-
-    # ── Cross-session memory retrieval ──
+
+    # ── Cross-session memory retrieval ──
     memory_context = ""
     if settings.enable_session_memory:
         memory_context = await _retrieve_session_memories(repo_root, task)
@@ -512,14 +512,6 @@ async def create_plan(
 
         phase4_scope = scope
         phase4_project_context = project_context
-        if expert_ctx <= 32768:
-            phase4_scope = ""
-            phase4_project_context = ""
-            logger.info(
-                "Phase 4: small context window (%d) — dropping scope and "
-                "project_context re-injection (already in design_and_risks)",
-                expert_ctx,
-            )
 
         verification_targets = _build_verification_targets(
             file_summary_obj,
@@ -528,6 +520,9 @@ async def create_plan(
         security_concerns = _build_security_concerns(design_and_risks_obj)
         testing_inventory = _format_testing_inventory(file_summary_obj)
         core_functionality = _format_core_functionality(design_and_risks_obj)
+        dependency_order_block = _format_dependency_order(design_and_risks_obj)
+        naming_conventions_block = _format_naming_conventions_section(design_and_risks_obj)
+        risk_assessment_block = _format_risk_assessment_section(design_and_risks_obj)
 
         plan = await _chat_structured_with_repair(
             messages=[
@@ -559,6 +554,9 @@ async def create_plan(
                         verification_targets=verification_targets or "(derive from affected behavioral files)",
                         security_concerns=security_concerns or "(none identified by Phase 3)",
                         core_functionality=core_functionality,
+                        dependency_order=dependency_order_block,
+                        naming_conventions=naming_conventions_block,
+                        risk_assessment=risk_assessment_block,
                     ),
                 },
             ],
@@ -609,25 +607,38 @@ async def create_plan(
 
         _sync_affected_files_from_steps(plan)
 
-        plan_warnings = _run_plan_validations(
+        plan_warnings, is_blocking = _run_plan_validations(
             plan,
             file_summary_obj,
             design_and_risks_obj,
         )
 
-        blocking_uncovered = [
-            mf for mf in _uncovered_missing_files(plan, design_and_risks_obj) if mf.blocking
-        ]
-        if blocking_uncovered:
+        # Revision loop with hard cap of 2 iterations for blocking warnings.
+        # Each iteration asks the LLM to revise the plan to address the
+        # blocking validation failures, then re-validates.
+        max_revisions = 2
+        revision_count = 0
+        while is_blocking and revision_count < max_revisions:
+            revision_count += 1
             logger.warning(
-                "Phase 4 plan validation — %d BLOCKING uncovered missing "
-                "file(s); triggering auto-revision",
-                len(blocking_uncovered),
+                "Phase 4 plan validation — blocking warnings detected "
+                "(revision %d/%d); triggering auto-revision",
+                revision_count,
+                max_revisions,
             )
+            # Build feedback from blocking warnings.
+            blocking_warnings = [
+                w for w in plan_warnings
+                if "[BLOCKING]" in w
+                or "invented path:" in w
+                or "write target not found" in w
+            ]
+            if not blocking_warnings:
+                blocking_warnings = plan_warnings
             feedback = (
-                "Phase 3 identified BLOCKING missing files that the plan "
-                "does not cover. Add a create_file or edit_file step for "
-                "each:\n" + "\n".join(f"- {mf.file_path}: {mf.purpose}" for mf in blocking_uncovered)
+                "Phase 4 plan validation produced BLOCKING warnings. "
+                "Revise the plan to address each one:\n"
+                + "\n".join(f"- {w}" for w in blocking_warnings)
             )
             plan = await _revise_plan(
                 task=task,
@@ -649,12 +660,20 @@ async def create_plan(
             for i, step in enumerate(plan.steps, 1):
                 step.step_number = i
             _sync_affected_files_from_steps(plan)
-            plan_warnings = _run_plan_validations(
+            plan_warnings, is_blocking = _run_plan_validations(
                 plan,
                 file_summary_obj,
                 design_and_risks_obj,
             )
 
+        if revision_count >= max_revisions and is_blocking:
+            logger.warning(
+                "Phase 4 revision cap reached (%d iterations) — "
+                "plan ships with %d blocking warning(s)",
+                max_revisions,
+                len([w for w in plan_warnings if "[BLOCKING]" in w]),
+            )
+
         plan.plan_validation_warnings = plan_warnings
 
         _save_debug_phase(
@@ -730,108 +749,119 @@ async def create_plan(
             phase=4,
         )
         return plan
-
-
+
+
 async def _run_phase5_verification(
-    *,
-    plan: ExecutionPlan,
-    task: str,
-    file_summary: str,
-    file_summary_obj: FileSummary | None,
-    design_and_risks_obj: DesignAndRisks,
-    test_command: str,
-    expert: "LLMClient",
-    plan_assembly_max_tokens: int,
-    ws: WebSocket | None,
+    *,
+    plan: ExecutionPlan,
+    task: str,
+    file_summary: str,
+    file_summary_obj: FileSummary | None,
+    design_and_risks_obj: DesignAndRisks,
+    test_command: str,
+    expert: "LLMClient",
+    plan_assembly_max_tokens: int,
+    ws: WebSocket | None,
     repo_root: str,
     session_id: str,
     on_thinking: Callable | None,
     on_metrics: Callable | None,
     on_metrics_reset: Callable | None,
 ) -> float:
-    """Run Phase 5: Verification step generation.
-
-    Appends test creation + test execution steps to the plan (normal
-    mode) or stores them separately in ``plan.tdd_test_steps`` (TDD
-    mode). Receives the structured Phase 2 ``FileSummary`` and Phase 3
-    ``DesignAndRisks`` so the user prompt can target specific files
-    for coverage and cite critical_risks as security cases.
-
-    Test-path convention warnings are appended to
-    ``plan.plan_validation_warnings`` so the approval UI (the Phase 4
-    surfacing mechanism) carries them through to the user.
-
-    Returns elapsed time in seconds.
-    """
-    tdd_mode = settings.enable_tdd
-    phase_label = (
-        "Phase 5: Designing TDD test steps..."
-        if tdd_mode
-        else "Phase 5: Adding verification steps..."
-    )
-    await _send_stage(ws, phase_label, model=expert.model_name, phase=5)
-    logger.info(
-        "Planning Phase 5: Verification step generation (tdd=%s)",
-        tdd_mode,
-    )
-    t0 = time.monotonic()
-
-    impl_plan_md = plan_to_markdown(plan, include_context=False)
-    next_step = len(plan.steps) + 1
-
-    verification_targets = _build_verification_targets(
-        file_summary_obj,
-        design_and_risks_obj,
-    )
-    security_concerns = _build_security_concerns(design_and_risks_obj)
-
-    # Layer 6 (testing inventory) populates in a later PR; for now pass
-    # an explicit empty-marker so the prompt still formats cleanly.
-    testing_inventory = _format_testing_inventory(file_summary_obj)
-
-    # Layer 9 (core functionality) populates in a later PR; for now pass
-    # an explicit empty-marker.
-    core_functionality = _format_core_functionality(plan)
-
-    # Layer 4 graceful-degradation scaffolding: when ``test_command`` is
-    # empty, omit the "end with run_tests" rule so the LLM doesn't
-    # invent a phantom test command. The Layer 4 PR removes the
-    # ``if test_command:`` gate around Phase 5 entirely.
-    if test_command:
-        run_tests_rule = f"- Exactly ONE final run_tests step invoking: {test_command}\n"
-    else:
-        run_tests_rule = (
-            "- Do NOT include a run_tests step — no test runner is "
-            "configured for this workspace yet. Create the test "
-            "files on disk; the runner will be added later.\n"
-        )
-
-    if tdd_mode:
-        user_content = registry.format(
-            "planning.verification_user_tdd",
-            task=task,
-            impl_plan_md=impl_plan_md,
-            testing_inventory=testing_inventory,
-            verification_targets=verification_targets,
-            security_concerns=security_concerns,
-            core_functionality=core_functionality,
-            next_step=str(next_step),
-        )
-    else:
-        user_content = registry.format(
-            "planning.verification_user_normal",
-            task=task,
-            test_command=test_command or "(none configured yet)",
-            impl_plan_md=impl_plan_md,
-            file_summary=file_summary,
-            testing_inventory=testing_inventory,
-            verification_targets=verification_targets,
-            security_concerns=security_concerns,
-            core_functionality=core_functionality,
-            next_step=str(next_step),
-            run_tests_rule=run_tests_rule,
-        )
-
+    """Run Phase 5: Verification step generation.
+
+    Appends test creation + test execution steps to the plan (normal
+    mode) or stores them separately in ``plan.tdd_test_steps`` (TDD
+    mode). Receives the structured Phase 2 ``FileSummary`` and Phase 3
+    ``DesignAndRisks`` so the user prompt can target specific files
+    for coverage and cite critical_risks as security cases.
+
+    Test-path convention warnings are appended to
+    ``plan.plan_validation_warnings`` so the approval UI (the Phase 4
+    surfacing mechanism) carries them through to the user.
+
+    Returns elapsed time in seconds.
+    """
+    tdd_mode = settings.enable_tdd
+    phase_label = (
+        "Phase 5: Designing TDD test steps..."
+        if tdd_mode
+        else "Phase 5: Adding verification steps..."
+    )
+    await _send_stage(ws, phase_label, model=expert.model_name, phase=5)
+    logger.info(
+        "Planning Phase 5: Verification step generation (tdd=%s)",
+        tdd_mode,
+    )
+    t0 = time.monotonic()
+
+    impl_plan_md = plan_to_markdown(plan, include_context=False)
+    next_step = len(plan.steps) + 1
+
+    verification_targets = _build_verification_targets(
+        file_summary_obj,
+        design_and_risks_obj,
+    )
+    security_concerns = _build_security_concerns(design_and_risks_obj)
+
+    # Layer 6 (testing inventory) populates in a later PR; for now pass
+    # an explicit empty-marker so the prompt still formats cleanly.
+    testing_inventory = _format_testing_inventory(file_summary_obj)
+
+    # Layer 9 (core functionality) populates in a later PR; for now pass
+    # an explicit empty-marker.
+    core_functionality = _format_core_functionality(plan)
+
+    # Structured sections from Phase 3 — always included in Phase 5 prompts.
+    dependency_order_block = _format_dependency_order(design_and_risks_obj)
+    naming_conventions_block = _format_naming_conventions_section(design_and_risks_obj)
+    risk_assessment_block = _format_risk_assessment_section(design_and_risks_obj)
+
+    # Layer 4 graceful-degradation scaffolding: when ``test_command`` is
+    # empty, omit the "end with run_tests" rule so the LLM doesn't
+    # invent a phantom test command. The Layer 4 PR removes the
+    # ``if test_command:`` gate around Phase 5 entirely.
+    if test_command:
+        run_tests_rule = f"- Exactly ONE final run_tests step invoking: {test_command}\n"
+    else:
+        run_tests_rule = (
+            "- Do NOT include a run_tests step — no test runner is "
+            "configured for this workspace yet. Create the test "
+            "files on disk; the runner will be added later.\n"
+        )
+
+    if tdd_mode:
+        user_content = registry.format(
+            "planning.verification_user_tdd",
+            task=task,
+            impl_plan_md=impl_plan_md,
+            testing_inventory=testing_inventory,
+            verification_targets=verification_targets,
+            security_concerns=security_concerns,
+            core_functionality=core_functionality,
+            next_step=str(next_step),
+            dependency_order=dependency_order_block,
+            naming_conventions=naming_conventions_block,
+            risk_assessment=risk_assessment_block,
+        )
+    else:
+        user_content = registry.format(
+            "planning.verification_user_normal",
+            task=task,
+            test_command=test_command or "(none configured yet)",
+            impl_plan_md=impl_plan_md,
+            file_summary=file_summary,
+            testing_inventory=testing_inventory,
+            verification_targets=verification_targets,
+            security_concerns=security_concerns,
+            core_functionality=core_functionality,
+            next_step=str(next_step),
+            run_tests_rule=run_tests_rule,
+            dependency_order=dependency_order_block,
+            naming_conventions=naming_conventions_block,
+            risk_assessment=risk_assessment_block,
+        )
+
     verification = await _chat_structured_with_repair(
         messages=[
             {"role": "system", "content": PLAN_VERIFICATION_SYSTEM_PROMPT},
@@ -847,172 +877,172 @@ async def _run_phase5_verification(
         on_metrics=on_metrics,
         on_metrics_reset=on_metrics_reset,
     )
-
-    if tdd_mode:
-        # TDD: keep test steps separate for expert-first execution.
-        # The TDD user prompt asks explicitly for no run_tests step;
-        # keep the filter as defensive safety in case the model
-        # ignores that instruction.
-        test_steps_only = [s for s in verification.steps if s.tool != "run_tests"]
-        for i, step in enumerate(test_steps_only, 1):
-            step.step_number = i
-        plan.tdd_test_steps = test_steps_only
-
-        # Re-number implementation steps starting after test steps.
-        offset = len(test_steps_only)
-        for i, step in enumerate(plan.steps, offset + 1):
-            step.step_number = i
-    else:
-        # Normal mode: append verification steps to plan.
-        appended = list(verification.steps)
-
-        # Safety net: Phase 5 must never skip running the existing
-        # test suite when a runner is configured. If the model omitted
-        # a run_tests step, inject one so the plan always ends with a
-        # test execution step — even when no new test files were
-        # created. When test_command is empty (Layer 4 — always run
-        # Phase 5 without a runner), the safety-net is disabled and
-        # test files are seeded on disk without a run_tests step.
-        if test_command and not any(s.tool == "run_tests" for s in appended):
-            logger.warning(
-                "Phase 5 produced no run_tests step — injecting one so existing tests run (%s).",
-                test_command,
-            )
-            # Defensively filter any run_tests step the LLM tried to
-            # produce with an empty command — only inject when we have
-            # a real command to invoke.
-            appended.append(
-                PlanStep(
-                    step_number=0,
-                    tool="run_tests",
-                    file_path="",
-                    instruction=(
-                        f"Run the project's test suite to confirm "
-                        f"the implementation works: {test_command}"
-                    ),
-                    reason=(
-                        "Verify the existing test suite still passes after the plan's changes."
-                    ),
-                    context="",
-                )
-            )
-            # Keep the injected step in the debug payload too so the
-            # saved JSON reflects what actually ran.
-            verification.steps = appended
-        elif not test_command:
-            # Layer 4 — defensive: drop any run_tests step the LLM
-            # produced despite our prompt telling it not to. Running
-            # an empty command is a no-op at best and a crash at
-            # worst.
-            stripped = [s for s in appended if s.tool != "run_tests"]
-            if len(stripped) != len(appended):
-                logger.info(
-                    "Phase 5 dropped %d run_tests step(s) — no test "
-                    "runner is configured for this workspace.",
-                    len(appended) - len(stripped),
-                )
-                appended = stripped
-                verification.steps = appended
-
-        for i, step in enumerate(appended, next_step):
-            step.step_number = i
-        plan.steps.extend(appended)
-
-    # Update affected_files with any new test files.
-    all_verification_steps = plan.tdd_test_steps if tdd_mode else verification.steps
-    existing = set(plan.affected_files)
-    for step in all_verification_steps:
-        if step.file_path and step.file_path not in existing:
-            plan.affected_files.append(step.file_path)
-
-    # Test-path convention check — append warnings to the plan so the
-    # approval UI surfacing (from Phase 4) picks them up.
-    path_warnings = _check_test_path_conventions(
-        verification,
-        file_summary_obj,
-    )
-    if path_warnings:
-        plan.plan_validation_warnings.extend(path_warnings)
-
-    # Layer 2 — coverage validator: warn when an executable affected
-    # file has no test step referencing it. Non-blocking.
-    coverage_warnings = _check_affected_files_covered(
-        verification,
-        plan,
-        file_summary_obj,
-    )
-    if coverage_warnings:
-        plan.plan_validation_warnings.extend(coverage_warnings)
-
-    # Layer 9 — core-functionality coverage: warn when a core entity
-    # has no matching regression test step. Non-blocking.
-    core_warnings = _check_core_functionality_covered(verification, plan)
-    if core_warnings:
-        plan.plan_validation_warnings.extend(core_warnings)
-
-    elapsed = time.monotonic() - t0
-    _save_debug_phase(
-        repo_root,
-        session_id,
-        "phase_5_verification",
-        verification.model_dump_json(indent=2),
-        elapsed,
-    )
-    test_steps = len(all_verification_steps)
-    if tdd_mode:
-        stage_msg = f"TDD test steps designed — {test_steps} step(s)"
-    elif not test_command:
-        stage_msg = f"Test files seeded — {test_steps} step(s); no test runner configured"
-    else:
-        stage_msg = f"Verification steps added — {test_steps} test step(s)"
-    await _send_stage_done(
-        ws,
-        stage_msg,
-        model=expert.model_name,
-        phase=5,
-    )
-
-    return elapsed
-
-
-# ── Phase 3 synthesis + rendering ───────────────────────────────────────────
-
-
+
+    if tdd_mode:
+        # TDD: keep test steps separate for expert-first execution.
+        # The TDD user prompt asks explicitly for no run_tests step;
+        # keep the filter as defensive safety in case the model
+        # ignores that instruction.
+        test_steps_only = [s for s in verification.steps if s.tool != "run_tests"]
+        for i, step in enumerate(test_steps_only, 1):
+            step.step_number = i
+        plan.tdd_test_steps = test_steps_only
+
+        # Re-number implementation steps starting after test steps.
+        offset = len(test_steps_only)
+        for i, step in enumerate(plan.steps, offset + 1):
+            step.step_number = i
+    else:
+        # Normal mode: append verification steps to plan.
+        appended = list(verification.steps)
+
+        # Safety net: Phase 5 must never skip running the existing
+        # test suite when a runner is configured. If the model omitted
+        # a run_tests step, inject one so the plan always ends with a
+        # test execution step — even when no new test files were
+        # created. When test_command is empty (Layer 4 — always run
+        # Phase 5 without a runner), the safety-net is disabled and
+        # test files are seeded on disk without a run_tests step.
+        if test_command and not any(s.tool == "run_tests" for s in appended):
+            logger.warning(
+                "Phase 5 produced no run_tests step — injecting one so existing tests run (%s).",
+                test_command,
+            )
+            # Defensively filter any run_tests step the LLM tried to
+            # produce with an empty command — only inject when we have
+            # a real command to invoke.
+            appended.append(
+                PlanStep(
+                    step_number=0,
+                    tool="run_tests",
+                    file_path="",
+                    instruction=(
+                        f"Run the project's test suite to confirm "
+                        f"the implementation works: {test_command}"
+                    ),
+                    reason=(
+                        "Verify the existing test suite still passes after the plan's changes."
+                    ),
+                    context="",
+                )
+            )
+            # Keep the injected step in the debug payload too so the
+            # saved JSON reflects what actually ran.
+            verification.steps = appended
+        elif not test_command:
+            # Layer 4 — defensive: drop any run_tests step the LLM
+            # produced despite our prompt telling it not to. Running
+            # an empty command is a no-op at best and a crash at
+            # worst.
+            stripped = [s for s in appended if s.tool != "run_tests"]
+            if len(stripped) != len(appended):
+                logger.info(
+                    "Phase 5 dropped %d run_tests step(s) — no test "
+                    "runner is configured for this workspace.",
+                    len(appended) - len(stripped),
+                )
+                appended = stripped
+                verification.steps = appended
+
+        for i, step in enumerate(appended, next_step):
+            step.step_number = i
+        plan.steps.extend(appended)
+
+    # Update affected_files with any new test files.
+    all_verification_steps = plan.tdd_test_steps if tdd_mode else verification.steps
+    existing = set(plan.affected_files)
+    for step in all_verification_steps:
+        if step.file_path and step.file_path not in existing:
+            plan.affected_files.append(step.file_path)
+
+    # Test-path convention check — append warnings to the plan so the
+    # approval UI surfacing (from Phase 4) picks them up.
+    path_warnings = _check_test_path_conventions(
+        verification,
+        file_summary_obj,
+    )
+    if path_warnings:
+        plan.plan_validation_warnings.extend(path_warnings)
+
+    # Layer 2 — coverage validator: warn when an executable affected
+    # file has no test step referencing it. Non-blocking.
+    coverage_warnings = _check_affected_files_covered(
+        verification,
+        plan,
+        file_summary_obj,
+    )
+    if coverage_warnings:
+        plan.plan_validation_warnings.extend(coverage_warnings)
+
+    # Layer 9 — core-functionality coverage: warn when a core entity
+    # has no matching regression test step. Non-blocking.
+    core_warnings = _check_core_functionality_covered(verification, plan)
+    if core_warnings:
+        plan.plan_validation_warnings.extend(core_warnings)
+
+    elapsed = time.monotonic() - t0
+    _save_debug_phase(
+        repo_root,
+        session_id,
+        "phase_5_verification",
+        verification.model_dump_json(indent=2),
+        elapsed,
+    )
+    test_steps = len(all_verification_steps)
+    if tdd_mode:
+        stage_msg = f"TDD test steps designed — {test_steps} step(s)"
+    elif not test_command:
+        stage_msg = f"Test files seeded — {test_steps} step(s); no test runner configured"
+    else:
+        stage_msg = f"Verification steps added — {test_steps} test step(s)"
+    await _send_stage_done(
+        ws,
+        stage_msg,
+        model=expert.model_name,
+        phase=5,
+    )
+
+    return elapsed
+
+
+# ── Phase 3 synthesis + rendering ───────────────────────────────────────────
+
+
 async def _synthesize_design_and_risks(
-    *,
-    task: str,
-    scope: str,
-    project_context_block: str,
-    file_summary: str,
-    exploration_prose: str,
+    *,
+    task: str,
+    scope: str,
+    project_context_block: str,
+    file_summary: str,
+    exploration_prose: str,
     expert: "LLMClient",
     expert_max_tokens: int,
     on_thinking: "Callable | None" = None,
     on_metrics: "Callable | None" = None,
     on_metrics_reset: "Callable | None" = None,
 ) -> DesignAndRisks:
-    """Coerce Phase 3's exploration prose + inputs into a DesignAndRisks.
-
-    On structured-output failure, returns a minimal DesignAndRisks with the
-    exploration prose stashed in ``notes`` so the pipeline keeps moving.
-    """
-    synthesis_system = registry.get("planning.design_synthesis_system")
-    user_parts = [
-        f"TASK: {task}",
-        f"SCOPE:\n{scope}",
-    ]
-    if project_context_block:
-        user_parts.append(project_context_block.rstrip())
-    user_parts.append(f"FILE SUMMARY:\n{file_summary}")
-    if exploration_prose.strip():
-        user_parts.append(f"PASS 1 EXPLORATION PROSE:\n{exploration_prose}")
-    user_parts.append(
-        "Produce a DesignAndRisks object from the inputs above. "
-        "Populate every field per the system-prompt rubric. Empty lists "
-        "are acceptable when an input contains nothing relevant."
-    )
-
-    try:
+    """Coerce Phase 3's exploration prose + inputs into a DesignAndRisks.
+
+    On structured-output failure, returns a minimal DesignAndRisks with the
+    exploration prose stashed in ``notes`` so the pipeline keeps moving.
+    """
+    synthesis_system = registry.get("planning.design_synthesis_system")
+    user_parts = [
+        f"TASK: {task}",
+        f"SCOPE:\n{scope}",
+    ]
+    if project_context_block:
+        user_parts.append(project_context_block.rstrip())
+    user_parts.append(f"FILE SUMMARY:\n{file_summary}")
+    if exploration_prose.strip():
+        user_parts.append(f"PASS 1 EXPLORATION PROSE:\n{exploration_prose}")
+    user_parts.append(
+        "Produce a DesignAndRisks object from the inputs above. "
+        "Populate every field per the system-prompt rubric. Empty lists "
+        "are acceptable when an input contains nothing relevant."
+    )
+
+    try:
         return await expert.chat_structured(
             messages=[
                 {"role": "system", "content": synthesis_system},
@@ -1024,143 +1054,188 @@ async def _synthesize_design_and_risks(
             on_metrics=on_metrics,
             on_metrics_reset=on_metrics_reset,
         )
-    except Exception:
-        logger.warning(
-            "Phase 3 synthesis failed — returning minimal DesignAndRisks "
-            "with exploration prose in notes",
-            exc_info=True,
-        )
-        return DesignAndRisks(notes=exploration_prose.strip())
-
-
-def _format_design_and_risks(dar: DesignAndRisks) -> str:
-    """Render a DesignAndRisks object to the markdown shape Phase 4 consumes
-    as ``{design_and_risks}``. Empty sections are omitted.
-    """
-    lines: list[str] = []
-
-    if dar.naming_conventions:
-        lines.append("## Naming Conventions")
-        lines.append("")
-        lines.append("| category | pattern | source_file |")
-        lines.append("|---|---|---|")
-        for nc in dar.naming_conventions:
-            lines.append(f"| {nc.category} | {nc.pattern} | {nc.source_file} |")
-        lines.append("")
-
-    if dar.change_designs:
-        lines.append("## Change Designs")
-        lines.append("")
-        for cd in dar.change_designs:
-            lines.append(f"### {cd.file_path}")
-            lines.append("")
-            lines.append(cd.decisions.strip())
-            lines.append("")
-
-    if dar.missing_files:
-        lines.append("## Missing Files")
-        lines.append("")
-        for i, m in enumerate(dar.missing_files, 1):
-            blocking = " [BLOCKING]" if m.blocking else ""
-            lines.append(f"{i}. {m.file_path} — {m.purpose}{blocking}")
-        lines.append("")
-
-    if dar.dependency_order:
-        lines.append("## Dependency Order")
-        lines.append("")
-        for d in dar.dependency_order:
-            lines.append(f"- {d.file_path} depends on {d.depends_on} — {d.reason}")
-        lines.append("")
-
-    if dar.critical_risks:
-        lines.append("## Critical Risks")
-        lines.append("")
-        for r in dar.critical_risks:
-            lines.append(f"- **[{r.severity}]** {r.risk} — {r.mitigation}")
-        lines.append("")
-
-    if dar.citations:
-        lines.append("## Citations")
-        lines.append("")
-        seen_urls: set[str] = set()
-        for c in dar.citations:
-            if c.docs_url in seen_urls:
-                continue
-            seen_urls.add(c.docs_url)
-            entry = f"- {c.dependency} — {c.docs_url}"
-            if c.version:
-                entry += f" — version: {c.version}"
-            if c.confirmed_patterns:
-                entry += f" — {c.confirmed_patterns}"
-            lines.append(entry)
-        lines.append("")
-
-    if dar.notes.strip():
-        lines.append("## Notes")
-        lines.append("")
-        lines.append(dar.notes.strip())
-        lines.append("")
-
-    if not lines:
-        return "(no design output)\n"
-    return "\n".join(lines).rstrip() + "\n"
-
-
-def _format_missing_files(missing: list[MissingFile]) -> str:
-    """Render the missing-files list as the numbered bullet string Phase 4
-    consumes as ``{missing_files}``. Empty string when no entries — matches
-    the prior behaviour of ``_extract_missing_files``.
-    """
-    if not missing:
-        return ""
-    rows: list[str] = []
-    for i, m in enumerate(missing, 1):
-        blocking = " [BLOCKING]" if m.blocking else ""
-        rows.append(f"{i}. {m.file_path} — {m.purpose}{blocking}")
-    return "\n".join(rows)
-
-
-# ── Phase 4 plan validation helpers ─────────────────────────────────────────
-#
-# All checks are set-membership against structured inputs from Phases 2 and
-# 3 — no regex, no parsing of LLM-generated prose. Warnings are logged and
-# also returned as a list so the caller can stash them on the plan for UI
-# surfacing.
-
-
-def _collect_known_paths(
-    file_summary: FileSummary | None,
-    dar: DesignAndRisks,
-) -> set[str]:
-    """Union of every file path the prior phases know about.
-
-    Returns an empty set when Phase 2 produced no structured output
-    (parallel path), which tells the caller to skip membership-based
-    checks cleanly rather than flag every path as invented.
-    """
-    if file_summary is None:
-        return set()
-    paths: set[str] = set()
-    for obs in file_summary.files_to_modify:
-        paths.add(obs.file_path)
-    for obs in file_summary.files_to_create:
-        paths.add(obs.file_path)
-    for obs in file_summary.files_read_for_context:
-        paths.add(obs.file_path)
-    for item in file_summary.missing_infrastructure:
-        paths.add(item.name)
-    for mf in dar.missing_files:
-        paths.add(mf.file_path)
-    return paths
-
-
-def _check_hallucinated_paths(
-    plan: ExecutionPlan,
-    known_paths: set[str],
-) -> list[str]:
-    """Flag any step.file_path that is not in the prior-phase path universe."""
+    except Exception:
+        logger.warning(
+            "Phase 3 synthesis failed — returning minimal DesignAndRisks "
+            "with exploration prose in notes",
+            exc_info=True,
+        )
+        return DesignAndRisks(notes=exploration_prose.strip())
+
+
+def _format_design_and_risks(dar: DesignAndRisks) -> str:
+    """Render a DesignAndRisks object to the markdown shape Phase 4 consumes
+    as ``{design_and_risks}``. Empty sections are omitted.
+    """
+    lines: list[str] = []
+
+    if dar.naming_conventions:
+        lines.append("## Naming Conventions")
+        lines.append("")
+        lines.append("| category | pattern | source_file |")
+        lines.append("|---|---|---|")
+        for nc in dar.naming_conventions:
+            lines.append(f"| {nc.category} | {nc.pattern} | {nc.source_file} |")
+        lines.append("")
+
+    if dar.change_designs:
+        lines.append("## Change Designs")
+        lines.append("")
+        for cd in dar.change_designs:
+            lines.append(f"### {cd.file_path}")
+            lines.append("")
+            lines.append(cd.decisions.strip())
+            lines.append("")
+
+    if dar.missing_files:
+        lines.append("## Missing Files")
+        lines.append("")
+        for i, m in enumerate(dar.missing_files, 1):
+            blocking = " [BLOCKING]" if m.blocking else ""
+            lines.append(f"{i}. {m.file_path} — {m.purpose}{blocking}")
+        lines.append("")
+
+    if dar.dependency_order:
+        lines.append("## Dependency Order")
+        lines.append("")
+        for d in dar.dependency_order:
+            lines.append(f"- {d.file_path} depends on {d.depends_on} — {d.reason}")
+        lines.append("")
+
+    if dar.critical_risks:
+        lines.append("## Critical Risks")
+        lines.append("")
+        for r in dar.critical_risks:
+            lines.append(f"- **[{r.severity}]** {r.risk} — {r.mitigation}")
+        lines.append("")
+
+    if dar.citations:
+        lines.append("## Citations")
+        lines.append("")
+        seen_urls: set[str] = set()
+        for c in dar.citations:
+            if c.docs_url in seen_urls:
+                continue
+            seen_urls.add(c.docs_url)
+            entry = f"- {c.dependency} — {c.docs_url}"
+            if c.version:
+                entry += f" — version: {c.version}"
+            if c.confirmed_patterns:
+                entry += f" — {c.confirmed_patterns}"
+            lines.append(entry)
+        lines.append("")
+
+    if dar.notes.strip():
+        lines.append("## Notes")
+        lines.append("")
+        lines.append(dar.notes.strip())
+        lines.append("")
+
+    if not lines:
+        return "(no design output)\n"
+    return "\n".join(lines).rstrip() + "\n"
+
+
+def _format_missing_files(missing: list[MissingFile]) -> str:
+    """Render the missing-files list as the numbered bullet string Phase 4
+    consumes as ``{missing_files}``. Empty string when no entries — matches
+    the prior behaviour of ``_extract_missing_files``.
+    """
+    if not missing:
+        return ""
+    rows: list[str] = []
+    for i, m in enumerate(missing, 1):
+        blocking = " [BLOCKING]" if m.blocking else ""
+        rows.append(f"{i}. {m.file_path} — {m.purpose}{blocking}")
+    return "\n".join(rows)
+
+
+def _format_dependency_order(dar: DesignAndRisks) -> str:
+    """Render dependency_order as a structured block for Phase 4 prompts.
+
+    Returns empty string when no dependency entries exist.
+    """
+    if not dar.dependency_order:
+        return ""
+    lines: list[str] = []
+    for d in dar.dependency_order:
+        lines.append(f"- {d.file_path} depends on {d.depends_on} — {d.reason}")
+    return "DEPENDENCY ORDER:\n" + "\n".join(lines) + "\n\n"
+
+
+def _format_naming_conventions_section(dar: DesignAndRisks) -> str:
+    """Render naming_conventions as a structured block for Phase 4 prompts.
+
+    Returns empty string when no naming conventions exist.
+    """
+    if not dar.naming_conventions:
+        return ""
+    lines: list[str] = []
+    lines.append("| category | pattern | source_file |")
+    lines.append("|---|---|---|")
+    for nc in dar.naming_conventions:
+        lines.append(f"| {nc.category} | {nc.pattern} | {nc.source_file} |")
+    return "NAMING CONVENTIONS:\n" + "\n".join(lines) + "\n\n"
+
+
+def _format_risk_assessment_section(dar: DesignAndRisks) -> str:
+    """Render critical_risks as a structured block for Phase 4/5 prompts.
+
+    Returns empty string when no risks exist.
+    """
+    if not dar.critical_risks:
+        return ""
+    lines: list[str] = []
+    for r in dar.critical_risks:
+        lines.append(f"- **[{r.severity}]** {r.risk} — {r.mitigation}")
+    return "RISK ASSESSMENT:\n" + "\n".join(lines) + "\n\n"
+
+
+# ── Phase 4 plan validation helpers ─────────────────────────────────────────
+#
+# All checks are set-membership against structured inputs from Phases 2 and
+# 3 — no regex, no parsing of LLM-generated prose. Warnings are logged and
+# also returned as a list so the caller can stash them on the plan for UI
+# surfacing.
+
+
+def _collect_known_paths(
+    file_summary: FileSummary | None,
+    dar: DesignAndRisks,
+) -> set[str]:
+    """Union of every file path the prior phases know about.
+
+    Returns an empty set when Phase 2 produced no structured output
+    (parallel path), which tells the caller to skip membership-based
+    checks cleanly rather than flag every path as invented.
+    """
+    if file_summary is None:
+        return set()
+    paths: set[str] = set()
+    for obs in file_summary.files_to_modify:
+        paths.add(obs.file_path)
+    for obs in file_summary.files_to_create:
+        paths.add(obs.file_path)
+    for obs in file_summary.files_read_for_context:
+        paths.add(obs.file_path)
+    for item in file_summary.missing_infrastructure:
+        paths.add(item.name)
+    for mf in dar.missing_files:
+        paths.add(mf.file_path)
+    return paths
+
+
+def _check_hallucinated_paths(
+    plan: ExecutionPlan,
+    known_paths: set[str],
+) -> tuple[list[str], bool]:
+    """Flag any step.file_path that is not in the prior-phase path universe.
+
+    Returns ``(warnings, is_blocking)``. Invented paths are blocking —
+    the plan references files the prior phases never identified.
+    """
     if not known_paths:
-        return []
+        return [], False
     plan_paths: set[str] = set()
     for step in plan.steps:
         if step.file_path:
@@ -1168,34 +1243,40 @@ def _check_hallucinated_paths(
         for target in step.may_change:
             if target.path:
                 plan_paths.add(target.path)
-    return [f"invented path: {p}" for p in sorted(plan_paths - known_paths)]
-
-
-def _uncovered_missing_files(
-    plan: ExecutionPlan,
-    dar: DesignAndRisks,
-) -> list[MissingFile]:
-    """Return MissingFile entries not covered by any plan step.
-
-    Returns the structured objects so the caller can branch on
-    ``.blocking`` (triggers auto-revision) versus non-blocking (warn only).
-    """
+    warnings = [f"invented path: {p}" for p in sorted(plan_paths - known_paths)]
+    return warnings, bool(warnings)
+
+
+def _uncovered_missing_files(
+    plan: ExecutionPlan,
+    dar: DesignAndRisks,
+) -> list[MissingFile]:
+    """Return MissingFile entries not covered by any plan step.
+
+    Returns the structured objects so the caller can branch on
+    ``.blocking`` (triggers auto-revision) versus non-blocking (warn only).
+    """
     step_paths = {s.file_path for s in plan.steps}
     for step in plan.steps:
         step_paths.update(target.path for target in step.may_change if target.path)
     return [mf for mf in dar.missing_files if mf.file_path not in step_paths]
-
-
+
+
 def _check_edit_create_consistency(
     plan: ExecutionPlan,
     file_summary: FileSummary | None,
     dar: DesignAndRisks,
-) -> list[str]:
-    """Flag edit_file on unknown paths and create_file on existing paths."""
-    if file_summary is None:
-        return []
-    to_modify: set[str] = {o.file_path for o in file_summary.files_to_modify}
-    to_modify |= {o.file_path for o in file_summary.files_read_for_context}
+) -> tuple[list[str], bool]:
+    """Flag edit_file on unknown paths and create_file on existing paths.
+
+    Returns ``(warnings, is_blocking)``. Tool/path mismatches are blocking
+    because the executor will fail if asked to edit a file it cannot find
+    or create a file that already exists.
+    """
+    if file_summary is None:
+        return [], False
+    to_modify: set[str] = {o.file_path for o in file_summary.files_to_modify}
+    to_modify |= {o.file_path for o in file_summary.files_read_for_context}
     to_create: set[str] = {o.file_path for o in file_summary.files_to_create}
     to_create |= {mf.file_path for mf in dar.missing_files}
     warnings: list[str] = []
@@ -1208,7 +1289,7 @@ def _check_edit_create_consistency(
                 continue
             if "edit_file" in s.allowed_tools or "create_file" in s.allowed_tools:
                 warnings.append(f"write target not found in prior-phase paths: {path}")
-    return warnings
+    return warnings, bool(warnings)
 
 
 def _sync_affected_files_from_steps(plan: ExecutionPlan) -> None:
@@ -1245,8 +1326,13 @@ def _step_contract_haystack(step: PlanStep) -> str:
 def _check_success_checks_cover_affected_files(
     plan: ExecutionPlan,
     file_summary: FileSummary | None,
-) -> list[str]:
-    """Warn when executable affected files have no test/success-check contract."""
+) -> tuple[list[str], bool]:
+    """Warn when executable affected files have no test/success-check contract.
+
+    Returns ``(warnings, is_blocking)``. Missing success checks are
+    non-blocking — the plan can still execute, but the user should be
+    aware of the gap.
+    """
     code_paths: set[str] = set()
     if file_summary is not None:
         for obs in file_summary.files_to_create:
@@ -1261,7 +1347,7 @@ def _check_success_checks_cover_affected_files(
                 code_paths.add(path)
 
     if not code_paths:
-        return []
+        return [], False
 
     warnings: list[str] = []
     for code_path in sorted(code_paths):
@@ -1274,14 +1360,21 @@ def _check_success_checks_cover_affected_files(
                 break
         if not covered:
             warnings.append(f"affected file has no success-check coverage: {code_path}")
-    return warnings
+    return warnings, False
 
 
-def _check_core_functionality_success_checked(plan: ExecutionPlan) -> list[str]:
-    """Warn when core-functionality tags lack regression-oriented checks."""
+def _check_core_functionality_success_checked(
+    plan: ExecutionPlan,
+) -> tuple[list[str], bool]:
+    """Warn when core-functionality tags lack regression-oriented checks.
+
+    Returns ``(warnings, is_blocking)``. Missing regression checks on
+    core functionality are non-blocking — the plan still executes but
+    the user is warned.
+    """
     tags = getattr(plan, "core_functionality", None) or []
     if not tags:
-        return []
+        return [], False
 
     confidence_rank = {"low": 0, "medium": 1, "high": 2}
     try:
@@ -1310,122 +1403,141 @@ def _check_core_functionality_success_checked(plan: ExecutionPlan) -> list[str]:
                 f"'{entity}' in {file_path} "
                 f"[{tag.source_signal}, confidence={tag.confidence}]"
             )
-    return warnings
+    return warnings, False
 
 
 def _run_plan_validations(
     plan: ExecutionPlan,
     file_summary: FileSummary | None,
     dar: DesignAndRisks,
-) -> list[str]:
-    """Run every validator, log each warning, and return the full list.
-
-    Shared between the pre- and post-revision passes so the logic stays
-    in one place.
-    """
-    warnings: list[str] = []
-    known = _collect_known_paths(file_summary, dar)
-    warnings.extend(_check_hallucinated_paths(plan, known))
-    warnings.extend(_check_edit_create_consistency(plan, file_summary, dar))
-    warnings.extend(_check_success_checks_cover_affected_files(plan, file_summary))
-    warnings.extend(_check_core_functionality_success_checked(plan))
-    for mf in _uncovered_missing_files(plan, dar):
+) -> tuple[list[str], bool]:
+    """Run every validator, log each warning, and return ``(warnings, is_blocking)``.
+
+    Shared between the pre- and post-revision passes so the logic stays
+    in one place. The ``is_blocking`` flag is True when any blocking
+    validator produced warnings, indicating the plan should be revised.
+    """
+    warnings: list[str] = []
+    is_blocking = False
+    known = _collect_known_paths(file_summary, dar)
+
+    w, b = _check_hallucinated_paths(plan, known)
+    warnings.extend(w)
+    is_blocking = is_blocking or b
+
+    w, b = _check_edit_create_consistency(plan, file_summary, dar)
+    warnings.extend(w)
+    is_blocking = is_blocking or b
+
+    w, b = _check_success_checks_cover_affected_files(plan, file_summary)
+    warnings.extend(w)
+    # Non-blocking — do not flip is_blocking
+
+    w, b = _check_core_functionality_success_checked(plan)
+    warnings.extend(w)
+    # Non-blocking — do not flip is_blocking
+
+    uncovered = _uncovered_missing_files(plan, dar)
+    for mf in uncovered:
         tag = " [BLOCKING]" if mf.blocking else ""
         warnings.append(f"uncovered missing file: {mf.file_path} — {mf.purpose}{tag}")
-    for w in warnings:
-        logger.warning("Phase 4 plan validation — %s", w)
-    return warnings
-
-
-# ── Phase 5 helpers ─────────────────────────────────────────────────────────
-#
-# Inputs derived from structured Phase 2/3 outputs, so Phase 5's prompt can
-# target test generation precisely. All three helpers operate on structured
-# Pydantic objects; no regex or LLM-prose parsing.
-
-
-def _build_verification_targets(
-    file_summary: FileSummary | None,
-    dar: DesignAndRisks,
-) -> str:
-    """Markdown bullet list of files that need test coverage.
-
-    Sources: ``dar.change_designs`` (non-obvious files Phase 3 designed)
-    plus ``file_summary.files_to_create`` (new files Phase 2 identified).
-    Deduplicates by path, preserves input order. Returns empty string
-    when neither source has entries so the prompt can omit the section
-    gracefully.
-    """
-    paths: list[str] = []
-    seen: set[str] = set()
-    for cd in dar.change_designs:
-        if cd.file_path and cd.file_path not in seen:
-            paths.append(cd.file_path)
-            seen.add(cd.file_path)
-    if file_summary is not None:
-        for obs in file_summary.files_to_create:
-            if obs.file_path and obs.file_path not in seen:
-                paths.append(obs.file_path)
-                seen.add(obs.file_path)
-    if not paths:
-        return ""
-    return "\n".join(f"- {p}" for p in paths)
-
-
-def _build_security_concerns(dar: DesignAndRisks) -> str:
-    """Markdown bullet list of Phase 3 critical risks for Phase 5 to
-    cover with tests.
-
-    Returns empty string when ``critical_risks`` is empty so the prompt
-    can omit the section gracefully.
-    """
-    if not dar.critical_risks:
-        return ""
-    return "\n".join(
-        f"- **[{r.severity}]** {r.risk} — mitigation: {r.mitigation}" for r in dar.critical_risks
-    )
-
-
-def _format_testing_inventory(file_summary: FileSummary | None) -> str:
-    """Render ``FileSummary.testing_inventory`` (Layer 6) for Phase 5.
-
-    Returns a concise markdown block with framework, directory,
-    assertion style, existing regression files, and per-affected-file
-    coverage. Returns the ``(none)`` sentinel when Phase 2 did not
-    populate the field so the prompt reads cleanly.
-
-    Phase 2 population lands in a later PR; this helper keeps the
-    Phase 5 call site stable until then.
-    """
-    inv = getattr(file_summary, "testing_inventory", None) if file_summary else None
-    if inv is None:
-        return (
-            "(none reported by Phase 2 — detect the framework and "
-            "directory from FILE SUMMARY yourself.)"
-        )
-    lines: list[str] = []
-    if inv.test_framework:
-        lines.append(f"- Framework: {inv.test_framework}")
-    if inv.test_directory:
-        lines.append(f"- Directory: {inv.test_directory}")
-    if inv.test_file_pattern:
-        lines.append(f"- File pattern: {inv.test_file_pattern}")
-    if inv.assertion_style_excerpt:
-        lines.append("- Assertion style excerpt:\n```\n" + inv.assertion_style_excerpt + "\n```")
-    if inv.existing_regression_files:
-        lines.append("- Existing regression files (MUST NOT be modified):")
-        for p in inv.existing_regression_files:
-            lines.append(f"  - {p}")
-    if inv.affected_files_existing_coverage:
-        lines.append("- Existing coverage for affected files:")
-        for cov in inv.affected_files_existing_coverage:
-            tests = ", ".join(cov.test_files) if cov.test_files else "(none)"
-            lines.append(f"  - {cov.source_file} → {tests}")
-    if inv.notes:
-        lines.append(f"- Notes: {inv.notes}")
-    return "\n".join(lines) if lines else "(empty)"
-
-
+        if mf.blocking:
+            is_blocking = True
+
+    for w in warnings:
+        logger.warning("Phase 4 plan validation — %s", w)
+    return warnings, is_blocking
+
+
+# ── Phase 5 helpers ─────────────────────────────────────────────────────────
+#
+# Inputs derived from structured Phase 2/3 outputs, so Phase 5's prompt can
+# target test generation precisely. All three helpers operate on structured
+# Pydantic objects; no regex or LLM-prose parsing.
+
+
+def _build_verification_targets(
+    file_summary: FileSummary | None,
+    dar: DesignAndRisks,
+) -> str:
+    """Markdown bullet list of files that need test coverage.
+
+    Sources: ``dar.change_designs`` (non-obvious files Phase 3 designed)
+    plus ``file_summary.files_to_create`` (new files Phase 2 identified).
+    Deduplicates by path, preserves input order. Returns empty string
+    when neither source has entries so the prompt can omit the section
+    gracefully.
+    """
+    paths: list[str] = []
+    seen: set[str] = set()
+    for cd in dar.change_designs:
+        if cd.file_path and cd.file_path not in seen:
+            paths.append(cd.file_path)
+            seen.add(cd.file_path)
+    if file_summary is not None:
+        for obs in file_summary.files_to_create:
+            if obs.file_path and obs.file_path not in seen:
+                paths.append(obs.file_path)
+                seen.add(obs.file_path)
+    if not paths:
+        return ""
+    return "\n".join(f"- {p}" for p in paths)
+
+
+def _build_security_concerns(dar: DesignAndRisks) -> str:
+    """Markdown bullet list of Phase 3 critical risks for Phase 5 to
+    cover with tests.
+
+    Returns empty string when ``critical_risks`` is empty so the prompt
+    can omit the section gracefully.
+    """
+    if not dar.critical_risks:
+        return ""
+    return "\n".join(
+        f"- **[{r.severity}]** {r.risk} — mitigation: {r.mitigation}" for r in dar.critical_risks
+    )
+
+
+def _format_testing_inventory(file_summary: FileSummary | None) -> str:
+    """Render ``FileSummary.testing_inventory`` (Layer 6) for Phase 5.
+
+    Returns a concise markdown block with framework, directory,
+    assertion style, existing regression files, and per-affected-file
+    coverage. Returns the ``(none)`` sentinel when Phase 2 did not
+    populate the field so the prompt reads cleanly.
+
+    Phase 2 population lands in a later PR; this helper keeps the
+    Phase 5 call site stable until then.
+    """
+    inv = getattr(file_summary, "testing_inventory", None) if file_summary else None
+    if inv is None:
+        return (
+            "(none reported by Phase 2 — detect the framework and "
+            "directory from FILE SUMMARY yourself.)"
+        )
+    lines: list[str] = []
+    if inv.test_framework:
+        lines.append(f"- Framework: {inv.test_framework}")
+    if inv.test_directory:
+        lines.append(f"- Directory: {inv.test_directory}")
+    if inv.test_file_pattern:
+        lines.append(f"- File pattern: {inv.test_file_pattern}")
+    if inv.assertion_style_excerpt:
+        lines.append("- Assertion style excerpt:\n```\n" + inv.assertion_style_excerpt + "\n```")
+    if inv.existing_regression_files:
+        lines.append("- Existing regression files (MUST NOT be modified):")
+        for p in inv.existing_regression_files:
+            lines.append(f"  - {p}")
+    if inv.affected_files_existing_coverage:
+        lines.append("- Existing coverage for affected files:")
+        for cov in inv.affected_files_existing_coverage:
+            tests = ", ".join(cov.test_files) if cov.test_files else "(none)"
+            lines.append(f"  - {cov.source_file} → {tests}")
+    if inv.notes:
+        lines.append(f"- Notes: {inv.notes}")
+    return "\n".join(lines) if lines else "(empty)"
+
+
 def _format_core_functionality(source: "ExecutionPlan | DesignAndRisks") -> str:
     """Render core-functionality tags for planning prompts.
 
@@ -1433,253 +1545,253 @@ def _format_core_functionality(source: "ExecutionPlan | DesignAndRisks") -> str:
     feature flag is disabled.
     """
     tags = getattr(source, "core_functionality", []) or []
-    if not tags:
-        return "(none tagged — no mandatory regression tests required by Phase 3.)"
-    lines: list[str] = []
-    for tag in tags:
-        lines.append(
-            f"- **{tag.entity}** in `{tag.file_path}` "
-            f"[{tag.source_signal}, confidence={tag.confidence}] "
-            f"— {tag.reason}"
-        )
-    return "\n".join(lines)
-
-
-_TEST_PATH_TOKENS: tuple[str, ...] = ("test", "spec")
-"""Common test-file naming tokens across languages: ``test`` (Python,
-Go, Rust, Java, Ruby minitest, JS *.test.js) and ``spec`` (Ruby RSpec,
-JS/TS *.spec.ts, Elixir). Case-insensitive ``in`` check against the
-file path — captures ``tests/``, ``spec/``, ``__tests__/``,
-``*_test.go``, ``*.spec.ts``, ``TestFoo.java``, etc."""
-
-
-def _check_test_path_conventions(
-    verification: VerificationPlan,
-    file_summary: FileSummary | None,
-) -> list[str]:
-    """Flag Phase 5 ``create_file`` steps with paths that violate test
-    conventions.
-
-    A path passes if it contains any common test token (``test`` or
-    ``spec``, case-insensitive) OR starts with any directory prefix
-    learned from ``file_summary.files_read_for_context`` for files
-    that themselves contain a test token — so repos with unusual test
-    dirs can be accepted when Phase 2 read one of their files as a
-    pattern reference. Pure string-contains and prefix checks over
-    structured fields; no regex on LLM prose.
-    """
-    warnings: list[str] = []
-    learned_prefixes: set[str] = set()
-    if file_summary is not None:
-        for obs in file_summary.files_read_for_context:
-            p = (obs.file_path or "").lower()
-            if "/" not in p:
-                continue
-            if any(tok in p for tok in _TEST_PATH_TOKENS):
-                learned_prefixes.add(p.rsplit("/", 1)[0])
-    for step in verification.steps:
-        if step.tool != "create_file" or not step.file_path:
-            continue
-        low = step.file_path.lower()
-        if any(tok in low for tok in _TEST_PATH_TOKENS):
-            continue
-        if any(low.startswith(pfx) for pfx in learned_prefixes):
-            continue
-        warnings.append(f"test step path outside test convention: {step.file_path}")
-    for w in warnings:
-        logger.warning("Phase 5 plan validation — %s", w)
-    return warnings
-
-
-# Layer 2 — files that would benefit from a test. We only expand
-# coverage checks to files with executable extensions. Docs / config /
-# lockfiles / generated assets are skipped.
-_EXECUTABLE_EXTENSIONS: frozenset[str] = frozenset(
-    {
-        ".py",
-        ".pyi",
-        ".ts",
-        ".tsx",
-        ".js",
-        ".jsx",
-        ".mjs",
-        ".cjs",
-        ".go",
-        ".rs",
-        ".java",
-        ".kt",
-        ".kts",
-        ".cs",
-        ".fs",
-        ".vb",
-        ".rb",
-        ".php",
-        ".swift",
-        ".m",
-        ".mm",
-        ".cpp",
-        ".cxx",
-        ".cc",
-        ".c",
-        ".h",
-        ".hpp",
-        ".hh",
-        ".ex",
-        ".exs",
-        ".erl",
-        ".hrl",
-        ".scala",
-        ".clj",
-        ".cljs",
-        ".lua",
-        ".dart",
-        ".ml",
-        ".mli",
-        ".hs",
-        ".r",
-        ".nim",
-        ".zig",
-        ".v",
-        ".d",
-    }
-)
-
-
-def _has_executable_extension(path: str) -> bool:
-    lower = path.lower()
-    return any(lower.endswith(ext) for ext in _EXECUTABLE_EXTENSIONS)
-
-
-def _check_core_functionality_covered(
-    verification: VerificationPlan,
-    plan: ExecutionPlan,
-) -> list[str]:
-    """Flag core-functionality tags missing a regression test step.
-
-    Layer 9 mandates that every ``plan.core_functionality`` tag whose
-    confidence is at or above
-    ``settings.core_functionality_min_confidence`` receives a
-    regression-file test step in Phase 5. Tags below the confidence
-    threshold are advisory only and do not trigger warnings.
-
-    A step qualifies as a regression test for a tag when its
-    ``file_path`` matches the regression convention AND its
-    ``file_path + instruction + context`` haystack mentions the tag's
-    entity or file_path. Warnings are non-blocking and go to
-    ``plan.plan_validation_warnings``.
-    """
-    from lean_ai.tools.regression_guard import is_regression_test_path
-
-    tags = getattr(plan, "core_functionality", None) or []
-    if not tags:
-        return []
-
-    # Confidence gating — min_confidence is "low" | "medium" | "high".
-    confidence_rank = {"low": 0, "medium": 1, "high": 2}
-    try:
-        min_rank = confidence_rank[settings.core_functionality_min_confidence]
-    except KeyError:
-        min_rank = confidence_rank["medium"]
-
-    enforced_tags = [t for t in tags if confidence_rank.get(t.confidence, 1) >= min_rank]
-    if not enforced_tags:
-        return []
-
-    # Build haystack of regression-convention test steps only.
-    haystacks: list[tuple[str, str]] = []
-    for step in verification.steps:
-        if step.tool != "create_file" or not step.file_path:
-            continue
-        if not is_regression_test_path(step.file_path):
-            continue
-        haystack = "\n".join(
-            [
-                step.file_path or "",
-                step.instruction or "",
-                step.context or "",
-                step.reason or "",
-            ]
-        )
-        haystacks.append((step.file_path, haystack))
-
-    warnings: list[str] = []
-    for tag in enforced_tags:
-        entity = tag.entity.strip()
-        file_path = tag.file_path.strip()
-        covered = any(
-            (entity and entity in hay) or (file_path and file_path in hay) for _, hay in haystacks
-        )
-        if not covered:
-            warnings.append(
-                f"core-functionality tag missing regression test: "
-                f"'{entity}' in {file_path} "
-                f"[{tag.source_signal}, confidence={tag.confidence}]"
-            )
-
-    for w in warnings:
-        logger.warning("Phase 5 core-functionality — %s", w)
-    return warnings
-
-
-def _check_affected_files_covered(
-    verification: VerificationPlan,
-    plan: ExecutionPlan,
-    file_summary: FileSummary | None,
-) -> list[str]:
-    """Flag plan files that receive no test coverage.
-
-    For every file in ``plan.affected_files`` that has an executable
-    extension AND corresponds to a ``FileSummary.files_to_create`` or
-    ``files_to_modify`` observation, verify at least one ``create_file``
-    step in ``verification.steps`` references that path in
-    ``file_path``, ``instruction``, or ``context``. Uncovered paths
-    append a warning to ``plan.plan_validation_warnings``.
-
-    This is intentionally a *warning*, not a blocker — the plan still
-    proceeds and the user sees the warning on the approval screen, so
-    they can decide whether the gap is acceptable (e.g. trivial data
-    classes, pure config).
-    """
-    # Set of paths Phase 2 said this plan will touch as code. Fall back
-    # to ``affected_files`` when no FileSummary was produced (parallel
-    # Phase 2 path returns None).
-    code_paths: set[str] = set()
-    if file_summary is not None:
-        for obs in file_summary.files_to_create:
-            if obs.file_path and _has_executable_extension(obs.file_path):
-                code_paths.add(obs.file_path)
-        for obs in file_summary.files_to_modify:
-            if obs.file_path and _has_executable_extension(obs.file_path):
-                code_paths.add(obs.file_path)
-    else:
-        for p in plan.affected_files:
-            if _has_executable_extension(p):
-                code_paths.add(p)
-
-    if not code_paths:
-        return []
-
-    # Build a haystack of everything Phase 5's create_file test steps
-    # reference. Any code path that appears anywhere in this haystack
-    # is considered covered.
-    haystacks: list[str] = []
-    for step in verification.steps:
-        if step.tool != "create_file":
-            continue
-        haystacks.append(step.file_path or "")
-        haystacks.append(step.instruction or "")
-        haystacks.append(step.context or "")
-    combined = "\n".join(haystacks)
-
-    warnings: list[str] = []
-    for code_path in sorted(code_paths):
-        # Treat the bare filename as a coarse match too, so a test step
-        # that says "test the foo() function from foo.py" counts.
-        filename = code_path.rsplit("/", 1)[-1]
-        if code_path in combined or filename in combined:
-            continue
-        warnings.append(f"affected file has no test coverage: {code_path}")
-
-    for w in warnings:
-        logger.warning("Phase 5 coverage — %s", w)
-    return warnings
+    if not tags:
+        return "(none tagged — no mandatory regression tests required by Phase 3.)"
+    lines: list[str] = []
+    for tag in tags:
+        lines.append(
+            f"- **{tag.entity}** in `{tag.file_path}` "
+            f"[{tag.source_signal}, confidence={tag.confidence}] "
+            f"— {tag.reason}"
+        )
+    return "\n".join(lines)
+
+
+_TEST_PATH_TOKENS: tuple[str, ...] = ("test", "spec")
+"""Common test-file naming tokens across languages: ``test`` (Python,
+Go, Rust, Java, Ruby minitest, JS *.test.js) and ``spec`` (Ruby RSpec,
+JS/TS *.spec.ts, Elixir). Case-insensitive ``in`` check against the
+file path — captures ``tests/``, ``spec/``, ``__tests__/``,
+``*_test.go``, ``*.spec.ts``, ``TestFoo.java``, etc."""
+
+
+def _check_test_path_conventions(
+    verification: VerificationPlan,
+    file_summary: FileSummary | None,
+) -> list[str]:
+    """Flag Phase 5 ``create_file`` steps with paths that violate test
+    conventions.
+
+    A path passes if it contains any common test token (``test`` or
+    ``spec``, case-insensitive) OR starts with any directory prefix
+    learned from ``file_summary.files_read_for_context`` for files
+    that themselves contain a test token — so repos with unusual test
+    dirs can be accepted when Phase 2 read one of their files as a
+    pattern reference. Pure string-contains and prefix checks over
+    structured fields; no regex on LLM prose.
+    """
+    warnings: list[str] = []
+    learned_prefixes: set[str] = set()
+    if file_summary is not None:
+        for obs in file_summary.files_read_for_context:
+            p = (obs.file_path or "").lower()
+            if "/" not in p:
+                continue
+            if any(tok in p for tok in _TEST_PATH_TOKENS):
+                learned_prefixes.add(p.rsplit("/", 1)[0])
+    for step in verification.steps:
+        if step.tool != "create_file" or not step.file_path:
+            continue
+        low = step.file_path.lower()
+        if any(tok in low for tok in _TEST_PATH_TOKENS):
+            continue
+        if any(low.startswith(pfx) for pfx in learned_prefixes):
+            continue
+        warnings.append(f"test step path outside test convention: {step.file_path}")
+    for w in warnings:
+        logger.warning("Phase 5 plan validation — %s", w)
+    return warnings
+
+
+# Layer 2 — files that would benefit from a test. We only expand
+# coverage checks to files with executable extensions. Docs / config /
+# lockfiles / generated assets are skipped.
+_EXECUTABLE_EXTENSIONS: frozenset[str] = frozenset(
+    {
+        ".py",
+        ".pyi",
+        ".ts",
+        ".tsx",
+        ".js",
+        ".jsx",
+        ".mjs",
+        ".cjs",
+        ".go",
+        ".rs",
+        ".java",
+        ".kt",
+        ".kts",
+        ".cs",
+        ".fs",
+        ".vb",
+        ".rb",
+        ".php",
+        ".swift",
+        ".m",
+        ".mm",
+        ".cpp",
+        ".cxx",
+        ".cc",
+        ".c",
+        ".h",
+        ".hpp",
+        ".hh",
+        ".ex",
+        ".exs",
+        ".erl",
+        ".hrl",
+        ".scala",
+        ".clj",
+        ".cljs",
+        ".lua",
+        ".dart",
+        ".ml",
+        ".mli",
+        ".hs",
+        ".r",
+        ".nim",
+        ".zig",
+        ".v",
+        ".d",
+    }
+)
+
+
+def _has_executable_extension(path: str) -> bool:
+    lower = path.lower()
+    return any(lower.endswith(ext) for ext in _EXECUTABLE_EXTENSIONS)
+
+
+def _check_core_functionality_covered(
+    verification: VerificationPlan,
+    plan: ExecutionPlan,
+) -> list[str]:
+    """Flag core-functionality tags missing a regression test step.
+
+    Layer 9 mandates that every ``plan.core_functionality`` tag whose
+    confidence is at or above
+    ``settings.core_functionality_min_confidence`` receives a
+    regression-file test step in Phase 5. Tags below the confidence
+    threshold are advisory only and do not trigger warnings.
+
+    A step qualifies as a regression test for a tag when its
+    ``file_path`` matches the regression convention AND its
+    ``file_path + instruction + context`` haystack mentions the tag's
+    entity or file_path. Warnings are non-blocking and go to
+    ``plan.plan_validation_warnings``.
+    """
+    from lean_ai.tools.regression_guard import is_regression_test_path
+
+    tags = getattr(plan, "core_functionality", None) or []
+    if not tags:
+        return []
+
+    # Confidence gating — min_confidence is "low" | "medium" | "high".
+    confidence_rank = {"low": 0, "medium": 1, "high": 2}
+    try:
+        min_rank = confidence_rank[settings.core_functionality_min_confidence]
+    except KeyError:
+        min_rank = confidence_rank["medium"]
+
+    enforced_tags = [t for t in tags if confidence_rank.get(t.confidence, 1) >= min_rank]
+    if not enforced_tags:
+        return []
+
+    # Build haystack of regression-convention test steps only.
+    haystacks: list[tuple[str, str]] = []
+    for step in verification.steps:
+        if step.tool != "create_file" or not step.file_path:
+            continue
+        if not is_regression_test_path(step.file_path):
+            continue
+        haystack = "\n".join(
+            [
+                step.file_path or "",
+                step.instruction or "",
+                step.context or "",
+                step.reason or "",
+            ]
+        )
+        haystacks.append((step.file_path, haystack))
+
+    warnings: list[str] = []
+    for tag in enforced_tags:
+        entity = tag.entity.strip()
+        file_path = tag.file_path.strip()
+        covered = any(
+            (entity and entity in hay) or (file_path and file_path in hay) for _, hay in haystacks
+        )
+        if not covered:
+            warnings.append(
+                f"core-functionality tag missing regression test: "
+                f"'{entity}' in {file_path} "
+                f"[{tag.source_signal}, confidence={tag.confidence}]"
+            )
+
+    for w in warnings:
+        logger.warning("Phase 5 core-functionality — %s", w)
+    return warnings
+
+
+def _check_affected_files_covered(
+    verification: VerificationPlan,
+    plan: ExecutionPlan,
+    file_summary: FileSummary | None,
+) -> list[str]:
+    """Flag plan files that receive no test coverage.
+
+    For every file in ``plan.affected_files`` that has an executable
+    extension AND corresponds to a ``FileSummary.files_to_create`` or
+    ``files_to_modify`` observation, verify at least one ``create_file``
+    step in ``verification.steps`` references that path in
+    ``file_path``, ``instruction``, or ``context``. Uncovered paths
+    append a warning to ``plan.plan_validation_warnings``.
+
+    This is intentionally a *warning*, not a blocker — the plan still
+    proceeds and the user sees the warning on the approval screen, so
+    they can decide whether the gap is acceptable (e.g. trivial data
+    classes, pure config).
+    """
+    # Set of paths Phase 2 said this plan will touch as code. Fall back
+    # to ``affected_files`` when no FileSummary was produced (parallel
+    # Phase 2 path returns None).
+    code_paths: set[str] = set()
+    if file_summary is not None:
+        for obs in file_summary.files_to_create:
+            if obs.file_path and _has_executable_extension(obs.file_path):
+                code_paths.add(obs.file_path)
+        for obs in file_summary.files_to_modify:
+            if obs.file_path and _has_executable_extension(obs.file_path):
+                code_paths.add(obs.file_path)
+    else:
+        for p in plan.affected_files:
+            if _has_executable_extension(p):
+                code_paths.add(p)
+
+    if not code_paths:
+        return []
+
+    # Build a haystack of everything Phase 5's create_file test steps
+    # reference. Any code path that appears anywhere in this haystack
+    # is considered covered.
+    haystacks: list[str] = []
+    for step in verification.steps:
+        if step.tool != "create_file":
+            continue
+        haystacks.append(step.file_path or "")
+        haystacks.append(step.instruction or "")
+        haystacks.append(step.context or "")
+    combined = "\n".join(haystacks)
+
+    warnings: list[str] = []
+    for code_path in sorted(code_paths):
+        # Treat the bare filename as a coarse match too, so a test step
+        # that says "test the foo() function from foo.py" counts.
+        filename = code_path.rsplit("/", 1)[-1]
+        if code_path in combined or filename in combined:
+            continue
+        warnings.append(f"affected file has no test coverage: {code_path}")
+
+    for w in warnings:
+        logger.warning("Phase 5 coverage — %s", w)
+    return warnings
diff --git a/backend/src/lean_ai/llm/planner_exploration.py b/backend/src/lean_ai/llm/planner_exploration.py
index fb729fd..d91c700 100644
--- a/backend/src/lean_ai/llm/planner_exploration.py
+++ b/backend/src/lean_ai/llm/planner_exploration.py
@@ -397,10 +397,10 @@ async def run_phase2_exploration(
     """Run Phase 2: File identification + content reading.
 
     Returns ``(FileSummary | None, file_identification_markdown, elapsed)``.
-    The structured ``FileSummary`` is non-None only on the serial path
-    (``num_parallel=1``) where the synthesis pass succeeded. Parallel
-    mode returns ``None`` for the structured object since Phase 2a/2b
-    produce free-form text. Phase 4 validators skip cleanly when the
+    The structured ``FileSummary`` is non-None when the synthesis pass
+    succeeded (both serial and parallel paths now run synthesis). On
+    synthesis failure the object is ``None`` and the raw prose is used
+    as the markdown handoff. Phase 4 validators skip cleanly when the
     object is ``None``.
     """
     t0 = time.monotonic()
@@ -431,10 +431,7 @@ async def run_phase2_exploration(
     file_summary_obj: FileSummary | None = None
 
     if settings.num_parallel >= 2:
-        # TODO(parallel-phase2): parallel exploration still returns a prose
-        # handoff and skips the observation-backed FileSummary contract. Keep
-        # this deferred while the single-model Phase 2 path is hardened first.
-        file_identification = await _run_parallel_exploration(
+        file_identification, file_summary_obj = await _run_parallel_exploration(
             task=task,
             scope=scope,
             context=context,
@@ -554,7 +551,7 @@ async def _run_parallel_exploration(
     on_metrics: Callable | None,
     on_metrics_reset: Callable | None,
     t0: float,
-) -> str:
+) -> tuple[str, FileSummary | None]:
     """Parallel Phase 2: fan-out scan then merge deep-dive reads."""
     # Phase 2a: broad scan — identify files without reading contents
     scan_tools = [
@@ -617,7 +614,7 @@ async def _run_parallel_exploration(
     logger.info("Phase 2a scan identified %d file paths", len(file_paths))
 
     if not file_paths:
-        return scan_output
+        return scan_output, None
 
     # Phase 2b: parallel deep-dive — read identified files
     n_workers = min(len(file_paths), settings.num_parallel)
@@ -625,6 +622,8 @@ async def _run_parallel_exploration(
 
     async def _deep_dive(file_subset: list[str]) -> str:
         """Read a subset of files and produce a summary."""
+        from lean_ai.llm.tool_definitions import RECORD_FILE_OBSERVATION_TOOL
+
         file_list = "\n".join(f"- {f}" for f in file_subset)
         dive_messages = [
             {"role": "system", "content": PLAN_EXPLORATION_SYSTEM_PROMPT},
@@ -636,7 +635,8 @@ async def _run_parallel_exploration(
                     f"classes/functions with signatures, and what needs to "
                     f"change for the task.\n\nTask: {task}\n\n"
                     f"Files to read:\n{file_list}\n\n"
-                    f"Call task_complete when done."
+                    f"Call record_file_observation for every relevant file "
+                    f"you read, then call task_complete when done."
                 ),
             },
         ]
@@ -650,6 +650,7 @@ async def _run_parallel_exploration(
                 "task_complete",
             )
         ]
+        read_tools.append(RECORD_FILE_OBSERVATION_TOOL)
         max_turns = max(10, 30 // n_workers)
         _, dive_output = await explorer.chat_with_tools(
             messages=dive_messages,
@@ -687,9 +688,26 @@ async def _run_parallel_exploration(
             good_results.append(result)
 
     if not good_results:
-        return scan_output
+        return scan_output, None
+
+    merged_prose = scan_output + "\n\n" + "\n\n".join(good_results)
+
+    # Synthesis pass: coerce observations recorded by deep-dive workers
+    # into a validated FileSummary so downstream phases get structured data.
+    file_summary_obj, file_identification = await _synthesize_file_summary(
+        task=task,
+        scope=scope,
+        exploration_output=merged_prose,
+        repo_root=repo_root,
+        session_id=session_id,
+        explorer=explorer,
+        phase_max_tokens=phase_max_tokens,
+        on_thinking=on_thinking,
+        on_metrics=on_metrics,
+        on_metrics_reset=on_metrics_reset,
+    )
 
-    return scan_output + "\n\n" + "\n\n".join(good_results)
+    return file_identification, file_summary_obj
 
 
 async def _run_serial_exploration(
diff --git a/backend/src/lean_ai/llm/planner_helpers.py b/backend/src/lean_ai/llm/planner_helpers.py
index 7997dfa..ab5d97a 100644
--- a/backend/src/lean_ai/llm/planner_helpers.py
+++ b/backend/src/lean_ai/llm/planner_helpers.py
@@ -822,9 +822,16 @@ async def _revise_plan(
     on_thinking: "Callable | None" = None,
     on_metrics: "Callable | None" = None,
     on_metrics_reset: "Callable | None" = None,
+    file_summary: str = "",
+    design_and_risks: str = "",
+    scope: str = "",
 ) -> ExecutionPlan:
     """Revise an existing plan based on user feedback.
 
+    Rebuilds the revision prompt to mirror the Phase 4 assembly prompt
+    structure so that file_summary, design_and_risks, and scope are
+    reinjected as full context sections — not lost at phase boundaries.
+
     Args:
         task: The original task.
         revision_context: Previous plan JSON + user feedback.
@@ -832,6 +839,9 @@ async def _revise_plan(
         context: Project context.
         ws: Optional WebSocket for progress.
         expert_llm_client: Optional expert LLM client for reasoning-heavy work.
+        file_summary: Formatted file summary from Phase 2 exploration.
+        design_and_risks: Formatted design and risk synthesis from Phase 3.
+        scope: Formatted scope document from Phase 1.
 
     Returns:
         Revised ExecutionPlan.
@@ -855,24 +865,48 @@ async def _revise_plan(
         model=expert.model_name,
     )
     logger.info("Plan revision")
+
+    # Build the revision prompt using the same Phase 4 assembly template
+    # so that file_summary, design_and_risks, and scope are reinjected
+    # as full structured context — not lost at phase boundaries.
+    project_context_block = (
+        f"PROJECT CONTEXT:\n{context}\n\n" if context else ""
+    )
+    assembly_prompt = registry.format(
+        "planning.assembly_user",
+        task=task,
+        design_and_risks=design_and_risks,
+        file_summary=file_summary,
+        project_context=project_context_block,
+        scope=scope,
+        missing_files="",
+        test_command="(none configured yet)",
+        testing_inventory="(none available during revision)",
+        verification_targets="(derive from affected behavioral files)",
+        security_concerns="(none identified during revision)",
+        core_functionality="(none identified during revision)",
+        dependency_order="",
+        naming_conventions="",
+        risk_assessment="",
+    )
+    revision_user_content = (
+        f"{assembly_prompt}\n\n"
+        f"REVISION CONTEXT:\n{revision_context}\n\n"
+        "Revise the plan based on the user's feedback. "
+        "Make targeted edits — don't rewrite from scratch. "
+        "Keep the Phase 4 job-contract format: each step needs "
+        "job, inputs, may_change, must_not_change, allowed_tools, "
+        "output_shape, success_checks, and blocked_protocol. "
+        "Legacy tool/file_path/instruction/context may remain as "
+        "short compatibility hints only."
+    )
     try:
         plan = await _chat_structured_with_repair(
             messages=[
                 {"role": "system", "content": PLAN_ASSEMBLY_SYSTEM_PROMPT},
                 {
                     "role": "user",
-                    "content": (
-                        f"TASK: {task}\n\n"
-                        f"CODEBASE CONTEXT:\n{context}\n\n"
-                        f"REVISION CONTEXT:\n{revision_context}\n\n"
-                        "Revise the plan based on the user's feedback. "
-                        "Make targeted edits — don't rewrite from scratch. "
-                        "Keep the Phase 4 job-contract format: each step needs "
-                        "job, inputs, may_change, must_not_change, allowed_tools, "
-                        "output_shape, success_checks, and blocked_protocol. "
-                        "Legacy tool/file_path/instruction/context may remain as "
-                        "short compatibility hints only."
-                    ),
+                    "content": revision_user_content,
                 },
             ],
             schema=ExecutionPlan,
diff --git a/backend/src/lean_ai/llm/prompt_defaults.py b/backend/src/lean_ai/llm/prompt_defaults.py
index 7039880..0731a99 100644
--- a/backend/src/lean_ai/llm/prompt_defaults.py
+++ b/backend/src/lean_ai/llm/prompt_defaults.py
@@ -851,6 +851,9 @@ def register_prompt_defaults(reg: PromptRegistry) -> None:
                 "core_functionality",
                 "next_step",
                 "run_tests_rule",
+                "dependency_order",
+                "naming_conventions",
+                "risk_assessment",
             ],
             warning=(
                 "Kept in parity with planning.verification_user_tdd — most "
@@ -879,6 +882,22 @@ def register_prompt_defaults(reg: PromptRegistry) -> None:
                 "coverage):\n{security_concerns}\n"
                 "(If this list is empty, apply general SECURITY-category "
                 "judgment to the files under test.)\n\n"
+                "DEPENDENCY ORDER (execution sequence constraints):\n"
+                "{dependency_order}\n"
+                "(Use this to determine which test files can be created "
+                "independently and which must wait for implementation "
+                "steps. Tests for dependent files should be ordered "
+                "after their dependencies.)\n\n"
+                "NAMING CONVENTIONS (from Phase 3/4):\n"
+                "{naming_conventions}\n"
+                "(Use these naming patterns for all test file names, "
+                "test class names, and test function names. Do NOT "
+                "invent alternate naming schemes.)\n\n"
+                "RISK ASSESSMENT (from Phase 3 design):\n"
+                "{risk_assessment}\n"
+                "(Prioritise test coverage for high-risk areas. If a "
+                "risk involves external I/O, ensure the test uses the "
+                "appropriate seam or mock.)\n\n"
                 "Produce ONLY the verification steps that should run "
                 "AFTER implementation.\n\n"
                 "REQUIRED OUTPUT SHAPE:\n"
@@ -998,6 +1017,9 @@ def register_prompt_defaults(reg: PromptRegistry) -> None:
                 "security_concerns",
                 "core_functionality",
                 "next_step",
+                "dependency_order",
+                "naming_conventions",
+                "risk_assessment",
             ],
             warning=(
                 "Kept in parity with planning.verification_user_normal — most "
@@ -1029,6 +1051,22 @@ def register_prompt_defaults(reg: PromptRegistry) -> None:
                 "coverage):\n{security_concerns}\n"
                 "(If this list is empty, apply general SECURITY-category "
                 "judgment to the files under test.)\n\n"
+                "DEPENDENCY ORDER (execution sequence constraints):\n"
+                "{dependency_order}\n"
+                "(Use this to determine which test files can be created "
+                "independently and which must wait for implementation "
+                "steps. Tests for dependent files should be ordered "
+                "after their dependencies.)\n\n"
+                "NAMING CONVENTIONS (from Phase 3/4):\n"
+                "{naming_conventions}\n"
+                "(Use these naming patterns for all test file names, "
+                "test class names, and test function names. Do NOT "
+                "invent alternate naming schemes.)\n\n"
+                "RISK ASSESSMENT (from Phase 3 design):\n"
+                "{risk_assessment}\n"
+                "(Prioritise test coverage for high-risk areas. If a "
+                "risk involves external I/O, ensure the test uses the "
+                "appropriate seam or mock.)\n\n"
                 "BEHAVIOR TO TEST (derived from the IMPLEMENTATION PLAN "
                 "above):\n"
                 "Design tests that pin down the *intended* behavior of "
@@ -1668,19 +1706,19 @@ def register_prompt_defaults(reg: PromptRegistry) -> None:
                 '  "step_number": 5,\n'
                 '  "job": "Wire ReviewHandler into the request handler registry.",\n'
                 '  "inputs": [\n'
-                '    {"source": "src/config/handlers.ext", "details": "Existing registry around line 34 and import block around line 8."},\n'
-                '    {"source": "Phase 3 dependency order", "details": "ReviewHandler must be registered after its module exists."}\n'
+                '    {{"source": "src/config/handlers.ext", "details": "Existing registry around line 34 and import block around line 8."}},\n'
+                '    {{"source": "Phase 3 dependency order", "details": "ReviewHandler must be registered after its module exists."}}\n'
                 '  ],\n'
                 '  "may_change": [\n'
-                '    {"path": "src/config/handlers.ext", "change": "Add import and one registry entry for ReviewHandler."},\n'
-                '    {"path": "tests/test_handlers.ext", "change": "Add or update coverage proving the handler is discoverable."}\n'
+                '    {{"path": "src/config/handlers.ext", "change": "Add import and one registry entry for ReviewHandler."}},\n'
+                '    {{"path": "tests/test_handlers.ext", "change": "Add or update coverage proving the handler is discoverable."}}\n'
                 '  ],\n'
                 '  "must_not_change": ["Existing handler names, route paths, or unrelated registry entries"],\n'
                 '  "allowed_tools": ["edit_file", "run_tests"],\n'
                 '  "output_shape": "The registry imports ReviewHandler and includes exactly one registration entry using the existing style. Test coverage proves the new handler is discoverable without changing existing handler behavior.",\n'
                 '  "success_checks": [\n'
-                '    {"description": "Registry contains one ReviewHandler import and registration entry.", "tool": "read_file", "expected": "ReviewHandler appears once in imports and registry."},\n'
-                '    {"description": "Relevant handler tests pass.", "tool": "run_tests", "command": "{test_command}", "expected": "Command exits successfully."}\n'
+                '    {{"description": "Registry contains one ReviewHandler import and registration entry.", "tool": "read_file", "expected": "ReviewHandler appears once in imports and registry."}},\n'
+                '    {{"description": "Relevant handler tests pass.", "tool": "run_tests", "command": "{test_command}", "expected": "Command exits successfully."}}\n'
                 '  ],\n'
                 '  "blocked_protocol": "If the registry shape differs from the input, read the file and adapt to the local pattern. If ReviewHandler is absent, stop and report the missing dependency.",\n'
                 '  "tool": "edit_file",\n'
diff --git a/backend/src/lean_ai/llm/tool_definitions.py b/backend/src/lean_ai/llm/tool_definitions.py
index 5b0fe68..d4f6e19 100644
--- a/backend/src/lean_ai/llm/tool_definitions.py
+++ b/backend/src/lean_ai/llm/tool_definitions.py
@@ -994,6 +994,60 @@ RECORD_FILE_OBSERVATION_TOOL: dict = {
 }
 
 
+RECORD_WEB_REFERENCE_TOOL: dict = {
+    "type": "function",
+    "function": {
+        "name": "record_web_reference",
+        "description": (
+            "Record a structured finding from a web search during Phase 2 "
+            "exploration. Call this after using search_internet or fetch_url "
+            "to verify an external dependency, API, or library. Web references "
+            "are the authoritative way web-research findings reach downstream "
+            "phases — free-form prose is for narrating reasoning, not for "
+            "transcribing documentation. If called twice for the same "
+            "dependency, the second call replaces the first (latest "
+            "understanding wins)."
+        ),
+        "parameters": {
+            "type": "object",
+            "properties": {
+                "dependency": {
+                    "type": "string",
+                    "description": (
+                        "Name of the external dependency, API, or library "
+                        "(e.g. 'ruff', 'pydantic v2', 'ollama tools parameter')."
+                    ),
+                },
+                "docs_url": {
+                    "type": "string",
+                    "description": (
+                        "URL to the official documentation page consulted "
+                        "(e.g. 'https://docs.astral.sh/ruff/')."
+                    ),
+                },
+                "version": {
+                    "type": "string",
+                    "description": (
+                        "Confirmed version or version range (e.g. '>=2.0', "
+                        "'0.4.5'). Leave empty if version was not specified."
+                    ),
+                },
+                "confirmed_patterns": {
+                    "type": "string",
+                    "description": (
+                        "Key API signatures, configuration patterns, or "
+                        "usage examples confirmed by the documentation. "
+                        "Include concrete details the planner must keep in "
+                        "hand for design and implementation."
+                    ),
+                },
+            },
+            "required": ["dependency", "docs_url"],
+        },
+    },
+}
+
+
 # Read-only tools for planning phases
 PLANNING_TOOLS: list[dict] = [
     tool
@@ -1021,6 +1075,7 @@ def build_planning_tools() -> list[dict]:
         + search_tools
         + [QUERY_CONTEXT_TOOL]
         + REFERENCE_TOOLS
+        + [RECORD_WEB_REFERENCE_TOOL]
         + _maybe_wiki_tools()
         + _maybe_ui_verification_tools()
     )
@@ -1046,7 +1101,13 @@ DESIGN_TOOLS: list[dict] = [
 
 def build_design_tools() -> list[dict]:
     """Search + task_complete + reference + wiki + UI verification tools for Phase 3."""
-    return DESIGN_TOOLS + REFERENCE_TOOLS + _maybe_wiki_tools() + _maybe_ui_verification_tools()
+    return (
+        DESIGN_TOOLS
+        + REFERENCE_TOOLS
+        + [RECORD_WEB_REFERENCE_TOOL]
+        + _maybe_wiki_tools()
+        + _maybe_ui_verification_tools()
+    )
 
 
 # Read-only tools for chat exploration (no task_complete — text exit)
diff --git a/backend/src/lean_ai/workflow/executor.py b/backend/src/lean_ai/workflow/executor.py
index 9d8e08e..1fd018a 100644
--- a/backend/src/lean_ai/workflow/executor.py
+++ b/backend/src/lean_ai/workflow/executor.py
@@ -1094,7 +1094,7 @@ async def _run_tdd_execution(
     )
     test_system_prompt = build_tdd_test_writing_prompt(
         load_execution_context(repo_root),
-        implementation_plan_md=plan_to_markdown(plan, include_context=False),
+        implementation_plan_md=plan_to_markdown(plan),
         naming_conventions=format_naming_conventions_for_prompt(
             getattr(plan, "naming_conventions", []) or [],
         ),
diff --git a/backend/tests/unit/test_phase5_coverage_validator.py b/backend/tests/unit/test_phase5_coverage_validator.py
index 7f8fa9a..1a021f7 100644
--- a/backend/tests/unit/test_phase5_coverage_validator.py
+++ b/backend/tests/unit/test_phase5_coverage_validator.py
@@ -34,14 +34,13 @@ def _verif(steps: list[PlanStep]) -> VerificationPlan:
     return VerificationPlan(steps=steps)
 
 
-def _test_step(*, file_path: str, instruction: str = "", context: str = "") -> PlanStep:
+def _test_step(*, file_path: str, instruction: str = "") -> PlanStep:
     return PlanStep(
         step_number=1,
         tool="create_file",
         file_path=file_path,
         instruction=instruction or "create test",
         reason="reason",
-        context=context,
     )
 
 
@@ -168,7 +167,6 @@ def test_run_tests_step_does_not_count_as_coverage() -> None:
                 file_path="",
                 instruction="pytest tests/ -q",
                 reason="execute test suite",
-                context="",
             )
         ]
     )

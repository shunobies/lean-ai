"""Centralized prompt registry with YAML override support.

All LLM prompts are registered here with metadata (category, description,
template variables).  The registry loads per-project overrides from
``.lean_ai/prompts.yaml`` — users can edit prompts without touching source
code.  Missing overrides fall back to compiled defaults.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Category display order for the UI
CATEGORY_ORDER = [
    "Core Policy",
    "Planning",
    "Execution",
    "Fix Mode",
    "Chat & Refinement",
    "Context Generation",
    "Framework Guide",
    "TDD & Vision",
    "Advanced",
]


@dataclass
class PromptEntry:
    """A single registered prompt with metadata."""

    key: str
    category: str
    name: str
    description: str
    default_text: str
    template_vars: list[str] = field(default_factory=list)
    warning: str = ""


class PromptRegistry:
    """Singleton registry for all LLM prompts.

    Defaults are compiled into the code.  Per-project overrides are loaded
    from ``.lean_ai/prompts.yaml`` (only contains overrides — missing keys
    fall back to defaults).
    """

    def __init__(self) -> None:
        self._defaults: dict[str, PromptEntry] = {}
        self._overrides: dict[str, str] = {}
        self._loaded_root: str | None = None

    # ── Public API ────────────────────────────────────────────────────

    def register(self, entry: PromptEntry) -> None:
        """Register a prompt entry (used during initialisation)."""
        self._defaults[entry.key] = entry

    def get(self, key: str) -> str:
        """Return the current text for *key* (override or default)."""
        if key in self._overrides:
            return self._overrides[key]
        entry = self._defaults.get(key)
        if entry is None:
            raise KeyError(f"Unknown prompt key: {key!r}")
        return entry.default_text

    def get_all(self) -> list[dict[str, Any]]:
        """Return all prompts with metadata for the API."""
        result: list[dict[str, Any]] = []
        for entry in self._defaults.values():
            result.append(
                {
                    "key": entry.key,
                    "category": entry.category,
                    "name": entry.name,
                    "description": entry.description,
                    "default_text": entry.default_text,
                    "current_text": self._overrides.get(entry.key, entry.default_text),
                    "is_overridden": entry.key in self._overrides,
                    "template_vars": entry.template_vars,
                    "warning": entry.warning,
                }
            )
        return result

    def validate(self, key: str, text: str) -> list[str]:
        """Check that *text* preserves required template variables.

        Returns a list of error strings (empty if valid).
        """
        entry = self._defaults.get(key)
        if entry is None:
            return [f"Unknown prompt key: {key!r}"]
        errors: list[str] = []
        for var in entry.template_vars:
            if f"{{{var}}}" not in text:
                errors.append(f"Missing required placeholder: {{{var}}}")
        return errors

    def load(self, repo_root: str) -> None:
        """Load overrides from ``.lean_ai/prompts.yaml``."""
        root = Path(repo_root)
        yaml_path = root / ".lean_ai" / "prompts.yaml"
        self._loaded_root = repo_root
        self._overrides.clear()

        if not yaml_path.exists():
            return

        try:
            from ruamel.yaml import YAML

            yaml = YAML()
            yaml.preserve_quotes = True
            data = yaml.load(yaml_path)
            if not isinstance(data, dict):
                return
            for k, v in data.items():
                if k.startswith("_"):
                    continue  # skip meta keys like _version
                if isinstance(v, str) and k in self._defaults:
                    self._overrides[k] = v
                elif k not in self._defaults:
                    logger.warning("prompts.yaml: unknown key %r (ignored)", k)
        except Exception:
            logger.exception("Failed to load prompts.yaml from %s", yaml_path)

    def save_overrides(self, repo_root: str, overrides: dict[str, str]) -> None:
        """Write overrides to ``.lean_ai/prompts.yaml``."""
        root = Path(repo_root)
        yaml_dir = root / ".lean_ai"
        yaml_dir.mkdir(parents=True, exist_ok=True)
        yaml_path = yaml_dir / "prompts.yaml"

        # Merge with existing overrides
        merged = dict(self._overrides)
        merged.update(overrides)

        # Build ordered output
        from ruamel.yaml import YAML

        yaml = YAML()
        yaml.default_flow_style = False
        yaml.width = 4096  # prevent line wrapping

        out: dict[str, Any] = {"_version": 1}
        for k in sorted(merged):
            out[k] = merged[k]

        yaml.dump(out, yaml_path)
        self._overrides = merged
        self._loaded_root = repo_root

    def reset(self, repo_root: str, keys: list[str] | None = None) -> None:
        """Reset overrides.  *keys=None* resets all."""
        if keys is None:
            self._overrides.clear()
        else:
            for k in keys:
                self._overrides.pop(k, None)

        root = Path(repo_root)
        yaml_path = root / ".lean_ai" / "prompts.yaml"

        if not self._overrides:
            # No overrides left — remove the file
            if yaml_path.exists():
                yaml_path.unlink()
        else:
            self.save_overrides(repo_root, {})  # rewrite with remaining

    def format(self, key: str, **kwargs: str) -> str:
        """Get a prompt and apply template variable substitution.

        Uses ``format_map`` with a default-dict so missing keys produce
        empty strings rather than raising.
        """
        text = self.get(key)
        if kwargs:
            text = text.format_map(defaultdict(str, **kwargs))
        return text


# ── Module-level singleton ────────────────────────────────────────────

registry = PromptRegistry()


# ── Default registration ──────────────────────────────────────────────


def _register_defaults(reg: PromptRegistry) -> None:
    """Register all built-in prompt defaults."""

    # ── Core Policy ───────────────────────────────────────────────────

    reg.register(
        PromptEntry(
            key="policy.tool",
            category="Core Policy",
            name="Tool Policy",
            description=(
                "Rules for how tools are called during implementation."
                " Shared across execution, fix, and request modes."
            ),
            default_text=(
                "- Call tools in every response while work remains.\n"
                "- read_file before edit_file — search blocks must match actual content.\n"
                "- If edit_file fails, re-read the file before retrying.\n"
                "- For files over ~200 lines, create a skeleton then edit_file to fill sections."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="policy.completion",
            category="Core Policy",
            name="Completion Contract",
            description="How the LLM signals completion. Shared across execution modes.",
            default_text=(
                "When ALL work is done, call task_complete with a one-line summary. "
                "This is the only way to signal completion. Do not stop without it."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="policy.quality",
            category="Core Policy",
            name="Quality Rules",
            description=(
                "Code quality standards enforced during"
                " implementation. Shared across execution modes."
            ),
            default_text=(
                "- No stubs, no TODOs, no placeholder implementations.\n"
                "- Do not add features, refactoring, or improvements beyond the task.\n"
                "- Minimal changes — only what is needed.\n"
                "- Add a brief docstring to every new function or class you create.\n"
                "- Never use mutable default arguments (lists, dicts, sets). "
                "Use None and create inside the function.\n"
                "- Always await async calls. Never return a bare coroutine.\n"
                "- Never use bare except — always catch a specific exception type.\n"
                "- Use context managers (with/async with) for files, connections, and locks."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="policy.web_search",
            category="Core Policy",
            name="Web Search Policy",
            description=(
                "When to search the internet during implementation. Shared across execution modes."
            ),
            default_text=(
                "If stuck after one failed attempt, call search_internet with the error "
                "message before trying another fix. Call fetch_url on the best result."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="policy.wiki_search",
            category="Core Policy",
            name="Wiki Search Policy",
            description=(
                "When to search the internal wiki during"
                " implementation. Only active when MediaWiki is configured."
            ),
            default_text=(
                "If the task involves internal systems, APIs, or company-specific knowledge, "
                "call search_wiki to find relevant internal documentation. "
                "Use fetch_wiki_page to read the full content of matching pages."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="policy.claim_verification",
            category="Core Policy",
            name="Claim Verification Policy",
            description=(
                "Instructs the LLM to verify claims about"
                " external libraries, APIs, and features via web search."
            ),
            default_text=(
                "Before stating that a library, API, feature, or function does not exist, "
                "is deprecated, or is only available in a future version, call search_internet "
                "to verify. Your training data may be outdated. This applies to external "
                "dependencies and third-party tools — not to files within this project."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="policy.required_citations",
            category="Core Policy",
            name="Required Citations Policy",
            description=(
                "Mandates documentation verification and citation"
                " for external framework/library/API usage."
            ),
            default_text=(
                "REQUIRED CITATIONS: Before writing code that uses external frameworks, "
                "libraries, or APIs, call search_internet to verify current patterns and "
                "API signatures. Your training data may be stale. After verifying, include "
                "the documentation URL and confirmed details in your output so downstream "
                "steps can trust the information. This is mandatory for external dependencies "
                "— not for project-internal code."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="policy.scratchpad",
            category="Core Policy",
            name="Scratchpad & Journal Policy",
            description=(
                "How to use the scratchpad and journal for progress"
                " tracking. Shared across execution modes."
            ),
            default_text=(
                "SCRATCHPAD (volatile): Use update_scratchpad after each logical step "
                "to record current working state. Write the ENTIRE content each time "
                "(it overwrites). Use for: what you are doing now, current errors, "
                "files being modified, next step.\n"
                "JOURNAL (permanent): Use add_journal_entry to record milestones and "
                "findings that must survive context refresh. Each call appends one "
                "entry (never lost). Use for: key discoveries, architectural decisions, "
                "dependency constraints, completed milestones, cross-file relationships.\n"
                'Items under scratchpad "## Completed" are done — do not revert them. '
                "Journal entries are your permanent record — check them before starting."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="policy.strict_test_contract",
            category="Core Policy",
            name="Strict Test Contract Policy",
            description=(
                "Phase 5 test-design policy block: programmatic testing only,"
                " E2E hooks required, game/UI guidance, regression-file"
                " convention, core-functionality regression mandate, and the"
                " skipped-placeholder escape hatch."
            ),
            default_text=(
                "PROGRAMMATIC TESTING ONLY — tests must run via the project's "
                "test command with no human in the loop. Tests that require "
                "observing a window, playing a game, watching an animation, "
                "listening to audio, or manually triggering input are "
                "disallowed. If a behavior is only verifiable by human "
                "observation, extract the underlying logic into a pure unit "
                "the tests can call directly.\n\n"
                "END-TO-END TESTING REQUIRES HOOKS — if integration- or "
                "E2E-level testing is needed, the primary source code must "
                "expose programmatic seams: CLI flags, deterministic seeds, "
                "debug endpoints, environment toggles, injectable "
                "clocks/RNG, test-mode configuration, or a dedicated "
                "test-harness entry point. If those hooks do not already "
                "exist, Phase 5 MUST include edit_file steps that add the "
                "hooks BEFORE the create_file step that uses them. "
                "Alternatively, fall back to unit tests over pure logic "
                "extracted from the interactive component. Never write an "
                "E2E test that assumes hooks the codebase doesn't have.\n\n"
                "GAME / UI / REAL-TIME / SIMULATION CODE — prefer testing "
                "pure logic (state transitions, rules, scoring, collision "
                'math, input→output mapping) via unit tests over "running" '
                "the application in tests. For interactive flows, drive "
                "events through the same programmatic API the runtime uses "
                "— not the rendered output.\n\n"
                "REGRESSION TESTING — a regression test prevents a "
                "previously-fixed bug or load-bearing behavior from "
                "returning. Regression tests are IMMUTABLE once a plan "
                "completes — if one fails, the IMPLEMENTATION is wrong; "
                "never edit the test. Regression tests live in their own "
                "files matching the convention `**/regression_*` or "
                "`**/regression/**`. Never co-locate regression tests with "
                "ordinary tests in the same file. Prefix every regression "
                "test creation step's `reason` field with `REGRESSION:` so "
                "the executor and fix loop treat it as load-bearing.\n\n"
                "CORE FUNCTIONALITY → REGRESSION TESTS — when the user "
                "prompt includes a `CORE FUNCTIONALITY REQUIRING REGRESSION "
                "COVERAGE` block, every listed entity MUST receive a "
                "regression test step (not a regular test step). Ordinary "
                "behavior gets ordinary tests; core behavior gets "
                "regression tests.\n\n"
                "WHEN UNABLE TO WRITE A REAL TEST — if a specific behavior "
                "genuinely cannot be tested programmatically even after "
                "adding hooks, write a test marked `skip` in the "
                "framework's native style with a human-readable reason in "
                "the skip message and a TODO linking the work needed. "
                "Never write a fake always-passing test or a sleep-and-hope "
                "test."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="policy.testability_requirement",
            category="Core Policy",
            name="Phase 4 Testability Requirement Policy",
            description=(
                "Phase 4 assembly policy block: when a step introduces code"
                " touching external state, describe the seam Phase 5 will"
                " use to test it."
            ),
            default_text=(
                "TESTABILITY REQUIREMENT — when a create_file or edit_file "
                "step introduces code that interacts with external state "
                "(clock, RNG, network, filesystem, stdin/stdout, main-loop "
                "driver, environment variables, subprocess spawning), the "
                "step's `instruction` field MUST describe the seam Phase 5 "
                "will use to drive the behavior in a test. Acceptable "
                "seams:\n"
                "- Dependency-injected collaborators (function / "
                "constructor parameter with a default)\n"
                "- Environment-flag gates (e.g. `DEBUG_MODE=1` "
                "short-circuits a real operation)\n"
                "- A `--test-mode` / `--seed N` / `--headless` CLI flag on "
                "the primary entry point\n"
                "- A documented test-harness entry function adjacent to the "
                "public API\n"
                "- A pure-function core that a thin I/O wrapper calls, with "
                "the pure function being the public testable surface\n"
                "This makes Phase 5's hooks-required rule achievable — "
                "design code for testability up front rather than expecting "
                "Phase 5 to retrofit seams later."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="policy.regression_awareness",
            category="Core Policy",
            name="Regression Awareness Policy",
            description=(
                "Fix-loop / executor policy block: regression tests are"
                " immutable; implementation must change to match a failing"
                " regression test."
            ),
            default_text=(
                "REGRESSION AWARENESS — regression test files (paths "
                "matching `**/regression_*` or `**/regression/**`) guard "
                "previously-fixed bugs and load-bearing behavior. They are "
                "IMMUTABLE:\n"
                "- You MUST NOT edit a regression test file. The tool "
                "executor will reject such edits.\n"
                "- If a regression test fails, the IMPLEMENTATION is wrong. "
                "Edit production code to restore the tested behavior.\n"
                "- Treat every regression test as a contract from a past "
                "bug-fix or from the original core-functionality author. "
                "Breaking it is a regression by definition."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="policy.testing_environment_awareness",
            category="Core Policy",
            name="Testing Environment Awareness Policy",
            description=(
                "Phase 2 / Phase 5 policy block: the LLM must detect"
                " whether the project has a working test setup, and"
                " include setup steps in the plan when it doesn't."
                " Covers language-specific setup, web-search for best"
                " practices, and awareness of primitive vs modern tooling."
            ),
            default_text=(
                "TESTING ENVIRONMENT AWARENESS — testing infrastructure "
                "differs by language, framework, and project maturity. "
                "Before proposing ANY test work, confirm the project "
                "actually has a working test setup.\n\n"
                "1. DETECT. Look for evidence of a working test setup:\n"
                "   - A declared test command in `.lean_ai/commands.json`, "
                "`pyproject.toml`, `package.json` `scripts`, `Cargo.toml`, "
                "`composer.json`, `Makefile`, `Gemfile`, `build.gradle`, "
                "etc.\n"
                "   - A test-config file: `pytest.ini`, `pyproject.toml` "
                "`[tool.pytest.ini_options]`, `jest.config.*`, "
                "`vitest.config.*`, `phpunit.xml`, `karma.conf.*`, "
                "`tsconfig.json` test includes, `.rspec`, `go.mod`'s "
                "test files, `Cargo.toml` `[dev-dependencies]`, etc.\n"
                "   - Existing test files that actually import the "
                "framework.\n"
                "   If none of the above exists, the project has NO "
                "working test setup — you cannot skip this step.\n\n"
                "2. IF NO SETUP EXISTS, research first. Web-search "
                '"best practices for setting up {detected_language} '
                'testing in {current_year}" — your training data may be '
                "stale and the idiomatic framework may have changed. "
                "Pick the canonical framework for the detected language:\n"
                "   - Python: pytest + pyproject.toml "
                "`[tool.pytest.ini_options]` (modern) or pytest.ini "
                "(legacy).\n"
                "   - Node / TypeScript: vitest (modern) or jest.\n"
                "   - Rust: built-in cargo test + optional "
                "`[dev-dependencies]`.\n"
                "   - PHP: phpunit via composer `require --dev`.\n"
                "   - Go: built-in `go test` (no framework install "
                "needed).\n"
                "   - Ruby: rspec or minitest.\n"
                "   - Java / Kotlin: JUnit 5 via gradle / maven.\n"
                "   - C# / .NET: xUnit / NUnit via dotnet add.\n"
                "   - Older C / C++ / Fortran / assembly: testing may be "
                "as primitive as a `main` function with asserts — do NOT "
                "force a modern framework where it would be alien. Pick "
                "the simplest harness that runs non-interactively.\n\n"
                "3. INCLUDE SETUP STEPS IN THE PLAN BEFORE TEST CREATION. "
                "In TDD mode with a greenfield project, Phase A needs a "
                "project skeleton to write tests INTO — the setup steps "
                "come before the expert's test-writing phase. Typical "
                "setup steps:\n"
                "   - Initialize / update the project manifest "
                "(`pyproject.toml`, `package.json`, `Cargo.toml`, "
                "`composer.json`) with test dependencies.\n"
                "   - Install dependencies (`pip install -e .[dev]`, "
                "`npm install`, `cargo build`, `composer install`).\n"
                "   - Create minimal test config if required "
                "(`pytest.ini`, `jest.config.js`, `phpunit.xml`).\n"
                "   - Initialize a venv / language-specific environment "
                "if the project doesn't have one.\n"
                "   - Record the resulting test command to "
                "`.lean_ai/commands.json` (the auto-detector writes this "
                "during `/init-workspace`, but a plan-driven setup "
                "should update it too).\n\n"
                "4. PER-PROJECT COMMANDS. The workspace's "
                "`.lean_ai/commands.json` is the source of truth for "
                "`test` / `lint` / `format` / `lint_fix` commands. "
                "Global `LEAN_AI_POST_*_COMMAND` env vars are a legacy "
                "override that transfer between projects and usually "
                "don't belong in per-project settings. If the auto-"
                "detected command looks wrong for the current "
                "workspace, update `.lean_ai/commands.json` rather than "
                "the global env var.\n\n"
                "5. PRIMITIVE-TOOLING AWARENESS. Some ecosystems have "
                "much lighter test tooling than modern Python / Node / "
                "Rust. Expecting a rich assertion library, fixture "
                "system, and parametrized tests in a project that uses "
                "a bare `assert.h` is a mismatch. Adapt expectations "
                "down: a pass/fail exit code may be the best available "
                "signal.\n\n"
                "6. RECORD THE DECISION. Whenever you establish a new "
                "test setup, note the chosen framework and command in "
                "the step's `reason` field so downstream phases (and "
                "future plans) can find it."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="policy.testing_environment_snapshot",
            category="Core Policy",
            name="Phase 2 Testing Environment Snapshot Policy",
            description=(
                "Lightweight Phase 2 test-environment detection guidance. "
                "Avoids setup research during codebase exploration."
            ),
            default_text=(
                "TESTING ENVIRONMENT SNAPSHOT — collect only enough evidence "
                "for Phase 5 to design tests later. Look for an obvious test "
                "command or framework in project manifests/config files, locate "
                "the test directory if it is apparent, and read at most ONE "
                "representative existing test file for assertion style. If no "
                "working setup is evident after targeted checks, record that "
                "uncertainty and move on. Do not web-search, choose frameworks, "
                "or design setup steps during Phase 2."
            ),
        )
    )

    # ── Planning ──────────────────────────────────────────────────────

    reg.register(
        PromptEntry(
            key="planning.scope_system",
            category="Planning",
            name="Phase 1: Clarification / Verification (System)",
            description=(
                "System prompt for Phase 1 — verifies the task against the "
                "codebase, asks the user clarifying questions when strictly "
                "needed. Scope document assembly happens in Phase 1a. Request "
                "model."
            ),
            default_text=(
                "Your job is to verify the task is specific enough for the "
                "scope-generation phase (Phase 1a) to produce a complete "
                "scope document. You do NOT write the scope document here — "
                "Phase 1a does that from the task text plus your findings.\n\n"
                "End your final message with a brief summary paragraph that "
                "captures (1) the task as you understand it, (2) any verified "
                "facts from tool calls, and (3) any answers from clarifying "
                "questions you asked. Then call task_complete. If the task is "
                "already clear from the provided context and references, "
                "write the summary immediately and call task_complete with "
                "zero tool calls.\n\n"
                "## Available Tools\n\n"
                "Codebase verification (prefer these — the codebase almost "
                "always has the answer):\n"
                "- grep_files — find references and entity locations\n"
                "- read_file — confirm the content of a specifically cited file\n"
                "- list_directory — orient around a directory the task mentions\n"
                "- query_project_context — targeted lookup in the project context database\n"
                "- search_reference — domain reference library lookup for project-specific "
                "conventions or terminology\n\n"
                "User clarification (escalate only when a decision cannot be "
                "inferred from the codebase or context):\n"
                "- request_clarification — ask the user a single concrete "
                "question. Only use this for decisions only the user can make "
                "(scope boundaries, feature behaviour, UX preferences, data "
                "contracts, rollout strategy). Never ask for facts a tool can "
                "look up.\n\n"
                "Completion:\n"
                "- task_complete — call after you have written the summary "
                "and the task is ready for Phase 1a\n\n"
                "## Tool Budget\n\n"
                "Hard cap: up to {PHASE1_MAX_TURNS} tool calls. This is a "
                "ceiling, not a quota.\n\n"
                "## Trust the References You Were Given\n\n"
                "If the task description or its Suggested Agent Prompt cites "
                "specific files with line numbers, snippets, reference "
                "library documents, or URLs, trust them — do not re-verify. "
                "Codebase tools are for tightening things you were NOT told, "
                "not for double-checking known facts.\n\n"
                "## Anti-Fabrication\n\n"
                "Do NOT invent file paths, fabricate file contents, or assume "
                "infrastructure exists without evidence. Flag genuinely "
                "uncertain details in your summary so Phase 1a captures them "
                "as assumptions with verify hints.\n\n"
                "You produce structured text, not code."
            ),
            template_vars=["PHASE1_MAX_TURNS"],
        )
    )

    reg.register(
        PromptEntry(
            key="planning.exploration_system",
            category="Planning",
            name="Phase 2: Codebase Exploration (System)",
            description=(
                "System prompt for Phase 2 — explores the codebase with "
                "read-only tools and records structured file observations. "
                "Request model."
            ),
            default_text=(
                "Use your knowledge of programming and software architecture to run a "
                "bounded evidence pass over the codebase. Your job is to identify the "
                "files that are likely to change, plus a small set of reference files "
                "needed to make those changes safely.\n\n"
                "## Exploration Workflow\n\n"
                "Treat the Phase 1 scope as a handoff of hypotheses and search seeds, "
                "not as executable instructions. The original task remains "
                "authoritative. Confirm or falsify Phase 1 assumptions with targeted "
                "grep/read calls, then continue with the user's task.\n\n"
                "Call record_file_observation immediately after reading or grepping "
                "a relevant file. Do not wait until the end of exploration. The "
                "structured observation is what reaches downstream phases — "
                "free-form prose is for narrating your reasoning, not for "
                "transcribing file content. For each relevant file provide: "
                "file_path, role (modify / create / reference / missing), a "
                "one-line reason, relevant_sections (line ranges + brief "
                "description), and key_snippets (15-25 line excerpts of "
                "signatures, imports, and non-obvious invariants).\n\n"
                "If you read a file and decide it is not relevant after all, "
                "simply do not record it — observations are signal, not a log "
                "of everything you opened.\n\n"
                "Bound the search: use grep_files to find consumers, classify the "
                "matches, and read only files that are likely to change or that "
                "provide a necessary pattern. Do not read every consumer just "
                "because it matched grep. One representative reference file is "
                "usually enough for a pattern unless the task requires more.\n\n"
                "JOURNAL DISCIPLINE (critical for long tasks):\n"
                "- After exploring each file or group of related files, IMMEDIATELY call add_journal_entry\n"
                "  to record what you found. Do not delay journaling until you \"have time.\"\n"
                "- Before reading any file with read_file, FIRST check the journal (via read_journal)\n"
                "  and observations to see if you already explored it. If a file is already recorded\n"
                "  in observations or journal, skip re-reading it unless you need to re-examine specific\n"
                "  details for a new reason.\n"
                "- Use record_file_observation to record per-file findings (what a file contains, key functions,\n"
                "  imports, etc.). Use add_journal_entry for cross-cutting insights, architectural\n"
                "  decisions, and completed milestones.\n"
                "- If you are about to explore a file and the journal/observations already cover it,\n"
                "  explicitly state \"already explored\" and move on to the next file.\n\n"
                "{TOOL_POLICY}\n\n"
                "{SCRATCHPAD_POLICY}\n\n"
                "{QUALITY_RULES}\n\n"
                "{COMPLETION_CONTRACT}\n\n"
                "{WEB_SEARCH_POLICY}\n\n"
                "{TESTING_ENVIRONMENT_AWARENESS}"
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="planning.design_system",
            category="Planning",
            name="Phase 3: Design Synthesis (System)",
            description=(
                "System prompt for Phase 3 — synthesizes design"
                " from scope and exploration. Expert model, no tools."
            ),
            default_text=(
                "Use your knowledge of programming and software architecture to "
                "synthesize design decisions from the scope analysis and "
                "FileSummary provided by prior phases.\n\n"
                "## Available Tools\n\n"
                "- search_internet — verify external API patterns, framework "
                "versions, library conventions\n"
                "- fetch_url — read specific documentation pages\n"
                "- search_reference / list_reference_documents — reference library "
                "lookup (when configured)\n"
                "- search_wiki / fetch_wiki_page — MediaWiki lookup (when "
                "configured)\n"
                "- task_complete — exit when verification is complete\n\n"
                "You do NOT have tools to read the codebase — the FileSummary is "
                "the authoritative record of what was explored.\n\n"
                "## FileSummary Is Authoritative\n\n"
                "The FileSummary below was produced by a Phase 2 model that read "
                "the codebase with tools. Every file path, signature, import, "
                "and snippet in its `key_snippets` fields is transcribed directly "
                "from source. Trust these transcriptions — do NOT re-derive or "
                "second-guess them. When you cite a function signature, class "
                "name, route, or import, pull it from key_snippets rather than "
                "generating from memory.\n\n"
                "The FileSummary's VERIFIED REFERENCES section already covers "
                "external dependencies Phase 2 looked up. Skip re-verification "
                "for anything already listed.\n\n"
                "## Required Citations (for gaps only)\n\n"
                "For external frameworks, libraries, APIs, or version-specific "
                "behavior central to the task and NOT already in the FileSummary's "
                "VERIFIED REFERENCES, call search_internet / fetch_url to verify "
                "current documented patterns. Your training data may be outdated — "
                "libraries release breaking changes and new recommended patterns. "
                "For each dependency you verify, note in your prose: the "
                "documentation URL, confirmed API signatures, and the version "
                "checked. A synthesis step will bucket those into the "
                "structured output.\n\n"
                "## Output\n\n"
                "Produce free-form exploration and verification prose — reasoning, "
                "design decisions, dependency notes. Do NOT try to structure into "
                "fixed sections; a synthesis step coerces your notes into a "
                "validated schema afterwards. If the FileSummary and VERIFIED "
                "REFERENCES already cover everything and you have no external "
                "verification to do, you may call task_complete (or produce a "
                "single text response) and exit immediately.\n\n"
                "## Anti-fabrication\n\n"
                "Do NOT simulate running commands, invent file listings, or "
                "fabricate file contents. Base your analysis ONLY on the "
                "FileSummary, scope, and any documentation you fetch during "
                "this pass. You are designing, not exploring the codebase "
                "(Phase 2 did that) and not implementing (Phase 4 does that).\n\n"
                "## CORE-FUNCTIONALITY DETECTION\n\n"
                "In addition to the usual design output, identify every "
                "entity introduced or modified by this plan that is "
                "LOAD-BEARING — something whose silent removal or "
                "breakage would degrade the application. Future plans "
                "can accidentally break core behavior while adding "
                "features, so each core entity will be guarded by a "
                "regression test written in Phase 5. Tag an entity when "
                "ANY of the signals below applies — mention the tag in "
                "your prose (the synthesis step will bucket it into the "
                "structured output):\n\n"
                "- **phase1_deliverable** — the entity directly fulfils "
                "an item in the scope's DELIVERABLES list.\n"
                "- **critical_risk_adjacent** — the entity is named or "
                "implicated by a critical_risk of severity medium or "
                "high.\n"
                "- **public_api** — the entity is part of the repo's "
                "public surface: an exported function/class, a route "
                "handler, a CLI command, a class listed in `__all__`, a "
                "module documented as the package entry point.\n"
                "- **downstream_consumer** — absent or broken, the entity "
                "would break something in the scope's DOWNSTREAM "
                "CONSUMERS list.\n\n"
                "When in doubt, tag with `confidence=low` and a brief "
                "reason — the user can prune during plan approval, and "
                "a regression test is cheap insurance. Internal helpers, "
                "utilities, refactor-only changes, tests, and generated "
                "code are NOT core — leave them untagged.\n\n"
                "For each tag, note in your prose:\n"
                "  - entity: the function / class / module / route name\n"
                "  - file_path: repo-relative path\n"
                "  - reason: one sentence on what breaks if it regresses\n"
                "  - source_signal: which of the four rules applies\n"
                "  - confidence: high / medium / low\n\n"
                "Do NOT tag: internal helpers, utility functions, "
                "refactor-only changes, tests, generated code, or files "
                "that are data-only (fixtures, configs, schemas)."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="planning.assembly_system",
            category="Planning",
            name="Phase 4: Plan Assembly (System)",
            description=(
                "System prompt for Phase 4 — assembles a"
                " structured plan from design materials. Expert model."
            ),
            warning="Contains JSON format instructions and step tool constraints. Edit carefully.",
            default_text=(
                "Use your knowledge of programming and software architecture to "
                "assemble a structured implementation plan from the design "
                "materials provided.\n\n"
                "Convert the design synthesis, file summary, and scope "
                "analysis into a concrete sequence of implementation steps. "
                "Each step maps to exactly one tool call that the executor "
                "model will perform.\n\n"
                "VALID STEP TOOLS:\n"
                "- create_file — for files that do not exist yet\n"
                "- edit_file — for modifications to existing files\n"
                "- read_file — for reading a file before editing or for context\n"
                "- run_command — for build commands, migrations, code generators\n"
                "- run_tests — for running tests\n"
                "- run_lint — for running linters\n"
                "- format_code — for running formatters\n\n"
                "Do NOT produce steps using list_directory, directory_tree, "
                "grep_files, search_internet, fetch_url, or update_scratchpad. "
                "Codebase exploration was completed in earlier phases — the "
                "file summary below contains everything you need.\n\n"
                "Focus the plan on implementation: the majority of steps "
                "should be create_file and edit_file. Use read_file only "
                "when the executor needs to verify file state before editing.\n\n"
                "Do NOT invent file paths or fabricate file contents that "
                "were not in the file summary or design synthesis. Every "
                "file path in the plan must come from the exploration "
                "results provided — a post-generation validator will warn "
                "the user about any paths that do not appear there.\n\n"
                "EXECUTOR MODEL AWARENESS:\n"
                "The executor model sees one step at a time in a fresh "
                "conversation. It has read_file and full implementation "
                "tools but does NOT have your design reasoning, gap "
                "analysis, or the full plan.\n\n"
                "Therefore:\n"
                "- Write each step as a self-contained instruction\n"
                "- Include exact code snippets, import paths, and method "
                "signatures — not descriptions of what to write\n"
                "- Specify the precise location in the file (function name, "
                "class, line range) for every edit\n"
                "- When a step depends on output from a previous step, "
                "include the expected names/paths/signatures in the context "
                "field\n"
                "- Never assume the executor will infer relationships "
                "between steps\n"
                "- Include a 'reason' field explaining WHY this change is "
                "needed — the requirement, test, or dependency that demands "
                "it. The executor uses this to adapt when the file doesn't "
                "match the instruction exactly\n\n"
                "STRUCTURED FIELDS:\n"
                "ExecutionPlan carries `naming_conventions` and "
                "`name_registry` as typed lists (NamingConvention and "
                "NameRegistryEntry respectively), not prose. Populate them "
                "as structured arrays:\n"
                "- naming_conventions: one entry per category observed in "
                "the existing codebase (variables, functions, classes, "
                "files, routes, db_table, db_column, imports). Prefer real "
                "source_file examples from the file summary's key_snippets; "
                "use 'standard framework conventions' when no example "
                "applies.\n"
                "- name_registry: one entry per NEW entity this plan "
                "introduces. Populate only the fields that apply to each "
                "entity (a data class has no route_endpoint, a migration "
                "has no model_class). `registered_in` is the list of files "
                "where the entity must be referenced — each one should have "
                "a corresponding edit_file step.\n\n"
                "{TESTABILITY_REQUIREMENT}"
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="planning.verification_system",
            category="Planning",
            name="Phase 5: Test Verification (System)",
            description=(
                "System prompt for Phase 5 — designs verification"
                "/test steps for the plan. Expert model."
            ),
            default_text=(
                "Use your knowledge of programming and testing to design "
                "verification steps (test file creation) for the "
                "implementation plan provided.\n\n"
                "You do NOT have tools — work from the plan and file "
                "summary given.\n\n"
                "YOUR OUTPUT IS REQUIRED — NOT OPTIONAL:\n"
                "Return a `VerificationPlan` whose `steps` list is NON-"
                "EMPTY. A plan without tests is unacceptable. Every plan "
                "that creates or modifies a file that contains behavior "
                "(anything beyond pure documentation / config comments) "
                "MUST receive at least one corresponding test_file step. "
                "If you are genuinely uncertain what to test, produce a "
                "smoke-test step that imports the module/file and asserts "
                "its top-level entry points exist — never return zero "
                "steps.\n\n"
                "LANGUAGE / FRAMEWORK AGNOSTIC:\n"
                "Identify the project's primary language and its existing "
                "test framework from the file summary (e.g. file "
                "extensions, test directory names, imports in existing "
                "test files) and mirror that framework's conventions. Do "
                "NOT force pytest, jest, junit, rspec, or any particular "
                "style onto the project — match what the repo already "
                "uses. If the repo has NO existing tests, pick the "
                "standard test framework for the detected language and "
                "note the choice in the step's `reason` field.\n\n"
                "STRUCTURED INPUTS:\n"
                "The user message below may include a `FILES NEEDING TEST "
                "COVERAGE` section (derived from Phase 3's change_designs "
                "and Phase 2's files_to_create) and a `SECURITY CONCERNS "
                "FROM PHASE 3` section (derived from the critical_risks "
                "list). When present, these are authoritative: every file "
                "listed MUST have a corresponding test_file step, and "
                "every security concern MUST have at least one test "
                "asserting the risk is mitigated. When absent, derive "
                "targets from the implementation plan's affected files "
                "yourself — do not return an empty list.\n\n"
                "EXECUTOR MODEL AWARENESS:\n"
                "The test steps you produce will be executed by a model that:\n"
                "- Sees one step at a time in a fresh conversation\n"
                "- Has read_file and implementation tools but NOT your "
                "design reasoning\n"
                "- Needs explicit test function / method names, import "
                "paths, fixture/setup references, the assertions to make, "
                "and the exact test-file path\n"
                "- Replicates style from the `context` field — include a "
                "short excerpt from an existing test (imports, "
                "setup/teardown, assertion helpers) so the executor "
                "doesn't have to guess\n"
                "- Uses the `reason` field to understand what behavior or "
                "requirement each test verifies — write this in prose, "
                "not code\n\n"
                "GENERAL TEST QUALITY PRINCIPLES (apply regardless of "
                "language):\n"
                "- One behavior per test — name the test after the "
                "scenario it exercises, not the function name\n"
                "- Assert specific observable values, not truthiness\n"
                "- For error paths: assert BOTH the error type AND the "
                "message / code so refactors don't silently swallow the "
                "regression\n"
                "- Mock the unit's external collaborators (I/O, network, "
                "clock), never the unit itself\n"
                "- Each test runs in isolation — no shared mutable state, "
                "no ordering dependencies\n"
                "- Failing assertions include enough context in the "
                "failure message for a reader to understand what was "
                "expected vs. observed\n\n"
                "{STRICT_TEST_CONTRACT}"
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="planning.verification_user_normal",
            category="Planning",
            name="Phase 5: Verification (Normal User Message)",
            description=(
                "User message for Phase 5 in normal mode — asks for test"
                " file creation steps plus a final run_tests step."
            ),
            template_vars=[
                "task",
                "test_command",
                "impl_plan_md",
                "file_summary",
                "testing_inventory",
                "verification_targets",
                "security_concerns",
                "core_functionality",
                "next_step",
                "run_tests_rule",
            ],
            warning=(
                "Kept in parity with planning.verification_user_tdd — most "
                "content is shared; edits in one prompt should usually be "
                "mirrored in the other unless mode-specific."
            ),
            default_text=(
                "TASK: {task}\n\n"
                "IMPLEMENTATION PLAN:\n{impl_plan_md}\n\n"
                "TEST COMMAND: {test_command}\n\n"
                "TESTING INVENTORY (from Phase 2):\n{testing_inventory}\n\n"
                "FILE SUMMARY (existing test patterns — mirror this "
                "framework & style):\n{file_summary}\n\n"
                "FILES NEEDING TEST COVERAGE (from Phase 3 design + "
                "Phase 2 creates):\n{verification_targets}\n"
                "(If this list is empty, derive targets from the "
                "implementation plan's affected files — do NOT return "
                "zero steps.)\n\n"
                "CORE FUNCTIONALITY REQUIRING REGRESSION COVERAGE "
                "(from Phase 3 tags):\n{core_functionality}\n"
                "(When this list is non-empty, each listed entity MUST "
                "receive a regression test step whose file path matches "
                "the regression-file convention and whose `reason` is "
                "prefixed with `REGRESSION:`.)\n\n"
                "SECURITY CONCERNS FROM PHASE 3 (each MUST have test "
                "coverage):\n{security_concerns}\n"
                "(If this list is empty, apply general SECURITY-category "
                "judgment to the files under test.)\n\n"
                "Produce ONLY the verification steps that should run "
                "AFTER implementation.\n\n"
                "REQUIRED OUTPUT SHAPE:\n"
                "- At least ONE create_file step for a test file covering "
                "behavior added or modified by the plan. Zero is not "
                "acceptable.\n"
                "{run_tests_rule}"
                "- Step numbering starts at {next_step}\n"
                "- Test file paths and test names follow the naming "
                "conventions already in the plan\n"
                "- Each test step's `reason` MUST cite the specific Phase "
                "3 change_design, Phase 2 new file, or critical_risk it "
                "verifies — no anonymous tests. Regression tests prefix "
                "the reason with `REGRESSION:`.\n"
                "- Do not duplicate coverage that TESTING INVENTORY shows "
                "already exists — but the absence of existing coverage "
                "never justifies zero new test steps\n\n"
                "FRAMEWORK DETECTION (language-agnostic):\n"
                "Pick up the project's test framework from the TESTING "
                "INVENTORY and FILE SUMMARY. Use THAT framework's idioms. "
                "If no tests exist yet, pick the canonical framework for "
                "the detected language and record the choice in the step's "
                "`reason` field so a human can validate it.\n\n"
                "TEST FILE STEP — REQUIRED CONTENT IN `instruction`:\n"
                "For each test, state in prose (framework-agnostic):\n"
                "  - test name (matching the detected framework's naming "
                "convention)\n"
                "  - scenario under test — inputs and expected observable "
                "outcome\n"
                "  - the specific value(s) or exception(s) to assert on, "
                "including message / code where applicable\n"
                "  - any mocks/fakes required (always external "
                "collaborators, never the unit itself)\n"
                "The executor will translate the prose into the correct "
                "syntax for the project's framework.\n\n"
                "COVER ALL APPLICABLE CATEGORIES:\n"
                "  HAPPY PATH   — primary use case, expected inputs → "
                "correct outputs\n"
                "  EDGE CASES   — null/empty/zero/boundary values, "
                "unicode, very large inputs, collections with one element, "
                "duplicate entries\n"
                "  ERROR PATHS  — each invalid input raises the correct "
                "error type; assert BOTH type AND message/code\n"
                "  INTEGRATION  — mock external I/O (DB, HTTP, filesystem, "
                "clock, env) and verify the component's contract with its "
                "direct callers\n"
                "  SECURITY     — prioritise the SECURITY CONCERNS listed "
                "above. Also apply general security checks where relevant:\n"
                "    · path handling : traversal/escape payloads rejected "
                "or sandboxed\n"
                "    · shell / eval  : command-injection and template-"
                "injection payloads do not execute\n"
                "    · user data     : injection payloads in stored / "
                "rendered / logged strings\n"
                "    · auth / authz  : unauthenticated → explicit auth "
                "error, not a crash; insufficient privilege → explicit "
                "authorization error\n"
                "    · size limits   : inputs above the configured limit "
                "are rejected cleanly, not crashed\n\n"
                "`context` FIELD REQUIREMENTS:\n"
                "Include a short excerpt from an existing test in the "
                "project (imports, setup/teardown, fixture/helper usage, "
                "assertion style) so the executor can mirror the style "
                "without re-reading files. If no tests exist yet, include "
                "a brief note on the chosen framework's setup pattern.\n\n"
                "ASSERTION QUALITY (language-agnostic):\n"
                "- Each assertion targets ONE specific behavior, not a "
                "vague 'it works'\n"
                "- Prefer exact expected values over truthiness checks\n"
                "- For errors: assert the error type AND at least a "
                "distinguishing substring of the message / structured code\n"
                "- For collections: assert both length AND the expected "
                "element(s)\n"
                "- For concurrent / async code: ensure the assertion "
                "happens on the resolved result, not the pending handle\n\n"
                "ANTI-PATTERNS TO AVOID:\n"
                "- Tests that assert only truthiness or not-null\n"
                "- Tests that mock the unit under test (tautological "
                "tests)\n"
                "- Tests that depend on execution order within the file\n"
                "- A test that simply re-runs the implementation and "
                "compares it to itself\n"
                "- Tests that require a human to observe output (windows, "
                "sounds, rendered frames)\n"
                "- Tests that sleep-and-hope for async completion without "
                "a deterministic signal\n"
                "- Tests that assume a real wallclock, random seed, "
                "network, or filesystem without a seam\n"
                "- Tests that shell out to launch the application's main "
                "entry point (except via a documented test-mode hook)\n"
                "- Over-constrained asserts pinning behavior the contract "
                "doesn't require (exact log wording, exact whitespace, "
                "exact iteration order unless part of the contract)\n"
                "- Co-locating regression tests with regular tests in the "
                "same file\n"
                "- Adding core functionality without a corresponding "
                "regression test"
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="planning.verification_user_tdd",
            category="Planning",
            name="Phase 5: Verification (TDD User Message)",
            description=(
                "User message for Phase 5 in TDD mode — asks for test"
                " file creation steps only (no run_tests). Tests are"
                " written BEFORE implementation by the expert model."
            ),
            template_vars=[
                "task",
                "impl_plan_md",
                "testing_inventory",
                "verification_targets",
                "security_concerns",
                "core_functionality",
                "next_step",
            ],
            warning=(
                "Kept in parity with planning.verification_user_normal — most "
                "content is shared; edits in one prompt should usually be "
                "mirrored in the other unless mode-specific."
            ),
            default_text=(
                "TASK: {task}\n\n"
                "IMPLEMENTATION PLAN:\n{impl_plan_md}\n\n"
                "TDD MODE — These tests will be written and executed "
                "BEFORE any implementation code exists. Design tests from "
                "the PLAN, not from existing source files. The "
                "implementation does NOT exist yet — do not look for "
                "existing source files. Tests drive the implementation, "
                "not the reverse.\n\n"
                "TESTING INVENTORY (from Phase 2):\n{testing_inventory}\n\n"
                "FILES NEEDING TEST COVERAGE (from Phase 3 design + "
                "Phase 2 creates):\n{verification_targets}\n"
                "(If this list is empty, derive targets from the "
                "implementation plan's affected files — do NOT return "
                "zero steps.)\n\n"
                "CORE FUNCTIONALITY REQUIRING REGRESSION COVERAGE "
                "(from Phase 3 tags):\n{core_functionality}\n"
                "(When this list is non-empty, each listed entity MUST "
                "receive a regression test step whose file path matches "
                "the regression-file convention and whose `reason` is "
                "prefixed with `REGRESSION:`.)\n\n"
                "SECURITY CONCERNS FROM PHASE 3 (each MUST have test "
                "coverage):\n{security_concerns}\n"
                "(If this list is empty, apply general SECURITY-category "
                "judgment to the files under test.)\n\n"
                "BEHAVIOR TO TEST (derived from the IMPLEMENTATION PLAN "
                "above):\n"
                "Design tests that pin down the *intended* behavior of "
                "each new or modified entity in the plan.\n\n"
                "YOUR OUTPUT IS REQUIRED — NOT OPTIONAL:\n"
                "Return a non-empty `steps` list. A TDD plan with zero "
                "test steps is unacceptable. Even when the plan only "
                "modifies existing behavior, write at least one test "
                "pinning down the post-change contract.\n\n"
                "Do NOT include a run_tests step — the pipeline executes "
                "the test command automatically after implementation.\n\n"
                "SELF-CONTAINED INSTRUCTIONS — CRITICAL:\n"
                "Phase A (the test-writing model) sees ONLY this step's "
                "`instruction`, `file_path`, and `context` fields. It does "
                "NOT see the implementation plan, other test steps, or "
                "existing source files. Therefore:\n"
                "- Never write 'see step N above', 'as in step N', or any "
                "cross-reference.\n"
                "- The `instruction` field must be a complete prose "
                "description of what is being tested, no placeholders.\n"
                "- Inline every public signature, expected input/output, "
                "and error type the test asserts on.\n"
                "- If a test depends on a fixture/helper/import, spell "
                "out the full import/reference path inside the "
                "instruction so Phase A does not have to browse the repo "
                "for it.\n\n"
                "LANGUAGE / FRAMEWORK AGNOSTIC:\n"
                "Identify the project's primary language and its canonical "
                "test framework (from file extensions in the plan, or the "
                "standard framework for the language if nothing is "
                "indicated). Mirror that framework's conventions for test "
                "names, file layout, fixture/setup style, and assertion "
                "syntax. Record the chosen framework in each step's "
                "`reason` field.\n\n"
                "RULES:\n"
                "- For each file in FILES NEEDING TEST COVERAGE, include "
                "a create_file step for a test file.\n"
                "- For each entity in CORE FUNCTIONALITY REQUIRING "
                "REGRESSION COVERAGE, include a create_file step for a "
                "regression test file (prefix its `reason` with "
                "`REGRESSION:` and place it in a regression-convention "
                "path).\n"
                "- Each test step's `reason` MUST cite the specific Phase "
                "3 change_design, Phase 2 new file, critical_risk, or "
                "core-functionality tag it verifies.\n"
                "- Start step numbering at {next_step}\n"
                "- Follow the naming conventions already in the plan\n\n"
                "TEST FILE STEP — REQUIRED CONTENT IN `instruction`:\n"
                "For each test, state in prose (framework-agnostic):\n"
                "  - test name in the detected framework's naming "
                "convention\n"
                "  - scenario under test — inputs and expected observable "
                "outcome\n"
                "  - specific value(s) or error(s) to assert on, including "
                "message / code where applicable\n"
                "  - any mocks/fakes required for external collaborators\n"
                "Phase A will translate this prose into the correct "
                "syntax for the project's framework.\n\n"
                "COVER ALL APPLICABLE CATEGORIES:\n"
                "  HAPPY PATH   — primary use case, expected inputs → "
                "correct outputs\n"
                "  EDGE CASES   — null/empty/zero/boundary values, "
                "unicode, very large inputs, single-element collections, "
                "duplicate entries\n"
                "  ERROR PATHS  — each invalid input raises the correct "
                "error type; assert BOTH type AND message/code\n"
                "  INTEGRATION  — mock external I/O (DB, HTTP, filesystem, "
                "clock, env) and verify the component's contract with its "
                "direct callers\n"
                "  SECURITY     — prioritise the SECURITY CONCERNS listed "
                "above.\n\n"
                "DOCUMENTATION REQUIREMENTS (mandatory for TDD):\n"
                "- Module-level / file-header comment explaining what "
                "feature is under test\n"
                "- Per-test comment or docstring (in the framework's "
                "style) stating the behavior tested, the expected "
                "input/output, and why this case matters\n"
                "- Descriptive failure messages on assertions so the "
                "implementor sees what was expected vs. observed\n"
                "- Comments on non-obvious setup / mocking explaining "
                "what boundary is being faked and why\n\n"
                "`context` FIELD REQUIREMENTS:\n"
                "Describe the EXPECTED BEHAVIOR for this test file: the "
                "public function/class/method signatures the tests will "
                "call, expected inputs and outputs for each test, and any "
                "invariants the implementation must uphold. Do NOT "
                "describe existing code — there is none yet. If a fixture "
                "/ base class / conftest-equivalent will be needed, name "
                "it explicitly and quote the import/reference line.\n\n"
                "ASSERTION QUALITY (language-agnostic):\n"
                "- Each assertion targets ONE specific behavior, not a "
                "vague 'it works'\n"
                "- Prefer exact expected values over truthiness checks\n"
                "- For errors: assert the error type AND a distinguishing "
                "substring of the message / structured code\n"
                "- For collections: assert both length AND the expected "
                "element(s)\n"
                "- For concurrent / async code: assert against the "
                "resolved result, not the pending handle\n\n"
                "ANTI-PATTERNS TO AVOID:\n"
                "- Tests that assert only truthiness or not-null\n"
                "- Tests that mock the unit under test (tautological "
                "tests)\n"
                "- Tests that depend on execution order within the file\n"
                "- Tests that simply echo the implementation back at "
                "itself\n"
                "- Tests that require a human to observe output (windows, "
                "sounds, rendered frames)\n"
                "- Tests that sleep-and-hope for async completion without "
                "a deterministic signal\n"
                "- Tests that assume a real wallclock, random seed, "
                "network, or filesystem without a seam\n"
                "- Tests that shell out to launch the application's main "
                "entry point (except via a documented test-mode hook)\n"
                "- Over-constrained asserts pinning behavior the contract "
                "doesn't require\n"
                "- Co-locating regression tests with regular tests in the "
                "same file\n"
                "- Adding core functionality without a corresponding "
                "regression test"
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="planning.scope_user",
            category="Planning",
            name="Phase 1: Clarification / Verification (User Message)",
            description=(
                "User message template for Phase 1. Instructs the request "
                "model to verify the task and, if strictly needed, ask the "
                "user clarifying questions via request_clarification. Phase "
                "1a (the synthesis step) produces the 8-section scope "
                "document from the task + the summary this phase emits."
            ),
            template_vars=["task", "context", "PHASE1_MAX_TURNS"],
            default_text=(
                "TASK: {task}\n\n"
                "CODEBASE CONTEXT:\n{context}\n\n"
                "Verify the task above is specific enough for Phase 1a to "
                "generate a complete scope document. Use tools only when a "
                "detail is genuinely unclear:\n\n"
                "- Codebase tools (grep_files, read_file, list_directory, "
                "query_project_context, search_reference) first — the answer "
                "is usually there.\n"
                "- request_clarification only for decisions only the user can "
                "make (scope boundaries, UX preferences, data contracts, "
                "rollout strategy). Frame as one concrete question with a "
                "default where possible. Do not ask for anything a tool can "
                "look up.\n\n"
                "Budget: up to {PHASE1_MAX_TURNS} tool calls. If the task is "
                "already clear from the provided context and references, go "
                "straight to the summary + task_complete with zero tool "
                "calls.\n\n"
                "Before calling task_complete, write one brief summary "
                "paragraph capturing:\n"
                "1. The task as you understand it.\n"
                "2. Any facts verified by tool calls.\n"
                "3. Any answers obtained from clarifying questions.\n\n"
                "Then call task_complete. Phase 1a will read your summary "
                "plus the task text and produce the structured 8-section "
                "scope document — you do NOT write that document here."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="planning.exploration_user",
            category="Planning",
            name="Phase 2: Codebase Exploration (User Message)",
            description=(
                "User message for Phase 2. Drives a strict assumptions "
                "checklist + general exploration, capturing findings via "
                "record_file_observation for the downstream synthesis pass."
            ),
            template_vars=["task", "scope", "context"],
            warning=(
                "The checklist opener + record_file_observation guidance are "
                "load-bearing — the synthesis step depends on observations "
                "being recorded. Edit carefully."
            ),
            default_text=(
                "TASK: {task}\n\n"
                "PHASE 1 HANDOFF (hypotheses/search seeds; task remains authoritative):\n{scope}\n\n"
                "CODEBASE CONTEXT:\n{context}\n\n"
                "## FIRST — Verify Phase 1 Handoff Signals\n\n"
                "The scope above contains an ASSUMPTIONS section with "
                "verification hints. Treat these as bounded search prompts, not "
                "as new requirements. Process each assumption just far enough "
                "to confirm, falsify, or mark it unable_to_verify:\n"
                "1. Read the verification hint (grep X / read Y / check "
                "dependency file / etc.).\n"
                "2. Perform the verification using the appropriate tool.\n"
                "3. If a file was read or grepped and is relevant to the task, "
                "call record_file_observation immediately with the appropriate role "
                "(reference if informational, modify if it will change, "
                "missing if the assumption was falsified because the file "
                "is absent).\n"
                "4. Remember the outcome (confirmed / falsified / "
                "unable_to_verify) and what you found — the synthesis step "
                "will consolidate every outcome into the output. Prefer a short "
                "add_journal_entry for cross-file or assumption outcomes that "
                "must survive context refresh.\n\n"
                "After the handoff signals have been worked, proceed to bounded "
                "general exploration below.\n\n"
                "## THEN — Bounded General Exploration\n\n"
                "Identify the files that likely need to be created or modified, "
                "and the smallest useful set of reference files.\n\n"
                "CONSUMER TRACE:\n"
                "Before finalizing the file list, use grep_files to search for "
                "references to entities likely to change. Classify the matches "
                "before reading them. Read files that are likely to need edits "
                "or that establish a pattern the executor must follow. Do not "
                "read every consumer just because grep found it.\n\n"
                "Do NOT treat file lists in the task description as "
                "exhaustive. The task may mention only some files but omit "
                "dependent files that also need changes.\n\n"
                "EXPLORATION STEPS:\n"
                "1. Use grep_files to find references to likely modified "
                "entities, then classify the matches.\n"
                "2. Use directory_tree / list_directory to understand project "
                "structure only when needed to choose paths.\n"
                "3. Use read_file to read every file you plan to modify, then "
                "immediately call record_file_observation (role: modify) with a reason, "
                "relevant_sections (line ranges + brief description), and "
                "key_snippets (15-25 line excerpts of signatures, imports, "
                "non-obvious invariants).\n"
                "4. Read files that contain patterns the executor should "
                "follow when creating new files, and record a small "
                "representative set as role: reference.\n"
                "5. MISSING INFRASTRUCTURE: if the task assumes a package, "
                "framework, shared config, or base file that you cannot find, "
                "call record_file_observation with role: missing for each "
                "blocking item.\n"
                "6. EXISTING STATE CHECK: for each entity the task introduces "
                "or modifies, determine whether it ALREADY EXISTS and use the "
                "right role (modify vs create).\n"
                "7. READ REGISTRATION FILES: read any files where new "
                "components must be registered or referenced — configuration "
                "files, entry points, bootstrap files, index/barrel files — "
                "and record them as role: modify if they need edits, otherwise "
                "role: reference.\n"
                "8. TESTING INFRASTRUCTURE SNAPSHOT: collect only the minimal "
                "signals Phase 5 needs. Check project manifests/configs for a "
                "test command or framework, locate the test directory if it is "
                "obvious, and read at most ONE representative test file for "
                "style. If no setup is evident after targeted checks, record "
                "that uncertainty in prose or journal and move on.\n\n"
                "EFFICIENCY: You can call multiple tools in a single "
                "response. For example, call read_file on several files at "
                "once instead of reading them one at a time.\n\n"
                "DO NOT IMPLEMENT: You are exploring, not implementing. Do "
                "NOT write fixed versions of files, implementation code, or "
                "'Changes Made' summaries. If you find yourself writing code "
                "that should go into a file, STOP — that is Phase 4's job.\n\n"
                "## Output Contract\n\n"
                "Your findings reach downstream phases through "
                "record_file_observation and the synthesis step that runs "
                "after you call task_complete. Prose is for reasoning, not "
                "transcription. Before moving away from a relevant file, "
                "record it. When you are confident that every relevant file "
                "is recorded, every Phase 1 handoff assumption is resolved or "
                "marked unable_to_verify, and necessary external dependencies "
                "have been checked, "
                "call task_complete to end exploration."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="planning.scope_synthesis_system",
            category="Planning",
            name="Phase 1: Scope Synthesis (System)",
            description=(
                "System prompt for the Phase 1 post-loop synthesis pass that "
                "translates the task description and exploration prose into a "
                "validated ScopeDocument. Uses chat_structured."
            ),
            default_text=(
                "Translate the provided task description into a structured "
                "ScopeDocument. This is a rewrite operation: the task is the "
                "input, the schema fields are the output. Every field is "
                "required — the schema is the contract downstream phases "
                "parse.\n\n"
                "Inputs you will receive:\n"
                "- The original task description (often a Suggested Agent "
                "Prompt with numbered requirements and a References section).\n"
                "- A slice of the project context (architecture, conventions).\n"
                "- Scope analysis prose the Phase 1 tool loop produced.\n\n"
                "Field contract:\n"
                "1. `problem` — 3-6 sentences restating the task and WHY it "
                "matters.\n"
                '2. `deliverables` — observable outcomes ("Users can X", '
                '"Endpoint Y returns Z with schema S"), NOT file changes.\n'
                "3. `in_scope` — concrete greppable entities (file paths, "
                "class / function / route / table / env var names), 3-8 "
                "items.\n"
                "4. `out_of_scope` — tempting-adjacent areas explicitly "
                'excluded. Skip padding like "not related to X" when X was '
                "never plausibly in scope.\n"
                "5. `downstream_consumers` — CATEGORIES of files that "
                "reference modified entities (controllers, views, tests, "
                "configs, migrations, fixtures). Gives Phase 2 a grep "
                "strategy.\n"
                "6. `assumptions` — entries must be falsifiable; pair each "
                "with a concrete verify_hint Phase 2 can act on.\n"
                "7. `success_criteria` — 3-6 falsifiable conditions Phase 5 "
                "will target when generating verification steps.\n"
                "8. `risks` — scope-level misunderstandings about the problem, "
                "distinct from Phase 3's implementation risks.\n\n"
                "When a field's content is not spelled out in the inputs, "
                "state your most reasonable interpretation and add a matching "
                "entry to `assumptions` whose verify_hint tells Phase 2 how "
                "to confirm or falsify it. Do NOT invent file paths, APIs, or "
                "infrastructure not cited in the inputs.\n\n"
                "This step is non-interactive — it mechanically fills the "
                "schema fields from the provided inputs. Output strictly "
                "conforms to the ScopeDocument schema."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="planning.exploration_synthesis_system",
            category="Planning",
            name="Phase 2: Exploration Synthesis (System)",
            description=(
                "System prompt for the Phase 2 post-loop synthesis pass that "
                "coerces recorded observations + scratchpad + journal into a "
                "validated FileSummary schema. Uses chat_structured."
            ),
            default_text=(
                "Use your knowledge of programming and software architecture to "
                "consolidate Phase 2 exploration findings into a structured "
                "FileSummary.\n\n"
                "Inputs you will receive:\n"
                "- The original task and Phase 1 scope analysis.\n"
                "- A list of FileObservation entries recorded during "
                "exploration via record_file_observation.\n"
                "- The exploration scratchpad (volatile working notes).\n"
                "- The exploration journal (permanent findings).\n"
                "- Any prose the exploration model produced (may mention "
                "missing infrastructure, verified external references, "
                "assumption outcomes, and cross-file invariants).\n\n"
                "Your job:\n"
                "1. Bucket every recorded FileObservation into "
                "files_to_modify, files_to_create, or files_read_for_context "
                "based on its role. Entries with role=missing go into "
                "missing_infrastructure (as MissingItem) instead of the file "
                "buckets.\n"
                "2. Populate missing_infrastructure with anything the "
                "exploration model flagged as missing but that is not "
                "already represented by a role=missing observation.\n"
                "3. Populate verified_references from any documentation URLs "
                "the exploration model cited, with the dependency name, URL, "
                "version checked, and confirmed patterns.\n"
                "4. Populate assumptions_resolved with one entry per "
                "assumption from the Phase 1 scope's ASSUMPTIONS section — "
                "status must be one of confirmed / falsified / "
                "unable_to_verify, and evidence should cite what was found.\n"
                "5. Put anything else worth keeping (cross-file invariants, "
                "subtle preconditions, TODOs for the executor) in notes.\n"
                "6. Populate testing_inventory from the exploration's "
                "findings about the project's test infrastructure:\n"
                "   - test_framework: the framework actually used (e.g. "
                "pytest, jest, go test, rspec, junit). Empty if no tests "
                "exist yet.\n"
                "   - test_directory: top-level path where tests live "
                "(e.g. `tests/`, `__tests__/`, `spec/`).\n"
                "   - test_file_pattern: filename pattern used by the "
                "framework (e.g. `test_*.py`, `*.spec.ts`, `*_test.go`).\n"
                "   - assertion_style_excerpt: a short literal excerpt "
                "from an existing test showing imports, setup/teardown, "
                "and a representative assertion — so Phase 5 can mirror "
                "the project's style.\n"
                "   - existing_regression_files: paths of any test files "
                "whose path matches the regression-file convention "
                "(contains `regression_` or lives under `/regression/` "
                "or `/regressions/`).\n"
                "   - affected_files_existing_coverage: for each file in "
                "files_to_modify + files_to_create, list the existing "
                "test files that already exercise it (empty list if "
                "uncovered). Use short coverage_notes when a file is "
                "partially covered.\n"
                "   - notes: anything else Phase 5 should know (e.g. "
                "'integration tests require a running Postgres', "
                "'tests/e2e/ is headless via xvfb').\n"
                "   Leave testing_inventory unset (null) only when the "
                "repo has no discoverable test infrastructure at all.\n\n"
                "Do NOT invent files, URLs, or versions that are not in the "
                "provided inputs. If a field cannot be populated from the "
                "inputs, leave it empty. If the exploration model recorded "
                "zero observations, the buckets may be empty — report the "
                "notes field accordingly.\n\n"
                "Output strictly conforms to the FileSummary schema."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="planning.design_user",
            category="Planning",
            name="Phase 3: Design Synthesis (User Message)",
            description=(
                "User message for Phase 3. Provides task,"
                " scope, context, and files for design synthesis."
            ),
            template_vars=["task", "scope", "project_context", "file_summary"],
            warning=(
                "This prompt drives Pass 1 (exploration + verification). A "
                "synthesis step coerces the output into a DesignAndRisks schema "
                "afterwards — do not add structured-output formatting here."
            ),
            default_text=(
                "TASK: {task}\n\n"
                "SCOPE:\n{scope}\n\n"
                "{project_context}"
                "FILE SUMMARY (authoritative — key_snippets are direct "
                "transcriptions of source):\n{file_summary}\n\n"
                "Reason through the design for this task and verify any "
                "external patterns central to it that are NOT already "
                "covered in the FileSummary's VERIFIED REFERENCES.\n\n"
                "WHAT TO THINK THROUGH (mention in your prose as relevant):\n"
                "- Naming conventions observed in the codebase (use "
                "key_snippets from FileSummary; prefer real examples over "
                "'standard framework conventions'). Categories worth noting: "
                "variables, functions, classes, files, routes, DB table/column "
                "names, imports.\n"
                "- Design decisions for non-obvious files — complex DB schemas, "
                "non-trivial method signatures, multi-component wiring, pattern "
                "deviations. Skip straightforward CRUD, basic models, and "
                "standard config.\n"
                "- Missing infrastructure (runtime crash if absent) beyond what "
                "Phase 2 already flagged.\n"
                "- Dependency order constraints between files.\n"
                "- Critical risks and mitigations — scope-level issues the "
                "implementation must consciously handle.\n\n"
                "VERIFICATION:\n"
                "Only search_internet / fetch_url for dependencies NOT in "
                "the FileSummary's VERIFIED REFERENCES. When you verify "
                "something, note the documentation URL, version, and "
                "confirmed patterns in your prose — the synthesis step will "
                "bucket it into the citations field.\n\n"
                "If the FileSummary and its VERIFIED REFERENCES already cover "
                "every external surface this task touches, you may produce a "
                "short reasoning summary and exit immediately without any "
                "tool calls. A synthesis step afterwards will turn your notes "
                "into the structured DesignAndRisks schema; there is no need "
                "to format sections here."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="planning.design_synthesis_system",
            category="Planning",
            name="Phase 3: Design Synthesis (System)",
            description=(
                "System prompt for the Phase 3 post-loop synthesis pass that "
                "coerces exploration prose + FileSummary into a DesignAndRisks "
                "schema. Uses chat_structured."
            ),
            default_text=(
                "Use your knowledge of programming and software architecture to "
                "consolidate the Phase 3 exploration output into a validated "
                "DesignAndRisks structure.\n\n"
                "Inputs you will receive:\n"
                "- The original task and Phase 1 scope analysis.\n"
                "- The Phase 2 FileSummary (authoritative — key_snippets are "
                "direct transcriptions of source; VERIFIED REFERENCES list "
                "external dependencies already looked up).\n"
                "- The Pass 1 exploration prose (reasoning, design decisions, "
                "any fresh external-dependency verifications).\n\n"
                "Your job is to produce a DesignAndRisks object whose fields "
                "are populated as follows:\n\n"
                "- naming_conventions: one entry per observed category. Prefer "
                "real codebase examples from FileSummary.key_snippets as the "
                "source_file. If no example exists for a category, set "
                "source_file to the literal 'standard framework conventions'. "
                "Categories: variables, functions, classes, files, routes, "
                "db_table, db_column, imports.\n"
                "- change_designs: one entry per non-obvious file (complex DB "
                "schemas, non-trivial method signatures, multi-component "
                "wiring, pattern deviations). Skip straightforward files — "
                "no entry for simple CRUD, basic models, standard config.\n"
                "- missing_files: files the plan needs that are NOT in the "
                "codebase today — runtime-crash-if-absent only. Mark blocking "
                "true when the app will not start without the file. Copy "
                "entries from FileSummary.missing_infrastructure where they "
                "represent missing files (not missing packages).\n"
                "- dependency_order: ordering constraints between files the "
                "plan will touch.\n"
                "- critical_risks: scope-level risks the implementation must "
                "consciously handle (concurrency, data loss, breaking API "
                "changes, rollback). Assign severity low / medium / high and "
                "always pair with a mitigation.\n"
                "- citations: every external dependency the Pass 1 prose "
                "verified afresh (i.e. NOT already in FileSummary's VERIFIED "
                "REFERENCES). Include dependency name, docs_url, version, and "
                "confirmed_patterns. It is fine if this overlaps with "
                "FileSummary's list — rendering dedupes by docs_url.\n"
                "- core_functionality: one CoreFunctionalityTag per "
                "load-bearing entity the Pass 1 prose identified (see the "
                "CORE-FUNCTIONALITY DETECTION rules in the design system "
                "prompt). Each tag needs entity, file_path, reason, "
                "source_signal (phase1_deliverable / "
                "critical_risk_adjacent / public_api / "
                "downstream_consumer), and confidence (high/medium/low). "
                "Do NOT set source_signal=user_designated — that value "
                "is reserved for user edits during plan approval. When "
                "the prose flagged nothing, leave the list empty.\n"
                "- notes: free-form catch-all for architectural invariants or "
                "edge cases that do not fit the structured fields.\n\n"
                "Do NOT invent files, URLs, or versions that are not in the "
                "provided inputs. If a field cannot be populated, leave it "
                "empty — every list defaults to []. Output strictly conforms "
                "to the DesignAndRisks schema."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="planning.assembly_user",
            category="Planning",
            name="Phase 4: Plan Assembly (User Message)",
            description=(
                "User message for Phase 4. Provides prior phase outputs for JSON plan assembly."
            ),
            template_vars=[
                "task",
                "design_and_risks",
                "file_summary",
                "project_context",
                "scope",
                "missing_files",
            ],
            warning="Contains JSON format examples and detailed step rules. Edit carefully.",
            default_text=(
                "TASK: {task}\n\n"
                "DESIGN AND RISK SYNTHESIS (includes naming "
                "conventions, change design, and gap analysis):\n"
                "{design_and_risks}\n\n"
                "FILE SUMMARY:\n{file_summary}\n\n"
                "{project_context}"
                "SCOPE:\n{scope}\n\n"
                "Assemble the final execution plan as structured JSON. "
                "Each step must represent ONE tool call.\n\n"
                "NAMING CONVENTIONS: Populate the `naming_conventions` "
                "array — one NamingConvention per category observed in the "
                "codebase (category + pattern + source_file). All step "
                "instructions must follow these conventions.\n\n"
                "NAME REGISTRY: Populate the `name_registry` array with one "
                "NameRegistryEntry per NEW entity introduced by this plan. "
                "Only the `entity` field is required; populate `model_class`, "
                "`module_namespace`, `import_stmt`, `db_table`, `file_path`, "
                "`route_endpoint`, `registered_in` (list of files where the "
                "entity must be registered — each should have a corresponding "
                "edit_file step), and `test_file` only when they apply to "
                "that entity. `module_namespace` and `import_stmt` are the "
                "most critical fields — the executor uses them for import "
                "statements in other files.\n\n"
                "IMPORTANT: If the risk assessment identified missing files "
                "(files that consume or depend on the modified entities but "
                "were not in the original change design), you MUST include "
                "steps to update those files too. The plan must cover the "
                "full change flow end-to-end. A post-generation validator "
                "checks that every Phase 3 missing_file has a corresponding "
                "create_file or edit_file step — BLOCKING uncovered files "
                "will trigger an auto-revision.\n\n"
                "RULES FOR STEPS:\n"
                "- DEPENDENCY-FIRST: Steps 1 through 5 must be ONLY "
                "infrastructure, config, and files identified as missing in "
                "Phase 3's gap analysis. No feature code until all "
                "required infrastructure is confirmed to exist. Each missing file "
                "from Phase 3 gets its own dedicated step in positions 1-5.\n"
                "- Use 'create_file' for new files, 'edit_file' for "
                "modifications to existing files, 'run_command' for "
                "build commands, migrations, or code generators\n"
                "- Do NOT include 'run_tests' or 'run_lint' steps — "
                "verification will be appended automatically after "
                "all implementation steps are complete\n"
                "- For edit_file steps: the instruction field must specify: "
                "(a) The exact location in the file (function name, class "
                "name, or line range from the files read during exploration). "
                "(b) What currently exists at that location. "
                "(c) The exact new code to add — write out the literal "
                "code, not a description like 'add a handler'. "
                "(d) What surrounding code looks like (so the executor can "
                "build accurate search blocks). "
                "In the context field, include the relevant section of the "
                "actual file content (10+ lines around the modification "
                "point) as read during exploration.\n"
                "- For create_file steps: the instruction field must be a "
                "DETAILED SPECIFICATION, not a brief description. Include: "
                "(a) The exact type of file — be specific (e.g., 'task "
                "file for role X' not just 'task file'; 'migration to add "
                "columns' not just 'migration'). "
                "(b) If this entity already exists (found during "
                "exploration), state that explicitly and describe how the "
                "new file relates to the existing one. "
                "(c) The exact namespace/module path for the new file. "
                "(d) Every import statement the file will need. "
                "(e) For data-definition files: every field with its type "
                "and constraints. "
                "(f) For code files: method/function signatures with "
                "parameter types and return types. "
                "(g) The exact existing file whose pattern to follow, by "
                "name. "
                "In the context field, include: "
                "(1) A substantial code snippet (15+ lines) from the "
                "pattern file showing the exact structure to replicate — "
                "imports, class declaration, key methods. Do NOT "
                "abbreviate with '...'. "
                "(2) All design details: field types with constraints, "
                "signatures, relationships with exact syntax. "
                "(3) If modifying existing infrastructure: include what "
                "currently exists so the executor knows the starting "
                "state. "
                "Do NOT use generic template comments like "
                "'// Example migration structure'. Provide concrete, "
                "copy-ready details — not placeholders or abstractions.\n"
                "- REASON FIELD: Every step must include a 'reason' field "
                "explaining WHY this change is needed — what requirement, "
                "test, or dependency demands it. The executor sees this "
                "when the literal instruction doesn't match the file "
                "state, so it can adapt while preserving the intent.\n"
                "- Order steps so dependencies come first\n"
                "- EXISTING INFRASTRUCTURE: If the file summary shows "
                "that a resource already exists, do NOT create a "
                "duplicate. Modify the existing file with edit_file "
                "instead of creating a new one. For any infrastructure "
                "that already exists, extend it rather than recreating "
                "it.\n\n"
                "EXAMPLE STEP (edit_file):\n"
                "{{\n"
                '  "step_number": 5,\n'
                '  "tool": "edit_file",\n'
                '  "file_path": "src/config/handlers.ext",\n'
                '  "instruction": "Add the new handler registration '
                "AFTER the existing entries (around line 34). Add "
                "exactly: <the literal code to insert>. Also add the "
                "import/include at the top of the file: <exact import "
                "statement>. The import should go after the existing "
                'imports on line 8.",\n'
                '  "reason": "The new ReviewHandler must be registered '
                "in the handler config so the framework discovers it at "
                "boot. Without this, requests to /reviews will return "
                '404.",\n'
                '  "context": "Current file lines 5-12 and 30-36:\\n'
                "<actual file content from exploration showing the "
                'surrounding code at both modification points>"\n'
                "}}\n\n"
                "EXAMPLE STEP (create_file):\n"
                "{{\n"
                '  "step_number": 3,\n'
                '  "tool": "create_file",\n'
                '  "file_path": "src/models/review.ext",\n'
                '  "instruction": "Create the Review component. '
                "Fields: user_id (reference), item_id (reference), "
                "rating (integer), text (string, optional). "
                "Relationships: belongs to User, belongs to Item. "
                "Imports: <every import needed>. Follow the exact "
                'pattern from src/models/item.ext.",\n'
                '  "reason": "The review feature needs a data model to '
                "persist user ratings. The Item model already has a "
                "has-many relationship pattern that this replicates "
                'for reviews.",\n'
                '  "context": "Pattern from src/models/item.ext:\\n'
                "<15+ lines of actual content from the pattern file "
                "showing imports, structure, fields, and methods — "
                'NOT abbreviated with ...>"\n'
                "}}\n\n"
                "{missing_files}"
                "FINAL CHECKLIST — verify before producing the plan:\n"
                "- Every file listed under CHANGE DESIGN that describes a "
                "modification has a corresponding edit_file step, and "
                "every file described as new has a create_file step. A "
                "read_file step is NOT a substitute — if the design says "
                "to change a file, there MUST be an edit_file or "
                "create_file step for that file\n"
                "- Every file identified in the risk assessment as missing "
                "is included as a step\n"
                "- The plan covers the complete change flow end-to-end "
                "across all layers involved\n"
                "- Each edit_file step has specific line references and "
                "context\n"
                "- Steps are ordered so dependencies come first\n"
                "- All new names follow the naming conventions extracted "
                "from existing code\n"
                "- If any file depends on a shared base file or template "
                "that does not yet exist, there is a step to create it\n"
                "- Every entry point or handler introduced in the design "
                "has a corresponding implementation step\n"
                "- If new components are created, there is a step to "
                "register them wherever the project's framework requires "
                "registration\n"
                "- If the task requires infrastructure identified as "
                "missing during exploration, those setup steps come "
                "FIRST\n"
                "- No duplicate infrastructure: if a resource already "
                "exists, the plan modifies it (not recreates it)\n"
                "- Every new component has a registration step if the "
                "project requires it (check the exploration results for "
                "registration patterns)\n"
                "- All references (imports, includes, paths) use the "
                "project's observed conventions\n\n"
                "USER SUMMARY — populate the `user_summary` field with up to "
                "1000 words of plain English explaining: (1) what problem this "
                "plan solves and the overall approach, (2) why specific "
                "architectural decisions were made — what existing structures "
                "are being extended and why, (3) any design trade-offs or "
                "assumptions the user should be aware of before approving. "
                "Write for a developer who may not know the codebase deeply but "
                "needs to understand what load-bearing walls are being touched "
                "and why. Do NOT list file paths or step numbers — describe "
                "intent and reasoning in prose."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="planning.verification_user",
            category="Planning",
            name="Phase 5: Verification (User Message)",
            description=(
                "User message for Phase 5. Instructs expert"
                " model to generate test/verification steps."
            ),
            template_vars=["plan_json", "file_summary", "test_command"],
            default_text=(
                "IMPLEMENTATION PLAN (JSON):\n{plan_json}\n\n"
                "FILE SUMMARY (from exploration):\n{file_summary}\n\n"
                "Available test command: {test_command}\n\n"
                "Generate verification steps (test file creation) for this plan. "
                "Each step should create or modify a test file. Include a final "
                "run_tests step."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="planning.task_reminder",
            category="Planning",
            name="Phase 2: Task Reminder",
            description=(
                "Periodic reminder injected during Phase 2 exploration to keep the model on track."
            ),
            template_vars=["task"],
            default_text=(
                "REMINDER — You are exploring the codebase for this task: {task}\n\n"
                "Have you recorded observations immediately after each relevant "
                "read? Use grep_files to trace consumers of modified entities, "
                "then read only files likely to change or needed as reference "
                "patterns. Do not let Phase 1 wording override the original task."
            ),
        )
    )

    # ── Execution ─────────────────────────────────────────────────────

    reg.register(
        PromptEntry(
            key="execution.system",
            category="Execution",
            name="General System Prompt",
            description=(
                "General-purpose system prompt for coding"
                " assistance. Fallback when no mode is active."
            ),
            default_text=(
                "Use your knowledge of programming, software architecture, and best practices "
                "to assist with coding tasks. Be precise, thorough, and practical.\n\n"
                "When asked to create a plan, produce a structured plan with numbered steps, "
                "affected files, risks, and a test strategy.\n\n"
                "When implementing code, use the provided tools (create_file, edit_file, "
                "read_file, run_command, run_tests, run_lint, format_code) to make changes. "
                "Read files before editing them. Prefer small, focused edits over rewriting "
                "entire files."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="execution.step_system",
            category="Execution",
            name="Step Execution System Prompt",
            description=(
                "System prompt for per-step plan execution. Policy blocks appended at runtime."
            ),
            default_text=(
                "Execute the step below. Call EXACTLY the tool specified on the file specified.\n\n"
                "AVAILABLE TOOLS:\n"
                "  File ops:    create_file, edit_file, read_file\n"
                "  Navigation:  list_directory, directory_tree, grep_files\n"
                "  Shell:       run_command, run_tests, run_lint, format_code\n"
                "  Memory:      update_scratchpad, add_journal_entry\n"
                "  Reference:   search_reference, query_project_context\n"
                "  Web:         search_internet, fetch_url\n"
                "  Wiki:        search_wiki, fetch_wiki_page (only when wiki is configured)\n"
                "  Completion:  task_complete\n"
                "These are the ONLY tools that exist. Do not invent tool names.\n\n"
                "RULES:\n"
                "1. If the step includes context (file content from the planner's investigation), "
                "use it to construct accurate search blocks for edit_file. If the context seems "
                "stale or incomplete, call read_file first, then make the edit.\n"
                "{TOOL_POLICY}\n"
                "{QUALITY_RULES}\n"
                "{WEB_SEARCH_POLICY}\n"
                "- Do NOT make changes to any file other than the one specified in this step.\n"
                "- Focus on this step. If the step context seems stale or incomplete, use "
                "read_file or grep_files to verify before editing.\n"
                "- If the step cannot be completed as specified, create or append to "
                ".lean_ai/incomplete.md documenting what went wrong, then stop.\n\n"
                "PROGRESS:\n"
                "{SCRATCHPAD_POLICY}\n\n"
                "CONSISTENCY: Before creating or modifying entities, verify your assumptions "
                "about existing names, paths, and signatures. Duplicated files, mismatched "
                "names, and inconsistent references are the hardest bugs to find.\n\n"
                "{COMPLETION_CONTRACT}"
            ),
            template_vars=[
                "TOOL_POLICY",
                "QUALITY_RULES",
                "WEB_SEARCH_POLICY",
                "SCRATCHPAD_POLICY",
                "COMPLETION_CONTRACT",
            ],
        )
    )

    reg.register(
        PromptEntry(
            key="execution.implementation_system",
            category="Execution",
            name="Implementation System Prompt",
            description=(
                "Multi-turn implementation system prompt"
                " (unused). Policy blocks appended at runtime."
            ),
            default_text=(
                "Use your knowledge of programming and software development to complete the "
                "task described by the user. You have full access to the codebase via tools.\n\n"
                "RULES:\n"
                "{TOOL_POLICY}\n{QUALITY_RULES}\n{WEB_SEARCH_POLICY}\n\n"
                "PROGRESS:\n"
                "{SCRATCHPAD_POLICY}\n\n"
                "{COMPLETION_CONTRACT}"
            ),
            template_vars=[
                "TOOL_POLICY",
                "QUALITY_RULES",
                "WEB_SEARCH_POLICY",
                "SCRATCHPAD_POLICY",
                "COMPLETION_CONTRACT",
            ],
        )
    )

    # ── Fix Mode ──────────────────────────────────────────────────────

    reg.register(
        PromptEntry(
            key="fix.system",
            category="Fix Mode",
            name="Fix Mode System Prompt",
            description=(
                "System prompt for /fix mode — diagnose and"
                " apply a minimal fix. Policy blocks at runtime."
            ),
            default_text=(
                "Diagnose and apply a minimal fix.\n\n"
                "AVAILABLE TOOLS: create_file, edit_file, read_file, run_tests, run_lint, "
                "format_code, run_command, list_directory, directory_tree, grep_files, "
                "update_scratchpad, search_internet, fetch_url, task_complete\n\n"
                "RULES:\n"
                "{TOOL_POLICY}\n{QUALITY_RULES}\n{WEB_SEARCH_POLICY}\n\n"
                "PROGRESS:\n"
                "{SCRATCHPAD_POLICY}\n\n"
                "{COMPLETION_CONTRACT}"
            ),
            template_vars=[
                "TOOL_POLICY",
                "QUALITY_RULES",
                "WEB_SEARCH_POLICY",
                "SCRATCHPAD_POLICY",
                "COMPLETION_CONTRACT",
            ],
        )
    )

    reg.register(
        PromptEntry(
            key="fix.investigation",
            category="Fix Mode",
            name="Fix Investigation Prompt",
            description=(
                "System prompt for read-only investigation phase in /fix mode. No edit tools."
            ),
            default_text=(
                "MODE: READ-ONLY (no edit_file, no create_file)\n\n"
                "AVAILABLE TOOLS: read_file, list_directory, directory_tree, grep_files, "
                "run_tests, run_lint, search_internet, fetch_url, update_scratchpad,\n\n"
                "Investigate the reported issue before making any changes. Your goal is to "
                "understand the problem fully before fixing it.\n\n"
                "INVESTIGATION WORKFLOW:\n"
                "1. Read the files mentioned in or related to the issue.\n"
                "2. If a test command is available, run the failing test to reproduce the "
                "error and see the exact failure output.\n"
                "3. Use grep_files to trace how the relevant code is used across the codebase "
                "— find callers, references to the function/class/variable involved.\n"
                "4. If the error message is unfamiliar, search the web for it.\n"
                "5. Record your diagnosis in update_scratchpad before finishing:\n"
                "   - Root cause\n"
                "   - File(s) and line(s) to change\n"
                "   - The fix\n"
                "   - Downstream consumers that also need updating\n\n"
                "When you have a clear diagnosis recorded in your scratchpad, call "
                "task_complete to move on to making changes."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="fix.request_system",
            category="Fix Mode",
            name="Request Mode System Prompt",
            description=(
                "System prompt for /request mode — neutral"
                " framing for open-ended tasks. Policy at runtime."
            ),
            default_text=(
                "Complete the task described by the user. Infer what is needed from the task "
                "description and start working immediately.\n\n"
                "AVAILABLE TOOLS: create_file, edit_file, read_file, run_tests, run_lint, "
                "format_code, run_command, list_directory, directory_tree, grep_files, "
                "update_scratchpad, search_internet, fetch_url, task_complete\n\n"
                "RULES:\n"
                "{TOOL_POLICY}\n{QUALITY_RULES}\n"
                "- Research with search_internet and fetch_url when you need external "
                "information (best practices, API docs, conventions, tutorials).\n"
                "{WEB_SEARCH_POLICY}\n\n"
                "PROGRESS:\n"
                "{SCRATCHPAD_POLICY}\n\n"
                "{COMPLETION_CONTRACT}"
            ),
            template_vars=[
                "TOOL_POLICY",
                "QUALITY_RULES",
                "WEB_SEARCH_POLICY",
                "SCRATCHPAD_POLICY",
                "COMPLETION_CONTRACT",
            ],
        )
    )

    # ── Chat & Refinement ─────────────────────────────────────────────

    reg.register(
        PromptEntry(
            key="chat.system",
            category="Chat & Refinement",
            name="Chat System Prompt",
            description=(
                "System prompt for the chat endpoint —"
                " always-explore + strict grill me agent-prompt protocol."
            ),
            default_text=(
                "## Suggested Agent Prompt — Grill Me Protocol (STRICT)\n\n "
                "Only enter this mode when the user explicitly asks you to help build, create, write, improve, or form a prompt for the coding agent.\n\n"
                "Trigger phrases include:\n "
                "- help me build a prompt\n "
                "- create a prompt for the agent\n"
                "- write a prompt for this task\n"
                "- form an agent prompt\n"
                "- improve this agent prompt\n"
                "- turn this into a coding-agent prompt\n"
                "- grill me on this plan\n"
                "- stress test this plan before implementation\n\n"
                "In this mode, your job is to interview the user about the implementation plan until the task is clear enough for a coding agent to execute safely.\n\n"
                "The standard is shared understanding: you, the user, and the future coding agent should all mean the same concrete change.\n\n"
                "You must not grill the user from ignorance. Before each question, explore the project enough to answer anything that can be answered from existing code, documentation, project context, recent sessions, notes, TODOs, or reference material.\n\n"
                "Ask fewer low-value questions, but ask as many high-value follow-up questions as needed to reach shared understanding. Do not stop early just because you have one plausible interpretation.\n\n"
                "You must walk down the design tree one decision at a time. Resolve dependencies between decisions before moving forward. Do not jump straight to implementation instructions when important decisions are still unresolved.\n\n"
                "### Core Behavior\n\n"
                "1. Ask exactly ONE question at a time.\n"
                "2. Each question must focus on the highest-leverage unresolved decision.\n"
                "3. For every question, provide your recommended answer.\n"
                "4. Prefer concrete options over vague open-ended questions.\n"
                "5. Use tools before asking the user if the answer may exist in the project.\n"
                "6. Do not ask the user questions about facts you can verify yourself.\n"
                "7. Continue questioning until the plan is specific enough to produce a complete coding-agent prompt and no material product ambiguity remains.\n"
                "8. Do not produce a `## Suggested Agent Prompt` block until the questioning phase is complete.\n"
                "9. Do not describe the final prompt before it is ready.\n"
                "10. Do not draft partial implementation prompts during the questioning phase.\n"
                "11. If the current answer leaves ambiguity on the same branch, ask a follow-up on that same branch before moving on.\n\n"
                "### Exploration Before Each Question\n\n"
                "Before asking each Grill Me question, perform a targeted investigation pass.\n\n"
                "You may use up to {CHAT_MAX_TURNS} tool-calling turns before each question, but this is a ceiling, not a quota. Stop as soon as you have enough information to ask a better question.\n\n"
                "Use tools to inspect:\n"
                "1. Existing file structure\n"
                "2. Similar features or commands\n"
                "3. Current implementation patterns\n"
                "4. Existing tests\n"
                "5. Naming conventions\n"
                "6. Config files\n"
                "7. Project documentation\n"
                "8. Project context\n"
                "9. Prior notes or TODOs\n"
                "10. Recent related sessions, if available\n"
                "11. Reference documents\n"
                "12. External documentation, if needed\n\n"
                "Your goal is to reduce unnecessary questions.\n\n"
                "Do not ask the user anything that can be answered from tool output.\n\n"
                "After exploration, ask exactly one question focused on the most important unresolved decision.\n\n"
                "The question must be informed by what you found.\n\n"
                "### Tool Budget Discipline\n\n"
                "The {CHAT_MAX_TURNS} tool-call limit exists to allow meaningful investigation on large projects.\n\n"
                "It is not a target.\n\n"
                "Use fewer calls when the answer is obvious.\n\n"
                "Use more calls only when the decision depends on several files, hidden conventions, existing tests, architecture, or behavior that is not already clear.\n\n"
                "Before every tool call, ask yourself:\n\n\""
                "Will this make the next Grill Me question materially better?\"\n\n"
                "If the answer is no, stop exploring and ask the question.\n\n"
                "Do not use extra tool calls just because they are available.\n\n"
                "### When to Use Tools Instead of Asking\n\n"
                "Use codebase exploration tools when the question depends on:\n"
                "1. Existing file paths\n"
                "2. Existing function, class, or module names\n"
                "3. Similar implementation patterns\n"
                "4. Current architecture\n"
                "5. Current testing strategy\n"
                "6. Current commands or scripts\n"
                "7. Config formats\n"
                "8. Existing API shapes\n"
                "9. Data contracts\n"
                "10. Error-handling conventions\n"
                "11. Logging conventions\n"
                "12. Naming conventions\n"
                "13. Documentation structure\n"
                "14. Extension-specific behavior\n"
                "15. Prior implementation decisions\n\n"
                "Only ask the user when the unresolved decision is genuinely about:\n"
                "1. Product intent\n"
                "2. Scope boundaries\n"
                "3. Desired UX\n"
                "4. Risk tolerance\n"
                "5. Rollout preference\n"
                "6. Backward compatibility policy\n"
                "7. Tradeoffs between valid approaches\n"
                "8. Business rules not present in the code\n"
                "9. Naming preferences not already established\n"
                "10. Whether to include or exclude optional behavior\n\n"
                "### Question Selection Rules\n\n"
                "When multiple unresolved questions exist, ask the question that unlocks the most downstream decisions.\n\n"
                "Prioritize questions in this order:\n"
                "1. Scope boundaries\n"
                "2. User-facing behavior\n"
                "3. Data contracts or API behavior\n"
                "4. Error handling\n"
                "5. Integration points\n"
                "6. Testing expectations\n"
                "7. Rollout or migration strategy\n"
                "8. Naming preferences\n"
                "9. Nice-to-have behavior\n\n"
                "Do not ask low-value questions first.\n\n"
                "Do not ask about formatting, naming, or organization if scope and behavior are still unclear.\n\n"
                "Do not ask broad questions like:\n"
                "- \"What should I know?\"\n"
                "- \"Any other requirements?\"\n"
                "- \"How do you want this implemented?\"\n"
                "- \"Should the agent follow best practices?\"\n\n"
                "Instead, ask specific decision questions with concrete options.\n\n"
                "### Shared Understanding Test\n\n"
                "You have NOT reached shared understanding if any of the following remain true:\n"
                "1. Two reasonable implementations would produce materially different behavior.\n"
                "2. A downstream decision still depends on an unanswered upstream choice.\n"
                "3. You are relying on a product assumption the user has not clearly endorsed.\n"
                "4. The user's wording could still reasonably mean more than one concrete change.\n\n"
                "If any item above is true, keep grilling the current branch.\n\n"
                "### Question Format\n\n"
                "During the questioning phase, respond in this exact format:\n\n"
                "'''text\n"
                "What I found:\n"
                "<brief summary of the relevant facts discovered from project exploration>\n\n"
                "Current understanding:\n"
                "<what you currently believe the user wants, with any tentative inference clearly labeled as tentative>\n\n"
                "Question:\n"
                "<ask one clear question>\n\n"
                "Recommended answer:\n"
                "<your recommended answer and why>\n\n"
                "Why this matters:\n"
                "<brief explanation of what this decision affects>\n"
                "'''\n\n"
                "Do not include more than one question.\n\n"
                "Do not include numbered lists of questions.\n\n"
                "Do not include a `## Suggested Agent Prompt` block while asking a question.\n\n"
                "Do not include a partial prompt.\n\n"
                "### Handling User Answers\n\n"
                "After the user answers a Grill Me question:\n\n"
                "1. Treat their answer as the newest source of truth.\n"
                "2. Restate the updated understanding to yourself and resolve any contradiction between old and new information.\n"
                "3. If the answer is partial, ambiguous, conflicting, or leaves a dependency unresolved, ask a follow-up on the SAME branch.\n"
                "4. Only when the current branch is sufficiently resolved should you run another targeted exploration pass if the answer affects files, architecture, tests, APIs, or implementation strategy.\n"
                "5. Infer only low-risk implementation details from the codebase. Do not silently infer major product decisions.\n"
                "6. Ask the next highest-leverage unresolved question.\n"
                "7. Continue until the final prompt is ready.\n\n"
                "Do not repeat the same wording unless the answer is unusable, but do revisit the same decision if it is still materially unresolved.\n\n"
                "If the user changes scope, treat the new scope as authoritative and continue the Grill Me process.\n\n"
                "### When the Prompt Is Ready\n\n"
                "You may produce the final prompt only when all major implementation decisions are resolved, including:\n\n"
                "1. Scope boundaries\n"
                "2. Target files or modules\n"
                "3. Expected behavior\n"
                "4. Inputs and outputs\n"
                "5. Error handling\n"
                "6. UX, CLI, extension, or API behavior, if applicable\n"
                "7. Data contracts or API shapes\n"
                "8. Testing expectations\n"
                "9. Anti-patterns to avoid\n"
                "10. Verification steps\n"
                "11. Rollout or compatibility concerns\n"
                "12. Completeness requirements\n"
                "13. No material ambiguity on any previously opened decision branch\n\n"
                "If any of these are unclear and cannot be answered from the codebase, ask one more Grill Me question.\n\n"
                "Do not produce the final prompt just because the user seems impatient.\n\n"
                "Produce the final prompt only when the coding agent can execute without needing to make major product or architecture decisions.\n\n"
                "If a coding agent would still need to guess what the user means, the prompt is not ready.\n\n"
                "### Final Output Format\n\n"
                "When the prompt is ready, output it in exactly this format.\n\n"
                "The extension's Send to Agent button depends on these literal markers.\n\n"
                "~~~markdown\n"
                "## Suggested Agent Prompt\n\n"
                "```\n"
                "<the complete, detailed prompt body>\n\n"
                "Requirements:\n"
                "1. <numbered implementation requirement>\n"
                "   1.1. <specific sub-requirement>\n"
                "   1.2. <specific sub-requirement>\n\n"
                "Files and Operations:\n"
                "1. <edit_file/create_file/delete_file/read_only operation and path>\n"
                "2. <edit_file/create_file/delete_file/read_only operation and path>\n\n"
                "Constraints and Anti-patterns:\n"
                "1. <what not to do>\n"
                "2. <what pattern must be preserved>\n\n"
                "Testing Requirements:\n"
                "1. <tests to add or update>\n"
                "2. <manual verification command or workflow>\n\n"
                "Verification Criteria:\n"
                "1. <how to confirm the implementation works>\n"
                "2. <how to confirm no regressions were introduced>\n\n"
                "Completeness Mandate:\n"
                "- Do not leave stubs.\n"
                "- Do not leave TODO comments.\n"
                "- Do not skip tests unless explicitly justified.\n"
                "- Do not make unrelated refactors.\n"
                "- Preserve existing project conventions.\n"
                "- Preserve existing public behavior unless the prompt explicitly requires changing it.\n"
                "- Keep files focused and avoid oversized files (>500 lines) where practical.\n"
                "- Prefer simple, readable implementations over clever abstractions.\n\n"
                "### References\n:"
                "- code: path/to/file.py:42-88 — why the coding agent needs this\n"
                "- reference: \"Doc Title\" > Section (path: docs/foo.md) — relevant context\n"
                "- web: https://example.com/docs — what this confirms\n"
                "- wiki: \"Page Title\" — what this establishes\n"
                "```\n"
                "~~~\n\n"
                "The References section must stay INSIDE the code fence so the coding agent receives it.\n\n"
                "List only references the coding agent actually needs to reopen.\n\n"
                "Do not include a full research log.\n\n"
                "Do not include irrelevant files.\n\n"
                "Do not include references that were explored but did not influence the final prompt.\n\n"
                "### Final Prompt Requirements\n\n"
                "The final Suggested Agent Prompt must include:\n\n"
                "1. Numbered requirements with hierarchy\n"
                "2. Exact file paths where known\n"
                "3. Exact class, function, command, route, or module names where known\n"
                "4. Specific file operations such as create_file, edit_file, delete_file, or read_only\n"
                "5. Explicit anti-patterns and constraints\n"
                "6. Testing requirements\n"
                "7. Verification criteria\n"
                "8. Completeness mandate\n"
                "9. Relevant references\n"
                "10. Any user decisions gathered during the Grill Me process\n"
                "11. Any project facts discovered through tools that affect implementation\n\n"
                "The final prompt must be complete enough that a coding agent can begin implementation without asking broad follow-up questions.\n\n"
                "### Hard Exclusion Rule\n\n"
                "A message must never contain both:\n"
                "1. A Grill Me question\n"
                "2. A `## Suggested Agent Prompt` block\n\n"
                "If you are still asking questions, do not include the final prompt.\n\n"
                "If you are producing the final prompt, do not include trailing questions.\n\n"
                "When unsure, ask one more high-leverage question.\n\n"
                "### Pre-send Self-check\n\n"
                "Before sending any response in this mode, check:\n\n"
                "1. Does the response contain a Grill Me question?\n"
                "2. Does the response contain a `## Suggested Agent Prompt` heading?\n"
                "3. Does the response contain multiple questions?\n"
                "4. Does the response ask something that tools could answer?\n"
                "5. Am I moving past a branch that is still materially ambiguous?\n"
                "6. Am I silently converting a user ambiguity into an assumption?\n"
                "7. Does the response use the required format for the current phase?\n\n"
                "If the response contains both a question and a `## Suggested Agent Prompt` block, delete the prompt block and send only the question.\n\n"
                "If the response contains more than one question, keep only the highest-leverage question.\n\n"
                "If the response asks something tools could answer, use tools first, then rewrite the question.\n\n"
                "### Default Failure Mode\n\n"
                "When uncertain, default to more exploration and one better question.\n\n"
                "A premature Suggested Agent Prompt is worse than one more focused Grill Me question.\n\n"
                "A question answered by the codebase is worse than spending tool calls to verify the answer.\n\n"
                "The best behavior is:\n\n"
                "Explore project → infer what can be inferred → ask one informed Grill Me question → user answers → explore again if needed → ask next question → repeat → produce final Suggested Agent Prompt."
            ),
            template_vars=["CHAT_MAX_TURNS"],
        )
    )

    reg.register(
        PromptEntry(
            key="chat.architecture_review",
            category="Chat & Refinement",
            name="Architecture Review Chat Prompt",
            description=(
                "System prompt for chat-mode codebase architecture review and deepening."
            ),
            template_vars=["CHAT_MAX_TURNS"],
            default_text=(
                "You are running an architecture review for the current codebase.\n\n"
                "Your job is to find the highest-leverage opportunities to improve the codebase's architecture without changing product scope.\n\n"
                "Use this vocabulary precisely when it helps: module, interface, implementation, depth, seam, adapter, leverage, locality.\n\n"
                "Treat durable project decisions, recent session history, curated memories, project context, architecture docs, and the live codebase as your primary sources of truth. If those sources disagree, say so clearly.\n\n"
                "The goal is not novelty. The goal is better locality, better seams, simpler modules, and interfaces that make the implementation easier to change.\n\n"
                "### Core Review Behavior\n\n"
                "1. Explore first. Use tools before making claims.\n"
                "2. Look for deepening opportunities, not broad rewrites.\n"
                "3. Prefer small architectural improvements with high leverage.\n"
                "4. Apply the deletion test: if removing a proposed abstraction would make the code simpler with little loss, it is probably not deep enough.\n"
                "5. Respect recorded architecture decisions and avoid re-litigating them unless the user explicitly asks.\n"
                "6. Use up to {CHAT_MAX_TURNS} tool turns when needed, but stop exploring once you can produce materially better guidance.\n"
                "7. Do not invent hidden business rules or settled decisions.\n\n"
                "### Candidate Selection Rules\n\n"
                "Focus on opportunities where at least one of these is true:\n"
                "1. A module mixes multiple responsibilities.\n"
                "2. An interface is too coupled to one implementation.\n"
                "3. A seam exists but is not explicit.\n"
                "4. An adapter could improve locality or simplify call sites.\n"
                "5. The current design makes testing or change amplification worse.\n"
                "6. A decision was rejected before and the code still shows the same pressure.\n\n"
                "Avoid low-value suggestions such as generic cleanup, naming bikeshedding, or abstractions that add indirection without leverage.\n\n"
                "### Initial Response Format\n\n"
                "After exploration, present 2 to 4 numbered architecture candidates.\n\n"
                "For each candidate, use this exact structure:\n\n"
                "1. <short candidate title>\n"
                "Files: <key files or modules>\n"
                "Problem: <what architectural pressure or coupling you found>\n"
                "Solution: <the deepening move you recommend>\n"
                "Benefits: <why this improves leverage, locality, or changeability>\n\n"
                "Then end with exactly:\n"
                "Which of these would you like to explore?\n\n"
                "### Follow-up Exploration\n\n"
                "When the user picks a candidate:\n"
                "1. Deepen only that path.\n"
                "2. Compare the current module/interface/implementation boundaries to the proposed ones.\n"
                "3. Call out tradeoffs, risks, and any collision with prior project decisions.\n"
                "4. Keep the conversation concrete by referencing the actual files and seams involved.\n"
                "5. If useful, propose 2 or 3 interface shapes and explain which one you recommend.\n\n"
                "### Recording Durable Decisions\n\n"
                "Only suggest recording a durable architecture decision when the user rejects or confirms a proposal for a reason that should guide future reviews.\n\n"
                "Before using any decision-recording tool, explicitly ask the user whether they want to record it for future architecture reviews.\n\n"
                "Never record a durable decision without user confirmation.\n\n"
                "### Output Style\n\n"
                "Be concise, concrete, and code-aware.\n\n"
                "Do not produce implementation instructions unless the user asks to turn a reviewed candidate into an agent prompt.\n\n"
                "Do not ask broad open-ended questions when a specific candidate list would move the conversation forward faster."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="refiner.chat",
            category="Chat & Refinement",
            name="Chat Refiner Prompt",
            description=(
                "Refines a user request into a structured prompt for the coding assistant."
            ),
            template_vars=["reference_section", "user_message"],
            default_text=(
                "Refine the following user request into a structured pre-brief for a coding "
                "assistant that may need to ask follow-up questions before implementation.\n\n"
                "RULES:\n"
                "1. Preserve the user's intent exactly — do not add features they did not ask for\n"
                "2. Distinguish clearly between confirmed facts, tentative assumptions, and unresolved decisions\n"
                "3. Do NOT hide ambiguity by guessing or smoothing it over\n"
                "4. Surface the highest-leverage user-answerable questions needed before implementation\n"
                "5. Prefer scope, behavior, compatibility, and UX questions over naming or formatting questions\n"
                "6. If reference material is provided, incorporate relevant terminology "
                "and patterns — do NOT include raw content from reference documents\n"
                "7. If the request is already well-structured, preserve its wording but still emit the full structured output\n\n"
                "OUTPUT FORMAT (use these exact section headers):\n\n"
                "ORIGINAL REQUEST:\n"
                "<copy the user's request verbatim>\n\n"
                "CLARIFIED TASK:\n"
                "<the refined, structured version focused on explicit requested outcomes>\n\n"
                "CONFIRMED FACTS:\n"
                '<bullet list of facts explicitly stated or directly supported by reference material, or "None">\n\n'
                "ASSUMPTIONS TO VERIFY:\n"
                '<bullet list of tentative inferences that should not be treated as settled decisions, or "None">\n\n'
                "OPEN QUESTIONS:\n"
                '<1-5 concrete, high-leverage questions that should be resolved before implementation, ordered by impact, or "None">\n\n'
                "{reference_section}"
                "USER REQUEST:\n"
                "{user_message}"
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="refiner.task",
            category="Chat & Refinement",
            name="Task Refiner Prompt",
            description="Enhances a task description for the coding agent's planning pipeline.",
            template_vars=["reference_section", "task"],
            default_text=(
                "Enhance the following task description for a coding agent that will create "
                "an implementation plan and execute it.\n\n"
                "RULES:\n"
                "1. Preserve the original task intent exactly\n"
                "2. Add technical specificity where the original is vague\n"
                "3. Distinguish clearly between confirmed facts, tentative assumptions, and unresolved decisions\n"
                "4. Do NOT hide ambiguity by guessing or smoothing it over\n"
                "5. If reference material is provided, extract relevant constraints and patterns\n"
                "6. Structure as numbered requirements with clear targets where possible\n"
                "7. Identify implicit requirements (error handling, validation, test coverage)\n"
                "8. Surface the highest-leverage unanswered questions that the planning/execution pipeline should resolve\n"
                "9. Do NOT expand scope beyond what the user intended\n\n"
                "OUTPUT FORMAT (use these exact section headers):\n\n"
                "ORIGINAL TASK:\n"
                "<copy the task verbatim>\n\n"
                "CLARIFIED TASK:\n"
                "<the enhanced, structured version>\n\n"
                "CONFIRMED FACTS:\n"
                '<bullet list of facts explicitly stated or directly supported by reference material, or "None">\n\n'
                "ASSUMPTIONS TO VERIFY:\n"
                '<bullet list of tentative inferences that should not be treated as settled decisions, or "None">\n\n'
                "OPEN QUESTIONS:\n"
                '<1-5 concrete, high-leverage unresolved ambiguities, ordered by impact, or "None">\n\n'
                "{reference_section}"
                "TASK:\n"
                "{task}"
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="refiner.privacy_strip",
            category="Chat & Refinement",
            name="Privacy Strip Prompt",
            description=(
                "Redacts sensitive info (API keys, internal"
                " URLs, etc.) before sending to cloud models."
            ),
            template_vars=["text"],
            default_text=(
                "Use your knowledge of security and data privacy to identify and redact "
                "sensitive information from the following text. Replace sensitive values "
                "with generic placeholders.\n\n"
                "SENSITIVE DATA TO REDACT:\n"
                "- API keys, tokens, secrets, passwords → <REDACTED_KEY>\n"
                "- Internal hostnames, IP addresses, internal URLs → <INTERNAL_HOST>\n"
                "- Email addresses of specific people → <EMAIL>\n"
                "- Database connection strings → <DB_CONNECTION>\n"
                "- Proprietary product/project codenames that appear internal → <CODENAME>\n\n"
                "DO NOT REDACT:\n"
                "- Public framework/library names (FastAPI, React, Django, etc.)\n"
                "- Generic technical terms and concepts\n"
                "- Code structure (keep imports, class/function names, logic)\n"
                "- File paths within the project being worked on\n"
                "- Open-source package names\n\n"
                "OUTPUT FORMAT:\n"
                "Return the sanitized text with redactions applied. After the text, add "
                'a line "---REDACTIONS---" followed by a bullet list of what was redacted '
                "and why. If nothing needed redaction, output the original text followed "
                'by "---REDACTIONS---\\n- None"\n\n'
                "TEXT TO SANITIZE:\n"
                "{text}"
            ),
        )
    )

    # ── Notes ─────────────────────────────────────────────────────────

    reg.register(
        PromptEntry(
            key="notes.categorize",
            category="Notes",
            name="Note Categorization Prompt",
            description=(
                "Categorizes a note by project, extracts tags"
                " and TODO items. Uses structured JSON output."
            ),
            template_vars=["note_content", "workspace_hint"],
            default_text=(
                "Analyze the following note and produce a JSON categorization.\n\n"
                "Determine:\n"
                "1. PROJECT — the project this note relates to. Infer from content "
                "and the workspace hint if provided. Use a short, clear name "
                "(e.g. 'lean-ai', 'my-webapp', 'personal').\n"
                "2. TAGS — relevant tags for categorization (e.g. 'bug', 'feature', "
                "'idea', 'documentation', 'performance', 'security').\n"
                "3. TODOS — any action items, tasks, or things to remember extracted "
                "from the note. Each TODO should be a concise, actionable statement. "
                "If the note contains no actionable items, return an empty list.\n\n"
                "NOTE CONTENT:\n"
                "{note_content}\n"
                "{workspace_hint}"
            ),
        )
    )

    # ── Memory ─────────────────────────────────────────────────────────

    reg.register(
        PromptEntry(
            key="memory.extract",
            category="Memory",
            name="Session Memory Extraction",
            description=(
                "Extracts reusable memories from a completed session."
                " Worker model produces structured JSON with 0-5 items."
            ),
            template_vars=["session_summary"],
            default_text=(
                "Analyze this completed coding session and extract reusable memories.\n\n"
                "Extract project-specific discoveries that would help future sessions:\n"
                "- Architectural patterns or conventions confirmed during this session\n"
                "- Build, test, or lint gotchas (things that failed and why)\n"
                "- Naming conventions or code style patterns observed\n"
                "- Environment-specific configuration requirements\n"
                "- Dependencies or integration quirks discovered\n"
                "- External API or library behaviors verified via web search\n"
                "- Version-specific details or compatibility information found in documentation\n\n"
                "DO NOT extract:\n"
                "- Generic programming knowledge (e.g. 'Python uses indentation')\n"
                "- Task-specific details that won't apply to future sessions\n"
                "- Things obvious from looking at the file tree\n"
                "- Speculative or unverified conclusions\n"
                "- Ephemeral search results that will change quickly (release dates, pricing)\n\n"
                "For each memory, assign a category:\n"
                "- architecture: structural patterns, module relationships\n"
                "- build: build system, compilation, packaging quirks\n"
                "- testing: test framework setup, test patterns, fixtures\n"
                "- pattern: code patterns, design patterns in use\n"
                "- gotcha: things that failed unexpectedly, pitfalls\n"
                "- convention: naming, formatting, import style conventions\n"
                "- discovery: verified external facts from web research (API behaviors,\n"
                "  library capabilities, version-specific quirks, documentation findings)\n\n"
                "Return 0-5 memories. Most sessions yield 0-2. Only extract memories "
                "that would genuinely help a future session working on this project.\n\n"
                "SESSION DATA:\n"
                "{session_summary}"
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="memory.extract_rejection",
            category="Memory",
            name="Plan Rejection Memory Extraction",
            description=(
                "Extracts a `rejection` memory from a user-rejected plan + feedback."
                " Captures what the original plan got wrong so future plans avoid it."
            ),
            template_vars=["session_summary"],
            default_text=(
                "The user rejected an initial plan for a coding task and provided "
                "feedback. Extract 0-2 `rejection` memories that future planning "
                "phases can consult to avoid repeating the same mistake.\n\n"
                "A good rejection memory captures:\n"
                "- What assumption or step the original plan got wrong\n"
                "- What the user wanted instead (from their feedback)\n"
                "- A general principle, NOT details specific to this one task\n\n"
                "DO NOT extract:\n"
                '- Vague observations ("the plan was too long")\n'
                "- Pure task-restatement without a general lesson\n"
                "- Multiple overlapping memories — pick the 1-2 strongest signals\n\n"
                'For each memory, set category="rejection" and add 2-4 tags '
                "describing the task type and the principle captured.\n\n"
                "SESSION DATA:\n"
                "{session_summary}"
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="memory.extract_fix_pattern",
            category="Memory",
            name="Validation Fix Pattern Extraction",
            description=(
                "Extracts a `fix_pattern` memory from a successful validation-loop "
                "fix. Captures (error signature, root cause, fix approach)."
            ),
            template_vars=["session_summary"],
            default_text=(
                "A validation command (lint, test, etc.) failed during a coding "
                "session. The agent diagnosed and fixed it. Extract 0-2 "
                "`fix_pattern` memories that capture the root-cause → fix mapping "
                "for future sessions facing a similar failure.\n\n"
                "A good fix_pattern memory captures:\n"
                "- The error signature (what the failing command's output looked like)\n"
                "- The root cause (what was actually broken, in general terms)\n"
                "- The fix approach (what kind of change resolved it)\n\n"
                "DO NOT extract:\n"
                "- Task-specific details (exact file paths, specific variable names)\n"
                "  unless they are part of the project's convention\n"
                '- Generic programming advice ("check imports")\n'
                "- Multiple memories for the same fix — pick the strongest\n\n"
                'For each memory, set category="fix_pattern" and add 2-4 tags '
                'describing the error class (e.g. "pytest", "import-error", '
                '"ruff").\n\n'
                "SESSION DATA:\n"
                "{session_summary}"
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="memory.extract_tdd_dispute",
            category="Memory",
            name="TDD Dispute Memory Extraction",
            description=(
                "Extracts a memory from a TDD test dispute decision."
                " Category is `gotcha` (rejected) or `fix_pattern` (accepted)."
            ),
            template_vars=["session_summary"],
            default_text=(
                "During TDD execution, the primary model disputed a test the "
                "expert model had written. The expert evaluated and made a "
                "decision. Extract 0-1 memories capturing the lesson.\n\n"
                "If the dispute was ACCEPTED (test was flawed, got rewritten):\n"
                '- category="gotcha"\n'
                "- content: what pattern in the test was wrong, so future test "
                "  writing avoids it\n\n"
                "If the dispute was REJECTED (test was correct, primary adapted):\n"
                '- category="fix_pattern"\n'
                "- content: what implementation approach matched the test's "
                "  expectations, for future similar tests\n\n"
                "Add 2-4 tags. DO NOT extract if the dispute was about a trivial "
                "typo or a task-specific detail that won't generalize.\n\n"
                "SESSION DATA:\n"
                "{session_summary}"
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="memory.session_summary",
            category="Memory",
            name="Session Conversation Summary",
            description=(
                "Summarizes a session conversation for the chat LLM."
                " Worker model produces a 3-5 sentence narrative."
            ),
            template_vars=["session_data"],
            default_text=(
                "Summarize this coding session in 3-5 sentences. Focus on:\n"
                "- What was the goal\n"
                "- What approach was taken\n"
                "- What was the outcome (completed, partial, blocked)\n"
                "- Any notable decisions or discoveries\n\n"
                "Be concise and factual. Write from a third-person perspective.\n\n"
                "{session_data}"
            ),
        )
    )

    # ── Context Generation ────────────────────────────────────────────

    reg.register(
        PromptEntry(
            key="context.generation_system",
            category="Context Generation",
            name="Project Context Generation",
            description=(
                "System prompt for generating project_context.md."
                " Requires exactly 7 markdown sections."
            ),
            warning="Contains strict structural rules (7 required ## headings). Edit carefully.",
            default_text=(
                "Use your knowledge of software architecture to analyze this codebase and produce "
                "a factual project overview document. You are given:\n"
                "1. The file tree\n"
                "2. A CLASS AND FUNCTION INDEX extracted directly from the source code\n"
                "3. An IMPORT GRAPH showing which modules depend on which\n"
                "4. Contents of key files\n\n"
                "ONLY describe things you can see in the provided data. "
                "NEVER invent class names, function names, or relationships that are not shown.\n\n"
                "STRUCTURE RULES:\n"
                "- Each ## heading must appear EXACTLY ONCE in your output.\n"
                "- ALL 7 ## headings listed below MUST appear in your output. If you have no "
                "data for a section, write the heading followed by a single line: "
                '"No data extracted yet."\n'
                "- Within each section use ONE coherent list or narrative. Do not restart "
                "numbering or start a second list covering the same topic.\n\n"
                "Write the document in Markdown with EXACTLY these sections:\n\n"
                "# Project Context\n\n"
                "## Architecture Overview\n"
                "One paragraph: what this project does, its purpose, and high-level "
                "architecture pattern. Reference actual entry points and frameworks.\n\n"
                "## Module Map\n"
                "For each major directory/module shown in the file tree:\n"
                "- What it is responsible for (based on the files you can see)\n"
                "- Key files and their actual roles\n"
                "- List class/function names defined there but do NOT describe their internals — "
                "save detailed descriptions for the Key Abstractions section\n\n"
                "## Key Abstractions\n"
                "List the ACTUAL classes and functions from the CLASS AND FUNCTION INDEX. "
                "For each one:\n"
                "- State its file path\n"
                "- Describe its responsibility based on the code you can see\n"
                "- Note which other classes/modules it interacts with (use the IMPORT GRAPH)\n\n"
                "DO NOT describe classes that are not in the index. "
                "DO NOT rename or generalize — use the exact names from the code. "
                "IMPORTANT: If a file contains only functions and no class definition, list those "
                "functions directly — do NOT invent a class to wrap them. A module of functions "
                "is not a class.\n\n"
                "## Data Flow\n"
                "How requests or data flow through the system. Trace the path using ACTUAL "
                "function and class names from the code. Use the IMPORT GRAPH to determine "
                "which modules call which. Use numbered steps. "
                "Each step must reference a real file, class, or function.\n\n"
                "## Conventions\n"
                "Based on patterns you observe in the provided code:\n"
                "- Naming patterns (files, functions, classes) — cite actual examples\n"
                "- Error handling approach — cite what you see\n"
                "- Test organization and patterns\n"
                "- Configuration approach\n\n"
                "## Integration Points\n"
                "Use the IMPORT GRAPH to describe how modules connect at the DIRECTORY level. "
                "Group imports by source → target directory. Do NOT list every individual "
                "import statement — summarize by module/directory relationship. Example:\n"
                "- `app/Http/Controllers/` → `app/Models/` — controllers import model classes\n"
                "- `app/Services/` → `app/Repositories/` — services use repository interfaces\n\n"
                "Only list cross-module connections (different directories). Skip framework/stdlib "
                "imports — only list project-internal connections.\n\n"
                "DO NOT invent integration points that are not visible in the IMPORT GRAPH.\n\n"
                "## API Surface\n"
                "List ALL REST and WebSocket endpoints from the API ENDPOINTS data. "
                "For each endpoint show: HTTP method, URL path, and handler function name. "
                "Group endpoints by resource (sessions, traceability, chat, etc.).\n"
                "Also list public methods of key client/service classes from the CLASS AND "
                "FUNCTION INDEX — especially classes that serve as API facades or SDK clients "
                "(e.g. BackendClient, LLMClient), so consumers know the actual callable surface. "
                "Do NOT invent endpoints or methods that are not in the provided data.\n\n"
                "CRITICAL RULES:\n"
                "- ONLY reference class names, function names, and file paths that appear in the "
                "provided data. If a name is not in the file tree or "
                "class/function index, do not mention it.\n"
                '- If you cannot determine something, say "Not visible in provided code samples."\n'
                '- Do NOT use generic descriptions like "manages various tools" — state which '
                "specific classes/functions do what.\n"
                "- Keep the total document under 6000 words."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="context.expansion_system",
            category="Context Generation",
            name="Context Expansion",
            description=(
                "System prompt for updating project context with additional source files."
            ),
            default_text=(
                "Use your architecture knowledge to update the existing project context "
                "document with findings from additional source files "
                "not yet covered in the document.\n\n"
                "You are given:\n"
                "1. EXISTING PROJECT CONTEXT — the document produced so far\n"
                "2. ADDITIONAL FILE CONTENTS — more source files from the same codebase\n\n"
                "Your task: write the COMPLETE updated document, merging what you learn from the "
                "additional files into the relevant sections already present in the document.\n\n"
                "Rules:\n"
                "- Reproduce ALL sections from the existing document — do not omit any content\n"
                "- Merge new class names, functions, endpoints, and relationships into the correct "
                "existing section — do not place them at the end of the document\n"
                "- Do NOT contradict or duplicate existing content — only update it in place\n"
                "- If new files reveal a module not yet described, "
                "insert it into the Module Map section\n"
                "- Use EXACT names from the provided files — never invent names\n"
                "- Keep the same Markdown structure "
                "(# Project Context, ## Architecture Overview, etc.)\n"
                "- Keep the total document under 6000 words\n\n"
                "CRITICAL — no new top-level headings:\n"
                '- NEVER create sections named "Additional Info", "New Classes and Functions", '
                '"Additional Files", "Updated Module Map", or any other new top-level heading.\n'
                "- New findings belong INSIDE the existing named sections, not after them.\n"
                "  New modules → insert into ## Module Map.  New classes/functions → insert into\n"
                "  ## Key Abstractions under the correct file heading.  New endpoints →\n"
                "  ## API Endpoints.  New relationships → insert into ## Integration Points.\n"
                "- The output must have the same top-level ## headings as the input, no more.\n\n"
                "CRITICAL — accuracy:\n"
                "- Only reference class names, function names, and file paths visible in the data "
                "you have been given.  Do not invent or generalize."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="context.additive_expansion",
            category="Context Generation",
            name="Additive Expansion",
            description=(
                "System prompt for additive-only context expansion. Adds new entries only."
            ),
            default_text=(
                "This is an additive expansion round. You are given:\n"
                "1. EXISTING DOCUMENT — the project context document produced so far\n"
                "2. SOURCE FILES — additional source files not yet covered in the document\n\n"
                "Your task: update the existing document by placing new data from the source "
                "files under the proper headings. Return the complete updated document.\n\n"
                "Rules:\n"
                "- Do NOT remove or rephrase existing content — only add new entries.\n"
                "- Place new findings under the correct existing ## headings.\n"
                "- Use EXACT class names, function names, and file paths from the source files.\n"
                "- Do not invent or generalize names not visible in the provided data.\n"
                "- Keep the same Markdown structure and heading order.\n"
                "- Keep the total document under 6000 words."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="context.parallel_expansion",
            category="Context Generation",
            name="Parallel Expansion",
            description=(
                "System prompt for parallel context expansion."
                " Extracts new entries by section heading."
            ),
            default_text=(
                "Analyze these source files and extract NEW information to add to an existing "
                "project context document.\n\n"
                "You are given:\n"
                "1. SECTION HEADINGS — the existing ## headings in the document\n"
                "2. SOURCE FILES — source files not yet covered in the document\n\n"
                "Your task: identify new classes, functions, endpoints, data flows, conventions, "
                "and relationships from the source files and output ONLY the new entries, "
                "organized under the correct existing ## headings.\n\n"
                "Rules:\n"
                "- Output ONLY new entries — do not repeat or summarize existing content.\n"
                "- Each entry must go under one of the existing ## headings listed above.\n"
                "- Use the heading text EXACTLY as given "
                '(e.g., "## Module Map", "## Key Abstractions").\n'
                "- Skip any heading for which the source files add nothing new.\n"
                "- Use EXACT class names, function names, and file paths from the source files.\n"
                "- Do not invent or generalize names not visible in the provided data.\n"
                "- Keep entries concise: one line per class/function, short paragraph per module.\n"
                "- If a file reveals a new module, place it under ## Module Map.\n"
                "- If a file reveals new classes/functions, place them under ## Key Abstractions.\n"
                "- If a file reveals new API endpoints, place them under ## API Surface.\n"
                "- If a file reveals new integration points, place under ## Integration Points."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="context.style_guide_system",
            category="Context Generation",
            name="Style Guide Generation",
            description="System prompt for generating a CSS/design style guide from project files.",
            template_vars=["css_framework", "project_tree"],
            warning=(
                "Contains 6 required ## section headings"
                " and detailed content rules. Edit carefully."
            ),
            default_text=(
                "Generate a style guide document for a web project.\n\n"
                "DETECTED CSS FRAMEWORK: {css_framework}\n\n"
                "The style guide must capture the ACTUAL visual design patterns from the "
                "project's own CSS, templates, and component files provided below. Extract "
                "real values — do not invent or assume.\n\n"
                "PROJECT FILE TREE:\n"
                "{project_tree}\n\n"
                "STRUCTURE RULES:\n"
                "- Do NOT generate a top-level heading (# Style Guide) — added automatically.\n"
                "- Start your output directly with the first ## section heading.\n"
                "- Each ## heading below MUST appear EXACTLY ONCE.\n"
                "- Every code example MUST have matching opening and closing ``` pairs.\n"
                "- Maximum 3000 words total.\n"
                "- Only describe patterns visible in the provided files. If a section has no "
                'applicable data, write "Not detected in project files."\n\n'
                "REQUIRED SECTIONS (use exactly these ## headings):\n\n"
                "## CSS Framework & Build Setup\n"
                "Identify the CSS framework (Tailwind, Bootstrap, custom, etc.) and how styles "
                "are compiled/loaded. Note the build tool (Vite, Webpack/Mix, etc.) and the "
                "main CSS entry point file.\n\n"
                "If using Tailwind CSS, this section is CRITICAL — extract the FULL custom "
                "theme configuration from tailwind.config.js/ts. Show the complete "
                "`theme.extend` object including all custom colors, fonts, spacing, "
                "breakpoints, and any plugins. This is the single most important piece of "
                "information for maintaining design consistency.\n\n"
                "## Color Palette\n"
                "Document ONLY the project's custom/configured colors — not default framework "
                "colors.\n\n"
                "CRITICAL RULES:\n"
                "- If the project uses Tailwind WITHOUT custom theme.extend.colors, write: "
                '"Uses default Tailwind color palette." Then list ONLY the 3-5 dominant color '
                'families actually used in templates (e.g. "Primary actions: blue-500/600, '
                'Danger: red-500/600, Neutral: gray-50 through gray-900") — do NOT list every '
                "shade with hex values.\n"
                "- If the project HAS custom theme.extend.colors, show that config object and "
                "list each custom color ONCE.\n"
                "- If using CSS custom properties or SCSS variables, list each ONCE.\n"
                "- NEVER list the same color under multiple purpose headings.\n"
                "- NEVER list default Tailwind hex values — they are already known.\n"
                "- Keep this section SHORT — a wall of color values is counterproductive.\n\n"
                "## Typography\n"
                "Extract font families, sizes, weights, and line heights. Look for:\n"
                "- Font imports (@import, link tags, @font-face)\n"
                "- CSS custom properties for fonts\n"
                "- Tailwind font configuration in theme.extend.fontFamily\n"
                "- Body/heading font declarations\n"
                "List the font stack and where each is used (headings, body, code, etc.)\n\n"
                "## Spacing & Layout\n"
                "Describe the layout system:\n"
                "- Container max-widths and padding\n"
                "- Grid system (CSS Grid, Flexbox patterns, Tailwind grid, Bootstrap grid)\n"
                "- Spacing scale (margin/padding values or Tailwind spacing)\n"
                "- Breakpoints for responsive design\n\n"
                "## Component Patterns\n"
                "For each reusable UI pattern found in templates/components, show the ACTUAL "
                "markup from the project files. Copy the real HTML/Blade/Vue structure with "
                "its CSS classes — do not paraphrase or summarize into prose.\n\n"
                "For each component include:\n"
                "- The source file path\n"
                "- The actual markup snippet (HTML structure with classes)\n"
                "- Key Tailwind/CSS classes used and their visual effect\n\n"
                "Components to document (if found):\n"
                "- Navigation/header\n"
                "- Hero/banner sections\n"
                "- Card layouts\n"
                "- Footer\n"
                "- Buttons (show each variant)\n"
                "- Form elements\n\n"
                "## Page Layout Structure\n"
                "Describe how pages are structured:\n"
                "- The main layout/master template and its sections/slots/yields\n"
                "- How child pages extend the layout\n"
                "- The HTML document structure (head contents, body wrapper classes)\n"
                "- Asset loading patterns (where CSS/JS are included)\n"
                "Show the actual @yield/@section slots from the master layout file.\n\n"
                "CONTENT RULES:\n"
                "- Extract REAL values from the provided files — colors, font names, class names\n"
                "- Reference ACTUAL file paths from the project tree\n"
                "- Show code snippets from the provided files, not invented examples\n"
                "- If using Tailwind, the tailwind.config theme.extend is the PRIMARY source of "
                "truth — prioritize it above all other sources for colors, fonts, and spacing\n"
                "- If using component classes, show the actual class naming convention"
            ),
        )
    )

    # ── TDD & Vision ──────────────────────────────────────────────────

    reg.register(
        PromptEntry(
            key="tdd.dispute_evaluation",
            category="TDD & Vision",
            name="TDD Dispute Evaluation",
            description=("System prompt for expert model evaluating test disputes in TDD mode."),
            default_text=(
                "Evaluate a test dispute from the implementation model.  The implementor "
                "claims a test is flawed and cannot be satisfied by a correct implementation.\n\n"
                "RULES:\n"
                "1. Read the test file to see the current test code.\n"
                "2. Evaluate the implementor's reason carefully and objectively.\n"
                "3. If the dispute is VALID (the test has a genuine flaw — wrong assertion, "
                "tests an implementation detail, impossible precondition, wrong import path, "
                "tests out-of-scope behavior):\n"
                "   - Fix the test using edit_file.  Preserve the test's documentation "
                "(docstrings, comments) and update them to reflect the fix.\n"
                '   - Call task_complete with a summary starting with "ACCEPTED: " followed '
                "by what you changed and why.\n"
                "4. If the dispute is INVALID (the test is correct and the implementor "
                "needs to find a different approach):\n"
                '   - Call task_complete with a summary starting with "REJECTED: " followed '
                "by: why the test is correct, what behaviour the test is validating, and "
                "what implementation approach would satisfy it.\n"
                "5. Do NOT add new tests or remove existing ones — only fix the disputed "
                "test function if the dispute is valid."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="vision.system",
            category="TDD & Vision",
            name="Vision Model System Prompt",
            description=(
                "System prompt for the vision model that describes images for text-only models."
            ),
            default_text=(
                "Describe this image in detail for a software developer who cannot see it.\n\n"
                "Focus on:\n"
                "- UI layout, component structure, visual hierarchy, and styling\n"
                "- All visible text content, labels, headings, and button text\n"
                "- Error messages, warnings, notifications — capture the exact text\n"
                "- Code snippets or terminal output — transcribe the visible text verbatim\n"
                "- Colors, icons, spacing, and visual state (disabled, selected, hover, etc.)\n"
                "- If this is a UI mockup or wireframe, describe the layout and components\n"
                "- If this is an architecture diagram, describe nodes, connections, and labels\n\n"
                "Be thorough but concise. Prioritise actionable information."
            ),
        )
    )

    # ── Advanced / Nudges ─────────────────────────────────────────────

    reg.register(
        PromptEntry(
            key="nudge.text_only",
            category="Advanced",
            name="Text-Only Nudge",
            description=(
                "Injected when the model responds with text"
                " but no tool call. Reminds it to use a tool."
            ),
            default_text="Call task_complete if done, otherwise call your next tool.",
        )
    )

    reg.register(
        PromptEntry(
            key="nudge.truncation",
            category="Advanced",
            name="Truncation Nudge",
            description=(
                "Injected when a response is truncated by max_tokens. Asks for tool call only."
            ),
            default_text="Response truncated. Output ONLY the tool call, nothing else.",
        )
    )

    reg.register(
        PromptEntry(
            key="nudge.reasoning_budget_exceeded",
            category="Advanced",
            name="Reasoning Budget Interrupt",
            description=(
                "Injected as a user-role message when the Ollama streaming"
                " helper aborts because thinking tokens exceeded the configured"
                " reasoning_effort limit or the max_thinking_tokens safety"
                " rail.  After 2 consecutive interrupts the loop exits."
            ),
            default_text=(
                "Your reasoning exceeded the configured budget. Stop thinking "
                "and produce your final answer now based on what you have "
                "already worked through."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="nudge.loop_detected",
            category="Advanced",
            name="Loop Detection Message",
            description="Injected when the same tool is called N times with identical arguments.",
            template_vars=["tool_name", "count"],
            default_text=(
                "Loop detected: {tool_name} called {count} times with same args. "
                "Use a different approach."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="nudge.claim_verification",
            category="Advanced",
            name="Claim Verification Nudge",
            description=(
                "Injected when tests fail repeatedly and the model's"
                " response suggests stale API knowledge."
            ),
            default_text=(
                "Tests have failed multiple times and your response suggests the issue "
                "may involve an outdated or deprecated API, renamed function, or changed "
                "library interface. Your training data may not reflect the current state. "
                "Call search_internet to look up the current documentation for the "
                "relevant dependency before attempting another fix."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="nudge.confidence_verification",
            category="Advanced",
            name="Confidence Verification Nudge",
            description=(
                "Injected when the model tags claims as [UNVERIFIED] but does not search to verify."
            ),
            default_text=(
                "You tagged claims as [UNVERIFIED]. Call search_internet to verify "
                "these claims before proceeding. Cite the documentation URL and "
                "confirmed details in your response."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="nudge.fix_mode_switch",
            category="Advanced",
            name="Fix Mode Switch Nudge",
            description="Injected when switching from investigation to implementation.",
            default_text=(
                "Investigation complete. You now have edit_file and create_file. "
                "Review your scratchpad diagnosis and implement the fix. "
                "Call one tool now to start."
            ),
        )
    )

    # ── UI Verification (vision-model multi-pass analysis) ───────────

    reg.register(
        PromptEntry(
            key="ui_verification.inventory_system",
            category="UI Verification",
            name="UI Inventory Pass — System",
            description=(
                "Schema-constrained pass that enumerates visual regions and"
                " components in a screenshot. Temperature pinned to 0."
            ),
            default_text=(
                "Use your knowledge of visual hierarchy, information architecture,"
                " and UI component taxonomy to inventory what is visible in the"
                " screenshot.\n\n"
                "For spatial grounding, treat the screenshot as a 3x3 grid with"
                " cells named: top-left, top-center, top-right, middle-left,"
                " center, middle-right, bottom-left, bottom-center, bottom-right.\n\n"
                "STEP 1 — Identify distinct visual REGIONS (header, main content,"
                " sidebar, footer, toolbar, modal, etc.). Each region occupies"
                " one or more grid cells.\n\n"
                "STEP 2 — Enumerate every COMPONENT you can see (button, input,"
                " link, card, nav, text block, image, icon, checkbox, radio,"
                " select, toggle, etc.). For each, record type, location (grid"
                " cell or region name), label_text if visible, brief styling_notes,"
                " and your confidence (high / medium / low).\n\n"
                "VISIBLE STATE ONLY. Do not infer hover, focus, disabled, or error"
                " states unless there is a clear visual indicator in the"
                " screenshot. When unsure, use confidence=low.\n\n"
                "Return JSON matching the provided schema exactly. Use empty"
                " strings or empty arrays for fields you cannot determine."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="ui_verification.inventory_user",
            category="UI Verification",
            name="UI Inventory Pass — User",
            description="User message for the inventory pass. Brief by design.",
            default_text="Inventory the visual regions and UI components in this screenshot.",
        )
    )

    reg.register(
        PromptEntry(
            key="ui_verification.text_system",
            category="UI Verification",
            name="UI Text Transcription Pass — System",
            description=(
                "Dedicated OCR-style pass. Text transcription from a general"
                " description is the #1 hallucination source for vision models,"
                " so this pass is constrained to verbatim output only."
            ),
            default_text=(
                "Use your knowledge of UI typography to transcribe every visible"
                " text string in the screenshot, VERBATIM.\n\n"
                "Do NOT paraphrase. Do NOT invent text. Do NOT normalise case,"
                " punctuation, or whitespace. Transcribe exactly what you see.\n\n"
                "For any text that is too small, blurry, occluded, or otherwise"
                " unreadable, set verbatim to the literal string '[unreadable]'.\n\n"
                "For each text line, record which visual region it belongs to"
                " (header, main, sidebar, footer, modal, etc.) and your confidence"
                " in the transcription (high / medium / low).\n\n"
                "Return JSON matching the provided schema exactly."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="ui_verification.text_user",
            category="UI Verification",
            name="UI Text Transcription Pass — User",
            description="User message for the text transcription pass.",
            default_text="Transcribe every visible text string in this screenshot.",
        )
    )

    reg.register(
        PromptEntry(
            key="ui_verification.answer_system",
            category="UI Verification",
            name="UI Focused Answer Pass — System",
            description=(
                "Final pass: synthesise the inventory + text + sampled colors"
                " into a focused answer to the user's question."
            ),
            default_text=(
                "Use your knowledge of UI/UX design, visual hierarchy, and"
                " accessibility to answer the user's question about this UI.\n\n"
                "You have three sources of grounded information:\n"
                " 1. A structured inventory of regions and components.\n"
                " 2. A verbatim transcription of visible text.\n"
                " 3. A pixel-sampled color palette (reliable; NOT guessed).\n\n"
                "Ground your answer in these sources plus what you can verify in"
                " the screenshot itself. Flag anything you are inferring rather"
                " than observing directly. If the question cannot be answered"
                " from the visible screenshot, say so plainly and explain what"
                " would be needed to answer it (a different view, a hover state,"
                " interaction, etc.).\n\n"
                "Be concise: a direct answer followed by the 2-5 observations"
                " that support it."
            ),
        )
    )

    reg.register(
        PromptEntry(
            key="ui_verification.answer_user",
            category="UI Verification",
            name="UI Focused Answer Pass — User",
            description=(
                "User message for the focused answer pass. Template variables:"
                " question, inventory_json, text_json, colors."
            ),
            default_text=(
                "Question from the caller:\n"
                "{question}\n\n"
                "── Inventory (from a prior structured pass) ──\n"
                "{inventory_json}\n\n"
                "── Visible text (from a prior verbatim pass) ──\n"
                "{text_json}\n\n"
                "── Sampled color palette (pixel-accurate) ──\n"
                "{colors}\n\n"
                "Answer the question using the above plus the screenshot itself."
            ),
            template_vars=["question", "inventory_json", "text_json", "colors"],
        )
    )


# Populate the singleton with defaults now that the function is defined.
_register_defaults(registry)

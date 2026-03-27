"""Framework guide generation — detect frameworks, search for best practices,
and produce a tailored ``.lean_ai/framework_guide.md``.

Runs as a post-generation step or standalone via endpoint.  Gracefully
returns ``""`` on any failure so it never blocks context generation.

No regex — all text processing uses simple string operations.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from lean_ai.context.framework_detection import (
    build_gap_fill_queries_llm,
    build_guide_search_queries,
    build_guide_search_queries_llm,
    canonicalize_name,
    get_compact_tree,
    get_primary_frameworks,
)
from lean_ai.context.framework_search import (
    extract_search_results,
    select_one_per_query,
)
from lean_ai.context.framework_validation import (
    check_invalid_paths,
    extract_file_paths,
    get_project_paths,
    remove_blocks,
    surgical_llm_fix,
)

if TYPE_CHECKING:
    from lean_ai.llm.client import LLMClient

logger = logging.getLogger(__name__)

# Required ## headings that semantic dedup must never remove.
_REQUIRED_HEADINGS: set[str] = {
    "## Framework Architecture",
    "## Component Relationships",
    "## Common CLI Commands",
    "## File Organization Conventions",
    "## Adding a New Feature",
    "## Common Patterns and Pitfalls",
}

# Budget constants for multi-pass (deep) guide generation.
_RAW_FETCH_CAP = 50_000       # Max raw chars fetched per page before extraction
_EXTRACTED_PAGE_CAP = 4_000   # Max chars per page after LLM extraction
_SECTION_PAGE_BUDGET = 20_000  # Total extracted page chars budget per section
_EXTRACTION_MAX_TOKENS = 1024  # Output token budget for extraction LLM calls

# Per-component budgets for section user message assembly.
# Each component is truncated independently so later sections still
# get full search evidence even when prior sections are large.
_SEARCH_BUDGET = 10_000        # Web search snippets
_PAGE_BUDGET = 10_000          # Fetched page content
_KEY_FILES_BUDGET = 8_000      # Project file contents (see _collect_key_file_contents)
_KEY_FILE_MAX_SINGLE = 2_000   # Max chars per individual project file
_TREE_BUDGET = 3_000           # Project file tree
_PRIOR_SECTIONS_BUDGET = 6_000  # Earlier sections for consistency
_TOTAL_INPUT_CAP = 40_000      # Safety net for entire user message

# Files to skip when collecting key project file contents.
_SKIP_KEY_FILE_NAMES = {
    "composer.lock", "package-lock.json", "yarn.lock",
    "poetry.lock", "Gemfile.lock", "go.sum", "Cargo.lock",
    "phpunit.xml", "phpunit.xml.dist", "webpack.mix.js",
}
_SKIP_KEY_FILE_EXTS = {".md", ".txt", ".rst", ".lock"}


def _collect_key_file_contents(
    repo_root: str,
    max_total: int = _KEY_FILES_BUDGET,
    max_per_file: int = _KEY_FILE_MAX_SINGLE,
) -> str:
    """Read framework-structural files for injection into guide prompts.

    Uses the language registry's ``key_files`` and ``entry_points`` to
    find files that reveal framework architecture (routes, config,
    bootstrap, entry points).  Returns formatted file contents within
    *max_total* chars.
    """
    from lean_ai.context.constants import _get_entry_points, _get_key_files

    root = Path(repo_root)

    # Merge key_files (relative paths) + entry_points (filenames).
    candidates: list[str] = []
    seen: set[str] = set()

    for filepath in _get_key_files():
        name = Path(filepath).name
        if name in _SKIP_KEY_FILE_NAMES:
            continue
        if Path(filepath).suffix in _SKIP_KEY_FILE_EXTS:
            continue
        full = root / filepath
        if full.is_file() and filepath not in seen:
            candidates.append(filepath)
            seen.add(filepath)

    # Entry points may be bare filenames — search common locations.
    for ep_name in _get_entry_points():
        if ep_name in seen:
            continue
        # Try as relative path first, then common web roots.
        for prefix in ["", "public/"]:
            candidate = prefix + ep_name if prefix else ep_name
            if candidate in seen:
                break
            full = root / candidate
            if full.is_file():
                candidates.append(candidate)
                seen.add(candidate)
                break

    # Read and format.
    parts: list[str] = []
    total = 0
    for filepath in candidates:
        if total >= max_total:
            break
        full = root / filepath
        try:
            content = full.read_text(encoding="utf-8")[:max_per_file]
        except Exception:
            continue
        # Guess language tag from extension.
        ext = Path(filepath).suffix.lstrip(".")
        lang_map = {
            "php": "php", "py": "python", "js": "javascript",
            "ts": "typescript", "rb": "ruby", "go": "go",
            "rs": "rust", "java": "java", "cs": "csharp",
            "json": "json", "yaml": "yaml", "yml": "yaml",
        }
        lang = lang_map.get(ext, "")
        block = f"--- {filepath} ---\n```{lang}\n{content}\n```"
        if total + len(block) > max_total:
            break
        parts.append(block)
        total += len(block)

    return "\n\n".join(parts)


# Per-section specifications for multi-pass guide generation.
# Each spec maps a required heading to its search topics, extraction focus,
# and the section-specific generation instructions (extracted from the
# original monolithic system prompt).
_SECTION_SPECS: list[dict] = [
    {
        "heading": "## Framework Architecture",
        "weight": 5,
        "search_topics": [
            "Architecture pattern and request lifecycle",
            "Middleware and request pipeline",
        ],
        "max_queries": 4,
        "extraction_focus": (
            "Extract architecture patterns, request lifecycle steps, "
            "middleware pipeline details, entry points, and component "
            "initialization order. Include code snippets showing "
            "request flow, boot sequence, and service providers."
        ),
        "section_prompt": (
            "## Framework Architecture\n"
            "Explain the framework's architectural pattern (MVC, MVVM, "
            "component-based, etc.) and the request/render lifecycle for "
            "THIS version. Describe how a request flows from entry point "
            "to response as a numbered sequence — which files and classes "
            "are involved at each stage. Only reference files and classes "
            "that exist in the detected version."
        ),
    },
    {
        "heading": "## Component Relationships",
        "weight": 4,
        "search_topics": [
            "Route to controller binding",
            "Model to migration relationship",
            "Dependency injection and service registration",
        ],
        "max_queries": 3,
        "extraction_focus": (
            "Extract component connection points: route-to-handler "
            "binding, model-to-migration links, service registration, "
            "validation classes, template rendering. Include code "
            "snippets showing both sides of each relationship."
        ),
        "section_prompt": (
            "## Component Relationships\n"
            "Identify the major component relationships that exist in the "
            "detected framework. For EACH relationship show a SHORT code "
            "snippet demonstrating the exact connection point between the "
            "two components.\n\n"
            "Common relationships to cover (skip any that do not apply to "
            "this framework):\n"
            "- Data schema to data model (migrations/schema to ORM "
            "model/entity)\n"
            "- Route definition to handler (URL routing to controller/"
            "view function/handler)\n"
            "- Handler to template/response (controller to view/template/"
            "serializer)\n"
            "- Middleware or interceptors in the request pipeline\n"
            "- Dependency injection or service registration (if the "
            "framework uses it)\n"
            "- Request validation (form objects, request classes, schema "
            "validators)\n\n"
            "For each relationship:\n"
            "- Show both sides of the connection as code snippets\n"
            "- Explain the naming convention or configuration that links "
            "them\n"
            "- Note which file(s) each side lives in\n\n"
            "Do NOT draw ASCII dependency diagrams. Describe the request "
            "flow as a numbered prose sequence."
        ),
    },
    {
        "heading": "## Common CLI Commands",
        "weight": 2,
        "search_topics": [
            "CLI tools and code generation commands",
            "Database migration commands",
        ],
        "max_queries": 3,
        "extraction_focus": (
            "Extract exact CLI command syntax with all flags and "
            "arguments. Include scaffolding commands, database "
            "migration commands, development server commands, "
            "testing commands, and cache/config management."
        ),
        "section_prompt": (
            "## Common CLI Commands\n"
            "List the essential CLI commands for this EXACT version. Group "
            "by purpose:\n"
            "- Project setup and scaffolding\n"
            "- Database (migrations, seeding, schema inspection)\n"
            "- Development server and tooling\n"
            "- Testing and debugging\n"
            "- Cache, config, and maintenance\n\n"
            "For each command show the EXACT syntax including all flags.\n\n"
            "CRITICAL: Only include commands and flags you are CERTAIN "
            "exist in the detected version:\n"
            "- Do not mix up flags between different subcommands\n"
            "- Do not invent commands or flags that do not exist\n"
            "- If the fetched page content includes CLI documentation, "
            "use those exact command signatures"
        ),
    },
    {
        "heading": "## File Organization Conventions",
        "weight": 2,
        "search_topics": [
            "Directory structure and file organization",
            "Project structure conventions",
        ],
        "max_queries": 2,
        "extraction_focus": (
            "Extract directory structure listings, file naming "
            "conventions, and which framework concept maps to which "
            "directory. Include directory trees if present."
        ),
        "section_prompt": (
            "## File Organization Conventions\n"
            "Describe the standard directory structure for THIS version "
            "and where each type of component lives. Map directories to "
            "framework concepts. Only list directories that exist in this "
            "version — do not carry over directory structures from older "
            "versions."
        ),
    },
    {
        "heading": "## Adding a New Feature",
        "weight": 4,
        "search_topics": [
            "Step by step adding new feature CRUD",
            "Scaffolding and code generation workflow",
        ],
        "max_queries": 2,
        "extraction_focus": (
            "Extract step-by-step workflows for creating new features, "
            "including which commands to run and which files to create "
            "or modify. Include code snippets for each step."
        ),
        "section_prompt": (
            "## Adding a New Feature\n"
            "Provide a step-by-step workflow for adding a typical new "
            "feature (e.g., a new CRUD resource). IMPORTANT:\n"
            "- Do NOT create the same artifact twice (e.g., if a command "
            "creates both a model and migration, do not also run a "
            "separate migration command)\n"
            "- Show the SINGLE optimal command that creates the most "
            "artifacts at once, then list what it generated\n"
            "- For each step show the exact file(s) created and what to "
            "add to them\n"
            "- Show how each new file connects back to previously created "
            "components"
        ),
    },
    {
        "heading": "## Common Patterns and Pitfalls",
        "weight": 3,
        "search_topics": [
            "Common mistakes and version-specific gotchas",
            "Upgrade guide migration from previous version",
            "Breaking changes and new features",
        ],
        "max_queries": 3,
        "extraction_focus": (
            "Extract version-specific pitfalls, deprecated features, "
            "breaking changes, migration steps, and new recommended "
            "patterns. Focus on what changed from the previous major "
            "version."
        ),
        "section_prompt": (
            "## Common Patterns and Pitfalls\n"
            "List 5-10 patterns that are easy to get wrong in the "
            "DETECTED VERSION. Each pattern MUST be specific to this "
            "version — generic advice that applies to all versions is "
            "not useful. Focus on:\n"
            "- What changed in this version vs. the previous major "
            "version\n"
            "- Deprecated features that developers might still try to "
            "use\n"
            "- New recommended patterns that replace old ones\n"
            "- Naming conventions the framework enforces implicitly"
        ),
    },
]


# ---------------------------------------------------------------------------
# Post-generation validation — three-phase path checking
# ---------------------------------------------------------------------------

async def _validate_guide(
    guide: str,
    repo_root: str,
    llm_client: LLMClient,
    system_prompt: str,
) -> str:
    """Validate file paths against the project tree.

    Three-phase approach:
    1. Detect invalid paths.
    2. Surgical LLM correction on affected blocks only.
    3. Mechanical strip of any remaining invalid paths.
    """
    # Phase 1: Detect
    project_paths = get_project_paths(repo_root)
    project_top_dirs = {p.split("/")[0] for p in project_paths}

    invalid = check_invalid_paths(guide, project_paths, project_top_dirs)
    if not invalid:
        referenced = extract_file_paths(guide)
        logger.info(
            "Framework guide: all %d file references valid",
            len(referenced),
        )
        return guide

    logger.info(
        "Framework guide: %d file references invalid: %s",
        len(invalid),
        ", ".join(sorted(invalid)),
    )

    # Phase 2: Surgical LLM correction
    corrected = await surgical_llm_fix(
        guide, invalid, llm_client, system_prompt,
    )

    still_invalid = check_invalid_paths(
        corrected, project_paths, project_top_dirs,
    )
    if not still_invalid:
        logger.info("Framework guide: surgical LLM pass fixed all paths")
        return corrected

    logger.info(
        "Framework guide: %d invalid paths remain after LLM pass, "
        "applying mechanical removal: %s",
        len(still_invalid),
        ", ".join(sorted(still_invalid)),
    )

    # Phase 3: Mechanical strip
    stripped = remove_blocks(corrected, still_invalid)

    final_invalid = check_invalid_paths(
        stripped, project_paths, project_top_dirs,
    )
    if final_invalid:
        logger.warning(
            "Framework guide: %d invalid paths remain after "
            "mechanical removal: %s",
            len(final_invalid),
            ", ".join(sorted(final_invalid)),
        )

    return stripped


# ---------------------------------------------------------------------------
# Post-generation deduplication — remove repeated ## sections
# ---------------------------------------------------------------------------

def _deduplicate_sections_mechanical(guide: str) -> str:
    """Remove duplicate ``##`` heading sections via exact heading match.

    Keeps only the first occurrence of each ``## Heading`` text and
    discards subsequent exact duplicates.  Used as a fast fallback when
    the LLM-based deduplication is unavailable.

    Returns the original text on any error.
    """
    try:
        lines = guide.split("\n")

        # Build list of (heading_text, start_line, end_line) tuples
        sections: list[tuple[str, int, int]] = []
        for i, line in enumerate(lines):
            if line.lstrip().startswith("## "):
                sections.append((line.strip(), i, -1))
                if len(sections) > 1:
                    sections[-2] = (
                        sections[-2][0],
                        sections[-2][1],
                        i,
                    )
        if sections:
            sections[-1] = (
                sections[-1][0],
                sections[-1][1],
                len(lines),
            )

        if not sections:
            return guide

        # Preamble: lines before the first heading
        preamble_end = sections[0][1]

        # Keep only the first occurrence of each heading
        seen: set[str] = set()
        kept_ranges: list[tuple[int, int]] = []
        removed = 0
        for heading, start, end in sections:
            if heading not in seen:
                seen.add(heading)
                kept_ranges.append((start, end))
            else:
                removed += 1

        if removed == 0:
            return guide

        # Reassemble
        result_lines = list(lines[:preamble_end])
        for start, end in kept_ranges:
            result_lines.extend(lines[start:end])

        # Collapse triple+ blank lines to double
        cleaned: list[str] = []
        blank_count = 0
        for line in result_lines:
            if not line.strip():
                blank_count += 1
                if blank_count <= 2:
                    cleaned.append(line)
            else:
                blank_count = 0
                cleaned.append(line)

        logger.info(
            "Framework guide: removed %d duplicate section(s)", removed,
        )
        return "\n".join(cleaned)
    except Exception as exc:
        logger.warning(
            "Framework guide: deduplication failed (non-blocking): %s",
            exc,
        )
        return guide


def _deduplicate_sections(guide: str) -> str:
    """Remove duplicate or overlapping ``##`` sections.

    Two-pass approach:
    1. Mechanical reorganization (merge same headings, drop exact-match
       lines).
    2. Mechanical heading-normalization dedup (catches parenthetical
       qualifier variants like ``## Heading (Updated)``).
    """
    from lean_ai.context.dedup import reorganize_sections

    result = reorganize_sections(guide, log_prefix="Framework guide")

    mechanical = _deduplicate_sections_mechanical(result)
    if mechanical != result:
        return mechanical

    return result


# ---------------------------------------------------------------------------
# Post-generation code block repair
# ---------------------------------------------------------------------------

# Prefixes that indicate code lines when found outside a fenced block.
# Grouped by language for automatic language identification.
_PHP_PREFIXES = (
    "<?php", "namespace ", "use App\\", "use Illuminate\\",
    "class ", "public ", "private ", "protected ", "function ",
    "Route::", "return view(", "return response(", "$",
)
_BASH_PREFIXES = (
    "$ ", "php ", "composer ", "npm ", "artisan ",
    "python ", "pip ", "rails ", "bundle ", "cargo ", "go ",
)
_PYTHON_PREFIXES = (
    "import ", "from ", "def ", "class ", "async def ", "async ",
    "@", "if __name__", "print(",
)
_JS_TS_PREFIXES = (
    "import ", "export ", "const ", "let ", "var ",
    "function ", "async function ", "module.exports",
    "require(", "app.", "router.",
)
_RUBY_PREFIXES = (
    "require ", "module ", "Rails.", "gem ",
)
_GO_PREFIXES = (
    "package ", "func ", "type ", "defer ",
)
_JAVA_PREFIXES = (
    "package ", "public class ", "private ", "protected ",
    "@Override", "@Autowired", "@Bean",
)


def _repair_code_blocks(text: str) -> str:
    """Wrap unfenced code lines in appropriate fenced code blocks.

    Detects lines that look like code (based on leading tokens) but
    are not inside a fenced code block, and wraps them.  This is a
    mechanical safety net — the prompt should handle most cases.

    No regex — uses simple string prefix checks.
    """
    lines = text.split("\n")
    result: list[str] = []
    in_fence = False
    code_run: list[str] = []
    code_lang = ""

    def _flush_code_run() -> None:
        nonlocal code_run, code_lang
        if code_run:
            result.append(f"```{code_lang}")
            result.extend(code_run)
            result.append("```")
            code_run = []
            code_lang = ""

    for line in lines:
        stripped = line.lstrip()

        # Track fenced code blocks
        if stripped.startswith("```"):
            if in_fence:
                in_fence = False
            else:
                _flush_code_run()
                in_fence = True
            result.append(line)
            continue

        if in_fence:
            result.append(line)
            continue

        # Detect code-like lines outside fences.
        # Check bash first — "$ " (bash prompt) must not be caught
        # by the PHP "$" prefix.  Then PHP, then others.
        detected_lang = ""
        if stripped.startswith(_BASH_PREFIXES):
            detected_lang = "bash"
        elif stripped.startswith(_PHP_PREFIXES):
            detected_lang = "php"
        elif stripped.startswith(_PYTHON_PREFIXES):
            detected_lang = "python"
        elif stripped.startswith(_JS_TS_PREFIXES):
            detected_lang = "typescript"
        elif stripped.startswith(_RUBY_PREFIXES):
            detected_lang = "ruby"
        elif stripped.startswith(_GO_PREFIXES):
            detected_lang = "go"
        elif stripped.startswith(_JAVA_PREFIXES):
            detected_lang = "java"

        if detected_lang and stripped:
            if code_run and code_lang != detected_lang:
                _flush_code_run()
            code_lang = code_lang or detected_lang
            code_run.append(line)
        else:
            _flush_code_run()
            result.append(line)

    _flush_code_run()

    # Close any unclosed code block left open by the LLM.
    if in_fence:
        result.append("```")

    return "\n".join(result)


def _strip_prompt_echoes(
    section_output: str,
    section_spec: dict,
) -> str:
    """Remove echoed prompt instructions from LLM section output.

    Detects and strips lines that substantially overlap with the
    ``section_prompt`` text.  Works on a sentence-level basis to catch
    partial echoes, not just exact matches.
    """
    prompt_text = section_spec.get("section_prompt", "")
    if not prompt_text:
        return section_output

    # Build a set of instruction phrases from the prompt (>30 chars).
    prompt_sentences: set[str] = set()
    for line in prompt_text.split("\n"):
        cleaned = line.strip().lstrip("- ").strip()
        if len(cleaned) > 30:
            prompt_sentences.add(cleaned.lower())

    if not prompt_sentences:
        return section_output

    lines = section_output.split("\n")
    result: list[str] = []
    stripped_count = 0

    for line in lines:
        cleaned = line.strip().lstrip("- ").strip()
        if not cleaned:
            result.append(line)
            continue

        is_echo = False
        cleaned_lower = cleaned.lower()
        for prompt_sent in prompt_sentences:
            if prompt_sent in cleaned_lower:
                is_echo = True
                break
            if len(cleaned_lower) > 30 and cleaned_lower in prompt_sent:
                is_echo = True
                break

        if is_echo:
            stripped_count += 1
        else:
            result.append(line)

    if stripped_count:
        logger.info(
            "Framework guide: stripped %d prompt-echo line(s) from %s",
            stripped_count, section_spec["heading"],
        )

    return "\n".join(result)


def _renumber_steps(text: str) -> str:
    """Renumber sequential step patterns after section removal.

    Handles two patterns:
    1. Markdown numbered lists: ``1. **Step** ...``
    2. Heading-style steps: ``### Step 3: ...``

    Only renumbers within each ``##`` section boundary.
    No regex — uses simple string operations.
    """
    lines = text.split("\n")
    result: list[str] = []
    list_counter = 0
    step_counter = 0

    for line in lines:
        stripped = line.lstrip()

        # Reset counters at ## section boundaries
        if stripped.startswith("## "):
            list_counter = 0
            step_counter = 0
            result.append(line)
            continue

        # Pattern 1: Numbered list items "1. " or "2. **Step**"
        if stripped and stripped[0].isdigit() and ". " in stripped[:5]:
            dot_pos = stripped.index(". ")
            old_num = stripped[:dot_pos]
            if old_num.isdigit():
                list_counter += 1
                indent = line[:len(line) - len(stripped)]
                rest = stripped[dot_pos:]  # ". rest of line"
                result.append(f"{indent}{list_counter}{rest}")
                continue

        # Pattern 2: "### Step N:" headings
        if stripped.startswith("### Step "):
            rest = stripped[9:]  # after "### Step "
            colon_pos = rest.find(":")
            if colon_pos > 0:
                old_num_str = rest[:colon_pos].strip()
                if old_num_str.isdigit():
                    step_counter += 1
                    after_colon = rest[colon_pos:]
                    indent = line[:len(line) - len(stripped)]
                    result.append(
                        f"{indent}### Step {step_counter}{after_colon}",
                    )
                    continue

        # Non-numbered lines reset the list counter
        if not stripped or not (
            stripped[0].isdigit() and ". " in stripped[:5]
        ):
            list_counter = 0

        result.append(line)

    return "\n".join(result)


# ---------------------------------------------------------------------------
# Web search content deduplication
# ---------------------------------------------------------------------------

def _deduplicate_search_parts(
    parts: list[str],
    threshold: float = 0.5,
) -> list[str]:
    """Remove search snippets that overlap significantly with earlier ones.

    Splits each snippet into sentences (strings ending with ``'.'`` that
    are longer than 30 chars) and computes the fraction of sentences
    already seen in previous snippets.  If the overlap exceeds
    *threshold*, the snippet is dropped.
    """
    if len(parts) <= 1:
        return parts

    kept: list[str] = []
    seen_sentences: set[str] = set()

    for part in parts:
        sentences = {
            s.strip().lower()
            for s in part.split(".")
            if len(s.strip()) > 30
        }
        if not sentences:
            kept.append(part)
            continue

        overlap = len(sentences & seen_sentences) / len(sentences)
        if overlap < threshold:
            kept.append(part)
            seen_sentences |= sentences
        else:
            logger.info(
                "Framework guide: dropped search snippet "
                "(%.0f%% overlap with previous)",
                overlap * 100,
            )

    if len(kept) < len(parts):
        logger.info(
            "Framework guide: search dedup kept %d/%d snippets",
            len(kept), len(parts),
        )
    return kept


# ---------------------------------------------------------------------------
# Gap detection — find subsections with no content
# ---------------------------------------------------------------------------

def _find_empty_subsections(guide: str) -> list[tuple[str, str]]:
    """Find subsections that have a heading but no substantive content.

    Scans the guide for ``###`` headings and bold-label lines
    (``**Some Label:**``) that have no meaningful content before the
    next heading.  A subsection is "empty" if it has zero content
    lines, or only a single line that is a sentinel phrase like
    "No information available".

    Returns a list of ``(parent_heading, sub_heading)`` tuples where
    *parent_heading* is the ``##`` section the empty sub lives under,
    and *sub_heading* is the heading text (stripped of markdown
    formatting).
    """
    lines = guide.split("\n")
    gaps: list[tuple[str, str]] = []

    # Scan lines, tracking the current ## parent heading.
    sub_entries: list[tuple[int, str, str]] = []  # (line_idx, parent, sub)
    current_parent = ""
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            current_parent = stripped
        elif stripped.startswith("### "):
            sub_entries.append(
                (i, current_parent, stripped.lstrip("# ").strip())
            )
        elif stripped.startswith("**") and stripped.endswith(":**"):
            sub_entries.append(
                (i, current_parent, stripped.strip("*: ").strip())
            )

    for idx, (line_idx, parent, sub_heading) in enumerate(sub_entries):
        # Determine the end boundary: next sub-heading, next ## heading,
        # or end of document.
        end_idx = len(lines)
        for j in range(line_idx + 1, len(lines)):
            s = lines[j].strip()
            if s.startswith("## ") or s.startswith("### "):
                end_idx = j
                break
            if s.startswith("**") and s.endswith(":**"):
                end_idx = j
                break

        # Collect non-blank content lines between heading and boundary.
        content_lines = [
            lines[k].strip()
            for k in range(line_idx + 1, end_idx)
            if lines[k].strip()
        ]

        # Empty if no content, or only a sentinel phrase.
        if len(content_lines) == 0:
            gaps.append((parent, sub_heading))
        elif (
            len(content_lines) == 1
            and "no information available" in content_lines[0].lower()
        ):
            gaps.append((parent, sub_heading))

    return gaps


# ---------------------------------------------------------------------------
# Gap-filling — LLM generates content for empty subsections
# ---------------------------------------------------------------------------

async def _fill_guide_gaps(
    guide: str,
    gaps: list[tuple[str, str]],
    search_content: str,
    llm_client: LLMClient,
    frameworks: list[tuple[str, str]],
    runtimes: list[tuple[str, str]],
    cutoff: str | None,
    max_tokens: int,
) -> str:
    """Fill empty subsections using new search results.

    Passes the current guide, the new search content, and the list of
    empty subsections to the LLM.  The LLM returns content for ONLY
    the empty subsections, which gets spliced back into the guide.

    Returns the updated guide, or the original on any failure.
    """
    from lean_ai.context.deprecations import _extract_major_minor

    fw_list = ", ".join(
        f"{canonicalize_name(name)} {_extract_major_minor(ver)}"
        if ver else canonicalize_name(name)
        for name, ver in frameworks
    )

    gap_list = "\n".join(
        f"- Under \"{parent}\": \"{sub}\"" for parent, sub in gaps
    )

    system_prompt = (
        f"Fill in missing subsections for a {fw_list} framework guide.\n\n"
        "RULES:\n"
        "- Write content ONLY for the empty subsections listed below\n"
        "- Use the search results as the source of truth\n"
        "- Include short code snippets where appropriate\n"
        "- Every fenced code block must have matching ``` pairs\n"
        "- Do NOT repeat content that already exists in the guide\n"
        "- Do NOT rewrite or restructure existing sections\n\n"
        "OUTPUT FORMAT:\n"
        "For each gap, output:\n"
        "### HEADING_TEXT\n"
        "(your content here)\n\n"
        "Output ONLY the filled subsections, nothing else."
    )

    user_msg = (
        f"CURRENT GUIDE:\n{guide}\n\n"
        f"EMPTY SUBSECTIONS TO FILL:\n{gap_list}\n\n"
        f"NEW SEARCH RESULTS:\n{search_content}"
    )

    try:
        response = await llm_client.chat_raw(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg[:40000]},
            ],
            max_tokens=max_tokens,
        )
    except Exception as exc:
        logger.warning("Framework guide: gap-fill LLM call failed: %s", exc)
        return guide

    if not response.strip():
        return guide

    # Parse response into heading → content blocks.
    filled: dict[str, str] = {}
    current_heading = ""
    current_lines: list[str] = []

    for line in response.split("\n"):
        stripped = line.strip()
        if stripped.startswith("### "):
            if current_heading and current_lines:
                filled[current_heading] = "\n".join(current_lines).strip()
            current_heading = stripped.lstrip("# ").strip()
            current_lines = []
        elif current_heading:
            current_lines.append(line)

    if current_heading and current_lines:
        filled[current_heading] = "\n".join(current_lines).strip()

    if not filled:
        logger.info("Framework guide: gap-fill LLM returned no parseable sections")
        return guide

    # Splice filled content into the guide after each empty heading.
    result_lines = guide.split("\n")
    # Process in reverse order so insertions don't shift indices.
    insertions: list[tuple[int, str]] = []  # (line_idx, content)

    for line_idx in range(len(result_lines)):
        stripped = result_lines[line_idx].strip()
        heading_text = ""
        if stripped.startswith("### "):
            heading_text = stripped.lstrip("# ").strip()
        elif stripped.startswith("**") and stripped.endswith(":**"):
            heading_text = stripped.strip("*: ").strip()

        if heading_text and heading_text in filled:
            # Check if this heading is actually empty (in our gaps list).
            is_gap = any(sub == heading_text for _, sub in gaps)
            if is_gap:
                insertions.append((line_idx, filled[heading_text]))

    # Apply insertions in reverse order.
    for line_idx, content in sorted(insertions, reverse=True):
        # Find the end of the empty area after this heading.
        end_idx = line_idx + 1
        while end_idx < len(result_lines):
            s = result_lines[end_idx].strip()
            if (s.startswith("## ") or s.startswith("### ")
                    or (s.startswith("**") and s.endswith(":**"))):
                break
            end_idx += 1

        # Replace empty content between heading and next heading.
        result_lines[line_idx + 1:end_idx] = [
            "", content, "",
        ]

    filled_count = len(insertions)
    logger.info(
        "Framework guide: gap-fill spliced %d/%d subsection(s)",
        filled_count, len(gaps),
    )
    return "\n".join(result_lines)


# ---------------------------------------------------------------------------
# Smart page extraction — LLM-based content selection
# ---------------------------------------------------------------------------

async def _extract_page_content(
    raw_text: str,
    url: str,
    extraction_focus: str,
    framework_label: str,
    llm_client: LLMClient,
    max_output_chars: int = _EXTRACTED_PAGE_CAP,
) -> str:
    """Extract the most relevant content from a fetched web page.

    If *raw_text* is shorter than *max_output_chars*, returns it as-is
    without an LLM call.  Otherwise, asks the LLM to extract focused
    content (code snippets, API signatures, CLI commands, directory
    structures) based on the *extraction_focus*.

    Falls back to simple truncation on any failure.
    """
    if len(raw_text) <= max_output_chars:
        return raw_text

    # Cap raw input to avoid overwhelming the LLM
    capped = raw_text[:_RAW_FETCH_CAP]

    try:
        extracted = await llm_client.chat_raw(
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Extract the most relevant information from this "
                        f"web page for a {framework_label} framework guide "
                        "that an AI coding agent will use as its primary "
                        "framework reference.\n\n"
                        f"Focus on: {extraction_focus}\n\n"
                        "Prioritize official documentation patterns over "
                        "blog opinions or tutorials. "
                        "Output ONLY the extracted content — code snippets, "
                        "API signatures, CLI commands, directory structures, "
                        "configuration examples. No commentary or summaries. "
                        f"Maximum {max_output_chars} characters."
                    ),
                },
                {"role": "user", "content": capped},
            ],
            max_tokens=_EXTRACTION_MAX_TOKENS,
        )

        if extracted and extracted.strip():
            logger.info(
                "Framework guide: extracted %d chars from %s "
                "(raw: %d chars)",
                len(extracted), url, len(raw_text),
            )
            return extracted[:max_output_chars]
    except Exception as exc:
        logger.info(
            "Framework guide: extraction failed for %s, "
            "falling back to truncation: %s",
            url, exc,
        )

    return raw_text[:max_output_chars]


# ---------------------------------------------------------------------------
# Per-section search + fetch + extract
# ---------------------------------------------------------------------------

async def _search_and_fetch_for_section(
    section_spec: dict,
    frameworks: list[tuple[str, str]],
    runtimes: list[tuple[str, str]],
    framework_label: str,
    llm_client: LLMClient,
    cutoff: str | None,
    search_timeout: int,
) -> tuple[list[str], list[str]]:
    """Run searches and fetch pages scoped to one guide section.

    Returns ``(search_snippets, extracted_pages)`` — lists of formatted
    text blocks ready for inclusion in the LLM prompt.
    """
    from lean_ai.context.framework_detection import (
        build_section_search_queries_llm,
    )
    from lean_ai.tools.internet import fetch_url, search_internet

    # 1. Generate queries for this section
    queries = await build_section_search_queries_llm(
        frameworks=frameworks,
        runtimes=runtimes,
        section_heading=section_spec["heading"],
        search_topics=section_spec["search_topics"],
        llm_client=llm_client,
        cutoff=cutoff,
        max_queries=section_spec.get("max_queries", 4),
    )

    # 2. Run web searches
    search_parts: list[str] = []
    query_results: list[list[tuple[str, str, str]]] = []

    for i, query in enumerate(queries, 1):
        extracted: list[tuple[str, str, str]] = []
        try:
            result = await asyncio.wait_for(
                search_internet(query, llm_client=None),
                timeout=search_timeout,
            )
            if result.success and result.output:
                search_parts.append(
                    f"=== Search: {query} ===\n{result.output}"
                )
                extracted = extract_search_results(result.output)
                logger.info(
                    "Framework guide [%s]: search %d/%d %r -> OK "
                    "(%d chars)",
                    section_spec["heading"], i, len(queries),
                    query, len(result.output),
                )
            else:
                logger.info(
                    "Framework guide [%s]: search %d/%d %r "
                    "-> no results",
                    section_spec["heading"], i, len(queries), query,
                )
        except asyncio.TimeoutError:
            logger.info(
                "Framework guide [%s]: search %d/%d %r -> timed out",
                section_spec["heading"], i, len(queries), query,
            )
        except Exception as exc:
            logger.info(
                "Framework guide [%s]: search %d/%d %r -> failed: %s",
                section_spec["heading"], i, len(queries), query, exc,
            )
        query_results.append(extracted)

    # 3. Select and fetch URLs
    fetch_urls = select_one_per_query(query_results)

    page_parts: list[str] = []
    page_chars_used = 0

    for j, url_candidates in enumerate(fetch_urls, 1):
        if page_chars_used >= _SECTION_PAGE_BUDGET:
            break
        for url in url_candidates:
            try:
                page_result = await asyncio.wait_for(
                    fetch_url(url, llm_client=None),
                    timeout=20,
                )
                if page_result.success and page_result.output:
                    # 4. Smart extraction
                    text = await _extract_page_content(
                        raw_text=page_result.output,
                        url=url,
                        extraction_focus=section_spec["extraction_focus"],
                        framework_label=framework_label,
                        llm_client=llm_client,
                    )
                    page_parts.append(f"=== Page: {url} ===\n{text}")
                    page_chars_used += len(text)
                    logger.info(
                        "Framework guide [%s]: fetched page %d/%d "
                        "(%d chars): %s",
                        section_spec["heading"], j, len(fetch_urls),
                        len(text), url,
                    )
                    break  # Got a page for this query
                else:
                    logger.info(
                        "Framework guide [%s]: page empty: %s",
                        section_spec["heading"], url,
                    )
            except asyncio.TimeoutError:
                logger.info(
                    "Framework guide [%s]: page timed out: %s",
                    section_spec["heading"], url,
                )
            except Exception as exc:
                logger.info(
                    "Framework guide [%s]: page failed: %s — %s",
                    section_spec["heading"], url, exc,
                )

    return search_parts, page_parts


# ---------------------------------------------------------------------------
# Section-specific system prompt
# ---------------------------------------------------------------------------

def _build_section_system_prompt(
    frameworks: list[tuple[str, str]],
    runtimes: list[tuple[str, str]],
    section_spec: dict,
    cutoff: str | None = None,
) -> str:
    """Build a focused system prompt for generating one guide section."""
    from lean_ai.context.deprecations import _extract_major_minor

    fw_list = ", ".join(
        f"{canonicalize_name(name)} {_extract_major_minor(ver)}"
        if ver else canonicalize_name(name)
        for name, ver in frameworks
    )
    rt_list = ", ".join(
        f"{canonicalize_name(name)} {_extract_major_minor(ver)}"
        if ver else canonicalize_name(name)
        for name, ver in runtimes
    ) or "not detected"

    cutoff_block = ""
    if cutoff:
        cutoff_block = (
            f"YOUR TRAINING DATA CUTOFF: {cutoff}. Any framework "
            "changes after this date are NOT in your training data. "
            "You MUST rely on the web search results and fetched page "
            "content for information after this date. Do NOT guess "
            "about post-cutoff features, file locations, or APIs.\n\n"
        )

    return (
        f"Generate the {section_spec['heading']} section of a "
        f"framework guide.\n\n"
        "PURPOSE: This guide serves as the primary framework reference "
        "for an AI coding agent that will use it to make architectural "
        "decisions, write correct code, and follow framework conventions. "
        "Accuracy and depth are critical — the agent has no other source "
        "of framework knowledge. Prioritize information from official "
        "documentation over blog posts, tutorials, or community guides. "
        "Include the exact patterns, class names, and conventions that "
        "the framework enforces — the agent needs to produce code that "
        "follows these patterns precisely.\n\n"
        f"DETECTED FRAMEWORKS: {fw_list}\n"
        f"DETECTED RUNTIMES: {rt_list}\n\n"
        f"{cutoff_block}"
        "The content must be tailored to THIS project's specific "
        "framework and version. Use the web search results and the "
        "project file tree to produce content that covers framework "
        "concepts and this project's actual structure.\n\n"
        "Use the PROJECT FILE TREE to identify which components this "
        "project actually uses. Tailor examples to use names from the "
        "project's actual files rather than generic placeholders.\n\n"
        "Use the KEY PROJECT FILES section for actual source code from "
        "this project. Prefer their content over training knowledge "
        "when writing code snippets and describing architecture.\n\n"
        "STRUCTURE RULES:\n"
        "- Every fenced code block MUST have a matching opening "
        "and closing ``` pair. Never leave a code block unclosed.\n"
        "- Never put multiple PHP files or multiple code languages "
        "inside a single fenced code block. Each code example gets "
        "its own ``` pair with its own language tag.\n"
        "- Separate every code block from surrounding text with a "
        "brief prose explanation of what it shows.\n"
        "- Never leave a subsection heading empty — if a subsection "
        "has no applicable content, write: \"No information available "
        "for this version.\"\n"
        "- Number steps sequentially starting from 1 with no gaps.\n\n"
        "CONTENT RULES:\n"
        f"- ONLY cover the detected frameworks: {fw_list}\n"
        "- Web search results and fetched page content are the "
        "SOURCE OF TRUTH for version-specific details. If they "
        "contradict your training knowledge, ALWAYS prefer the "
        "search results. Frameworks change significantly between "
        "major versions — do not assume features from older versions "
        "still exist.\n"
        "- CROSS-CHECK: Before including any file path, class name, "
        "or CLI command, verify it appears in either the web search "
        "results, the fetched page content, or the project file "
        "tree. If it does not appear in any of these sources and "
        "the information postdates your training cutoff, DO NOT "
        "include it.\n"
        "- NEVER reference files, classes, or commands that do not "
        "exist in the detected version. If unsure whether something "
        "exists in this version, omit it rather than guess.\n"
        "- Use concrete code examples (short snippets, not full "
        "files).\n"
        "- Reference actual class names and method names from the "
        "framework.\n\n"
        f"SECTION TO GENERATE:\n\n{section_spec['section_prompt']}\n\n"
        "IMPORTANT: Write the section content directly. Do NOT echo "
        "back the instructions above. Do NOT start with phrases like "
        "'Explain the framework' or 'Identify the major component' "
        "— just write the content.\n"
    )


# ---------------------------------------------------------------------------
# Section generation
# ---------------------------------------------------------------------------

async def _generate_section(
    section_spec: dict,
    frameworks: list[tuple[str, str]],
    runtimes: list[tuple[str, str]],
    framework_label: str,
    project_tree: str,
    prior_sections: list[str],
    key_files_content: str,
    llm_client: LLMClient,
    cutoff: str | None,
    search_timeout: int,
    max_tokens: int,
) -> str:
    """Generate content for one framework guide section.

    Runs a dedicated search/fetch/extract cycle, then calls the LLM
    with a section-specific prompt.  Prior sections are included as
    context so the LLM can maintain consistency.
    """
    # 1. Search and fetch for this section
    search_parts, page_parts = await _search_and_fetch_for_section(
        section_spec=section_spec,
        frameworks=frameworks,
        runtimes=runtimes,
        framework_label=framework_label,
        llm_client=llm_client,
        cutoff=cutoff,
        search_timeout=search_timeout,
    )

    # 2. Deduplicate search snippets
    search_parts = _deduplicate_search_parts(search_parts)

    # 3. Build user message with per-component budgets so that
    #    prior sections cannot crowd out search evidence.
    user_parts: list[str] = []
    if search_parts:
        search_text = "\n\n".join(search_parts)
        user_parts.append(
            "WEB SEARCH RESULTS (snippets):\n\n"
            + search_text[:_SEARCH_BUDGET]
        )
    if page_parts:
        page_text = "\n\n".join(page_parts)
        user_parts.append(
            "FULL PAGE CONTENT (from top search results):\n\n"
            + page_text[:_PAGE_BUDGET]
        )
    if key_files_content:
        user_parts.append(
            "KEY PROJECT FILES (actual source from this project — "
            "prefer over training knowledge):\n\n"
            + key_files_content[:_KEY_FILES_BUDGET]
        )
    if project_tree:
        user_parts.append(
            f"PROJECT FILE TREE:\n{project_tree[:_TREE_BUDGET]}"
        )
    if prior_sections:
        # Only inject the 2 most recent sections to keep input budget
        # for search evidence.  Earlier sections are already
        # synthesised into the newer ones.
        recent = (
            prior_sections[-2:]
            if len(prior_sections) > 2
            else prior_sections
        )
        prior_text = "\n\n".join(recent)
        user_parts.append(
            "EARLIER SECTIONS (for reference — maintain consistency, "
            "do not repeat content):\n\n"
            + prior_text[:_PRIOR_SECTIONS_BUDGET]
        )

    user_content = (
        "\n\n".join(user_parts)
        if user_parts
        else "Generate based on training knowledge."
    )
    user_content = user_content[:_TOTAL_INPUT_CAP]

    # 4. Build section system prompt
    system_prompt = _build_section_system_prompt(
        frameworks=frameworks,
        runtimes=runtimes,
        section_spec=section_spec,
        cutoff=cutoff,
    )

    logger.info(
        "Framework guide: generating section %s "
        "(%d snippets, %d pages, %d-char prompt)",
        section_spec["heading"],
        len(search_parts), len(page_parts),
        len(user_content),
    )

    # 5. LLM generates the section
    try:
        section = await llm_client.chat_raw(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            max_tokens=max_tokens,
        )
    except Exception as exc:
        logger.warning(
            "Framework guide: section generation failed for %s: %s",
            section_spec["heading"], exc,
        )
        return ""

    if not section or not section.strip():
        logger.info(
            "Framework guide: LLM returned empty output for %s",
            section_spec["heading"],
        )
        return ""

    section = _strip_prompt_echoes(section.strip(), section_spec)
    return section


# ---------------------------------------------------------------------------
# LLM system prompt (used by basic mode and post-processing)
# ---------------------------------------------------------------------------

def _build_guide_system_prompt(
    frameworks: list[tuple[str, str]],
    runtimes: list[tuple[str, str]],
    cutoff: str | None = None,
) -> str:
    """Build the system prompt that instructs the LLM to generate a guide."""
    from lean_ai.context.deprecations import _extract_major_minor

    fw_list = ", ".join(
        f"{canonicalize_name(name)} {_extract_major_minor(ver)}"
        if ver else canonicalize_name(name)
        for name, ver in frameworks
    )
    rt_list = ", ".join(
        f"{canonicalize_name(name)} {_extract_major_minor(ver)}"
        if ver else canonicalize_name(name)
        for name, ver in runtimes
    ) or "not detected"

    # Cutoff awareness block — tells the LLM exactly what it doesn't know
    cutoff_block = ""
    if cutoff:
        cutoff_block = (
            f"YOUR TRAINING DATA CUTOFF: {cutoff}. Any framework "
            "changes after this date are NOT in your training data. "
            "You MUST rely on the web search results and fetched page "
            "content for information after this date. Do NOT guess "
            "about post-cutoff features, file locations, or APIs.\n\n"
        )

    return (
        "Generate a framework guide for a development project.\n\n"
        "PURPOSE: This guide serves as the primary framework reference "
        "for an AI coding agent that will use it to make architectural "
        "decisions, write correct code, and follow framework conventions. "
        "Accuracy and depth are critical — the agent has no other source "
        "of framework knowledge. Prioritize information from official "
        "documentation over blog posts, tutorials, or community guides. "
        "Include the exact patterns, class names, and conventions that "
        "the framework enforces — the agent needs to produce code that "
        "follows these patterns precisely.\n\n"
        f"DETECTED FRAMEWORKS: {fw_list}\n"
        f"DETECTED RUNTIMES: {rt_list}\n\n"
        f"{cutoff_block}"
        "The guide must be tailored to THIS project's specific framework "
        "and version. Use the web search results and the project file "
        "tree to produce a guide that covers framework concepts and "
        "this project's actual structure.\n\n"
        "Use the PROJECT FILE TREE to identify which components this "
        "project actually uses. Tailor examples to use names from the "
        "project's actual files rather than generic placeholders.\n\n"
        # ── Rules at the top — highest attention weight ──
        "STRUCTURE RULES:\n"
        "- Each ## heading below MUST appear EXACTLY ONCE. Never "
        "repeat a ## heading. If you have already written "
        "## Framework Architecture, do not write it again.\n"
        "- Do NOT restart or re-draft sections. Write each section "
        "once, completely, then move to the next.\n"
        "- Every fenced code block MUST have a matching opening "
        "and closing ``` pair. Never leave a code block unclosed.\n"
        "- Never put multiple PHP files or multiple code languages "
        "inside a single fenced code block. Each code example gets "
        "its own ``` pair with its own language tag.\n"
        "- Separate every code block from surrounding text with a "
        "brief prose explanation of what it shows.\n"
        "- Never leave a subsection heading empty — if a subsection "
        "has no applicable content, write: \"No information available "
        "for this version.\"\n"
        "- Number steps sequentially starting from 1 with no gaps.\n\n"
        "CONTENT RULES:\n"
        f"- ONLY cover the detected frameworks: {fw_list}\n"
        "- Web search results and fetched page content are the "
        "SOURCE OF TRUTH for version-specific details. If they "
        "contradict your training knowledge, ALWAYS prefer the "
        "search results. Frameworks change significantly between "
        "major versions — do not assume features from older versions "
        "still exist.\n"
        "- CROSS-CHECK: Before including any file path, class name, "
        "or CLI command, verify it appears in either the web search "
        "results, the fetched page content, or the project file "
        "tree. If it does not appear in any of these sources and "
        "the information postdates your training cutoff, DO NOT "
        "include it.\n"
        "- NEVER reference files, classes, or commands that do not "
        "exist in the detected version. If unsure whether something "
        "exists in this version, omit it rather than guess.\n"
        "- Use concrete code examples (short snippets, not full "
        "files).\n"
        "- Reference actual class names and method names from the "
        "framework.\n\n"
        # ── Full section descriptions ──
        "REQUIRED SECTIONS (use exactly these ## headings):\n\n"
        "## Framework Architecture\n"
        "Explain the framework's architectural pattern (MVC, MVVM, "
        "component-based, etc.) and the request/render lifecycle for "
        "THIS version. Describe how a request flows from entry point "
        "to response as a numbered sequence — which files and classes "
        "are involved at each stage. Only reference files and classes "
        "that exist in the detected version.\n\n"
        "## Component Relationships\n"
        "Identify the major component relationships that exist in the "
        "detected framework. For EACH relationship show a SHORT code "
        "snippet demonstrating the exact connection point between the "
        "two components.\n\n"
        "Common relationships to cover (skip any that do not apply to "
        "this framework):\n"
        "- Data schema to data model (migrations/schema to ORM "
        "model/entity)\n"
        "- Route definition to handler (URL routing to controller/"
        "view function/handler)\n"
        "- Handler to template/response (controller to view/template/"
        "serializer)\n"
        "- Middleware or interceptors in the request pipeline\n"
        "- Dependency injection or service registration (if the "
        "framework uses it)\n"
        "- Request validation (form objects, request classes, schema "
        "validators)\n\n"
        "For each relationship:\n"
        "- Show both sides of the connection as code snippets\n"
        "- Explain the naming convention or configuration that links "
        "them\n"
        "- Note which file(s) each side lives in\n\n"
        "Do NOT draw ASCII dependency diagrams. Describe the request "
        "flow as a numbered prose sequence.\n\n"
        "## Common CLI Commands\n"
        "List the essential CLI commands for this EXACT version. Group "
        "by purpose:\n"
        "- Project setup and scaffolding\n"
        "- Database (migrations, seeding, schema inspection)\n"
        "- Development server and tooling\n"
        "- Testing and debugging\n"
        "- Cache, config, and maintenance\n\n"
        "For each command show the EXACT syntax including all flags.\n\n"
        "CRITICAL: Only include commands and flags you are CERTAIN "
        "exist in the detected version:\n"
        "- Do not mix up flags between different subcommands\n"
        "- Do not invent commands or flags that do not exist\n"
        "- If the fetched page content includes CLI documentation, "
        "use those exact command signatures\n\n"
        "## File Organization Conventions\n"
        "Describe the standard directory structure for THIS version "
        "and where each type of component lives. Map directories to "
        "framework concepts. Only list directories that exist in this "
        "version — do not carry over directory structures from older "
        "versions.\n\n"
        "## Adding a New Feature\n"
        "Provide a step-by-step workflow for adding a typical new "
        "feature (e.g., a new CRUD resource). IMPORTANT:\n"
        "- Do NOT create the same artifact twice (e.g., if a command "
        "creates both a model and migration, do not also run a "
        "separate migration command)\n"
        "- Show the SINGLE optimal command that creates the most "
        "artifacts at once, then list what it generated\n"
        "- For each step show the exact file(s) created and what to "
        "add to them\n"
        "- Show how each new file connects back to previously created "
        "components\n\n"
        "## Common Patterns and Pitfalls\n"
        "List 5-10 patterns that are easy to get wrong in the "
        "DETECTED VERSION. Each pattern MUST be specific to this "
        "version — generic advice that applies to all versions is "
        "not useful. Focus on:\n"
        "- What changed in this version vs. the previous major "
        "version\n"
        "- Deprecated features that developers might still try to "
        "use\n"
        "- New recommended patterns that replace old ones\n"
        "- Naming conventions the framework enforces implicitly\n"
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def _generate_guide_basic(
    repo_root: str,
    llm_client: LLMClient,
    frameworks: list[tuple[str, str]],
    runtimes: list[tuple[str, str]],
    max_tokens: int,
) -> str:
    """Single-pass guide generation (basic mode).

    Original monolithic flow: one set of searches, one LLM call.
    """
    from lean_ai.config import settings
    from lean_ai.tools.internet import fetch_url, search_internet

    llm_queries = await build_guide_search_queries_llm(
        frameworks, runtimes, llm_client, cutoff=None,
    )

    if llm_queries is not None:
        from lean_ai.context.deprecations import _extract_major_minor

        for name, version in frameworks:
            v = _extract_major_minor(version)
            canonical = canonicalize_name(name)
            label = f"{canonical} {v}" if v else canonical
            llm_queries.append(
                f"{label} changelog breaking changes new features",
            )

    queries = llm_queries or build_guide_search_queries(
        frameworks, runtimes, cutoff=None,
    )

    project_tree = get_compact_tree(repo_root)

    # Web search (sequential — primp/lxml not thread-safe)
    search_parts: list[str] = []
    query_results: list[list[tuple[str, str, str]]] = []
    search_timeout = 90 if settings.search_provider in ("google", "bing") else 15

    logger.info("Framework guide [basic]: running %d web searches", len(queries))
    for i, query in enumerate(queries, 1):
        extracted: list[tuple[str, str, str]] = []
        try:
            result = await asyncio.wait_for(
                search_internet(query, llm_client=None),
                timeout=search_timeout,
            )
            if result.success and result.output:
                search_parts.append(
                    f"=== Search: {query} ===\n{result.output}"
                )
                extracted = extract_search_results(result.output)
        except (asyncio.TimeoutError, Exception):
            pass
        query_results.append(extracted)

    fetch_urls = select_one_per_query(query_results)
    page_parts: list[str] = []
    page_chars_used = 0

    for j, url_candidates in enumerate(fetch_urls, 1):
        if page_chars_used >= 32000:
            break
        for url in url_candidates:
            try:
                page_result = await asyncio.wait_for(
                    fetch_url(url, llm_client=None), timeout=20,
                )
                if page_result.success and page_result.output:
                    cap = min(5000, 32000 - page_chars_used)
                    text = page_result.output[:cap]
                    page_parts.append(f"=== Page: {url} ===\n{text}")
                    page_chars_used += len(text)
                    break
            except (asyncio.TimeoutError, Exception):
                continue

    search_parts = _deduplicate_search_parts(search_parts)

    user_parts: list[str] = []
    if search_parts:
        user_parts.append(
            "WEB SEARCH RESULTS (snippets):\n\n"
            + "\n\n".join(search_parts)
        )
    if page_parts:
        user_parts.append(
            "FULL PAGE CONTENT (from top search results):\n\n"
            + "\n\n".join(page_parts)
        )
    if project_tree:
        user_parts.append(f"PROJECT FILE TREE:\n{project_tree}")

    user_content = (
        "\n\n".join(user_parts) if user_parts
        else "Generate based on training knowledge."
    )

    try:
        guide = await llm_client.chat_raw(
            messages=[
                {
                    "role": "system",
                    "content": _build_guide_system_prompt(
                        frameworks, runtimes, cutoff=None,
                    ),
                },
                {"role": "user", "content": user_content[:40000]},
            ],
            max_tokens=max_tokens,
        )
    except Exception as exc:
        logger.warning("Framework guide [basic]: LLM generation failed: %s", exc)
        return ""

    if not guide.strip():
        return ""

    return await _postprocess_guide(
        guide, repo_root, llm_client, frameworks, runtimes,
        None, search_timeout, max_tokens,
    )


async def _generate_guide_deep(
    repo_root: str,
    llm_client: LLMClient,
    frameworks: list[tuple[str, str]],
    runtimes: list[tuple[str, str]],
    max_tokens: int,
) -> str:
    """Multi-pass guide generation (deep mode).

    Generates each section independently with its own dedicated
    search/fetch/extract cycle for deeper, more focused content.
    """
    from lean_ai.config import settings

    fw_label = ", ".join(
        canonicalize_name(name) for name, _ver in frameworks
    )

    project_tree = get_compact_tree(repo_root)
    key_files = _collect_key_file_contents(repo_root)
    search_timeout = 90 if settings.search_provider in ("google", "bing") else 15

    # Per-section max_tokens: weighted by section complexity
    min_section_tokens = 2048
    total_weight = sum(s.get("weight", 3) for s in _SECTION_SPECS)
    section_budgets = [
        max(
            min_section_tokens,
            (max_tokens * s.get("weight", 3)) // total_weight,
        )
        for s in _SECTION_SPECS
    ]

    generated_sections: list[str] = []
    for i, spec in enumerate(_SECTION_SPECS):
        logger.info(
            "Framework guide [deep]: generating section %d/%d: %s "
            "(%d max_tokens)",
            i + 1, len(_SECTION_SPECS), spec["heading"],
            section_budgets[i],
        )
        section_content = await _generate_section(
            section_spec=spec,
            frameworks=frameworks,
            runtimes=runtimes,
            framework_label=fw_label,
            project_tree=project_tree,
            prior_sections=generated_sections,
            key_files_content=key_files,
            llm_client=llm_client,
            cutoff=None,
            search_timeout=search_timeout,
            max_tokens=section_budgets[i],
        )
        if section_content.strip():
            generated_sections.append(section_content)

    if not generated_sections:
        logger.info("Framework guide [deep]: all sections empty")
        return ""

    guide = "\n\n".join(generated_sections)

    return await _postprocess_guide(
        guide, repo_root, llm_client, frameworks, runtimes,
        None, search_timeout, max_tokens,
    )


async def _postprocess_guide(
    guide: str,
    repo_root: str,
    llm_client: LLMClient,
    frameworks: list[tuple[str, str]],
    runtimes: list[tuple[str, str]],
    cutoff: str | None,
    search_timeout: int,
    max_tokens: int,
) -> str:
    """Shared post-processing: repair, dedup, validate, gap-fill."""
    from lean_ai.tools.internet import search_internet

    guide = _repair_code_blocks(guide)
    guide = _deduplicate_sections(guide)

    try:
        guide = await _validate_guide(
            guide,
            repo_root,
            llm_client,
            _build_guide_system_prompt(frameworks, runtimes, cutoff=cutoff),
        )
    except Exception as exc:
        logger.warning(
            "Framework guide: validation pass failed (non-blocking): %s",
            exc,
        )

    guide = _renumber_steps(guide)

    # Gap-fill: 1 iteration for deep mode (sections are pre-filled),
    # kept as safety net.
    try:
        gaps = _find_empty_subsections(guide)
        if gaps:
            logger.info(
                "Framework guide: gap-fill — %d empty subsection(s): %s",
                len(gaps), [sub for _, sub in gaps],
            )

            gap_queries = await build_gap_fill_queries_llm(
                frameworks, runtimes, gaps, llm_client,
            )
            if gap_queries:
                gap_search_parts: list[str] = []
                for qi, query in enumerate(gap_queries, 1):
                    try:
                        result = await asyncio.wait_for(
                            search_internet(query, llm_client=None),
                            timeout=search_timeout,
                        )
                        if result.success and result.output:
                            gap_search_parts.append(
                                f"=== Search: {query} ===\n"
                                f"{result.output}"
                            )
                    except (asyncio.TimeoutError, Exception):
                        pass

                if gap_search_parts:
                    gap_content = "\n\n".join(gap_search_parts)
                    guide = await _fill_guide_gaps(
                        guide, gaps, gap_content, llm_client,
                        frameworks, runtimes, cutoff, max_tokens,
                    )
                    guide = _repair_code_blocks(guide)
                    guide = _renumber_steps(guide)
    except Exception as exc:
        logger.warning(
            "Framework guide: gap-fill failed (non-blocking): %s", exc,
        )

    return guide


async def generate_framework_guide(
    repo_root: str,
    llm_client: LLMClient,
    max_tokens: int | None = None,
) -> str:
    """Detect frameworks, search for best practices, and generate a guide.

    *max_tokens* defaults to 25 % of the active context window (same
    derivation as project context and implementation turns).

    Uses ``LEAN_AI_FRAMEWORK_GUIDE_DEPTH`` to select the generation
    strategy: ``"basic"`` (single-pass) or ``"deep"`` (multi-pass,
    per-section searches with smart extraction).

    Returns the guide content as a Markdown string, or ``""`` if no
    frameworks are detected or any step fails.
    """
    from lean_ai.config import settings

    if not settings.enable_framework_guide:
        return ""

    if max_tokens is None:
        max_tokens = settings.implementation_max_tokens or 4096

    try:
        frameworks, runtimes = get_primary_frameworks(repo_root)
    except Exception as exc:
        logger.warning("Framework guide: detection failed: %s", exc)
        return ""

    if not frameworks:
        logger.info("Framework guide: no frameworks detected, skipping")
        return ""

    logger.info(
        "Framework guide: detected %d framework(s): %s",
        len(frameworks),
        ", ".join(f"{n} {v}" for n, v in frameworks),
    )

    depth = (settings.framework_guide_depth or "deep").lower()

    if depth == "basic":
        logger.info("Framework guide: using basic (single-pass) mode")
        guide = await _generate_guide_basic(
            repo_root, llm_client, frameworks, runtimes, max_tokens,
        )
    else:
        logger.info("Framework guide: using deep (multi-pass) mode")
        guide = await _generate_guide_deep(
            repo_root, llm_client, frameworks, runtimes, max_tokens,
        )

    if not guide:
        return ""

    fw_label = ", ".join(
        canonicalize_name(name) for name, _ver in frameworks
    )
    content = (
        f"# Framework Guide: {fw_label}\n\n"
        "_Auto-generated. Edit freely — this file is yours to curate._\n\n"
        f"{guide.strip()}\n"
    )

    logger.info("Framework guide: generated %d-char guide", len(content))
    return content


# ---------------------------------------------------------------------------
# File writer
# ---------------------------------------------------------------------------

def write_framework_guide(repo_root: str, content: str) -> str:
    """Write framework guide to ``.lean_ai/framework_guide.md``."""
    output_dir = Path(repo_root) / ".lean_ai"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / "framework_guide.md"
    output_path.write_text(content, encoding="utf-8")

    logger.info("Framework guide written to %s", output_path)
    return str(output_path)

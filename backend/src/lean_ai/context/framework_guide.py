"""Framework guide generation — detect frameworks, search for best practices,
and produce a tailored ``.lean_ai/framework_guide.md``.

Runs as a post-generation step or standalone via endpoint.  Gracefully
returns ``""`` on any failure so it never blocks context generation.

No regex — all text processing uses simple string operations.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lean_ai.llm.client import LLMClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Name canonicalization — package manager names → search-friendly names
# ---------------------------------------------------------------------------

_CANONICAL_NAMES: dict[str, str] = {
    # PHP / Composer
    "laravel/framework": "Laravel",
    "laravel/tinker": "Laravel Tinker",
    "laravel/sanctum": "Laravel Sanctum",
    "laravel/cashier": "Laravel Cashier",
    "laravel/scout": "Laravel Scout",
    "laravel/horizon": "Laravel Horizon",
    "laravel/breeze": "Laravel Breeze",
    "laravel/jetstream": "Laravel Jetstream",
    "symfony/framework-bundle": "Symfony",
    "symfony/symfony": "Symfony",
    "symfony/console": "Symfony Console",
    "cakephp/cakephp": "CakePHP",
    # Go modules
    "github.com/gin-gonic/gin": "Gin",
    "github.com/labstack/echo": "Echo",
    "github.com/gofiber/fiber": "Fiber",
    # npm scoped
    "@angular/core": "Angular",
    "@nestjs/core": "NestJS",
    "@vue/core": "Vue",
    # .NET
    "microsoft.aspnetcore": "ASP.NET Core",
}


def _canonicalize_name(raw_name: str) -> str:
    """Convert a package manager name to a human-friendly search term.

    Checks an explicit mapping first, then applies heuristics for
    Composer ``vendor/package``, Go ``github.com/user/repo``, and npm
    scoped ``@scope/package`` formats.  Plain names (``django``,
    ``react``) pass through unchanged.
    """
    # Exact match in mapping
    if raw_name in _CANONICAL_NAMES:
        return _CANONICAL_NAMES[raw_name]
    lower = raw_name.lower()
    if lower in _CANONICAL_NAMES:
        return _CANONICAL_NAMES[lower]

    # npm scoped: @scope/package → package part, title-cased
    if raw_name.startswith("@") and "/" in raw_name:
        return raw_name.split("/")[-1].replace("-", " ").title()

    # Composer vendor/package or Go github.com/user/repo
    if "/" in raw_name:
        parts = raw_name.split("/")
        # Go modules: github.com/user/repo → last segment
        if "." in parts[0]:
            return parts[-1].replace("-", " ").title()
        # Composer: vendor/package → package, title-cased
        return parts[-1].replace("-", " ").title()

    # Already fine (django, flask, react, rails, axum, etc.)
    return raw_name


# ---------------------------------------------------------------------------
# Post-generation validation — file-path extraction
# ---------------------------------------------------------------------------

_FILE_EXTENSIONS = frozenset({
    ".php", ".js", ".ts", ".tsx", ".jsx", ".py", ".rb",
    ".java", ".go", ".rs", ".c", ".h", ".cpp", ".cs",
    ".vue", ".svelte", ".blade.php", ".erb", ".html",
    ".css", ".scss", ".json", ".yaml", ".yml", ".toml",
    ".xml", ".sql", ".sh", ".env",
})


def _extract_file_paths(text: str) -> set[str]:
    """Extract potential file paths from markdown text.

    Looks for substrings containing ``/`` that end with a known
    file extension.  Strips surrounding punctuation (backticks,
    parens, quotes).  Ignores URLs.
    """
    paths: set[str] = set()
    for word in text.split():
        clean = word.strip("`()[]{}\"',:;")
        if "/" not in clean or clean.startswith(("http://", "https://")):
            continue
        # Handle .blade.php (compound extension)
        if clean.endswith(".blade.php"):
            paths.add(clean)
            continue
        # Check single extensions
        dot = clean.rfind(".")
        if dot != -1 and clean[dot:] in _FILE_EXTENSIONS:
            paths.add(clean)
    return paths


# PHP vendor namespace prefixes — these are framework/library namespaces
# that should NOT be validated against the project tree.
_PHP_STDLIB_PREFIXES = frozenset({
    "Illuminate", "Symfony", "Carbon", "Doctrine", "League",
    "Monolog", "Psr", "GuzzleHttp", "Ramsey", "Faker",
    "PHPUnit", "Mockery", "Composer",
})


def _extract_php_class_refs(text: str) -> dict[str, str]:
    """Extract PHP namespace references and convert to file paths.

    Finds backslash-separated tokens starting with an uppercase letter
    (e.g., ``App\\Http\\Kernel``) and converts them to file paths using
    PSR-4 convention: replace ``\\`` with ``/``, lowercase the first
    segment, append ``.php``.

    Skips vendor/framework namespaces (Illuminate, Symfony, etc.).

    Returns ``{converted_path: original_namespace}`` so callers can
    search for the original string in the text when removing blocks.
    """
    refs: dict[str, str] = {}
    for word in text.split():
        clean = word.strip("`()[]{}\"',:;")
        if "\\" not in clean:
            continue
        parts = clean.split("\\")
        if len(parts) < 2:
            continue
        # Must start with an uppercase letter (namespace convention)
        if not parts[0] or not parts[0][0].isupper():
            continue
        # Skip vendor/framework namespaces
        if parts[0] in _PHP_STDLIB_PREFIXES:
            continue
        # Convert to file path: App\Http\Kernel → app/Http/Kernel.php
        path_parts = list(parts)
        path_parts[0] = path_parts[0].lower()
        converted = "/".join(path_parts) + ".php"
        refs[converted] = clean
    return refs


def _get_project_paths(repo_root: str) -> set[str]:
    """Return a set of all file paths in the project (relative to root)."""
    from lean_ai.indexer.tree import list_repo_tree

    return {e.path for e in list_repo_tree(repo_root)}


def _check_invalid_paths(
    text: str,
    project_paths: set[str],
    project_top_dirs: set[str],
) -> set[str]:
    """Return referenced strings whose file paths don't exist in the project.

    Checks both explicit file paths (with ``/``) and PHP namespace
    references (with ``\\``) converted to paths via PSR-4.

    Returns the original text strings (not converted paths) so that
    block removal can find them in the source text.
    """
    # Explicit file paths — the string in the text IS the file path
    referenced = _extract_file_paths(text)
    invalid: set[str] = set()
    for path in referenced:
        top_dir = path.split("/")[0]
        if top_dir in project_top_dirs and path not in project_paths:
            invalid.add(path)

    # PHP namespace references — convert to path, validate, but return
    # the original namespace string for block matching
    php_refs = _extract_php_class_refs(text)
    for converted_path, original_namespace in php_refs.items():
        top_dir = converted_path.split("/")[0]
        if top_dir in project_top_dirs and converted_path not in project_paths:
            invalid.add(original_namespace)

    return invalid


def _find_block_boundaries(
    lines: list[str],
    path: str,
) -> list[tuple[int, int]]:
    """Find line ranges of Markdown blocks that reference *path*.

    Returns ``(start_inclusive, end_exclusive)`` tuples.  A "block" is
    a fenced code block, a list item, or a contiguous paragraph.
    """
    blocks: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].lstrip()
        # ── Fenced code block ──
        if stripped.startswith("```"):
            fence_start = i
            fence_has_path = path in lines[i]
            i += 1
            while i < len(lines):
                if path in lines[i]:
                    fence_has_path = True
                if lines[i].lstrip().startswith("```"):
                    i += 1
                    break
                i += 1
            if fence_has_path:
                blocks.append((fence_start, i))
            continue
        # ── Check if this line references the path ──
        if path not in lines[i]:
            i += 1
            continue
        # ── List item ──
        if stripped.startswith(("- ", "* ")) or (
            stripped[:1].isdigit() and ". " in stripped[:4]
        ):
            block_start = i
            i += 1
            while i < len(lines):
                s = lines[i].lstrip()
                if not lines[i].strip():
                    break
                if s.startswith(("- ", "* ")) or (
                    s[:1].isdigit() and ". " in s[:4]
                ):
                    break
                i += 1
            blocks.append((block_start, i))
            continue
        # ── Paragraph ──
        block_start = i
        while block_start > 0 and lines[block_start - 1].strip():
            if lines[block_start - 1].lstrip().startswith("#"):
                break
            block_start -= 1
        i += 1
        while i < len(lines) and lines[i].strip():
            if lines[i].lstrip().startswith("#"):
                break
            i += 1
        blocks.append((block_start, i))
    return blocks


def _remove_blocks(text: str, invalid_paths: set[str]) -> str:
    """Remove Markdown blocks referencing any *invalid_paths*.

    Uses line-level operations only — no LLM.
    """
    lines = text.split("\n")

    remove_ranges: list[tuple[int, int]] = []
    for path in invalid_paths:
        remove_ranges.extend(_find_block_boundaries(lines, path))

    if not remove_ranges:
        return text

    # Merge overlapping ranges
    remove_ranges.sort()
    merged: list[tuple[int, int]] = [remove_ranges[0]]
    for start, end in remove_ranges[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    remove_lines: set[int] = set()
    for start, end in merged:
        remove_lines.update(range(start, end))

    kept = [line for i, line in enumerate(lines) if i not in remove_lines]

    # Collapse triple+ blank lines to double
    cleaned: list[str] = []
    blank_count = 0
    for line in kept:
        if not line.strip():
            blank_count += 1
            if blank_count <= 2:
                cleaned.append(line)
        else:
            blank_count = 0
            cleaned.append(line)

    return "\n".join(cleaned)


async def _surgical_llm_fix(
    guide: str,
    invalid_paths: set[str],
    llm_client: LLMClient,
    system_prompt: str,
    max_llm_calls: int = 5,
) -> str:
    """Fix invalid path references by correcting individual blocks."""
    lines = guide.split("\n")

    # Collect all blocks referencing any invalid path
    all_ranges: list[tuple[int, int]] = []
    for path in invalid_paths:
        all_ranges.extend(_find_block_boundaries(lines, path))

    if not all_ranges:
        return guide

    # Merge overlapping ranges
    all_ranges.sort()
    merged: list[tuple[int, int]] = [all_ranges[0]]
    for start, end in all_ranges[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    # Cap at max_llm_calls by merging closest blocks
    while len(merged) > max_llm_calls and len(merged) > 1:
        min_gap = float("inf")
        min_idx = 0
        for idx in range(len(merged) - 1):
            gap = merged[idx + 1][0] - merged[idx][1]
            if gap < min_gap:
                min_gap = gap
                min_idx = idx
        merged[min_idx] = (merged[min_idx][0], merged[min_idx + 1][1])
        del merged[min_idx + 1]

    # Process in reverse order to preserve line indices
    result_lines = list(lines)
    for start, end in reversed(merged):
        block_text = "\n".join(lines[start:end])
        block_invalid = {p for p in invalid_paths if p in block_text}

        prompt = (
            "The following file paths do NOT exist in this project:\n"
            + "\n".join(f"- `{p}`" for p in sorted(block_invalid))
            + "\n\n"
            "Rewrite ONLY the following Markdown excerpt, correcting "
            "or removing references to the non-existent paths. "
            "If a code block references a non-existent file and you "
            "cannot determine the correct replacement, remove the "
            "entire code block. Output ONLY the corrected excerpt, "
            "nothing else.\n\n"
            "EXCERPT:\n" + block_text
        )

        try:
            fixed = await llm_client.chat_raw(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt[:8000]},
                ],
                max_tokens=1024,
            )
            fixed = fixed.strip()
            if fixed:
                result_lines[start:end] = fixed.split("\n")
        except Exception as exc:
            logger.info(
                "Framework guide: surgical fix lines %d-%d failed: %s",
                start, end, exc,
            )

    return "\n".join(result_lines)


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
    project_paths = _get_project_paths(repo_root)
    project_top_dirs = {p.split("/")[0] for p in project_paths}

    invalid = _check_invalid_paths(guide, project_paths, project_top_dirs)
    if not invalid:
        referenced = _extract_file_paths(guide)
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
    corrected = await _surgical_llm_fix(
        guide, invalid, llm_client, system_prompt,
    )

    still_invalid = _check_invalid_paths(
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
    stripped = _remove_blocks(corrected, still_invalid)

    final_invalid = _check_invalid_paths(
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


async def _deduplicate_sections(
    guide: str,
    llm_client: LLMClient,
) -> str:
    """Remove duplicate or overlapping ``##`` sections.

    Two-pass approach:
    1. LLM-based semantic dedup (catches near-duplicates with different
       wording).
    2. Mechanical dedup on the LLM result (catches exact heading matches
       the LLM missed).

    Always runs both passes — the LLM may remove some duplicates but
    miss others, and the old conditional logic only ran mechanical dedup
    when the LLM changed nothing at all.
    """
    from lean_ai.context.dedup import deduplicate_sections_llm

    result = await deduplicate_sections_llm(
        guide, llm_client, log_prefix="Framework guide",
    )

    # Always run mechanical dedup on the result — the LLM may have
    # removed some duplicates but missed exact heading matches.
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
        # by the PHP "$" prefix.
        detected_lang = ""
        if stripped.startswith(_BASH_PREFIXES):
            detected_lang = "bash"
        elif stripped.startswith(_PHP_PREFIXES):
            detected_lang = "php"

        if detected_lang and stripped:
            if code_run and code_lang != detected_lang:
                _flush_code_run()
            code_lang = code_lang or detected_lang
            code_run.append(line)
        else:
            _flush_code_run()
            result.append(line)

    _flush_code_run()
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
# Framework detection (reuses deprecations._detect_versions)
# ---------------------------------------------------------------------------

def _get_primary_frameworks(
    repo_root: str,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Detect frameworks and runtimes in the project.

    Returns ``(frameworks, runtimes)`` where each is a list of
    ``(name, version)`` tuples.  Frameworks are capped at 3,
    runtimes at 2.
    """
    from lean_ai.context.deprecations import _detect_versions

    deps = _detect_versions(repo_root)

    frameworks = [(d.name, d.version) for d in deps if d.category == "framework"]
    runtimes = [(d.name, d.version) for d in deps if d.category == "runtime"]

    return frameworks[:3], runtimes[:2]


# ---------------------------------------------------------------------------
# Training cutoff detection
# ---------------------------------------------------------------------------

async def _get_training_cutoff(
    llm_client: LLMClient,
    repo_root: str,
) -> str | None:
    """Ask the LLM for its training data cutoff date.

    Returns a date string like ``"2024-04"`` or ``None`` on failure.
    Caches per model name in ``.lean_ai/model_cutoff.json`` so we only
    ask once per model.
    """
    cache_path = Path(repo_root) / ".lean_ai" / "model_cutoff.json"
    model_name = llm_client.model_name

    # Check cache first
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            if model_name in cache:
                logger.info(
                    "Framework guide: using cached training cutoff "
                    "%s for model %s",
                    cache[model_name], model_name,
                )
                return cache[model_name]
        except Exception:
            pass

    # Ask the LLM
    logger.info(
        "Framework guide: asking %s for training cutoff date", model_name,
    )
    try:
        response = await llm_client.chat_raw(
            messages=[{
                "role": "user",
                "content": (
                    "What is your training data cutoff date? "
                    "Reply with ONLY the date in YYYY-MM format, "
                    "nothing else. Example: 2024-04"
                ),
            }],
            max_tokens=32,
        )
        # Parse — expect something like "2024-04"
        cutoff = response.strip()[:7]
        datetime.strptime(cutoff, "%Y-%m")  # validate format

        # Save to cache
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, str] = {}
        if cache_path.exists():
            try:
                existing = json.loads(
                    cache_path.read_text(encoding="utf-8"),
                )
            except Exception:
                pass
        existing[model_name] = cutoff
        cache_path.write_text(
            json.dumps(existing, indent=2), encoding="utf-8",
        )

        logger.info(
            "Framework guide: model %s reports cutoff %s",
            model_name, cutoff,
        )
        return cutoff
    except Exception as exc:
        logger.info(
            "Framework guide: could not determine cutoff: %s", exc,
        )
        return None


# ---------------------------------------------------------------------------
# Search query generation
# ---------------------------------------------------------------------------

def _build_guide_search_queries(
    frameworks: list[tuple[str, str]],
    runtimes: list[tuple[str, str]],
    cutoff: str | None = None,
) -> list[str]:
    """Generate web search queries for framework architecture and best practices."""
    from lean_ai.context.deprecations import _extract_major_minor

    queries: list[str] = []
    for name, version in frameworks:
        v = _extract_major_minor(version)
        canonical = _canonicalize_name(name)
        label = f"{canonical} {v}" if v else canonical
        queries.append(f"{label} architecture guide request lifecycle")
        queries.append(f"{label} CLI commands scaffolding generators")
        queries.append(
            f"{label} project structure directory conventions",
        )
        queries.append(
            f"{label} upgrade guide migration from previous version",
        )
        queries.append(f"{label} middleware configuration setup")
        queries.append(
            f"{label} testing patterns setup best practices",
        )
        queries.append(
            f"{label} common pitfalls gotchas version specific",
        )
        # When we know the LLM's training cutoff, add a query that
        # specifically targets post-cutoff changes.
        if cutoff:
            queries.append(
                f"{label} changelog breaking changes new features",
            )

    return queries[:16]


# ---------------------------------------------------------------------------
# Project tree (compact, for prompt inclusion)
# ---------------------------------------------------------------------------

def _get_compact_tree(repo_root: str, max_entries: int = 100) -> str:
    """Return a compact file tree of the project for inclusion in the prompt."""
    try:
        from lean_ai.indexer.tree import list_repo_tree

        entries = list_repo_tree(repo_root)
        lines = [e.path for e in entries[:max_entries]]
        return "\n".join(lines)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# LLM system prompt
# ---------------------------------------------------------------------------

def _build_guide_system_prompt(
    frameworks: list[tuple[str, str]],
    runtimes: list[tuple[str, str]],
    cutoff: str | None = None,
) -> str:
    """Build the system prompt that instructs the LLM to generate a guide."""
    from lean_ai.context.deprecations import _extract_major_minor

    fw_list = ", ".join(
        f"{_canonicalize_name(name)} {_extract_major_minor(ver)}"
        if ver else _canonicalize_name(name)
        for name, ver in frameworks
    )
    rt_list = ", ".join(
        f"{_canonicalize_name(name)} {_extract_major_minor(ver)}"
        if ver else _canonicalize_name(name)
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
        "- Naming conventions the framework enforces implicitly\n\n"
        "STRUCTURE RULES:\n"
        "- Each ## heading above MUST appear EXACTLY ONCE. Never "
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
        "has no applicable content, omit the heading entirely.\n"
        "- Number steps sequentially starting from 1 with no gaps.\n"
        "- Maximum 4000 words total.\n\n"
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
        "framework.\n"
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def _extract_urls_from_search(search_text: str, max_urls: int = 5) -> list[str]:
    """Extract URLs from formatted search result text.

    Search results are formatted as ``URL: <url>`` lines by the search
    providers.  Returns unique URLs in order of appearance.
    """
    seen: set[str] = set()
    urls: list[str] = []
    for line in search_text.splitlines():
        if line.startswith("URL: "):
            url = line[5:].strip()
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
                if len(urls) >= max_urls:
                    break
    return urls


def _extract_search_results(
    search_text: str,
) -> list[tuple[str, str, str]]:
    """Extract ``(title, url, snippet)`` tuples from formatted search output.

    Both DuckDuckGo and SearXNG providers format results as::

        Title: <title>
        URL: <url>
        <snippet body>

        ---

    Returns a list of tuples preserving order.
    """
    results: list[tuple[str, str, str]] = []
    blocks = search_text.split("---")
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        title = ""
        url = ""
        snippet_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("Title: "):
                title = line[7:].strip()
            elif line.startswith("URL: "):
                url = line[5:].strip()
            else:
                snippet_lines.append(line)
        if url:
            snippet = " ".join(
                ln.strip() for ln in snippet_lines if ln.strip()
            )
            results.append((title, url, snippet[:300]))
    return results


# Domains that indicate package registries, repository hosts, or Q&A
# sites rather than documentation — deprioritized when selecting the
# best URL from each search category.
_DEPRIORITIZED_DOMAINS = (
    "packagist.org",
    "npmjs.com",
    "pypi.org",
    "hub.docker.com",
    "github.com",
    "stackoverflow.com",
    "stackexchange.com",
)


def _select_one_per_query(
    query_results: list[list[tuple[str, str, str]]],
) -> list[list[str]]:
    """Pick ranked candidate URLs per search query.

    Iterates each query's result list and returns an ordered list of
    candidate URLs for each query.  The first URL is the preferred pick
    (non-deprioritized domain when possible), followed by fallbacks in
    case the primary fetch fails (403, 404, timeout, etc.).

    Candidates are globally deduped — a URL picked as primary for one
    query won't appear as a candidate for another.

    Returns a list of URL lists — one list per query that produced
    results.
    """
    selected: set[str] = set()
    picks: list[list[str]] = []

    for results in query_results:
        # Dedup within this query's results, skip globally selected
        seen: set[str] = set()
        candidates: list[str] = []
        for _title, url, _snippet in results:
            if url not in seen and url not in selected:
                seen.add(url)
                candidates.append(url)

        if not candidates:
            continue

        # Prefer URLs not from deprioritized domains
        preferred = [
            u for u in candidates
            if not any(d in u for d in _DEPRIORITIZED_DOMAINS)
        ]
        deprioritized = [
            u for u in candidates
            if any(d in u for d in _DEPRIORITIZED_DOMAINS)
        ]

        # Ordered: preferred first, then deprioritized as fallbacks
        ranked = preferred + deprioritized
        # Reserve the primary pick globally so other queries don't reuse it
        selected.add(ranked[0])
        picks.append(ranked)

    return picks


async def generate_framework_guide(
    repo_root: str,
    llm_client: LLMClient,
    max_tokens: int = 4096,
) -> str:
    """Detect frameworks, search for best practices, and generate a guide.

    Returns the guide content as a Markdown string, or ``""`` if no
    frameworks are detected or any step fails.
    """
    from lean_ai.config import settings
    from lean_ai.tools.internet import fetch_url, search_internet

    if not settings.enable_framework_guide:
        return ""

    # Step 1: Detect frameworks
    try:
        frameworks, runtimes = _get_primary_frameworks(repo_root)
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

    # Step 1b: Detect training cutoff (cached per model)
    cutoff = await _get_training_cutoff(llm_client, repo_root)

    # Step 2: Get project tree for project-specific guide
    project_tree = _get_compact_tree(repo_root)

    # Step 3: Web search for current best practices (snippets)
    # Sequential — primp/lxml are not thread-safe for concurrent use.
    queries = _build_guide_search_queries(
        frameworks, runtimes, cutoff=cutoff,
    )
    search_parts: list[str] = []
    query_results: list[list[tuple[str, str, str]]] = []

    # Browser providers need more time: browser init + rate-limit delay +
    # navigation + consent handling + potential Bing fallback.
    search_timeout = 90 if settings.search_provider in ("google", "bing") else 15

    logger.info("Framework guide: running %d web searches", len(queries))
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
                extracted = _extract_search_results(result.output)
                logger.info(
                    "Framework guide: search %d/%d %r -> OK (%d chars)",
                    i, len(queries), query, len(result.output),
                )
            else:
                logger.info(
                    "Framework guide: search %d/%d %r -> no results",
                    i, len(queries), query,
                )
        except asyncio.TimeoutError:
            logger.info(
                "Framework guide: search %d/%d %r -> timed out",
                i, len(queries), query,
            )
        except Exception as exc:
            logger.info(
                "Framework guide: search %d/%d %r -> failed: %s",
                i, len(queries), query, exc,
            )
        query_results.append(extracted)

    # Step 3b: Per-category page fetching.
    # Select one URL per search query (category), preferring documentation
    # over package registries and repository hosts.
    fetch_urls = _select_one_per_query(query_results)

    logger.info(
        "Framework guide: selected %d URLs (one per search category)",
        len(fetch_urls),
    )

    page_parts: list[str] = []
    page_chars_budget = 32000  # Total budget for per-category pages
    page_chars_used = 0
    per_page_cap = 5000

    if fetch_urls:
        logger.info(
            "Framework guide: fetching %d category page(s) for full content",
            len(fetch_urls),
        )
    for j, url_candidates in enumerate(fetch_urls, 1):
        if page_chars_used >= page_chars_budget:
            break
        fetched = False
        for attempt, url in enumerate(url_candidates, 1):
            try:
                page_result = await asyncio.wait_for(
                    fetch_url(url, llm_client=None),
                    timeout=20,
                )
                if page_result.success and page_result.output:
                    remaining = page_chars_budget - page_chars_used
                    cap = min(per_page_cap, remaining)
                    text = page_result.output[:cap]
                    page_parts.append(f"=== Page: {url} ===\n{text}")
                    page_chars_used += len(text)
                    if attempt > 1:
                        logger.info(
                            "Framework guide: category %d/%d fetched "
                            "fallback %d (%d chars): %s",
                            j, len(fetch_urls), attempt, len(text), url,
                        )
                    else:
                        logger.info(
                            "Framework guide: fetched page %d/%d "
                            "(%d chars): %s",
                            j, len(fetch_urls), len(text), url,
                        )
                    fetched = True
                    break
                else:
                    logger.info(
                        "Framework guide: category %d page empty: %s",
                        j, url,
                    )
            except asyncio.TimeoutError:
                logger.info(
                    "Framework guide: category %d page timed out: %s",
                    j, url,
                )
            except Exception as exc:
                logger.info(
                    "Framework guide: category %d page failed: %s — %s",
                    j, url, exc,
                )
        if not fetched:
            logger.info(
                "Framework guide: category %d/%d — all %d URL(s) failed",
                j, len(fetch_urls), len(url_candidates),
            )

    # Step 4: Build user message with search results + pages + tree
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
        "\n\n".join(user_parts)
        if user_parts
        else "Generate based on training knowledge."
    )

    total_chars = len(user_content)
    logger.info(
        "Framework guide: generating via LLM "
        "(%d snippets, %d pages, %d-char prompt)",
        len(search_parts), len(page_parts), min(total_chars, 40000),
    )

    # Step 5: LLM generates the guide
    try:
        guide = await llm_client.chat_raw(
            messages=[
                {
                    "role": "system",
                    "content": _build_guide_system_prompt(
                        frameworks, runtimes, cutoff=cutoff,
                    ),
                },
                {"role": "user", "content": user_content[:40000]},
            ],
            max_tokens=max_tokens,
        )
    except Exception as exc:
        logger.warning("Framework guide: LLM generation failed: %s", exc)
        return ""

    if not guide.strip():
        logger.info("Framework guide: LLM returned empty output")
        return ""

    # Step 5a: Repair unfenced code blocks
    guide = _repair_code_blocks(guide)

    # Step 5b: Deduplicate repeated sections
    guide = await _deduplicate_sections(guide, llm_client)

    # Step 5c: Validate file references against project tree
    # (runs before renumber because LLM surgical fixes can change text)
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

    # Step 5d: Renumber steps (runs last since dedup and validation
    # can both remove or rewrite content, introducing gaps)
    guide = _renumber_steps(guide)

    # Step 6: Add header
    fw_label = ", ".join(
        _canonicalize_name(name) for name, _ver in frameworks
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

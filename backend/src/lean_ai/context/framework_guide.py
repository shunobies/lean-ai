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


def _get_project_paths(repo_root: str) -> set[str]:
    """Return a set of all file paths in the project (relative to root)."""
    from lean_ai.indexer.tree import list_repo_tree

    return {e.path for e in list_repo_tree(repo_root)}


def _check_invalid_paths(
    text: str,
    project_paths: set[str],
    project_top_dirs: set[str],
) -> set[str]:
    """Return referenced file paths that don't exist in the project."""
    referenced = _extract_file_paths(text)
    invalid: set[str] = set()
    for path in referenced:
        top_dir = path.split("/")[0]
        if top_dir in project_top_dirs and path not in project_paths:
            invalid.add(path)
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
        label = f"{name} {v}" if v else name
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
        f"{name} {_extract_major_minor(ver)}" if ver else name
        for name, ver in frameworks
    )
    rt_list = ", ".join(
        f"{name} {_extract_major_minor(ver)}" if ver else name
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
        "RULES:\n"
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
        "files)\n"
        "- Reference actual class names and method names from the "
        "framework\n"
        "- Format the output as clean Markdown with ## headings\n"
        "- Maximum 4000 words total\n"
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
    all_urls: list[str] = []

    logger.info("Framework guide: running %d web searches", len(queries))
    for i, query in enumerate(queries, 1):
        try:
            result = await asyncio.wait_for(
                search_internet(query, llm_client=None),
                timeout=15,
            )
            if result.success and result.output:
                search_parts.append(
                    f"=== Search: {query} ===\n{result.output}"
                )
                all_urls.extend(_extract_urls_from_search(result.output))
                logger.info(
                    "Framework guide: search %d/%d OK (%d chars)",
                    i, len(queries), len(result.output),
                )
            else:
                logger.info(
                    "Framework guide: search %d/%d returned no results",
                    i, len(queries),
                )
        except asyncio.TimeoutError:
            logger.info(
                "Framework guide: search %d/%d timed out", i, len(queries),
            )
        except Exception as exc:
            logger.info(
                "Framework guide: search %d/%d failed: %s",
                i, len(queries), exc,
            )

    # Step 3b: Fetch full page content from top search result URLs.
    # DuckDuckGo only returns ~100-200 char snippets per result.
    # Fetching the actual pages gives the LLM real documentation to
    # work from, which dramatically improves version accuracy.
    seen_urls: set[str] = set()
    unique_urls = []
    for url in all_urls:
        if url not in seen_urls:
            seen_urls.add(url)
            unique_urls.append(url)
    fetch_urls = unique_urls[:6]  # Top 6 unique URLs

    page_parts: list[str] = []
    page_chars_budget = 24000  # Total budget for fetched pages
    page_chars_used = 0
    per_page_cap = 5000

    if fetch_urls:
        logger.info(
            "Framework guide: fetching %d page(s) for full content",
            len(fetch_urls),
        )
    for j, url in enumerate(fetch_urls, 1):
        if page_chars_used >= page_chars_budget:
            break
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
                logger.info(
                    "Framework guide: fetched page %d/%d (%d chars): %s",
                    j, len(fetch_urls), len(text), url,
                )
            else:
                logger.info(
                    "Framework guide: page %d/%d empty: %s",
                    j, len(fetch_urls), url,
                )
        except asyncio.TimeoutError:
            logger.info(
                "Framework guide: page %d/%d timed out: %s",
                j, len(fetch_urls), url,
            )
        except Exception as exc:
            logger.info(
                "Framework guide: page %d/%d failed: %s — %s",
                j, len(fetch_urls), url, exc,
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

    # Step 5b: Validate file references against project tree
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

    # Step 6: Add header
    fw_label = ", ".join(name for name, _ver in frameworks)
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

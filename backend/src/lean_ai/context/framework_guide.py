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

if TYPE_CHECKING:
    from lean_ai.llm.client import LLMClient

logger = logging.getLogger(__name__)


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
# Search query generation
# ---------------------------------------------------------------------------

def _build_guide_search_queries(
    frameworks: list[tuple[str, str]],
    runtimes: list[tuple[str, str]],
) -> list[str]:
    """Generate web search queries for framework architecture and best practices."""
    from lean_ai.context.deprecations import _extract_major_minor

    queries: list[str] = []
    for name, version in frameworks:
        v = _extract_major_minor(version)
        label = f"{name} {v}" if v else name
        queries.append(f"{label} architecture guide request lifecycle MVC")
        queries.append(f"{label} CLI commands common artisan make")
        queries.append(f"{label} project structure conventions best practices")

    return queries[:6]


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

    return (
        "Generate a framework guide for a development project.\n\n"
        f"DETECTED FRAMEWORKS: {fw_list}\n"
        f"DETECTED RUNTIMES: {rt_list}\n\n"
        "The guide must be tailored to THIS project's specific framework "
        "and version. Use the web search results and the project file tree "
        "to produce a guide that covers framework concepts and this "
        "project's actual structure.\n\n"
        "Use the PROJECT FILE TREE to identify which components this "
        "project actually uses. Tailor examples to match the project's "
        "real structure when possible (e.g., if the tree shows "
        "app/Models/Customer.php, use Customer as the example model).\n\n"
        "REQUIRED SECTIONS (use exactly these ## headings):\n\n"
        "## Framework Architecture\n"
        "Explain the framework's architectural pattern (MVC, MVVM, etc.) "
        "and the request lifecycle for THIS version. Describe how a "
        "request flows from entry point to response as a numbered "
        "sequence — which files and classes are involved at each stage. "
        "Only reference files and classes that exist in the detected "
        "version.\n\n"
        "## Component Relationships\n"
        "For each relationship below, show a SHORT code snippet "
        "demonstrating the exact connection point between the two "
        "components:\n\n"
        "1. **Migration to Model**: Show a migration column definition "
        "(e.g., a foreign key column) alongside the corresponding model "
        "relationship method (e.g., belongsTo, hasMany). Explain the "
        "naming convention that makes the implicit binding work.\n\n"
        "2. **Route to Controller**: Show a route definition and the "
        "controller method it maps to. Explain how the framework "
        "resolves the controller class and method name.\n\n"
        "3. **Controller to View**: Show a controller method returning "
        "a view with data, and how the view accesses that data. Include "
        "both template and API resource patterns if applicable.\n\n"
        "4. **Middleware to Request Pipeline**: Show how middleware is "
        "registered and which file controls the middleware stack for "
        "THIS version. Do NOT reference middleware configuration files "
        "from older versions.\n\n"
        "5. **Service Provider to Container**: Show a service provider "
        "binding and how a controller or service resolves it via "
        "dependency injection.\n\n"
        "6. **Form Request to Controller**: Show a form request class "
        "with validation rules and how it auto-validates when "
        "type-hinted in a controller method.\n\n"
        "Do NOT draw ASCII dependency diagrams. Instead, describe the "
        "request flow as a numbered prose sequence.\n\n"
        "## Common CLI Commands\n"
        "List the essential CLI commands for this EXACT version. Group "
        "by purpose:\n"
        "- Project setup and scaffolding\n"
        "- Database (migrations, seeding, schema inspection)\n"
        "- Development server and tooling\n"
        "- Testing and debugging\n"
        "- Cache, config, and maintenance\n\n"
        "For each command show the EXACT syntax including all flags.\n\n"
        "CRITICAL: Only include commands and flags you are CERTAIN exist "
        "in the detected version. Common mistakes to avoid:\n"
        "- Do not mix up flags between different CLI commands (e.g., "
        "the -m flag on make:model creates a migration, but -m does NOT "
        "exist on make:controller)\n"
        "- Do not invent commands that do not exist\n"
        "- If the web search results include CLI documentation, use "
        "those exact command signatures\n\n"
        "## File Organization Conventions\n"
        "Describe the standard directory structure for THIS version and "
        "where each type of component lives. Map directories to "
        "framework concepts. Only list directories that exist in this "
        "version — do not carry over directory structures from older "
        "versions.\n\n"
        "## Adding a New Feature\n"
        "Provide a step-by-step workflow for adding a typical new "
        "feature (e.g., a new CRUD resource). IMPORTANT:\n"
        "- Do NOT create the same artifact twice (e.g., if a command "
        "creates both a model and migration, do not also run a separate "
        "migration command)\n"
        "- Show the SINGLE optimal command that creates the most "
        "artifacts at once, then list what it generated\n"
        "- For each step show the exact file(s) created and what to "
        "add to them\n"
        "- Show how each new file connects back to previously created "
        "components\n\n"
        "## Common Patterns and Pitfalls\n"
        "List 5-10 patterns that are easy to get wrong in the DETECTED "
        "VERSION. Each pattern MUST be specific to this version — "
        "generic advice that applies to all versions is not useful. "
        "Focus on:\n"
        "- What changed in this version vs. the previous major version\n"
        "- Deprecated features that developers might still try to use\n"
        "- New recommended patterns that replace old ones\n"
        "- Naming conventions the framework enforces implicitly\n\n"
        "RULES:\n"
        f"- ONLY cover the detected frameworks: {fw_list}\n"
        "- Web search results are the SOURCE OF TRUTH for "
        "version-specific details. If search results contradict your "
        "training knowledge, ALWAYS prefer the search results. "
        "Frameworks change significantly between major versions — do "
        "not assume features from older versions still exist.\n"
        "- NEVER reference files, classes, or commands that do not "
        "exist in the detected version. If unsure whether something "
        "exists in this version, omit it rather than guess.\n"
        "- Use concrete code examples (short snippets, not full files)\n"
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

    # Step 2: Get project tree for project-specific guide
    project_tree = _get_compact_tree(repo_root)

    # Step 3: Web search for current best practices (snippets)
    # Sequential — primp/lxml are not thread-safe for concurrent use.
    queries = _build_guide_search_queries(frameworks, runtimes)
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
    fetch_urls = unique_urls[:4]  # Top 4 unique URLs

    page_parts: list[str] = []
    page_chars_budget = 16000  # Total budget for fetched pages
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
                    "content": _build_guide_system_prompt(frameworks, runtimes),
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

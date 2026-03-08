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
        "Generate a comprehensive framework guide for a development project.\n\n"
        f"DETECTED FRAMEWORKS: {fw_list}\n"
        f"DETECTED RUNTIMES: {rt_list}\n\n"
        "The guide must be tailored to THIS project's specific framework and "
        "version. Use the web search results and the project file tree to "
        "produce a guide that is both general (framework concepts) and "
        "specific (this project's structure).\n\n"
        "REQUIRED SECTIONS (use exactly these ## headings):\n\n"
        "## Framework Architecture\n"
        "Explain the framework's architectural pattern (MVC, MVVM, etc.) and "
        "request lifecycle. Describe how a request flows from entry point to "
        "response — which components are involved at each stage.\n\n"
        "## Component Relationships\n"
        "Explain how the framework's major components relate to each other:\n"
        "- How models relate to migrations and database schema\n"
        "- How controllers/handlers connect to routes\n"
        "- How views/templates consume data from controllers\n"
        "- How middleware/filters intercept the request pipeline\n"
        "- How configuration, service providers, and dependency injection work\n"
        "- How form validation, request objects, and API resources fit in\n"
        "Draw the dependency graph in words: which components depend on which.\n\n"
        "## Common CLI Commands\n"
        "List the essential CLI commands for development. Group by purpose:\n"
        "- Project setup and scaffolding (create models, controllers, etc.)\n"
        "- Database (migrations, seeding, schema inspection)\n"
        "- Development server and tooling\n"
        "- Testing and debugging\n"
        "- Cache, config, and maintenance\n"
        "For each command, show the exact syntax and a one-line description.\n\n"
        "## File Organization Conventions\n"
        "Describe the standard directory structure and where each type of "
        "component lives. Map the directories to the framework concepts.\n\n"
        "## Adding a New Feature\n"
        "Provide a concrete step-by-step workflow for adding a typical new "
        "feature (e.g., a new CRUD resource). For each step:\n"
        "- Which file(s) to create or modify\n"
        "- Which CLI command generates them (if applicable)\n"
        "- What the file should contain\n"
        "- How it connects to the other components created in previous steps\n\n"
        "## Common Patterns and Pitfalls\n"
        "List 5-10 patterns that are easy to get wrong, with the correct "
        "approach. Include naming conventions, relationship definitions, "
        "validation rules, and common gotchas for the specific version.\n\n"
        "RULES:\n"
        f"- ONLY cover the detected frameworks: {fw_list}\n"
        "- Be SPECIFIC to the detected version — do not mix advice from "
        "different versions\n"
        "- Use concrete code examples (short snippets, not full files)\n"
        "- Reference actual class names and method names from the framework\n"
        "- Format the output as clean Markdown with ## headings\n"
        "- Maximum 3000 words total\n"
        "- If the search results lack information for a section, use your "
        "training knowledge for that framework version\n"
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

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
    from lean_ai.tools.internet import search_internet

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

    # Step 3: Web search for current best practices
    # Sequential — primp/lxml are not thread-safe for concurrent use.
    queries = _build_guide_search_queries(frameworks, runtimes)
    search_parts: list[str] = []

    for query in queries:
        try:
            result = await asyncio.wait_for(
                search_internet(query, llm_client=None),
                timeout=15,
            )
            if result.success and result.output:
                search_parts.append(f"=== Search: {query} ===\n{result.output}")
        except asyncio.TimeoutError:
            logger.debug("Framework guide search timed out for '%s'", query)
        except Exception as exc:
            logger.debug("Framework guide search failed for '%s': %s", query, exc)

    # Step 4: Build user message with search results + project tree
    user_parts: list[str] = []
    if search_parts:
        user_parts.append("WEB SEARCH RESULTS:\n\n" + "\n\n".join(search_parts))
    if project_tree:
        user_parts.append(f"PROJECT FILE TREE:\n{project_tree}")

    user_content = (
        "\n\n".join(user_parts)
        if user_parts
        else "Generate based on training knowledge."
    )

    # Step 5: LLM generates the guide
    try:
        guide = await llm_client.chat_raw(
            messages=[
                {
                    "role": "system",
                    "content": _build_guide_system_prompt(frameworks, runtimes),
                },
                {"role": "user", "content": user_content[:20000]},
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

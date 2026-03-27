"""Framework detection, training cutoff, search query generation, and project tree."""

from __future__ import annotations

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
    # Tooling / integrations
    "livewire/livewire": "Laravel Livewire",
    "inertiajs/inertia-laravel": "Inertia.js",
    "@inertiajs/vue3": "Inertia.js (Vue)",
    "@inertiajs/react": "Inertia.js (React)",
    "@inertiajs/svelte": "Inertia.js (Svelte)",
    "tailwindcss": "Tailwind CSS",
    "alpinejs": "Alpine.js",
    "htmx.org": "htmx",
    "postcss": "PostCSS",
    "pestphp/pest": "Pest",
    "@playwright/test": "Playwright",
}


def canonicalize_name(raw_name: str) -> str:
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
# Framework detection (reuses deprecations._detect_versions)
# ---------------------------------------------------------------------------

def get_primary_frameworks(
    repo_root: str,
) -> tuple[
    list[tuple[str, str]],
    list[tuple[str, str]],
    list[tuple[str, str]],
]:
    """Detect frameworks, runtimes, and tooling in the project.

    Returns ``(frameworks, runtimes, tooling)`` where each is a list of
    ``(name, version)`` tuples.  Capped at 3, 2, and 5 respectively.
    """
    from lean_ai.context.deprecations import _detect_versions

    deps = _detect_versions(repo_root)

    frameworks = [(d.name, d.version) for d in deps if d.category == "framework"]
    runtimes = [(d.name, d.version) for d in deps if d.category == "runtime"]
    tooling = [(d.name, d.version) for d in deps if d.category == "tooling"]

    return frameworks[:3], runtimes[:2], tooling[:5]


# ---------------------------------------------------------------------------
# Training cutoff detection
# ---------------------------------------------------------------------------

async def get_training_cutoff(
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

def build_guide_search_queries(
    frameworks: list[tuple[str, str]],
    runtimes: list[tuple[str, str]],
    cutoff: str | None = None,
) -> list[str]:
    """Generate web search queries for framework architecture and best practices."""
    from lean_ai.context.deprecations import _extract_major_minor

    queries: list[str] = []
    for name, version in frameworks:
        v = _extract_major_minor(version)
        canonical = canonicalize_name(name)
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


async def build_guide_search_queries_llm(
    frameworks: list[tuple[str, str]],
    runtimes: list[tuple[str, str]],
    llm_client: LLMClient,
    cutoff: str | None = None,
    max_queries: int = 16,
) -> list[str] | None:
    """Generate varied, natural search queries using the LLM.

    Asks the LLM to produce search queries that sound like a real
    developer typing into a search engine — varied phrasing, mix of
    questions and keyword phrases, version-specific.

    Returns ``None`` on any failure so the caller can fall back to
    the static :func:`build_guide_search_queries`.
    """
    from lean_ai.context.deprecations import _extract_major_minor

    if not frameworks:
        return None

    fw_lines: list[str] = []
    for name, version in frameworks:
        v = _extract_major_minor(version)
        canonical = canonicalize_name(name)
        label = f"{canonical} {v}" if v else canonical
        fw_lines.append(label)

    rt_lines: list[str] = []
    for name, version in runtimes:
        v = _extract_major_minor(version)
        canonical = canonicalize_name(name)
        label = f"{canonical} {v}" if v else canonical
        rt_lines.append(label)

    fw_text = ", ".join(fw_lines)
    rt_text = ", ".join(rt_lines) if rt_lines else "not detected"

    topics = (
        "1. Architecture pattern and request lifecycle\n"
        "2. CLI tools and code generation commands\n"
        "3. Directory structure and file organization\n"
        "4. Upgrade/migration guide from previous version\n"
        "5. Middleware and request pipeline\n"
        "6. Testing setup and patterns\n"
        "7. Common mistakes and version-specific gotchas"
    )
    if cutoff:
        topics += (
            f"\n8. Breaking changes and new features since {cutoff}"
        )

    prompt = (
        f"Generate {max_queries} web search queries to research "
        f"these frameworks: {fw_text} (runtime: {rt_text}).\n\n"
        "Each query should sound like a real developer typing into "
        "a search engine. Vary the phrasing — some as questions, "
        "some as keyword phrases, some as natural sentences. "
        "Include the framework version number in most queries.\n\n"
        f"Cover these topics:\n{topics}\n\n"
        "IMPORTANT:\n"
        "- Do NOT include framework-specific CLI command names "
        "(no 'artisan', 'manage.py', 'rails', etc.)\n"
        "- Do NOT repeat the same query structure — each query "
        "should look different\n"
        "- Output ONLY the queries, one per line, no numbers or "
        "bullets\n"
    )

    try:
        response = await llm_client.chat_raw(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=512,
        )

        queries = [
            line.strip().strip('"').strip("'")
            for line in response.strip().split("\n")
            if line.strip() and len(line.strip()) > 10
        ]

        if len(queries) < 3:
            logger.info(
                "Framework guide: LLM query generation returned "
                "too few queries (%d), falling back to static",
                len(queries),
            )
            return None

        logger.info(
            "Framework guide: LLM generated %d search queries",
            len(queries),
        )
        return queries[:max_queries]

    except Exception as exc:
        logger.info(
            "Framework guide: LLM query generation failed, "
            "falling back to static: %s",
            exc,
        )
        return None


async def build_gap_fill_queries_llm(
    frameworks: list[tuple[str, str]],
    runtimes: list[tuple[str, str]],
    gaps: list[tuple[str, str]],
    llm_client: LLMClient,
    max_queries: int = 4,
) -> list[str] | None:
    """Generate targeted search queries for empty guide subsections.

    Takes a list of ``(parent_heading, sub_heading)`` gaps and asks the
    LLM to produce focused web search queries that will find content
    specifically for those missing subsections.

    Returns ``None`` on any failure so the caller can skip gap-filling.
    """
    from lean_ai.context.deprecations import _extract_major_minor

    if not frameworks or not gaps:
        return None

    fw_lines: list[str] = []
    for name, version in frameworks:
        v = _extract_major_minor(version)
        canonical = canonicalize_name(name)
        label = f"{canonical} {v}" if v else canonical
        fw_lines.append(label)

    fw_text = ", ".join(fw_lines)

    gap_descriptions = "\n".join(
        f"- {sub} (under {parent})" for parent, sub in gaps
    )

    prompt = (
        f"Generate {max_queries} web search queries to find information "
        f"about these specific missing topics for {fw_text}:\n\n"
        f"{gap_descriptions}\n\n"
        "Each query should sound like a real developer typing into "
        "a search engine. Include the framework name and version.\n\n"
        "IMPORTANT:\n"
        "- Focus ONLY on the missing topics listed above\n"
        "- Do NOT include framework-specific CLI command names "
        "(no 'artisan', 'manage.py', 'rails', etc.)\n"
        "- Each query should target a different aspect of the "
        "missing content\n"
        "- Output ONLY the queries, one per line, no numbers or "
        "bullets\n"
    )

    try:
        response = await llm_client.chat_raw(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=256,
        )

        queries = [
            line.strip().strip('"').strip("'")
            for line in response.strip().split("\n")
            if line.strip() and len(line.strip()) > 10
        ]

        if not queries:
            logger.info(
                "Framework guide: gap-fill query generation returned "
                "no usable queries",
            )
            return None

        logger.info(
            "Framework guide: LLM generated %d gap-fill queries for %d gaps",
            len(queries), len(gaps),
        )
        return queries[:max_queries]

    except Exception as exc:
        logger.info(
            "Framework guide: gap-fill query generation failed: %s",
            exc,
        )
        return None


async def build_section_search_queries_llm(
    frameworks: list[tuple[str, str]],
    runtimes: list[tuple[str, str]],
    section_heading: str,
    search_topics: list[str],
    llm_client: LLMClient,
    cutoff: str | None = None,
    max_queries: int = 4,
    tooling: list[tuple[str, str]] | None = None,
) -> list[str]:
    """Generate search queries scoped to a single guide section.

    Returns LLM-generated queries, or falls back to static queries
    built from the section's ``search_topics`` combined with the
    framework label.  Always returns a non-empty list.

    When *tooling* is provided, tooling labels are included in the
    query context so the LLM generates queries covering build tools
    and CSS frameworks alongside the primary framework.
    """
    from lean_ai.context.deprecations import _extract_major_minor

    fw_lines: list[str] = []
    for name, version in frameworks:
        v = _extract_major_minor(version)
        canonical = canonicalize_name(name)
        label = f"{canonical} {v}" if v else canonical
        fw_lines.append(label)

    rt_lines: list[str] = []
    for name, version in runtimes:
        v = _extract_major_minor(version)
        canonical = canonicalize_name(name)
        label = f"{canonical} {v}" if v else canonical
        rt_lines.append(label)

    tool_lines: list[str] = []
    if tooling:
        for name, version in tooling:
            v = _extract_major_minor(version)
            canonical = canonicalize_name(name)
            label = f"{canonical} {v}" if v else canonical
            tool_lines.append(label)

    fw_text = ", ".join(fw_lines)
    rt_text = ", ".join(rt_lines) if rt_lines else "not detected"
    tool_text = ", ".join(tool_lines) if tool_lines else "none"
    topics_text = "\n".join(f"- {t}" for t in search_topics)

    stack_desc = f"{fw_text} (runtime: {rt_text}"
    if tool_lines:
        stack_desc += f", tooling: {tool_text}"
    stack_desc += ")"

    prompt = (
        f"Generate {max_queries} web search queries to research "
        f"{stack_desc} specifically about: "
        f"{section_heading}\n\n"
        f"Topics to cover:\n{topics_text}\n\n"
        "Each query should sound like a real developer typing into "
        "a search engine. Vary the phrasing — some as questions, "
        "some as keyword phrases. Include the framework/tool version "
        "number in most queries.\n\n"
        "IMPORTANT:\n"
        "- Do NOT include framework-specific CLI command names "
        "(no 'artisan', 'manage.py', 'rails', etc.)\n"
        "- Each query should target a different aspect\n"
        "- Output ONLY the queries, one per line, no numbers or "
        "bullets\n"
    )

    try:
        response = await llm_client.chat_raw(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=256,
        )

        queries = [
            line.strip().strip('"').strip("'")
            for line in response.strip().split("\n")
            if line.strip() and len(line.strip()) > 10
        ]

        if len(queries) >= 2:
            logger.info(
                "Framework guide: LLM generated %d section queries "
                "for %s",
                len(queries), section_heading,
            )
            return queries[:max_queries]

        logger.info(
            "Framework guide: section query LLM returned too few "
            "(%d), using static fallback for %s",
            len(queries), section_heading,
        )
    except Exception as exc:
        logger.info(
            "Framework guide: section query LLM failed for %s, "
            "using static fallback: %s",
            section_heading, exc,
        )

    # Static fallback: combine each topic with the framework label
    static = [f"{fw_text} {topic}" for topic in search_topics]
    if tooling:
        for name, version in tooling:
            v = _extract_major_minor(version)
            canonical = canonicalize_name(name)
            label = f"{canonical} {v}" if v else canonical
            static.append(f"{label} configuration setup guide")
    if cutoff:
        static.append(
            f"{fw_text} changelog breaking changes since {cutoff}"
        )
    return static[:max_queries]


# ---------------------------------------------------------------------------
# Project tree (compact, for prompt inclusion)
# ---------------------------------------------------------------------------

def get_compact_tree(repo_root: str, max_entries: int = 100) -> str:
    """Return a compact file tree of the project for inclusion in the prompt."""
    try:
        from lean_ai.indexer.tree import list_repo_tree

        entries = list_repo_tree(repo_root)
        lines = [e.path for e in entries[:max_entries]]
        return "\n".join(lines)
    except Exception:
        return ""

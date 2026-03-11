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

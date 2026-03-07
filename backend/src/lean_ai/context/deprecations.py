"""Deprecation lookup — detect framework versions, search for deprecations,
and produce a ``## Deprecation Warnings`` section for project_context.md.

Runs as a post-generation append step.  Gracefully returns ``""`` on any
failure so it never blocks context generation.

No regex — version parsing uses simple string operations, json, and tomllib.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import tomllib

if TYPE_CHECKING:
    from lean_ai.llm.client import LLMClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class DetectedDependency:
    """A language, runtime, or framework with its detected version."""

    name: str       # e.g. "Python", "Laravel", "React"
    version: str    # e.g. ">=3.12", "^18.2.0"
    category: str   # "runtime" | "framework" | "library"


# ---------------------------------------------------------------------------
# Well-known framework names (used to promote category to "framework")
# ---------------------------------------------------------------------------

_KNOWN_FRAMEWORKS: frozenset[str] = frozenset({
    # Python
    "django", "flask", "fastapi", "starlette", "tornado", "celery",
    "sqlalchemy", "pydantic",
    # JavaScript / TypeScript
    "react", "next", "vue", "nuxt", "angular", "svelte", "express",
    "nestjs", "vite",
    # PHP
    "laravel/framework", "symfony/framework-bundle", "symfony/symfony",
    "cakephp/cakephp",
    # Ruby
    "rails", "sinatra",
    # Go
    "gin-gonic/gin", "labstack/echo", "gofiber/fiber",
    # Java
    "spring-boot", "spring-boot-starter", "spring-boot-starter-web",
    # Rust
    "actix-web", "axum", "rocket",
    # C#
    "microsoft.aspnetcore",
})


def _categorize(name: str) -> str:
    """Determine if a dependency is a framework or library."""
    lower = name.lower().replace("_", "-")
    for fw in _KNOWN_FRAMEWORKS:
        if lower == fw or lower.startswith(fw):
            return "framework"
    return "library"


# ---------------------------------------------------------------------------
# Version string helpers
# ---------------------------------------------------------------------------

def _extract_major_minor(version_str: str) -> str:
    """Extract a clean major.minor version from version specifiers.

    ``">=3.12,<4"`` → ``"3.12"``
    ``"~> 7.1"`` → ``"7.1"``
    ``"^18.2.0"`` → ``"18.2"``
    ``"==4.2.1"`` → ``"4.2"``

    No regex — scans characters directly.
    """
    # Strip common prefixes
    cleaned = version_str.lstrip(">=<~^! ")

    # Take first version number (stop at comma or non-version char)
    parts: list[str] = []
    for ch in cleaned:
        if ch.isdigit() or ch == ".":
            parts.append(ch)
        elif parts:
            break
    version = "".join(parts).strip(".")

    # Return up to major.minor
    segments = version.split(".")
    if len(segments) >= 2:
        return f"{segments[0]}.{segments[1]}"
    return version if version else ""


def _parse_pep508(dep_str: str) -> tuple[str, str]:
    """Parse a PEP 508 dependency string.

    ``"django>=4.2,<5.0"`` → ``("django", ">=4.2,<5.0")``
    ``"requests"`` → ``("requests", "")``

    No regex — scans for first version specifier character.
    """
    # Strip extras like [dev]
    base = dep_str.split(";")[0].strip()
    bracket = base.find("[")
    if bracket > 0:
        base = base[:bracket].rstrip() + base[base.find("]") + 1:].lstrip()

    for i, ch in enumerate(base):
        if ch in ">=<!~":
            return base[:i].strip().lower(), base[i:].strip()
    return base.strip().lower(), ""


def _parse_requirement_line(line: str) -> tuple[str, str]:
    """Parse a requirements.txt line.

    ``"django==4.2.1"`` → ``("django", "==4.2.1")``

    No regex — splits on known separators.
    """
    for sep in ["==", ">=", "<=", "~=", "!="]:
        if sep in line:
            parts = line.split(sep, 1)
            return parts[0].strip().lower(), sep + parts[1].strip()
    return line.strip().lower(), ""


# ---------------------------------------------------------------------------
# Per-ecosystem version detectors
# ---------------------------------------------------------------------------

def _detect_python_versions(root: Path) -> list[DetectedDependency]:
    deps: list[DetectedDependency] = []

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)
            requires_python = data.get("project", {}).get("requires-python", "")
            if requires_python:
                deps.append(DetectedDependency("Python", requires_python, "runtime"))
            for dep_str in data.get("project", {}).get("dependencies", []):
                name, version = _parse_pep508(dep_str)
                if name and version:
                    deps.append(DetectedDependency(name, version, _categorize(name)))
        except Exception as exc:
            logger.debug("Failed to parse pyproject.toml: %s", exc)

    # Fallback to requirements.txt if pyproject didn't yield deps
    if not deps:
        req_txt = root / "requirements.txt"
        if req_txt.is_file():
            try:
                for line in req_txt.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("-"):
                        continue
                    name, version = _parse_requirement_line(line)
                    if name and version:
                        deps.append(DetectedDependency(name, version, _categorize(name)))
            except Exception as exc:
                logger.debug("Failed to parse requirements.txt: %s", exc)

    return deps


def _detect_node_versions(root: Path) -> list[DetectedDependency]:
    deps: list[DetectedDependency] = []
    pkg = root / "package.json"
    if not pkg.is_file():
        return deps

    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("Failed to parse package.json: %s", exc)
        return deps

    # Node.js runtime version
    node_version = data.get("engines", {}).get("node", "")
    if node_version:
        deps.append(DetectedDependency("Node.js", node_version, "runtime"))

    # Dependencies
    for section in ("dependencies", "devDependencies"):
        for name, version in data.get(section, {}).items():
            deps.append(DetectedDependency(name, version, _categorize(name)))

    return deps


def _detect_php_versions(root: Path) -> list[DetectedDependency]:
    deps: list[DetectedDependency] = []
    composer = root / "composer.json"
    if not composer.is_file():
        return deps

    try:
        data = json.loads(composer.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("Failed to parse composer.json: %s", exc)
        return deps

    require = data.get("require", {})
    for name, version in require.items():
        if name == "php":
            deps.append(DetectedDependency("PHP", version, "runtime"))
        else:
            deps.append(DetectedDependency(name, version, _categorize(name)))

    return deps


def _detect_go_versions(root: Path) -> list[DetectedDependency]:
    deps: list[DetectedDependency] = []
    gomod = root / "go.mod"
    if not gomod.is_file():
        return deps

    try:
        lines = gomod.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        logger.debug("Failed to read go.mod: %s", exc)
        return deps

    in_require = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("go "):
            version = stripped[3:].strip()
            deps.append(DetectedDependency("Go", version, "runtime"))
        elif stripped == "require (":
            in_require = True
        elif stripped == ")" and in_require:
            in_require = False
        elif in_require and stripped:
            parts = stripped.split()
            if len(parts) >= 2:
                deps.append(DetectedDependency(parts[0], parts[1], _categorize(parts[0])))

    return deps


def _detect_ruby_versions(root: Path) -> list[DetectedDependency]:
    deps: list[DetectedDependency] = []
    gemfile = root / "Gemfile"
    if not gemfile.is_file():
        return deps

    try:
        lines = gemfile.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        logger.debug("Failed to read Gemfile: %s", exc)
        return deps

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            continue

        # ruby "3.2.0"
        if stripped.startswith("ruby "):
            version = stripped[5:].strip().strip("'\"")
            if version:
                deps.append(DetectedDependency("Ruby", version, "runtime"))

        # gem 'rails', '~> 7.1'
        if stripped.startswith("gem "):
            parts = stripped[4:].split(",")
            if parts:
                name = parts[0].strip().strip("'\"")
                version = parts[1].strip().strip("'\"") if len(parts) >= 2 else ""
                if name:
                    deps.append(DetectedDependency(name, version, _categorize(name)))

    return deps


def _detect_java_versions(root: Path) -> list[DetectedDependency]:
    deps: list[DetectedDependency] = []

    # pom.xml — simple string scanning for key elements
    pom = root / "pom.xml"
    if pom.is_file():
        try:
            content = pom.read_text(encoding="utf-8")
            # Java version: <java.version>17</java.version>
            tag = "<java.version>"
            end_tag = "</java.version>"
            idx = content.find(tag)
            if idx >= 0:
                end = content.find(end_tag, idx)
                if end > idx:
                    version = content[idx + len(tag):end].strip()
                    deps.append(DetectedDependency("Java", version, "runtime"))

            # Spring Boot parent version
            parent_start = content.find("<parent>")
            parent_end = content.find("</parent>")
            if parent_start >= 0 and parent_end > parent_start:
                parent = content[parent_start:parent_end]
                if "spring-boot" in parent.lower():
                    v_tag = "<version>"
                    v_end = "</version>"
                    vi = parent.find(v_tag)
                    if vi >= 0:
                        ve = parent.find(v_end, vi)
                        if ve > vi:
                            version = parent[vi + len(v_tag):ve].strip()
                            deps.append(DetectedDependency(
                                "Spring Boot", version, "framework",
                            ))
        except Exception as exc:
            logger.debug("Failed to parse pom.xml: %s", exc)

    # build.gradle — look for sourceCompatibility and spring boot plugin
    gradle = root / "build.gradle"
    if gradle.is_file() and not deps:
        try:
            content = gradle.read_text(encoding="utf-8")
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("sourceCompatibility"):
                    # sourceCompatibility = '17'
                    parts = stripped.split("=", 1)
                    if len(parts) == 2:
                        version = parts[1].strip().strip("'\"")
                        deps.append(DetectedDependency("Java", version, "runtime"))
                if "spring-boot" in stripped and "version" in stripped:
                    # id 'org.springframework.boot' version '3.2.0'
                    for token in stripped.split():
                        cleaned = token.strip("'\"")
                        if cleaned and cleaned[0].isdigit():
                            deps.append(DetectedDependency(
                                "Spring Boot", cleaned, "framework",
                            ))
                            break
        except Exception as exc:
            logger.debug("Failed to parse build.gradle: %s", exc)

    return deps


def _detect_rust_versions(root: Path) -> list[DetectedDependency]:
    deps: list[DetectedDependency] = []
    cargo = root / "Cargo.toml"
    if not cargo.is_file():
        return deps

    try:
        with open(cargo, "rb") as f:
            data = tomllib.load(f)
    except Exception as exc:
        logger.debug("Failed to parse Cargo.toml: %s", exc)
        return deps

    # Rust edition
    edition = data.get("package", {}).get("edition", "")
    if edition:
        deps.append(DetectedDependency("Rust", f"edition {edition}", "runtime"))

    # Dependencies
    for name, spec in data.get("dependencies", {}).items():
        if isinstance(spec, str):
            deps.append(DetectedDependency(name, spec, _categorize(name)))
        elif isinstance(spec, dict):
            version = spec.get("version", "")
            if version:
                deps.append(DetectedDependency(name, version, _categorize(name)))

    return deps


def _detect_csharp_versions(root: Path) -> list[DetectedDependency]:
    deps: list[DetectedDependency] = []

    # Find first .csproj file
    csproj_files = list(root.glob("*.csproj"))
    if not csproj_files:
        # Check one level deep
        csproj_files = list(root.glob("*/*.csproj"))
    if not csproj_files:
        return deps

    try:
        content = csproj_files[0].read_text(encoding="utf-8")
    except Exception as exc:
        logger.debug("Failed to read .csproj: %s", exc)
        return deps

    # <TargetFramework>net8.0</TargetFramework>
    tag = "<TargetFramework>"
    end_tag = "</TargetFramework>"
    idx = content.find(tag)
    if idx >= 0:
        end = content.find(end_tag, idx)
        if end > idx:
            framework = content[idx + len(tag):end].strip()
            deps.append(DetectedDependency(".NET", framework, "runtime"))

    # <PackageReference Include="name" Version="version" />
    search = '<PackageReference Include="'
    pos = 0
    while True:
        idx = content.find(search, pos)
        if idx < 0:
            break
        name_start = idx + len(search)
        name_end = content.find('"', name_start)
        if name_end < 0:
            break
        name = content[name_start:name_end]

        version_marker = 'Version="'
        vi = content.find(version_marker, name_end)
        if vi >= 0 and vi < name_end + 200:
            ve = content.find('"', vi + len(version_marker))
            if ve > vi:
                version = content[vi + len(version_marker):ve]
                deps.append(DetectedDependency(name, version, _categorize(name)))

        pos = name_end + 1

    return deps


# ---------------------------------------------------------------------------
# Aggregate version detection
# ---------------------------------------------------------------------------

def _detect_versions(repo_root: str) -> list[DetectedDependency]:
    """Scan package/config files for language and framework versions.

    Returns an empty list if no versions can be determined.
    """
    root = Path(repo_root)
    deps: list[DetectedDependency] = []

    for detector in (
        _detect_python_versions,
        _detect_node_versions,
        _detect_php_versions,
        _detect_go_versions,
        _detect_ruby_versions,
        _detect_java_versions,
        _detect_rust_versions,
        _detect_csharp_versions,
    ):
        try:
            deps.extend(detector(root))
        except Exception as exc:
            logger.debug("Version detector %s failed: %s", detector.__name__, exc)

    return deps


# ---------------------------------------------------------------------------
# Search query generation
# ---------------------------------------------------------------------------

def _build_search_queries(deps: list[DetectedDependency]) -> list[str]:
    """Generate web search queries for deprecation information.

    Prioritizes runtimes and frameworks over individual libraries.
    """
    from lean_ai.config import settings

    queries: list[str] = []

    # First pass: runtimes and frameworks (highest value)
    for dep in deps:
        if dep.category in ("runtime", "framework"):
            version_clean = _extract_major_minor(dep.version)
            if version_clean:
                queries.append(
                    f"{dep.name} {version_clean} deprecated functions breaking changes"
                )

    # Second pass: top libraries (if budget remains)
    for dep in deps:
        if dep.category == "library" and len(queries) < settings.deprecation_max_searches:
            version_clean = _extract_major_minor(dep.version)
            if version_clean:
                queries.append(
                    f"{dep.name} {version_clean} deprecations migration guide"
                )

    return queries[:settings.deprecation_max_searches]


# ---------------------------------------------------------------------------
# LLM summarization prompt
# ---------------------------------------------------------------------------

def _build_deprecation_summary_prompt(deps: list[DetectedDependency]) -> str:
    """Build the LLM summary prompt, scoped to the project's actual dependencies."""
    dep_names = ", ".join(sorted({d.name for d in deps}))
    return (
        "Analyze the following web search results about software deprecations and "
        "produce a concise, actionable deprecation guide.\n\n"
        f"This project uses ONLY these technologies: {dep_names}\n"
        "ONLY include deprecation warnings for the technologies listed above. "
        "Ignore any information about frameworks or libraries NOT in that list.\n\n"
        "For each deprecated item, provide:\n"
        "1. The SPECIFIC function, method, class, or feature name that is deprecated\n"
        "2. What version deprecated it\n"
        "3. What to use INSTEAD (the recommended replacement)\n"
        "4. Brief migration note if applicable\n\n"
        "Format the output as Markdown. Group by framework/library. "
        "Use a bullet list for each deprecated item.\n\n"
        "RULES:\n"
        f"- ONLY list deprecations for: {dep_names}\n"
        "- Only list items that are actually deprecated — do not speculate\n"
        "- Be SPECIFIC: give exact function/class names, not vague descriptions\n"
        "- If the search results contain no deprecation information for the listed "
        "technologies, say \"No deprecations found\"\n"
        "- Keep it concise — maximum 1500 words total"
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def generate_deprecation_section(
    repo_root: str,
    llm_client: LLMClient,
    max_tokens: int = 2048,
) -> str:
    """Detect versions, search for deprecations, and return a Markdown section.

    Returns ``""`` if no deprecations are found or if any step fails.
    Designed to be called AFTER the main context generation completes.
    """
    from lean_ai.config import settings
    from lean_ai.tools.internet import search_internet

    if not settings.enable_deprecation_lookup:
        return ""

    # Step 1: Detect versions
    try:
        deps = _detect_versions(repo_root)
    except Exception as exc:
        logger.warning("Deprecation lookup: version detection failed: %s", exc)
        return ""

    if not deps:
        logger.info("Deprecation lookup: no versions detected, skipping")
        return ""

    logger.info(
        "Deprecation lookup: detected %d dependencies: %s",
        len(deps),
        ", ".join(f"{d.name} {d.version}" for d in deps[:10]),
    )

    # Step 2: Generate search queries
    queries = _build_search_queries(deps)
    if not queries:
        logger.info("Deprecation lookup: no search queries generated, skipping")
        return ""

    logger.info("Deprecation lookup: running %d web searches", len(queries))

    # Step 3: Search sequentially (primp/lxml are not thread-safe for
    # concurrent use — parallel asyncio.to_thread calls cause segfaults
    # that crash the uvicorn subprocess).
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
            logger.debug("Deprecation search timed out for '%s'", query)
        except Exception as exc:
            logger.debug("Deprecation search failed for '%s': %s", query, exc)

    if not search_parts:
        logger.info("Deprecation lookup: all searches returned empty, skipping")
        return ""

    combined_search = "\n\n".join(search_parts)

    # Step 4: LLM summarization
    try:
        summary = await llm_client.chat_raw(
            messages=[
                {"role": "system", "content": _build_deprecation_summary_prompt(deps)},
                {"role": "user", "content": combined_search[:20000]},
            ],
            max_tokens=max_tokens,
        )
    except Exception as exc:
        logger.warning("Deprecation lookup: LLM summarization failed: %s", exc)
        return ""

    if not summary.strip() or "no deprecations found" in summary.lower():
        logger.info("Deprecation lookup: LLM found no actionable deprecations")
        return ""

    # Step 5: Assemble section
    section = (
        "\n\n## Deprecation Warnings\n\n"
        "_Auto-generated from web search. Verify before acting on these._\n\n"
        f"{summary.strip()}\n"
    )

    logger.info("Deprecation lookup: generated %d-char section", len(section))
    return section

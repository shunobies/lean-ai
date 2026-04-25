"""Style guide generation — discovers CSS/template files and generates a design reference.

Writes to ``.lean_ai/context/style_guide.md`` so the guide is automatically
loaded alongside other custom steering documents during workflows and chat.
"""

import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from lean_ai.context.content import _read_file_safe
from lean_ai.indexer.tree import list_repo_tree

logger = logging.getLogger(__name__)


def get_compact_tree(repo_root: str, max_entries: int = 100) -> str:
    """Return a compact file tree of the project for inclusion in the prompt."""
    try:
        entries = list_repo_tree(repo_root)
        lines = [e.path for e in entries[:max_entries]]
        return "\n".join(lines)
    except Exception:
        return ""


# Directories containing compiled/bundled output — skip these.
_SKIP_DIRS = ("public/", "dist/", "build/", ".output/", ".next/", "vendor/", "node_modules/")

# Maximum files per category to prevent prompt bloat.
_MAX_CONFIG = 3
_MAX_GLOBAL_CSS = 4
_MAX_SCSS_VARS = 4
_MAX_LAYOUTS = 3
_MAX_COMPONENTS = 5

# Character budgets.
_MAX_TOTAL_CHARS = 30_000
_MAX_FILE_CHARS = 5_000


# ---------------------------------------------------------------------------
# File classification
# ---------------------------------------------------------------------------


def _classify_file(path: str) -> str | None:
    """Classify a file path into a style category, or *None* if irrelevant."""
    lower = path.lower()

    # Skip compiled output directories.
    for skip in _SKIP_DIRS:
        if skip in lower:
            return None

    name = Path(path).name.lower()
    stem = Path(path).stem.lower()
    suffix = Path(path).suffix.lower()

    # Tier 1: CSS framework / build config
    if name.startswith("tailwind.config"):
        return "css_framework_config"
    if name.startswith("postcss.config"):
        return "css_framework_config"
    if name == "webpack.mix.js":
        return "css_framework_config"

    # Tier 2: Global CSS / SCSS variables
    if suffix in (".css", ".scss", ".sass", ".less"):
        # SCSS partials starting with underscore (e.g. _variables.scss)
        if name.startswith("_"):
            return "scss_variables"
        _css_keywords = ("variables", "theme", "colors", "typography", "custom-properties")
        if any(kw in stem for kw in _css_keywords):
            return "global_css"
        if name in ("app.css", "global.css", "globals.css", "style.css", "styles.css", "main.css"):
            return "global_css"
        # Other CSS files — treat as lower-priority global
        return "global_css"

    # Tier 3: Layout / master templates
    if ".blade.php" in lower:
        # Blade stems have double extension: master.blade.php → stem="master.blade"
        blade_stem = name.split(".")[0]
        if "/layouts/" in lower or blade_stem in ("app", "master", "layout", "base", "main"):
            return "layout_templates"
        if "/partials/" in lower or "/components/" in lower:
            return "component_templates"
        return "component_templates"

    if suffix == ".vue":
        if stem in ("app", "layout", "defaultlayout", "baselayout", "mainlayout"):
            return "layout_templates"
        return "component_templates"

    if suffix in (".tsx", ".jsx"):
        if "layout" in stem:
            return "layout_templates"
        if "/components/" in lower or "/pages/" in lower:
            return "component_templates"

    if suffix in (".html", ".htm"):
        if stem in ("base", "layout", "master", "index"):
            return "layout_templates"

    return None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _discover_style_files(repo_root: str) -> dict[str, list[str]]:
    """Discover style-relevant files grouped by category.

    Returns a dict keyed by category with lists of relative file paths.
    """
    categories: dict[str, list[str]] = {
        "css_framework_config": [],
        "global_css": [],
        "scss_variables": [],
        "layout_templates": [],
        "component_templates": [],
    }

    try:
        entries = list_repo_tree(repo_root)
    except Exception:
        return categories

    for entry in entries:
        cat = _classify_file(entry.path)
        if cat and cat in categories:
            categories[cat].append(entry.path)

    # Sort each category alphabetically, then apply file count limits.
    limits = {
        "css_framework_config": _MAX_CONFIG,
        "global_css": _MAX_GLOBAL_CSS,
        "scss_variables": _MAX_SCSS_VARS,
        "layout_templates": _MAX_LAYOUTS,
        "component_templates": _MAX_COMPONENTS,
    }
    for cat in categories:
        categories[cat].sort()
        categories[cat] = categories[cat][: limits.get(cat, 5)]

    return categories


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def _detect_css_framework(
    categories: dict[str, list[str]],
    repo_root: str,
) -> str:
    """Detect the CSS framework from config files and dependencies."""
    for path in categories.get("css_framework_config", []):
        name = Path(path).name.lower()
        if name.startswith("tailwind.config"):
            return "Tailwind CSS"
        if name == "webpack.mix.js":
            return "Laravel Mix"

    # Check package.json for common frameworks.
    pkg_path = Path(repo_root) / "package.json"
    if pkg_path.is_file():
        try:
            content = pkg_path.read_text(encoding="utf-8", errors="replace").lower()
            if "tailwindcss" in content:
                return "Tailwind CSS"
            if "bootstrap" in content:
                return "Bootstrap"
            if "bulma" in content:
                return "Bulma"
        except Exception:
            pass

    # Check for any CSS files at all.
    has_css = any(categories.get(k) for k in ("global_css", "scss_variables"))
    return "custom CSS" if has_css else "none detected"


def _collect_style_contents(
    repo_root: str,
    categories: dict[str, list[str]],
    max_total: int = _MAX_TOTAL_CHARS,
    max_per_file: int = _MAX_FILE_CHARS,
) -> str:
    """Read style files within character budget.

    Returns the collected content as a formatted string.
    """
    root = Path(repo_root)
    parts: list[str] = []
    total = 0

    # Priority order for reading.
    priority = [
        "css_framework_config",
        "global_css",
        "scss_variables",
        "layout_templates",
        "component_templates",
    ]

    for cat in priority:
        for path in categories.get(cat, []):
            if total >= max_total:
                break
            remaining = max_total - total
            cap = min(max_per_file, remaining)
            content = _read_file_safe(root / path, max_chars=cap)
            if content.strip():
                parts.append(f"--- {path} ({cat}) ---\n{content}")
                total += len(content)

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_STYLE_GUIDE_SYSTEM_PROMPT = """\
Generate a style guide document for a web project.

DETECTED CSS FRAMEWORK: {css_framework}

The style guide must capture the ACTUAL visual design patterns from the \
project's own CSS, templates, and component files provided below. Extract \
real values — do not invent or assume.

PROJECT FILE TREE:
{project_tree}

STRUCTURE RULES:
- Do NOT generate a top-level heading (# Style Guide) — it is added automatically.
- Start your output directly with the first ## section heading.
- Each ## heading below MUST appear EXACTLY ONCE.
- Every code example MUST have matching opening and closing ``` pairs.
- Maximum 3000 words total.
- Only describe patterns visible in the provided files. If a section has no \
applicable data, write "Not detected in project files."

REQUIRED SECTIONS (use exactly these ## headings):

## CSS Framework & Build Setup
Identify the CSS framework (Tailwind, Bootstrap, custom, etc.) and how styles \
are compiled/loaded. Note the build tool (Vite, Webpack/Mix, etc.) and the \
main CSS entry point file.

If using Tailwind CSS, this section is CRITICAL — extract the FULL custom \
theme configuration from tailwind.config.js/ts. Show the complete \
`theme.extend` object including all custom colors, fonts, spacing, \
breakpoints, and any plugins. This is the single most important piece of \
information for maintaining design consistency.

## Color Palette
Document ONLY the project's custom/configured colors — not default framework \
colors.

CRITICAL RULES:
- If the project uses Tailwind WITHOUT custom theme.extend.colors, write: \
"Uses default Tailwind color palette." Then list ONLY the 3-5 dominant color \
families actually used in templates (e.g. "Primary actions: blue-500/600, \
Danger: red-500/600, Neutral: gray-50 through gray-900") — do NOT list every \
shade with hex values.
- If the project HAS custom theme.extend.colors, show that config object and \
list each custom color ONCE.
- If using CSS custom properties or SCSS variables, list each ONCE.
- NEVER list the same color under multiple purpose headings.
- NEVER list default Tailwind hex values — they are already known.
- Keep this section SHORT — a wall of color values is counterproductive.

## Typography
Extract font families, sizes, weights, and line heights. Look for:
- Font imports (@import, link tags, @font-face)
- CSS custom properties for fonts
- Tailwind font configuration in theme.extend.fontFamily
- Body/heading font declarations
List the font stack and where each is used (headings, body, code, etc.)

## Spacing & Layout
Describe the layout system:
- Container max-widths and padding
- Grid system (CSS Grid, Flexbox patterns, Tailwind grid, Bootstrap grid)
- Spacing scale (margin/padding values or Tailwind spacing)
- Breakpoints for responsive design

## Component Patterns
For each reusable UI pattern found in templates/components, show the ACTUAL \
markup from the project files. Copy the real HTML/Blade/Vue structure with \
its CSS classes — do not paraphrase or summarize into prose.

For each component include:
- The source file path
- The actual markup snippet (HTML structure with classes)
- Key Tailwind/CSS classes used and their visual effect

Components to document (if found):
- Navigation/header
- Hero/banner sections
- Card layouts
- Footer
- Buttons (show each variant)
- Form elements

Example of GOOD output:
```blade
<!-- resources/views/components/hero.blade.php -->
<section class="bg-gradient-to-r from-indigo-900 to-purple-900 py-20">
  <div class="max-w-4xl mx-auto text-center">
    <h1 class="text-5xl font-bold text-white mb-6">{{{{ $title }}}}</h1>
  </div>
</section>
```

Example of BAD output (do not do this):
"The hero section uses a gradient background with centered text."

## Page Layout Structure
Describe how pages are structured:
- The main layout/master template and its sections/slots/yields
- How child pages extend the layout
- The HTML document structure (head contents, body wrapper classes)
- Asset loading patterns (where CSS/JS are included)
Show the actual @yield/@section slots from the master layout file.

CONTENT RULES:
- Extract REAL values from the provided files — colors, font names, class names
- Reference ACTUAL file paths from the project tree
- Show code snippets from the provided files, not invented examples
- If using Tailwind, the tailwind.config theme.extend is the PRIMARY source of \
truth — prioritize it above all other sources for colors, fonts, and spacing
- If using component classes, show the actual class naming convention
"""


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def generate_style_guide(
    repo_root: str,
    llm_client,
    max_tokens: int = 4096,
    thinking_callback: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """Discover style files, collect contents, and generate a style guide.

    Returns the guide as a Markdown string, or ``""`` if no style files found.
    """
    categories = _discover_style_files(repo_root)
    total_files = sum(len(v) for v in categories.values())
    if total_files == 0:
        logger.info("Style guide: no style files found")
        return ""

    logger.info(
        "Style guide: found %d files (%s)",
        total_files,
        ", ".join(f"{k}={len(v)}" for k, v in categories.items() if v),
    )

    css_framework = _detect_css_framework(categories, repo_root)
    collected = _collect_style_contents(repo_root, categories)
    project_tree = get_compact_tree(repo_root)

    system_prompt = _STYLE_GUIDE_SYSTEM_PROMPT.format(
        css_framework=css_framework,
        project_tree=project_tree or "(not available)",
    )
    user_content = (
        f"Analyze the following project files and generate the style guide.\n\n{collected}"
    )[:40_000]

    logger.info(
        "Style guide: generating via LLM (%d files, %d-char prompt, framework=%s)",
        total_files,
        len(user_content),
        css_framework,
    )

    try:
        guide = await llm_client.chat_raw(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            max_tokens=max_tokens,
            thinking_callback=thinking_callback,
        )
    except Exception as exc:
        logger.warning("Style guide: LLM generation failed: %s", exc)
        return ""

    if not guide.strip():
        logger.info("Style guide: LLM returned empty output")
        return ""

    # Strip any top-level heading the LLM may have generated despite instructions.
    cleaned = re.sub(r"^#\s+[Ss]tyle\s+[Gg]uide\s*\n+", "", guide.strip())

    header = (
        "# Style Guide\n\n"
        "_Auto-generated from project CSS and template files. "
        "Edit freely — this file is yours to curate._\n\n"
    )
    return header + cleaned.strip() + "\n"


def write_style_guide(repo_root: str, content: str) -> str:
    """Write style guide to ``.lean_ai/context/style_guide.md``."""
    output_dir = Path(repo_root) / ".lean_ai" / "context"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "style_guide.md"
    output_path.write_text(content, encoding="utf-8")
    logger.info("Style guide written to %s (%d chars)", output_path, len(content))
    return str(output_path)

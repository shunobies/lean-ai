"""Constants, size caps, system prompts, and registry helpers for project context generation.

These values are calibrated for a 32 768-token context window.
``_scale_generation_caps()`` multiplies them by ``(context_window / 32768)``
so users with larger GPUs automatically get richer coverage without
touching any config file.
"""

import logging

from lean_ai.languages.registry import get_registry as _get_registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Universal key files (language-agnostic).  Language-specific key files
# (pyproject.toml, package.json, etc.) come from YAML definitions via
# the language registry.
# ---------------------------------------------------------------------------

_UNIVERSAL_KEY_FILES = [
    "README.md",
    "CLAUDE.md",
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".env.example",
    # Infrastructure / IaC
    "ansible.cfg",
    "site.yml",
    "playbook.yml",
    "main.tf",
    "Chart.yaml",
    "Pulumi.yaml",
]


def _get_key_files() -> list[str]:
    """Return universal key files merged with language-specific ones."""
    try:
        lang_files = _get_registry().all_key_files()
    except Exception:
        lang_files = [
            "pyproject.toml", "package.json", "requirements.txt",
            "setup.py", "setup.cfg",
        ]
    seen: set[str] = set()
    result: list[str] = []
    for f in _UNIVERSAL_KEY_FILES + lang_files:
        if f not in seen:
            result.append(f)
            seen.add(f)
    return result


def _get_source_exts() -> set[str]:
    """Return all registered source file extensions from the language registry."""
    try:
        return _get_registry().all_source_extensions()
    except Exception:
        return {".py", ".ts", ".js"}


def _get_entry_points() -> set[str]:
    """Return aggregated entry point filenames from the language registry."""
    try:
        return _get_registry().all_entry_points()
    except Exception:
        return {
            "main.py", "app.py", "server.py",
            "index.ts", "index.js", "main.ts", "main.js", "app.ts", "app.js",
        }

# ---------------------------------------------------------------------------
# Section-size constants — 32K context-window baselines
# ---------------------------------------------------------------------------

# Max chars for doc files (README, CLAUDE.md) at 32K.
_MAX_DOC_FILE_CHARS = 6000

# Max chars to read from any single source file at 32K.
_MAX_FILE_CHARS = 5000

# Max total chars for the file samples section at 32K.
_MAX_SAMPLE_CHARS = 25000

# Max chars for the class/function index section at 32K.
_MAX_INDEX_CHARS = 40000

# Max chars for the import graph section at 32K.
_MAX_IMPORT_GRAPH_CHARS = 10000


_ADDITIVE_EXPANSION_PROMPT = """\
This is an additive expansion round. You are given:
1. EXISTING DOCUMENT — the project context document produced so far
2. SOURCE FILES — additional source files not yet covered in the document

Your task: update the existing document by placing new data from the source \
files under the proper existing headings. Return the complete updated document.

Rules:
- Do NOT remove or rephrase existing content — only add new entries.
- Place new findings under the correct existing ## headings.
- Use EXACT class names, function names, and file paths from the source files.
- Do not invent or generalize names not visible in the provided data.
- Keep the same Markdown structure and heading order.
- Keep the total document under 6000 words.\
"""


_PARALLEL_EXPANSION_PROMPT = """\
Analyze these source files and extract NEW information to add to an existing \
project context document.

You are given:
1. SECTION HEADINGS — the existing ## headings in the document
2. SOURCE FILES — source files not yet covered in the document

Your task: identify new classes, functions, endpoints, data flows, conventions, \
and relationships from the source files and output ONLY the new entries, \
organized under the correct existing ## headings.

Rules:
- Output ONLY new entries — do not repeat or summarize existing content.
- Each entry must go under one of the existing ## headings listed above.
- Use the heading text EXACTLY as given (e.g., "## Module Map", "## Key Abstractions").
- Skip any heading for which the source files add nothing new.
- Use EXACT class names, function names, and file paths from the source files.
- Do not invent or generalize names not visible in the provided data.
- Keep entries concise: one line per class/function, a short paragraph per module.
- If a file reveals a new module, place it under ## Module Map.
- If a file reveals new classes/functions, place them under ## Key Abstractions.
- If a file reveals new API endpoints, place them under ## API Surface.
- If a file reveals new integration points, place them under ## Integration Points.\
"""


_SKELETON_GENERATION_SYSTEM_PROMPT = """\
Produce a structural overview document for this codebase using the provided \
metadata (file tree, class/function index, import graph, API endpoints). \
Use only names visible in the data — do not invent names or relationships. \
Source file contents will be added in later passes; keep descriptions brief.

If a section has no relevant data, write the heading then "No data extracted yet."

Output the document in Markdown with these sections:

# Project Context

## Architecture Overview
One paragraph: what this project does, its high-level architecture, entry \
points, and frameworks visible in the data.

## Module Map
For each major directory in the file tree: its responsibility, key files, \
and class/function names from the index.

## Key Abstractions
Classes and important functions from the CLASS AND FUNCTION INDEX. \
For each: file path, likely responsibility, and interactions from the import graph.

## Data Flow
How data flows through the system based on the import graph. Use numbered \
steps with actual names. Mark inferred connections with "(inferred)".

## Conventions
Naming patterns, project structure conventions, and configuration approach \
observed in the file tree and index.

## Integration Points
How modules connect at the directory level based on the import graph. \
Group by source directory → target directory.

## API Surface
REST and WebSocket endpoints from the API ENDPOINTS data: HTTP method, \
URL path, and handler function name.\
"""


_SINGLE_FILE_UPDATE_PROMPT = """\
This is a single-file update round. You are given:
1. EXISTING DOCUMENT — the project context document built so far
2. SOURCE FILE — one source file not yet analyzed in the document

Your task: update the existing document by incorporating any new information \
from this source file under the proper existing headings. Return the complete \
updated document.

Rules:
- Do NOT remove or rephrase existing content — only add or refine entries.
- Place new findings under the correct existing ## headings.
- Use EXACT class names, function names, and file paths from the source file.
- Do not invent or generalize names not visible in the provided data.
- Keep the same Markdown structure and heading order.
- If the file adds nothing new to a section, leave that section unchanged.
- Keep the total document under 6000 words.\
"""


# ---------------------------------------------------------------------------
# Fixed token overhead for the skeleton generation LLM call
# ---------------------------------------------------------------------------

_SKELETON_PROMPT_WRAPPER_CHARS: int = len(
    "=== FILE TREE ===\n\n\n"
    "=== CLASS AND FUNCTION INDEX ===\n\n\n"
    "=== IMPORT GRAPH ===\n\n\n"
    "=== API ENDPOINTS ===\n\n\n"
    "Write the structural overview document using only the data above."
)

_GENERATION_FIXED_OVERHEAD_TOKENS: int = int(
    (len(_SKELETON_GENERATION_SYSTEM_PROMPT) + _SKELETON_PROMPT_WRAPPER_CHARS)
    / 4.2
    * 1.2
)


_CONTEXT_GENERATION_CAP_TOKENS: int = 65536
"""Hard cap on the effective context window used for generation budget
calculations.  Prevents oversized single-pass prompts on large-context models
(e.g. 256k) by keeping generation input within a manageable size."""

# 70% of the capped context window, used as the threshold for switching
# to headings-only mode during iterative file-by-file generation.
_ITERATIVE_INPUT_BUDGET_CHARS: int = int(
    int(_CONTEXT_GENERATION_CAP_TOKENS * 0.70) * 3.3
)


def _scale_generation_caps(context_window: int, max_output_tokens: int) -> dict[str, int]:
    """Compute input-section size caps for the context-generation prompt.

    Scales each section proportionally to the available input token budget
    so the generation prompt always fits within the model's context window.

    The effective context window is capped at ``_CONTEXT_GENERATION_CAP_TOKENS``
    (65 536) regardless of the model's actual window to keep prompts
    manageable for local LLMs.

    Allocations (of input budget chars):
        index         35% — class/function index (code-dense)
        import_graph  10% — import relationships
        sample        28% — key file contents
        api_endpoints 11% — REST/WS endpoint listing

    Total: 84%, leaving ~16% headroom for the file tree section and
    tokeniser variance.
    """
    effective_window = min(context_window, _CONTEXT_GENERATION_CAP_TOKENS)
    input_budget_tokens = max(
        0, effective_window - max_output_tokens - _GENERATION_FIXED_OVERHEAD_TOKENS
    )
    input_budget_chars = int(input_budget_tokens * 3.3)

    baseline = 32768
    scale = max(1.0, effective_window / baseline)

    return {
        "index":              min(_MAX_INDEX_CHARS * 10,        int(input_budget_chars * 0.35)),
        "import_graph":       min(_MAX_IMPORT_GRAPH_CHARS * 10, int(input_budget_chars * 0.10)),
        "sample":             min(_MAX_SAMPLE_CHARS * 10,       int(input_budget_chars * 0.28)),
        "api_endpoints":      min(80000,                        int(input_budget_chars * 0.11)),
        "max_file_chars":     min(25000, max(3000, int(_MAX_FILE_CHARS     * scale))),
        "max_doc_file_chars": min(30000, max(3600, int(_MAX_DOC_FILE_CHARS * scale))),
        "max_sampled_files":  min(25,    max(15,   int(15                  * scale))),
    }

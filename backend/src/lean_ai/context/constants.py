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


_PARALLEL_EXPANSION_PROMPT = _ADDITIVE_EXPANSION_PROMPT  # kept for back-compat


_SINGLE_FILE_UPDATE_PROMPT = """\
You are updating an existing project context document.

Task:
Extract ONLY net-new facts from ONE source file and map them to EXISTING \
section headings.

Inputs:
1. EXISTING_SECTION_HEADINGS
2. EXISTING_CONTEXT_TEXT (may be omitted when document is large)
3. SOURCE_FILE_PATH
4. SOURCE_FILE_CONTENT

Goal:
Return only facts from SOURCE_FILE_CONTENT that are not already present \
in EXISTING_CONTEXT_TEXT.

Hard rules:
- Do NOT think out loud.
- Do NOT explain your reasoning.
- Do NOT say things like "I checked", "it seems", "I will add", or "wait".
- Do NOT repeat existing facts already present in EXISTING_CONTEXT_TEXT.
- Do NOT invent names, roles, relationships, or behavior.
- Use only names that appear exactly in SOURCE_FILE_CONTENT.
- Every entry must include the exact file path.
- If nothing new is found, output exactly: NO_NEW_INFO

Allowed section targets:
- ## Architecture Overview
- ## Module Map
- ## Key Abstractions
- ## API Surface
- ## Integration Points
- ## Data Flow
- ## Conventions

Extraction rules:
- ## Architecture Overview: new entry points, frameworks, or architectural \
patterns not already documented.
- ## Module Map: new module/file purpose only if not already covered.
- ## Key Abstractions: exact class names, functions, methods, constants, \
type aliases.
- ## API Surface: exact routes, handlers, request/response models, CLI \
commands, public interfaces.
- ## Integration Points: external services, databases, queues, filesystems, \
SDKs, env vars, third-party calls.
- ## Data Flow: explicit producer/consumer relationships and transformations \
visible in code.
- ## Conventions: naming patterns, registry usage, inheritance patterns, \
decorators, folder conventions clearly shown in code.

Output format:
Return markdown only.

For each section with new facts:
## <exact heading>
- `<exact symbol or module>` — <concise fact grounded in source file> \
(`<file path>`)

Do not output any heading with zero new facts.
Do not output any text before or after the markdown.\
"""


# ---------------------------------------------------------------------------
# Fixed token overhead for the per-file update LLM calls
# ---------------------------------------------------------------------------

_GENERATION_FIXED_OVERHEAD_TOKENS: int = int(
    len(_SINGLE_FILE_UPDATE_PROMPT) / 4.2 * 1.2
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

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


_EXPANSION_SYSTEM_PROMPT = """\
Use your knowledge of software architecture to update the existing project context \
document with findings from additional source files not yet covered in the document.

You are given:
1. EXISTING PROJECT CONTEXT — the document produced so far
2. ADDITIONAL FILE CONTENTS — more source files from the same codebase

Your task: write the COMPLETE updated document, merging what you learn from the \
additional files into the relevant sections already present in the document.

Rules:
- Reproduce ALL sections from the existing document — do not omit any content
- Merge new class names, functions, endpoints, and relationships into the correct
  existing section — do not place them at the end of the document
- Do NOT contradict or duplicate existing content — only update it in place
- If new files reveal a module not yet described, insert it into the Module Map section
- Use EXACT names from the provided files — never invent names
- Keep the same Markdown structure (# Project Context, ## Architecture Overview, etc.)
- Keep the total document under 6000 words

CRITICAL — no new top-level headings:
- NEVER create sections named "Additional Information", "New Classes and Functions",
  "Additional Files", "Updated Module Map", or any other new top-level heading.
- New findings belong INSIDE the existing named sections, not after them.
  New modules → insert into ## Module Map.  New classes/functions → insert into
  ## Key Abstractions under the correct file heading.  New endpoints → insert into
  ## API Endpoints.  New relationships → insert into ## Integration Points.
- The output must have the same top-level ## headings as the input, no more.

CRITICAL — accuracy:
- Only reference class names, function names, and file paths visible in the data
  you have been given.  Do not invent or generalize.\
"""


_ADDITIVE_EXPANSION_PROMPT = """\
Use your knowledge of software architecture to analyze new source files and \
produce entries to ADD to an existing project context document.

You are given:
1. SECTION HEADINGS — the top-level sections already in the document
2. ALREADY COVERED — class/function names already described (do NOT repeat these)
3. SOURCE FILES — new files not yet described in the document

Your task: produce ONLY new entries to insert into the existing sections. \
Do NOT reproduce the existing document — output only the delta.

Output format — group entries under matching ## headings:

## Module Map
### path/to/new_module/
- Responsible for X
- Key files: `file_a.py`, `file_b.py`, `file_c.py`
- Key classes: `ClassName`, `OtherClass`
- Key functions: `function_name()`, `other_function()`
(List names only — detailed descriptions belong in Key Abstractions)

## Key Abstractions
### path/to/file.py
- `ClassName` — responsibility, interacts with X
- `function_name()` — responsibility

## Integration Points
- `new_module/` → `other_module/` — imports client classes for API communication

## Data Flow
- (Add numbered steps ONLY if the new files reveal a completely new request/data path \
not covered above. Do NOT restate existing flows in different words.)

## Conventions
- (Add a pattern ONLY if it is a genuinely new convention not already stated above. \
Do NOT rephrase or elaborate on existing conventions.)

## API Surface
- `POST /new-endpoint` → `handler_function()` in `file.py`

Rules:
- ONLY include sections where the new files contribute something. Skip empty sections.
- Use EXACT class names, function names, and file paths from the provided source files.
- Do NOT invent or generalize. If a name is not in the source files, do not mention it.
- Do NOT repeat entries already listed in the ALREADY COVERED section.
- Be concise but thorough — every class, function, and endpoint in the source files \
should be accounted for.\
"""


_CONTEXT_GENERATION_SYSTEM_PROMPT = """\
Use your knowledge of software architecture to analyze this codebase and produce \
a factual project overview document. You are given:
1. The file tree
2. A CLASS AND FUNCTION INDEX extracted directly from the source code
3. An IMPORT GRAPH showing which modules depend on which
4. Contents of key files

ONLY describe things you can see in the provided data. \
NEVER invent class names, function names, or relationships that are not shown.

Write the document in Markdown with EXACTLY these sections:

# Project Context

## Architecture Overview
One paragraph: what this project does, its purpose, and high-level \
architecture pattern. Reference the actual entry points and frameworks you see.

## Module Map
For each major directory/module shown in the file tree:
- What it is responsible for (based on the files you can see)
- Key files and their actual roles
- List class/function names defined there but do NOT describe their internals — \
save detailed descriptions for the Key Abstractions section

## Key Abstractions
List the ACTUAL classes and important functions from the CLASS AND FUNCTION INDEX. \
For each one:
- State its file path
- Describe its responsibility based on the code you can see
- Note which other classes/modules it interacts with (use the IMPORT GRAPH)

DO NOT describe classes that are not in the index. \
DO NOT rename or generalize — use the exact names from the code. \
IMPORTANT: If a file contains only functions and no class definition, list those \
functions directly — do NOT invent a class name to wrap them. A module of functions \
is not a class.

## Data Flow
How requests or data flow through the system. Trace the path using ACTUAL \
function and class names from the code. Use the IMPORT GRAPH to determine \
which modules call which. Use numbered steps. \
Each step must reference a real file, class, or function.

## Conventions
Based on patterns you observe in the provided code:
- Naming patterns (files, functions, classes) — cite actual examples
- Error handling approach — cite what you see
- Test organization and patterns
- Configuration approach

## Integration Points
Use the IMPORT GRAPH to describe how modules connect at the DIRECTORY level. \
Group imports by source directory → target directory. Do NOT list every individual \
import statement — summarize by module/directory relationship. Example:
- `app/Http/Controllers/` → `app/Models/` — controllers import model classes
- `app/Services/` → `app/Repositories/` — services use repository interfaces

Only list cross-module connections (different directories). Skip framework/stdlib \
imports — only list project-internal connections.

DO NOT invent integration points that are not visible in the IMPORT GRAPH.

## API Surface
List ALL REST and WebSocket endpoints from the API ENDPOINTS data. \
For each endpoint show: HTTP method, URL path, and handler function name. \
Group endpoints by resource (sessions, traceability, chat, etc.).
Also list public methods of key client/service classes from the CLASS AND \
FUNCTION INDEX — especially classes that serve as API facades or SDK clients \
(e.g. BackendClient, LLMClient), so consumers know the actual callable surface. \
Do NOT invent endpoints or methods that are not in the provided data.

CRITICAL RULES:
- ONLY reference class names, function names, and file paths that appear in the \
provided data. If a name is not in the file tree or class/function index, do not mention it.
- If you cannot determine something, say "Not visible in provided code samples."
- Do NOT use generic descriptions like "manages various tools" — state which \
specific classes/functions do what.
- Keep the total document under 6000 words.
"""

# ---------------------------------------------------------------------------
# Fixed token overhead for the context-generation LLM call
# ---------------------------------------------------------------------------

_GENERATION_PROMPT_WRAPPER_CHARS: int = len(
    "Analyze this repository and produce the project context document.\n\n"
    "=== FILE TREE ===\n"
    "\n\n"
    "=== CLASS AND FUNCTION INDEX ===\n"
    "These are the ACTUAL class and function definitions found in the source code. "
    "Use ONLY these names in your document — do not invent others.\n\n"
    "\n\n"
    "=== IMPORT GRAPH ===\n"
    "These are the ACTUAL import relationships between modules. "
    "Use this to describe how modules connect — do not guess connections.\n\n"
    "\n\n"
    "=== API ENDPOINTS ===\n"
    "These are the ACTUAL REST and WebSocket endpoint routes defined in "
    "the source code. Include ALL of these in your API Surface section — "
    "do not invent endpoints that are not listed here.\n\n"
    "\n\n"
    "=== KEY FILE CONTENTS ===\n"
    "\n\n"
    "Now write the project context document. Remember: ONLY reference "
    "class names, function names, and files that appear above. "
    "Do NOT invent or generalize."
)

_GENERATION_FIXED_OVERHEAD_TOKENS: int = int(
    (len(_CONTEXT_GENERATION_SYSTEM_PROMPT) + _GENERATION_PROMPT_WRAPPER_CHARS)
    / 4.2
    * 1.2
)


def _scale_generation_caps(context_window: int, max_output_tokens: int) -> dict[str, int]:
    """Compute input-section size caps for the context-generation prompt.

    Scales each section proportionally to the available input token budget
    so the generation prompt always fits within the model's context window.

    Allocations (of input budget chars):
        index         35% — class/function index (code-dense)
        import_graph  10% — import relationships
        sample        28% — key file contents
        api_endpoints 11% — REST/WS endpoint listing

    Total: 84%, leaving ~16% headroom for the file tree section and
    tokeniser variance.
    """
    input_budget_tokens = max(
        0, context_window - max_output_tokens - _GENERATION_FIXED_OVERHEAD_TOKENS
    )
    input_budget_chars = int(input_budget_tokens * 3.3)

    baseline = 32768
    scale = max(1.0, context_window / baseline)

    return {
        "index":              min(_MAX_INDEX_CHARS * 10,        int(input_budget_chars * 0.35)),
        "import_graph":       min(_MAX_IMPORT_GRAPH_CHARS * 10, int(input_budget_chars * 0.10)),
        "sample":             min(_MAX_SAMPLE_CHARS * 10,       int(input_budget_chars * 0.28)),
        "api_endpoints":      min(80000,                        int(input_budget_chars * 0.11)),
        "max_file_chars":     min(25000, max(3000, int(_MAX_FILE_CHARS     * scale))),
        "max_doc_file_chars": min(30000, max(3600, int(_MAX_DOC_FILE_CHARS * scale))),
        "max_sampled_files":  min(100,   max(15,   int(15                  * scale))),
    }

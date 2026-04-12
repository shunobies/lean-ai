"""Native tool definitions for Ollama's tools= parameter.

Uses OpenAI-compatible JSON schema format. Ollama injects these into
the system prompt using the model's chat template.
"""

IMPLEMENTATION_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": (
                "Create a new file with the given content. "
                "Use ONLY for files that do not exist yet. "
                "For modifying existing files, use edit_file instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "File path relative to the repository root, or an "
                            "absolute path for files outside the project "
                            "(requires user approval)"
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": "The complete file content to write",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Edit an existing file by finding and replacing a specific text block. "
                "The search text must match the file content exactly, including "
                "indentation and whitespace. Keep search blocks small — only the "
                "lines being changed plus 1-2 lines of surrounding context. "
                "Use multiple edit_file calls for multiple changes in the same file. "
                "If edit_file fails, always re-read the file with read_file "
                "before retrying — the file content changes after each "
                "successful edit."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "File path relative to the repository root, or an "
                            "absolute path for files outside the project "
                            "(requires user approval)"
                        ),
                    },
                    "search": {
                        "type": "string",
                        "description": (
                            "The exact text to find in the file, including "
                            "indentation and whitespace"
                        ),
                    },
                    "replace": {
                        "type": "string",
                        "description": "The replacement text",
                    },
                },
                "required": ["path", "search", "replace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the contents of a file with line numbers. "
                "Returns up to 500 lines by default. For large files, "
                "use start_line and end_line to read specific sections. "
                "Can read files outside the project (e.g. log files) "
                "using absolute paths — requires user approval."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "File path relative to the repository root, or an "
                            "absolute path for files outside the project "
                            "(requires user approval)"
                        ),
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "First line to read (1-based).",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line to read (1-based, inclusive).",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run a test command to verify changes work correctly.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The test command to execute (e.g. 'pytest tests/ -v')",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_lint",
            "description": "Run a linting command to check code quality.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The lint command to execute (e.g. 'ruff check src/')",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "format_code",
            "description": "Run a code formatter.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The format command to execute (e.g. 'ruff format src/')",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run a general-purpose shell command such as a build command, "
                "database migration, code generator, package install, or "
                "asset compilation. Do NOT use this for testing (use run_tests), "
                "linting (use run_lint), or formatting (use format_code). "
                "Destructive commands (rm, dd, chmod, git push, etc.) require "
                "user approval; other commands execute immediately.\n\n"
                "IMPORTANT:\n"
                "- Commands run non-interactively (no stdin). Commands that "
                "prompt for input will hang — use flags like --yes or -y.\n"
                "- Output over ~2000 chars is saved to a file. You will receive "
                "a tail preview and a file path — use read_file on that path "
                "to see the full output before diagnosing failures.\n"
                "- Failed commands include the exit code in the result."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "The shell command to execute "
                            "(e.g. 'npm run build', 'cargo build', "
                            "'python manage.py migrate')"
                        ),
                    },
                    "working_directory": {
                        "type": "string",
                        "description": (
                            "Directory to run the command in, relative to the "
                            "repository root. Defaults to the repository root "
                            "if omitted."
                        ),
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": (
                "List files and subdirectories in a directory. "
                "Returns up to max_entries entries (default 100). "
                "If truncated, increase max_entries or list a subdirectory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Directory path relative to the repository root. "
                            "Empty string or omit for the repository root."
                        ),
                    },
                    "max_entries": {
                        "type": "integer",
                        "description": "Maximum number of entries to return (default 100).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "directory_tree",
            "description": (
                "Show the recursive file tree of the repository or a subtree. "
                "Returns up to 200 entries at max_depth 3 by default. "
                "If truncated, use path to focus on a subtree."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Subtree path relative to the repository root. "
                            "Empty string or omit for the full repository tree."
                        ),
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Maximum directory depth to recurse (default 3).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_files",
            "description": (
                "Search for a text pattern across all files in the repository. "
                "Returns matching file paths with line numbers and matching lines. "
                "Respects .gitignore. Use this to find all references to a class, "
                "function, variable, route, or any string across the codebase. "
                "Essential for tracing where models, components, or data are used."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": (
                            "Text or pattern to search for (case-insensitive). "
                            "Examples: 'Customer', 'class Customer', "
                            "'$customer->', 'customer.index'"
                        ),
                    },
                    "file_glob": {
                        "type": "string",
                        "description": (
                            "Optional glob to filter files. "
                            "Examples: '*.php', '*.blade.php', '*.py'. "
                            "Omit to search all files."
                        ),
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_scratchpad",
            "description": (
                "Save your progress so you don't lose track of completed work. "
                "As conversations grow long, earlier messages may be evicted "
                "from context — the scratchpad is your only persistent memory "
                "across turns. It is automatically included in periodic task "
                "reminders so you can recall what you have done and what to "
                "do next.\n\n"
                "Call this after completing each logical step. Write the ENTIRE "
                "scratchpad content (previous content is overwritten, not "
                "appended). Use these sections:\n"
                "## Completed — tasks done (DO NOT redo these)\n"
                "## Current State — what works, what's broken, current errors\n"
                "## Cross-File References — route names, middleware aliases, "
                "model-table mappings, config keys that must stay consistent "
                "across files\n"
                "## Files Modified — files changed this session\n"
                "## Next Step — what to do next\n\n"
                "Keep it concise (under 2000 chars)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": (
                            "The complete scratchpad content with structured "
                            "sections. Overwrites previous content."
                        ),
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_journal_entry",
            "description": (
                "Record a permanent milestone, key finding, or decision that "
                "must survive context refresh. Unlike the scratchpad (volatile "
                "working memory you overwrite each time), journal entries are "
                "append-only and never lost.\n\n"
                "WHEN TO JOURNAL:\n"
                "- A key architectural decision or discovery\n"
                "- A dependency or constraint found during investigation\n"
                "- Completion of a significant milestone\n"
                "- A cross-file relationship that must not be forgotten\n"
                "- An error pattern and its root cause\n\n"
                "Keep each entry to 1-2 sentences. The journal has a "
                "separate budget from the scratchpad."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": (
                            "A concise entry (1-2 sentences) describing "
                            "a milestone, finding, or decision."
                        ),
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_internet",
            "description": (
                "Search the web for information on a topic. "
                "Returns summarized search results. Use this to research "
                "error messages, API documentation, library usage, "
                "best practices, or any external information needed "
                "to complete or unblock the task."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": (
                "Fetch the content of a URL and return it as text. "
                "Use this to read documentation pages, blog posts, "
                "Stack Overflow answers, or other web resources found "
                "via search_internet. Long pages (over 500 lines) are "
                "saved to a file — the output will include the first "
                "500 lines and a read_file command to continue reading."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_complete",
            "description": (
                "Signal that you have finished all work for this task. "
                "Call this tool ONLY when all changes have been made, "
                "verified, and no further tool calls are needed. "
                "Provide a brief summary of what was accomplished."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Brief summary of what was accomplished.",
                    },
                },
                "required": ["summary"],
            },
        },
    },
]


# ── MediaWiki tools ──────────────────────────────────────────────────────────

WIKI_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_wiki",
            "description": (
                "Search the internal wiki for information. Returns matching "
                "page titles and snippets. Use this to find internal documentation, "
                "architecture decisions, runbooks, API docs, or team knowledge "
                "that may help with the current task."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_wiki_page",
            "description": (
                "Fetch the full content of an internal wiki page by title. "
                "Use after search_wiki to read a specific page. Long pages "
                "are saved to a file — use read_file to continue reading."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "The wiki page title",
                    },
                },
                "required": ["title"],
            },
        },
    },
]


def build_implementation_tools() -> list[dict]:
    """IMPLEMENTATION_TOOLS + wiki tools when configured."""
    from lean_ai.config import settings

    tools = list(IMPLEMENTATION_TOOLS)
    if settings.wiki_url:
        tools.extend(WIKI_TOOLS)
    return tools


# ── TDD-specific tool ────────────────────────────────────────────────────────

REQUEST_TEST_CHANGE_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "request_test_change",
        "description": (
            "Dispute a test that you believe is flawed. Provide the test file "
            "path and a specific, programmatic reason why the test is incorrect "
            "(e.g., wrong assertion, tests an implementation detail rather than "
            "behavior, impossible precondition, tests something out of scope). "
            "The expert model will evaluate your dispute and either fix the test "
            "or reject the dispute with an explanation. "
            "Do NOT use this tool simply because a test is hard to pass — only "
            "when the test itself has a genuine defect."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "test_file": {
                    "type": "string",
                    "description": "Path to the test file containing the flawed test",
                },
                "test_function": {
                    "type": "string",
                    "description": "Name of the specific test function being disputed",
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "Specific, programmatic reason the test is flawed. "
                        "Must reference concrete code behavior. Examples: "
                        "'asserts return type is list but the API returns a generator', "
                        "'tests a private method that was renamed in the plan', "
                        "'import path does not match the module structure'"
                    ),
                },
            },
            "required": ["test_file", "test_function", "reason"],
        },
    },
}


def build_tdd_implementation_tools() -> list[dict]:
    """IMPLEMENTATION_TOOLS + request_test_change + wiki tools for TDD mode."""
    return build_implementation_tools() + [REQUEST_TEST_CHANGE_TOOL]


def _maybe_wiki_tools() -> list[dict]:
    """Return wiki tools if MediaWiki is configured, else empty list."""
    from lean_ai.config import settings

    return list(WIKI_TOOLS) if settings.wiki_url else []


# Read-only tools for planning phases
PLANNING_TOOLS: list[dict] = [
    tool
    for tool in IMPLEMENTATION_TOOLS
    if tool["function"]["name"] in (
        "read_file", "list_directory", "directory_tree", "grep_files", "task_complete",
    )
]


def build_planning_tools() -> list[dict]:
    """PLANNING_TOOLS + search tools + wiki tools when configured."""
    search_tools = [
        tool
        for tool in IMPLEMENTATION_TOOLS
        if tool["function"]["name"] in ("search_internet", "fetch_url")
    ]
    return PLANNING_TOOLS + search_tools + _maybe_wiki_tools()


def build_planning_tools_with_scratchpad() -> list[dict]:
    """Planning tools + update_scratchpad + add_journal_entry for long serial explorations."""
    memory_tools = [
        t for t in IMPLEMENTATION_TOOLS
        if t["function"]["name"] in ("update_scratchpad", "add_journal_entry")
    ]
    return build_planning_tools() + memory_tools


# Search + task_complete tools for Phase 3 design synthesis
DESIGN_TOOLS: list[dict] = [
    tool
    for tool in IMPLEMENTATION_TOOLS
    if tool["function"]["name"] in ("search_internet", "fetch_url", "task_complete")
]


def build_design_tools() -> list[dict]:
    """Search + task_complete + wiki tools for Phase 3 design synthesis."""
    return DESIGN_TOOLS + _maybe_wiki_tools()


# Read-only tools for chat exploration (no task_complete — text exit)
CHAT_TOOLS: list[dict] = [
    tool
    for tool in IMPLEMENTATION_TOOLS
    if tool["function"]["name"] in (
        "read_file", "list_directory", "directory_tree", "grep_files",
        "search_internet", "fetch_url",
    )
] + [
    {
        "type": "function",
        "function": {
            "name": "save_note",
            "description": (
                "Save a note for the user. Use when the user asks to take a note, "
                "remember something, or jot something down."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The note content to save",
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_project_todos",
            "description": (
                "List TODOs for the current project. Use when the user asks about "
                "their todos, tasks, or what they need to work on."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": (
                            "Project name to filter by. "
                            "Leave empty to use the current workspace."
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_recent_sessions",
            "description": (
                "List recent coding sessions for this workspace. Shows what "
                "was worked on, status, and when. Use when the user asks about "
                "past work or what they've been doing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max sessions to return (default 5).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_session_summary",
            "description": (
                "Get a detailed summary of a specific session, including task, "
                "files changed, tool usage, commits, and outcome."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "The session ID to summarize.",
                    },
                },
                "required": ["session_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_workspace_memory",
            "description": (
                "Search memories and lessons learned from previous sessions. "
                "Use when the user asks about past work or when you need "
                "historical context about the project."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query for memories.",
                    },
                },
                "required": ["query"],
            },
        },
    },
]

def build_chat_tools() -> list[dict]:
    """CHAT_TOOLS + wiki tools when configured."""
    return CHAT_TOOLS + _maybe_wiki_tools()


# Read-only + diagnostic tools for fix-mode investigation phase
INVESTIGATION_TOOLS: list[dict] = [
    tool
    for tool in IMPLEMENTATION_TOOLS
    if tool["function"]["name"] in (
        "read_file", "list_directory", "directory_tree", "grep_files",
        "run_tests", "run_lint",
        "search_internet", "fetch_url",
        "update_scratchpad", "add_journal_entry", "task_complete",
    )
]


def build_investigation_tools() -> list[dict]:
    """INVESTIGATION_TOOLS + wiki tools when configured."""
    return INVESTIGATION_TOOLS + _maybe_wiki_tools()

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
                        "description": "File path relative to the repository root",
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
                "Use multiple edit_file calls for multiple changes in the same file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to the repository root",
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
                "use start_line and end_line to read specific sections."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to the repository root",
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
                "Update the session scratchpad to track your progress. Call this "
                "after completing each logical step. Write the ENTIRE scratchpad "
                "content (previous content is overwritten, not appended). "
                "Use these sections:\n"
                "## Completed — tasks done (DO NOT redo these)\n"
                "## Current State — what works, what's broken, current errors\n"
                "## Cross-File References — route names, middleware aliases, "
                "model-table mappings, config keys that must stay consistent "
                "across files\n"
                "## Files Modified — files changed this session\n"
                "## Next Step — what to do next\n\n"
                "Keep it concise (under 2000 chars). This scratchpad is "
                "injected into your context periodically so you remember "
                "what you have done."
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
]


# Read-only tools for planning phases
PLANNING_TOOLS: list[dict] = [
    tool
    for tool in IMPLEMENTATION_TOOLS
    if tool["function"]["name"] in ("read_file", "list_directory", "directory_tree", "grep_files")
]

"""Human-friendly descriptions for tool invocations.

Used by both the WebSocket workflow callbacks and the chat SSE endpoint
to produce concise, user-facing progress messages.
"""


def humanize_tool_call(name: str, args: dict) -> str:
    """Return a concise, user-facing description of a tool invocation."""
    if name == "read_file":
        return f"Reading {args.get('path', '...')}"
    if name == "grep_files":
        return f"Searching for '{args.get('pattern', '...')}'"
    if name == "list_directory":
        return f"Listing {args.get('path', '') or '.'}"
    if name == "directory_tree":
        return f"Tree of {args.get('path', '') or '.'}"
    if name == "create_file":
        return f"Creating {args.get('path', '...')}"
    if name == "edit_file":
        return f"Editing {args.get('path', '...')}"
    if name == "run_command":
        cmd = args.get("command", "...")
        return f"Running: {cmd[:60]}{'…' if len(cmd) > 60 else ''}"
    if name == "run_tests":
        return "Running tests"
    if name == "run_lint":
        return "Running lint"
    if name == "format_code":
        return "Formatting code"
    if name == "search_internet":
        return f"Searching: {args.get('query', '...')}"
    if name == "fetch_url":
        return f"Fetching {args.get('url', '...')}"
    if name == "search_wiki":
        return f"Wiki search: {args.get('query', '...')}"
    if name == "fetch_wiki_page":
        return f"Fetching wiki: {args.get('title', '...')}"
    if name == "update_scratchpad":
        return "Updating scratchpad"
    if name == "add_journal_entry":
        return "Recording journal entry"
    if name == "task_complete":
        return "Task complete"
    if name == "request_test_change":
        return f"Requesting test change: {args.get('test_file', '...')}"
    if name == "save_note":
        return "Saving note"
    if name == "list_project_todos":
        return "Listing project todos"
    if name == "list_recent_sessions":
        return "Listing recent sessions"
    if name == "get_session_summary":
        return "Getting session summary"
    if name == "search_workspace_memory":
        return f"Searching memory: {args.get('query', '...')}"
    if name == "query_project_context":
        parts = []
        if args.get("section"):
            parts.append(args["section"])
        if args.get("file_path"):
            parts.append(args["file_path"])
        if args.get("keyword"):
            parts.append(f"'{args['keyword']}'")
        detail = ", ".join(parts) if parts else "all"
        return f"Querying context: {detail}"
    # Fallback
    path = args.get("path", args.get("command", ""))
    return f"{name} {path}".strip()

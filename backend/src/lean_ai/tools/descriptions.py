"""Human-friendly descriptions for tool invocations.

Used by both the WebSocket workflow callbacks and the chat SSE endpoint
to produce concise, user-facing progress messages.
"""


def _string_arg(args: dict, key: str, default: str) -> str:
    """Return a safe string value for display-only tool descriptions."""
    value = args.get(key)
    if value is None or value == "":
        return default
    return str(value)


def humanize_tool_call(name: str, args: dict) -> str:
    """Return a concise, user-facing description of a tool invocation."""
    if name == "read_file":
        return f"Reading {_string_arg(args, 'path', '...')} (up to 500 lines)"
    if name == "grep_files":
        return f"Searching for '{_string_arg(args, 'pattern', '...')}'"
    if name == "list_directory":
        return f"Listing {_string_arg(args, 'path', '.')}"
    if name == "directory_tree":
        return f"Tree of {_string_arg(args, 'path', '.')}"
    if name == "create_file":
        return f"Creating {_string_arg(args, 'path', '...')}"
    if name == "edit_file":
        return f"Editing {_string_arg(args, 'path', '...')}"
    if name == "run_command":
        cmd = _string_arg(args, "command", "...")
        return f"Running: {cmd[:60]}{'…' if len(cmd) > 60 else ''}"
    if name == "run_tests":
        return "Running tests"
    if name == "run_lint":
        return "Running lint"
    if name == "format_code":
        return "Formatting code"
    if name == "search_internet":
        return f"Searching: {_string_arg(args, 'query', '...')}"
    if name == "fetch_url":
        return f"Fetching {_string_arg(args, 'url', '...')}"
    if name == "search_reference":
        return f"Searching reference library: {_string_arg(args, 'query', '...')}"
    if name == "list_reference_documents":
        return "Listing reference library documents"
    if name == "search_wiki":
        return f"Wiki search: {_string_arg(args, 'query', '...')}"
    if name == "fetch_wiki_page":
        return f"Fetching wiki: {_string_arg(args, 'title', '...')}"
    if name == "update_scratchpad":
        return "Updating scratchpad"
    if name == "add_journal_entry":
        return "Recording journal entry"
    if name == "task_complete":
        return "Task complete"
    if name == "request_test_change":
        return f"Requesting test change: {_string_arg(args, 'test_file', '...')}"
    if name == "save_note":
        return "Saving note"
    if name == "list_project_todos":
        return "Listing project todos"
    if name == "list_recent_sessions":
        return "Listing recent sessions"
    if name == "get_session_summary":
        return "Getting session summary"
    if name == "search_workspace_memory":
        return f"Searching memory: {_string_arg(args, 'query', '...')}"
    if name == "search_architecture_decisions":
        return f"Searching architecture decisions: {_string_arg(args, 'query', '...')}"
    if name == "get_architecture_decision":
        return f"Loading architecture decision {_string_arg(args, 'decision_id', '...')}"
    if name == "record_architecture_decision":
        return f"Recording architecture decision: {_string_arg(args, 'title', '...')}"
    if name == "query_project_context":
        parts = []
        if args.get("section"):
            parts.append(str(args["section"]))
        if args.get("file_path"):
            parts.append(str(args["file_path"]))
        if args.get("keyword"):
            parts.append(f"'{args['keyword']}'")
        detail = ", ".join(parts) if parts else "all"
        return f"Querying context: {detail}"
    # Fallback
    path = _string_arg(args, "path", _string_arg(args, "command", ""))
    return f"{name} {path}".strip()

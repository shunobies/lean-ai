/**
 * Self-contained HTML template for the Notes & TODOs panel.
 */
export function getNotesPanelHtml(): string {
    return /*html*/ `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
        font-family: var(--vscode-font-family);
        font-size: var(--vscode-font-size);
        color: var(--vscode-foreground);
        background: var(--vscode-editor-background);
        display: flex;
        flex-direction: column;
        height: 100vh;
        overflow: hidden;
    }

    /* ── Header ─────────────────────────────── */
    .panel-header {
        padding: 16px 20px 12px;
        border-bottom: 1px solid var(--vscode-panel-border);
        flex-shrink: 0;
    }
    .panel-header h1 {
        font-size: 16px;
        font-weight: 600;
        margin-bottom: 4px;
    }
    .panel-header p {
        font-size: 12px;
        opacity: 0.7;
    }
    .header-actions {
        display: flex;
        gap: 8px;
        margin-top: 8px;
    }

    /* ── Search ──────────────────────────────── */
    .search-bar {
        padding: 8px 20px;
        border-bottom: 1px solid var(--vscode-panel-border);
        flex-shrink: 0;
        display: flex;
        gap: 8px;
    }
    .search-bar input {
        flex: 1;
        padding: 6px 10px;
        border: 1px solid var(--vscode-input-border);
        background: var(--vscode-input-background);
        color: var(--vscode-input-foreground);
        font-family: inherit;
        font-size: 12px;
        border-radius: 3px;
        outline: none;
    }
    .search-bar input:focus {
        border-color: var(--vscode-focusBorder);
    }

    /* ── Layout ──────────────────────────────── */
    .main-layout {
        display: flex;
        flex: 1;
        overflow: hidden;
    }

    /* ── Project nav ─────────────────────────── */
    .project-nav {
        width: 160px;
        min-width: 130px;
        border-right: 1px solid var(--vscode-panel-border);
        padding: 8px 0;
        overflow-y: auto;
        flex-shrink: 0;
    }
    .project-btn {
        display: block;
        width: 100%;
        text-align: left;
        padding: 6px 14px;
        border: none;
        background: none;
        color: var(--vscode-foreground);
        font-family: inherit;
        font-size: 12px;
        cursor: pointer;
        opacity: 0.7;
    }
    .project-btn:hover { opacity: 1; background: var(--vscode-list-hoverBackground); }
    .project-btn.active {
        opacity: 1;
        background: var(--vscode-list-activeSelectionBackground);
        color: var(--vscode-list-activeSelectionForeground);
    }
    .project-count {
        font-size: 10px;
        opacity: 0.6;
        margin-left: 4px;
    }

    /* ── Content ──────────────────────────────── */
    .content-area {
        flex: 1;
        overflow-y: auto;
        padding: 16px 20px;
    }

    /* ── Note card ───────────────────────────── */
    .note-card {
        border: 1px solid var(--vscode-panel-border);
        border-radius: 4px;
        margin-bottom: 12px;
        overflow: hidden;
    }
    .note-header {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 10px 14px;
        background: var(--vscode-sideBar-background);
        cursor: pointer;
    }
    .note-header:hover {
        background: var(--vscode-list-hoverBackground);
    }
    .note-project-badge {
        font-size: 10px;
        padding: 2px 6px;
        border-radius: 3px;
        background: var(--vscode-badge-background);
        color: var(--vscode-badge-foreground);
        white-space: nowrap;
    }
    .note-date {
        font-size: 10px;
        opacity: 0.5;
        margin-left: auto;
        white-space: nowrap;
    }
    .note-expand-icon {
        font-size: 10px;
        opacity: 0.5;
    }
    .note-preview {
        font-size: 12px;
        opacity: 0.8;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        flex: 1;
        min-width: 0;
    }
    .note-body {
        display: none;
        padding: 12px 14px;
        border-top: 1px solid var(--vscode-panel-border);
    }
    .note-body.expanded {
        display: block;
    }
    .note-content {
        font-size: 13px;
        line-height: 1.5;
        margin-bottom: 12px;
        white-space: pre-wrap;
        word-break: break-word;
    }
    .note-tags {
        display: flex;
        flex-wrap: wrap;
        gap: 4px;
        margin-bottom: 10px;
    }
    .note-tag {
        font-size: 10px;
        padding: 1px 5px;
        border-radius: 3px;
        background: var(--vscode-textCodeBlock-background);
        opacity: 0.8;
    }

    /* ── TODOs ───────────────────────────────── */
    .todo-section {
        margin-top: 8px;
        padding-top: 8px;
        border-top: 1px solid var(--vscode-panel-border);
    }
    .todo-section-title {
        font-size: 11px;
        font-weight: 600;
        opacity: 0.7;
        margin-bottom: 6px;
    }
    .todo-item {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 4px 0;
        font-size: 12px;
    }
    .todo-item input[type="checkbox"] {
        cursor: pointer;
    }
    .todo-item.completed .todo-text {
        text-decoration: line-through;
        opacity: 0.5;
    }
    .todo-text {
        flex: 1;
    }
    .todo-delete-btn {
        background: none;
        border: none;
        color: var(--vscode-errorForeground);
        cursor: pointer;
        font-size: 11px;
        opacity: 0;
        padding: 2px 4px;
    }
    .todo-item:hover .todo-delete-btn {
        opacity: 0.7;
    }
    .todo-delete-btn:hover {
        opacity: 1 !important;
    }
    .add-todo-row {
        display: flex;
        gap: 6px;
        margin-top: 6px;
    }
    .add-todo-row input {
        flex: 1;
        padding: 4px 8px;
        border: 1px solid var(--vscode-input-border);
        background: var(--vscode-input-background);
        color: var(--vscode-input-foreground);
        font-family: inherit;
        font-size: 11px;
        border-radius: 3px;
        outline: none;
    }
    .add-todo-row input:focus {
        border-color: var(--vscode-focusBorder);
    }

    /* ── Note actions ────────────────────────── */
    .note-actions {
        display: flex;
        gap: 8px;
        margin-top: 10px;
        padding-top: 8px;
        border-top: 1px solid var(--vscode-panel-border);
    }

    /* ── Buttons ─────────────────────────────── */
    .btn {
        padding: 4px 10px;
        border: 1px solid var(--vscode-button-border, transparent);
        border-radius: 3px;
        font-family: inherit;
        font-size: 11px;
        cursor: pointer;
    }
    .btn-primary {
        background: var(--vscode-button-background);
        color: var(--vscode-button-foreground);
    }
    .btn-primary:hover {
        background: var(--vscode-button-hoverBackground);
    }
    .btn-secondary {
        background: var(--vscode-button-secondaryBackground);
        color: var(--vscode-button-secondaryForeground);
    }
    .btn-secondary:hover {
        background: var(--vscode-button-secondaryHoverBackground);
    }
    .btn-danger {
        background: none;
        color: var(--vscode-errorForeground);
        border-color: var(--vscode-errorForeground);
    }
    .btn-danger:hover {
        background: var(--vscode-errorForeground);
        color: var(--vscode-editor-background);
    }

    /* ── Inline edit ─────────────────────────── */
    .edit-field {
        width: 100%;
        padding: 6px 8px;
        border: 1px solid var(--vscode-input-border);
        background: var(--vscode-input-background);
        color: var(--vscode-input-foreground);
        font-family: inherit;
        font-size: 12px;
        border-radius: 3px;
        outline: none;
        resize: vertical;
        min-height: 60px;
    }
    .edit-field:focus {
        border-color: var(--vscode-focusBorder);
    }

    /* ── Empty state ─────────────────────────── */
    .empty-state {
        text-align: center;
        padding: 40px 20px;
        opacity: 0.6;
    }
    .empty-state p {
        margin-bottom: 8px;
    }

    /* ── New note form ────────────────────────── */
    .new-note-form {
        padding: 12px 20px;
        border-bottom: 1px solid var(--vscode-panel-border);
        display: none;
        flex-shrink: 0;
    }
    .new-note-form.visible {
        display: block;
    }
    .new-note-form textarea {
        width: 100%;
        padding: 8px 10px;
        border: 1px solid var(--vscode-input-border);
        background: var(--vscode-input-background);
        color: var(--vscode-input-foreground);
        font-family: inherit;
        font-size: 12px;
        border-radius: 3px;
        outline: none;
        resize: vertical;
        min-height: 60px;
        margin-bottom: 8px;
    }
    .new-note-form textarea:focus {
        border-color: var(--vscode-focusBorder);
    }
</style>
</head>
<body>
    <div class="panel-header">
        <h1>Notes & TODOs</h1>
        <p>Global notes across all projects with AI-powered categorization</p>
        <div class="header-actions">
            <button class="btn btn-primary" onclick="toggleNewNote()">+ New Note</button>
            <button class="btn btn-secondary" onclick="refreshNotes()">Refresh</button>
        </div>
    </div>

    <div class="new-note-form" id="newNoteForm">
        <textarea id="newNoteContent" placeholder="Write your note here..."></textarea>
        <div style="display:flex;gap:8px;">
            <button class="btn btn-primary" onclick="saveNewNote()">Save Note</button>
            <button class="btn btn-secondary" onclick="toggleNewNote()">Cancel</button>
        </div>
    </div>

    <div class="search-bar">
        <input type="text" id="searchInput" placeholder="Search notes..." />
        <button class="btn btn-secondary" onclick="searchNotes()">Search</button>
        <button class="btn btn-secondary" onclick="clearSearch()">Clear</button>
    </div>

    <div class="main-layout">
        <div class="project-nav" id="projectNav">
            <button class="project-btn active" onclick="filterProject(null)">All Notes</button>
        </div>
        <div class="content-area" id="contentArea">
            <div class="empty-state">
                <p>No notes yet.</p>
                <p>Use <code>/note</code> in chat or click "+ New Note" above.</p>
            </div>
        </div>
    </div>

<script>
    const vscode = acquireVsCodeApi();
    let allNotes = [];
    let allProjects = [];
    let activeProject = null;
    let isSearching = false;

    // ── Messaging ──

    window.addEventListener("message", (event) => {
        const msg = event.data;
        switch (msg.type) {
            case "loadNotes":
                allNotes = msg.notes || [];
                allProjects = msg.projects || [];
                activeProject = msg.activeProject || null;
                isSearching = false;
                renderProjectNav();
                renderNotes(allNotes);
                break;
            case "searchResults":
                isSearching = true;
                renderNotes(msg.notes || []);
                break;
        }
    });

    vscode.postMessage({ type: "webviewReady" });

    // ── Project nav ──

    function renderProjectNav() {
        const nav = document.getElementById("projectNav");
        const projectCounts = {};
        allNotes.forEach(n => {
            const p = n.project || "Uncategorized";
            projectCounts[p] = (projectCounts[p] || 0) + 1;
        });

        let html = '<button class="project-btn' + (!activeProject ? ' active' : '') +
            '" data-project="">All Notes <span class="project-count">(' +
            allNotes.length + ')</span></button>';

        allProjects.forEach(p => {
            const count = projectCounts[p] || 0;
            const isActive = activeProject === p;
            html += '<button class="project-btn' + (isActive ? ' active' : '') +
                '" data-project="'+escapeHtml(p)+'">'+escapeHtml(p)+
                ' <span class="project-count">(' + count + ')</span></button>';
        });

        // Uncategorized
        const uncatCount = allNotes.filter(n => !n.project).length;
        if (uncatCount > 0) {
            const isActive = activeProject === "__uncategorized__";
            html += '<button class="project-btn' + (isActive ? ' active' : '') +
                '" data-project="__uncategorized__">Uncategorized' +
                ' <span class="project-count">(' + uncatCount + ')</span></button>';
        }

        nav.innerHTML = html;
        nav.querySelectorAll("[data-project]").forEach(btn => {
            btn.addEventListener("click", () => {
                const project = btn.getAttribute("data-project") || null;
                filterProject(project);
            });
        });
    }

    function filterProject(project) {
        activeProject = project;
        if (project === "__uncategorized__") {
            renderNotes(allNotes.filter(n => !n.project));
            renderProjectNav();
        } else {
            vscode.postMessage({ type: "loadNotes", project: project || undefined });
        }
    }

    // ── Note rendering ──

    function renderNotes(notes) {
        const area = document.getElementById("contentArea");
        if (!notes.length) {
            area.innerHTML = '<div class="empty-state"><p>No notes found.</p>' +
                '<p>Use <code>/note</code> in chat or click "+ New Note" above.</p></div>';
            return;
        }

        let html = "";
        notes.forEach((note, idx) => {
            const preview = (note.content || "").substring(0, 80).replace(/\\n/g, " ");
            const date = note.updated_at ? new Date(note.updated_at).toLocaleDateString() : "";
            const tags = (note.tags || []);
            const todos = (note.todos || []);

            html += '<div class="note-card" id="note-' + note.id + '">';

            // Header (clickable to expand)
            html += '<div class="note-header" onclick="toggleNote(\\''+note.id+'\\')">';
            html += '<span class="note-expand-icon" id="icon-'+note.id+'">&#9654;</span>';
            if (note.project) {
                html += '<span class="note-project-badge">'+escapeHtml(note.project)+'</span>';
            }
            html += '<span class="note-preview">'+escapeHtml(preview)+'</span>';
            html += '<span class="note-date">'+escapeHtml(date)+'</span>';
            html += '</div>';

            // Body (hidden by default)
            html += '<div class="note-body" id="body-'+note.id+'">';

            // Content
            html += '<div class="note-content" id="content-'+note.id+'">'+escapeHtml(note.content)+'</div>';

            // Tags
            if (tags.length) {
                html += '<div class="note-tags">';
                tags.forEach(t => {
                    html += '<span class="note-tag">'+escapeHtml(t)+'</span>';
                });
                html += '</div>';
            }

            // TODOs
            if (todos.length || true) {
                html += '<div class="todo-section">';
                html += '<div class="todo-section-title">TODOs</div>';
                todos.forEach(todo => {
                    const checked = todo.completed ? "checked" : "";
                    const cls = todo.completed ? "todo-item completed" : "todo-item";
                    html += '<div class="'+cls+'">';
                    html += '<input type="checkbox" '+checked+' onchange="toggleTodo('+todo.id+', this.checked)" />';
                    html += '<span class="todo-text">'+escapeHtml(todo.description)+'</span>';
                    html += '<button class="todo-delete-btn" onclick="deleteTodo('+todo.id+')" title="Delete">x</button>';
                    html += '</div>';
                });
                // Add todo input
                html += '<div class="add-todo-row">';
                html += '<input type="text" placeholder="Add a TODO..." id="todo-input-'+note.id+'" ';
                html += 'onkeydown="if(event.key===\\'Enter\\')addTodo(\\''+note.id+'\\')" />';
                html += '<button class="btn btn-secondary" onclick="addTodo(\\''+note.id+'\\')">Add</button>';
                html += '</div>';
                html += '</div>';
            }

            // Actions
            html += '<div class="note-actions">';
            html += '<button class="btn btn-secondary" onclick="editNote(\\''+note.id+'\\')">Edit</button>';
            html += '<button class="btn btn-danger" onclick="deleteNote(\\''+note.id+'\\')">Delete</button>';
            html += '</div>';

            html += '</div>'; // note-body
            html += '</div>'; // note-card
        });

        area.innerHTML = html;
    }

    // ── Interactions ──

    function toggleNote(noteId) {
        const body = document.getElementById("body-" + noteId);
        const icon = document.getElementById("icon-" + noteId);
        if (body.classList.contains("expanded")) {
            body.classList.remove("expanded");
            icon.innerHTML = "&#9654;";
        } else {
            body.classList.add("expanded");
            icon.innerHTML = "&#9660;";
        }
    }

    function toggleTodo(todoId, completed) {
        vscode.postMessage({ type: "toggleTodo", todoId, completed });
    }

    function deleteTodo(todoId) {
        vscode.postMessage({ type: "deleteTodo", todoId });
    }

    function addTodo(noteId) {
        const input = document.getElementById("todo-input-" + noteId);
        const description = input.value.trim();
        if (!description) return;
        vscode.postMessage({ type: "addTodo", noteId, description });
        input.value = "";
    }

    function editNote(noteId) {
        const contentEl = document.getElementById("content-" + noteId);
        const currentContent = contentEl.textContent;

        contentEl.innerHTML = '<textarea class="edit-field" id="edit-'+noteId+'">'+escapeHtml(currentContent)+'</textarea>' +
            '<div style="display:flex;gap:8px;margin-top:8px;">' +
            '<button class="btn btn-primary" onclick="saveEdit(\\''+noteId+'\\')">Save</button>' +
            '<button class="btn btn-secondary" onclick="refreshNotes()">Cancel</button>' +
            '</div>';

        document.getElementById("edit-" + noteId).focus();
    }

    function saveEdit(noteId) {
        const textarea = document.getElementById("edit-" + noteId);
        const content = textarea.value;
        vscode.postMessage({ type: "updateNote", noteId, data: { content } });
    }

    function deleteNote(noteId) {
        vscode.postMessage({ type: "deleteNote", noteId });
    }

    function searchNotes() {
        const query = document.getElementById("searchInput").value.trim();
        if (!query) return;
        vscode.postMessage({ type: "searchNotes", query });
    }

    function clearSearch() {
        document.getElementById("searchInput").value = "";
        isSearching = false;
        refreshNotes();
    }

    function refreshNotes() {
        vscode.postMessage({ type: "loadNotes" });
    }

    function toggleNewNote() {
        const form = document.getElementById("newNoteForm");
        form.classList.toggle("visible");
        if (form.classList.contains("visible")) {
            document.getElementById("newNoteContent").focus();
        }
    }

    function saveNewNote() {
        const textarea = document.getElementById("newNoteContent");
        const content = textarea.value.trim();
        if (!content) return;
        vscode.postMessage({ type: "createNote", content });
        textarea.value = "";
        document.getElementById("newNoteForm").classList.remove("visible");
    }

    // Search on Enter key
    document.getElementById("searchInput").addEventListener("keydown", (e) => {
        if (e.key === "Enter") searchNotes();
    });

    // ── Helpers ──

    function escapeHtml(text) {
        if (!text) return "";
        const div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }
</script>
</body>
</html>`;
}

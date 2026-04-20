/**
 * Self-contained HTML template for the Memories panel.
 */
export function getMemoriesPanelHtml(): string {
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
    .panel-header {
        padding: 14px 20px 10px;
        border-bottom: 1px solid var(--vscode-panel-border);
        flex-shrink: 0;
    }
    .panel-header h1 {
        font-size: 16px;
        font-weight: 600;
        margin-bottom: 2px;
    }
    .panel-header p {
        font-size: 12px;
        opacity: 0.7;
    }

    .tabs {
        display: flex;
        gap: 2px;
        padding: 0 20px;
        border-bottom: 1px solid var(--vscode-panel-border);
        flex-shrink: 0;
    }
    .tab {
        padding: 10px 14px;
        border: none;
        background: none;
        color: var(--vscode-foreground);
        font-family: inherit;
        font-size: 12px;
        cursor: pointer;
        opacity: 0.65;
        border-bottom: 2px solid transparent;
    }
    .tab.active {
        opacity: 1;
        border-bottom-color: var(--vscode-focusBorder);
    }
    .tab:hover { opacity: 1; }
    .tab .count {
        display: inline-block;
        padding: 0 5px;
        margin-left: 4px;
        border-radius: 8px;
        background: var(--vscode-badge-background);
        color: var(--vscode-badge-foreground);
        font-size: 10px;
    }

    .search-bar {
        padding: 8px 20px;
        border-bottom: 1px solid var(--vscode-panel-border);
        flex-shrink: 0;
    }
    .search-bar input {
        width: 100%;
        padding: 6px 10px;
        border: 1px solid var(--vscode-input-border);
        background: var(--vscode-input-background);
        color: var(--vscode-input-foreground);
        font-family: inherit;
        font-size: 12px;
        border-radius: 3px;
        outline: none;
    }
    .search-bar input:focus { border-color: var(--vscode-focusBorder); }

    .content { flex: 1; overflow-y: auto; padding: 12px 20px 20px; }

    .memory-card {
        border: 1px solid var(--vscode-panel-border);
        border-radius: 4px;
        padding: 12px;
        margin-bottom: 10px;
        background: var(--vscode-editorWidget-background);
    }
    .memory-head {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 6px;
        flex-wrap: wrap;
    }
    .chip {
        display: inline-block;
        padding: 1px 7px;
        border-radius: 10px;
        font-size: 10px;
        background: var(--vscode-badge-background);
        color: var(--vscode-badge-foreground);
        text-transform: lowercase;
    }
    .chip.phase { background: var(--vscode-editorInfo-foreground); color: var(--vscode-editor-background); opacity: 0.75; }
    .chip.confidence { background: transparent; color: var(--vscode-descriptionForeground); border: 1px solid var(--vscode-panel-border); }
    .memory-body { font-size: 12px; line-height: 1.4; white-space: pre-wrap; word-wrap: break-word; }
    .memory-meta { margin-top: 6px; font-size: 10px; opacity: 0.55; }
    .memory-tags { margin-top: 6px; display: flex; gap: 4px; flex-wrap: wrap; }
    .memory-tag { font-size: 10px; opacity: 0.7; }

    .memory-actions {
        margin-top: 10px;
        display: flex;
        gap: 6px;
    }
    .memory-actions button {
        padding: 4px 10px;
        border: 1px solid var(--vscode-button-border, transparent);
        border-radius: 3px;
        font-family: inherit;
        font-size: 11px;
        cursor: pointer;
    }
    .btn-confirm {
        background: var(--vscode-button-background);
        color: var(--vscode-button-foreground);
    }
    .btn-confirm:hover { background: var(--vscode-button-hoverBackground); }
    .btn-reject, .btn-delete {
        background: transparent;
        color: var(--vscode-foreground);
    }
    .btn-reject:hover, .btn-delete:hover { background: var(--vscode-list-hoverBackground); }
    .btn-delete { color: var(--vscode-errorForeground); }

    .empty-state {
        padding: 32px 12px;
        text-align: center;
        opacity: 0.6;
        font-size: 12px;
    }

    .manual-save {
        border-top: 1px solid var(--vscode-panel-border);
        padding: 12px 20px;
        background: var(--vscode-editor-background);
    }
    .manual-save.collapsed .manual-save-body { display: none; }
    .manual-save-toggle {
        background: none;
        border: none;
        color: var(--vscode-textLink-foreground);
        font-family: inherit;
        font-size: 12px;
        cursor: pointer;
        padding: 0;
    }
    .manual-save-body { margin-top: 10px; display: flex; flex-direction: column; gap: 6px; }
    .manual-save-body input, .manual-save-body select, .manual-save-body textarea {
        padding: 6px 10px;
        border: 1px solid var(--vscode-input-border);
        background: var(--vscode-input-background);
        color: var(--vscode-input-foreground);
        font-family: inherit;
        font-size: 12px;
        border-radius: 3px;
        outline: none;
    }
    .manual-save-body textarea { min-height: 60px; resize: vertical; }
    .manual-save-body .row { display: flex; gap: 6px; }
    .manual-save-body .row > * { flex: 1; }
    .manual-save-body button {
        align-self: flex-start;
        padding: 5px 12px;
        border: none;
        border-radius: 3px;
        background: var(--vscode-button-background);
        color: var(--vscode-button-foreground);
        font-family: inherit;
        font-size: 12px;
        cursor: pointer;
    }
    .manual-save-body button:hover { background: var(--vscode-button-hoverBackground); }
</style>
</head>
<body>
    <div class="panel-header">
        <h1>Memories</h1>
        <p>Curate cross-session memories — what worked, what didn't, and what the planner should consult next time.</p>
    </div>
    <div class="tabs">
        <button class="tab active" data-tab="pending">Pending Review <span class="count" id="count-pending">0</span></button>
        <button class="tab" data-tab="confirmed">Confirmed <span class="count" id="count-confirmed">0</span></button>
        <button class="tab" data-tab="archived">Archive <span class="count" id="count-archived">0</span></button>
    </div>
    <div class="search-bar"><input id="search" type="text" placeholder="Filter loaded memories..." /></div>
    <div class="content" id="content"></div>
    <div class="manual-save collapsed" id="manualSave">
        <button class="manual-save-toggle" id="manualSaveToggle">+ Add a memory manually</button>
        <div class="manual-save-body">
            <div class="row">
                <select id="m-category">
                    <option value="convention">convention</option>
                    <option value="pattern">pattern</option>
                    <option value="gotcha">gotcha</option>
                    <option value="architecture">architecture</option>
                    <option value="build">build</option>
                    <option value="testing">testing</option>
                    <option value="fix_pattern">fix_pattern</option>
                    <option value="rejection">rejection</option>
                    <option value="success_pattern">success_pattern</option>
                    <option value="discovery">discovery</option>
                </select>
                <input id="m-tags" type="text" placeholder="tags (comma separated)" />
            </div>
            <textarea id="m-content" placeholder="Memory content (1-3 sentences)"></textarea>
            <button id="m-save">Save memory</button>
        </div>
    </div>
<script>
    const vscode = acquireVsCodeApi();
    const state = { active: "pending", data: { pending: [], confirmed: [], archived: [] }, filter: "" };

    function escapeHtml(s) {
        return String(s ?? "").replace(/[&<>"']/g, (c) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        })[c]);
    }

    function renderMemory(m, tab) {
        const tags = (m.tags || []).map((t) => '<span class="memory-tag">#' + escapeHtml(t) + '</span>').join(" ");
        const phase = m.source_phase ? '<span class="chip phase">' + escapeHtml(m.source_phase) + '</span>' : "";
        const conf = m.confidence != null ? '<span class="chip confidence">conf ' + (+m.confidence).toFixed(2) + '</span>' : "";
        let actions = "";
        if (tab === "pending") {
            actions = \`
                <button class="btn-confirm" data-action="confirm" data-id="\${m.id}">Confirm</button>
                <button class="btn-reject" data-action="reject" data-id="\${m.id}">Reject</button>
                <button class="btn-delete" data-action="delete" data-id="\${m.id}">Delete</button>
            \`;
        } else if (tab === "confirmed") {
            actions = \`
                <button class="btn-reject" data-action="reject" data-id="\${m.id}">Move to archive</button>
                <button class="btn-delete" data-action="delete" data-id="\${m.id}">Delete</button>
            \`;
        } else {
            actions = \`
                <button class="btn-confirm" data-action="confirm" data-id="\${m.id}">Restore</button>
                <button class="btn-delete" data-action="delete" data-id="\${m.id}">Delete</button>
            \`;
        }
        return \`
            <div class="memory-card" data-id="\${m.id}">
                <div class="memory-head">
                    <span class="chip">\${escapeHtml(m.category || "general")}</span>
                    \${phase}
                    \${conf}
                </div>
                <div class="memory-body">\${escapeHtml(m.content || "")}</div>
                \${tags ? '<div class="memory-tags">' + tags + '</div>' : ""}
                <div class="memory-meta">\${escapeHtml(m.created_at || "")}\${m.model_name ? " · " + escapeHtml(m.model_name) : ""}</div>
                <div class="memory-actions">\${actions}</div>
            </div>
        \`;
    }

    function render() {
        const rows = state.data[state.active] || [];
        const filter = state.filter.toLowerCase();
        const filtered = filter
            ? rows.filter((m) => (m.content || "").toLowerCase().includes(filter) ||
                (m.category || "").toLowerCase().includes(filter) ||
                (m.tags || []).some((t) => t.toLowerCase().includes(filter)))
            : rows;
        const content = document.getElementById("content");
        if (!filtered.length) {
            content.innerHTML = '<div class="empty-state">No memories to show.</div>';
        } else {
            content.innerHTML = filtered.map((m) => renderMemory(m, state.active)).join("");
        }
        document.getElementById("count-pending").textContent = state.data.pending.length;
        document.getElementById("count-confirmed").textContent = state.data.confirmed.length;
        document.getElementById("count-archived").textContent = state.data.archived.length;
    }

    document.querySelectorAll(".tab").forEach((btn) => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            state.active = btn.getAttribute("data-tab");
            render();
        });
    });

    document.getElementById("search").addEventListener("input", (e) => {
        state.filter = e.target.value || "";
        render();
    });

    document.getElementById("content").addEventListener("click", (e) => {
        const target = e.target.closest("button[data-action]");
        if (!target) { return; }
        const action = target.getAttribute("data-action");
        const memoryId = target.getAttribute("data-id");
        vscode.postMessage({ type: action, memoryId });
    });

    const manualWrap = document.getElementById("manualSave");
    document.getElementById("manualSaveToggle").addEventListener("click", () => {
        manualWrap.classList.toggle("collapsed");
    });
    document.getElementById("m-save").addEventListener("click", () => {
        const content = document.getElementById("m-content").value || "";
        const category = document.getElementById("m-category").value || "convention";
        const tags = document.getElementById("m-tags").value || "";
        vscode.postMessage({ type: "createManual", content, category, tags });
        document.getElementById("m-content").value = "";
        document.getElementById("m-tags").value = "";
    });

    window.addEventListener("message", (event) => {
        const m = event.data;
        if (m.type === "loadMemories") {
            state.data = { pending: m.pending || [], confirmed: m.confirmed || [], archived: m.archived || [] };
            render();
        }
    });

    vscode.postMessage({ type: "webviewReady" });
</script>
</body>
</html>`;
}

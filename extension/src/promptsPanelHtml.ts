/**
 * Self-contained HTML template for the Prompts Editor panel.
 */
export function getPromptsPanelHtml(): string {
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

    /* ── Layout ──────────────────────────────── */
    .main-layout {
        display: flex;
        flex: 1;
        overflow: hidden;
    }

    /* ── Sidebar ─────────────────────────────── */
    .category-nav {
        width: 160px;
        min-width: 130px;
        border-right: 1px solid var(--vscode-panel-border);
        padding: 8px 0;
        overflow-y: auto;
        flex-shrink: 0;
    }
    .category-btn {
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
    .category-btn:hover { opacity: 1; background: var(--vscode-list-hoverBackground); }
    .category-btn.active {
        opacity: 1;
        background: var(--vscode-list-activeSelectionBackground);
        color: var(--vscode-list-activeSelectionForeground);
    }
    .category-count {
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

    /* ── Search ──────────────────────────────── */
    .search-bar {
        margin-bottom: 16px;
    }
    .search-bar input {
        width: 100%;
        max-width: 400px;
        padding: 6px 10px;
        border: 1px solid var(--vscode-input-border);
        background: var(--vscode-input-background);
        color: var(--vscode-input-foreground);
        font-family: inherit;
        font-size: 12px;
        border-radius: 4px;
        outline: none;
    }
    .search-bar input:focus {
        border-color: var(--vscode-focusBorder);
    }

    /* ── Prompt Card ──────────────────────────── */
    .prompt-card {
        margin-bottom: 20px;
        border: 1px solid var(--vscode-panel-border);
        border-radius: 6px;
        overflow: hidden;
    }
    .prompt-card.modified {
        border-color: var(--vscode-inputValidation-infoBorder, #007acc);
    }
    .prompt-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 10px 14px;
        background: var(--vscode-sideBar-background);
        cursor: pointer;
        user-select: none;
    }
    .prompt-header:hover {
        background: var(--vscode-list-hoverBackground);
    }
    .prompt-header-left {
        display: flex;
        align-items: center;
        gap: 8px;
        flex: 1;
        min-width: 0;
    }
    .prompt-name {
        font-weight: 600;
        font-size: 13px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .badge {
        font-size: 10px;
        padding: 1px 6px;
        border-radius: 10px;
        font-weight: 500;
        flex-shrink: 0;
    }
    .badge-modified {
        background: var(--vscode-inputValidation-infoBackground, #063b49);
        color: var(--vscode-inputValidation-infoForeground, #75beff);
    }
    .badge-warning {
        background: var(--vscode-inputValidation-warningBackground, #352a05);
        color: var(--vscode-inputValidation-warningForeground, #cca700);
    }
    .prompt-chevron {
        font-size: 11px;
        opacity: 0.5;
        transition: transform 0.15s;
        flex-shrink: 0;
        margin-left: 8px;
    }
    .prompt-card.expanded .prompt-chevron {
        transform: rotate(90deg);
    }

    .prompt-body {
        display: none;
        padding: 14px;
        border-top: 1px solid var(--vscode-panel-border);
    }
    .prompt-card.expanded .prompt-body {
        display: block;
    }
    .prompt-desc {
        font-size: 12px;
        opacity: 0.75;
        margin-bottom: 10px;
        line-height: 1.4;
    }
    .prompt-warning {
        font-size: 11px;
        padding: 6px 10px;
        margin-bottom: 10px;
        border-radius: 4px;
        background: var(--vscode-inputValidation-warningBackground, #352a05);
        color: var(--vscode-inputValidation-warningForeground, #cca700);
        border: 1px solid var(--vscode-inputValidation-warningBorder, #665500);
    }
    .prompt-textarea {
        width: 100%;
        min-height: 150px;
        padding: 10px;
        border: 1px solid var(--vscode-input-border);
        background: var(--vscode-input-background);
        color: var(--vscode-input-foreground);
        font-family: var(--vscode-editor-font-family, 'Consolas', monospace);
        font-size: 12px;
        line-height: 1.5;
        border-radius: 4px;
        resize: vertical;
        outline: none;
        tab-size: 4;
    }
    .prompt-textarea:focus {
        border-color: var(--vscode-focusBorder);
    }
    .prompt-meta {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-top: 8px;
        flex-wrap: wrap;
        gap: 8px;
    }
    .template-vars {
        display: flex;
        gap: 4px;
        flex-wrap: wrap;
    }
    .var-chip {
        font-size: 10px;
        padding: 2px 6px;
        border-radius: 3px;
        background: var(--vscode-badge-background);
        color: var(--vscode-badge-foreground);
        font-family: var(--vscode-editor-font-family, monospace);
    }
    .var-chip.missing {
        background: var(--vscode-inputValidation-errorBackground, #5a1d1d);
        color: var(--vscode-errorForeground, #f48771);
    }
    .prompt-actions {
        display: flex;
        gap: 6px;
    }
    .btn {
        padding: 4px 10px;
        border: 1px solid var(--vscode-button-border, transparent);
        border-radius: 4px;
        font-family: inherit;
        font-size: 11px;
        cursor: pointer;
    }
    .btn-primary {
        background: var(--vscode-button-background);
        color: var(--vscode-button-foreground);
    }
    .btn-primary:hover { background: var(--vscode-button-hoverBackground); }
    .btn-secondary {
        background: var(--vscode-button-secondaryBackground);
        color: var(--vscode-button-secondaryForeground);
    }
    .btn-secondary:hover { background: var(--vscode-button-secondaryHoverBackground); }

    /* ── Footer ──────────────────────────────── */
    .panel-footer {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 10px 20px;
        border-top: 1px solid var(--vscode-panel-border);
        flex-shrink: 0;
    }
    .footer-left {
        font-size: 11px;
        opacity: 0.6;
    }
    .footer-right {
        display: flex;
        gap: 8px;
    }

    /* ── Loading ──────────────────────────────── */
    .loading {
        display: flex;
        align-items: center;
        justify-content: center;
        height: 200px;
        opacity: 0.5;
    }
</style>
</head>
<body>

<div class="panel-header">
    <h1>Edit Prompts</h1>
    <p>Customize LLM prompts for this project. Overrides are saved to .lean_ai/prompts.yaml</p>
</div>

<div class="main-layout">
    <nav class="category-nav" id="categoryNav"></nav>
    <div class="content-area" id="contentArea">
        <div class="search-bar">
            <input type="text" id="searchInput" placeholder="Search prompts..." />
        </div>
        <div id="promptList">
            <div class="loading">Loading prompts...</div>
        </div>
    </div>
</div>

<div class="panel-footer">
    <div class="footer-left" id="statusText"></div>
    <div class="footer-right">
        <button class="btn btn-secondary" id="resetAllBtn">Reset All to Defaults</button>
        <button class="btn btn-primary" id="saveAllBtn">Save Changes</button>
    </div>
</div>

<script>
const vscode = acquireVsCodeApi();

let allPrompts = [];
let categories = [];
let activeCategory = null;
let editedPrompts = {};  // key -> edited text (dirty state)

// ── Message handling ────────────────────────────────
window.addEventListener('message', (event) => {
    const msg = event.data;
    if (msg.type === 'loadPrompts') {
        allPrompts = msg.prompts || [];
        categories = msg.categories || [];
        editedPrompts = {};
        renderCategories();
        renderPrompts();
        updateStatus();
    }
});

// ── Category nav ─────────────────────────────────────
function renderCategories() {
    const nav = document.getElementById('categoryNav');
    let html = '<button class="category-btn' + (!activeCategory ? ' active' : '') +
        '" data-cat="">All<span class="category-count">(' + allPrompts.length + ')</span></button>';
    for (const cat of categories) {
        const count = allPrompts.filter(p => p.category === cat).length;
        if (count === 0) continue;
        const active = activeCategory === cat ? ' active' : '';
        html += '<button class="category-btn' + active +
            '" data-cat="' + escapeHtml(cat) + '">' +
            escapeHtml(cat) +
            '<span class="category-count">(' + count + ')</span></button>';
    }
    nav.innerHTML = html;

    nav.querySelectorAll('.category-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            activeCategory = btn.getAttribute('data-cat') || null;
            renderCategories();
            renderPrompts();
        });
    });
}

// ── Prompt list ──────────────────────────────────────
function renderPrompts() {
    const search = (document.getElementById('searchInput').value || '').toLowerCase();
    const list = document.getElementById('promptList');

    const filtered = allPrompts.filter(p => {
        if (activeCategory && p.category !== activeCategory) return false;
        if (search) {
            return p.name.toLowerCase().includes(search) ||
                   p.key.toLowerCase().includes(search) ||
                   p.description.toLowerCase().includes(search);
        }
        return true;
    });

    if (filtered.length === 0) {
        list.innerHTML = '<div class="loading">No prompts match your search.</div>';
        return;
    }

    let html = '';
    for (const p of filtered) {
        const isModified = p.is_overridden || (p.key in editedPrompts);
        const currentText = p.key in editedPrompts ? editedPrompts[p.key] : p.current_text;
        const modClass = isModified ? ' modified' : '';

        html += '<div class="prompt-card' + modClass + '" data-key="' + escapeHtml(p.key) + '">';
        html += '<div class="prompt-header">';
        html += '<div class="prompt-header-left">';
        html += '<span class="prompt-name">' + escapeHtml(p.name) + '</span>';
        if (isModified) {
            html += '<span class="badge badge-modified">Modified</span>';
        }
        if (p.warning) {
            html += '<span class="badge badge-warning">Caution</span>';
        }
        html += '</div>';
        html += '<span class="prompt-chevron">&#9654;</span>';
        html += '</div>';

        html += '<div class="prompt-body">';
        html += '<div class="prompt-desc">' + escapeHtml(p.description) + '</div>';
        if (p.warning) {
            html += '<div class="prompt-warning">' + escapeHtml(p.warning) + '</div>';
        }
        html += '<textarea class="prompt-textarea" data-key="' + escapeHtml(p.key) +
            '" rows="' + Math.min(Math.max(currentText.split('\\n').length + 2, 6), 30) +
            '">' + escapeHtml(currentText) + '</textarea>';

        // Template vars + actions
        html += '<div class="prompt-meta">';
        if (p.template_vars && p.template_vars.length > 0) {
            html += '<div class="template-vars">';
            html += '<span style="font-size:10px;opacity:0.6;">Placeholders:</span> ';
            for (const v of p.template_vars) {
                const missing = !currentText.includes('{' + v + '}');
                html += '<span class="var-chip' + (missing ? ' missing' : '') +
                    '">{' + escapeHtml(v) + '}</span>';
            }
            html += '</div>';
        } else {
            html += '<div></div>';
        }
        html += '<div class="prompt-actions">';
        if (isModified) {
            html += '<button class="btn btn-secondary reset-btn" data-key="' +
                escapeHtml(p.key) + '">Reset to Default</button>';
        }
        html += '</div>';
        html += '</div>';  // prompt-meta

        html += '</div>';  // prompt-body
        html += '</div>';  // prompt-card
    }

    list.innerHTML = html;

    // Attach event listeners
    list.querySelectorAll('.prompt-header').forEach(header => {
        header.addEventListener('click', () => {
            const card = header.closest('.prompt-card');
            card.classList.toggle('expanded');
        });
    });

    list.querySelectorAll('.prompt-textarea').forEach(textarea => {
        textarea.addEventListener('input', (e) => {
            const key = e.target.getAttribute('data-key');
            const original = allPrompts.find(p => p.key === key);
            if (!original) return;
            const newText = e.target.value;
            if (newText !== original.default_text) {
                editedPrompts[key] = newText;
            } else {
                delete editedPrompts[key];
            }
            updateStatus();
            // Update var chips
            updateVarChips(e.target);
        });
    });

    list.querySelectorAll('.reset-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const key = btn.getAttribute('data-key');
            delete editedPrompts[key];
            vscode.postMessage({ type: 'resetPrompt', keys: [key] });
        });
    });
}

function updateVarChips(textarea) {
    const key = textarea.getAttribute('data-key');
    const prompt = allPrompts.find(p => p.key === key);
    if (!prompt || !prompt.template_vars) return;
    const card = textarea.closest('.prompt-card');
    card.querySelectorAll('.var-chip').forEach(chip => {
        const varName = chip.textContent.replace(/[{}]/g, '');
        if (!textarea.value.includes('{' + varName + '}')) {
            chip.classList.add('missing');
        } else {
            chip.classList.remove('missing');
        }
    });
}

function updateStatus() {
    const overridden = allPrompts.filter(p => p.is_overridden).length;
    const dirty = Object.keys(editedPrompts).length;
    const parts = [];
    if (overridden > 0) parts.push(overridden + ' overridden');
    if (dirty > 0) parts.push(dirty + ' unsaved');
    document.getElementById('statusText').textContent = parts.length > 0
        ? parts.join(', ')
        : allPrompts.length + ' prompts loaded';
}

// ── Footer buttons ───────────────────────────────────
document.getElementById('saveAllBtn').addEventListener('click', () => {
    // Collect all overrides: existing overrides + new edits
    const overrides = {};
    for (const p of allPrompts) {
        if (p.is_overridden && !(p.key in editedPrompts)) {
            // Keep existing override
            overrides[p.key] = p.current_text;
        }
    }
    // Add/update from edits
    for (const [key, text] of Object.entries(editedPrompts)) {
        overrides[key] = text;
    }

    if (Object.keys(overrides).length === 0) {
        return;  // Nothing to save
    }

    vscode.postMessage({ type: 'savePrompts', overrides: overrides });
});

document.getElementById('resetAllBtn').addEventListener('click', () => {
    vscode.postMessage({ type: 'resetAll' });
});

// ── Search ───────────────────────────────────────────
document.getElementById('searchInput').addEventListener('input', () => {
    renderPrompts();
});

// ── Utility ──────────────────────────────────────────
function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;')
              .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// Signal ready
vscode.postMessage({ type: 'webviewReady' });
</script>
</body>
</html>`;
}

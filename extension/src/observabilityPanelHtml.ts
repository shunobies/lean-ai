/**
 * Self-contained HTML template for the Observability panel.
 * Provides 4 tabs: Sessions, Traces, Feedback, Metrics.
 * Uses VS Code theme variables, no external dependencies.
 */
export function getObservabilityPanelHtml(): string {
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

    /* Tab navigation */
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

    /* Tab panels */
    .tab-panel { display: none; }
    .tab-panel.active { display: flex; flex-direction: column; flex: 1; overflow: hidden; }

    /* Content area */
    .content { flex: 1; overflow-y: auto; padding: 12px 20px 20px; }

    /* Search / filter bar */
    .filter-bar {
        padding: 8px 20px;
        border-bottom: 1px solid var(--vscode-panel-border);
        flex-shrink: 0;
        display: flex;
        gap: 8px;
        align-items: center;
    }
    .filter-bar input, .filter-bar select {
        padding: 6px 10px;
        border: 1px solid var(--vscode-input-border);
        background: var(--vscode-input-background);
        color: var(--vscode-input-foreground);
        font-family: inherit;
        font-size: 12px;
        border-radius: 3px;
        outline: none;
    }
    .filter-bar input:focus, .filter-bar select:focus { border-color: var(--vscode-focusBorder); }
    .filter-bar input { flex: 1; }

    /* Buttons */
    .btn {
        padding: 4px 10px;
        border: 1px solid var(--vscode-button-border, transparent);
        border-radius: 3px;
        font-family: inherit;
        font-size: 11px;
        cursor: pointer;
        background: var(--vscode-button-background);
        color: var(--vscode-button-foreground);
    }
    .btn:hover { background: var(--vscode-button-hoverBackground); }
    .btn-secondary {
        background: transparent;
        color: var(--vscode-foreground);
    }
    .btn-secondary:hover { background: var(--vscode-list-hoverBackground); }

    /* Chips / badges */
    .chip {
        display: inline-block;
        padding: 1px 7px;
        border-radius: 10px;
        font-size: 10px;
        background: var(--vscode-badge-background);
        color: var(--vscode-badge-foreground);
    }
    .chip.success { background: var(--vscode-terminal-ansiGreen, #3fb950); color: var(--vscode-editor-background); }
    .chip.failure { background: var(--vscode-terminal-ansiRed, #f85149); color: var(--vscode-editor-background); }
    .chip.warning { background: var(--vscode-terminal-ansiYellow, #d19a66); color: var(--vscode-editor-background); }
    .chip.info { background: var(--vscode-editorInfo-foreground); color: var(--vscode-editor-background); opacity: 0.85; }

    /* Empty state */
    .empty-state {
        padding: 32px 12px;
        text-align: center;
        opacity: 0.6;
        font-size: 12px;
    }

    /* ==================== Sessions Tab ==================== */
    .session-card {
        border: 1px solid var(--vscode-panel-border);
        border-radius: 4px;
        padding: 12px;
        margin-bottom: 10px;
        background: var(--vscode-editorWidget-background);
    }
    .session-head {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 6px;
        flex-wrap: wrap;
    }
    .session-task {
        font-size: 12px;
        font-weight: 500;
        flex: 1;
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    .session-meta {
        font-size: 10px;
        opacity: 0.55;
        margin-top: 4px;
    }
    .session-tokens {
        font-size: 10px;
        opacity: 0.7;
    }
    .session-actions {
        margin-top: 8px;
        display: flex;
        gap: 6px;
        align-items: center;
    }
    .thumb-btn {
        background: none;
        border: 1px solid var(--vscode-panel-border);
        border-radius: 3px;
        padding: 2px 8px;
        font-size: 14px;
        cursor: pointer;
        color: var(--vscode-foreground);
        line-height: 1;
    }
    .thumb-btn:hover { background: var(--vscode-list-hoverBackground); }
    .thumb-btn.thumbs-up.active { background: var(--vscode-terminal-ansiGreen, #3fb950); color: var(--vscode-editor-background); border-color: transparent; }
    .thumb-btn.thumbs-down.active { background: var(--vscode-terminal-ansiRed, #f85149); color: var(--vscode-editor-background); border-color: transparent; }

    /* Inline feedback form on session cards */
    .feedback-form {
        display: none;
        margin-top: 8px;
        padding-top: 8px;
        border-top: 1px solid var(--vscode-panel-border);
    }
    .feedback-form.visible { display: block; }
    .star-rating { display: flex; gap: 2px; margin-bottom: 6px; }
    .star-rating span {
        cursor: pointer;
        font-size: 16px;
        color: var(--vscode-descriptionForeground);
    }
    .star-rating span.filled { color: var(--vscode-terminal-ansiYellow, #d19a66); }
    .feedback-form textarea {
        width: 100%;
        padding: 6px 10px;
        border: 1px solid var(--vscode-input-border);
        background: var(--vscode-input-background);
        color: var(--vscode-input-foreground);
        font-family: inherit;
        font-size: 12px;
        border-radius: 3px;
        outline: none;
        resize: vertical;
        min-height: 40px;
        margin-bottom: 6px;
    }
    .feedback-form .tags-input {
        width: 100%;
        padding: 6px 10px;
        border: 1px solid var(--vscode-input-border);
        background: var(--vscode-input-background);
        color: var(--vscode-input-foreground);
        font-family: inherit;
        font-size: 12px;
        border-radius: 3px;
        outline: none;
        margin-bottom: 6px;
    }
    .feedback-form .form-actions { display: flex; gap: 6px; }

    /* ==================== Traces Tab ==================== */
    .trace-toolbar {
        padding: 8px 20px;
        border-bottom: 1px solid var(--vscode-panel-border);
        flex-shrink: 0;
        display: flex;
        gap: 8px;
        align-items: center;
    }
    .trace-toolbar select {
        padding: 6px 10px;
        border: 1px solid var(--vscode-input-border);
        background: var(--vscode-input-background);
        color: var(--vscode-input-foreground);
        font-family: inherit;
        font-size: 12px;
        border-radius: 3px;
        outline: none;
        flex: 1;
    }
    .trace-toolbar select:focus { border-color: var(--vscode-focusBorder); }

    .trace-tree { list-style: none; }
    .trace-tree ul { list-style: none; padding-left: 18px; }
    .trace-node {
        padding: 3px 0;
        font-size: 12px;
        cursor: pointer;
        display: flex;
        align-items: center;
        gap: 4px;
    }
    .trace-node:hover { background: var(--vscode-list-hoverBackground); }
    .trace-node.selected { background: var(--vscode-list-activeSelectionBackground); color: var(--vscode-list-activeSelectionForeground); }
    .trace-toggle {
        display: inline-block;
        width: 14px;
        text-align: center;
        font-size: 10px;
        opacity: 0.6;
        user-select: none;
    }
    .trace-icon { font-size: 11px; opacity: 0.7; }
    .trace-label { flex: 1; }
    .trace-duration { font-size: 10px; opacity: 0.5; }

    .trace-detail {
        border-top: 1px solid var(--vscode-panel-border);
        padding: 12px 20px;
        flex-shrink: 0;
        max-height: 200px;
        overflow-y: auto;
        font-size: 12px;
        background: var(--vscode-editor-background);
    }
    .trace-detail h3 { font-size: 12px; margin-bottom: 6px; }
    .trace-detail pre {
        background: var(--vscode-textCodeBlock-background);
        padding: 8px;
        border-radius: 3px;
        overflow-x: auto;
        font-size: 11px;
        white-space: pre-wrap;
        word-wrap: break-word;
    }

    /* ==================== Feedback Tab ==================== */
    .feedback-item {
        border: 1px solid var(--vscode-panel-border);
        border-radius: 4px;
        padding: 12px;
        margin-bottom: 10px;
        background: var(--vscode-editorWidget-background);
    }
    .feedback-head {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 6px;
    }
    .feedback-stars { display: flex; gap: 1px; }
    .feedback-stars span { font-size: 14px; color: var(--vscode-descriptionForeground); }
    .feedback-stars span.filled { color: var(--vscode-terminal-ansiYellow, #d19a66); }
    .feedback-comment {
        font-size: 12px;
        line-height: 1.4;
        white-space: pre-wrap;
        word-wrap: break-word;
        margin-bottom: 4px;
    }
    .feedback-tags { display: flex; gap: 4px; flex-wrap: wrap; margin-top: 4px; }
    .feedback-tag {
        font-size: 10px;
        padding: 1px 6px;
        border-radius: 8px;
        background: var(--vscode-badge-background);
        color: var(--vscode-badge-foreground);
    }
    .feedback-meta {
        font-size: 10px;
        opacity: 0.55;
        margin-top: 4px;
    }
    .feedback-actions {
        margin-top: 8px;
        display: flex;
        gap: 6px;
    }

    /* Filter row for feedback tab */
    .filter-row {
        display: flex;
        gap: 8px;
        align-items: center;
        flex-wrap: wrap;
    }
    .filter-row select {
        padding: 6px 10px;
        border: 1px solid var(--vscode-input-border);
        background: var(--vscode-input-background);
        color: var(--vscode-input-foreground);
        font-family: inherit;
        font-size: 12px;
        border-radius: 3px;
        outline: none;
    }
    .filter-row select:focus { border-color: var(--vscode-focusBorder); }

    /* ==================== Metrics Tab ==================== */
    .metrics-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
        gap: 10px;
        margin-bottom: 20px;
    }
    .metric-card {
        border: 1px solid var(--vscode-panel-border);
        border-radius: 4px;
        padding: 12px;
        background: var(--vscode-editorWidget-background);
        text-align: center;
    }
    .metric-value {
        font-size: 22px;
        font-weight: 600;
        margin-bottom: 2px;
    }
    .metric-label {
        font-size: 11px;
        opacity: 0.7;
    }

    /* CSS flexbox bar charts */
    .chart-section {
        margin-bottom: 20px;
    }
    .chart-section h3 {
        font-size: 13px;
        font-weight: 600;
        margin-bottom: 10px;
    }
    .bar-chart {
        display: flex;
        flex-direction: column;
        gap: 6px;
    }
    .bar-row {
        display: flex;
        align-items: center;
        gap: 8px;
    }
    .bar-label {
        width: 100px;
        font-size: 11px;
        text-align: right;
        flex-shrink: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    .bar-track {
        flex: 1;
        height: 18px;
        background: var(--vscode-editorWidget-background);
        border: 1px solid var(--vscode-panel-border);
        border-radius: 3px;
        overflow: hidden;
        display: flex;
    }
    .bar-fill {
        height: 100%;
        display: flex;
        align-items: center;
        padding-left: 6px;
        font-size: 10px;
        color: var(--vscode-editor-background);
        min-width: 0;
    }
    .bar-fill.success { background: var(--vscode-terminal-ansiGreen, #3fb950); }
    .bar-fill.failure { background: var(--vscode-terminal-ansiRed, #f85149); }
    .bar-fill.warning { background: var(--vscode-terminal-ansiYellow, #d19a66); }
    .bar-fill.info { background: var(--vscode-editorInfo-foreground); }
    .bar-value {
        width: 50px;
        font-size: 11px;
        text-align: right;
        flex-shrink: 0;
        opacity: 0.7;
    }
</style>
</head>
<body>
    <div class="panel-header">
        <h1>Observability</h1>
        <p>Monitor sessions, trace execution, review feedback, and view metrics.</p>
    </div>
    <div class="tabs">
        <button class="tab active" data-tab="sessions">Sessions <span class="count" id="count-sessions">0</span></button>
        <button class="tab" data-tab="traces">Traces</button>
        <button class="tab" data-tab="feedback">Feedback <span class="count" id="count-feedback">0</span></button>
        <button class="tab" data-tab="metrics">Metrics</button>
    </div>

    <!-- Sessions Tab -->
    <div class="tab-panel active" id="panel-sessions">
        <div class="filter-bar">
            <input id="session-search" type="text" placeholder="Filter sessions..." />
        </div>
        <div class="content" id="sessions-content"></div>
    </div>

    <!-- Traces Tab -->
    <div class="tab-panel" id="panel-traces">
        <div class="trace-toolbar">
            <select id="trace-session-select">
                <option value="">Select session...</option>
            </select>
            <button class="btn" id="trace-export-btn">Export</button>
        </div>
        <div class="content" id="traces-content">
            <div class="empty-state">Select a session to view traces.</div>
        </div>
        <div class="trace-detail" id="trace-detail" style="display:none;">
            <h3>Span Details</h3>
            <pre id="trace-detail-content">Select a span to see details.</pre>
        </div>
    </div>

    <!-- Feedback Tab -->
    <div class="tab-panel" id="panel-feedback">
        <div class="filter-bar">
            <div class="filter-row" style="flex:1;">
                <input id="feedback-search" type="text" placeholder="Filter feedback..." style="flex:1;" />
                <select id="feedback-rating-filter">
                    <option value="">All ratings</option>
                    <option value="5">5 stars</option>
                    <option value="4">4 stars</option>
                    <option value="3">3 stars</option>
                    <option value="2">2 stars</option>
                    <option value="1">1 star</option>
                </select>
                <select id="feedback-tag-filter">
                    <option value="">All tags</option>
                </select>
                <button class="btn" id="feedback-export-btn">Export</button>
            </div>
        </div>
        <div class="content" id="feedback-content"></div>
    </div>

    <!-- Metrics Tab -->
    <div class="tab-panel" id="panel-metrics">
        <div class="content" id="metrics-content">
            <div class="empty-state">Loading metrics...</div>
        </div>
    </div>

<script>
    const vscode = acquireVsCodeApi();
    const state = {
        active: "sessions",
        sessions: [],
        traces: {},
        feedback: [],
        metrics: null,
        sessionFilter: "",
        feedbackFilter: "",
        feedbackRatingFilter: "",
        feedbackTagFilter: "",
        selectedTraceSession: "",
        selectedSpanId: null,
        feedbackForms: {}
    };

    function escapeHtml(s) {
        return String(s ?? "").replace(/[&<>"']/g, (c) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        })[c]);
    }

    /* ==================== Tab Navigation ==================== */
    document.querySelectorAll(".tab").forEach((btn) => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
            document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
            btn.classList.add("active");
            const tabId = btn.getAttribute("data-tab");
            state.active = tabId;
            document.getElementById("panel-" + tabId).classList.add("active");
        });
    });

    /* ==================== Sessions Tab ==================== */
    function renderSessions() {
        const container = document.getElementById("sessions-content");
        let rows = state.sessions || [];
        const filter = state.sessionFilter.toLowerCase();
        if (filter) {
            rows = rows.filter((s) =>
                (s.task || "").toLowerCase().includes(filter) ||
                (s.session_id || "").toLowerCase().includes(filter) ||
                (s.outcome || "").toLowerCase().includes(filter)
            );
        }
        document.getElementById("count-sessions").textContent = state.sessions.length;
        if (!rows.length) {
            container.innerHTML = '<div class="empty-state">No sessions to show.</div>';
            return;
        }
        container.innerHTML = rows.map((s) => {
            const outcomeClass = (s.outcome === "success") ? "success" : (s.outcome === "failure") ? "failure" : "info";
            const tokens = (s.tokens_prompt || 0) + (s.tokens_completion || 0);
            const existingRating = state.feedbackForms[s.session_id];
            return \`
                <div class="session-card" data-session="\${escapeHtml(s.session_id)}">
                    <div class="session-head">
                        <span class="session-task" title="\${escapeHtml(s.task || "")}">\${escapeHtml(s.task || "Untitled session")}</span>
                        <span class="chip \${outcomeClass}">\${escapeHtml(s.outcome || "unknown")}</span>
                    </div>
                    <div class="session-meta">
                        \${escapeHtml(s.session_id || "")} · \${escapeHtml(s.created_at || "")}
                        \${s.model_name ? " · " + escapeHtml(s.model_name) : ""}
                    </div>
                    <div class="session-tokens">Tokens: \${tokens}</div>
                    <div class="session-actions">
                        <button class="thumb-btn thumbs-up \${existingRating && existingRating.rating >= 3 ? 'active' : ''}" data-action="thumbs-up" data-session="\${escapeHtml(s.session_id)}" title="Thumbs up">👍</button>
                        <button class="thumb-btn thumbs-down \${existingRating && existingRating.rating < 3 ? 'active' : ''}" data-action="thumbs-down" data-session="\${escapeHtml(s.session_id)}" title="Thumbs down">👎</button>
                        <button class="btn-secondary" data-action="feedback-toggle" data-session="\${escapeHtml(s.session_id)}" style="font-size:11px;">Feedback</button>
                    </div>
                    <div class="feedback-form \${existingRating ? 'visible' : ''}" data-form="\${escapeHtml(s.session_id)}">
                        <div class="star-rating" data-stars="\${escapeHtml(s.session_id)}">
                            \${[1,2,3,4,5].map((i) => '<span data-star="' + i + '" class="' + (existingRating && i <= (existingRating.rating || 0) ? 'filled' : '') + '">★</span>').join("")}
                        </div>
                        <textarea placeholder="Add a comment..." data-comment="\${escapeHtml(s.session_id)}">\${escapeHtml(existingRating ? (existingRating.comment || "") : "")}</textarea>
                        <input class="tags-input" type="text" placeholder="tags (comma separated)" data-tags="\${escapeHtml(s.session_id)}" value="\${escapeHtml(existingRating ? (existingRating.tags || []).join(", ") : "")}" />
                        <div class="form-actions">
                            <button class="btn" data-action="save-feedback" data-session="\${escapeHtml(s.session_id)}">Save</button>
                        </div>
                    </div>
                </div>
            \`;
        }).join("");
    }

    document.getElementById("session-search").addEventListener("input", (e) => {
        state.sessionFilter = e.target.value || "";
        renderSessions();
    });

    document.getElementById("sessions-content").addEventListener("click", (e) => {
        const target = e.target.closest("[data-action]");
        if (!target) return;
        const action = target.getAttribute("data-action");
        const sessionId = target.getAttribute("data-session");

        if (action === "thumbs-up") {
            const existing = state.feedbackForms[sessionId] || {};
            state.feedbackForms[sessionId] = { ...existing, rating: Math.max(existing.rating || 0, 3) };
            vscode.postMessage({ type: "sessionFeedback", sessionId, rating: 5, comment: existing.comment || "", tags: existing.tags || [] });
            renderSessions();
            return;
        }
        if (action === "thumbs-down") {
            const existing = state.feedbackForms[sessionId] || {};
            state.feedbackForms[sessionId] = { ...existing, rating: Math.min(existing.rating || 5, 2) };
            vscode.postMessage({ type: "sessionFeedback", sessionId, rating: 1, comment: existing.comment || "", tags: existing.tags || [] });
            renderSessions();
            return;
        }
        if (action === "feedback-toggle") {
            const form = document.querySelector('.feedback-form[data-form="' + sessionId + '"]');
            if (form) form.classList.toggle("visible");
            return;
        }
        if (action === "save-feedback") {
            const starsEl = document.querySelector('.star-rating[data-stars="' + sessionId + '"]');
            const commentEl = document.querySelector('textarea[data-comment="' + sessionId + '"]');
            const tagsEl = document.querySelector('input[data-tags="' + sessionId + '"]');
            let rating = state.feedbackForms[sessionId] ? (state.feedbackForms[sessionId].rating || 0) : 0;
            if (starsEl) {
                const filled = starsEl.querySelectorAll("span.filled");
                rating = filled.length || 0;
            }
            const comment = commentEl ? commentEl.value : "";
            const tags = tagsEl ? (tagsEl.value || "").split(",").map((t) => t.trim()).filter(Boolean) : [];
            state.feedbackForms[sessionId] = { rating, comment, tags };
            vscode.postMessage({ type: "sessionFeedback", sessionId, rating, comment, tags });
            return;
        }
    });

    /* Star rating click handler */
    document.getElementById("sessions-content").addEventListener("click", (e) => {
        const star = e.target.closest(".star-rating span[data-star]");
        if (!star) return;
        const rating = parseInt(star.getAttribute("data-star"), 10);
        const sessionId = star.parentElement.getAttribute("data-stars");
        const siblings = star.parentElement.querySelectorAll("span");
        siblings.forEach((s) => {
            const sv = parseInt(s.getAttribute("data-star"), 10);
            s.classList.toggle("filled", sv <= rating);
        });
        if (state.feedbackForms[sessionId]) {
            state.feedbackForms[sessionId].rating = rating;
        }
    });

    /* ==================== Traces Tab ==================== */
    function buildTraceTree(spans, parentId) {
        const children = (spans || []).filter((s) => s.parent_span_uuid === parentId);
        if (!children.length) return "";
        return children.map((span) => {
            const hasChildren = spans.some((s) => s.parent_span_uuid === span.span_uuid);
            const icon = span.span_type === "llm_call" ? "🔮" : span.span_type === "tool_call" ? "🔧" : span.span_type === "phase" ? "📦" : "📄";
            const duration = span.end_time && span.start_time
                ? Math.max(0, new Date(span.end_time) - new Date(span.start_time)) + "ms"
                : "...";
            return \`
                <li>
                    <div class="trace-node" data-span="\${escapeHtml(span.span_uuid)}">
                        <span class="trace-toggle">\${hasChildren ? '▶' : ' '}</span>
                        <span class="trace-icon">\${icon}</span>
                        <span class="trace-label">\${escapeHtml(span.span_name)}</span>
                        <span class="trace-duration">\${duration}</span>
                    </div>
                    <ul class="trace-children" style="display:none;">\${buildTraceTree(spans, span.span_uuid)}</ul>
                </li>
            \`;
        }).join("");
    }

    function renderTraces() {
        const container = document.getElementById("traces-content");
        const sessionId = state.selectedTraceSession;
        if (!sessionId) {
            container.innerHTML = '<div class="empty-state">Select a session to view traces.</div>';
            return;
        }
        const spans = state.traces[sessionId] || [];
        if (!spans.length) {
            container.innerHTML = '<div class="empty-state">No traces for this session.</div>';
            return;
        }
        const rootSpans = spans.filter((s) => !s.parent_span_uuid);
        container.innerHTML = '<ul class="trace-tree">' + rootSpans.map((span) => {
            const hasChildren = spans.some((s) => s.parent_span_uuid === span.span_uuid);
            const icon = span.span_type === "llm_call" ? "🔮" : span.span_type === "tool_call" ? "🔧" : span.span_type === "phase" ? "📦" : "📄";
            const duration = span.end_time && span.start_time
                ? Math.max(0, new Date(span.end_time) - new Date(span.start_time)) + "ms"
                : "...";
            return \`
                <li>
                    <div class="trace-node" data-span="\${escapeHtml(span.span_uuid)}">
                        <span class="trace-toggle">\${hasChildren ? '▶' : ' '}</span>
                        <span class="trace-icon">\${icon}</span>
                        <span class="trace-label">\${escapeHtml(span.span_name)}</span>
                        <span class="trace-duration">\${duration}</span>
                    </div>
                    <ul class="trace-children" style="display:none;">\${buildTraceTree(spans, span.span_uuid)}</ul>
                </li>
            \`;
        }).join("") + '</ul>';
    }

    document.getElementById("traces-content").addEventListener("click", (e) => {
        const node = e.target.closest(".trace-node");
        if (!node) return;
        const spanUuid = node.getAttribute("data-span");
        const toggle = node.querySelector(".trace-toggle");
        const childrenUl = node.parentElement.querySelector("ul.trace-children");

        // Toggle collapse
        if (childrenUl && toggle) {
            const isCollapsed = childrenUl.style.display === "none";
            childrenUl.style.display = isCollapsed ? "block" : "none";
            toggle.textContent = isCollapsed ? "▼" : "▶";
        }

        // Select span for detail panel
        document.querySelectorAll(".trace-node.selected").forEach((n) => n.classList.remove("selected"));
        node.classList.add("selected");
        state.selectedSpanId = spanUuid;

        const sessionId = state.selectedTraceSession;
        const spans = state.traces[sessionId] || [];
        const span = spans.find((s) => s.span_uuid === spanUuid);
        const detailPanel = document.getElementById("trace-detail");
        const detailContent = document.getElementById("trace-detail-content");
        if (span) {
            detailPanel.style.display = "block";
            detailContent.textContent = JSON.stringify(span, null, 2);
        }
    });

    document.getElementById("trace-session-select").addEventListener("change", (e) => {
        state.selectedTraceSession = e.target.value;
        state.selectedSpanId = null;
        document.getElementById("trace-detail").style.display = "none";
        renderTraces();
    });

    document.getElementById("trace-export-btn").addEventListener("click", () => {
        vscode.postMessage({ type: "exportTraces", sessionId: state.selectedTraceSession });
    });

    /* ==================== Feedback Tab ==================== */
    function renderFeedback() {
        const container = document.getElementById("feedback-content");
        let rows = state.feedback || [];
        const filter = state.feedbackFilter.toLowerCase();
        const ratingFilter = state.feedbackRatingFilter;
        const tagFilter = state.feedbackTagFilter;

        if (filter) {
            rows = rows.filter((f) =>
                (f.comment || "").toLowerCase().includes(filter) ||
                (f.session_id || "").toLowerCase().includes(filter)
            );
        }
        if (ratingFilter) {
            rows = rows.filter((f) => (f.rating || 0) === parseInt(ratingFilter, 10));
        }
        if (tagFilter) {
            rows = rows.filter((f) => (f.tags || []).some((t) => t.toLowerCase().includes(tagFilter)));
        }

        document.getElementById("count-feedback").textContent = state.feedback.length;

        // Populate tag filter options
        const tagFilterEl = document.getElementById("feedback-tag-filter");
        const allTags = new Set();
        state.feedback.forEach((f) => (f.tags || []).forEach((t) => allTags.add(t)));
        const currentTagVal = tagFilterEl.value;
        tagFilterEl.innerHTML = '<option value="">All tags</option>' +
            Array.from(allTags).sort().map((t) => '<option value="' + escapeHtml(t) + '">' + escapeHtml(t) + '</option>').join("");
        tagFilterEl.value = currentTagVal;

        if (!rows.length) {
            container.innerHTML = '<div class="empty-state">No feedback to show.</div>';
            return;
        }
        container.innerHTML = rows.map((f) => {
            const stars = [1, 2, 3, 4, 5].map((i) =>
                '<span class="' + (i <= (f.rating || 0) ? "filled" : '') + '">★</span>'
            ).join("");
            const tags = (f.tags || []).map((t) => '<span class="feedback-tag">' + escapeHtml(t) + '</span>').join("");
            return \`
                <div class="feedback-item" data-id="\${escapeHtml(f.id || "")}">
                    <div class="feedback-head">
                        <div class="feedback-stars">\${stars}</div>
                        <span class="chip">\${escapeHtml(f.session_id || "unknown")}</span>
                    </div>
                    \${f.comment ? '<div class="feedback-comment">' + escapeHtml(f.comment) + '</div>' : ""}
                    \${tags ? '<div class="feedback-tags">' + tags + '</div>' : ""}
                    <div class="feedback-meta">\${escapeHtml(f.created_at || "")}</div>
                    <div class="feedback-actions">
                        <button class="btn btn-secondary" data-action="delete-feedback" data-id="\${escapeHtml(f.id || "")}">Delete</button>
                    </div>
                </div>
            \`;
        }).join("");
    }

    document.getElementById("feedback-search").addEventListener("input", (e) => {
        state.feedbackFilter = e.target.value || "";
        renderFeedback();
    });

    document.getElementById("feedback-rating-filter").addEventListener("change", (e) => {
        state.feedbackRatingFilter = e.target.value;
        renderFeedback();
    });

    document.getElementById("feedback-tag-filter").addEventListener("change", (e) => {
        state.feedbackTagFilter = e.target.value;
        renderFeedback();
    });

    document.getElementById("feedback-content").addEventListener("click", (e) => {
        const target = e.target.closest("[data-action]");
        if (!target) return;
        const action = target.getAttribute("data-action");
        const id = target.getAttribute("data-id");
        if (action === "delete-feedback") {
            vscode.postMessage({ type: "deleteFeedback", id });
        }
    });

    document.getElementById("feedback-export-btn").addEventListener("click", () => {
        vscode.postMessage({ type: "exportFeedback" });
    });

    /* ==================== Metrics Tab ==================== */
    function renderMetrics() {
        const container = document.getElementById("metrics-content");
        const m = state.metrics;
        if (!m) {
            container.innerHTML = '<div class="empty-state">No metrics available.</div>';
            return;
        }

        const totalTokens = (m.total_prompt_tokens || 0) + (m.total_completion_tokens || 0);
        const cards = \`
            <div class="metrics-grid">
                <div class="metric-card">
                    <div class="metric-value">\${m.total_sessions || 0}</div>
                    <div class="metric-label">Total Sessions</div>
                </div>
                <div class="metric-card">
                    <div class="metric-value">\${totalTokens.toLocaleString()}</div>
                    <div class="metric-label">Total Tokens</div>
                </div>
                <div class="metric-card">
                    <div class="metric-value">\${m.avg_latency_ms != null ? Math.round(m.avg_latency_ms) + 'ms' : '—'}</div>
                    <div class="metric-label">Avg Latency</div>
                </div>
                <div class="metric-card">
                    <div class="metric-value">\${m.total_feedback || 0}</div>
                    <div class="metric-label">Feedback Entries</div>
                </div>
            </div>
        \`;

        // Token usage bar chart (CSS flexbox)
        const tokenBars = (m.token_by_model || []).map((entry) => {
            const pct = totalTokens > 0 ? Math.round(((entry.tokens || 0) / totalTokens) * 100) : 0;
            return \`
                <div class="bar-row">
                    <span class="bar-label">\${escapeHtml(entry.model || "unknown")}</span>
                    <div class="bar-track">
                        <div class="bar-fill info" style="width:\${pct}%">\${pct}%</div>
                    </div>
                    <span class="bar-value">\${(entry.tokens || 0).toLocaleString()}</span>
                </div>
            \`;
        }).join("");

        // Tool success rate bar chart (CSS flexbox)
        const toolBars = (m.tool_success_rates || []).map((entry) => {
            const successPct = entry.total > 0 ? Math.round(((entry.success || 0) / entry.total) * 100) : 0;
            const failurePct = 100 - successPct;
            return \`
                <div class="bar-row">
                    <span class="bar-label">\${escapeHtml(entry.tool || "unknown")}</span>
                    <div class="bar-track">
                        <div class="bar-fill success" style="width:\${successPct}%"></div>
                        <div class="bar-fill failure" style="width:\${failurePct}%"></div>
                    </div>
                    <span class="bar-value">\${successPct}%</span>
                </div>
            \`;
        }).join("");

        container.innerHTML = cards +
            (tokenBars ? '<div class="chart-section"><h3>Token Usage by Model</h3><div class="bar-chart">' + tokenBars + '</div></div>' : '') +
            (toolBars ? '<div class="chart-section"><h3>Tool Success Rates</h3><div class="bar-chart">' + toolBars + '</div></div>' : '');
    }

    /* ==================== Message Handling ==================== */
    window.addEventListener("message", (event) => {
        const m = event.data;
        switch (m.type) {
            case "loadSessions":
                state.sessions = m.sessions || [];
                renderSessions();
                break;
            case "loadTraces":
                state.traces = m.traces || {};
                // Populate session selector
                const select = document.getElementById("trace-session-select");
                const currentVal = select.value;
                const sessionIds = Object.keys(m.traces || {});
                select.innerHTML = '<option value="">Select session...</option>' +
                    sessionIds.map((id) => '<option value="' + escapeHtml(id) + '">' + escapeHtml(id) + '</option>').join("");
                select.value = currentVal;
                if (state.selectedTraceSession) renderTraces();
                break;
            case "loadFeedback":
                state.feedback = m.feedback || [];
                renderFeedback();
                break;
            case "loadMetrics":
                state.metrics = m.metrics;
                renderMetrics();
                break;
            case "feedbackSaved":
                // Refresh feedback list
                break;
            case "feedbackDeleted":
                state.feedback = state.feedback.filter((f) => f.id !== m.id);
                renderFeedback();
                break;
        }
    });

    // Request initial data
    vscode.postMessage({ type: "webviewReady" });
</script>
</body>
</html>`;
}

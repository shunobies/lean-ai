/**
 * Webview HTML template for the Lean AI sidebar chat panel.
 * Extracted from sidebarProvider.ts for maintainability.
 */
export function getWebviewHtml(chatFontSize: number): string {
    return /*html*/ `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<style>
    :root { --sai-chat-font-size: ${chatFontSize}px; }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
        font-family: var(--vscode-font-family);
        font-size: var(--vscode-font-size);
        color: var(--vscode-foreground);
        background: var(--vscode-sideBar-background);
        display: flex;
        flex-direction: column;
        height: 100vh;
        overflow: hidden;
    }

    .header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 8px 12px;
        border-bottom: 1px solid var(--vscode-panel-border);
        flex-shrink: 0;
    }
    .header-left {
        display: flex;
        align-items: center;
        gap: 8px;
    }
    .header-title {
        font-weight: 600;
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        opacity: 0.8;
    }
    .stage-badge {
        display: none;
        font-size: 10px;
        padding: 2px 6px;
        border-radius: 10px;
        background: var(--vscode-badge-background);
        color: var(--vscode-badge-foreground);
        font-weight: 500;
        text-transform: lowercase;
    }
    .stage-badge.visible { display: inline-block; }

    .header-right {
        display: flex;
        align-items: center;
        gap: 6px;
    }

    .agent-prompt-wrapper {
        position: relative;
        margin-top: 8px;
    }
    .agent-prompt-wrapper pre {
        margin: 0;
        padding-right: 40px;
    }
    .send-to-agent-btn {
        position: absolute;
        top: 6px;
        right: 6px;
        padding: 4px 10px;
        background: var(--vscode-button-background);
        color: var(--vscode-button-foreground);
        border: none;
        border-radius: 4px;
        cursor: pointer;
        font-size: 11px;
        font-family: inherit;
        opacity: 0;
        transition: opacity 0.15s;
    }
    .agent-prompt-wrapper:hover .send-to-agent-btn {
        opacity: 1;
    }
    .send-to-agent-btn:hover {
        background: var(--vscode-button-hoverBackground);
    }

    .new-chat-btn {
        background: none;
        border: none;
        color: var(--vscode-foreground);
        cursor: pointer;
        font-size: 14px;
        padding: 2px 6px;
        border-radius: 4px;
        opacity: 0.7;
    }
    .new-chat-btn:hover {
        opacity: 1;
        background: var(--vscode-toolbar-hoverBackground);
    }

    .messages {
        flex: 1;
        overflow-y: auto;
        padding: 12px;
        display: flex;
        flex-direction: column;
        gap: 12px;
    }
    .msg {
        padding: 8px 12px;
        border-radius: 8px;
        max-width: 95%;
        line-height: 1.5;
        word-wrap: break-word;
        white-space: pre-wrap;
        font-size: var(--sai-chat-font-size);
    }
    .msg-user {
        align-self: flex-end;
        background: var(--vscode-button-background);
        color: var(--vscode-button-foreground);
    }
    .msg-ai {
        align-self: flex-start;
        background: var(--vscode-editor-background);
        border: 1px solid var(--vscode-panel-border);
    }
    .msg-error {
        align-self: flex-start;
        background: var(--vscode-inputValidation-errorBackground);
        border: 1px solid var(--vscode-inputValidation-errorBorder);
        color: var(--vscode-errorForeground);
    }
    .msg-system {
        align-self: center;
        font-size: 11px;
        opacity: 0.7;
        font-style: italic;
    }

    .timestamp-divider {
        align-self: center;
        font-size: 10px;
        opacity: 0.4;
        padding: 4px 0;
        text-align: center;
        user-select: none;
    }

    .header-icon-btn {
        background: none;
        border: none;
        color: var(--vscode-foreground);
        cursor: pointer;
        font-size: 14px;
        padding: 2px 6px;
        border-radius: 4px;
        opacity: 0.7;
    }
    .header-icon-btn:hover {
        opacity: 1;
        background: var(--vscode-toolbar-hoverBackground);
    }

    .search-bar {
        padding: 6px 12px;
        border-bottom: 1px solid var(--vscode-panel-border);
        display: flex;
        gap: 6px;
        flex-shrink: 0;
    }
    .search-bar input {
        flex: 1;
        border: 1px solid var(--vscode-input-border);
        background: var(--vscode-input-background);
        color: var(--vscode-input-foreground);
        padding: 4px 8px;
        border-radius: 4px;
        font-family: inherit;
        font-size: inherit;
        outline: none;
    }
    .search-bar input:focus {
        border-color: var(--vscode-focusBorder);
    }
    .search-clear-btn {
        background: none;
        border: none;
        color: var(--vscode-foreground);
        cursor: pointer;
        font-size: 16px;
        opacity: 0.6;
        padding: 0 4px;
    }
    .search-clear-btn:hover { opacity: 1; }

    .search-result {
        padding: 8px 12px;
        border-radius: 6px;
        border: 1px solid var(--vscode-panel-border);
        background: var(--vscode-editor-background);
        cursor: pointer;
        transition: background 0.1s;
    }
    .search-result:hover {
        background: var(--vscode-list-hoverBackground);
    }
    .search-result-title {
        font-weight: 600;
        font-size: 12px;
        margin-bottom: 2px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .search-result-date {
        font-size: 10px;
        opacity: 0.5;
        margin-bottom: 4px;
    }
    .search-result-snippet {
        font-size: 11px;
        opacity: 0.8;
        white-space: pre-wrap;
        word-break: break-word;
    }
    .search-result-snippet mark {
        background: var(--vscode-editor-findMatchHighlightBackground, #ea5c0055);
        padding: 0 1px;
        border-radius: 2px;
    }

    .msg code {
        background: var(--vscode-textCodeBlock-background);
        padding: 1px 4px;
        border-radius: 3px;
        font-family: var(--vscode-editor-font-family);
        font-size: var(--vscode-editor-font-size);
    }
    .msg pre {
        background: var(--vscode-textCodeBlock-background);
        padding: 8px;
        border-radius: 4px;
        overflow-x: auto;
        margin: 4px 0;
        font-family: var(--vscode-editor-font-family);
        font-size: var(--vscode-editor-font-size);
    }

    .thinking {
        display: none;
        align-self: flex-start;
        padding: 8px 12px;
        font-style: italic;
        opacity: 0.6;
    }
    .thinking.visible { display: block; }

    .tool-approval-card {
        align-self: stretch;
        border: 1px solid var(--vscode-inputValidation-warningBorder, #cca700);
        border-radius: 8px;
        background: var(--vscode-inputValidation-warningBackground, #332b00);
        padding: 10px 12px;
        font-size: 12px;
        line-height: 1.5;
    }
    .tool-approval-card .tool-approval-title {
        font-weight: 600;
        margin-bottom: 4px;
        color: var(--vscode-inputValidation-warningForeground, #ffcc00);
    }
    .tool-approval-card .tool-approval-cmd {
        font-family: var(--vscode-editor-font-family);
        font-size: 11px;
        background: var(--vscode-textCodeBlock-background);
        border-radius: 4px;
        padding: 4px 8px;
        margin: 6px 0;
        word-break: break-all;
        white-space: pre-wrap;
    }
    .tool-approval-card .tool-approval-reason {
        opacity: 0.8;
        margin-bottom: 8px;
    }
    .tool-approval-card .tool-approval-actions {
        display: flex;
        gap: 8px;
    }
    .tool-approval-card .btn-approve {
        padding: 4px 14px;
        background: var(--vscode-testing-iconPassed, #28a745);
        color: #fff;
        border: none;
        border-radius: 4px;
        cursor: pointer;
        font-family: inherit;
        font-size: 12px;
        font-weight: 600;
    }
    .tool-approval-card .btn-approve:hover { opacity: 0.85; }
    .tool-approval-card .btn-deny {
        padding: 4px 14px;
        background: var(--vscode-inputValidation-errorBackground, #5a1d1d);
        color: var(--vscode-errorForeground, #f48771);
        border: 1px solid var(--vscode-inputValidation-errorBorder, #be1100);
        border-radius: 4px;
        cursor: pointer;
        font-family: inherit;
        font-size: 12px;
    }
    .tool-approval-card .btn-deny:hover { opacity: 0.85; }

    .approval-bar {
        display: none;
        padding: 10px 12px;
        border-top: 1px solid var(--vscode-panel-border);
        background: var(--vscode-editor-background);
        flex-shrink: 0;
        gap: 8px;
        align-items: center;
        justify-content: center;
    }
    .approval-bar.visible {
        display: flex;
    }
    .approval-bar .approve-btn {
        padding: 6px 18px;
        background: var(--vscode-testing-iconPassed, #28a745);
        color: #fff;
        border: none;
        border-radius: 4px;
        cursor: pointer;
        font-family: inherit;
        font-size: 13px;
        font-weight: 600;
    }
    .approval-bar .approve-btn:hover { opacity: 0.9; }
    .approval-bar .approval-hint {
        font-size: 11px;
        opacity: 0.6;
        font-style: italic;
    }
    .approval-bar .approval-label {
        font-size: 12px;
        opacity: 0.8;
        margin-right: 4px;
    }

    .input-area {
        padding: 8px 12px;
        border-top: 1px solid var(--vscode-panel-border);
        display: flex;
        gap: 8px;
        flex-shrink: 0;
    }
    .input-area textarea {
        flex: 1;
        resize: none;
        border: 1px solid var(--vscode-input-border);
        background: var(--vscode-input-background);
        color: var(--vscode-input-foreground);
        font-family: inherit;
        font-size: inherit;
        line-height: 1.4;
        padding: 6px 10px;
        border-radius: 4px;
        outline: none;
        min-height: 36px;
        max-height: 200px;
        overflow: hidden;
        overflow-wrap: break-word;
        word-break: break-word;
    }
    .input-area textarea:focus {
        border-color: var(--vscode-focusBorder);
    }
    .input-area button {
        padding: 6px 14px;
        background: var(--vscode-button-background);
        color: var(--vscode-button-foreground);
        border: none;
        border-radius: 4px;
        cursor: pointer;
        font-family: inherit;
        font-size: 13px;
        align-self: flex-end;
    }
    .input-area button:hover {
        background: var(--vscode-button-hoverBackground);
    }
    .input-area button:disabled {
        opacity: 0.5;
        cursor: not-allowed;
    }
</style>
</head>
<body>

<div class="header">
    <div class="header-left">
        <span class="header-title">Lean AI</span>
        <span class="stage-badge" id="stageBadge"></span>
    </div>
    <div class="header-right">
        <button class="header-icon-btn" id="backBtn" title="Back to current chat" style="display:none;">&#8592;</button>
        <button class="header-icon-btn" id="searchBtn" title="Search conversations">&#128269;</button>
        <button class="new-chat-btn" id="newChatBtn" title="New Chat">+</button>
    </div>
</div>

<div class="search-bar" id="searchBar" style="display:none;">
    <input type="text" id="searchInput" placeholder="Search past conversations..." />
    <button class="search-clear-btn" id="searchClearBtn" title="Close search">&times;</button>
</div>

<div class="messages" id="messages">
    <div class="msg msg-system">Describe what you'd like to build or change. I'll help you refine it into a clear task for the agent.</div>
</div>
<div class="thinking" id="thinking">Processing...</div>

<div class="approval-bar" id="approvalBar">
    <button class="approve-btn" id="approveBtn">Approve Plan</button>
    <span class="approval-hint">or type feedback below to revise</span>
</div>

<div class="input-area">
    <textarea id="input" rows="2" placeholder="Ask a question or describe a task..." autofocus></textarea>
    <button id="sendBtn">Send</button>
</div>

<script>
    const vscode = acquireVsCodeApi();
    const messagesEl = document.getElementById('messages');
    const inputEl = document.getElementById('input');
    const sendBtn = document.getElementById('sendBtn');
    const thinkingEl = document.getElementById('thinking');
    const newChatBtn = document.getElementById('newChatBtn');
    const approvalBar = document.getElementById('approvalBar');
    const approveBtn = document.getElementById('approveBtn');
    const stageBadge = document.getElementById('stageBadge');
    const searchBtn = document.getElementById('searchBtn');
    const backBtn = document.getElementById('backBtn');
    const searchBar = document.getElementById('searchBar');
    const searchInput = document.getElementById('searchInput');
    const searchClearBtn = document.getElementById('searchClearBtn');

    let sending = false;
    let lastTimestamp = null;
    let searchMode = false;
    let savedMessagesHtml = null;
    let searchDebounceTimer = null;

    // ── Timestamp helpers ──

    function formatTimestamp(date) {
        const now = new Date();
        const hours = date.getHours();
        const minutes = date.getMinutes();
        const ampm = hours >= 12 ? 'PM' : 'AM';
        const h = hours % 12 || 12;
        const m = minutes.toString().padStart(2, '0');
        const timeStr = h + ':' + m + ' ' + ampm;
        const isToday = date.toDateString() === now.toDateString();
        if (!isToday) {
            const month = date.toLocaleString('default', { month: 'short' });
            const day = date.getDate();
            return month + ' ' + day + ', ' + timeStr;
        }
        return timeStr;
    }

    function addTimestampDivider(date) {
        const minuteKey = date.getFullYear() + '-' + date.getMonth() + '-' + date.getDate()
            + '-' + date.getHours() + '-' + date.getMinutes();
        if (lastTimestamp === minuteKey) return;
        lastTimestamp = minuteKey;
        const div = document.createElement('div');
        div.className = 'timestamp-divider';
        div.textContent = formatTimestamp(date);
        messagesEl.appendChild(div);
    }

    function escapeHtml(str) {
        const d = document.createElement('div');
        d.textContent = str;
        return d.innerHTML;
    }

    function _applyMarkdownFormatting(html) {
        // Code blocks
        html = html.replace(/\`\`\`([\\s\\S]*?)\`\`\`/g, '<pre>$1</pre>');
        // Inline code
        html = html.replace(/\`([^\`]+)\`/g, '<code>$1</code>');
        // Bold
        html = html.replace(/\\*\\*([^*]+)\\*\\*/g, '<b>$1</b>');
        // Italic
        html = html.replace(/\\*([^*]+)\\*/g, '<i>$1</i>');
        return html;
    }

    function formatMarkdown(text) {
        // Pre-process: extract "## Suggested Agent Prompt" from raw text BEFORE
        // escapeHtml.  This prevents nested code fences inside the prompt content
        // from terminating the agent-prompt container early.  We use lastIndexOf
        // to find the true closing fence regardless of how many code blocks the
        // prompt itself contains.
        const AGENT_HEADING = '## Suggested Agent Prompt';
        const headingIdx = text.indexOf(AGENT_HEADING);
        if (headingIdx !== -1) {
            const afterHeading = text.slice(headingIdx + AGENT_HEADING.length);
            const openFenceIdx = afterHeading.indexOf('\`\`\`');
            if (openFenceIdx !== -1) {
                // Skip past the opening fence line (handles language-tagged fences too)
                const afterOpenFenceNewline = afterHeading.indexOf('\\n', openFenceIdx + 3);
                if (afterOpenFenceNewline !== -1) {
                    const bodyAndMore = afterHeading.slice(afterOpenFenceNewline + 1);
                    // lastIndexOf finds the closing fence even when the content
                    // contains nested code blocks
                    const closingFenceIdx = bodyAndMore.lastIndexOf('\\n\`\`\`');
                    if (closingFenceIdx !== -1) {
                        const promptContent = bodyAndMore.slice(0, closingFenceIdx);
                        const afterAgent = bodyAndMore.slice(closingFenceIdx + 4);
                        const beforeHtml = _applyMarkdownFormatting(escapeHtml(text.slice(0, headingIdx)));
                        const agentHtml =
                            '<div class="agent-prompt-wrapper">' +
                            '<b>Suggested Agent Prompt</b>' +
                            '<pre>' + escapeHtml(promptContent) + '</pre>' +
                            '<button class="send-to-agent-btn" onclick="sendToAgent(this)">Send to Agent &#9654;</button>' +
                            '</div>';
                        const afterHtml = afterAgent.trim() ? _applyMarkdownFormatting(escapeHtml(afterAgent)) : '';
                        return beforeHtml + agentHtml + afterHtml;
                    }
                }
            }
        }
        // No agent prompt section — normal markdown pipeline
        return _applyMarkdownFormatting(escapeHtml(text));
    }

    function sendToAgent(btn) {
        const wrapper = btn.closest('.agent-prompt-wrapper');
        const pre = wrapper.querySelector('pre');
        const promptText = pre.textContent.trim();
        vscode.postMessage({ type: 'sendToAgent', text: promptText });
    }

    function approveToolCmd(btn, approved) {
        const card = btn.closest('.tool-approval-card');
        const token = card ? card.dataset.token : null;
        if (!token) return;
        // Disable both buttons to prevent double-click
        card.querySelectorAll('button').forEach(b => b.disabled = true);
        // Replace actions with outcome label
        const actions = card.querySelector('.tool-approval-actions');
        if (actions) {
            actions.innerHTML = approved
                ? '<span style="color: var(--vscode-testing-iconPassed, #28a745); font-weight: 600;">&#10003; Allowed</span>'
                : '<span style="color: var(--vscode-errorForeground, #f48771); font-weight: 600;">&#10007; Denied</span>';
        }
        vscode.postMessage({ type: approved ? 'approve_tool' : 'deny_tool', token });
    }

    function addMessage(html, cls) {
        addTimestampDivider(new Date());
        const div = document.createElement('div');
        div.className = 'msg ' + cls;
        div.innerHTML = html;
        messagesEl.appendChild(div);
        messagesEl.scrollTop = messagesEl.scrollHeight;
        return div;
    }

    function setStage(stage) {
        if (stage) {
            stageBadge.textContent = stage.replace(/_/g, ' ');
            stageBadge.classList.add('visible');
        } else {
            stageBadge.classList.remove('visible');
        }
    }

    let sendTimeout = null;
    function send() {
        const text = inputEl.value.trim();
        if (!text || sending) return;
        // If viewing a historic conversation or in search mode, return to current chat first
        if (backBtn.style.display !== 'none') {
            vscode.postMessage({ type: 'backToCurrentChat' });
            return;
        }
        if (searchMode) {
            closeSearch();
            return;
        }
        sending = true;
        sendBtn.disabled = true;
        addMessage(escapeHtml(text), 'msg-user');
        inputEl.value = '';
        autoResize();
        vscode.postMessage({ type: 'sendMessage', text });
        // Safety: re-enable after 120s if response is lost
        if (sendTimeout) clearTimeout(sendTimeout);
        sendTimeout = setTimeout(() => {
            if (sending) {
                sending = false;
                sendBtn.disabled = false;
            }
        }, 120000);
    }

    sendBtn.addEventListener('click', send);
    inputEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            send();
        }
    });

    function autoResize() {
        const maxH = 200;
        inputEl.style.height = 'auto';
        inputEl.style.overflow = 'hidden';
        const sh = inputEl.scrollHeight;
        if (sh > maxH) {
            inputEl.style.height = maxH + 'px';
            inputEl.style.overflow = 'auto';
        } else {
            inputEl.style.height = sh + 'px';
        }
    }
    inputEl.addEventListener('input', autoResize);
    inputEl.addEventListener('paste', () => { setTimeout(autoResize, 0); });

    newChatBtn.addEventListener('click', () => {
        if (searchMode) closeSearch();
        messagesEl.innerHTML = '<div class="msg msg-system">Describe what you\\'d like to build or change. I\\'ll help you refine it into a clear task for the agent.</div>';
        setStage(null);
        approvalBar.classList.remove('visible');
        sending = false;
        sendBtn.disabled = false;
        lastTimestamp = null;
        backBtn.style.display = 'none';
        vscode.postMessage({ type: 'newChat' });
    });

    approveBtn.addEventListener('click', () => {
        vscode.postMessage({ type: 'approve' });
    });

    // ── Search UI ──

    function openSearch() {
        searchMode = true;
        savedMessagesHtml = messagesEl.innerHTML;
        searchBar.style.display = 'flex';
        messagesEl.innerHTML = '<div class="msg msg-system">Search your past conversations...</div>';
        searchInput.focus();
        backBtn.style.display = 'inline-block';
    }

    function closeSearch() {
        searchMode = false;
        searchBar.style.display = 'none';
        searchInput.value = '';
        backBtn.style.display = 'none';
        if (savedMessagesHtml !== null) {
            messagesEl.innerHTML = savedMessagesHtml;
            savedMessagesHtml = null;
        }
    }

    searchBtn.addEventListener('click', () => {
        if (searchMode) {
            closeSearch();
        } else {
            openSearch();
        }
    });

    searchClearBtn.addEventListener('click', closeSearch);

    backBtn.addEventListener('click', () => {
        if (searchMode) {
            closeSearch();
        } else {
            vscode.postMessage({ type: 'backToCurrentChat' });
        }
    });

    searchInput.addEventListener('input', () => {
        if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
        searchDebounceTimer = setTimeout(() => {
            const query = searchInput.value.trim();
            if (query.length >= 2) {
                vscode.postMessage({ type: 'searchConversations', query: query });
            } else {
                messagesEl.innerHTML = '<div class="msg msg-system">Type at least 2 characters to search...</div>';
            }
        }, 300);
    });

    searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            closeSearch();
        }
    });

    window.addEventListener('message', (event) => {
        const msg = event.data;
        switch (msg.type) {
            case 'thinking':
                thinkingEl.textContent = msg.text || 'Processing...';
                thinkingEl.classList.toggle('visible', msg.show);
                if (msg.show) messagesEl.scrollTop = messagesEl.scrollHeight;
                break;

            case 'stage':
                setStage(msg.stage);
                break;

            case 'reply':
                sending = false;
                sendBtn.disabled = false;
                if (sendTimeout) { clearTimeout(sendTimeout); sendTimeout = null; }
                addMessage(formatMarkdown(msg.text), msg.cls || 'msg-ai');
                break;

            case 'error':
                sending = false;
                sendBtn.disabled = false;
                if (sendTimeout) { clearTimeout(sendTimeout); sendTimeout = null; }
                addMessage(escapeHtml(msg.text), 'msg-error');
                break;

            case 'showApproval':
                approvalBar.classList.add('visible');
                break;

            case 'hideApproval':
                approvalBar.classList.remove('visible');
                break;

            case 'sendEnabled':
                sending = false;
                sendBtn.disabled = false;
                if (sendTimeout) { clearTimeout(sendTimeout); sendTimeout = null; }
                break;

            case 'chatReset':
                lastTimestamp = null;
                break;

            case 'setFontSize':
                document.documentElement.style.setProperty('--sai-chat-font-size', msg.size + 'px');
                break;

            case 'searchResults': {
                const results = msg.results || [];
                if (results.length === 0) {
                    messagesEl.innerHTML = '<div class="msg msg-system">No matching conversations found.</div>';
                } else {
                    messagesEl.innerHTML = '';
                    const query = searchInput.value.trim().toLowerCase();
                    for (const r of results) {
                        const card = document.createElement('div');
                        card.className = 'search-result';
                        card.addEventListener('click', () => {
                            vscode.postMessage({ type: 'loadConversation', id: r.id });
                        });

                        let snippetHtml = escapeHtml(r.matchSnippet);
                        if (query) {
                            const escaped = query.replace(/[.*+?^{}()|[\\]\\\\$]/g, '\\\\$&');
                            const re = new RegExp('(' + escaped + ')', 'gi');
                            snippetHtml = snippetHtml.replace(re, '<mark>$1</mark>');
                        }

                        const dateStr = new Date(r.createdAt).toLocaleDateString(undefined, {
                            month: 'short', day: 'numeric', year: 'numeric',
                            hour: 'numeric', minute: '2-digit'
                        });

                        card.innerHTML =
                            '<div class="search-result-title">' + escapeHtml(r.title) + '</div>' +
                            '<div class="search-result-date">' + dateStr + '</div>' +
                            '<div class="search-result-snippet">' + snippetHtml + '</div>';

                        messagesEl.appendChild(card);
                    }
                }
                messagesEl.scrollTop = 0;
                break;
            }

            case 'showConversation': {
                if (searchMode) {
                    searchBar.style.display = 'none';
                    searchMode = false;
                    searchInput.value = '';
                }
                backBtn.style.display = 'inline-block';
                messagesEl.innerHTML = '';
                lastTimestamp = null;

                const conv = msg.conversation;
                const header = document.createElement('div');
                header.className = 'msg msg-system';
                header.textContent = 'Viewing: ' + (conv.title || 'Past conversation');
                messagesEl.appendChild(header);

                for (const m of conv.messages) {
                    if (m.timestamp) {
                        addTimestampDivider(new Date(m.timestamp));
                    }
                    const cls = m.role === 'user' ? 'msg-user' :
                                m.role === 'assistant' ? 'msg-ai' : 'msg-system';
                    const div = document.createElement('div');
                    div.className = 'msg ' + cls;
                    div.innerHTML = m.role === 'user' ? escapeHtml(m.content) : formatMarkdown(m.content);
                    messagesEl.appendChild(div);
                }
                messagesEl.scrollTop = 0;
                break;
            }

            case 'tool_approval_required': {
                // Destructive shell command detected — show inline approval card.
                const token = msg.token;
                const toolName = msg.tool_name || 'run_tests';
                const cmd = msg.command || '';
                const reason = msg.reason || 'destructive command detected';

                const card = document.createElement('div');
                card.className = 'msg tool-approval-card';
                card.dataset.token = token;
                card.innerHTML =
                    '<div class="tool-approval-title">&#9888; Command approval required</div>' +
                    '<div class="tool-approval-reason">Tool: <b>' + escapeHtml(toolName) + '</b> — ' + escapeHtml(reason) + '</div>' +
                    '<div class="tool-approval-cmd">' + escapeHtml(cmd) + '</div>' +
                    '<div class="tool-approval-actions">' +
                    '<button class="btn-approve" onclick="approveToolCmd(this, true)">Allow</button>' +
                    '<button class="btn-deny" onclick="approveToolCmd(this, false)">Deny</button>' +
                    '</div>';
                messagesEl.appendChild(card);
                messagesEl.scrollTop = messagesEl.scrollHeight;
                break;
            }

            case 'restoreCurrentChat': {
                backBtn.style.display = 'none';
                savedMessagesHtml = null;
                messagesEl.innerHTML = '<div class="msg msg-system">Describe what you\\'d like to build or change. I\\'ll help you refine it into a clear task for the agent.</div>';
                lastTimestamp = null;

                const msgs = msg.messages || [];
                for (const m of msgs) {
                    if (m.timestamp) {
                        addTimestampDivider(new Date(m.timestamp));
                    }
                    const cls = m.role === 'user' ? 'msg-user' :
                                m.role === 'assistant' ? 'msg-ai' : 'msg-system';
                    const div = document.createElement('div');
                    div.className = 'msg ' + cls;
                    div.innerHTML = m.role === 'user' ? escapeHtml(m.content) : formatMarkdown(m.content);
                    messagesEl.appendChild(div);
                }
                messagesEl.scrollTop = messagesEl.scrollHeight;
                break;
            }
        }
    });

    // Tell the extension host that this webview is ready. If the panel was
    // destroyed and recreated (e.g. sidebar collapse/expand, open-in-editor),
    // the host will replay chatHistory via restoreCurrentChat.
    vscode.postMessage({ type: 'webviewReady' });
</script>
</body>
</html>`;
}

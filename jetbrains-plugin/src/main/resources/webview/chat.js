/**
 * Lean AI Chat — JCEF JavaScript
 * Port of the inline JS from extension/src/sidebarHtml.ts.
 *
 * Communication with Kotlin plugin:
 *   JS → Kotlin: window.postToPlugin(JSON.stringify(msg))
 *   Kotlin → JS: window.receiveFromPlugin(jsonString) — called by ChatBridge.kt
 */

(function () {
    'use strict';

    // ── DOM References ──────────────────────────────────────────────────────
    const messagesEl = document.getElementById('messages');
    const inputEl = document.getElementById('message-input');
    const sendBtn = document.getElementById('send-btn');
    const cancelBtn = document.getElementById('cancel-btn');
    const statusText = document.getElementById('status-text');
    const metricsBadge = document.getElementById('metrics-badge');
    const commandHint = document.getElementById('command-hint');

    // ── State ────────────────────────────────────────────────────────────────
    let isStreaming = false;
    let currentAssistantEl = null;
    let currentThinkingEl = null;
    let assistantBuffer = '';
    let thinkingBuffer = '';

    // ── Slash command definitions ────────────────────────────────────────────
    const SLASH_COMMANDS = [
        { cmd: 'init', desc: 'Index workspace & generate context' },
        { cmd: 'agent', desc: 'Start agent workflow (plan → approve → execute)', hasArgs: true },
        { cmd: 'fix', desc: 'Bug-fix mode (skip planning)', hasArgs: true },
        { cmd: 'request', desc: 'Open-ended task mode', hasArgs: true },
        { cmd: 'skill', desc: 'Run a skill via request workflow (auto-branch)', hasArgs: true },
        { cmd: 'guide', desc: 'Regenerate framework guide' },
        { cmd: 'style', desc: 'Regenerate code style guide' },
        { cmd: 'scaffold', desc: 'Create project from template' },
        { cmd: 'approve', desc: 'Approve pending plan' },
        { cmd: 'reject', desc: 'Reject plan with feedback', hasArgs: true },
        { cmd: 'resume', desc: 'Resume paused session' },
        { cmd: 'reboot', desc: 'Restart backend server' },
    ];

    // ── Input Handling ───────────────────────────────────────────────────────

    inputEl.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    inputEl.addEventListener('input', function () {
        // Auto-resize textarea
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 120) + 'px';

        // Show slash command hints
        const text = this.value;
        if (text.startsWith('/')) {
            const partial = text.split(' ')[0].slice(1).toLowerCase();
            const matches = SLASH_COMMANDS.filter(c => c.cmd.startsWith(partial));
            if (matches.length > 0 && partial.length > 0) {
                commandHint.textContent = matches.map(c => '/' + c.cmd + ' — ' + c.desc).join('\n');
                commandHint.style.display = 'block';
            } else {
                commandHint.style.display = 'none';
            }
        } else {
            commandHint.style.display = 'none';
        }
    });

    sendBtn.addEventListener('click', sendMessage);
    cancelBtn.addEventListener('click', function () {
        postToPlugin({ command: 'cancel' });
    });

    function sendMessage() {
        const text = inputEl.value.trim();
        if (!text) return;

        inputEl.value = '';
        inputEl.style.height = 'auto';
        commandHint.style.display = 'none';

        // Check for slash commands
        if (text.startsWith('/')) {
            const parts = text.split(/\s+/);
            const cmd = parts[0].slice(1).toLowerCase();
            const args = parts.slice(1).join(' ');
            postToPlugin({ command: 'slashCommand', cmd: cmd, args: args });
            addMessage('user', text);
        } else {
            postToPlugin({ command: 'sendMessage', text: text });
            addMessage('user', text);
        }
    }

    // ── Message Rendering ────────────────────────────────────────────────────

    function addMessage(type, content) {
        const div = document.createElement('div');
        div.className = 'message ' + type;
        div.innerHTML = renderContent(content);
        messagesEl.appendChild(div);
        scrollToBottom();
        return div;
    }

    function renderContent(text) {
        // Basic markdown rendering (code blocks, bold, italic, links)
        return escapeHtml(text)
            // Code blocks
            .replace(/```(\w*)\n([\s\S]*?)```/g, function (_, lang, code) {
                return '<pre><code class="language-' + sanitizeClassName(lang) + '">' + code + '</code></pre>';
            })
            // Inline code
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            // Bold
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            // Italic
            .replace(/\*([^*]+)\*/g, '<em>$1</em>')
            // Links
            .replace(/\[([^\]]+)\]\(([^)]+)\)/g, function (_, label, href) {
                return '<a href="' + sanitizeHref(href) + '" target="_blank" rel="noopener noreferrer">' + label + '</a>';
            })
            // Line breaks
            .replace(/\n/g, '<br>');
    }

    function escapeHtml(str) {
        return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function sanitizeClassName(value) {
        return String(value || '').replace(/[^A-Za-z0-9_-]/g, '');
    }

    function sanitizeHref(value) {
        var href = String(value || '').trim();
        if (/^(https?:|mailto:)/i.test(href)) {
            return href;
        }
        return '#';
    }

    function scrollToBottom() {
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    // ── Plugin Communication ─────────────────────────────────────────────────

    function postToPlugin(msg) {
        if (window.postToPlugin) {
            window.postToPlugin(JSON.stringify(msg));
        }
    }

    /**
     * Receive messages from Kotlin plugin (called by ChatBridge.kt).
     */
    window.receiveFromPlugin = function (jsonStr) {
        try {
            var data = typeof jsonStr === 'string' ? JSON.parse(jsonStr) : jsonStr;
            handlePluginMessage(data);
        } catch (e) {
            console.error('Failed to parse plugin message:', e);
        }
    };

    function handlePluginMessage(data) {
        switch (data.type) {
            case 'assistantContent':
                handleAssistantContent(data);
                break;
            case 'thinkingContent':
                handleThinkingContent(data);
                break;
            case 'stageChanged':
                statusText.textContent = data.stage + (data.status === 'running' ? '...' : '');
                isStreaming = data.status === 'running';
                sendBtn.style.display = isStreaming ? 'none' : '';
                cancelBtn.style.display = isStreaming ? '' : 'none';
                break;
            case 'approvalRequired':
                showApprovalCard(data.plan, data.userSummary);
                break;
            case 'toolProgress':
                showToolProgress(data.tool, data.status, data.output);
                break;
            case 'toolApprovalRequired':
                showToolApprovalCard(data.tool, data.command, data.token, data.description);
                break;
            case 'diff':
                showDiff(data.filePath, data.diff);
                break;
            case 'testResult':
                showTestResult(data.passed, data.output);
                break;
            case 'checkpoint':
                addMessage('system', '\u2713 Step ' + data.stepIndex + ': ' + data.description);
                break;
            case 'branchCreated':
                addMessage('system', 'Branch created: ' + data.branchName);
                break;
            case 'mergeComplete':
                addMessage('system', 'Merged' + (data.mergeSha ? ' (' + data.mergeSha.slice(0, 7) + ')' : ''));
                break;
            case 'metricsUpdate':
                updateMetrics(data.contextPercent, data.promptTokens, data.contextWindow);
                break;
            case 'contextRefreshed':
                addMessage('system', '\u2014 Context refreshed \u2014');
                break;
            case 'complete':
                handleComplete(data);
                break;
            case 'cancelled':
                statusText.textContent = 'Cancelled';
                isStreaming = false;
                sendBtn.style.display = '';
                cancelBtn.style.display = 'none';
                addMessage('system', 'Workflow cancelled.');
                break;
            case 'error':
                addMessage('error', data.message);
                if (!data.recoverable) {
                    isStreaming = false;
                    sendBtn.style.display = '';
                    cancelBtn.style.display = 'none';
                    statusText.textContent = 'Error';
                }
                break;
            case 'statusMessage':
                addMessage('system', data.text);
                break;
            case 'errorMessage':
                addMessage('error', data.text);
                break;
            case 'suggestedPrompt':
                showSuggestedPrompt(data.prompt);
                break;
            case 'clarificationNeeded':
                handleClarification(data.questions);
                break;
        }
    }

    // ── Content Streaming ────────────────────────────────────────────────────

    function handleAssistantContent(data) {
        if (data.done) {
            // Final content — replace with rendered markdown
            if (currentAssistantEl) {
                currentAssistantEl.innerHTML = renderContent(data.content || assistantBuffer);
            } else {
                addMessage('assistant', data.content || assistantBuffer);
            }
            currentAssistantEl = null;
            assistantBuffer = '';
            return;
        }

        if (data.streaming) {
            if (!currentAssistantEl) {
                currentAssistantEl = addMessage('assistant', '');
                assistantBuffer = '';
            }
            assistantBuffer += data.content;
            currentAssistantEl.textContent = assistantBuffer;
            scrollToBottom();
        } else {
            addMessage('assistant', data.content);
        }
    }

    function handleThinkingContent(data) {
        if (data.done) {
            currentThinkingEl = null;
            thinkingBuffer = '';
            return;
        }

        if (data.streaming) {
            if (!currentThinkingEl) {
                var block = document.createElement('div');
                block.className = 'thinking-block';
                block.innerHTML = '<span class="thinking-toggle" onclick="this.nextElementSibling.classList.toggle(\'expanded\')">' +
                    '\u25B6 Thinking...</span><div class="thinking-content"></div>';
                messagesEl.appendChild(block);
                currentThinkingEl = block.querySelector('.thinking-content');
                thinkingBuffer = '';
            }
            thinkingBuffer += data.content;
            currentThinkingEl.textContent = thinkingBuffer;
        }
    }

    // ── UI Components ────────────────────────────────────────────────────────

    function showApprovalCard(plan, userSummary) {
        var card = document.createElement('div');
        card.className = 'approval-card';
        card.innerHTML =
            '<h3>Plan Ready for Approval</h3>' +
            (userSummary ? '<p>' + escapeHtml(userSummary) + '</p>' : '') +
            '<div class="plan-content">' + escapeHtml(plan) + '</div>' +
            '<div class="approval-buttons">' +
            '  <button class="btn-approve" onclick="approvePlan(this)">Approve</button>' +
            '  <button class="btn-reject" onclick="rejectPlan(this)">Reject</button>' +
            '</div>';
        messagesEl.appendChild(card);
        scrollToBottom();
    }

    window.approvePlan = function (btn) {
        btn.parentElement.innerHTML = '<span style="color:#4ec990;">\u2713 Approved</span>';
        postToPlugin({ command: 'approve' });
    };

    window.rejectPlan = function (btn) {
        var feedback = prompt('Provide feedback for rejection:');
        if (feedback !== null) {
            btn.parentElement.innerHTML = '<span style="color:#ff6b68;">\u2717 Rejected</span>';
            postToPlugin({ command: 'reject', feedback: feedback });
        }
    };

    function showToolApprovalCard(tool, command, token, description) {
        var card = document.createElement('div');
        card.className = 'tool-approval-card';
        card.innerHTML =
            '<strong>Command approval needed</strong>' +
            (description ? '<p>' + escapeHtml(description) + '</p>' : '') +
            '<div class="command-display">' + escapeHtml(command) + '</div>' +
            '<div class="approval-buttons">' +
            '  <button class="btn-approve">Allow</button>' +
            '  <button class="btn-reject">Deny</button>' +
            '</div>';
        card.querySelector('.btn-approve').addEventListener('click', function () {
            approveToolExec(this, token);
        });
        card.querySelector('.btn-reject').addEventListener('click', function () {
            denyToolExec(this, token);
        });
        messagesEl.appendChild(card);
        scrollToBottom();
    }

    window.approveToolExec = function (btn, token) {
        btn.parentElement.innerHTML = '<span style="color:#4ec990;">\u2713 Allowed</span>';
        postToPlugin({ command: 'approveTool', token: token });
    };

    window.denyToolExec = function (btn, token) {
        btn.parentElement.innerHTML = '<span style="color:#ff6b68;">\u2717 Denied</span>';
        postToPlugin({ command: 'denyTool', token: token });
    };

    function showToolProgress(tool, status, output) {
        var div = document.createElement('div');
        div.className = 'tool-progress ' + status;
        var icon = status === 'completed' ? '\u2713' : status === 'failed' ? '\u2717' : '\u25B6';
        div.innerHTML = '<span class="tool-icon">' + icon + '</span> ' + escapeHtml(tool);
        if (output && status !== 'running') {
            div.innerHTML += '<pre style="margin-top:4px;font-size:11px;">' + escapeHtml(output.slice(-500)) + '</pre>';
        }
        messagesEl.appendChild(div);
        scrollToBottom();
    }

    function showDiff(filePath, diff) {
        var div = document.createElement('div');
        div.className = 'message system';
        var diffHtml = diff.split('\n').map(function (line) {
            if (line.startsWith('+') && !line.startsWith('+++')) return '<span class="diff-add">' + escapeHtml(line) + '</span>';
            if (line.startsWith('-') && !line.startsWith('---')) return '<span class="diff-del">' + escapeHtml(line) + '</span>';
            if (line.startsWith('@@') || line.startsWith('---') || line.startsWith('+++')) return '<span class="diff-header">' + escapeHtml(line) + '</span>';
            return escapeHtml(line);
        }).join('\n');
        div.innerHTML = '<strong>' + escapeHtml(filePath) + '</strong><div class="diff-block">' + diffHtml + '</div>';
        messagesEl.appendChild(div);
        scrollToBottom();
    }

    function showTestResult(passed, output) {
        var div = addMessage(passed ? 'system' : 'error',
            (passed ? '\u2713 Tests passed' : '\u2717 Tests failed'));
        if (output) {
            var lines = output.split('\n');
            var display = passed ? lines.slice(0, 20) : lines.slice(-30);
            div.innerHTML += '<pre style="font-size:11px;margin-top:4px;">' + escapeHtml(display.join('\n')) + '</pre>';
        }
    }

    function showSuggestedPrompt(prompt) {
        var div = document.createElement('div');
        div.className = 'message system';
        div.innerHTML = '<strong>Suggested Agent Prompt:</strong>' +
            '<pre style="margin:6px 0;white-space:pre-wrap;">' + escapeHtml(prompt) + '</pre>' +
            '<button class="btn-approve" onclick="sendSuggestedPrompt(this)" style="font-size:11px;padding:4px 10px;">Send to Agent</button>';
        messagesEl.appendChild(div);
        scrollToBottom();
    }

    window.sendSuggestedPrompt = function (btn) {
        var pre = btn.previousElementSibling;
        var prompt = pre.textContent;
        btn.remove();
        postToPlugin({ command: 'slashCommand', cmd: 'agent', args: prompt });
        addMessage('user', '/agent ' + prompt);
    };

    function handleClarification(questions) {
        var content = 'The assistant needs clarification:\n' + questions.map(function (q, i) {
            return (i + 1) + '. ' + q;
        }).join('\n');
        addMessage('assistant', content);
        // Re-enable input for response
        isStreaming = false;
        sendBtn.style.display = '';
        cancelBtn.style.display = 'none';
        inputEl.focus();
    }

    function updateMetrics(contextPercent, promptTokens, contextWindow) {
        var pct = Math.round(contextPercent);
        metricsBadge.textContent = pct + '% context';
        metricsBadge.style.display = '';
        metricsBadge.className = 'metrics-badge' +
            (pct >= 85 ? ' critical' : pct >= 70 ? ' warning' : '');
    }

    function handleComplete(data) {
        statusText.textContent = 'Complete';
        isStreaming = false;
        sendBtn.style.display = '';
        cancelBtn.style.display = 'none';

        var summary = data.summary || 'Workflow completed.';
        var msg = '\u2713 ' + summary;
        if (data.filesModified && data.filesModified.length > 0) {
            msg += '\n\nFiles modified: ' + data.filesModified.join(', ');
        }
        if (data.tokensPerSecond && data.tokensPerSecond > 0) {
            msg += '\n(' + data.tokensPerSecond.toFixed(1) + ' tok/s)';
        }
        addMessage('system', msg);
    }

    // ── Initial focus ────────────────────────────────────────────────────────
    inputEl.focus();

})();

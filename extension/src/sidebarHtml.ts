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

    .metrics-badge {
        display: none;
        font-size: 10px;
        padding: 2px 6px;
        border-radius: 10px;
        background: var(--vscode-badge-background);
        color: var(--vscode-badge-foreground);
        font-weight: 500;
        font-variant-numeric: tabular-nums;
        opacity: 0.85;
    }
    .metrics-badge.visible { display: inline-block; }
    .metrics-badge.warning {
        background: var(--vscode-inputValidation-warningBackground, #332b00);
        color: var(--vscode-inputValidation-warningForeground, #ffcc00);
    }
    .metrics-badge.critical {
        background: var(--vscode-inputValidation-errorBackground, #5a1d1d);
        color: var(--vscode-errorForeground, #f48771);
    }

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

    .messages-wrapper {
        flex: 1;
        position: relative;
        overflow: hidden;
        min-height: 0;
    }
    .messages {
        height: 100%;
        overflow-y: auto;
        padding: 12px;
        display: flex;
        flex-direction: column;
        gap: 12px;
    }
    .jump-to-bottom {
        display: none;
        position: absolute;
        bottom: 8px;
        left: 50%;
        transform: translateX(-50%);
        padding: 4px 12px;
        border-radius: 12px;
        background: var(--vscode-button-background);
        color: var(--vscode-button-foreground);
        border: none;
        cursor: pointer;
        font-size: 11px;
        font-family: inherit;
        opacity: 0.9;
        z-index: 10;
        box-shadow: 0 2px 6px rgba(0,0,0,0.3);
    }
    .jump-to-bottom:hover {
        opacity: 1;
    }
    .jump-to-bottom.visible {
        display: block;
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
    .msg-tool-activity {
        color: var(--vscode-descriptionForeground);
        font-size: 0.85em;
        padding: 2px 10px;
        opacity: 0.8;
    }
    .msg-tool-activity .tool-done {
        color: var(--vscode-testing-iconPassed);
    }

    /* First-boot setup guide */
    .setup-guide {
        align-self: flex-start;
        background: var(--vscode-editor-background);
        border: 1px solid var(--vscode-panel-border);
        border-radius: 8px;
        padding: 14px 16px;
        max-width: 95%;
        line-height: 1.5;
    }
    .setup-guide h3 {
        margin: 0 0 4px 0;
        font-size: 15px;
    }
    .setup-guide .setup-tagline {
        opacity: 0.7;
        font-size: 12px;
        margin-bottom: 12px;
    }
    .setup-guide .setup-step {
        margin-bottom: 10px;
    }
    .setup-guide .setup-step-title {
        font-weight: 600;
        margin-bottom: 4px;
    }
    .setup-guide .setup-step-note {
        font-size: 11px;
        opacity: 0.7;
        margin-top: 2px;
    }
    .setup-cmd {
        display: flex;
        align-items: center;
        background: var(--vscode-textBlockQuote-background, rgba(127,127,127,0.1));
        border-radius: 4px;
        padding: 4px 8px;
        margin: 4px 0;
        font-family: var(--vscode-editor-font-family, monospace);
        font-size: 12px;
        gap: 8px;
    }
    .setup-cmd code {
        flex: 1;
        word-break: break-all;
    }
    .setup-cmd .copy-btn {
        background: none;
        border: none;
        cursor: pointer;
        padding: 2px 4px;
        border-radius: 3px;
        font-size: 13px;
        opacity: 0.6;
        flex-shrink: 0;
    }
    .setup-cmd .copy-btn:hover {
        opacity: 1;
        background: var(--vscode-toolbar-hoverBackground);
    }
    .setup-guide a {
        color: var(--vscode-textLink-foreground);
        text-decoration: none;
    }
    .setup-guide a:hover {
        text-decoration: underline;
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
    .code-block-wrapper {
        position: relative;
        margin: 4px 0;
    }
    .code-block-wrapper pre {
        margin: 0;
        padding-right: 60px;
    }
    .code-block-toolbar {
        position: absolute;
        top: 4px;
        right: 4px;
        display: flex;
        gap: 2px;
        opacity: 0;
        transition: opacity 0.15s;
    }
    .code-block-wrapper:hover .code-block-toolbar {
        opacity: 1;
    }
    .code-block-toolbar button {
        background: var(--vscode-button-secondaryBackground);
        color: var(--vscode-button-secondaryForeground);
        border: none;
        border-radius: 3px;
        cursor: pointer;
        padding: 2px 6px;
        font-size: 12px;
        line-height: 1;
    }
    .code-block-toolbar button:hover {
        background: var(--vscode-button-background);
        color: var(--vscode-button-foreground);
    }
    .file-diff-link {
        color: var(--vscode-textLink-foreground);
        cursor: pointer;
        text-decoration: none;
    }
    .file-diff-link:hover {
        text-decoration: underline;
        color: var(--vscode-textLink-activeForeground);
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

    /* Plan approval: human-readable summary + collapsible technical details */
    .plan-user-summary {
        line-height: 1.55;
        margin-bottom: 10px;
        white-space: pre-wrap;
    }
    .vision-desc {
        margin-top: 6px;
        border: 1px solid var(--vscode-panel-border);
        border-radius: 4px;
        padding: 6px 8px;
    }
    .vision-desc > summary {
        cursor: pointer;
        color: var(--vscode-descriptionForeground);
        font-style: italic;
        font-size: 11px;
    }
    .vision-desc > summary:hover {
        color: var(--vscode-foreground);
    }
    .vision-desc[open] > summary {
        margin-bottom: 6px;
    }
    .vision-desc pre {
        white-space: pre-wrap;
        font-size: 11px;
        opacity: 0.85;
    }
    .plan-details {
        margin-top: 6px;
        border-top: 1px solid var(--vscode-panel-border);
        padding-top: 6px;
    }
    .plan-details > summary {
        cursor: pointer;
        font-size: 11px;
        opacity: 0.65;
        user-select: none;
        padding: 2px 0;
        list-style: disclosure-closed;
    }
    .plan-details[open] > summary {
        list-style: disclosure-open;
        margin-bottom: 6px;
    }
    .plan-details-inner {
        font-size: 11px;
        opacity: 0.75;
    }
    .plan-validation-warnings {
        margin: 0 0 10px 0;
        padding: 8px 10px;
        border-left: 3px solid var(--vscode-editorWarning-foreground, #d4a94a);
        background: var(--vscode-editorWarning-background, rgba(212, 169, 74, 0.08));
        border-radius: 3px;
        font-size: 12px;
    }
    .plan-validation-warnings .pvw-title {
        font-weight: 600;
        margin-bottom: 4px;
        color: var(--vscode-editorWarning-foreground, #d4a94a);
    }
    .plan-validation-warnings ul {
        margin: 0;
        padding-left: 18px;
    }
    .plan-validation-warnings li {
        margin: 2px 0;
        line-height: 1.4;
    }

    .thinking-details {
        margin: 4px 0;
        border: 1px solid var(--vscode-panel-border);
        border-radius: 4px;
        padding: 4px 8px;
    }
    .thinking-details > summary {
        cursor: pointer;
        font-size: 11px;
        user-select: none;
        padding: 2px 0;
        list-style: disclosure-closed;
        color: var(--vscode-foreground);
        opacity: 0.8;
    }
    .thinking-details[open] > summary {
        list-style: disclosure-open;
        margin-bottom: 4px;
    }
    .thinking-pre {
        font-size: 11px;
        white-space: pre-wrap;
        word-break: break-word;
        margin: 0;
        max-height: 300px;
        overflow-y: auto;
        color: var(--vscode-foreground);
        opacity: 0.85;
    }

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

    .tab-bar {
        display: flex;
        overflow-x: auto;
        border-bottom: 1px solid var(--vscode-panel-border);
        flex-shrink: 0;
        gap: 0;
        scrollbar-width: thin;
        min-height: 0;
    }
    .tab-bar:empty { display: none; }
    .tab-bar::-webkit-scrollbar { height: 3px; }
    .tab {
        padding: 5px 10px;
        font-size: 15px;
        cursor: pointer;
        white-space: nowrap;
        border-bottom: 2px solid transparent;
        opacity: 0.6;
        display: flex;
        align-items: center;
        gap: 6px;
        flex-shrink: 0;
        user-select: none;
    }
    .tab:hover { opacity: 0.85; }
    .tab.active {
        opacity: 1;
        border-bottom-color: var(--vscode-focusBorder);
    }
    .tab .tab-close {
        font-size: 16px;
        opacity: 0.4;
        cursor: pointer;
        padding: 0 4px;
        line-height: 1;
    }
    .tab .tab-close:hover { opacity: 1; }
    .tab.read-only .tab-label { font-style: italic; }

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
    .input-area .stop-btn {
        background: var(--vscode-inputValidation-errorBackground, #5a1d1d);
        border: 1px solid var(--vscode-inputValidation-errorBorder, #be1100);
        color: var(--vscode-errorForeground, #f48771);
        padding: 6px 10px;
        display: none;
    }
    .input-area .stop-btn:hover {
        background: var(--vscode-inputValidation-errorBorder, #be1100);
        color: #fff;
    }
    .input-area.drag-over {
        border-color: var(--vscode-focusBorder);
        background: var(--vscode-list-hoverBackground);
    }

    /* Voice controls */
    .voice-controls {
        padding: 4px 12px;
        display: flex;
        gap: 10px;
        align-items: center;
        border-bottom: 1px solid var(--vscode-panel-border);
        flex-shrink: 0;
        flex-wrap: wrap;
    }
    .voice-toggle {
        display: flex;
        align-items: center;
        gap: 3px;
        font-size: 11px;
        cursor: pointer;
        opacity: 0.8;
    }
    .voice-toggle:hover { opacity: 1; }
    .voice-toggle input { accent-color: var(--vscode-button-background); }
    .voice-select {
        font-size: 11px;
        padding: 1px 4px;
        background: var(--vscode-input-background);
        color: var(--vscode-input-foreground);
        border: 1px solid var(--vscode-input-border);
        border-radius: 3px;
        max-width: 120px;
    }
    .voice-speed {
        width: 60px;
        accent-color: var(--vscode-button-background);
    }
    .voice-speed-label {
        font-size: 10px;
        opacity: 0.7;
        min-width: 24px;
    }
    .voice-stop-tts {
        background: none;
        border: 1px solid var(--vscode-input-border);
        color: var(--vscode-foreground);
        border-radius: 3px;
        padding: 1px 6px;
        cursor: pointer;
        font-size: 10px;
    }
    .voice-stop-tts:hover {
        background: var(--vscode-inputValidation-errorBackground, #5a1d1d);
    }
    .voice-replay-tts {
        background: none;
        border: 1px solid var(--vscode-input-border);
        color: var(--vscode-foreground);
        border-radius: 3px;
        padding: 1px 6px;
        cursor: pointer;
        font-size: 10px;
    }
    .voice-replay-tts:hover {
        background: var(--vscode-inputValidation-infoBackground, #063b49);
    }
    .voice-setup-hint {
        font-size: 11px;
        color: var(--vscode-editorWarning-foreground, #cca700);
        margin-left: auto;
    }
    .voice-setup-hint a {
        color: var(--vscode-textLink-foreground);
        text-decoration: underline;
        cursor: pointer;
    }

    /* Mic button */
    .mic-btn {
        background: none;
        border: 1px solid var(--vscode-input-border);
        color: var(--vscode-foreground);
        border-radius: 4px;
        padding: 6px 8px;
        cursor: pointer;
        align-self: flex-end;
        display: flex;
        align-items: center;
    }
    .mic-btn:hover {
        background: var(--vscode-list-hoverBackground);
    }
    .mic-btn.recording {
        background: var(--vscode-inputValidation-errorBackground, #5a1d1d);
        border-color: var(--vscode-inputValidation-errorBorder, #be1100);
        color: var(--vscode-errorForeground, #f48771);
        animation: mic-pulse 1s infinite;
    }
    @keyframes mic-pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.5; }
    }

    .attachment-strip {
        display: flex;
        gap: 6px;
        padding: 0 12px 4px;
        flex-wrap: wrap;
        flex-shrink: 0;
    }
    .attachment-strip:empty { display: none; }
    .attachment-thumb {
        position: relative;
        width: 48px;
        height: 48px;
        border-radius: 4px;
        overflow: visible;
        border: 1px solid var(--vscode-panel-border);
        flex-shrink: 0;
    }
    .attachment-thumb img {
        width: 100%;
        height: 100%;
        object-fit: cover;
        border-radius: 3px;
    }
    .attachment-thumb .remove-btn {
        position: absolute;
        top: -6px;
        right: -6px;
        width: 16px;
        height: 16px;
        border-radius: 50%;
        background: var(--vscode-inputValidation-errorBackground, #5a1d1d);
        color: #fff;
        border: 1px solid var(--vscode-inputValidation-errorBorder, #be1100);
        font-size: 10px;
        line-height: 14px;
        text-align: center;
        cursor: pointer;
        padding: 0;
    }
    .attachment-thumb .remove-btn:hover {
        background: var(--vscode-inputValidation-errorBorder, #be1100);
    }

    .context-pills {
        padding: 0 12px 6px;
        display: flex;
        gap: 6px;
        flex-wrap: wrap;
        flex-shrink: 0;
    }
    .context-pill {
        padding: 2px 8px;
        font-size: 11px;
        border-radius: 10px;
        border: 1px solid var(--vscode-input-border);
        background: transparent;
        color: var(--vscode-descriptionForeground);
        cursor: pointer;
        font-family: inherit;
        user-select: none;
        white-space: nowrap;
    }
    .context-pill:hover {
        background: var(--vscode-toolbar-hoverBackground);
        color: var(--vscode-foreground);
    }
    .context-pill.active {
        background: var(--vscode-button-background);
        color: var(--vscode-button-foreground);
        border-color: var(--vscode-button-background);
    }
    .context-pill.has-errors {
        color: var(--vscode-errorForeground);
        border-color: var(--vscode-errorForeground);
    }
    .context-pill.active.has-errors {
        background: var(--vscode-errorForeground);
        color: var(--vscode-button-foreground);
    }

    /* Execution checklist */
    .execution-checklist {
        border: 1px solid var(--vscode-panel-border);
        border-radius: 8px;
        background: var(--vscode-editor-background);
        padding: 10px 12px;
        margin: 6px 0;
        align-self: stretch;
    }
    .execution-checklist .checklist-title {
        font-weight: 600;
        font-size: 12px;
        margin-bottom: 8px;
        opacity: 0.7;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .checklist-step {
        display: flex;
        align-items: flex-start;
        gap: 8px;
        padding: 3px 0;
        font-size: 12px;
        line-height: 1.4;
    }
    .checklist-step .step-icon {
        flex-shrink: 0;
        width: 16px;
        height: 16px;
        text-align: center;
        line-height: 16px;
        font-size: 13px;
    }
    .checklist-step.pending .step-icon { opacity: 0.3; }
    .checklist-step.running .step-icon {
        color: var(--vscode-progressBar-background, #0078d4);
    }
    .checklist-step.running .step-icon svg {
        animation: checklist-spin 1s linear infinite;
    }
    .checklist-step.completed .step-icon {
        color: var(--vscode-testing-iconPassed, #28a745);
    }
    .checklist-step.completed .step-text { opacity: 0.55; }
    .checklist-step .step-text {
        flex: 1;
        word-break: break-word;
    }
    .checklist-step .step-file {
        font-family: var(--vscode-editor-font-family);
        font-size: 10px;
        opacity: 0.5;
        margin-left: 4px;
    }
    @keyframes checklist-spin {
        from { transform: rotate(0deg); }
        to { transform: rotate(360deg); }
    }

    /* Pinned execution progress card — fixed 5-line window, auto-scrolls to running step */
    .execution-progress-card {
        border-bottom: 1px solid var(--vscode-panel-border);
        border-left: 3px solid var(--vscode-progressBar-background, #0078d4);
        background: var(--vscode-editorWidget-background, var(--vscode-sideBar-background, var(--vscode-editor-background)));
        padding: 8px 12px 10px;
        flex-shrink: 0;
        position: relative;
        z-index: 1;
    }
    .execution-progress-card .progress-card-title {
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-weight: 600;
        font-size: 11px;
        opacity: 0.75;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin-bottom: 6px;
    }
    .execution-progress-card .progress-card-counter {
        font-weight: 400;
        text-transform: none;
        letter-spacing: 0;
        opacity: 0.8;
    }
    .execution-progress-card .progress-card-list {
        max-height: 110px; /* ~5 rows at ~22px each */
        overflow-y: auto;
        scroll-behavior: smooth;
    }
    .execution-progress-card .progress-card-list .checklist-step {
        padding: 2px 0;
    }
</style>
</head>
<body>

<div class="header">
    <div class="header-left">
        <span class="header-title">Lean AI</span>
        <span class="stage-badge" id="stageBadge"></span>
        <span class="metrics-badge" id="ctxBadge" title="Context window usage"></span>
        <span class="metrics-badge" id="timeBadge" title="Elapsed time"></span>
    </div>
    <div class="header-right">
        <button class="header-icon-btn" id="backBtn" title="Back to current chat" style="display:none;"><svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M7 1L1 8L7 15V10H15V6H7V1Z"/></svg></button>
        <button class="header-icon-btn" id="notesBtn" title="Notes & TODOs"><svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M3 1h10a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V2a1 1 0 0 1 1-1zm1 3v1h8V4H4zm0 3v1h8V7H4zm0 3v1h5v-1H4z"/></svg></button>
        <button class="header-icon-btn" id="editPromptsBtn" title="Edit Prompts"><svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M13.23 1h-1.46L3.52 9.25l-.16.22L1 13.59 2.41 15l4.12-2.36.22-.16L15 4.23V2.77L13.23 1zM2.41 13.59l1.51-2.53.49.49.49.49-2.49 1.55zm3.45-2.15L4.56 10.1 11 3.66l1.34 1.34-6.48 6.44z"/></svg></button>
        <button class="header-icon-btn" id="settingsBtn" title="Lean AI Settings"><svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M9.1 4.4L8.6 2H7.4L6.9 4.4L6.2 4.7L4.2 3.4L3.4 4.2L4.7 6.2L4.4 6.9L2 7.4V8.6L4.4 9.1L4.7 9.8L3.4 11.8L4.2 12.6L6.2 11.3L6.9 11.6L7.4 14H8.6L9.1 11.6L9.8 11.3L11.8 12.6L12.6 11.8L11.3 9.8L11.6 9.1L14 8.6V7.4L11.6 6.9L11.3 6.2L12.6 4.2L11.8 3.4L9.8 4.7L9.1 4.4ZM8 10C6.9 10 6 9.1 6 8C6 6.9 6.9 6 8 6C9.1 6 10 6.9 10 8C10 9.1 9.1 10 8 10Z"/></svg></button>
        <button class="header-icon-btn" id="searchBtn" title="Search conversations"><svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M11.7 10.3C12.5 9.3 13 8 13 6.5C13 2.9 10.1 0 6.5 0C2.9 0 0 2.9 0 6.5C0 10.1 2.9 13 6.5 13C8 13 9.3 12.5 10.3 11.7L14.3 15.7L15.7 14.3L11.7 10.3ZM6.5 11C4 11 2 9 2 6.5C2 4 4 2 6.5 2C9 2 11 4 11 6.5C11 9 9 11 6.5 11Z"/></svg></button>
        <button class="header-icon-btn" id="popOutBtn" title="Open chat in new window"><svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M1.5 1C.67 1 0 1.67 0 2.5v11c0 .83.67 1.5 1.5 1.5h11c.83 0 1.5-.67 1.5-1.5V10h-1v3.5a.5.5 0 0 1-.5.5h-11a.5.5 0 0 1-.5-.5v-11a.5.5 0 0 1 .5-.5H5V1H1.5zM9 1v1h3.29L7.15 7.15l.7.7L13 2.71V6h1V1H9z"/></svg></button>
        <button class="new-chat-btn" id="newChatBtn" title="New Chat">+</button>
    </div>
</div>

<div class="tab-bar" id="tabBar"></div>

<div class="search-bar" id="searchBar" style="display:none;">
    <input type="text" id="searchInput" placeholder="Search past conversations..." />
    <button class="search-clear-btn" id="searchClearBtn" title="Close search">&times;</button>
</div>

<div class="execution-progress-card" id="executionProgressCard" style="display:none;">
    <div class="progress-card-title">
        <span>Execution Progress</span>
        <span class="progress-card-counter" id="progressCardCounter"></span>
    </div>
    <div class="progress-card-list" id="progressCardList"></div>
</div>

<div class="messages-wrapper">
<div class="messages" id="messages">
    <div class="msg msg-system">Describe what you'd like to build or change. I'll help you refine it into a clear task for the agent.</div>
</div>
<button class="jump-to-bottom" id="jumpToBottom" title="Jump to latest">&#8595; New messages</button>
</div>
<div class="thinking" id="thinking">Processing...</div>

<div class="approval-bar" id="approvalBar">
    <button class="approve-btn" id="approveBtn">Approve Plan</button>
    <span class="approval-hint">or type feedback below to revise</span>
</div>

<div class="context-pills" id="contextPills">
    <button class="context-pill" id="problemsPill" title="Include Problems tab (errors &amp; warnings) as context for the agent">&#9888; Problems (<span id="problemsCount">0</span>)</button>
    <button class="context-pill" id="debugPill" title="Include active debug session state (call stack, variables) as context" style="display:none;">&#9679; Debug State</button>
</div>

<div class="voice-controls" id="voiceControls" style="display:none;">
    <label class="voice-toggle"><input type="checkbox" id="sttToggle"> STT</label>
    <label class="voice-toggle"><input type="checkbox" id="ttsToggle"> TTS</label>
    <label class="voice-toggle"><input type="checkbox" id="wakeWordToggle"> Wake</label>
    <select id="voiceSelect" class="voice-select" title="TTS Voice" style="display:none;"></select>
    <input type="range" id="speedSlider" class="voice-speed" min="0.5" max="2.0" step="0.1" value="1.0" title="TTS Speed" style="display:none;">
    <span id="speedLabel" class="voice-speed-label" style="display:none;">1.0x</span>
    <button class="voice-stop-tts" id="stopTtsBtn" style="display:none;" title="Stop speaking">&#9724;</button>
    <button class="voice-replay-tts" id="replayTtsBtn" style="display:none;" title="Replay last response">&#9654;</button>
    <span id="voiceSetupHint" class="voice-setup-hint" style="display:none;">&#9888; Deps missing &mdash; <a href="#" id="voiceSetupLink">setup steps</a></span>
</div>

<div class="attachment-strip" id="attachmentStrip"></div>

<div class="input-area">
    <textarea id="input" rows="2" placeholder="Ask a question or describe a task..." autofocus></textarea>
    <button id="micBtn" class="mic-btn" title="Record voice (STT)" style="display:none;">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M8 10a2 2 0 0 0 2-2V4a2 2 0 1 0-4 0v4a2 2 0 0 0 2 2z"/><path d="M12 8a1 1 0 0 0-2 0 2 2 0 1 1-4 0 1 1 0 0 0-2 0 4 4 0 0 0 3 3.87V13H5.5a.5.5 0 0 0 0 1h5a.5.5 0 0 0 0-1H9v-1.13A4 4 0 0 0 12 8z"/></svg>
    </button>
    <button id="stopBtn" class="stop-btn" title="Stop the running workflow">Stop</button>
    <button id="sendBtn">Send</button>
</div>

<script>
    const vscode = acquireVsCodeApi();
    const messagesEl = document.getElementById('messages');
    const inputEl = document.getElementById('input');
    const sendBtn = document.getElementById('sendBtn');
    const stopBtn = document.getElementById('stopBtn');
    const thinkingEl = document.getElementById('thinking');
    const newChatBtn = document.getElementById('newChatBtn');
    const approvalBar = document.getElementById('approvalBar');
    const approveBtn = document.getElementById('approveBtn');
    const stageBadge = document.getElementById('stageBadge');
    const searchBtn = document.getElementById('searchBtn');
    const editPromptsBtn = document.getElementById('editPromptsBtn');
    const notesBtn = document.getElementById('notesBtn');
    const settingsBtn = document.getElementById('settingsBtn');
    const backBtn = document.getElementById('backBtn');
    const searchBar = document.getElementById('searchBar');
    const searchInput = document.getElementById('searchInput');
    const searchClearBtn = document.getElementById('searchClearBtn');
    const tabBar = document.getElementById('tabBar');
    const ctxBadge = document.getElementById('ctxBadge');
    const timeBadge = document.getElementById('timeBadge');

    const problemsPill = document.getElementById('problemsPill');
    const debugPill = document.getElementById('debugPill');
    const problemsCount = document.getElementById('problemsCount');

    let sending = false;
    let runtimeInterval = null;
    let runtimeStart = null;
    let lastTimestamp = null;
    let searchMode = false;
    let viewMode = 'sidebar';
    let savedMessagesHtml = null;
    let searchDebounceTimer = null;
    let currentStreamDiv = null; // AI bubble being filled during chat streaming
    let currentPlanningDiv = null; // AI bubble being filled during planning streaming
    let problemsActive = false;
    let debugActive = false;
    let visionAvailable = false; // Set by health check response
    // Pinned execution progress card state
    const progressCardEl = document.getElementById('executionProgressCard');
    const progressCardList = document.getElementById('progressCardList');
    const progressCardCounter = document.getElementById('progressCardCounter');
    let progressCardTotal = 0;
    let progressCardCompleted = 0;

    // --- Voice state ---
    const voiceControls = document.getElementById('voiceControls');
    const sttToggle = document.getElementById('sttToggle');
    const ttsToggle = document.getElementById('ttsToggle');
    const wakeWordToggle = document.getElementById('wakeWordToggle');
    const micBtn = document.getElementById('micBtn');
    const stopTtsBtn = document.getElementById('stopTtsBtn');
    const replayTtsBtn = document.getElementById('replayTtsBtn');
    const voiceSelect = document.getElementById('voiceSelect');
    const speedSlider = document.getElementById('speedSlider');
    const speedLabel = document.getElementById('speedLabel');
    const voiceSetupHint = document.getElementById('voiceSetupHint');
    const voiceSetupLink = document.getElementById('voiceSetupLink');
    let sttEnabled = false;
    let ttsEnabled = false;
    let wakeWordEnabled = false;
    let isRecording = false;
    let wakeWordTriggered = false;
    let ttsPlaying = false;
    let ttsQueue = [];
    let ttsReplayCache = []; // Accumulated AudioBuffer objects for replay
    // Web Audio API — bypasses autoplay restrictions in webview iframes
    let audioCtx = null;
    // Gapless playback state
    let ttsDecodedQueue = [];   // Pre-decoded AudioBuffer objects
    let ttsNextStartTime = 0;   // AudioContext time for next buffer
    let ttsScheduledSources = []; // Active BufferSourceNodes for cleanup

    // --- Image attachments ---
    const attachmentStrip = document.getElementById('attachmentStrip');
    let pendingAttachments = []; // [{data: base64, filename: string, mimeType: string}]
    const MAX_ATTACHMENTS = 5;
    const MAX_IMAGE_SIZE = 10 * 1024 * 1024; // 10 MB

    function addImageAttachment(file) {
        if (!file.type.startsWith('image/')) return;
        if (pendingAttachments.length >= MAX_ATTACHMENTS) return;
        if (file.size > MAX_IMAGE_SIZE) return;

        const reader = new FileReader();
        reader.onload = () => {
            const base64 = reader.result.split(',')[1];
            pendingAttachments.push({
                data: base64,
                filename: file.name || 'pasted-image.png',
                mimeType: file.type,
            });
            renderAttachments();
        };
        reader.readAsDataURL(file);
    }

    function renderAttachments() {
        attachmentStrip.innerHTML = '';
        pendingAttachments.forEach((att, idx) => {
            const thumb = document.createElement('div');
            thumb.className = 'attachment-thumb';
            const img = document.createElement('img');
            img.src = 'data:' + att.mimeType + ';base64,' + att.data;
            img.alt = att.filename;
            const btn = document.createElement('button');
            btn.className = 'remove-btn';
            btn.textContent = '\\u00d7';
            btn.title = 'Remove image';
            btn.addEventListener('click', () => {
                pendingAttachments.splice(idx, 1);
                renderAttachments();
            });
            thumb.appendChild(img);
            thumb.appendChild(btn);
            attachmentStrip.appendChild(thumb);
        });
    }

    function clearAttachments() {
        pendingAttachments = [];
        renderAttachments();
    }

    // --- Smart auto-scroll: only scroll to bottom when user is already there ---
    const jumpBtn = document.getElementById('jumpToBottom');
    let userScrolledUp = false;

    function isNearBottom(el, threshold) {
        return el.scrollHeight - el.scrollTop - el.clientHeight < (threshold || 80);
    }

    function scrollToBottom() {
        if (!userScrolledUp) {
            messagesEl.scrollTop = messagesEl.scrollHeight;
        } else {
            jumpBtn.classList.add('visible');
        }
    }

    function forceScrollToBottom() {
        userScrolledUp = false;
        jumpBtn.classList.remove('visible');
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    messagesEl.addEventListener('scroll', function() {
        userScrolledUp = !isNearBottom(messagesEl, 80);
        if (!userScrolledUp) {
            jumpBtn.classList.remove('visible');
        }
    });

    jumpBtn.addEventListener('click', forceScrollToBottom);

    // Delegated click handler for file-diff links in completion summary
    messagesEl.addEventListener('click', function(e) {
        var link = e.target.closest('.file-diff-link');
        if (link) {
            e.preventDefault();
            vscode.postMessage({
                type: 'openFileDiff',
                file: link.dataset.file,
                baseBranch: link.dataset.base,
            });
        }
    });

    // ── Tab management ──

    const tabs = new Map(); // id -> { id, label, messagesHtml, readOnly }
    let activeTabId = 'current';

    // Initialize the default "Chat" tab (always present, not closeable)
    tabs.set('current', { id: 'current', label: 'Chat', messagesHtml: null, readOnly: false });

    function renderTabs() {
        // Only show tab bar when there are session tabs (more than just "current")
        if (tabs.size <= 1) {
            tabBar.innerHTML = '';
            return;
        }
        tabBar.innerHTML = '';
        for (const [id, tab] of tabs) {
            const el = document.createElement('div');
            el.className = 'tab' + (id === activeTabId ? ' active' : '') + (tab.readOnly ? ' read-only' : '');
            el.dataset.tabId = id;

            const label = document.createElement('span');
            label.className = 'tab-label';
            label.textContent = tab.label;
            el.appendChild(label);

            if (id !== 'current') {
                const closeBtn = document.createElement('span');
                closeBtn.className = 'tab-close';
                closeBtn.textContent = '\\u00d7';
                closeBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    closeTab(id);
                });
                el.appendChild(closeBtn);
            }

            el.addEventListener('click', () => switchTab(id));
            tabBar.appendChild(el);
        }
    }

    function switchTab(tabId) {
        if (tabId === activeTabId) return;
        const currentTab = tabs.get(activeTabId);
        if (currentTab) {
            currentTab.messagesHtml = messagesEl.innerHTML;
        }

        activeTabId = tabId;
        const targetTab = tabs.get(tabId);
        if (targetTab && targetTab.messagesHtml !== null) {
            messagesEl.innerHTML = targetTab.messagesHtml;
        }

        // Toggle input area based on readOnly
        if (targetTab && targetTab.readOnly) {
            inputEl.disabled = true;
            sendBtn.disabled = true;
            inputEl.placeholder = 'Session history (read-only)';
        } else {
            inputEl.disabled = false;
            sendBtn.disabled = sending;
            inputEl.placeholder = 'Ask a question or describe a task...';
        }

        renderTabs();
    }

    function openSessionTab(id, title, messagesHtml) {
        if (tabs.has(id)) {
            // Tab already open — just switch to it
            switchTab(id);
            return;
        }
        // Save current tab state
        const currentTab = tabs.get(activeTabId);
        if (currentTab) {
            currentTab.messagesHtml = messagesEl.innerHTML;
        }

        // Create new read-only tab
        const shortTitle = title.length > 20 ? title.slice(0, 20) + '...' : title;
        tabs.set(id, { id, label: shortTitle, messagesHtml: messagesHtml, readOnly: true });
        activeTabId = id;
        messagesEl.innerHTML = messagesHtml;

        // Disable input
        inputEl.disabled = true;
        sendBtn.disabled = true;
        inputEl.placeholder = 'Session history (read-only)';

        renderTabs();
        messagesEl.scrollTop = 0;
    }

    function closeTab(tabId) {
        tabs.delete(tabId);
        if (activeTabId === tabId) {
            switchTab('current');
        }
        renderTabs();
        vscode.postMessage({ type: 'closeTab', tabId });
    }

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

    // Expose globally for inline onclick handlers in setup guide
    window.copyCmd = function(btn) {
        const code = btn.parentElement.querySelector('code');
        if (code) {
            navigator.clipboard.writeText(code.textContent).then(function() {
                btn.textContent = '✓';
                setTimeout(function() { btn.textContent = '📋'; }, 1500);
            });
        }
    };
    window.openExternal = function(url) {
        vscode.postMessage({ type: 'openExternal', url: url });
    };

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

    function addCodeBlockButtons(container) {
        var pres = container.querySelectorAll('pre');
        pres.forEach(function(pre) {
            if (pre.parentElement && pre.parentElement.classList.contains('code-block-wrapper')) return;
            if (pre.closest('.agent-prompt-wrapper')) return;
            if (pre.classList.contains('thinking-pre')) return;

            var wrapper = document.createElement('div');
            wrapper.className = 'code-block-wrapper';
            pre.parentElement.insertBefore(wrapper, pre);
            wrapper.appendChild(pre);

            var toolbar = document.createElement('div');
            toolbar.className = 'code-block-toolbar';
            toolbar.innerHTML =
                '<button onclick="copyCodeBlock(this)" title="Copy to clipboard"><svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M4 4h1V2H2v3h2V4zm7-2v2h1v1h2V2h-3zm-6 9H4v1H2v-3h2v-1H1v5h4v-2zm9-1v1h-1v2h3v-5h-2v2zM6 2H5v1h1V2zm4 0H9v1h1V2zM6 12H5v1h1v-1zm4 0H9v1h1v-1zM2 6H1v1h1V6zm0 3H1v1h1V9zm12-3h1v1h-1V6zm0 3h1v1h-1V9zM5 5h6v6H5V5z"/></svg></button>' +
                '<button onclick="sendCodeToTerminal(this)" title="Send to terminal"><svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M1 3l5 4-5 4V3zm6 7h8v1H7v-1z"/></svg></button>';
            wrapper.appendChild(toolbar);
        });
    }

    window.copyCodeBlock = function(btn) {
        var pre = btn.closest('.code-block-wrapper').querySelector('pre');
        if (pre) {
            navigator.clipboard.writeText(pre.textContent).then(function() {
                var original = btn.innerHTML;
                btn.textContent = '\\u2713';
                setTimeout(function() { btn.innerHTML = original; }, 1500);
            });
        }
    };

    window.sendCodeToTerminal = function(btn) {
        var pre = btn.closest('.code-block-wrapper').querySelector('pre');
        if (pre) {
            vscode.postMessage({ type: 'sendToTerminal', code: pre.textContent });
        }
    };

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
        addCodeBlockButtons(div);
        messagesEl.appendChild(div);
        scrollToBottom();
        return div;
    }

    function setStage(stage) {
        if (stage) {
            stageBadge.textContent = stage.replace(/_/g, ' ');
            stageBadge.classList.add('visible');
            stopBtn.style.display = 'inline-block';
        } else {
            stageBadge.classList.remove('visible');
            stopBtn.style.display = 'none';
        }
    }

    function formatElapsed(totalSeconds) {
        const h = Math.floor(totalSeconds / 3600);
        const m = Math.floor((totalSeconds % 3600) / 60);
        const s = totalSeconds % 60;
        if (h > 0) {
            return h + ':' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
        }
        return m + ':' + String(s).padStart(2, '0');
    }

    function startRuntime() {
        if (runtimeInterval) return;
        runtimeStart = Date.now();
        timeBadge.textContent = '0:00';
        timeBadge.classList.add('visible');
        runtimeInterval = setInterval(function() {
            const elapsed = Math.floor((Date.now() - runtimeStart) / 1000);
            timeBadge.textContent = formatElapsed(elapsed);
        }, 1000);
    }

    function stopRuntime() {
        if (runtimeInterval) {
            clearInterval(runtimeInterval);
            runtimeInterval = null;
        }
    }

    function resetMetrics() {
        stopRuntime();
        ctxBadge.classList.remove('visible', 'warning', 'critical');
        timeBadge.classList.remove('visible');
        runtimeStart = null;
    }

    let sendTimeout = null;
    function send() {
        const text = inputEl.value.trim();
        const hasAttachments = pendingAttachments.length > 0;
        if ((!text && !hasAttachments) || sending) return;
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
        userScrolledUp = false;
        jumpBtn.classList.remove('visible');
        // Show user message with image count if applicable
        let displayText = escapeHtml(text);
        if (hasAttachments) {
            const n = pendingAttachments.length;
            const label = n === 1 ? '1 image attached' : n + ' images attached';
            displayText = (displayText ? displayText + '<br>' : '') +
                '<span style="opacity:0.6;font-size:0.9em">[' + label + ']</span>';
        }
        addMessage(displayText, 'msg-user');
        inputEl.value = '';
        autoResize();
        const msg = { type: 'sendMessage', text };
        if (hasAttachments) {
            msg.attachments = pendingAttachments.slice();
        }
        vscode.postMessage(msg);
        clearAttachments();
        // Safety: re-enable after 120s if response is lost
        if (sendTimeout) clearTimeout(sendTimeout);
        sendTimeout = setTimeout(() => {
            if (sending) {
                sending = false;
                sendBtn.disabled = false;
            }
        }, 120000);
    }

    problemsPill.addEventListener('click', () => {
        problemsActive = !problemsActive;
        problemsPill.classList.toggle('active', problemsActive);
        vscode.postMessage({ type: 'toggleProblems', enabled: problemsActive });
    });

    debugPill.addEventListener('click', () => {
        debugActive = !debugActive;
        debugPill.classList.toggle('active', debugActive);
        vscode.postMessage({ type: 'toggleDebug', enabled: debugActive });
    });

    sendBtn.addEventListener('click', () => {
        if (ttsEnabled) { ensureAudioCtx(); }
        send();
    });
    stopBtn.addEventListener('click', () => {
        vscode.postMessage({ type: 'stopWorkflow' });
        stopBtn.style.display = 'none';
    });
    inputEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            if (ttsEnabled) { ensureAudioCtx(); }
            send();
        }
    });

    // --- Voice event handlers ---
    voiceSetupLink.addEventListener('click', (e) => {
        e.preventDefault();
        vscode.postMessage({ type: 'voiceShowSetup' });
    });

    micBtn.addEventListener('click', () => {
        if (isRecording) {
            vscode.postMessage({ type: 'sttStop' });
            micBtn.classList.remove('recording');
            isRecording = false;
        } else {
            vscode.postMessage({ type: 'sttStart', autoStop: false });
            micBtn.classList.add('recording');
            isRecording = true;
        }
    });

    sttToggle.addEventListener('change', (e) => {
        sttEnabled = e.target.checked;
        micBtn.style.display = sttEnabled ? '' : 'none';
        vscode.postMessage({ type: 'voiceToggle', feature: 'stt', enabled: sttEnabled });
    });

    ttsToggle.addEventListener('change', (e) => {
        ttsEnabled = e.target.checked;
        voiceSelect.style.display = ttsEnabled ? '' : 'none';
        speedSlider.style.display = ttsEnabled ? '' : 'none';
        speedLabel.style.display = ttsEnabled ? '' : 'none';
        vscode.postMessage({ type: 'voiceToggle', feature: 'tts', enabled: ttsEnabled });
    });

    wakeWordToggle.addEventListener('change', (e) => {
        wakeWordEnabled = e.target.checked;
        vscode.postMessage({ type: 'voiceToggle', feature: 'wakeWord', enabled: wakeWordEnabled });
    });

    voiceSelect.addEventListener('change', (e) => {
        vscode.postMessage({ type: 'voiceConfigChange', voice: e.target.value });
    });

    speedSlider.addEventListener('input', (e) => {
        const val = parseFloat(e.target.value);
        speedLabel.textContent = val.toFixed(1) + 'x';
        vscode.postMessage({ type: 'voiceConfigChange', speed: val });
    });

    function ensureAudioCtx() {
        if (!audioCtx) { audioCtx = new AudioContext(); }
        if (audioCtx.state === 'suspended') { audioCtx.resume(); }
        return audioCtx;
    }

    /** Decode base64 audio to an ArrayBuffer. */
    function base64ToArrayBuffer(base64) {
        const binary = atob(base64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) { bytes[i] = binary.charCodeAt(i); }
        return bytes.buffer.slice(0);
    }

    /** Convert raw PCM Int16 base64 to an AudioBuffer (synchronous, no decodeAudioData). */
    function pcmToAudioBuffer(base64Pcm, sampleRate) {
        const ctx = ensureAudioCtx();
        const binary = atob(base64Pcm);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) { bytes[i] = binary.charCodeAt(i); }
        const int16 = new Int16Array(bytes.buffer);
        const buffer = ctx.createBuffer(1, int16.length, sampleRate);
        const channelData = buffer.getChannelData(0);
        for (let i = 0; i < int16.length; i++) {
            channelData[i] = int16[i] / 32768.0;
        }
        return buffer;
    }

    /** Schedule a decoded AudioBuffer for gapless playback. */
    function scheduleBuffer(buffer, isReplay) {
        const ctx = ensureAudioCtx();
        const source = ctx.createBufferSource();
        source.buffer = buffer;
        source.connect(ctx.destination);

        // First buffer or if we've fallen behind, start from now
        const now = ctx.currentTime;
        if (!ttsPlaying || ttsNextStartTime < now) {
            ttsNextStartTime = now;
        }

        source.start(ttsNextStartTime);
        ttsNextStartTime += buffer.duration;

        ttsPlaying = true;
        stopTtsBtn.style.display = '';
        replayTtsBtn.style.display = 'none';
        ttsScheduledSources.push(source);

        // Cache buffer for replay (skip if this IS a replay)
        if (!isReplay) {
            ttsReplayCache.push(buffer);
        }

        source.onended = () => {
            const idx = ttsScheduledSources.indexOf(source);
            if (idx !== -1) { ttsScheduledSources.splice(idx, 1); }
            // If no more sources and no more queued data, we're done
            if (ttsScheduledSources.length === 0 && ttsQueue.length === 0 && ttsDecodedQueue.length === 0) {
                ttsPlaying = false;
                stopTtsBtn.style.display = 'none';
                // Show replay button if we have cached audio
                if (ttsReplayCache.length > 0) {
                    replayTtsBtn.style.display = '';
                }
            }
        };
    }

    /** Pre-decode raw queue items and schedule decoded buffers for gapless playback. */
    function pumpTtsPipeline() {
        const ctx = ensureAudioCtx();

        // Pre-decode: convert base64 WAV chunks to AudioBuffers (cap at 3 concurrent decodes)
        while (ttsQueue.length > 0 && ttsDecodedQueue.length < 3) {
            const base64 = ttsQueue.shift();
            const arrayBuf = base64ToArrayBuffer(base64);
            ctx.decodeAudioData(arrayBuf, (decoded) => {
                ttsDecodedQueue.push(decoded);
                // Schedule any newly decoded buffers
                while (ttsDecodedQueue.length > 0) {
                    scheduleBuffer(ttsDecodedQueue.shift());
                }
                // Continue pumping if more raw items
                if (ttsQueue.length > 0) { pumpTtsPipeline(); }
            }, (err) => {
                console.error('[Lean AI] TTS decode failed:', err);
                // Continue with remaining queue
                if (ttsQueue.length > 0 || ttsDecodedQueue.length > 0) { pumpTtsPipeline(); }
            });
        }

        // Schedule any already-decoded buffers
        while (ttsDecodedQueue.length > 0) {
            scheduleBuffer(ttsDecodedQueue.shift());
        }
    }

    /** Enqueue audio for gapless playback (WAV base64 or PCM base64). */
    function playTtsAudio(base64Audio, isPcm, sampleRate) {
        if (isPcm && sampleRate) {
            // PCM: synchronous decode, schedule immediately
            const buffer = pcmToAudioBuffer(base64Audio, sampleRate);
            scheduleBuffer(buffer);
        } else {
            // WAV: async decode via pipeline
            ttsQueue.push(base64Audio);
            pumpTtsPipeline();
        }
    }

    /** Stop all TTS playback and clear queues (keeps AudioContext alive for reuse). */
    function stopTtsPlayback() {
        for (const src of ttsScheduledSources) {
            try { src.stop(); src.disconnect(); } catch {}
        }
        ttsScheduledSources = [];
        ttsDecodedQueue = [];
        ttsQueue = [];
        ttsPlaying = false;
        ttsNextStartTime = 0;
        stopTtsBtn.style.display = 'none';
        // Show replay if we have cached audio
        if (ttsReplayCache.length > 0) {
            replayTtsBtn.style.display = '';
        }
    }

    stopTtsBtn.addEventListener('click', () => {
        stopTtsPlayback();
        vscode.postMessage({ type: 'ttsStop' });
    });

    replayTtsBtn.addEventListener('click', () => {
        if (ttsReplayCache.length === 0) { return; }
        replayTtsBtn.style.display = 'none';
        ttsNextStartTime = 0;
        for (const buffer of ttsReplayCache) {
            scheduleBuffer(buffer, true);
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
    inputEl.addEventListener('paste', (e) => {
        // Intercept pasted images from clipboard
        if (visionAvailable && e.clipboardData && e.clipboardData.items) {
            for (const item of e.clipboardData.items) {
                if (item.type.startsWith('image/')) {
                    e.preventDefault();
                    const file = item.getAsFile();
                    if (file) addImageAttachment(file);
                    return;
                }
            }
        }
        setTimeout(autoResize, 0);
    });

    // --- Drag and drop images ---
    const inputArea = document.querySelector('.input-area');
    inputArea.addEventListener('dragover', (e) => {
        if (!visionAvailable) return;
        e.preventDefault();
        inputArea.classList.add('drag-over');
    });
    inputArea.addEventListener('dragleave', () => {
        inputArea.classList.remove('drag-over');
    });
    inputArea.addEventListener('drop', (e) => {
        e.preventDefault();
        inputArea.classList.remove('drag-over');
        if (!visionAvailable) return;
        const files = e.dataTransfer && e.dataTransfer.files;
        if (!files) return;
        for (const file of files) {
            addImageAttachment(file);
        }
    });

    newChatBtn.addEventListener('click', () => {
        if (searchMode) closeSearch();

        // Save the current tab's messages before archiving
        const currentTab = tabs.get('current');
        if (currentTab) {
            currentTab.messagesHtml = messagesEl.innerHTML;
        }

        // Tell the backend to persist + archive the current conversation
        // (it will reply with 'chatArchived' containing the tab info)
        vscode.postMessage({ type: 'newChat', messagesHtml: messagesEl.innerHTML });
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

    editPromptsBtn.addEventListener('click', () => {
        vscode.postMessage({ type: 'openEditPrompts' });
    });

    notesBtn.addEventListener('click', () => {
        vscode.postMessage({ type: 'openNotes' });
    });

    settingsBtn.addEventListener('click', () => {
        vscode.postMessage({ type: 'openSettings' });
    });

    searchBtn.addEventListener('click', () => {
        if (searchMode) {
            closeSearch();
        } else {
            openSearch();
        }
    });

    searchClearBtn.addEventListener('click', closeSearch);

    const popOutBtn = document.getElementById('popOutBtn');
    popOutBtn.addEventListener('click', () => {
        if (viewMode === 'popout') {
            vscode.postMessage({ type: 'returnToSidebar' });
        } else {
            vscode.postMessage({ type: 'openChatInNewWindow' });
        }
    });

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
                if (msg.show) scrollToBottom();
                break;

            case 'stage':
                setStage(msg.stage);
                if (msg.stage && !runtimeInterval) {
                    startRuntime();
                }
                break;

            case 'metricsUpdate':
                ctxBadge.textContent = 'Ctx ' + msg.contextPercent + '%';
                ctxBadge.classList.add('visible');
                ctxBadge.classList.toggle('warning', msg.contextPercent >= 75 && msg.contextPercent < 90);
                ctxBadge.classList.toggle('critical', msg.contextPercent >= 90);
                break;

            case 'metricsFinal':
                stopRuntime();
                break;

            case 'metricsReset':
                resetMetrics();
                break;

            case 'executionChecklist': {
                const steps = msg.steps || [];
                progressCardTotal = msg.total || steps.length;
                progressCardCompleted = 0;
                progressCardList.innerHTML = '';
                for (let i = 0; i < steps.length; i++) {
                    const s = steps[i];
                    const stepDiv = document.createElement('div');
                    stepDiv.className = 'checklist-step pending';
                    stepDiv.dataset.stepIndex = String(s.step_index);
                    const icon = document.createElement('span');
                    icon.className = 'step-icon';
                    icon.textContent = '\u25CB';
                    const textSpan = document.createElement('span');
                    textSpan.className = 'step-text';
                    let label = (i + 1) + '. ' + escapeHtml(s.description);
                    if (s.file_path) {
                        label += ' <span class="step-file">' + escapeHtml(s.file_path) + '</span>';
                    }
                    textSpan.innerHTML = label;
                    stepDiv.appendChild(icon);
                    stepDiv.appendChild(textSpan);
                    progressCardList.appendChild(stepDiv);
                }
                progressCardCounter.textContent = '0 / ' + progressCardTotal;
                progressCardEl.style.display = 'block';
                progressCardList.scrollTop = 0;
                break;
            }

            case 'checkpointUpdate': {
                const idx = msg.stepIndex;
                const status = msg.status;
                const stepEl = progressCardList.querySelector('[data-step-index="' + idx + '"]');
                if (!stepEl) break;
                const wasCompleted = stepEl.classList.contains('completed');
                stepEl.classList.remove('pending', 'running', 'completed');
                stepEl.classList.add(status);
                const iconEl = stepEl.querySelector('.step-icon');
                if (status === 'running') {
                    iconEl.innerHTML = '<svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zm0 1a6 6 0 1 1 0 12A6 6 0 0 1 8 2z" opacity="0.2"/><path d="M8 1a7 7 0 0 1 7 7h-1a6 6 0 0 0-6-6V1z"/></svg>';
                    stepEl.scrollIntoView({ block: 'center', behavior: 'smooth' });
                } else if (status === 'completed') {
                    iconEl.innerHTML = '';
                    iconEl.textContent = '\u2713';
                    if (!wasCompleted) {
                        progressCardCompleted += 1;
                        progressCardCounter.textContent = progressCardCompleted + ' / ' + progressCardTotal;
                    }
                }
                break;
            }

            case 'setViewMode':
                viewMode = msg.mode;
                if (msg.mode === 'popout') {
                    popOutBtn.title = 'Return to sidebar';
                    popOutBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M0 2.5A1.5 1.5 0 0 1 1.5 1H5v1H1.5a.5.5 0 0 0-.5.5v11a.5.5 0 0 0 .5.5H5v1H1.5A1.5 1.5 0 0 1 0 13.5v-11zM6 1h8.5A1.5 1.5 0 0 1 16 2.5v11a1.5 1.5 0 0 1-1.5 1.5H6V1z"/></svg>';
                } else {
                    popOutBtn.title = 'Open chat in new window';
                    popOutBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M1.5 1C.67 1 0 1.67 0 2.5v11c0 .83.67 1.5 1.5 1.5h11c.83 0 1.5-.67 1.5-1.5V10h-1v3.5a.5.5 0 0 1-.5.5h-11a.5.5 0 0 1-.5-.5v-11a.5.5 0 0 1 .5-.5H5V1H1.5zM9 1v1h3.29L7.15 7.15l.7.7L13 2.71V6h1V1H9z"/></svg>';
                }
                break;

            case 'reply':
                if (msg.streaming) {
                    // Streaming token — append to current planning bubble
                    if (!currentPlanningDiv) {
                        currentPlanningDiv = document.createElement('div');
                        currentPlanningDiv.className = 'msg msg-ai';
                        messagesEl.appendChild(currentPlanningDiv);
                    }
                    currentPlanningDiv.textContent += msg.text;
                    scrollToBottom();
                } else if (msg.done && currentPlanningDiv) {
                    // Streaming complete — apply markdown formatting
                    currentPlanningDiv.innerHTML = formatMarkdown(msg.text);
                    addCodeBlockButtons(currentPlanningDiv);
                    currentPlanningDiv = null;
                    scrollToBottom();
                } else {
                    // Non-streaming reply (existing behavior)
                    sending = false;
                    sendBtn.disabled = false;
                    if (sendTimeout) { clearTimeout(sendTimeout); sendTimeout = null; }
                    addMessage(formatMarkdown(msg.text), msg.cls || 'msg-ai');
                }
                break;

            case 'filesModifiedLinks': {
                var files = msg.files || [];
                var baseBranch = msg.baseBranch || '';
                var links = files.map(function(f) {
                    return '<a href="#" class="file-diff-link" data-file="' + escapeHtml(f) +
                        '" data-base="' + escapeHtml(baseBranch) + '">' + escapeHtml(f) + '</a>';
                }).join(', ');
                addMessage('<b>Files modified:</b> ' + links, 'msg-ai');
                break;
            }

            case 'llmThinking': {
                // Collapsible thinking/reasoning trace from the LLM
                const container = document.getElementById('messages');
                const last = container.lastElementChild;
                if (msg.streaming) {
                    // Streaming token — append without double newline
                    if (last && last.querySelector('.thinking-details')) {
                        const pre = last.querySelector('.thinking-pre');
                        if (pre) { pre.textContent += msg.text; }
                    } else {
                        const div = document.createElement('div');
                        div.className = 'msg msg-system';
                        const details = document.createElement('details');
                        details.className = 'thinking-details';
                        details.open = true;
                        const summary = document.createElement('summary');
                        summary.textContent = 'Thinking...';
                        const pre = document.createElement('pre');
                        pre.className = 'thinking-pre';
                        pre.textContent = msg.text;
                        details.appendChild(summary);
                        details.appendChild(pre);
                        div.appendChild(details);
                        container.appendChild(div);
                        scrollToBottom();
                    }
                } else {
                    // Bulk thinking content (existing behavior)
                    if (last && last.querySelector('.thinking-details')) {
                        const pre = last.querySelector('.thinking-pre');
                        if (pre) { pre.textContent += String.fromCharCode(10, 10) + msg.text; }
                    } else {
                        const div = document.createElement('div');
                        div.className = 'msg msg-system';
                        const details = document.createElement('details');
                        details.className = 'thinking-details';
                        details.open = true;
                        const summary = document.createElement('summary');
                        summary.textContent = 'Thinking...';
                        const pre = document.createElement('pre');
                        pre.className = 'thinking-pre';
                        pre.textContent = msg.text;
                        details.appendChild(summary);
                        details.appendChild(pre);
                        div.appendChild(details);
                        container.appendChild(div);
                        scrollToBottom();
                    }
                }
                break;
            }

            case 'chatToolActivity': {
                // Show tool exploration status (e.g. "Reading src/config.py")
                const actDiv = document.createElement('div');
                actDiv.className = 'msg msg-tool-activity';
                actDiv.innerHTML = '<em>' + escapeHtml(msg.description) + '</em>';
                messagesEl.appendChild(actDiv);
                scrollToBottom();
                break;
            }

            case 'chatToolResult': {
                // Mark the last tool activity as done
                const lastAct = messagesEl.querySelector('.msg-tool-activity:last-child');
                if (lastAct) {
                    lastAct.innerHTML += ' <span class="tool-done">\u2713</span>';
                }
                scrollToBottom();
                break;
            }

            case 'chatToken': {
                // First token: hide "Thinking...", create an empty AI bubble
                if (msg.isFirst) {
                    thinkingEl.classList.remove('visible');
                    addTimestampDivider(new Date());
                    currentStreamDiv = document.createElement('div');
                    currentStreamDiv.className = 'msg msg-ai';
                    messagesEl.appendChild(currentStreamDiv);
                }
                // Append raw text (markdown applied on chatDone)
                if (currentStreamDiv) {
                    currentStreamDiv.textContent += msg.content;
                    scrollToBottom();
                }
                break;
            }

            case 'chatDone': {
                // Re-render the streamed bubble with full markdown formatting
                if (currentStreamDiv && msg.fullText) {
                    currentStreamDiv.innerHTML = formatMarkdown(msg.fullText);
                    addCodeBlockButtons(currentStreamDiv);
                    scrollToBottom();
                }
                currentStreamDiv = null;
                // tok/s footer (client-side computation — mirrors old blocking chat mode)
                if (msg.tps != null) {
                    const countStr = msg.evalCount != null ? ' · ' + msg.evalCount.toLocaleString() + ' tokens' : '';
                    const truncStr = msg.truncated ? ' · (truncated)' : '';
                    addMessage('*' + msg.tps + ' tok/s' + countStr + truncStr + '*', 'msg-system');
                }
                sending = false;
                sendBtn.disabled = false;
                if (sendTimeout) { clearTimeout(sendTimeout); sendTimeout = null; }
                break;
            }

            case 'streamingCleanup':
                // Finalize any open planning streaming bubble (e.g. on stage done or error)
                currentPlanningDiv = null;
                break;

            case 'error':
                sending = false;
                sendBtn.disabled = false;
                stopBtn.style.display = 'none';
                if (sendTimeout) { clearTimeout(sendTimeout); sendTimeout = null; }
                currentStreamDiv = null; // clean up any partial streaming bubble
                currentPlanningDiv = null; // clean up any partial planning bubble
                addMessage(escapeHtml(msg.text), 'msg-error');
                break;

            case 'setupGuide': {
                const div = document.createElement('div');
                div.className = 'msg setup-guide';
                div.innerHTML =
                    '<h3>Welcome to Lean AI!</h3>' +
                    '<div class="setup-tagline">Your AI coding assistant — let\\'s get you up and running in under 5 minutes.</div>' +

                    '<div class="setup-step">' +
                    '<div class="setup-step-title">Step 1 — Install Ollama</div>' +
                    '<div class="setup-cmd"><code>' + escapeHtml(msg.installCmd) + '</code>' +
                    '<button class="copy-btn" title="Copy" onclick="copyCmd(this)">📋</button></div>' +
                    '<div class="setup-step-note">' +
                    '<a href="#" onclick="openExternal(\\'https://ollama.com/download\\'); return false;">Download from ollama.com</a>' +
                    ' — Ollama runs AI models locally on your machine. No cloud, no API keys needed.</div>' +
                    '</div>' +

                    '<div class="setup-step">' +
                    '<div class="setup-step-title">Step 2 — Pull a model</div>' +
                    '<div class="setup-cmd"><code>' + escapeHtml(msg.pullCmd) + '</code>' +
                    '<button class="copy-btn" title="Copy" onclick="copyCmd(this)">📋</button></div>' +
                    '<div class="setup-step-note">This downloads the model. It only needs to happen once. ' +
                    '<a href="#" onclick="openExternal(\\'https://ollama.com/library\\'); return false;">Browse other models</a></div>' +
                    '</div>' +

                    '<div class="setup-step">' +
                    '<div class="setup-step-title">Step 3 — Start chatting!</div>' +
                    '<div class="setup-step-note">Once the download finishes, type a message below to start. ' +
                    '<a href="#" onclick="vscode.postMessage({type:\\'openSettings\\'})">Open Settings</a> to customize.</div>' +
                    '</div>';
                messagesEl.appendChild(div);
                scrollToBottom();
                break;
            }

            case 'cancelled':
                sending = false;
                sendBtn.disabled = false;
                stopBtn.style.display = 'none';
                if (sendTimeout) { clearTimeout(sendTimeout); sendTimeout = null; }
                currentStreamDiv = null;
                currentPlanningDiv = null;
                setStage(null);
                resetMetrics();
                thinkingEl.classList.remove('visible');
                approvalBar.classList.remove('visible');
                addMessage('Workflow stopped by user.', 'msg-system');
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
                stopBtn.style.display = 'none';
                if (sendTimeout) { clearTimeout(sendTimeout); sendTimeout = null; }
                break;

            case 'visionAvailable':
                visionAvailable = !!msg.available;
                break;

            case 'voiceAvailable':
                if (msg.stt || msg.tts || msg.wakeWord) {
                    voiceControls.style.display = '';
                }
                // STT — init toggle + mic button
                if (msg.stt) {
                    sttToggle.parentElement.style.display = '';
                    sttToggle.checked = true;
                    sttEnabled = true;
                    micBtn.style.display = '';
                } else {
                    sttToggle.parentElement.style.display = 'none';
                    micBtn.style.display = 'none';
                }
                // TTS — init toggle + voice/speed controls
                if (msg.tts) {
                    ttsToggle.parentElement.style.display = '';
                    ttsToggle.checked = true;
                    ttsEnabled = true;
                    voiceSelect.style.display = '';
                    speedSlider.style.display = '';
                    speedLabel.style.display = '';
                } else {
                    ttsToggle.parentElement.style.display = 'none';
                }
                // Wake word
                if (msg.wakeWord) {
                    wakeWordToggle.parentElement.style.display = '';
                } else {
                    wakeWordToggle.parentElement.style.display = 'none';
                }
                // Setup hint — show when settings enabled but deps missing
                if (msg.setupNeeded) {
                    voiceControls.style.display = '';
                    voiceSetupHint.style.display = '';
                } else {
                    voiceSetupHint.style.display = 'none';
                }
                break;

            case 'voiceVoices':
                // Populate voice dropdown
                voiceSelect.innerHTML = '';
                if (msg.voices) {
                    for (const v of msg.voices) {
                        const opt = document.createElement('option');
                        opt.value = v.id;
                        opt.textContent = v.id + ' (' + v.language + ')';
                        voiceSelect.appendChild(opt);
                    }
                    if (msg.current) { voiceSelect.value = msg.current; }
                }
                break;

            case 'sttResult':
                inputEl.value += (inputEl.value ? ' ' : '') + msg.text;
                inputEl.dispatchEvent(new Event('input'));
                micBtn.classList.remove('recording');
                isRecording = false;
                if (wakeWordTriggered && msg.autoSubmit && msg.text.trim()) {
                    wakeWordTriggered = false;
                    sendBtn.click();
                } else {
                    wakeWordTriggered = false;
                }
                break;

            case 'sttRecording':
                micBtn.classList.toggle('recording', !!msg.active);
                isRecording = !!msg.active;
                break;

            case 'ttsAudio':
                if (ttsEnabled && msg.audio) {
                    playTtsAudio(msg.audio, msg.isPcm, msg.sampleRate);
                }
                break;

            case 'ttsStopPlayback':
                stopTtsPlayback();
                break;

            case 'ttsClearReplay':
                ttsReplayCache = [];
                replayTtsBtn.style.display = 'none';
                break;

            case 'wakeWordDetected':
                if (sttEnabled && !isRecording) {
                    wakeWordTriggered = true;
                    vscode.postMessage({ type: 'sttStart', autoStop: true });
                    micBtn.classList.add('recording');
                    isRecording = true;
                }
                break;

            case 'sttAutoStopped':
                if (isRecording) {
                    vscode.postMessage({ type: 'sttStop' });
                }
                break;

            case 'wakeWordStopped':
                wakeWordEnabled = false;
                wakeWordToggle.checked = false;
                break;

            case 'chatReset':
                lastTimestamp = null;
                break;

            case 'chatArchived': {
                // Open the old conversation as a read-only tab
                if (msg.tabId && msg.title && msg.messagesHtml) {
                    const shortTitle = msg.title.length > 20
                        ? msg.title.slice(0, 20) + '...' : msg.title;
                    tabs.set(msg.tabId, {
                        id: msg.tabId,
                        label: shortTitle,
                        messagesHtml: msg.messagesHtml,
                        readOnly: true,
                    });
                }

                // Reset the current tab to a fresh chat
                activeTabId = 'current';
                const curTab = tabs.get('current');
                if (curTab) { curTab.messagesHtml = null; }
                messagesEl.innerHTML = '<div class="msg msg-system">Describe what you&#39;d like to build or change. I&#39;ll help you refine it into a clear task for the agent.</div>';
                setStage(null);
                resetMetrics();
                approvalBar.classList.remove('visible');
                sending = false;
                sendBtn.disabled = false;
                inputEl.disabled = false;
                inputEl.placeholder = 'Ask a question or describe a task...';
                lastTimestamp = null;
                backBtn.style.display = 'none';

                renderTabs();
                break;
            }

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
                    addCodeBlockButtons(div);
                    messagesEl.appendChild(div);
                }
                messagesEl.scrollTop = 0;
                break;
            }

            case 'openSessionTab': {
                // Build HTML from messages array
                let tabHtml = '';
                const tabTitle = msg.title || 'Session';
                tabHtml += '<div class="msg msg-system">Session: ' + escapeHtml(tabTitle) + '</div>';
                const tabMessages = msg.messages || [];
                for (const m of tabMessages) {
                    if (m.timestamp) {
                        addTimestampDivider(new Date(m.timestamp));
                    }
                    const cls = m.role === 'user' ? 'msg-user' :
                                m.role === 'assistant' ? 'msg-ai' :
                                m.role === 'tool_call' ? 'msg-system' :
                                m.role === 'tool_result' ? 'msg-system' : 'msg-system';
                    const div = document.createElement('div');
                    div.className = 'msg ' + cls;
                    div.innerHTML = m.role === 'user' ? escapeHtml(m.content) : formatMarkdown(m.content);
                    addCodeBlockButtons(div);
                    tabHtml += div.outerHTML;
                }
                openSessionTab(msg.tabId || ('session-' + msg.sessionId), tabTitle, tabHtml);
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
                scrollToBottom();
                break;
            }

            case 'diagnosticsUpdate': {
                const total = (msg.errorCount || 0) + (msg.warningCount || 0);
                problemsCount.textContent = String(total);
                problemsPill.classList.toggle('has-errors', (msg.errorCount || 0) > 0);
                break;
            }

            case 'debugStateUpdate': {
                const isActive = !!msg.active;
                debugPill.style.display = isActive ? 'inline-block' : 'none';
                if (msg.name) {
                    debugPill.title = 'Include debug state from "' + msg.name + '" as context';
                }
                if (!isActive && debugActive) {
                    debugActive = false;
                    debugPill.classList.remove('active');
                }
                break;
            }

            case 'restoreCurrentChat': {
                backBtn.style.display = 'none';
                savedMessagesHtml = null;
                messagesEl.innerHTML = '<div class="msg msg-system">Describe what you&#39;d like to build or change. I&#39;ll help you refine it into a clear task for the agent.</div>';
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
                    addCodeBlockButtons(div);
                    messagesEl.appendChild(div);
                }
                forceScrollToBottom();
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

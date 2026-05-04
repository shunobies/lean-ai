/**
 * HTML for the Lean AI Settings Panel webview.
 * Self-contained with inline CSS and JS.
 */

export function getSettingsPanelHtml(): string {
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lean AI Settings</title>
<style>
    :root {
        --gap: 12px;
        --section-gap: 24px;
        --radius: 6px;
    }
    * { box-sizing: border-box; }
    body {
        font-family: var(--vscode-font-family);
        font-size: var(--vscode-font-size);
        color: var(--vscode-foreground);
        background: var(--vscode-editor-background);
        margin: 0;
        padding: 20px 24px 100px;
        max-width: 720px;
    }
    h1 {
        font-size: 1.3em;
        font-weight: 600;
        margin: 0 0 4px;
        display: flex;
        align-items: center;
        gap: 10px;
    }
    .subtitle {
        color: var(--vscode-descriptionForeground);
        margin: 0 0 var(--section-gap);
        font-size: 0.9em;
    }
    .subtitle a {
        color: var(--vscode-textLink-foreground);
        text-decoration: none;
    }
    .subtitle a:hover { text-decoration: underline; }

    h2 {
        font-size: 1em;
        font-weight: 600;
        margin: 0 0 var(--gap);
        padding-bottom: 6px;
        border-bottom: 1px solid var(--vscode-panel-border);
        color: var(--vscode-foreground);
    }
    .section {
        margin-bottom: var(--section-gap);
    }

    /* Radio group for provider selection */
    .radio-group {
        display: flex;
        flex-direction: column;
        gap: 8px;
        margin-bottom: var(--gap);
    }
    .radio-option {
        display: flex;
        align-items: center;
        gap: 8px;
        cursor: pointer;
        padding: 8px 12px;
        border-radius: var(--radius);
        border: 1px solid var(--vscode-input-border);
        transition: background 0.1s;
        user-select: none;
    }
    .radio-option:hover { background: var(--vscode-toolbar-hoverBackground); }
    .radio-option.selected {
        background: var(--vscode-editor-selectionBackground);
        border-color: var(--vscode-focusBorder);
    }
    .radio-option input[type="radio"] { margin: 0; accent-color: var(--vscode-button-background); }
    .radio-label { font-weight: 500; }
    .radio-desc { color: var(--vscode-descriptionForeground); font-size: 0.85em; margin-left: 4px; }

    /* Provider-specific sub-sections */
    .provider-section { display: none; margin-top: var(--gap); }
    .provider-section.active { display: block; }
    .expert-section { display: none; margin-top: var(--gap); }
    .expert-section.active { display: block; }
    .request-section { display: none; margin-top: var(--gap); }
    .request-section.active { display: block; }
    .worker-section { display: none; margin-top: var(--gap); }
    .worker-section.active { display: block; }
    .capability-row {
        display: flex;
        gap: 20px;
        margin-top: var(--gap);
        padding-top: 12px;
        border-top: 1px solid var(--vscode-settings-headerBorder, var(--vscode-panel-border));
    }
    .capability-row .field-check { flex: 1; }
    .capability-warning {
        display: none;
        font-size: 0.82em;
        margin-top: 2px;
        padding: 2px 6px;
        border-radius: 3px;
    }
    .capability-warning.unknown {
        display: inline-block;
        color: var(--vscode-editorWarning-foreground, #c08a00);
        background: color-mix(in srgb, var(--vscode-editorWarning-foreground, #c08a00) 10%, transparent);
    }
    .capability-warning.likely-no {
        display: inline-block;
        color: var(--vscode-errorForeground);
        background: color-mix(in srgb, var(--vscode-errorForeground) 10%, transparent);
    }

    /* Form fields */
    .field {
        margin-bottom: var(--gap);
    }
    .field label {
        display: block;
        margin-bottom: 4px;
        font-size: 0.9em;
        color: var(--vscode-foreground);
    }
    .field label .hint {
        color: var(--vscode-descriptionForeground);
        font-weight: normal;
        font-size: 0.85em;
        margin-left: 6px;
    }
    .field input[type="text"],
    .field input[type="number"],
    .field input[type="password"],
    .field select {
        width: 100%;
        padding: 6px 8px;
        background: var(--vscode-input-background);
        color: var(--vscode-input-foreground);
        border: 1px solid var(--vscode-input-border);
        border-radius: 4px;
        font-family: inherit;
        font-size: inherit;
        outline: none;
    }
    .field input:focus, .field select:focus {
        border-color: var(--vscode-focusBorder);
    }
    .field-row {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: var(--gap);
    }
    @media (max-width: 480px) { .field-row { grid-template-columns: 1fr; } }

    /* Checkbox fields */
    .field-check {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: var(--gap);
    }
    .field-check input[type="checkbox"] {
        accent-color: var(--vscode-button-background);
        width: 14px;
        height: 14px;
    }
    .field-check label { cursor: pointer; font-size: 0.9em; }
    .field-check .hint {
        display: block;
        color: var(--vscode-descriptionForeground);
        font-size: 0.82em;
        margin-left: 22px;
        margin-top: 2px;
        margin-bottom: 4px;
    }

    /* Custom select dropdown (native <select> is unreliable in Electron webview panels) */
    .custom-select {
        position: relative;
        width: 100%;
    }
    .custom-select-trigger {
        padding: 6px 8px;
        padding-right: 24px;
        background: var(--vscode-input-background);
        color: var(--vscode-input-foreground);
        border: 1px solid var(--vscode-input-border);
        border-radius: 4px;
        font-family: inherit;
        font-size: inherit;
        cursor: pointer;
        position: relative;
        user-select: none;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .custom-select-trigger::after {
        content: '▾';
        position: absolute;
        right: 8px;
        top: 50%;
        transform: translateY(-50%);
        pointer-events: none;
        opacity: 0.7;
    }
    .custom-select.open .custom-select-trigger {
        border-color: var(--vscode-focusBorder);
        border-radius: 4px 4px 0 0;
    }
    .custom-select-options {
        display: none;
        position: absolute;
        top: 100%;
        left: 0;
        right: 0;
        background: var(--vscode-input-background);
        border: 1px solid var(--vscode-focusBorder);
        border-top: none;
        border-radius: 0 0 4px 4px;
        z-index: 200;
    }
    .custom-select.open .custom-select-options { display: block; }
    .custom-select-option {
        padding: 6px 8px;
        cursor: pointer;
        color: var(--vscode-input-foreground);
        font-size: inherit;
    }
    .custom-select-option:hover { background: var(--vscode-list-hoverBackground); }
    .custom-select-option.selected {
        background: var(--vscode-list-activeSelectionBackground);
        color: var(--vscode-list-activeSelectionForeground);
    }

    /* Model combobox — text input + chevron button + dropdown list */
    .model-combobox {
        position: relative;
        display: flex;
        align-items: stretch;
    }
    .model-combobox input[type="text"] {
        flex: 1;
        min-width: 0;
        border-radius: 4px 0 0 4px !important;
    }
    .model-combobox.open input[type="text"],
    .model-combobox input[type="text"]:focus {
        border-color: var(--vscode-focusBorder);
    }
    .model-combobox-btn {
        flex-shrink: 0;
        padding: 0 8px;
        background: var(--vscode-input-background);
        color: var(--vscode-input-foreground);
        border: 1px solid var(--vscode-input-border);
        border-left: none;
        border-radius: 0 4px 4px 0;
        cursor: pointer;
        font-size: 0.85em;
        user-select: none;
        line-height: 1;
    }
    .model-combobox-btn:hover { background: var(--vscode-toolbar-hoverBackground); }
    .model-combobox.open .model-combobox-btn {
        border-color: var(--vscode-focusBorder);
        border-radius: 0 4px 0 0;
    }
    .model-combobox-options {
        display: none;
        position: absolute;
        top: 100%;
        left: 0;
        right: 0;
        background: var(--vscode-input-background);
        border: 1px solid var(--vscode-focusBorder);
        border-top: none;
        border-radius: 0 0 4px 4px;
        z-index: 200;
        max-height: 200px;
        overflow-y: auto;
    }
    .model-combobox.open .model-combobox-options { display: block; }
    .model-combobox-option {
        padding: 6px 8px;
        cursor: pointer;
        color: var(--vscode-input-foreground);
        font-size: inherit;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .model-combobox-option:hover { background: var(--vscode-list-hoverBackground); }

    /* API Key status widget */
    .api-key-widget {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 4px;
        flex-wrap: wrap;
    }
    .key-status {
        display: flex;
        align-items: center;
        gap: 6px;
        padding: 5px 10px;
        border-radius: 4px;
        font-size: 0.88em;
        flex: 1;
    }
    .key-status.set {
        background: color-mix(in srgb, var(--vscode-testing-iconPassed) 15%, transparent);
        color: var(--vscode-testing-iconPassed);
        border: 1px solid color-mix(in srgb, var(--vscode-testing-iconPassed) 40%, transparent);
    }
    .key-status.unset {
        background: var(--vscode-input-background);
        color: var(--vscode-descriptionForeground);
        border: 1px solid var(--vscode-input-border);
    }
    .key-input-row {
        display: flex;
        gap: 8px;
        align-items: center;
        margin-bottom: 4px;
    }
    .key-input-row input {
        flex: 1;
        padding: 6px 8px;
        background: var(--vscode-input-background);
        color: var(--vscode-input-foreground);
        border: 1px solid var(--vscode-input-border);
        border-radius: 4px;
        font-family: inherit;
        font-size: inherit;
        outline: none;
    }
    .key-input-row input:focus { border-color: var(--vscode-focusBorder); }
    .key-hint {
        font-size: 0.8em;
        color: var(--vscode-descriptionForeground);
        margin-bottom: 8px;
    }

    /* Buttons */
    button {
        padding: 5px 12px;
        border: none;
        border-radius: 4px;
        cursor: pointer;
        font-family: inherit;
        font-size: 0.88em;
        white-space: nowrap;
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
    .btn-danger {
        background: color-mix(in srgb, var(--vscode-errorForeground) 15%, transparent);
        color: var(--vscode-errorForeground);
        border: 1px solid color-mix(in srgb, var(--vscode-errorForeground) 40%, transparent);
    }
    .btn-danger:hover { background: color-mix(in srgb, var(--vscode-errorForeground) 25%, transparent); }

    /* Advanced collapsible */
    details { margin-bottom: var(--section-gap); }
    summary {
        cursor: pointer;
        font-size: 1em;
        font-weight: 600;
        padding-bottom: 6px;
        border-bottom: 1px solid var(--vscode-panel-border);
        color: var(--vscode-foreground);
        list-style: none;
        display: flex;
        align-items: center;
        gap: 6px;
        margin-bottom: var(--gap);
    }
    summary::before { content: '▶'; font-size: 0.75em; transition: transform 0.15s; }
    details[open] summary::before { transform: rotate(90deg); }
    summary::-webkit-details-marker { display: none; }

    /* Fixed footer */
    .footer {
        position: fixed;
        bottom: 0;
        left: 0;
        right: 0;
        padding: 12px 24px;
        background: var(--vscode-editor-background);
        border-top: 1px solid var(--vscode-panel-border);
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
        z-index: 100;
    }
    .footer-left { display: flex; align-items: center; gap: 10px; }
    .footer a {
        color: var(--vscode-textLink-foreground);
        text-decoration: none;
        font-size: 0.88em;
    }
    .footer a:hover { text-decoration: underline; }
    .save-btn {
        padding: 7px 18px;
        font-size: 0.95em;
        background: var(--vscode-button-background);
        color: var(--vscode-button-foreground);
        border: none;
        border-radius: 4px;
        cursor: pointer;
        font-family: inherit;
    }
    .save-btn:hover { background: var(--vscode-button-hoverBackground); }
    .save-feedback {
        font-size: 0.85em;
        color: var(--vscode-testing-iconPassed);
        opacity: 0;
        transition: opacity 0.3s;
    }
    .save-feedback.visible { opacity: 1; }

    .indent { margin-left: 16px; }
</style>
</head>
<body>
<h1>&#9881; Lean AI Settings</h1>
<p class="subtitle">
    Changes are saved to <code>backend/config.yaml</code> and take effect after a backend restart. &nbsp;
    <a href="#" class="github-link">View documentation on GitHub ↗</a>
    &nbsp;·&nbsp;
    <a href="#" id="openVscodeSettingsLink">Python path &amp; backend directory ↗ VSCode Settings</a>
</p>

<!-- ── Primary LLM Provider ── -->
<div class="section">
    <h2>LLM Provider</h2>
    <div class="radio-group" id="providerGroup">
        <label class="radio-option" id="opt-ollama">
            <input type="radio" name="provider" value="ollama">
            <span>
                <span class="radio-label">Ollama</span>
                <span class="radio-desc">— local models, no API key required</span>
            </span>
        </label>
        <label class="radio-option" id="opt-openai">
            <input type="radio" name="provider" value="openai">
            <span>
                <span class="radio-label">OpenAI</span>
                <span class="radio-desc">— GPT-4o and compatible APIs (Groq, Together AI, vLLM…)</span>
            </span>
        </label>
        <label class="radio-option" id="opt-anthropic">
            <input type="radio" name="provider" value="anthropic">
            <span>
                <span class="radio-label">Anthropic</span>
                <span class="radio-desc">— Claude Sonnet, Opus, Haiku</span>
            </span>
        </label>
        <label class="radio-option" id="opt-gemini">
            <input type="radio" name="provider" value="gemini">
            <span>
                <span class="radio-label">Gemini</span>
                <span class="radio-desc">— Google Gemini API (1M+ context window)</span>
            </span>
        </label>
        <label class="radio-option" id="opt-serve">
            <input type="radio" name="provider" value="serve">
            <span>
                <span class="radio-label">Lean AI Serve</span>
                <span class="radio-desc">— vLLM inference wrapper, API key required</span>
            </span>
        </label>
    </div>

    <!-- Ollama fields -->
    <div class="provider-section indent" id="ollama-fields">
        <div class="field-row">
            <div class="field">
                <label>Ollama URL</label>
                <input type="text" id="ollamaUrl" placeholder="http://localhost:11434">
            </div>
            <div class="field">
                <label>Model <span class="hint">e.g. qwen3-coder:30b</span></label>
                <div class="model-combobox" id="ollamaModelCombobox">
                    <input type="text" id="ollamaModel" placeholder="qwen3-coder:30b">
                    <button type="button" class="model-combobox-btn" tabindex="-1">&#9660;</button>
                    <div class="model-combobox-options"></div>
                </div>
            </div>
        </div>
        <div class="field-row">
            <div class="field">
                <label>Context Window <span class="hint">tokens, e.g. 131072 or 128</span></label>
                <input type="text" id="ollamaContextWindow" placeholder="131072">
            </div>
            <div class="field">
                <label>Temperature <span class="hint">0–2, Qwen3 recommends 0.7</span></label>
                <input type="number" id="ollamaTemperature" min="0" max="2" step="0.05" placeholder="0.7">
            </div>
        </div>
        <div class="field-row">
            <div class="field">
                <label>Top P <span class="hint">0–1, Qwen3 recommends 0.8</span></label>
                <input type="number" id="ollamaTopP" min="0" max="1" step="0.05" placeholder="0.8">
            </div>
            <div class="field">
                <label>Top K <span class="hint">Qwen3 recommends 20</span></label>
                <input type="number" id="ollamaTopK" min="0" step="1" placeholder="20">
            </div>
        </div>
        <div class="field-row">
            <div class="field">
                <label>Repeat Penalty <span class="hint">1.05 recommended</span></label>
                <input type="number" id="ollamaRepeatPenalty" min="0" step="0.01" placeholder="1.05">
            </div>
            <div class="field">
                <label>Max Tokens <span class="hint">0 = auto (25% of context window)</span></label>
                <input type="number" id="ollamaMaxTokens" min="0" step="256" placeholder="0">
            </div>
        </div>
        <div class="field-row">
            <div class="field">
                <label>Min P <span class="hint">leave blank to omit — not all models support</span></label>
                <input type="number" id="ollamaMinP" min="0" max="1" step="0.01" placeholder="">
            </div>
            <div class="field">
                <label>Presence Penalty <span class="hint">leave blank to omit — not all models support</span></label>
                <input type="number" id="ollamaPresencePenalty" min="0" max="2" step="0.1" placeholder="">
            </div>
        </div>
    </div>

    <!-- OpenAI fields -->
    <div class="provider-section indent" id="openai-fields">
        <div class="field">
            <label>API Key</label>
            <div id="openai-key-widget"></div>
            <div class="key-hint">Stored securely in your OS keychain — never written to settings.json or config files</div>
        </div>
        <div class="field-row">
            <div class="field">
                <label>Model <span class="hint">e.g. gpt-4o, gpt-4o-mini</span></label>
                <input type="text" id="openaiModel" placeholder="gpt-4o">
            </div>
            <div class="field">
                <label>Base URL <span class="hint">optional, for compatible APIs</span></label>
                <input type="text" id="openaiBaseUrl" placeholder="https://api.openai.com/v1">
            </div>
        </div>
        <div class="field-row">
            <div class="field">
                <label>Temperature <span class="hint">0–2</span></label>
                <input type="number" id="openaiTemperature" min="0" max="2" step="0.05" placeholder="0.7">
            </div>
            <div class="field">
                <label>Context Window <span class="hint">tokens</span></label>
                <input type="number" id="openaiContextWindow" min="1000" step="1000" placeholder="128000">
            </div>
        </div>
    </div>

    <!-- Anthropic fields -->
    <div class="provider-section indent" id="anthropic-fields">
        <div class="field">
            <label>API Key</label>
            <div id="anthropic-key-widget"></div>
            <div class="key-hint">Stored securely in your OS keychain — never written to settings.json or config files</div>
        </div>
        <div class="field-row">
            <div class="field">
                <label>Model <span class="hint">e.g. claude-sonnet-4-6</span></label>
                <input type="text" id="anthropicModel" placeholder="claude-sonnet-4-20250514">
            </div>
            <div class="field">
                <label>Temperature <span class="hint">0–1</span></label>
                <input type="number" id="anthropicTemperature" min="0" max="1" step="0.05" placeholder="0.7">
            </div>
        </div>
        <div class="field">
            <label>Context Window <span class="hint">tokens, Claude supports up to 200k</span></label>
            <input type="number" id="anthropicContextWindow" min="1000" step="1000" placeholder="200000">
        </div>
    </div>

    <!-- Gemini fields -->
    <div class="provider-section indent" id="gemini-fields">
        <div class="field">
            <label>API Key</label>
            <div id="gemini-key-widget"></div>
            <div class="key-hint">Stored securely in your OS keychain — never written to settings.json or config files</div>
        </div>
        <div class="field-row">
            <div class="field">
                <label>Model <span class="hint">e.g. gemini-2.5-flash</span></label>
                <input type="text" id="geminiModel" placeholder="gemini-2.5-flash">
            </div>
            <div class="field">
                <label>Temperature <span class="hint">0–2</span></label>
                <input type="number" id="geminiTemperature" min="0" max="2" step="0.05" placeholder="0.7">
            </div>
        </div>
        <div class="field">
            <label>Context Window <span class="hint">tokens, Gemini supports up to 1M+</span></label>
            <input type="number" id="geminiContextWindow" min="1000" step="1000" placeholder="1048576">
        </div>
    </div>

    <!-- Lean AI Serve fields -->
    <div class="provider-section indent" id="serve-fields">
        <div class="field">
            <label>API Key</label>
            <div id="serve-key-widget"></div>
            <div class="key-hint">Stored securely in your OS keychain — never written to settings.json or config files</div>
        </div>
        <div class="field-row">
            <div class="field">
                <label>Server URL</label>
                <input type="text" id="serveUrl" placeholder="http://localhost:8420">
            </div>
            <div class="field">
                <label>Model <span class="hint">must match vLLM loaded model</span></label>
                <input type="text" id="serveModel" placeholder="">
            </div>
        </div>
        <div class="field-row">
            <div class="field">
                <label>Temperature <span class="hint">0–2</span></label>
                <input type="number" id="serveTemperature" min="0" max="2" step="0.05" placeholder="0.7">
            </div>
            <div class="field">
                <label>Context Window <span class="hint">tokens, e.g. 131072 or 128</span></label>
                <input type="text" id="serveContextWindow" placeholder="131072">
            </div>
        </div>
        <div class="field">
            <label>Max Tokens <span class="hint">0 = auto (25% of context window)</span></label>
            <input type="number" id="serveMaxTokens" min="0" step="256" placeholder="0">
        </div>
    </div>

    <!-- Per-model capability declarations (provider-agnostic) -->
    <div class="capability-row">
        <div class="field-check">
            <input type="checkbox" id="supportsImagePrimary">
            <div>
                <label for="supportsImagePrimary">Supports image input</label>
                <span class="hint">Images go to this model directly — no separate vision model needed.</span>
                <span class="capability-warning" data-for="supportsImagePrimary"></span>
            </div>
        </div>
        <div class="field-check">
            <input type="checkbox" id="supportsAudioPrimary">
            <div>
                <label for="supportsAudioPrimary">Supports audio input</label>
                <span class="hint">Mic recordings transcribe via this model — Whisper stays as fallback.</span>
                <span class="capability-warning" data-for="supportsAudioPrimary"></span>
            </div>
        </div>
        <div class="field-check">
            <input type="checkbox" id="preserveThinkingPrimary">
            <div>
                <label for="preserveThinkingPrimary">Preserve thinking across turns</label>
                <span class="hint">Qwen3.6+/vLLM feature — keeps chain-of-thought in tool loops. Ignored by providers that don't support chat_template_kwargs.</span>
                <span class="capability-warning" data-for="preserveThinkingPrimary"></span>
            </div>
        </div>
    </div>
    <div class="field-row">
        <div class="field">
            <label>Reasoning effort <span class="hint">soft cap on thinking tokens</span></label>
            <select id="reasoningEffortPrimary">
                <option value="">Off (provider default)</option>
                <option value="low">Low — ~768 tokens</option>
                <option value="medium">Medium — ~3072 tokens</option>
                <option value="high">High — ~8192 tokens</option>
                <option value="max">Max (no soft limit)</option>
            </select>
        </div>
    </div>
</div>

<!-- ── Expert Model ── -->
<div class="section">
    <h2>Expert Model <span style="font-weight:normal;font-size:0.85em;color:var(--vscode-descriptionForeground)">— optional, for reasoning-heavy planning phases</span></h2>
    <div class="field-check">
        <input type="checkbox" id="useExpertModel">
        <div>
            <label for="useExpertModel">Use a separate model for planning phases 3–5</label>
            <span class="hint">If disabled, the primary model is used for everything.</span>
        </div>
    </div>
    <div class="expert-section indent" id="expert-section">
        <div class="radio-group" id="expertProviderGroup">
            <label class="radio-option" id="xopt-ollama">
                <input type="radio" name="expertProvider" value="ollama">
                <span><span class="radio-label">Ollama</span></span>
            </label>
            <label class="radio-option" id="xopt-openai">
                <input type="radio" name="expertProvider" value="openai">
                <span><span class="radio-label">OpenAI</span></span>
            </label>
            <label class="radio-option" id="xopt-anthropic">
                <input type="radio" name="expertProvider" value="anthropic">
                <span><span class="radio-label">Anthropic</span></span>
            </label>
            <label class="radio-option" id="xopt-gemini">
                <input type="radio" name="expertProvider" value="gemini">
                <span><span class="radio-label">Gemini</span></span>
            </label>
            <label class="radio-option" id="xopt-serve">
                <input type="radio" name="expertProvider" value="serve">
                <span><span class="radio-label">Lean AI Serve</span></span>
            </label>
        </div>
        <div class="indent">
            <!-- Expert Ollama fields -->
            <div class="provider-section" id="expert-ollama-fields">
                <div class="field-row">
                    <div class="field">
                        <label>Expert Ollama Model <span class="hint">e.g. qwen3:72b</span></label>
                        <div class="model-combobox" id="ollamaModelExpertCombobox">
                            <input type="text" id="ollamaModelExpert" placeholder="qwen3:72b">
                            <button type="button" class="model-combobox-btn" tabindex="-1">&#9660;</button>
                            <div class="model-combobox-options"></div>
                        </div>
                    </div>
                    <div class="field">
                        <label>Context Window <span class="hint">leave empty to inherit</span></label>
                        <input type="text" id="ollamaExpertContextWindow" placeholder="">
                    </div>
                </div>
                <div class="field-row">
                    <div class="field">
                        <label>Temperature <span class="hint">leave empty to inherit</span></label>
                        <input type="number" id="ollamaExpertTemperature" min="0" max="2" step="0.05" placeholder="">
                    </div>
                    <div class="field">
                        <label>Top P <span class="hint">leave empty to inherit</span></label>
                        <input type="number" id="ollamaExpertTopP" min="0" max="1" step="0.05" placeholder="">
                    </div>
                </div>
                <div class="field-row">
                    <div class="field">
                        <label>Top K <span class="hint">leave empty to inherit</span></label>
                        <input type="number" id="ollamaExpertTopK" min="0" step="1" placeholder="">
                    </div>
                    <div class="field">
                        <label>Repeat Penalty <span class="hint">leave empty to inherit</span></label>
                        <input type="number" id="ollamaExpertRepeatPenalty" min="0" step="0.01" placeholder="">
                    </div>
                </div>
                <div class="field-row">
                    <div class="field">
                        <label>Max Tokens <span class="hint">0 = auto (25% of context window)</span></label>
                        <input type="number" id="ollamaExpertMaxTokens" min="0" step="256" placeholder="">
                    </div>
                    <div class="field">
                    </div>
                </div>
                <div class="field-row">
                    <div class="field">
                        <label>Min P <span class="hint">leave blank to inherit from primary</span></label>
                        <input type="number" id="ollamaExpertMinP" min="0" max="1" step="0.01" placeholder="">
                    </div>
                    <div class="field">
                        <label>Presence Penalty <span class="hint">leave blank to inherit from primary</span></label>
                        <input type="number" id="ollamaExpertPresencePenalty" min="0" max="2" step="0.1" placeholder="">
                    </div>
                </div>
                <div class="field-check">
                    <input type="checkbox" id="enableThinkingExpert" checked>
                    <div>
                        <label for="enableThinkingExpert">Enable thinking mode</label>
                        <span class="hint">Enable for reasoning models (Qwen3, Qwen3.5). Disable for models that don't support thinking.</span>
                    </div>
                </div>
            </div>
            <!-- Expert OpenAI fields -->
            <div class="provider-section" id="expert-openai-fields">
                <div class="field">
                    <label>Expert OpenAI Model <span class="hint">e.g. gpt-4o, o3</span></label>
                    <input type="text" id="openaiExpertModel" placeholder="gpt-4o">
                </div>
            </div>
            <!-- Expert Anthropic fields -->
            <div class="provider-section" id="expert-anthropic-fields">
                <div class="field">
                    <label>Expert Anthropic Model <span class="hint">e.g. claude-opus-4-6</span></label>
                    <input type="text" id="anthropicExpertModel" placeholder="claude-opus-4-6">
                </div>
            </div>
            <!-- Expert Gemini fields -->
            <div class="provider-section" id="expert-gemini-fields">
                <div class="field">
                    <label>Expert Gemini Model <span class="hint">e.g. gemini-2.5-pro</span></label>
                    <input type="text" id="geminiExpertModel" placeholder="gemini-2.5-pro">
                </div>
            </div>
            <!-- Expert Lean AI Serve fields -->
            <div class="provider-section" id="expert-serve-fields">
                <div class="field">
                    <label>Expert Serve Model <span class="hint">falls back to primary Serve model</span></label>
                    <input type="text" id="serveExpertModel" placeholder="">
                </div>
            </div>
        </div>
        <!-- Expert capability declarations (provider-agnostic) -->
        <div class="capability-row">
            <div class="field-check">
                <input type="checkbox" id="supportsImageExpert">
                <div>
                    <label for="supportsImageExpert">Supports image input</label>
                    <span class="hint">Route images to the expert model when it's the active role.</span>
                    <span class="capability-warning" data-for="supportsImageExpert"></span>
                </div>
            </div>
            <div class="field-check">
                <input type="checkbox" id="supportsAudioExpert">
                <div>
                    <label for="supportsAudioExpert">Supports audio input</label>
                    <span class="hint">Fourth priority in the audio chain.</span>
                    <span class="capability-warning" data-for="supportsAudioExpert"></span>
                </div>
            </div>
            <div class="field-check">
                <input type="checkbox" id="preserveThinkingExpert">
                <div>
                    <label for="preserveThinkingExpert">Preserve thinking across turns</label>
                    <span class="hint">Qwen3.6+/vLLM — keep chain-of-thought in tool loops.</span>
                    <span class="capability-warning" data-for="preserveThinkingExpert"></span>
                </div>
            </div>
        </div>
        <div class="field-row">
            <div class="field">
                <label>Reasoning effort <span class="hint">blank = inherit from primary</span></label>
                <select id="reasoningEffortExpert">
                    <option value="">Inherit from primary</option>
                    <option value="low">Low — ~768 tokens</option>
                    <option value="medium">Medium — ~3072 tokens</option>
                    <option value="high">High — ~8192 tokens</option>
                    <option value="max">Max (no soft limit)</option>
                </select>
            </div>
        </div>
    </div>
</div>

<!-- ── Request Model ── -->
<div class="section">
    <h2>Request Model <span style="font-weight:normal;font-size:0.85em;color:var(--vscode-descriptionForeground)">— optional, for chat and /request mode</span></h2>
    <div class="field-check">
        <input type="checkbox" id="useRequestModel">
        <div>
            <label for="useRequestModel">Use a separate model for chat and /request mode</label>
            <span class="hint">If disabled, the primary model is used. A conversational or general-purpose model works better here than a coding-tuned model.</span>
        </div>
    </div>
    <div class="request-section indent" id="request-section">
        <div class="radio-group" id="requestProviderGroup">
            <label class="radio-option" id="ropt-ollama">
                <input type="radio" name="requestProvider" value="ollama">
                <span><span class="radio-label">Ollama</span></span>
            </label>
            <label class="radio-option" id="ropt-openai">
                <input type="radio" name="requestProvider" value="openai">
                <span><span class="radio-label">OpenAI</span></span>
            </label>
            <label class="radio-option" id="ropt-anthropic">
                <input type="radio" name="requestProvider" value="anthropic">
                <span><span class="radio-label">Anthropic</span></span>
            </label>
            <label class="radio-option" id="ropt-gemini">
                <input type="radio" name="requestProvider" value="gemini">
                <span><span class="radio-label">Gemini</span></span>
            </label>
            <label class="radio-option" id="ropt-serve">
                <input type="radio" name="requestProvider" value="serve">
                <span><span class="radio-label">Lean AI Serve</span></span>
            </label>
        </div>
        <div class="indent">
            <!-- Request Ollama fields -->
            <div class="provider-section" id="request-ollama-fields">
                <div class="field-row">
                    <div class="field">
                        <label>Request Ollama Model <span class="hint">e.g. qwen3.5:27b</span></label>
                        <div class="model-combobox" id="ollamaModelRequestCombobox">
                            <input type="text" id="ollamaModelRequest" placeholder="qwen3.5:27b">
                            <button type="button" class="model-combobox-btn" tabindex="-1">&#9660;</button>
                            <div class="model-combobox-options"></div>
                        </div>
                    </div>
                    <div class="field">
                        <label>Context Window <span class="hint">leave empty to inherit</span></label>
                        <input type="text" id="ollamaRequestContextWindow" placeholder="">
                    </div>
                </div>
                <div class="field-row">
                    <div class="field">
                        <label>Temperature <span class="hint">leave empty to inherit</span></label>
                        <input type="number" id="ollamaRequestTemperature" min="0" max="2" step="0.05" placeholder="">
                    </div>
                    <div class="field">
                        <label>Top P <span class="hint">leave empty to inherit</span></label>
                        <input type="number" id="ollamaRequestTopP" min="0" max="1" step="0.05" placeholder="">
                    </div>
                </div>
                <div class="field-row">
                    <div class="field">
                        <label>Top K <span class="hint">leave empty to inherit</span></label>
                        <input type="number" id="ollamaRequestTopK" min="0" step="1" placeholder="">
                    </div>
                    <div class="field">
                        <label>Repeat Penalty <span class="hint">leave empty to inherit</span></label>
                        <input type="number" id="ollamaRequestRepeatPenalty" min="0" step="0.01" placeholder="">
                    </div>
                </div>
                <div class="field-row">
                    <div class="field">
                        <label>Max Tokens <span class="hint">0 = auto (25% of context window)</span></label>
                        <input type="number" id="ollamaRequestMaxTokens" min="0" step="256" placeholder="">
                    </div>
                    <div class="field">
                    </div>
                </div>
                <div class="field-row">
                    <div class="field">
                        <label>Min P <span class="hint">leave blank to inherit from primary</span></label>
                        <input type="number" id="ollamaRequestMinP" min="0" max="1" step="0.01" placeholder="">
                    </div>
                    <div class="field">
                        <label>Presence Penalty <span class="hint">leave blank to inherit from primary</span></label>
                        <input type="number" id="ollamaRequestPresencePenalty" min="0" max="2" step="0.1" placeholder="">
                    </div>
                </div>
                <div class="field-check">
                    <input type="checkbox" id="enableThinkingRequest" checked>
                    <div>
                        <label for="enableThinkingRequest">Enable thinking mode</label>
                        <span class="hint">Enable for reasoning models (Qwen3, Qwen3.5). Disable for models that don't support thinking.</span>
                    </div>
                </div>
            </div>
            <!-- Request OpenAI fields -->
            <div class="provider-section" id="request-openai-fields">
                <div class="field">
                    <label>Request OpenAI Model <span class="hint">e.g. gpt-4o</span></label>
                    <input type="text" id="openaiRequestModel" placeholder="gpt-4o">
                </div>
            </div>
            <!-- Request Anthropic fields -->
            <div class="provider-section" id="request-anthropic-fields">
                <div class="field">
                    <label>Request Anthropic Model <span class="hint">e.g. claude-sonnet-4-20250514</span></label>
                    <input type="text" id="anthropicRequestModel" placeholder="claude-sonnet-4-20250514">
                </div>
            </div>
            <!-- Request Gemini fields -->
            <div class="provider-section" id="request-gemini-fields">
                <div class="field">
                    <label>Request Gemini Model <span class="hint">e.g. gemini-2.5-pro</span></label>
                    <input type="text" id="geminiRequestModel" placeholder="gemini-2.5-pro">
                </div>
            </div>
            <!-- Request Lean AI Serve fields -->
            <div class="provider-section" id="request-serve-fields">
                <div class="field">
                    <label>Request Serve Model <span class="hint">falls back to primary Serve model</span></label>
                    <input type="text" id="serveRequestModel" placeholder="">
                </div>
            </div>
        </div>
        <!-- Request capability declarations (provider-agnostic) -->
        <div class="capability-row">
            <div class="field-check">
                <input type="checkbox" id="supportsImageRequest">
                <div>
                    <label for="supportsImageRequest">Supports image input</label>
                    <span class="hint">Chat-mode images go directly to this model.</span>
                    <span class="capability-warning" data-for="supportsImageRequest"></span>
                </div>
            </div>
            <div class="field-check">
                <input type="checkbox" id="supportsAudioRequest">
                <div>
                    <label for="supportsAudioRequest">Supports audio input</label>
                    <span class="hint">Second priority in the audio chain (after primary).</span>
                    <span class="capability-warning" data-for="supportsAudioRequest"></span>
                </div>
            </div>
            <div class="field-check">
                <input type="checkbox" id="preserveThinkingRequest">
                <div>
                    <label for="preserveThinkingRequest">Preserve thinking across turns</label>
                    <span class="hint">Qwen3.6+/vLLM — keep chain-of-thought in tool loops.</span>
                    <span class="capability-warning" data-for="preserveThinkingRequest"></span>
                </div>
            </div>
        </div>
        <div class="field-row">
            <div class="field">
                <label>Reasoning effort <span class="hint">blank = inherit from primary</span></label>
                <select id="reasoningEffortRequest">
                    <option value="">Inherit from primary</option>
                    <option value="low">Low — ~768 tokens</option>
                    <option value="medium">Medium — ~3072 tokens</option>
                    <option value="high">High — ~8192 tokens</option>
                    <option value="max">Max (no soft limit)</option>
                </select>
            </div>
        </div>
    </div>
</div>

<!-- ── Worker Model ── -->
<div class="section">
    <h2>Worker Model <span style="font-weight:normal;font-size:0.85em;color:var(--vscode-descriptionForeground)">— optional, small fast model for auxiliary tasks</span></h2>
    <div class="field-check">
        <input type="checkbox" id="useWorkerModel">
        <div>
            <label for="useWorkerModel">Use a separate model for summarization and compression</label>
            <span class="hint">If disabled, the primary model handles auxiliary tasks. A small, fast model (e.g. qwen3.5:2b-q8_0) is ideal.</span>
        </div>
    </div>
    <div class="worker-section indent" id="worker-section">
        <div class="radio-group" id="workerProviderGroup">
            <label class="radio-option" id="wopt-ollama">
                <input type="radio" name="workerProvider" value="ollama">
                <span><span class="radio-label">Ollama</span></span>
            </label>
            <label class="radio-option" id="wopt-openai">
                <input type="radio" name="workerProvider" value="openai">
                <span><span class="radio-label">OpenAI</span></span>
            </label>
            <label class="radio-option" id="wopt-anthropic">
                <input type="radio" name="workerProvider" value="anthropic">
                <span><span class="radio-label">Anthropic</span></span>
            </label>
            <label class="radio-option" id="wopt-gemini">
                <input type="radio" name="workerProvider" value="gemini">
                <span><span class="radio-label">Gemini</span></span>
            </label>
            <label class="radio-option" id="wopt-serve">
                <input type="radio" name="workerProvider" value="serve">
                <span><span class="radio-label">Lean AI Serve</span></span>
            </label>
        </div>
        <div class="indent">
            <!-- Worker Ollama fields -->
            <div class="provider-section" id="worker-ollama-fields">
                <div class="field-row">
                    <div class="field">
                        <label>Worker Ollama Model <span class="hint">e.g. qwen3.5:2b-q8_0</span></label>
                        <div class="model-combobox" id="ollamaModelWorkerCombobox">
                            <input type="text" id="ollamaModelWorker" placeholder="qwen3.5:2b-q8_0">
                            <button type="button" class="model-combobox-btn" tabindex="-1">&#9660;</button>
                            <div class="model-combobox-options"></div>
                        </div>
                    </div>
                    <div class="field">
                        <label>Context Window <span class="hint">leave empty to inherit</span></label>
                        <input type="text" id="ollamaWorkerContextWindow" placeholder="">
                    </div>
                </div>
                <div class="field-row">
                    <div class="field">
                        <label>Temperature <span class="hint">leave empty to inherit</span></label>
                        <input type="number" id="ollamaWorkerTemperature" min="0" max="2" step="0.05" placeholder="">
                    </div>
                    <div class="field">
                        <label>Top P <span class="hint">leave empty to inherit</span></label>
                        <input type="number" id="ollamaWorkerTopP" min="0" max="1" step="0.05" placeholder="">
                    </div>
                </div>
                <div class="field-row">
                    <div class="field">
                        <label>Top K <span class="hint">leave empty to inherit</span></label>
                        <input type="number" id="ollamaWorkerTopK" min="0" step="1" placeholder="">
                    </div>
                    <div class="field">
                        <label>Repeat Penalty <span class="hint">leave empty to inherit</span></label>
                        <input type="number" id="ollamaWorkerRepeatPenalty" min="0" step="0.01" placeholder="">
                    </div>
                </div>
                <div class="field-row">
                    <div class="field">
                        <label>Max Tokens <span class="hint">0 = auto (25% of context window)</span></label>
                        <input type="number" id="ollamaWorkerMaxTokens" min="0" step="256" placeholder="">
                    </div>
                    <div class="field">
                    </div>
                </div>
                <div class="field-row">
                    <div class="field">
                        <label>Min P <span class="hint">leave blank to inherit from primary</span></label>
                        <input type="number" id="ollamaWorkerMinP" min="0" max="1" step="0.01" placeholder="">
                    </div>
                    <div class="field">
                        <label>Presence Penalty <span class="hint">leave blank to inherit from primary</span></label>
                        <input type="number" id="ollamaWorkerPresencePenalty" min="0" max="2" step="0.1" placeholder="">
                    </div>
                </div>
                <div class="field-check">
                    <input type="checkbox" id="enableThinkingWorker">
                    <div>
                        <label for="enableThinkingWorker">Enable thinking mode</label>
                        <span class="hint">Disabled by default — worker model prioritizes speed over deep reasoning.</span>
                    </div>
                </div>
            </div>
            <!-- Worker OpenAI fields -->
            <div class="provider-section" id="worker-openai-fields">
                <div class="field">
                    <label>Worker OpenAI Model <span class="hint">e.g. gpt-4o-mini</span></label>
                    <input type="text" id="openaiWorkerModel" placeholder="gpt-4o-mini">
                </div>
            </div>
            <!-- Worker Anthropic fields -->
            <div class="provider-section" id="worker-anthropic-fields">
                <div class="field">
                    <label>Worker Anthropic Model <span class="hint">e.g. claude-haiku-4-5-20251001</span></label>
                    <input type="text" id="anthropicWorkerModel" placeholder="claude-haiku-4-5-20251001">
                </div>
            </div>
            <!-- Worker Gemini fields -->
            <div class="provider-section" id="worker-gemini-fields">
                <div class="field">
                    <label>Worker Gemini Model <span class="hint">e.g. gemini-2.5-flash</span></label>
                    <input type="text" id="geminiWorkerModel" placeholder="gemini-2.5-flash">
                </div>
            </div>
            <!-- Worker Lean AI Serve fields -->
            <div class="provider-section" id="worker-serve-fields">
                <div class="field">
                    <label>Worker Serve Model <span class="hint">falls back to primary Serve model</span></label>
                    <input type="text" id="serveWorkerModel" placeholder="">
                </div>
            </div>
        </div>
        <!-- Worker capability declarations (provider-agnostic) -->
        <div class="capability-row">
            <div class="field-check">
                <input type="checkbox" id="supportsImageWorker">
                <div>
                    <label for="supportsImageWorker">Supports image input</label>
                    <span class="hint">Worker rarely sees user images; supplied for symmetry.</span>
                    <span class="capability-warning" data-for="supportsImageWorker"></span>
                </div>
            </div>
            <div class="field-check">
                <input type="checkbox" id="supportsAudioWorker">
                <div>
                    <label for="supportsAudioWorker">Supports audio input</label>
                    <span class="hint">Third priority in the audio chain.</span>
                    <span class="capability-warning" data-for="supportsAudioWorker"></span>
                </div>
            </div>
            <div class="field-check">
                <input type="checkbox" id="preserveThinkingWorker">
                <div>
                    <label for="preserveThinkingWorker">Preserve thinking across turns</label>
                    <span class="hint">Qwen3.6+/vLLM — keep chain-of-thought in tool loops.</span>
                    <span class="capability-warning" data-for="preserveThinkingWorker"></span>
                </div>
            </div>
        </div>
        <div class="field-row">
            <div class="field">
                <label>Reasoning effort <span class="hint">blank = inherit from primary</span></label>
                <select id="reasoningEffortWorker">
                    <option value="">Inherit from primary</option>
                    <option value="low">Low — ~768 tokens</option>
                    <option value="medium">Medium — ~3072 tokens</option>
                    <option value="high">High — ~8192 tokens</option>
                    <option value="max">Max (no soft limit)</option>
                </select>
            </div>
        </div>
    </div>
</div>

<!-- ── TDD Mode ── -->
<div class="section">
    <h2>TDD Mode</h2>
    <div class="field-check">
        <input type="checkbox" id="enableTdd">
        <div>
            <label for="enableTdd">TDD Mode — expert writes tests first, primary implements</label>
            <span class="hint">Requires an expert model. Primary cannot edit tests but can dispute flawed tests.</span>
        </div>
    </div>
</div>

<!-- ── Post-Validation ── -->
<div class="section">
    <h2>Post-Validation</h2>
    <div class="field-check">
        <input type="checkbox" id="enablePostValidation">
        <div>
            <label for="enablePostValidation">Run lint/test checks after the agent makes changes</label>
            <span class="hint">Failures trigger an automatic fix loop (up to max retries).</span>
        </div>
    </div>
    <div id="postValidationFields">
        <div class="field">
            <label>Format command <span class="hint">auto-fixes formatting, e.g. <code>ruff format src/</code></span></label>
            <input type="text" id="postFormatCommand" placeholder="(auto-detected from /init)">
        </div>
        <div class="field">
            <label>Lint fix command <span class="hint">auto-fixes lint issues, e.g. <code>ruff check --fix src/</code></span></label>
            <input type="text" id="postLintFixCommand" placeholder="(auto-detected from /init)">
        </div>
        <div class="field">
            <label>Lint check command <span class="hint">read-only lint check, e.g. <code>ruff check src/</code></span></label>
            <input type="text" id="postLintCommand" placeholder="(auto-detected from /init)">
        </div>
        <div class="field">
            <label>Test command <span class="hint">e.g. <code>pytest tests/ -x -q</code></span></label>
            <input type="text" id="postTestCommand" placeholder="(auto-detected from /init)">
        </div>
        <div class="field" style="max-width: 160px;">
            <label>Max fix retries <span class="hint">0 = no retries</span></label>
            <input type="number" id="postValidationMaxRetries" min="0" max="10" step="1" placeholder="2">
        </div>
        <div class="field" style="max-width: 160px;">
            <label>Fix loop turns <span class="hint">turns per fix attempt</span></label>
            <input type="number" id="postValidationFixTurns" min="5" max="200" step="1" placeholder="30">
        </div>
    </div>
</div>

<!-- ── Voice ── -->
<div class="section">
    <h2>Voice</h2>
    <div class="field-check">
        <input type="checkbox" id="enableStt">
        <div>
            <label for="enableStt">Speech-to-Text (faster-whisper)</label>
            <span class="hint">Click the mic icon in chat to dictate. Requires portaudio system library.</span>
        </div>
    </div>
    <div class="field-row">
        <div class="field">
            <label>STT model</label>
            <select id="sttModel">
                <option value="tiny">tiny</option>
                <option value="base">base</option>
                <option value="small">small</option>
                <option value="medium">medium</option>
                <option value="large-v3">large-v3</option>
                <option value="turbo" selected>turbo</option>
            </select>
        </div>
        <div class="field">
            <label>STT language <span class="hint">empty = auto-detect</span></label>
            <input type="text" id="sttLanguage" placeholder="">
        </div>
    </div>
    <div class="field-check">
        <input type="checkbox" id="enableTts">
        <div>
            <label for="enableTts">Text-to-Speech (Kokoro)</label>
            <span class="hint">Reads LLM responses aloud. Voice and speed can be changed inline in the chat area.</span>
        </div>
    </div>
    <div class="field-row">
        <div class="field">
            <label>TTS voice</label>
            <input type="text" id="ttsVoice" placeholder="af_heart">
        </div>
        <div class="field">
            <label>TTS speed <span class="hint">0.5–2.0</span></label>
            <input type="number" id="ttsSpeed" min="0.5" max="2.0" step="0.1" placeholder="1.0">
        </div>
    </div>
    <div class="field-row">
        <div class="field">
            <label>TTS CPU threads <span class="hint">0 = auto</span></label>
            <input type="number" id="ttsCpuThreads" min="0" max="32" step="1" placeholder="0">
        </div>
    </div>
    <div class="field-check">
        <input type="checkbox" id="enableWakeWord">
        <div>
            <label for="enableWakeWord">Wake word ("Hey Computer")</label>
            <span class="hint">Hands-free STT activation via openWakeWord. Requires portaudio.</span>
        </div>
    </div>
</div>

<!-- ── UI Verification ── -->
<div class="section">
    <h2>UI Verification</h2>
    <div class="field-check">
        <input type="checkbox" id="enableUiVerification">
        <div>
            <label for="enableUiVerification">Enable verify_web_ui + verify_desktop_ui tools</label>
            <span class="hint">
                Vision-backed screenshot analysis. Requires a configured vision model and
                the <code>ui-verification</code> backend extras. After enabling, run
                <strong>Lean AI: Install UI Verification (Chromium)</strong> from the
                command palette to download the workspace-local browser (~300MB).
                See <a href="https://github.com/shunobies/lean-ai/blob/main/docs/ui-verification.md">docs/ui-verification.md</a>.
            </span>
        </div>
    </div>
    <div class="field-row">
        <div class="field">
            <label>Overall timeout <span class="hint">seconds</span></label>
            <input type="number" id="uiVerificationTimeout" min="30" max="600" step="10" placeholder="180">
        </div>
        <div class="field">
            <label>Default viewport <span class="hint">WxH, e.g. 1280x800 or 375x812</span></label>
            <input type="text" id="uiVerificationViewport" placeholder="1280x800">
        </div>
        <div class="field">
            <label>Post-render wait <span class="hint">seconds</span></label>
            <input type="number" id="uiVerificationWaitSeconds" min="0" max="30" step="0.5" placeholder="3">
        </div>
    </div>
    <div class="field-row" style="gap: 8px; margin-top: 4px;">
        <button type="button" id="uiVerificationInstallBtn" class="btn-secondary">Install Chromium</button>
        <button type="button" id="uiVerificationTestBtn" class="btn-secondary">Run Test Capture</button>
    </div>
</div>

<!-- ── Advanced (collapsible) ── -->
<details>
    <summary>Advanced</summary>
    <div class="field-row">
        <div class="field">
            <label>Inline predictions model <span class="hint">leave empty to share primary model</span></label>
            <div class="model-combobox" id="inlineModelCombobox">
                <input type="text" id="inlineModel" placeholder="qwen2.5-coder:7b">
                <button type="button" class="model-combobox-btn" tabindex="-1">&#9660;</button>
                <div class="model-combobox-options"></div>
            </div>
        </div>
        <div class="field">
            <label>Inline Ollama URL <span class="hint">leave empty to share primary URL</span></label>
            <input type="text" id="inlineOllamaUrl" placeholder="">
        </div>
    </div>
    <div class="capability-row">
        <div class="field-check">
            <input type="checkbox" id="supportsImageInline">
            <div>
                <label for="supportsImageInline">Supports image input</label>
                <span class="hint">Inline/FIM rarely carries images; supplied for symmetry.</span>
                <span class="capability-warning" data-for="supportsImageInline"></span>
            </div>
        </div>
        <div class="field-check">
            <input type="checkbox" id="supportsAudioInline">
            <div>
                <label for="supportsAudioInline">Supports audio input</label>
                <span class="hint">Last in the audio priority chain.</span>
                <span class="capability-warning" data-for="supportsAudioInline"></span>
            </div>
        </div>
    </div>
    <div class="field-row">
        <div class="field">
            <label>Embedding model</label>
            <div class="model-combobox" id="embeddingModelCombobox">
                <input type="text" id="embeddingModel" placeholder="qwen3-embedding:0.6b">
                <button type="button" class="model-combobox-btn" tabindex="-1">&#9660;</button>
                <div class="model-combobox-options"></div>
            </div>
        </div>
        <div class="field">
            <label>Embedding context window <span class="hint">tokens; 0 or empty = auto-detect via Ollama</span></label>
            <input type="text" id="embeddingContextWindow" placeholder="auto">
        </div>
    </div>
    <div class="field-row">
        <div class="field">
            <label>Vision model <span class="hint">leave empty to disable image support</span></label>
            <div class="model-combobox" id="visionModelCombobox">
                <input type="text" id="visionModel" placeholder="qwen3-vl:8b">
                <button type="button" class="model-combobox-btn" tabindex="-1">&#9660;</button>
                <div class="model-combobox-options"></div>
            </div>
        </div>
        <div class="field">
            <label>Vision Ollama URL <span class="hint">leave empty to share primary URL</span></label>
            <input type="text" id="visionOllamaUrl" placeholder="">
        </div>
    </div>
    <div class="field-row">
        <div class="field">
            <label>Search provider</label>
            <input type="hidden" id="searchProvider" value="duckduckgo">
            <div class="custom-select" id="searchProviderDropdown">
                <div class="custom-select-trigger" id="searchProviderTrigger">DuckDuckGo (default, no setup)</div>
                <div class="custom-select-options" id="searchProviderOptions">
                    <div class="custom-select-option selected" data-value="duckduckgo">DuckDuckGo (default, no setup)</div>
                    <div class="custom-select-option" data-value="searxng">SearXNG (self-hosted)</div>
                    <div class="custom-select-option" data-value="google">Google (requires Chrome)</div>
                    <div class="custom-select-option" data-value="bing">Bing (requires Chrome)</div>
                </div>
            </div>
        </div>
    </div>
    <div class="field" id="searxngUrlField" style="display:none">
        <label>SearXNG API URL</label>
        <input type="text" id="searchApiUrl" placeholder="http://localhost:8888/search">
    </div>
    <div class="field-row">
        <div class="field">
            <label>Search delay <span class="hint">seconds between searches</span></label>
            <input type="number" id="searchDelay" min="0" step="0.5" placeholder="2.0">
        </div>
        <div class="field">
            <label>Max implementation turns <span class="hint">0 = unlimited</span></label>
            <input type="number" id="implementationMaxTurns" min="0" step="1" placeholder="0">
        </div>
    </div>
    <div class="field-row">
        <div class="field">
            <label>Context refresh threshold <span class="hint">0.3–0.95, refresh at this % full</span></label>
            <input type="number" id="refreshThreshold" min="0.3" max="0.95" step="0.05" placeholder="0.7">
        </div>
        <div class="field">
            <label>Parallel LLM requests <span class="hint">match OLLAMA_NUM_PARALLEL, 1 = sequential</span></label>
            <input type="number" id="numParallel" min="1" max="16" step="1" placeholder="1">
        </div>
    </div>
    <div class="field-row">
        <div class="field">
            <label>Max thinking tokens <span class="hint">universal safety rail (Ollama); fires even on Max effort</span></label>
            <input type="number" id="maxThinkingTokens" min="1024" max="200000" step="1024" placeholder="32768">
        </div>
    </div>
    <div class="field">
        <label>Reference library chunks <span class="hint">chunks injected into refiner context (cloud providers only)</span></label>
        <input type="number" id="refinerReferenceChunks" min="1" max="50" step="1" placeholder="5">
    </div>
    <div class="field-row">
        <div class="field">
            <label>Reference chunk size (chars) <span class="hint">target chars per prose chunk (~4 chars/token); changing rebuilds index</span></label>
            <input type="number" id="referenceChunkChars" min="200" max="20000" step="100" placeholder="1800">
        </div>
        <div class="field">
            <label>Reference neighbor window <span class="hint">± chunks merged around each hit, 0 disables</span></label>
            <input type="number" id="referenceNeighborWindow" min="0" max="10" step="1" placeholder="2">
        </div>
    </div>
    <div class="field">
        <label>Reference search default limit <span class="hint">hits returned when LLM doesn't specify a limit</span></label>
        <input type="number" id="referenceSearchDefaultLimit" min="1" max="25" step="1" placeholder="5">
    </div>
    <div class="field-check">
        <input type="checkbox" id="enableEmbeddings">
        <div>
            <label for="enableEmbeddings">Enable semantic search embeddings</label>
            <span class="hint">Requires the embedding model to be pulled in Ollama. Disable for faster indexing without semantic ranking.</span>
        </div>
    </div>
    <div class="field-check">
        <input type="checkbox" id="enableRequiredCitations">
        <div>
            <label for="enableRequiredCitations">Require documentation citations</label>
            <span class="hint">Mandate online documentation verification before using external frameworks/libraries/APIs during planning and execution.</span>
        </div>
    </div>
    <div class="field-check">
        <input type="checkbox" id="enableThinking">
        <div>
            <label for="enableThinking">Enable thinking mode</label>
            <span class="hint">Show model reasoning in chat for Qwen3, Qwen3.5 and other thinking models. Disable for faster responses.</span>
        </div>
    </div>
    <div class="field-check">
        <input type="checkbox" id="debugPlanning">
        <div>
            <label for="debugPlanning">Debug planning (save phase outputs to .lean_ai/plan_debug/)</label>
            <span class="hint">Enable to diagnose how the planner breaks down tasks.</span>
        </div>
    </div>

    <hr style="border:none;border-top:1px solid var(--vscode-panel-border);margin:16px 0;">
    <h3 style="margin-top:0;margin-bottom:8px;opacity:0.9;">Integrations</h3>
    <div class="field-check">
        <input type="checkbox" id="enableIntegrations">
        <div>
            <label for="enableIntegrations">Enable Integrations</label>
            <span class="hint">Enable external service integrations (GitHub, Jira, ServiceNow, etc.) for two-way task sync</span>
        </div>
    </div>
    <div class="field-check">
        <input type="checkbox" id="integrationAutoPush">
        <div>
            <label for="integrationAutoPush">Auto-push session summaries</label>
            <span class="hint">Automatically push session data to linked external tasks on completion</span>
        </div>
    </div>

    <h3 style="margin-top:16px;margin-bottom:8px;opacity:0.9;">GitHub</h3>
    <div class="field">
        <label>GitHub Repository <span class="hint">Target repo for issue/PR comments, e.g. owner/repo</span></label>
        <input type="text" id="githubRepo" placeholder="owner/repo">
    </div>
    <div class="field">
        <label>GitHub API Token</label>
        <div id="github-key-widget"></div>
    </div>
    <div class="field-check">
        <input type="checkbox" id="githubCoauthorEnabled">
        <div>
            <label for="githubCoauthorEnabled">Add Lean AI as a Git co-author</label>
            <span class="hint">Appends a <code>Co-authored-by</code> trailer using the built-in LeanAI bot identity by default.</span>
        </div>
    </div>
    <div class="field">
        <label>Co-author Name <span class="hint">Shown in the git trailer</span></label>
        <input type="text" id="githubCoauthorName" placeholder="LeanAI">
    </div>
    <div class="field">
        <label>Co-author Email <span class="hint">Defaults to LeanAI's verified GitHub email for commit credit</span></label>
        <input type="text" id="githubCoauthorEmail" placeholder="leanai@timcomp.com">
    </div>

    <h3 style="margin-top:16px;margin-bottom:8px;opacity:0.9;">Jira Cloud</h3>
    <div class="field">
        <label>Jira URL <span class="hint">e.g. https://yourcompany.atlassian.net</span></label>
        <input type="text" id="jiraUrl" placeholder="https://yourcompany.atlassian.net">
    </div>
    <div class="field">
        <label>Jira Email <span class="hint">Account email for API authentication</span></label>
        <input type="text" id="jiraEmail" placeholder="you@company.com">
    </div>
    <div class="field">
        <label>Jira API Token</label>
        <div id="jira-key-widget"></div>
    </div>

    <h3 style="margin-top:16px;margin-bottom:8px;opacity:0.9;">ServiceNow</h3>
    <div class="field">
        <label>ServiceNow URL <span class="hint">e.g. https://yourinstance.service-now.com</span></label>
        <input type="text" id="servicenowUrl" placeholder="https://yourinstance.service-now.com">
    </div>
    <div class="field">
        <label>ServiceNow Username</label>
        <input type="text" id="servicenowUsername" placeholder="admin">
    </div>
    <div class="field">
        <label>ServiceNow Password</label>
        <div id="servicenow-key-widget"></div>
    </div>
    <div class="field">
        <label>ServiceNow Table <span class="hint">Default: incident</span></label>
        <input type="text" id="servicenowTable" placeholder="incident">
    </div>

    <h3 style="margin-top:16px;margin-bottom:8px;opacity:0.9;">MediaWiki</h3>
    <div class="field">
        <label>Wiki URL <span class="hint">e.g. https://wiki.company.com — leave empty to disable</span></label>
        <input type="text" id="wikiUrl" placeholder="https://wiki.company.com">
    </div>
    <div class="field">
        <label>Wiki API Path <span class="hint">Default: /w/api.php</span></label>
        <input type="text" id="wikiApiPath" placeholder="/w/api.php">
    </div>
    <div class="field">
        <label>Wiki Username <span class="hint">Optional — for authenticated wikis</span></label>
        <input type="text" id="wikiUsername" placeholder="bot-account">
    </div>
    <div class="field">
        <label>Wiki Password</label>
        <div id="wiki-key-widget"></div>
    </div>
</details>

<!-- ── Fixed Footer ── -->
<div class="footer">
    <div class="footer-left">
        <button class="save-btn" id="saveBtn">Save &amp; Restart Backend</button>
        <span class="save-feedback" id="saveFeedback">&#10003; Saved</span>
    </div>
    <a href="#" class="github-link">View documentation on GitHub ↗</a>
</div>

<script>
    const vscode = acquireVsCodeApi();

    // ── Per-model capability heuristics ─────────────────────────────────────
    //
    // Non-blocking UI hint rendered next to each "Supports image / audio"
    // checkbox.  Backend doesn't consult this — it attempts the flagged
    // path and falls back on CapabilityError.  This just tells the user
    // when a flag is very likely a misconfiguration (e.g. Anthropic + audio).

    function assessCapability(provider, model, kind) {
        const m = (model || '').toLowerCase();
        const p = (provider || '').toLowerCase();

        // preserve_thinking: honored by Ollama (Qwen3.6+ chat template) and
        // Lean AI Serve (vLLM forwards chat_template_kwargs). OpenAI /
        // Anthropic / Gemini ignore the flag silently.
        if (kind === 'thinking') {
            if (p === 'anthropic') {
                return { level: 'likely-no', message: 'Anthropic does not honor chat_template_kwargs — flag will be a no-op.' };
            }
            if (p === 'openai') {
                return { level: 'likely-no', message: 'Pure OpenAI does not honor chat_template_kwargs — flag will be a no-op (Serve/vLLM does).' };
            }
            if (p === 'gemini') {
                return { level: 'likely-no', message: 'Gemini has its own thinking semantics; this flag is ignored.' };
            }
            if (p === 'ollama') {
                if (/qwen.*3\\.?6|qwen.*-?3\\.6/.test(m)) {
                    return { level: 'ok', message: '' };
                }
                return { level: 'unknown', message: 'Requires Qwen3.6+ (or another model whose chat template honors preserve_thinking).' };
            }
            if (p === 'serve') {
                return { level: 'unknown', message: 'Honored by vLLM via chat_template_kwargs — verify the loaded model\\'s template supports it.' };
            }
            return { level: 'unknown', message: 'Unknown provider — verify the chat template honors preserve_thinking.' };
        }

        if (p === 'anthropic' && kind === 'audio') {
            return { level: 'likely-no', message: 'Anthropic does not accept audio input — this flag will be ignored at runtime.' };
        }
        if (p === 'ollama' && kind === 'audio') {
            return { level: 'likely-no', message: 'Common Ollama models do not accept audio input.' };
        }
        if (p === 'ollama' && kind === 'image') {
            if (/qwen.*-?vl|llava|llama.*-?vision|bakllava|moondream|granite-?vision/.test(m)) {
                return { level: 'ok', message: '' };
            }
            return { level: 'unknown', message: 'Verify this Ollama model supports images (qwen3-vl, llava, llama3.2-vision, …).' };
        }
        if (p === 'openai') {
            if (kind === 'image' && /gpt-4o|gpt-4.*vision|gpt-5/.test(m)) {
                return { level: 'ok', message: '' };
            }
            if (kind === 'audio' && /gpt-4o.*(audio|realtime)/.test(m)) {
                return { level: 'ok', message: '' };
            }
            return { level: 'unknown', message: 'Verify the model supports ' + kind + ' input.' };
        }
        if (p === 'anthropic' && kind === 'image') {
            if (/claude-[3-9]/.test(m)) { return { level: 'ok', message: '' }; }
            return { level: 'unknown', message: 'Most Claude 3+ models accept images; verify yours does.' };
        }
        if (p === 'gemini') {
            if (/(1\\.5|2\\.0|2\\.5|3\\.0)/.test(m)) { return { level: 'ok', message: '' }; }
            return { level: 'unknown', message: 'Verify this Gemini model supports ' + kind + ' input.' };
        }
        if (p === 'serve') {
            return { level: 'unknown', message: 'Capability depends on the vLLM-loaded model.' };
        }
        return { level: 'unknown', message: 'Unknown provider — verify before relying on this.' };
    }

    // Mapping from checkbox id → (provider-resolver, model-resolver, kind).
    // Each resolver is a () => string function called at refresh time so
    // we always see the current form state.
    function getPrimaryProvider() {
        const sel = document.querySelector('input[name="provider"]:checked');
        return sel ? sel.value : 'ollama';
    }
    function getPrimaryModel() {
        const prov = getPrimaryProvider();
        const map = { ollama: 'ollamaModel', openai: 'openaiModel', anthropic: 'anthropicModel', gemini: 'geminiModel', serve: 'serveModel' };
        const el = document.getElementById(map[prov] || '');
        return el ? el.value : '';
    }
    function getRoleProvider(roleRadioName, fallback) {
        const sel = document.querySelector('input[name="' + roleRadioName + '"]:checked');
        return sel ? sel.value : fallback;
    }
    function getRoleModel(role, provider) {
        const map = {
            expert: { ollama: 'ollamaModelExpert', openai: 'openaiExpertModel', anthropic: 'anthropicExpertModel', gemini: 'geminiExpertModel', serve: 'serveExpertModel' },
            request: { ollama: 'ollamaModelRequest', openai: 'openaiRequestModel', anthropic: 'anthropicRequestModel', gemini: 'geminiRequestModel', serve: 'serveRequestModel' },
            worker: { ollama: 'ollamaModelWorker', openai: 'openaiWorkerModel', anthropic: 'anthropicWorkerModel', gemini: 'geminiWorkerModel', serve: 'serveWorkerModel' },
        };
        const el = document.getElementById((map[role] || {})[provider] || '');
        return el ? el.value : '';
    }

    const CAPABILITY_CHECKBOXES = [
        { id: 'supportsImagePrimary',    kind: 'image',    role: 'primary' },
        { id: 'supportsAudioPrimary',    kind: 'audio',    role: 'primary' },
        { id: 'preserveThinkingPrimary', kind: 'thinking', role: 'primary' },
        { id: 'supportsImageExpert',     kind: 'image',    role: 'expert' },
        { id: 'supportsAudioExpert',     kind: 'audio',    role: 'expert' },
        { id: 'preserveThinkingExpert',  kind: 'thinking', role: 'expert' },
        { id: 'supportsImageRequest',    kind: 'image',    role: 'request' },
        { id: 'supportsAudioRequest',    kind: 'audio',    role: 'request' },
        { id: 'preserveThinkingRequest', kind: 'thinking', role: 'request' },
        { id: 'supportsImageWorker',     kind: 'image',    role: 'worker' },
        { id: 'supportsAudioWorker',     kind: 'audio',    role: 'worker' },
        { id: 'preserveThinkingWorker',  kind: 'thinking', role: 'worker' },
        { id: 'supportsImageInline',     kind: 'image',    role: 'inline' },
        { id: 'supportsAudioInline',     kind: 'audio',    role: 'inline' },
    ];

    function refreshCapabilityWarning(entry) {
        const cb = document.getElementById(entry.id);
        if (!cb) { return; }
        const warning = document.querySelector('.capability-warning[data-for="' + entry.id + '"]');
        if (!warning) { return; }

        // Only show a warning when the user has actually checked the flag.
        if (!cb.checked) {
            warning.className = 'capability-warning';
            warning.textContent = '';
            return;
        }

        let provider = '';
        let model = '';
        if (entry.role === 'primary') {
            provider = getPrimaryProvider();
            model = getPrimaryModel();
        } else if (entry.role === 'inline') {
            provider = 'ollama';
            const el = document.getElementById('inlineModel');
            model = el ? el.value : '';
        } else {
            const radioName = entry.role + 'Provider';
            provider = getRoleProvider(radioName, 'ollama');
            model = getRoleModel(entry.role, provider);
        }

        const result = assessCapability(provider, model, entry.kind);
        warning.className = 'capability-warning ' + result.level;
        warning.textContent = result.level === 'ok' ? '' : result.message;
    }

    function refreshAllCapabilityWarnings() {
        for (const entry of CAPABILITY_CHECKBOXES) { refreshCapabilityWarning(entry); }
    }

    // ── API key widget factory ──────────────────────────────────────────────

    function renderKeyWidget(containerId, provider) {
        const container = document.getElementById(containerId);
        container._keySet = false;
        container._editing = false;

        function renderSet() {
            container._keySet = true;
            container._editing = false;
            container.innerHTML =
                '<div class="api-key-widget">' +
                '<div class="key-status set">&#10003; API key configured (stored in OS keychain)</div>' +
                '<button class="btn-secondary" onclick="startEdit(\\''+containerId+'\\', \\''+provider+'\\')">Update</button>' +
                '<button class="btn-danger" onclick="clearKey(\\''+containerId+'\\', \\''+provider+'\\')">Clear</button>' +
                '</div>';
        }

        function renderUnset() {
            container._keySet = false;
            container._editing = false;
            container.innerHTML =
                '<div class="key-status unset">No API key configured</div>' +
                '<div class="key-input-row" style="margin-top:8px;">' +
                '<input type="password" id="key-input-'+provider+'" placeholder="Paste your API key here...">' +
                '<button class="btn-primary" onclick="saveKey(\\''+containerId+'\\', \\''+provider+'\\')">Save Key</button>' +
                '</div>';
        }

        function renderEditing() {
            container._editing = true;
            container.innerHTML =
                '<div class="api-key-widget">' +
                '<div class="key-status set">&#10003; Key already set — enter new key to replace</div>' +
                '</div>' +
                '<div class="key-input-row" style="margin-top:8px;">' +
                '<input type="password" id="key-input-'+provider+'" placeholder="Enter new API key...">' +
                '<button class="btn-primary" onclick="saveKey(\\''+containerId+'\\', \\''+provider+'\\')">Save Key</button>' +
                '<button class="btn-secondary" onclick="cancelEdit(\\''+containerId+'\\', \\''+provider+'\\')">Cancel</button>' +
                '</div>';
        }

        container._renderSet = renderSet;
        container._renderUnset = renderUnset;
        container._renderEditing = renderEditing;
        renderUnset();
    }

    window.startEdit = function(containerId, provider) {
        document.getElementById(containerId)._renderEditing();
    };

    window.cancelEdit = function(containerId, provider) {
        document.getElementById(containerId)._renderSet();
    };

    window.saveKey = function(containerId, provider) {
        const input = document.getElementById('key-input-' + provider);
        const val = input ? input.value.trim() : '';
        if (!val) return;
        vscode.postMessage({ type: 'saveApiKey', provider, value: val });
        document.getElementById(containerId)._renderSet();
    };

    window.clearKey = function(containerId, provider) {
        vscode.postMessage({ type: 'clearApiKey', provider });
        document.getElementById(containerId)._renderUnset();
    };

    renderKeyWidget('openai-key-widget', 'openai');
    renderKeyWidget('anthropic-key-widget', 'anthropic');
    renderKeyWidget('gemini-key-widget', 'gemini');
    renderKeyWidget('serve-key-widget', 'serve');
    renderKeyWidget('github-key-widget', 'github');
    renderKeyWidget('jira-key-widget', 'jira');
    renderKeyWidget('servicenow-key-widget', 'servicenow');
    renderKeyWidget('wiki-key-widget', 'wiki');

    // ── Provider radio group logic ─────────────────────────────────────────

    function setupRadioGroup(groupId, sectionPrefix, radioName) {
        const options = document.querySelectorAll('#' + groupId + ' .radio-option');
        const radios  = document.querySelectorAll('input[name="' + radioName + '"]');

        function update() {
            const selected = document.querySelector('input[name="' + radioName + '"]:checked');
            const val = selected ? selected.value : null;
            options.forEach(opt => opt.classList.remove('selected'));
            document.querySelectorAll('#' + groupId + ' ~ * .' + sectionPrefix + ', .' + sectionPrefix).forEach(s => {
                s.classList.remove('active');
            });
            if (val) {
                const opt = document.getElementById(
                    (radioName === 'expertProvider' ? 'xopt-' : 'opt-') + val
                );
                if (opt) opt.classList.add('selected');
                const sec = document.getElementById(
                    (sectionPrefix.startsWith('expert') ? 'expert-' : '') + val + '-fields'
                );
                if (sec) sec.classList.add('active');
            }
        }

        radios.forEach(r => r.addEventListener('change', update));
    }

    // Primary provider
    document.querySelectorAll('#providerGroup .radio-option').forEach(opt => {
        opt.addEventListener('click', function() {
            const radio = this.querySelector('input[type="radio"]');
            if (radio) {
                radio.checked = true;
                updateProvider();
            }
        });
    });

    function updateProvider() {
        const selected = document.querySelector('input[name="provider"]:checked');
        const val = selected ? selected.value : null;
        document.querySelectorAll('#providerGroup .radio-option').forEach(o => o.classList.remove('selected'));
        document.querySelectorAll('.provider-section').forEach(s => {
            // only top-level provider sections, not expert sub-sections
            if (!s.closest('.expert-section') && !s.closest('.request-section') && !s.closest('.worker-section')) s.classList.remove('active');
        });
        if (val) {
            const opt = document.getElementById('opt-' + val);
            if (opt) opt.classList.add('selected');
            const sec = document.getElementById(val + '-fields');
            if (sec) sec.classList.add('active');
        }
    }
    document.querySelectorAll('input[name="provider"]').forEach(r => r.addEventListener('change', updateProvider));

    // Expert provider
    document.querySelectorAll('#expertProviderGroup .radio-option').forEach(opt => {
        opt.addEventListener('click', function() {
            const radio = this.querySelector('input[type="radio"]');
            if (radio) { radio.checked = true; updateExpertProvider(); }
        });
    });

    function updateExpertProvider() {
        const selected = document.querySelector('input[name="expertProvider"]:checked');
        const val = selected ? selected.value : null;
        document.querySelectorAll('#expertProviderGroup .radio-option').forEach(o => o.classList.remove('selected'));
        document.querySelectorAll('#expert-section .provider-section').forEach(s => s.classList.remove('active'));
        if (val) {
            const opt = document.getElementById('xopt-' + val);
            if (opt) opt.classList.add('selected');
            const sec = document.getElementById('expert-' + val + '-fields');
            if (sec) sec.classList.add('active');
        }
    }
    document.querySelectorAll('input[name="expertProvider"]').forEach(r => r.addEventListener('change', updateExpertProvider));

    // Expert model checkbox
    document.getElementById('useExpertModel').addEventListener('change', function() {
        document.getElementById('expert-section').classList.toggle('active', this.checked);
    });

    // Request model provider radio buttons
    document.querySelectorAll('#requestProviderGroup .radio-option').forEach(opt => {
        opt.addEventListener('click', () => {
            const radio = opt.querySelector('input[type="radio"]');
            if (radio) { radio.checked = true; updateRequestProvider(); }
        });
    });

    function updateRequestProvider() {
        const selected = document.querySelector('input[name="requestProvider"]:checked');
        const val = selected ? selected.value : null;
        document.querySelectorAll('#requestProviderGroup .radio-option').forEach(o => o.classList.remove('selected'));
        document.querySelectorAll('#request-section .provider-section').forEach(s => s.classList.remove('active'));
        if (val) {
            const opt = document.getElementById('ropt-' + val);
            if (opt) opt.classList.add('selected');
            const sec = document.getElementById('request-' + val + '-fields');
            if (sec) sec.classList.add('active');
        }
    }
    document.querySelectorAll('input[name="requestProvider"]').forEach(r => r.addEventListener('change', updateRequestProvider));

    // Request model checkbox
    document.getElementById('useRequestModel').addEventListener('change', function() {
        document.getElementById('request-section').classList.toggle('active', this.checked);
    });

    // Worker model provider radio buttons
    document.querySelectorAll('#workerProviderGroup .radio-option').forEach(opt => {
        opt.addEventListener('click', () => {
            const radio = opt.querySelector('input[type="radio"]');
            if (radio) { radio.checked = true; updateWorkerProvider(); }
        });
    });

    function updateWorkerProvider() {
        const selected = document.querySelector('input[name="workerProvider"]:checked');
        const val = selected ? selected.value : null;
        document.querySelectorAll('#workerProviderGroup .radio-option').forEach(o => o.classList.remove('selected'));
        document.querySelectorAll('#worker-section .provider-section').forEach(s => s.classList.remove('active'));
        if (val) {
            const opt = document.getElementById('wopt-' + val);
            if (opt) opt.classList.add('selected');
            const sec = document.getElementById('worker-' + val + '-fields');
            if (sec) sec.classList.add('active');
        }
    }
    document.querySelectorAll('input[name="workerProvider"]').forEach(r => r.addEventListener('change', updateWorkerProvider));

    // Worker model checkbox
    document.getElementById('useWorkerModel').addEventListener('change', function() {
        document.getElementById('worker-section').classList.toggle('active', this.checked);
    });

    // ── GitHub link — route through extension to open external browser ─────

    document.querySelectorAll('.github-link').forEach(a => {
        a.addEventListener('click', e => {
            e.preventDefault();
            vscode.postMessage({ type: 'openExternal', url: 'https://github.com/shunobies/lean-ai/' });
        });
    });

    // ── VSCode Settings link (Python path, backend dir) ───────────────────

    document.getElementById('openVscodeSettingsLink').addEventListener('click', e => {
        e.preventDefault();
        vscode.postMessage({ type: 'openVscodeSettings', query: '@id:lean-ai.pythonPath' });
    });

    // ── Search provider custom dropdown ───────────────────────────────────

    const searchProviderOptions = [
        { value: 'duckduckgo', label: 'DuckDuckGo (default, no setup)' },
        { value: 'searxng',    label: 'SearXNG (self-hosted)' },
        { value: 'google',     label: 'Google (requires Chrome)' },
        { value: 'bing',       label: 'Bing (requires Chrome)' },
    ];

    function setSearchProvider(value) {
        const opt = searchProviderOptions.find(o => o.value === value) || searchProviderOptions[0];
        document.getElementById('searchProvider').value = opt.value;
        document.getElementById('searchProviderTrigger').textContent = opt.label;
        document.querySelectorAll('#searchProviderOptions .custom-select-option').forEach(el => {
            el.classList.toggle('selected', el.dataset.value === opt.value);
        });
        document.getElementById('searxngUrlField').style.display =
            opt.value === 'searxng' ? '' : 'none';
    }

    document.getElementById('searchProviderTrigger').addEventListener('click', (e) => {
        e.stopPropagation();
        document.getElementById('searchProviderDropdown').classList.toggle('open');
    });

    document.querySelectorAll('#searchProviderOptions .custom-select-option').forEach(el => {
        el.addEventListener('click', () => {
            setSearchProvider(el.dataset.value);
            document.getElementById('searchProviderDropdown').classList.remove('open');
        });
    });

    document.addEventListener('click', (e) => {
        document.getElementById('searchProviderDropdown').classList.remove('open');
        if (!e.target.closest('.model-combobox')) {
            document.querySelectorAll('.model-combobox').forEach(b => b.classList.remove('open'));
        }
    });

    // ── Ollama model comboboxes ────────────────────────────────────────────

    const MODEL_COMBOBOXES = [
        { comboboxId: 'ollamaModelCombobox',       inputId: 'ollamaModel' },
        { comboboxId: 'ollamaModelExpertCombobox', inputId: 'ollamaModelExpert' },
        { comboboxId: 'ollamaModelRequestCombobox', inputId: 'ollamaModelRequest' },
        { comboboxId: 'ollamaModelWorkerCombobox',  inputId: 'ollamaModelWorker' },
        { comboboxId: 'inlineModelCombobox',       inputId: 'inlineModel' },
        { comboboxId: 'embeddingModelCombobox',    inputId: 'embeddingModel' },
        { comboboxId: 'visionModelCombobox',      inputId: 'visionModel' },
    ];

    function updateOllamaModelDropdown(models) {
        MODEL_COMBOBOXES.forEach(function(item) {
            var box = document.getElementById(item.comboboxId);
            if (!box) return;
            var optEl = box.querySelector('.model-combobox-options');
            if (!optEl) return;
            if (!models || models.length === 0) {
                optEl.innerHTML = '<div class="model-combobox-option" style="color:var(--vscode-descriptionForeground);font-style:italic;cursor:default">No models found — is Ollama running?</div>';
                return;
            }
            optEl.innerHTML = models.map(function(name) {
                return '<div class="model-combobox-option" data-value="' + name + '">' + name + '</div>';
            }).join('');
            var inputId = item.inputId;
            var comboboxId = item.comboboxId;
            optEl.querySelectorAll('.model-combobox-option[data-value]').forEach(function(el) {
                el.addEventListener('click', function() {
                    var input = document.getElementById(inputId);
                    if (input) { input.value = el.dataset.value; }
                    document.getElementById(comboboxId).classList.remove('open');
                });
            });
        });
    }

    function requestOllamaModels() {
        var url = (document.getElementById('ollamaUrl') ? document.getElementById('ollamaUrl').value.trim() : '') || 'http://localhost:11434';
        MODEL_COMBOBOXES.forEach(function(item) {
            var box = document.getElementById(item.comboboxId);
            if (!box) return;
            var optEl = box.querySelector('.model-combobox-options');
            if (optEl) { optEl.innerHTML = '<div class="model-combobox-option" style="color:var(--vscode-descriptionForeground);font-style:italic;cursor:default">Loading\u2026</div>'; }
        });
        vscode.postMessage({ type: 'requestOllamaModels', ollamaUrl: url });
    }

    // Toggle dropdown open/close on chevron button click
    document.querySelectorAll('.model-combobox-btn').forEach(function(btn) {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            var box = btn.closest('.model-combobox');
            var isOpen = box.classList.contains('open');
            document.querySelectorAll('.model-combobox').forEach(function(b) { b.classList.remove('open'); });
            document.getElementById('searchProviderDropdown').classList.remove('open');
            if (!isOpen) { box.classList.add('open'); }
        });
    });

    // Re-fetch model list when Ollama URL changes
    document.getElementById('ollamaUrl').addEventListener('change', requestOllamaModels);

    // ── Save ──────────────────────────────────────────────────────────────

    document.getElementById('saveBtn').addEventListener('click', () => {
        const val = (id) => {
            const el = document.getElementById(id);
            if (!el) return '';
            if (el.type === 'checkbox') return el.checked;
            return el.value.trim();
        };

        const provider = document.querySelector('input[name="provider"]:checked')?.value || 'ollama';
        const expertChecked = document.getElementById('useExpertModel').checked;
        const expertProvider = document.querySelector('input[name="expertProvider"]:checked')?.value || '';
        const requestChecked = document.getElementById('useRequestModel').checked;
        const requestProvider = (document.querySelector('input[name="requestProvider"]:checked') || {}).value || '';
        const workerChecked = document.getElementById('useWorkerModel').checked;
        const workerProvider = (document.querySelector('input[name="workerProvider"]:checked') || {}).value || '';

        const values = {
            // Provider
            llmProvider: provider,

            // Ollama
            ollamaUrl:             val('ollamaUrl'),
            ollamaModel:           val('ollamaModel'),
            ollamaContextWindow:   val('ollamaContextWindow'),
            ollamaTemperature:     val('ollamaTemperature'),
            ollamaTopP:            val('ollamaTopP'),
            ollamaTopK:            val('ollamaTopK'),
            ollamaRepeatPenalty:   val('ollamaRepeatPenalty'),
            ollamaMinP:            val('ollamaMinP'),
            ollamaPresencePenalty: val('ollamaPresencePenalty'),
            ollamaMaxTokens:       val('ollamaMaxTokens'),

            // OpenAI
            openaiModel:           val('openaiModel'),
            openaiBaseUrl:         val('openaiBaseUrl'),
            openaiTemperature:     val('openaiTemperature'),
            openaiContextWindow:   val('openaiContextWindow'),

            // Anthropic
            anthropicModel:        val('anthropicModel'),
            anthropicTemperature:  val('anthropicTemperature'),
            anthropicContextWindow:val('anthropicContextWindow'),

            // Gemini
            geminiModel:           val('geminiModel'),
            geminiTemperature:     val('geminiTemperature'),
            geminiContextWindow:   val('geminiContextWindow'),

            // Lean AI Serve
            serveUrl:              val('serveUrl'),
            serveModel:            val('serveModel'),
            serveTemperature:      val('serveTemperature'),
            serveContextWindow:    val('serveContextWindow'),
            serveMaxTokens:        val('serveMaxTokens'),

            // Primary preserve-thinking (provider-agnostic)
            preserveThinkingPrimary: val('preserveThinkingPrimary'),
            reasoningEffortPrimary:  val('reasoningEffortPrimary'),

            // Expert model
            expertLlmProvider:     expertChecked ? expertProvider : '',
            ollamaModelExpert:     expertChecked && expertProvider === 'ollama' ? val('ollamaModelExpert') : '',
            ollamaExpertContextWindow: expertChecked && expertProvider === 'ollama' ? val('ollamaExpertContextWindow') : '',
            ollamaExpertTemperature:   expertChecked && expertProvider === 'ollama' ? val('ollamaExpertTemperature') : '',
            ollamaExpertTopP:          expertChecked && expertProvider === 'ollama' ? val('ollamaExpertTopP') : '',
            ollamaExpertTopK:          expertChecked && expertProvider === 'ollama' ? val('ollamaExpertTopK') : '',
            ollamaExpertRepeatPenalty: expertChecked && expertProvider === 'ollama' ? val('ollamaExpertRepeatPenalty') : '',
            ollamaExpertMinP:          expertChecked && expertProvider === 'ollama' ? val('ollamaExpertMinP') : '',
            ollamaExpertPresencePenalty: expertChecked && expertProvider === 'ollama' ? val('ollamaExpertPresencePenalty') : '',
            ollamaExpertMaxTokens:     expertChecked && expertProvider === 'ollama' ? val('ollamaExpertMaxTokens') : '',
            enableThinkingExpert:      expertChecked && expertProvider === 'ollama' ? val('enableThinkingExpert') : '',
            preserveThinkingExpert:    expertChecked ? val('preserveThinkingExpert') : '',
            reasoningEffortExpert:     expertChecked ? val('reasoningEffortExpert') : '',
            openaiExpertModel:     expertChecked && expertProvider === 'openai' ? val('openaiExpertModel') : '',
            anthropicExpertModel:  expertChecked && expertProvider === 'anthropic' ? val('anthropicExpertModel') : '',
            geminiExpertModel:     expertChecked && expertProvider === 'gemini' ? val('geminiExpertModel') : '',
            serveExpertModel:      expertChecked && expertProvider === 'serve' ? val('serveExpertModel') : '',

            // Request model
            requestLlmProvider:       requestChecked ? requestProvider : '',
            ollamaModelRequest:       requestChecked && requestProvider === 'ollama' ? val('ollamaModelRequest') : '',
            ollamaRequestContextWindow: requestChecked && requestProvider === 'ollama' ? val('ollamaRequestContextWindow') : '',
            ollamaRequestTemperature:   requestChecked && requestProvider === 'ollama' ? val('ollamaRequestTemperature') : '',
            ollamaRequestTopP:          requestChecked && requestProvider === 'ollama' ? val('ollamaRequestTopP') : '',
            ollamaRequestTopK:          requestChecked && requestProvider === 'ollama' ? val('ollamaRequestTopK') : '',
            ollamaRequestRepeatPenalty: requestChecked && requestProvider === 'ollama' ? val('ollamaRequestRepeatPenalty') : '',
            ollamaRequestMinP:          requestChecked && requestProvider === 'ollama' ? val('ollamaRequestMinP') : '',
            ollamaRequestPresencePenalty: requestChecked && requestProvider === 'ollama' ? val('ollamaRequestPresencePenalty') : '',
            ollamaRequestMaxTokens:     requestChecked && requestProvider === 'ollama' ? val('ollamaRequestMaxTokens') : '',
            enableThinkingRequest:      requestChecked && requestProvider === 'ollama' ? val('enableThinkingRequest') : '',
            preserveThinkingRequest:    requestChecked ? val('preserveThinkingRequest') : '',
            reasoningEffortRequest:     requestChecked ? val('reasoningEffortRequest') : '',
            openaiRequestModel:       requestChecked && requestProvider === 'openai' ? val('openaiRequestModel') : '',
            anthropicRequestModel:    requestChecked && requestProvider === 'anthropic' ? val('anthropicRequestModel') : '',
            geminiRequestModel:       requestChecked && requestProvider === 'gemini' ? val('geminiRequestModel') : '',
            serveRequestModel:        requestChecked && requestProvider === 'serve' ? val('serveRequestModel') : '',

            // Worker model
            workerLlmProvider:        workerChecked ? workerProvider : '',
            ollamaModelWorker:        workerChecked && workerProvider === 'ollama' ? val('ollamaModelWorker') : '',
            ollamaWorkerContextWindow:workerChecked && workerProvider === 'ollama' ? val('ollamaWorkerContextWindow') : '',
            ollamaWorkerTemperature:  workerChecked && workerProvider === 'ollama' ? val('ollamaWorkerTemperature') : '',
            ollamaWorkerTopP:         workerChecked && workerProvider === 'ollama' ? val('ollamaWorkerTopP') : '',
            ollamaWorkerTopK:         workerChecked && workerProvider === 'ollama' ? val('ollamaWorkerTopK') : '',
            ollamaWorkerRepeatPenalty:workerChecked && workerProvider === 'ollama' ? val('ollamaWorkerRepeatPenalty') : '',
            ollamaWorkerMinP:         workerChecked && workerProvider === 'ollama' ? val('ollamaWorkerMinP') : '',
            ollamaWorkerPresencePenalty: workerChecked && workerProvider === 'ollama' ? val('ollamaWorkerPresencePenalty') : '',
            ollamaWorkerMaxTokens:    workerChecked && workerProvider === 'ollama' ? val('ollamaWorkerMaxTokens') : '',
            enableThinkingWorker:     workerChecked && workerProvider === 'ollama' ? val('enableThinkingWorker') : '',
            preserveThinkingWorker:   workerChecked ? val('preserveThinkingWorker') : '',
            reasoningEffortWorker:    workerChecked ? val('reasoningEffortWorker') : '',
            openaiWorkerModel:        workerChecked && workerProvider === 'openai' ? val('openaiWorkerModel') : '',
            anthropicWorkerModel:     workerChecked && workerProvider === 'anthropic' ? val('anthropicWorkerModel') : '',
            geminiWorkerModel:        workerChecked && workerProvider === 'gemini' ? val('geminiWorkerModel') : '',
            serveWorkerModel:         workerChecked && workerProvider === 'serve' ? val('serveWorkerModel') : '',

            // Integrations
            enableIntegrations:    val('enableIntegrations'),
            integrationAutoPush:   val('integrationAutoPush'),
            githubRepo:            val('githubRepo'),
            githubCoauthorEnabled: val('githubCoauthorEnabled'),
            githubCoauthorName:    val('githubCoauthorName'),
            githubCoauthorEmail:   val('githubCoauthorEmail'),
            jiraUrl:               val('jiraUrl'),
            jiraEmail:             val('jiraEmail'),
            servicenowUrl:         val('servicenowUrl'),
            servicenowUsername:    val('servicenowUsername'),
            servicenowTable:       val('servicenowTable'),
            wikiUrl:               val('wikiUrl'),
            wikiApiPath:           val('wikiApiPath'),
            wikiUsername:          val('wikiUsername'),

            // TDD mode
            enableTdd:             val('enableTdd'),

            // Post-validation
            enablePostValidation:  val('enablePostValidation'),
            postFormatCommand:     val('postFormatCommand'),
            postLintFixCommand:    val('postLintFixCommand'),
            postLintCommand:       val('postLintCommand'),
            postTestCommand:       val('postTestCommand'),
            postValidationMaxRetries: val('postValidationMaxRetries'),
            postValidationFixTurns:   val('postValidationFixTurns'),

            // Voice
            enableStt:             val('enableStt'),
            sttModel:              val('sttModel'),
            sttLanguage:           val('sttLanguage'),
            enableTts:             val('enableTts'),
            ttsVoice:              val('ttsVoice'),
            ttsSpeed:              val('ttsSpeed'),
            ttsCpuThreads:         val('ttsCpuThreads'),
            enableWakeWord:        val('enableWakeWord'),

            // Per-model capability flags
            supportsImagePrimary:  val('supportsImagePrimary'),
            supportsImageExpert:   val('supportsImageExpert'),
            supportsImageRequest:  val('supportsImageRequest'),
            supportsImageWorker:   val('supportsImageWorker'),
            supportsImageInline:   val('supportsImageInline'),
            supportsAudioPrimary:  val('supportsAudioPrimary'),
            supportsAudioExpert:   val('supportsAudioExpert'),
            supportsAudioRequest:  val('supportsAudioRequest'),
            supportsAudioWorker:   val('supportsAudioWorker'),
            supportsAudioInline:   val('supportsAudioInline'),

            // UI Verification
            enableUiVerification:      val('enableUiVerification'),
            uiVerificationTimeout:     val('uiVerificationTimeout'),
            uiVerificationViewport:    val('uiVerificationViewport'),
            uiVerificationWaitSeconds: val('uiVerificationWaitSeconds'),

            // Advanced
            inlineModel:           val('inlineModel'),
            inlineOllamaUrl:       val('inlineOllamaUrl'),
            embeddingModel:        val('embeddingModel'),
            enableEmbeddings:      val('enableEmbeddings'),
            embeddingContextWindow: val('embeddingContextWindow'),
            visionModel:           val('visionModel'),
            visionOllamaUrl:       val('visionOllamaUrl'),
            searchProvider:        val('searchProvider'),
            searchApiUrl:          val('searchApiUrl'),
            searchDelay:           val('searchDelay'),
            implementationMaxTurns:val('implementationMaxTurns'),
            refreshThreshold:      val('refreshThreshold'),
            maxThinkingTokens:     val('maxThinkingTokens'),
            numParallel:           val('numParallel'),
            enableRequiredCitations: val('enableRequiredCitations'),
            refinerReferenceChunks:      val('refinerReferenceChunks'),
            referenceChunkChars:         val('referenceChunkChars'),
            referenceNeighborWindow:     val('referenceNeighborWindow'),
            referenceSearchDefaultLimit: val('referenceSearchDefaultLimit'),
            debugPlanning:         val('debugPlanning'),
            enableThinking:        val('enableThinking'),
        };

        vscode.postMessage({ type: 'saveSettings', values });

        const fb = document.getElementById('saveFeedback');
        fb.classList.add('visible');
        setTimeout(() => fb.classList.remove('visible'), 2500);
    });

    // ── UI Verification action buttons ────────────────────────────────────
    document.getElementById('uiVerificationInstallBtn').addEventListener('click', () => {
        vscode.postMessage({ type: 'runCommand', command: 'lean-ai.installUiVerification' });
    });
    document.getElementById('uiVerificationTestBtn').addEventListener('click', () => {
        vscode.postMessage({ type: 'runCommand', command: 'lean-ai.testUiVerification' });
    });

    // ── Capability warning wiring ──────────────────────────────────────────
    // Refresh the warning chip next to each "Supports image/audio" checkbox
    // whenever its state changes, the role's provider flips, or the model
    // name is edited.  Runs once on load too (from setFormValues).
    for (const entry of CAPABILITY_CHECKBOXES) {
        const cb = document.getElementById(entry.id);
        if (cb) { cb.addEventListener('change', () => refreshCapabilityWarning(entry)); }
    }
    const _capabilityWatchInputs = [
        'ollamaModel', 'openaiModel', 'anthropicModel', 'geminiModel', 'serveModel',
        'ollamaModelExpert', 'openaiExpertModel', 'anthropicExpertModel', 'geminiExpertModel', 'serveExpertModel',
        'ollamaModelRequest', 'openaiRequestModel', 'anthropicRequestModel', 'geminiRequestModel', 'serveRequestModel',
        'ollamaModelWorker', 'openaiWorkerModel', 'anthropicWorkerModel', 'geminiWorkerModel', 'serveWorkerModel',
        'inlineModel',
    ];
    for (const id of _capabilityWatchInputs) {
        const el = document.getElementById(id);
        if (el) { el.addEventListener('input', refreshAllCapabilityWarnings); }
    }
    const _capabilityWatchRadios = ['provider', 'expertProvider', 'requestProvider', 'workerProvider'];
    for (const name of _capabilityWatchRadios) {
        document.querySelectorAll('input[name="' + name + '"]').forEach(r => {
            r.addEventListener('change', refreshAllCapabilityWarnings);
        });
    }

    // ── Load initial settings from extension host ─────────────────────────

    window.addEventListener('message', event => {
        const msg = event.data;
        if (msg.type === 'ollamaModelsLoaded') {
            updateOllamaModelDropdown(msg.models || []);
            return;
        }
        if (msg.type !== 'loadSettings') return;
        const v = msg.values;

        // Set field values
        const setVal = (id, val) => {
            const el = document.getElementById(id);
            if (!el) return;
            if (el.type === 'checkbox') { el.checked = !!val; }
            else if (val !== undefined && val !== null) { el.value = val; }
        };

        // Primary provider radio
        const providerRadio = document.querySelector('input[name="provider"][value="' + (v.llmProvider || 'ollama') + '"]');
        if (providerRadio) { providerRadio.checked = true; }
        updateProvider();

        // Ollama fields
        setVal('ollamaUrl',             v.ollamaUrl);
        setVal('ollamaModel',           v.ollamaModel);
        setVal('ollamaContextWindow',   v.ollamaContextWindow);
        setVal('ollamaTemperature',     v.ollamaTemperature);
        setVal('ollamaTopP',            v.ollamaTopP);
        setVal('ollamaTopK',            v.ollamaTopK);
        setVal('ollamaRepeatPenalty',   v.ollamaRepeatPenalty);
        setVal('ollamaMinP',            v.ollamaMinP);
        setVal('ollamaPresencePenalty', v.ollamaPresencePenalty);
        setVal('ollamaMaxTokens',       v.ollamaMaxTokens);
        setVal('preserveThinkingPrimary', v.preserveThinkingPrimary);
        setVal('reasoningEffortPrimary',  v.reasoningEffortPrimary);

        // OpenAI fields
        setVal('openaiModel',           v.openaiModel);
        setVal('openaiBaseUrl',         v.openaiBaseUrl);
        setVal('openaiTemperature',     v.openaiTemperature);
        setVal('openaiContextWindow',   v.openaiContextWindow);
        if (v.openaiKeySet) {
            document.getElementById('openai-key-widget')._renderSet();
        }

        // Anthropic fields
        setVal('anthropicModel',        v.anthropicModel);
        setVal('anthropicTemperature',  v.anthropicTemperature);
        setVal('anthropicContextWindow',v.anthropicContextWindow);
        if (v.anthropicKeySet) {
            document.getElementById('anthropic-key-widget')._renderSet();
        }

        // Gemini fields
        setVal('geminiModel',           v.geminiModel);
        setVal('geminiTemperature',     v.geminiTemperature);
        setVal('geminiContextWindow',   v.geminiContextWindow);
        if (v.geminiKeySet) {
            document.getElementById('gemini-key-widget')._renderSet();
        }

        // Lean AI Serve fields
        setVal('serveUrl',              v.serveUrl);
        setVal('serveModel',            v.serveModel);
        setVal('serveTemperature',      v.serveTemperature);
        setVal('serveContextWindow',    v.serveContextWindow);
        setVal('serveMaxTokens',        v.serveMaxTokens);
        if (v.serveKeySet) {
            document.getElementById('serve-key-widget')._renderSet();
        }

        // Expert model
        const hasExpert = !!(v.expertLlmProvider || v.ollamaModelExpert);
        document.getElementById('useExpertModel').checked = hasExpert;
        document.getElementById('expert-section').classList.toggle('active', hasExpert);

        const expertProv = v.expertLlmProvider || (v.ollamaModelExpert ? 'ollama' : '');
        if (expertProv) {
            const xr = document.querySelector('input[name="expertProvider"][value="' + expertProv + '"]');
            if (xr) { xr.checked = true; }
            updateExpertProvider();
        }
        setVal('ollamaModelExpert',        v.ollamaModelExpert);
        setVal('ollamaExpertContextWindow',v.ollamaExpertContextWindow);
        setVal('ollamaExpertTemperature',  v.ollamaExpertTemperature);
        setVal('ollamaExpertTopP',         v.ollamaExpertTopP);
        setVal('ollamaExpertTopK',         v.ollamaExpertTopK);
        setVal('ollamaExpertRepeatPenalty',v.ollamaExpertRepeatPenalty);
        setVal('ollamaExpertMinP',         v.ollamaExpertMinP);
        setVal('ollamaExpertPresencePenalty', v.ollamaExpertPresencePenalty);
        setVal('ollamaExpertMaxTokens',    v.ollamaExpertMaxTokens);
        setVal('enableThinkingExpert',     v.enableThinkingExpert);
        setVal('preserveThinkingExpert',   v.preserveThinkingExpert);
        setVal('reasoningEffortExpert',    v.reasoningEffortExpert);
        setVal('openaiExpertModel',        v.openaiExpertModel);
        setVal('anthropicExpertModel',     v.anthropicExpertModel);
        setVal('geminiExpertModel',        v.geminiExpertModel);
        setVal('serveExpertModel',         v.serveExpertModel);

        // Request model
        const hasRequest = !!(v.requestLlmProvider || v.ollamaModelRequest);
        document.getElementById('useRequestModel').checked = hasRequest;
        document.getElementById('request-section').classList.toggle('active', hasRequest);

        const requestProv = v.requestLlmProvider || (v.ollamaModelRequest ? 'ollama' : '');
        if (requestProv) {
            const rr = document.querySelector('input[name="requestProvider"][value="' + requestProv + '"]');
            if (rr) { rr.checked = true; }
            updateRequestProvider();
        }
        setVal('ollamaModelRequest',      v.ollamaModelRequest);
        setVal('ollamaRequestContextWindow', v.ollamaRequestContextWindow);
        setVal('ollamaRequestTemperature',v.ollamaRequestTemperature);
        setVal('ollamaRequestTopP',       v.ollamaRequestTopP);
        setVal('ollamaRequestTopK',       v.ollamaRequestTopK);
        setVal('ollamaRequestRepeatPenalty',v.ollamaRequestRepeatPenalty);
        setVal('ollamaRequestMinP',       v.ollamaRequestMinP);
        setVal('ollamaRequestPresencePenalty', v.ollamaRequestPresencePenalty);
        setVal('ollamaRequestMaxTokens',  v.ollamaRequestMaxTokens);
        setVal('enableThinkingRequest',   v.enableThinkingRequest);
        setVal('preserveThinkingRequest', v.preserveThinkingRequest);
        setVal('reasoningEffortRequest',  v.reasoningEffortRequest);
        setVal('openaiRequestModel',      v.openaiRequestModel);
        setVal('anthropicRequestModel',   v.anthropicRequestModel);
        setVal('geminiRequestModel',      v.geminiRequestModel);
        setVal('serveRequestModel',       v.serveRequestModel);

        // Worker model
        const hasWorker = !!(v.workerLlmProvider || v.ollamaModelWorker);
        document.getElementById('useWorkerModel').checked = hasWorker;
        document.getElementById('worker-section').classList.toggle('active', hasWorker);

        const workerProv = v.workerLlmProvider || (v.ollamaModelWorker ? 'ollama' : '');
        if (workerProv) {
            const wr = document.querySelector('input[name="workerProvider"][value="' + workerProv + '"]');
            if (wr) { wr.checked = true; }
            updateWorkerProvider();
        }
        setVal('ollamaModelWorker',        v.ollamaModelWorker);
        setVal('ollamaWorkerContextWindow',v.ollamaWorkerContextWindow);
        setVal('ollamaWorkerTemperature',  v.ollamaWorkerTemperature);
        setVal('ollamaWorkerTopP',         v.ollamaWorkerTopP);
        setVal('ollamaWorkerTopK',         v.ollamaWorkerTopK);
        setVal('ollamaWorkerRepeatPenalty',v.ollamaWorkerRepeatPenalty);
        setVal('ollamaWorkerMinP',         v.ollamaWorkerMinP);
        setVal('ollamaWorkerPresencePenalty', v.ollamaWorkerPresencePenalty);
        setVal('ollamaWorkerMaxTokens',    v.ollamaWorkerMaxTokens);
        setVal('enableThinkingWorker',     v.enableThinkingWorker);
        setVal('preserveThinkingWorker',   v.preserveThinkingWorker);
        setVal('reasoningEffortWorker',    v.reasoningEffortWorker);
        setVal('openaiWorkerModel',        v.openaiWorkerModel);
        setVal('anthropicWorkerModel',     v.anthropicWorkerModel);
        setVal('geminiWorkerModel',        v.geminiWorkerModel);
        setVal('serveWorkerModel',         v.serveWorkerModel);

        // Integrations
        setVal('enableIntegrations',       v.enableIntegrations);
        setVal('integrationAutoPush',      v.integrationAutoPush);
        setVal('githubRepo',               v.githubRepo);
        setVal('githubCoauthorEnabled',    v.githubCoauthorEnabled);
        setVal('githubCoauthorName',       v.githubCoauthorName);
        setVal('githubCoauthorEmail',      v.githubCoauthorEmail);
        setVal('jiraUrl',                  v.jiraUrl);
        setVal('jiraEmail',                v.jiraEmail);
        setVal('servicenowUrl',            v.servicenowUrl);
        setVal('servicenowUsername',        v.servicenowUsername);
        setVal('servicenowTable',          v.servicenowTable);

        // GitHub key widget
        if (v.githubKeySet) {
            document.getElementById('github-key-widget')._renderSet();
        }
        // Jira key widget
        if (v.jiraKeySet) {
            document.getElementById('jira-key-widget')._renderSet();
        }
        // ServiceNow key widget
        if (v.servicenowKeySet) {
            document.getElementById('servicenow-key-widget')._renderSet();
        }

        // MediaWiki
        setVal('wikiUrl',                  v.wikiUrl);
        setVal('wikiApiPath',              v.wikiApiPath);
        setVal('wikiUsername',             v.wikiUsername);
        // Wiki key widget
        if (v.wikiKeySet) {
            document.getElementById('wiki-key-widget')._renderSet();
        }

        // TDD mode
        setVal('enableTdd',                v.enableTdd);

        // Post-validation
        setVal('enablePostValidation',     v.enablePostValidation);
        setVal('postFormatCommand',        v.postFormatCommand);
        setVal('postLintFixCommand',       v.postLintFixCommand);
        setVal('postLintCommand',          v.postLintCommand);
        setVal('postTestCommand',          v.postTestCommand);
        setVal('postValidationMaxRetries', v.postValidationMaxRetries);
        setVal('postValidationFixTurns',   v.postValidationFixTurns);

        // Voice
        setVal('enableStt',             v.enableStt);
        setVal('sttModel',              v.sttModel);
        setVal('sttLanguage',           v.sttLanguage);
        setVal('enableTts',             v.enableTts);
        setVal('ttsVoice',              v.ttsVoice);
        setVal('ttsSpeed',              v.ttsSpeed);
        setVal('ttsCpuThreads',         v.ttsCpuThreads);
        setVal('enableWakeWord',        v.enableWakeWord);

        // Per-model capability flags
        setVal('supportsImagePrimary', v.supportsImagePrimary);
        setVal('supportsImageExpert',  v.supportsImageExpert);
        setVal('supportsImageRequest', v.supportsImageRequest);
        setVal('supportsImageWorker',  v.supportsImageWorker);
        setVal('supportsImageInline',  v.supportsImageInline);
        setVal('supportsAudioPrimary', v.supportsAudioPrimary);
        setVal('supportsAudioExpert',  v.supportsAudioExpert);
        setVal('supportsAudioRequest', v.supportsAudioRequest);
        setVal('supportsAudioWorker',  v.supportsAudioWorker);
        setVal('supportsAudioInline',  v.supportsAudioInline);
        refreshAllCapabilityWarnings();

        // UI Verification
        setVal('enableUiVerification',      v.enableUiVerification);
        setVal('uiVerificationTimeout',     v.uiVerificationTimeout);
        setVal('uiVerificationViewport',    v.uiVerificationViewport);
        setVal('uiVerificationWaitSeconds', v.uiVerificationWaitSeconds);

        // Advanced
        setVal('inlineModel',           v.inlineModel);
        setVal('inlineOllamaUrl',       v.inlineOllamaUrl);
        setVal('embeddingModel',        v.embeddingModel);
        setVal('enableEmbeddings',      v.enableEmbeddings);
        setVal('embeddingContextWindow', v.embeddingContextWindow);
        setVal('visionModel',           v.visionModel);
        setVal('visionOllamaUrl',       v.visionOllamaUrl);
        setSearchProvider(v.searchProvider || 'duckduckgo');
        setVal('searchApiUrl',          v.searchApiUrl);
        setVal('searchDelay',           v.searchDelay);
        setVal('implementationMaxTurns',v.implementationMaxTurns);
        setVal('maxThinkingTokens',     v.maxThinkingTokens);
        setVal('refreshThreshold',      v.refreshThreshold);
        setVal('numParallel',           v.numParallel);
        setVal('enableRequiredCitations', v.enableRequiredCitations);
        setVal('refinerReferenceChunks',      v.refinerReferenceChunks);
        setVal('referenceChunkChars',         v.referenceChunkChars);
        setVal('referenceNeighborWindow',     v.referenceNeighborWindow);
        setVal('referenceSearchDefaultLimit', v.referenceSearchDefaultLimit);
        setVal('debugPlanning',         v.debugPlanning);
        setVal('enableThinking',        v.enableThinking);

        // Populate Ollama model dropdowns
        requestOllamaModels();
    });
</script>
</body>
</html>`;
}

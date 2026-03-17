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
    Changes are saved to <code>backend/.env</code> and take effect after a backend restart. &nbsp;
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
    </div>

    <!-- OpenAI fields -->
    <div class="provider-section indent" id="openai-fields">
        <div class="field">
            <label>API Key</label>
            <div id="openai-key-widget"></div>
            <div class="key-hint">Stored securely in your OS keychain — never written to settings.json or .env</div>
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
            <div class="key-hint">Stored securely in your OS keychain — never written to settings.json or .env</div>
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
</div>

<!-- ── Expert Model ── -->
<div class="section">
    <h2>Expert Model <span style="font-weight:normal;font-size:0.85em;color:var(--vscode-descriptionForeground)">— optional, for reasoning-heavy planning phases</span></h2>
    <div class="field-check">
        <input type="checkbox" id="useExpertModel">
        <div>
            <label for="useExpertModel">Use a separate model for planning phases 3–6</label>
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
                        <label>Expert Context Window <span class="hint">leave empty to inherit</span></label>
                        <input type="text" id="ollamaExpertContextWindow" placeholder="">
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
            <label>Expansion concurrency <span class="hint">parallel LLM calls for /init, 0 = sequential</span></label>
            <input type="number" id="expansionConcurrency" min="0" max="16" step="1" placeholder="3">
        </div>
    </div>
    <div class="field-check">
        <input type="checkbox" id="enableEmbeddings">
        <div>
            <label for="enableEmbeddings">Enable semantic search embeddings</label>
            <span class="hint">Requires the embedding model to be pulled in Ollama. Disable for faster indexing without semantic ranking.</span>
        </div>
    </div>
    <div class="field-check">
        <input type="checkbox" id="enableFrameworkGuide">
        <div>
            <label for="enableFrameworkGuide">Generate framework guide during /init</label>
            <span class="hint">Detects frameworks and creates a conventions guide. Requires web search.</span>
        </div>
    </div>
    <div class="field-check">
        <input type="checkbox" id="debugPlanning">
        <div>
            <label for="debugPlanning">Debug planning (save phase outputs to .lean_ai/plan_debug/)</label>
            <span class="hint">Enable to diagnose how the planner breaks down tasks.</span>
        </div>
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
            if (!s.closest('.expert-section')) s.classList.remove('active');
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
        { comboboxId: 'inlineModelCombobox',       inputId: 'inlineModel' },
        { comboboxId: 'embeddingModelCombobox',    inputId: 'embeddingModel' },
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

            // Expert model
            expertLlmProvider:     expertChecked ? expertProvider : '',
            ollamaModelExpert:     expertChecked && expertProvider === 'ollama' ? val('ollamaModelExpert') : '',
            ollamaExpertContextWindow: expertChecked && expertProvider === 'ollama' ? val('ollamaExpertContextWindow') : '',
            openaiExpertModel:     expertChecked && expertProvider === 'openai' ? val('openaiExpertModel') : '',
            anthropicExpertModel:  expertChecked && expertProvider === 'anthropic' ? val('anthropicExpertModel') : '',

            // Post-validation
            enablePostValidation:  val('enablePostValidation'),
            postFormatCommand:     val('postFormatCommand'),
            postLintFixCommand:    val('postLintFixCommand'),
            postLintCommand:       val('postLintCommand'),
            postTestCommand:       val('postTestCommand'),
            postValidationMaxRetries: val('postValidationMaxRetries'),
            postValidationFixTurns:   val('postValidationFixTurns'),

            // Advanced
            inlineModel:           val('inlineModel'),
            inlineOllamaUrl:       val('inlineOllamaUrl'),
            embeddingModel:        val('embeddingModel'),
            enableEmbeddings:      val('enableEmbeddings'),
            searchProvider:        val('searchProvider'),
            searchApiUrl:          val('searchApiUrl'),
            searchDelay:           val('searchDelay'),
            implementationMaxTurns:val('implementationMaxTurns'),
            refreshThreshold:      val('refreshThreshold'),
            expansionConcurrency:  val('expansionConcurrency'),
            enableFrameworkGuide:  val('enableFrameworkGuide'),
            debugPlanning:         val('debugPlanning'),
        };

        vscode.postMessage({ type: 'saveSettings', values });

        const fb = document.getElementById('saveFeedback');
        fb.classList.add('visible');
        setTimeout(() => fb.classList.remove('visible'), 2500);
    });

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
        setVal('ollamaMaxTokens',       v.ollamaMaxTokens);

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
        setVal('openaiExpertModel',        v.openaiExpertModel);
        setVal('anthropicExpertModel',     v.anthropicExpertModel);

        // Post-validation
        setVal('enablePostValidation',     v.enablePostValidation);
        setVal('postFormatCommand',        v.postFormatCommand);
        setVal('postLintFixCommand',       v.postLintFixCommand);
        setVal('postLintCommand',          v.postLintCommand);
        setVal('postTestCommand',          v.postTestCommand);
        setVal('postValidationMaxRetries', v.postValidationMaxRetries);
        setVal('postValidationFixTurns',   v.postValidationFixTurns);

        // Advanced
        setVal('inlineModel',           v.inlineModel);
        setVal('inlineOllamaUrl',       v.inlineOllamaUrl);
        setVal('embeddingModel',        v.embeddingModel);
        setVal('enableEmbeddings',      v.enableEmbeddings);
        setSearchProvider(v.searchProvider || 'duckduckgo');
        setVal('searchApiUrl',          v.searchApiUrl);
        setVal('searchDelay',           v.searchDelay);
        setVal('implementationMaxTurns',v.implementationMaxTurns);
        setVal('refreshThreshold',      v.refreshThreshold);
        setVal('expansionConcurrency',  v.expansionConcurrency);
        setVal('enableFrameworkGuide',  v.enableFrameworkGuide);
        setVal('debugPlanning',         v.debugPlanning);

        // Populate Ollama model dropdowns
        requestOllamaModels();
    });
</script>
</body>
</html>`;
}

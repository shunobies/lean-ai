/**
 * Settings panel — a singleton WebviewPanel that opens in the editor area.
 * Reads/writes VSCode settings for non-secret fields and uses SecretStorage
 * (OS keychain) for API keys. Never sends actual key values to the webview.
 */

import * as http from "http";
import * as https from "https";
import { URL } from "url";

import * as vscode from "vscode";
import { getSettingsPanelHtml } from "./settingsPanelHtml";
import {
    BACKEND_SETTING_MAP,
    SECRET_KEYS,
    clearEnvSetting,
    resolveEnvFilePath,
    writeEnvSetting,
} from "./settingsSync";

/** Fetch available model names from Ollama's /api/tags endpoint. Returns [] on failure. */
async function listOllamaModels(ollamaUrl: string): Promise<string[]> {
    return new Promise((resolve) => {
        try {
            const fullUrl = new URL(`${ollamaUrl.replace(/\/$/, "")}/api/tags`);
            const isHttps = fullUrl.protocol === "https:";
            const transport = isHttps ? https : http;

            const options: http.RequestOptions = {
                hostname: fullUrl.hostname,
                port: fullUrl.port || (isHttps ? "443" : "80"),
                path: fullUrl.pathname,
                method: "GET",
                timeout: 5000,
            };

            const req = transport.request(options, (res) => {
                let data = "";
                res.on("data", (chunk: Buffer | string) => { data += chunk.toString(); });
                res.on("end", () => {
                    if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
                        try {
                            const parsed = JSON.parse(data) as { models?: Array<{ name: string }> };
                            const names = (parsed.models ?? []).map(m => m.name).sort();
                            resolve(names);
                        } catch {
                            resolve([]);
                        }
                    } else {
                        resolve([]);
                    }
                });
            });

            req.on("timeout", () => { req.destroy(); resolve([]); });
            req.on("error", () => resolve([]));
            req.end();
        } catch {
            resolve([]);
        }
    });
}

export class SettingsPanel {
    static currentPanel: SettingsPanel | undefined;
    private static readonly viewType = "lean-ai.settingsPanel";

    private readonly _panel: vscode.WebviewPanel;
    private readonly _context: vscode.ExtensionContext;
    private _disposables: vscode.Disposable[] = [];

    static createOrShow(context: vscode.ExtensionContext): void {
        const column = vscode.window.activeTextEditor
            ? vscode.window.activeTextEditor.viewColumn
            : undefined;

        if (SettingsPanel.currentPanel) {
            SettingsPanel.currentPanel._panel.reveal(column);
            return;
        }

        const panel = vscode.window.createWebviewPanel(
            SettingsPanel.viewType,
            "Lean AI Settings",
            column || vscode.ViewColumn.One,
            {
                enableScripts: true,
                retainContextWhenHidden: true,
                localResourceRoots: [],
            },
        );

        SettingsPanel.currentPanel = new SettingsPanel(panel, context);
    }

    private constructor(
        panel: vscode.WebviewPanel,
        context: vscode.ExtensionContext,
    ) {
        this._panel = panel;
        this._context = context;

        this._panel.webview.html = getSettingsPanelHtml();

        // Load initial state once webview is ready
        this._panel.webview.onDidReceiveMessage(
            async (message: Record<string, unknown>) => {
                await this._handleMessage(message);
            },
            null,
            this._disposables,
        );

        this._panel.onDidDispose(() => this._dispose(), null, this._disposables);

        // Push initial state after a short delay to ensure webview JS is ready
        setTimeout(() => {
            void this._sendCurrentSettings();
        }, 200);
    }

    // ── Message handling ─────────────────────────────────────────────────────

    private async _handleMessage(msg: Record<string, unknown>): Promise<void> {
        switch (msg.type) {
            case "webviewReady":
                await this._sendCurrentSettings();
                break;

            case "saveSettings":
                try {
                    await this._handleSaveSettings(msg.values as Record<string, unknown>);
                } catch (err) {
                    const errMsg = err instanceof Error ? err.message : String(err);
                    void vscode.window.showErrorMessage(
                        `Lean AI: failed to save settings — ${errMsg}`,
                    );
                }
                break;

            case "saveApiKey": {
                const provider = msg.provider as string;
                const value = msg.value as string;
                if (!value) { break; }
                const secretKey =
                    provider === "openai"
                        ? SECRET_KEYS.openaiApiKey
                        : SECRET_KEYS.anthropicApiKey;
                await this._context.secrets.store(secretKey, value);
                // Refresh the key-set status in the webview (still no value sent)
                await this._sendCurrentSettings();
                break;
            }

            case "clearApiKey": {
                const provider = msg.provider as string;
                const secretKey =
                    provider === "openai"
                        ? SECRET_KEYS.openaiApiKey
                        : SECRET_KEYS.anthropicApiKey;
                await this._context.secrets.delete(secretKey);
                await this._sendCurrentSettings();
                break;
            }

            case "requestOllamaModels": {
                const ollamaUrl = (msg.ollamaUrl as string) || "http://localhost:11434";
                const models = await listOllamaModels(ollamaUrl);
                await this._panel.webview.postMessage({ type: "ollamaModelsLoaded", models });
                break;
            }

            case "openVscodeSettings": {
                const query = (msg.query as string) || "lean-ai";
                await vscode.commands.executeCommand("workbench.action.openSettings", query);
                break;
            }

            case "openExternal": {
                const url = msg.url as string;
                if (url) {
                    await vscode.env.openExternal(vscode.Uri.parse(url));
                }
                break;
            }
        }
    }

    // ── Save non-secret settings ─────────────────────────────────────────────

    private async _handleSaveSettings(
        values: Record<string, unknown>,
    ): Promise<void> {
        const config = vscode.workspace.getConfiguration();

        // Map from form field names → VSCode setting keys
        const fieldToSetting: Record<string, string> = {
            llmProvider:              "lean-ai.llmProvider",
            ollamaUrl:                "lean-ai.ollamaUrl",
            ollamaModel:              "lean-ai.ollamaModel",
            ollamaContextWindow:      "lean-ai.ollamaContextWindow",
            ollamaTemperature:        "lean-ai.ollamaTemperature",
            ollamaTopP:               "lean-ai.ollamaTopP",
            ollamaTopK:               "lean-ai.ollamaTopK",
            ollamaRepeatPenalty:      "lean-ai.ollamaRepeatPenalty",
            ollamaMaxTokens:          "lean-ai.ollamaMaxTokens",
            ollamaModelExpert:        "lean-ai.ollamaModelExpert",
            ollamaExpertContextWindow:"lean-ai.ollamaExpertContextWindow",
            ollamaExpertTemperature:  "lean-ai.ollamaExpertTemperature",
            ollamaExpertTopP:         "lean-ai.ollamaExpertTopP",
            ollamaExpertTopK:         "lean-ai.ollamaExpertTopK",
            ollamaExpertRepeatPenalty:"lean-ai.ollamaExpertRepeatPenalty",
            ollamaExpertMaxTokens:    "lean-ai.ollamaExpertMaxTokens",
            enableThinkingExpert:     "lean-ai.enableThinkingExpert",
            expertLlmProvider:        "lean-ai.expertLlmProvider",
            ollamaModelRequest:       "lean-ai.ollamaModelRequest",
            ollamaRequestContextWindow:"lean-ai.ollamaRequestContextWindow",
            ollamaRequestTemperature: "lean-ai.ollamaRequestTemperature",
            ollamaRequestTopP:        "lean-ai.ollamaRequestTopP",
            ollamaRequestTopK:        "lean-ai.ollamaRequestTopK",
            ollamaRequestRepeatPenalty:"lean-ai.ollamaRequestRepeatPenalty",
            ollamaRequestMaxTokens:   "lean-ai.ollamaRequestMaxTokens",
            enableThinkingRequest:    "lean-ai.enableThinkingRequest",
            requestLlmProvider:       "lean-ai.requestLlmProvider",
            openaiRequestModel:       "lean-ai.openaiRequestModel",
            anthropicRequestModel:    "lean-ai.anthropicRequestModel",
            openaiModel:              "lean-ai.openaiModel",
            openaiBaseUrl:            "lean-ai.openaiBaseUrl",
            openaiTemperature:        "lean-ai.openaiTemperature",
            openaiContextWindow:      "lean-ai.openaiContextWindow",
            openaiExpertModel:        "lean-ai.openaiExpertModel",
            anthropicModel:           "lean-ai.anthropicModel",
            anthropicTemperature:     "lean-ai.anthropicTemperature",
            anthropicContextWindow:   "lean-ai.anthropicContextWindow",
            anthropicExpertModel:     "lean-ai.anthropicExpertModel",
            inlineModel:              "lean-ai.inlineModel",
            inlineOllamaUrl:          "lean-ai.inlineOllamaUrl",
            embeddingModel:           "lean-ai.embeddingModel",
            enableEmbeddings:         "lean-ai.enableEmbeddings",
            visionModel:              "lean-ai.visionModel",
            visionOllamaUrl:          "lean-ai.visionOllamaUrl",
            searchProvider:           "lean-ai.searchProvider",
            searchApiUrl:             "lean-ai.searchApiUrl",
            searchDelay:              "lean-ai.searchDelay",
            enableTdd:                "lean-ai.enableTdd",
            enablePostValidation:     "lean-ai.enablePostValidation",
            postFormatCommand:        "lean-ai.postFormatCommand",
            postLintFixCommand:       "lean-ai.postLintFixCommand",
            postLintCommand:          "lean-ai.postLintCommand",
            postTestCommand:          "lean-ai.postTestCommand",
            postValidationMaxRetries: "lean-ai.postValidationMaxRetries",
            postValidationFixTurns:   "lean-ai.postValidationFixTurns",
            enableFrameworkGuide:     "lean-ai.enableFrameworkGuide",
            implementationMaxTurns:   "lean-ai.implementationMaxTurns",
            refreshThreshold:         "lean-ai.refreshThreshold",
            numParallel:              "lean-ai.numParallel",
            debugPlanning:            "lean-ai.debugPlanning",
            enableThinking:           "lean-ai.enableThinking",
        };

        const numericFields = new Set([
            "ollamaContextWindow", "ollamaMaxTokens", "ollamaExpertContextWindow",
            "ollamaExpertTemperature", "ollamaExpertTopP", "ollamaExpertTopK",
            "ollamaExpertRepeatPenalty", "ollamaExpertMaxTokens",
            "ollamaRequestContextWindow", "ollamaRequestTemperature",
            "ollamaRequestTopP", "ollamaRequestTopK",
            "ollamaRequestRepeatPenalty", "ollamaRequestMaxTokens",
            "openaiContextWindow", "anthropicContextWindow",
            "searchDelay", "postValidationMaxRetries", "postValidationFixTurns",
            "implementationMaxTurns", "refreshThreshold", "numParallel",
            "ollamaTemperature", "ollamaTopP", "ollamaTopK", "ollamaRepeatPenalty",
            "openaiTemperature", "anthropicTemperature",
        ]);

        // Numeric fields where 0 means "inherit/auto-derive" — treat 0 as unset
        // so the backend receives None and falls back to the primary model's value.
        const zeroMeansInherit = new Set([
            "ollamaExpertTemperature", "ollamaExpertTopP", "ollamaExpertTopK",
            "ollamaExpertRepeatPenalty", "ollamaExpertContextWindow", "ollamaExpertMaxTokens",
            "ollamaRequestTemperature", "ollamaRequestTopP", "ollamaRequestTopK",
            "ollamaRequestRepeatPenalty", "ollamaRequestContextWindow", "ollamaRequestMaxTokens",
            "ollamaMaxTokens", "ollamaContextWindow",
            "openaiContextWindow", "anthropicContextWindow",
        ]);

        const booleanFields = new Set([
            "enableEmbeddings", "enableTdd", "enablePostValidation",
            "enableFrameworkGuide", "debugPlanning", "enableThinking",
            "enableThinkingExpert", "enableThinkingRequest",
        ]);

        const coercedMap = new Map<string, unknown>();
        for (const [field, settingKey] of Object.entries(fieldToSetting)) {
            if (!(field in values)) { continue; }
            const raw = values[field];
            let coerced: unknown = raw;

            if (booleanFields.has(field)) {
                coerced = raw === true || raw === "true";
            } else if (numericFields.has(field)) {
                const n = parseFloat(String(raw));
                coerced = isNaN(n) ? undefined : n;
                if (coerced === 0 && zeroMeansInherit.has(field)) {
                    coerced = undefined;
                }
            } else {
                // String — omit empty strings (revert to default)
                coerced = String(raw ?? "").trim() || undefined;
            }

            coercedMap.set(field, coerced);
            if (coerced !== undefined) {
                await config.update(settingKey, coerced, vscode.ConfigurationTarget.Global);
            } else {
                // Reset to default
                await config.update(settingKey, undefined, vscode.ConfigurationTarget.Global);
            }
        }

        // Write to .env using the coerced values directly (not from config)
        // so manually-set .env values aren't overwritten with stale config.
        const backendDir = config.get<string>("lean-ai.backendDir", "");
        const envPath = resolveEnvFilePath(backendDir || undefined, this._context.globalStorageUri.fsPath);
        if (envPath) {
            for (const [field, settingKey] of Object.entries(fieldToSetting)) {
                if (!(field in values)) { continue; }
                const envVar = BACKEND_SETTING_MAP[settingKey];
                if (!envVar) { continue; }
                const val = coercedMap.get(field);
                if (val !== undefined && val !== null && String(val) !== "") {
                    writeEnvSetting(envPath, envVar, String(val));
                } else {
                    // Clear stale .env entry so backend uses its default
                    clearEnvSetting(envPath, envVar);
                }
            }
        }

        // Offer backend restart
        const action = await vscode.window.showInformationMessage(
            "Lean AI settings saved. Restart the backend to apply changes.",
            "Restart Now",
            "Later",
        );
        if (action === "Restart Now") {
            await vscode.commands.executeCommand("lean-ai.restartBackend");
        }
    }

    // ── Send current settings to webview ────────────────────────────────────

    private async _sendCurrentSettings(): Promise<void> {
        const config = vscode.workspace.getConfiguration();

        const openaiKeySet  = !!(await this._context.secrets.get(SECRET_KEYS.openaiApiKey));
        const anthropicKeySet = !!(await this._context.secrets.get(SECRET_KEYS.anthropicApiKey));

        const values = {
            // Provider
            llmProvider:               config.get("lean-ai.llmProvider", "ollama"),

            // Ollama
            ollamaUrl:                 config.get("lean-ai.ollamaUrl", ""),
            ollamaModel:               config.get("lean-ai.ollamaModel", ""),
            ollamaContextWindow:       config.get("lean-ai.ollamaContextWindow", ""),
            ollamaTemperature:         config.get("lean-ai.ollamaTemperature", ""),
            ollamaTopP:                config.get("lean-ai.ollamaTopP", ""),
            ollamaTopK:                config.get("lean-ai.ollamaTopK", ""),
            ollamaRepeatPenalty:       config.get("lean-ai.ollamaRepeatPenalty", ""),
            ollamaMaxTokens:           config.get("lean-ai.ollamaMaxTokens", ""),

            // Expert model
            ollamaModelExpert:         config.get("lean-ai.ollamaModelExpert", ""),
            ollamaExpertContextWindow: config.get("lean-ai.ollamaExpertContextWindow", ""),
            ollamaExpertTemperature:   config.get("lean-ai.ollamaExpertTemperature", ""),
            ollamaExpertTopP:          config.get("lean-ai.ollamaExpertTopP", ""),
            ollamaExpertTopK:          config.get("lean-ai.ollamaExpertTopK", ""),
            ollamaExpertRepeatPenalty: config.get("lean-ai.ollamaExpertRepeatPenalty", ""),
            ollamaExpertMaxTokens:     config.get("lean-ai.ollamaExpertMaxTokens", ""),
            enableThinkingExpert:      config.get("lean-ai.enableThinkingExpert", true),
            expertLlmProvider:         config.get("lean-ai.expertLlmProvider", ""),

            // Request model
            ollamaModelRequest:        config.get("lean-ai.ollamaModelRequest", ""),
            ollamaRequestContextWindow:config.get("lean-ai.ollamaRequestContextWindow", ""),
            ollamaRequestTemperature:  config.get("lean-ai.ollamaRequestTemperature", ""),
            ollamaRequestTopP:         config.get("lean-ai.ollamaRequestTopP", ""),
            ollamaRequestTopK:         config.get("lean-ai.ollamaRequestTopK", ""),
            ollamaRequestRepeatPenalty:config.get("lean-ai.ollamaRequestRepeatPenalty", ""),
            ollamaRequestMaxTokens:    config.get("lean-ai.ollamaRequestMaxTokens", ""),
            enableThinkingRequest:     config.get("lean-ai.enableThinkingRequest", true),
            requestLlmProvider:        config.get("lean-ai.requestLlmProvider", ""),
            openaiRequestModel:        config.get("lean-ai.openaiRequestModel", ""),
            anthropicRequestModel:     config.get("lean-ai.anthropicRequestModel", ""),

            // OpenAI (no key value — boolean only)
            openaiKeySet,
            openaiModel:               config.get("lean-ai.openaiModel", ""),
            openaiBaseUrl:             config.get("lean-ai.openaiBaseUrl", ""),
            openaiTemperature:         config.get("lean-ai.openaiTemperature", ""),
            openaiContextWindow:       config.get("lean-ai.openaiContextWindow", ""),
            openaiExpertModel:         config.get("lean-ai.openaiExpertModel", ""),

            // Anthropic (no key value — boolean only)
            anthropicKeySet,
            anthropicModel:            config.get("lean-ai.anthropicModel", ""),
            anthropicTemperature:      config.get("lean-ai.anthropicTemperature", ""),
            anthropicContextWindow:    config.get("lean-ai.anthropicContextWindow", ""),
            anthropicExpertModel:      config.get("lean-ai.anthropicExpertModel", ""),

            // Inline & embeddings
            inlineModel:               config.get("lean-ai.inlineModel", ""),
            inlineOllamaUrl:           config.get("lean-ai.inlineOllamaUrl", ""),
            embeddingModel:            config.get("lean-ai.embeddingModel", ""),
            enableEmbeddings:          config.get("lean-ai.enableEmbeddings", true),

            // Vision
            visionModel:               config.get("lean-ai.visionModel", ""),
            visionOllamaUrl:           config.get("lean-ai.visionOllamaUrl", ""),

            // Search
            searchProvider:            config.get("lean-ai.searchProvider", "duckduckgo"),
            searchApiUrl:              config.get("lean-ai.searchApiUrl", ""),
            searchDelay:               config.get("lean-ai.searchDelay", ""),

            // TDD mode
            enableTdd:                 config.get("lean-ai.enableTdd", false),

            // Post-validation
            enablePostValidation:      config.get("lean-ai.enablePostValidation", true),
            postFormatCommand:         config.get("lean-ai.postFormatCommand", ""),
            postLintFixCommand:        config.get("lean-ai.postLintFixCommand", ""),
            postLintCommand:           config.get("lean-ai.postLintCommand", ""),
            postTestCommand:           config.get("lean-ai.postTestCommand", ""),
            postValidationMaxRetries:  config.get("lean-ai.postValidationMaxRetries", ""),
            postValidationFixTurns:    config.get("lean-ai.postValidationFixTurns", ""),

            // Advanced
            enableFrameworkGuide:      config.get("lean-ai.enableFrameworkGuide", true),
            implementationMaxTurns:    config.get("lean-ai.implementationMaxTurns", ""),
            refreshThreshold:          config.get("lean-ai.refreshThreshold", ""),
            numParallel:               config.get("lean-ai.numParallel", ""),
            debugPlanning:             config.get("lean-ai.debugPlanning", false),
            enableThinking:            config.get("lean-ai.enableThinking", true),
        };

        await this._panel.webview.postMessage({ type: "loadSettings", values });
    }

    // ── Cleanup ──────────────────────────────────────────────────────────────

    private _dispose(): void {
        SettingsPanel.currentPanel = undefined;
        this._panel.dispose();
        for (const d of this._disposables) {
            d.dispose();
        }
        this._disposables = [];
    }
}

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
    clearYamlSetting,
    resolveConfigFilePath,
    writeYamlSetting,
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
                const secretKeyMap: Record<string, string> = {
                    openai: SECRET_KEYS.openaiApiKey,
                    anthropic: SECRET_KEYS.anthropicApiKey,
                    gemini: SECRET_KEYS.geminiApiKey,
                    serve: SECRET_KEYS.serveApiKey,
                    jira: SECRET_KEYS.jiraApiToken,
                    servicenow: SECRET_KEYS.servicenowPassword,
                    wiki: SECRET_KEYS.wikiPassword,
                };
                const secretKey = secretKeyMap[provider];
                if (!secretKey) { break; }
                await this._context.secrets.store(secretKey, value);
                // Refresh the key-set status in the webview (still no value sent)
                await this._sendCurrentSettings();
                break;
            }

            case "clearApiKey": {
                const provider = msg.provider as string;
                const clearKeyMap: Record<string, string> = {
                    openai: SECRET_KEYS.openaiApiKey,
                    anthropic: SECRET_KEYS.anthropicApiKey,
                    gemini: SECRET_KEYS.geminiApiKey,
                    serve: SECRET_KEYS.serveApiKey,
                    jira: SECRET_KEYS.jiraApiToken,
                    servicenow: SECRET_KEYS.servicenowPassword,
                    wiki: SECRET_KEYS.wikiPassword,
                };
                const secretKey = clearKeyMap[provider];
                if (!secretKey) { break; }
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

            case "runCommand": {
                const command = msg.command as string;
                const allowed = new Set([
                    "lean-ai.installUiVerification",
                    "lean-ai.testUiVerification",
                ]);
                if (command && allowed.has(command)) {
                    await vscode.commands.executeCommand(command);
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
            geminiRequestModel:       "lean-ai.geminiRequestModel",
            ollamaModelWorker:        "lean-ai.ollamaModelWorker",
            workerLlmProvider:        "lean-ai.workerLlmProvider",
            ollamaWorkerContextWindow:"lean-ai.ollamaWorkerContextWindow",
            ollamaWorkerTemperature:  "lean-ai.ollamaWorkerTemperature",
            ollamaWorkerTopP:         "lean-ai.ollamaWorkerTopP",
            ollamaWorkerTopK:         "lean-ai.ollamaWorkerTopK",
            ollamaWorkerRepeatPenalty:"lean-ai.ollamaWorkerRepeatPenalty",
            ollamaWorkerMaxTokens:    "lean-ai.ollamaWorkerMaxTokens",
            enableThinkingWorker:     "lean-ai.enableThinkingWorker",
            openaiWorkerModel:        "lean-ai.openaiWorkerModel",
            anthropicWorkerModel:     "lean-ai.anthropicWorkerModel",
            geminiWorkerModel:        "lean-ai.geminiWorkerModel",
            serveWorkerModel:         "lean-ai.serveWorkerModel",
            openaiModel:              "lean-ai.openaiModel",
            openaiBaseUrl:            "lean-ai.openaiBaseUrl",
            openaiTemperature:        "lean-ai.openaiTemperature",
            openaiContextWindow:      "lean-ai.openaiContextWindow",
            openaiExpertModel:        "lean-ai.openaiExpertModel",
            anthropicModel:           "lean-ai.anthropicModel",
            anthropicTemperature:     "lean-ai.anthropicTemperature",
            anthropicContextWindow:   "lean-ai.anthropicContextWindow",
            anthropicExpertModel:     "lean-ai.anthropicExpertModel",
            geminiModel:              "lean-ai.geminiModel",
            geminiTemperature:        "lean-ai.geminiTemperature",
            geminiContextWindow:      "lean-ai.geminiContextWindow",
            geminiExpertModel:        "lean-ai.geminiExpertModel",
            serveUrl:                 "lean-ai.serveUrl",
            serveModel:               "lean-ai.serveModel",
            serveTemperature:         "lean-ai.serveTemperature",
            serveContextWindow:       "lean-ai.serveContextWindow",
            serveMaxTokens:           "lean-ai.serveMaxTokens",
            serveExpertModel:         "lean-ai.serveExpertModel",
            serveRequestModel:        "lean-ai.serveRequestModel",
            inlineModel:              "lean-ai.inlineModel",
            inlineOllamaUrl:          "lean-ai.inlineOllamaUrl",
            embeddingModel:           "lean-ai.embeddingModel",
            enableEmbeddings:         "lean-ai.enableEmbeddings",
            embeddingContextWindow:   "lean-ai.embeddingContextWindow",
            visionModel:              "lean-ai.visionModel",
            visionOllamaUrl:          "lean-ai.visionOllamaUrl",
            searchProvider:           "lean-ai.searchProvider",
            searchApiUrl:             "lean-ai.searchApiUrl",
            searchDelay:              "lean-ai.searchDelay",
            enableIntegrations:       "lean-ai.enableIntegrations",
            integrationAutoPush:      "lean-ai.integrationAutoPush",
            jiraUrl:                  "lean-ai.jiraUrl",
            jiraEmail:                "lean-ai.jiraEmail",
            servicenowUrl:            "lean-ai.servicenowUrl",
            servicenowUsername:       "lean-ai.servicenowUsername",
            servicenowTable:          "lean-ai.servicenowTable",
            wikiUrl:                  "lean-ai.wikiUrl",
            wikiApiPath:              "lean-ai.wikiApiPath",
            wikiUsername:             "lean-ai.wikiUsername",
            enableTdd:                "lean-ai.enableTdd",
            enablePostValidation:     "lean-ai.enablePostValidation",
            postFormatCommand:        "lean-ai.postFormatCommand",
            postLintFixCommand:       "lean-ai.postLintFixCommand",
            postLintCommand:          "lean-ai.postLintCommand",
            postTestCommand:          "lean-ai.postTestCommand",
            postValidationMaxRetries: "lean-ai.postValidationMaxRetries",
            postValidationFixTurns:   "lean-ai.postValidationFixTurns",
            enableRequiredCitations:  "lean-ai.enableRequiredCitations",
            refinerReferenceChunks:      "lean-ai.refinerReferenceChunks",
            referenceChunkChars:         "lean-ai.referenceChunkChars",
            referenceNeighborWindow:     "lean-ai.referenceNeighborWindow",
            referenceSearchDefaultLimit: "lean-ai.referenceSearchDefaultLimit",
            implementationMaxTurns:   "lean-ai.implementationMaxTurns",
            refreshThreshold:         "lean-ai.refreshThreshold",
            numParallel:              "lean-ai.numParallel",
            debugPlanning:            "lean-ai.debugPlanning",
            enableThinking:           "lean-ai.enableThinking",
            enableStt:                "lean-ai.enableStt",
            sttModel:                 "lean-ai.sttModel",
            sttLanguage:              "lean-ai.sttLanguage",
            enableTts:                "lean-ai.enableTts",
            ttsVoice:                 "lean-ai.ttsVoice",
            ttsSpeed:                 "lean-ai.ttsSpeed",
            ttsCpuThreads:            "lean-ai.ttsCpuThreads",
            enableWakeWord:           "lean-ai.enableWakeWord",
            enableUiVerification:     "lean-ai.enableUiVerification",
            uiVerificationTimeout:    "lean-ai.uiVerificationTimeout",
            uiVerificationViewport:   "lean-ai.uiVerificationViewport",
            uiVerificationWaitSeconds:"lean-ai.uiVerificationWaitSeconds",
            supportsImagePrimary:     "lean-ai.supportsImagePrimary",
            supportsImageExpert:      "lean-ai.supportsImageExpert",
            supportsImageRequest:     "lean-ai.supportsImageRequest",
            supportsImageWorker:      "lean-ai.supportsImageWorker",
            supportsImageInline:      "lean-ai.supportsImageInline",
            supportsAudioPrimary:     "lean-ai.supportsAudioPrimary",
            supportsAudioExpert:      "lean-ai.supportsAudioExpert",
            supportsAudioRequest:     "lean-ai.supportsAudioRequest",
            supportsAudioWorker:      "lean-ai.supportsAudioWorker",
            supportsAudioInline:      "lean-ai.supportsAudioInline",
        };

        const numericFields = new Set([
            "ollamaContextWindow", "ollamaMaxTokens", "ollamaExpertContextWindow",
            "ollamaExpertTemperature", "ollamaExpertTopP", "ollamaExpertTopK",
            "ollamaExpertRepeatPenalty", "ollamaExpertMaxTokens",
            "ollamaRequestContextWindow", "ollamaRequestTemperature",
            "ollamaRequestTopP", "ollamaRequestTopK",
            "ollamaRequestRepeatPenalty", "ollamaRequestMaxTokens",
            "ollamaWorkerContextWindow", "ollamaWorkerTemperature",
            "ollamaWorkerTopP", "ollamaWorkerTopK",
            "ollamaWorkerRepeatPenalty", "ollamaWorkerMaxTokens",
            "openaiContextWindow", "anthropicContextWindow",
            "geminiContextWindow", "geminiTemperature",
            "serveContextWindow", "serveTemperature", "serveMaxTokens",
            "embeddingContextWindow",
            "searchDelay", "postValidationMaxRetries", "postValidationFixTurns",
            "implementationMaxTurns", "refreshThreshold", "numParallel", "ttsSpeed", "ttsCpuThreads",
            "ollamaTemperature", "ollamaTopP", "ollamaTopK", "ollamaRepeatPenalty",
            "openaiTemperature", "anthropicTemperature",
            "uiVerificationTimeout", "uiVerificationWaitSeconds",
        ]);

        // Numeric fields where 0 means "inherit/auto-derive" — treat 0 as unset
        // so the backend receives None and falls back to the primary model's value.
        const zeroMeansInherit = new Set([
            "ollamaExpertTemperature", "ollamaExpertTopP", "ollamaExpertTopK",
            "ollamaExpertRepeatPenalty", "ollamaExpertContextWindow", "ollamaExpertMaxTokens",
            "ollamaRequestTemperature", "ollamaRequestTopP", "ollamaRequestTopK",
            "ollamaRequestRepeatPenalty", "ollamaRequestContextWindow", "ollamaRequestMaxTokens",
            "ollamaWorkerTemperature", "ollamaWorkerTopP", "ollamaWorkerTopK",
            "ollamaWorkerRepeatPenalty", "ollamaWorkerContextWindow", "ollamaWorkerMaxTokens",
            "ollamaMaxTokens", "ollamaContextWindow",
            "openaiContextWindow", "anthropicContextWindow",
            "geminiContextWindow",
            "serveContextWindow", "serveMaxTokens",
            "embeddingContextWindow",
        ]);

        const booleanFields = new Set([
            "enableEmbeddings", "enableIntegrations", "integrationAutoPush",
            "enableTdd", "enablePostValidation",
            "enableRequiredCitations", "debugPlanning", "enableThinking",
            "enableThinkingExpert", "enableThinkingRequest", "enableThinkingWorker",
            "enableStt", "enableTts", "enableWakeWord",
            "enableUiVerification",
            "supportsImagePrimary", "supportsImageExpert", "supportsImageRequest",
            "supportsImageWorker", "supportsImageInline",
            "supportsAudioPrimary", "supportsAudioExpert", "supportsAudioRequest",
            "supportsAudioWorker", "supportsAudioInline",
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

        // Write to config.yaml using the coerced values directly (not from config)
        // so manually-set config values aren't overwritten with stale config.
        const backendDir = config.get<string>("lean-ai.backendDir", "");
        const configPath = resolveConfigFilePath(backendDir || undefined, this._context.globalStorageUri.fsPath);
        if (configPath) {
            for (const [field, settingKey] of Object.entries(fieldToSetting)) {
                if (!(field in values)) { continue; }
                const envVar = BACKEND_SETTING_MAP[settingKey];
                if (!envVar) { continue; }
                const val = coercedMap.get(field);
                if (val !== undefined && val !== null && String(val) !== "") {
                    writeYamlSetting(configPath, envVar, String(val));
                } else {
                    clearYamlSetting(configPath, envVar);
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

        const openaiKeySet      = !!(await this._context.secrets.get(SECRET_KEYS.openaiApiKey));
        const anthropicKeySet   = !!(await this._context.secrets.get(SECRET_KEYS.anthropicApiKey));
        const geminiKeySet      = !!(await this._context.secrets.get(SECRET_KEYS.geminiApiKey));
        const serveKeySet       = !!(await this._context.secrets.get(SECRET_KEYS.serveApiKey));
        const jiraKeySet        = !!(await this._context.secrets.get(SECRET_KEYS.jiraApiToken));
        const servicenowKeySet  = !!(await this._context.secrets.get(SECRET_KEYS.servicenowPassword));
        const wikiKeySet        = !!(await this._context.secrets.get(SECRET_KEYS.wikiPassword));

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
            geminiRequestModel:        config.get("lean-ai.geminiRequestModel", ""),

            // Worker model
            ollamaModelWorker:         config.get("lean-ai.ollamaModelWorker", ""),
            ollamaWorkerContextWindow: config.get("lean-ai.ollamaWorkerContextWindow", ""),
            ollamaWorkerTemperature:   config.get("lean-ai.ollamaWorkerTemperature", ""),
            ollamaWorkerTopP:          config.get("lean-ai.ollamaWorkerTopP", ""),
            ollamaWorkerTopK:          config.get("lean-ai.ollamaWorkerTopK", ""),
            ollamaWorkerRepeatPenalty: config.get("lean-ai.ollamaWorkerRepeatPenalty", ""),
            ollamaWorkerMaxTokens:     config.get("lean-ai.ollamaWorkerMaxTokens", ""),
            enableThinkingWorker:      config.get("lean-ai.enableThinkingWorker", false),
            workerLlmProvider:         config.get("lean-ai.workerLlmProvider", ""),
            openaiWorkerModel:         config.get("lean-ai.openaiWorkerModel", ""),
            anthropicWorkerModel:      config.get("lean-ai.anthropicWorkerModel", ""),
            geminiWorkerModel:         config.get("lean-ai.geminiWorkerModel", ""),
            serveWorkerModel:          config.get("lean-ai.serveWorkerModel", ""),

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

            // Gemini (no key value — boolean only)
            geminiKeySet,
            geminiModel:               config.get("lean-ai.geminiModel", ""),
            geminiTemperature:         config.get("lean-ai.geminiTemperature", ""),
            geminiContextWindow:       config.get("lean-ai.geminiContextWindow", ""),
            geminiExpertModel:         config.get("lean-ai.geminiExpertModel", ""),

            // Lean AI Serve (no key value — boolean only)
            serveKeySet,
            serveUrl:                  config.get("lean-ai.serveUrl", ""),
            serveModel:                config.get("lean-ai.serveModel", ""),
            serveTemperature:          config.get("lean-ai.serveTemperature", ""),
            serveContextWindow:        config.get("lean-ai.serveContextWindow", ""),
            serveMaxTokens:            config.get("lean-ai.serveMaxTokens", ""),
            serveExpertModel:          config.get("lean-ai.serveExpertModel", ""),
            serveRequestModel:         config.get("lean-ai.serveRequestModel", ""),

            // Inline & embeddings
            inlineModel:               config.get("lean-ai.inlineModel", ""),
            inlineOllamaUrl:           config.get("lean-ai.inlineOllamaUrl", ""),
            embeddingModel:            config.get("lean-ai.embeddingModel", ""),
            enableEmbeddings:          config.get("lean-ai.enableEmbeddings", true),
            embeddingContextWindow:    config.get("lean-ai.embeddingContextWindow", ""),

            // Vision
            visionModel:               config.get("lean-ai.visionModel", ""),
            visionOllamaUrl:           config.get("lean-ai.visionOllamaUrl", ""),

            // Voice
            enableStt:                 config.get("lean-ai.enableStt", false),
            sttModel:                  config.get("lean-ai.sttModel", "turbo"),
            sttLanguage:               config.get("lean-ai.sttLanguage", ""),
            enableTts:                 config.get("lean-ai.enableTts", false),
            ttsVoice:                  config.get("lean-ai.ttsVoice", "af_heart"),
            ttsSpeed:                  config.get("lean-ai.ttsSpeed", 1.0),
            ttsCpuThreads:             config.get("lean-ai.ttsCpuThreads", 0),
            enableWakeWord:            config.get("lean-ai.enableWakeWord", false),

            // UI Verification
            enableUiVerification:      config.get("lean-ai.enableUiVerification", false),
            uiVerificationTimeout:     config.get("lean-ai.uiVerificationTimeout", 180),
            uiVerificationViewport:    config.get("lean-ai.uiVerificationViewport", "1280x800"),
            uiVerificationWaitSeconds: config.get("lean-ai.uiVerificationWaitSeconds", 3),

            // Per-model capability flags
            supportsImagePrimary: config.get("lean-ai.supportsImagePrimary", false),
            supportsImageExpert:  config.get("lean-ai.supportsImageExpert", false),
            supportsImageRequest: config.get("lean-ai.supportsImageRequest", false),
            supportsImageWorker:  config.get("lean-ai.supportsImageWorker", false),
            supportsImageInline:  config.get("lean-ai.supportsImageInline", false),
            supportsAudioPrimary: config.get("lean-ai.supportsAudioPrimary", false),
            supportsAudioExpert:  config.get("lean-ai.supportsAudioExpert", false),
            supportsAudioRequest: config.get("lean-ai.supportsAudioRequest", false),
            supportsAudioWorker:  config.get("lean-ai.supportsAudioWorker", false),
            supportsAudioInline:  config.get("lean-ai.supportsAudioInline", false),

            // Search
            searchProvider:            config.get("lean-ai.searchProvider", "duckduckgo"),
            searchApiUrl:              config.get("lean-ai.searchApiUrl", ""),
            searchDelay:               config.get("lean-ai.searchDelay", ""),

            // Integrations
            enableIntegrations:        config.get("lean-ai.enableIntegrations", false),
            integrationAutoPush:       config.get("lean-ai.integrationAutoPush", true),
            jiraKeySet,
            jiraUrl:                   config.get("lean-ai.jiraUrl", ""),
            jiraEmail:                 config.get("lean-ai.jiraEmail", ""),
            servicenowKeySet,
            servicenowUrl:             config.get("lean-ai.servicenowUrl", ""),
            servicenowUsername:        config.get("lean-ai.servicenowUsername", ""),
            servicenowTable:           config.get("lean-ai.servicenowTable", "incident"),
            wikiKeySet,
            wikiUrl:                   config.get("lean-ai.wikiUrl", ""),
            wikiApiPath:               config.get("lean-ai.wikiApiPath", "/w/api.php"),
            wikiUsername:              config.get("lean-ai.wikiUsername", ""),

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
            enableRequiredCitations:   config.get("lean-ai.enableRequiredCitations", true),
            refinerReferenceChunks:      config.get("lean-ai.refinerReferenceChunks", 5),
            referenceChunkChars:         config.get("lean-ai.referenceChunkChars", 1800),
            referenceNeighborWindow:     config.get("lean-ai.referenceNeighborWindow", 2),
            referenceSearchDefaultLimit: config.get("lean-ai.referenceSearchDefaultLimit", 5),
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

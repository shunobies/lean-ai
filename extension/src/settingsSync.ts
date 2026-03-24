/**
 * Settings synchronisation — maps VSCode settings to LEAN_AI_* env vars and
 * writes non-secret settings to backend/.env. API keys are handled separately
 * via VSCode SecretStorage (OS keychain), never written to .env.
 */

import * as vscode from "vscode";
import * as fs from "fs";
import * as path from "path";

// ── Secret key identifiers (stored in OS keychain via context.secrets) ──────

export const SECRET_KEYS = {
    openaiApiKey:    "lean-ai.openaiApiKey",
    anthropicApiKey: "lean-ai.anthropicApiKey",
} as const;

// ── Non-secret setting → env var mapping ────────────────────────────────────

export const BACKEND_SETTING_MAP: Record<string, string> = {
    // LLM provider selection
    "lean-ai.llmProvider":               "LEAN_AI_LLM_PROVIDER",

    // Ollama primary model
    "lean-ai.ollamaUrl":                 "LEAN_AI_OLLAMA_URL",
    "lean-ai.ollamaModel":               "LEAN_AI_OLLAMA_MODEL",
    "lean-ai.ollamaContextWindow":       "LEAN_AI_OLLAMA_CONTEXT_WINDOW",
    "lean-ai.ollamaTemperature":         "LEAN_AI_OLLAMA_TEMPERATURE",
    "lean-ai.ollamaTopP":                "LEAN_AI_OLLAMA_TOP_P",
    "lean-ai.ollamaTopK":                "LEAN_AI_OLLAMA_TOP_K",
    "lean-ai.ollamaRepeatPenalty":       "LEAN_AI_OLLAMA_REPEAT_PENALTY",
    "lean-ai.ollamaMaxTokens":           "LEAN_AI_OLLAMA_MAX_TOKENS",

    // Ollama expert model
    "lean-ai.ollamaModelExpert":         "LEAN_AI_OLLAMA_MODEL_EXPERT",
    "lean-ai.ollamaExpertContextWindow": "LEAN_AI_OLLAMA_EXPERT_CONTEXT_WINDOW",
    "lean-ai.ollamaExpertTemperature":   "LEAN_AI_OLLAMA_EXPERT_TEMPERATURE",
    "lean-ai.ollamaExpertTopP":          "LEAN_AI_OLLAMA_EXPERT_TOP_P",
    "lean-ai.ollamaExpertTopK":          "LEAN_AI_OLLAMA_EXPERT_TOP_K",
    "lean-ai.ollamaExpertRepeatPenalty": "LEAN_AI_OLLAMA_EXPERT_REPEAT_PENALTY",
    "lean-ai.ollamaExpertMaxTokens":     "LEAN_AI_OLLAMA_EXPERT_MAX_TOKENS",

    // Expert model provider
    "lean-ai.expertLlmProvider":         "LEAN_AI_EXPERT_LLM_PROVIDER",

    // Request model
    "lean-ai.ollamaModelRequest":        "LEAN_AI_OLLAMA_MODEL_REQUEST",
    "lean-ai.ollamaRequestContextWindow":"LEAN_AI_OLLAMA_REQUEST_CONTEXT_WINDOW",
    "lean-ai.ollamaRequestTemperature":  "LEAN_AI_OLLAMA_REQUEST_TEMPERATURE",
    "lean-ai.ollamaRequestTopP":         "LEAN_AI_OLLAMA_REQUEST_TOP_P",
    "lean-ai.ollamaRequestTopK":         "LEAN_AI_OLLAMA_REQUEST_TOP_K",
    "lean-ai.ollamaRequestRepeatPenalty":"LEAN_AI_OLLAMA_REQUEST_REPEAT_PENALTY",
    "lean-ai.ollamaRequestMaxTokens":    "LEAN_AI_OLLAMA_REQUEST_MAX_TOKENS",
    "lean-ai.requestLlmProvider":        "LEAN_AI_REQUEST_LLM_PROVIDER",
    "lean-ai.openaiRequestModel":        "LEAN_AI_OPENAI_REQUEST_MODEL",
    "lean-ai.anthropicRequestModel":     "LEAN_AI_ANTHROPIC_REQUEST_MODEL",

    // OpenAI (no API key — stored in SecretStorage)
    "lean-ai.openaiModel":               "LEAN_AI_OPENAI_MODEL",
    "lean-ai.openaiBaseUrl":             "LEAN_AI_OPENAI_BASE_URL",
    "lean-ai.openaiTemperature":         "LEAN_AI_OPENAI_TEMPERATURE",
    "lean-ai.openaiContextWindow":       "LEAN_AI_OPENAI_CONTEXT_WINDOW",
    "lean-ai.openaiExpertModel":         "LEAN_AI_OPENAI_EXPERT_MODEL",

    // Anthropic (no API key — stored in SecretStorage)
    "lean-ai.anthropicModel":            "LEAN_AI_ANTHROPIC_MODEL",
    "lean-ai.anthropicTemperature":      "LEAN_AI_ANTHROPIC_TEMPERATURE",
    "lean-ai.anthropicContextWindow":    "LEAN_AI_ANTHROPIC_CONTEXT_WINDOW",
    "lean-ai.anthropicExpertModel":      "LEAN_AI_ANTHROPIC_EXPERT_MODEL",

    // Inline predictions & embeddings
    "lean-ai.inlineModel":               "LEAN_AI_INLINE_MODEL",
    "lean-ai.inlineOllamaUrl":           "LEAN_AI_INLINE_OLLAMA_URL",
    "lean-ai.embeddingModel":            "LEAN_AI_EMBEDDING_MODEL",
    "lean-ai.enableEmbeddings":          "LEAN_AI_ENABLE_EMBEDDINGS",

    // Vision model
    "lean-ai.visionModel":              "LEAN_AI_VISION_MODEL",
    "lean-ai.visionOllamaUrl":          "LEAN_AI_VISION_OLLAMA_URL",

    // Voice
    "lean-ai.enableStt":                 "LEAN_AI_ENABLE_STT",
    "lean-ai.sttModel":                  "LEAN_AI_STT_MODEL",
    "lean-ai.sttLanguage":               "LEAN_AI_STT_LANGUAGE",
    "lean-ai.enableTts":                 "LEAN_AI_ENABLE_TTS",
    "lean-ai.ttsVoice":                  "LEAN_AI_TTS_VOICE",
    "lean-ai.ttsSpeed":                  "LEAN_AI_TTS_SPEED",
    "lean-ai.enableWakeWord":            "LEAN_AI_ENABLE_WAKE_WORD",

    // Search
    "lean-ai.searchProvider":            "LEAN_AI_SEARCH_PROVIDER",
    "lean-ai.searchApiUrl":              "LEAN_AI_SEARCH_API_URL",
    "lean-ai.searchDelay":               "LEAN_AI_SEARCH_DELAY",

    // TDD mode
    "lean-ai.enableTdd":                 "LEAN_AI_ENABLE_TDD",

    // Post-validation
    "lean-ai.enablePostValidation":      "LEAN_AI_ENABLE_POST_VALIDATION",
    "lean-ai.postFormatCommand":         "LEAN_AI_POST_FORMAT_COMMAND",
    "lean-ai.postLintFixCommand":        "LEAN_AI_POST_LINT_FIX_COMMAND",
    "lean-ai.postLintCommand":           "LEAN_AI_POST_LINT_COMMAND",
    "lean-ai.postTestCommand":           "LEAN_AI_POST_TEST_COMMAND",
    "lean-ai.postValidationMaxRetries":  "LEAN_AI_POST_VALIDATION_MAX_RETRIES",
    "lean-ai.postValidationFixTurns":    "LEAN_AI_POST_VALIDATION_FIX_TURNS",

    // Advanced / misc
    "lean-ai.enableFrameworkGuide":      "LEAN_AI_ENABLE_FRAMEWORK_GUIDE",
    "lean-ai.numParallel":               "LEAN_AI_NUM_PARALLEL",
    "lean-ai.implementationMaxTurns":    "LEAN_AI_IMPLEMENTATION_MAX_TURNS",
    "lean-ai.refreshThreshold":          "LEAN_AI_REFRESH_THRESHOLD",
    "lean-ai.debugPlanning":             "LEAN_AI_DEBUG_PLANNING",
    "lean-ai.enableThinking":            "LEAN_AI_ENABLE_THINKING",
    "lean-ai.enableThinkingExpert":      "LEAN_AI_ENABLE_THINKING_EXPERT",
    "lean-ai.enableThinkingRequest":     "LEAN_AI_ENABLE_THINKING_REQUEST",
};

// ── Env building helpers ─────────────────────────────────────────────────────

/**
 * Read all non-secret VSCode settings and return them as a LEAN_AI_* env var
 * map. Empty / undefined settings are omitted so backend defaults still apply.
 */
export function buildBackendEnv(): Record<string, string> {
    const config = vscode.workspace.getConfiguration();
    const env: Record<string, string> = {};
    for (const [key, envVar] of Object.entries(BACKEND_SETTING_MAP)) {
        const val = config.get<unknown>(key);
        if (val !== undefined && val !== null && val !== "") {
            env[envVar] = String(val);
        }
    }
    return env;
}

/**
 * Like buildBackendEnv but also injects API keys from SecretStorage.
 * Always use this when spawning the backend subprocess.
 */
export async function buildFullBackendEnv(
    secrets: vscode.SecretStorage,
): Promise<Record<string, string>> {
    const env = buildBackendEnv();
    const openaiKey    = await secrets.get(SECRET_KEYS.openaiApiKey);
    const anthropicKey = await secrets.get(SECRET_KEYS.anthropicApiKey);
    if (openaiKey)    { env["LEAN_AI_OPENAI_API_KEY"]    = openaiKey; }
    if (anthropicKey) { env["LEAN_AI_ANTHROPIC_API_KEY"] = anthropicKey; }
    return env;
}

// ── .env file write ──────────────────────────────────────────────────────────

/**
 * Write or update a single LEAN_AI_* key in the backend .env file.
 * Preserves existing comments and unrelated lines. Creates the file if missing.
 * Never call this with API key env vars — those live only in SecretStorage.
 */
export function writeEnvSetting(
    envFilePath: string,
    envKey: string,
    value: string,
): void {
    let lines: string[] = [];
    if (fs.existsSync(envFilePath)) {
        lines = fs.readFileSync(envFilePath, "utf-8").split("\n");
    }

    const prefix = `${envKey}=`;
    // Match active line OR a commented-out version of the same key
    const commentedRe = new RegExp(`^#\\s*${envKey}=`);
    const idx = lines.findIndex(
        (l) => l.startsWith(prefix) || commentedRe.test(l),
    );

    const newLine = `${envKey}=${value}`;
    if (idx >= 0) {
        lines[idx] = newLine;
    } else {
        // Append, ensuring a blank separator before the new line
        if (lines.length > 0 && lines[lines.length - 1] !== "") {
            lines.push("");
        }
        lines.push(newLine);
    }

    fs.mkdirSync(path.dirname(envFilePath), { recursive: true });
    fs.writeFileSync(envFilePath, lines.join("\n"), "utf-8");
}

/**
 * Comment out a LEAN_AI_* key in the backend .env file, preserving the old
 * value for reference.  No-op if the key is already absent or commented.
 */
export function clearEnvSetting(envFilePath: string, envKey: string): void {
    if (!fs.existsSync(envFilePath)) { return; }
    const lines = fs.readFileSync(envFilePath, "utf-8").split("\n");
    const prefix = `${envKey}=`;
    const idx = lines.findIndex((l) => l.startsWith(prefix));
    if (idx < 0) { return; } // not present or already commented
    lines[idx] = `#${lines[idx]}`;
    fs.writeFileSync(envFilePath, lines.join("\n"), "utf-8");
}

/**
 * Resolve the backend .env file path.
 * Priority: explicit backendDir → workspace backend/ → globalStorageDir (managed mode).
 */
export function resolveEnvFilePath(backendDir?: string, globalStorageDir?: string): string | null {
    if (backendDir) {
        return path.join(backendDir, ".env");
    }
    const folders = vscode.workspace.workspaceFolders;
    if (folders && folders.length > 0) {
        const candidate = path.join(folders[0].uri.fsPath, "backend");
        if (fs.existsSync(candidate)) {
            return path.join(candidate, ".env");
        }
    }
    if (globalStorageDir) {
        return path.join(globalStorageDir, ".env");
    }
    return null;
}

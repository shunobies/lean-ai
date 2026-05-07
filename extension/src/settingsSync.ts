/**
 * Settings synchronisation — maps VSCode settings to LEAN_AI_* env vars and
 * writes non-secret settings to backend/config.yaml (falling back to .env for
 * legacy installs).  API keys are handled separately via VSCode SecretStorage
 * (OS keychain), never written to config files.
 */

import * as vscode from "vscode";
import * as fs from "fs";
import * as path from "path";

// ── Secret key identifiers (stored in OS keychain via context.secrets) ──────

export const SECRET_KEYS = {
    openaiApiKey:       "lean-ai.openaiApiKey",
    anthropicApiKey:    "lean-ai.anthropicApiKey",
    geminiApiKey:       "lean-ai.geminiApiKey",
    serveApiKey:        "lean-ai.serveApiKey",
    githubApiToken:     "lean-ai.githubApiToken",
    jiraApiToken:       "lean-ai.jiraApiToken",
    servicenowPassword: "lean-ai.servicenowPassword",
    wikiPassword:       "lean-ai.wikiPassword",
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

    // Worker model
    "lean-ai.ollamaModelWorker":         "LEAN_AI_OLLAMA_MODEL_WORKER",
    "lean-ai.workerLlmProvider":         "LEAN_AI_WORKER_LLM_PROVIDER",
    "lean-ai.ollamaWorkerContextWindow": "LEAN_AI_OLLAMA_WORKER_CONTEXT_WINDOW",
    "lean-ai.ollamaWorkerTemperature":   "LEAN_AI_OLLAMA_WORKER_TEMPERATURE",
    "lean-ai.ollamaWorkerTopP":          "LEAN_AI_OLLAMA_WORKER_TOP_P",
    "lean-ai.ollamaWorkerTopK":          "LEAN_AI_OLLAMA_WORKER_TOP_K",
    "lean-ai.ollamaWorkerRepeatPenalty": "LEAN_AI_OLLAMA_WORKER_REPEAT_PENALTY",
    "lean-ai.ollamaWorkerMaxTokens":     "LEAN_AI_OLLAMA_WORKER_MAX_TOKENS",
    "lean-ai.enableThinkingWorker":      "LEAN_AI_ENABLE_THINKING_WORKER",
    "lean-ai.openaiWorkerModel":         "LEAN_AI_OPENAI_WORKER_MODEL",
    "lean-ai.anthropicWorkerModel":      "LEAN_AI_ANTHROPIC_WORKER_MODEL",
    "lean-ai.geminiWorkerModel":         "LEAN_AI_GEMINI_WORKER_MODEL",
    "lean-ai.serveWorkerModel":          "LEAN_AI_SERVE_WORKER_MODEL",

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

    // Gemini (no API key — stored in SecretStorage)
    "lean-ai.geminiModel":              "LEAN_AI_GEMINI_MODEL",
    "lean-ai.geminiTemperature":        "LEAN_AI_GEMINI_TEMPERATURE",
    "lean-ai.geminiContextWindow":      "LEAN_AI_GEMINI_CONTEXT_WINDOW",
    "lean-ai.geminiExpertModel":        "LEAN_AI_GEMINI_EXPERT_MODEL",
    "lean-ai.geminiRequestModel":       "LEAN_AI_GEMINI_REQUEST_MODEL",

    // Lean AI Serve (no API key — stored in SecretStorage)
    "lean-ai.serveUrl":                  "LEAN_AI_SERVE_URL",
    "lean-ai.serveModel":               "LEAN_AI_SERVE_MODEL",
    "lean-ai.serveTemperature":          "LEAN_AI_SERVE_TEMPERATURE",
    "lean-ai.serveContextWindow":        "LEAN_AI_SERVE_CONTEXT_WINDOW",
    "lean-ai.serveMaxTokens":            "LEAN_AI_SERVE_MAX_TOKENS",
    "lean-ai.serveExpertModel":          "LEAN_AI_SERVE_EXPERT_MODEL",
    "lean-ai.serveRequestModel":         "LEAN_AI_SERVE_REQUEST_MODEL",

    // Inline predictions & embeddings
    "lean-ai.inlineModel":               "LEAN_AI_INLINE_MODEL",
    "lean-ai.inlineOllamaUrl":           "LEAN_AI_INLINE_OLLAMA_URL",
    "lean-ai.embeddingModel":            "LEAN_AI_EMBEDDING_MODEL",
    "lean-ai.enableEmbeddings":          "LEAN_AI_ENABLE_EMBEDDINGS",
    "lean-ai.embeddingContextWindow":    "LEAN_AI_EMBEDDING_CONTEXT_WINDOW",

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
    "lean-ai.ttsCpuThreads":             "LEAN_AI_TTS_CPU_THREADS",
    "lean-ai.enableWakeWord":            "LEAN_AI_ENABLE_WAKE_WORD",

    // UI Verification
    "lean-ai.enableUiVerification":      "LEAN_AI_ENABLE_UI_VERIFICATION",
    "lean-ai.uiVerificationTimeout":     "LEAN_AI_UI_VERIFICATION_TIMEOUT",
    "lean-ai.uiVerificationViewport":    "LEAN_AI_UI_VERIFICATION_VIEWPORT",
    "lean-ai.uiVerificationWaitSeconds": "LEAN_AI_UI_VERIFICATION_WAIT_SECONDS",

    // Per-role optional sampling params (blank → backend omits from options dict)
    "lean-ai.ollamaMinP":                "LEAN_AI_OLLAMA_MIN_P",
    "lean-ai.ollamaPresencePenalty":     "LEAN_AI_OLLAMA_PRESENCE_PENALTY",
    "lean-ai.ollamaExpertMinP":          "LEAN_AI_OLLAMA_EXPERT_MIN_P",
    "lean-ai.ollamaExpertPresencePenalty": "LEAN_AI_OLLAMA_EXPERT_PRESENCE_PENALTY",
    "lean-ai.ollamaRequestMinP":         "LEAN_AI_OLLAMA_REQUEST_MIN_P",
    "lean-ai.ollamaRequestPresencePenalty": "LEAN_AI_OLLAMA_REQUEST_PRESENCE_PENALTY",
    "lean-ai.ollamaWorkerMinP":          "LEAN_AI_OLLAMA_WORKER_MIN_P",
    "lean-ai.ollamaWorkerPresencePenalty": "LEAN_AI_OLLAMA_WORKER_PRESENCE_PENALTY",

    // Preserve chain-of-thought across turns (Qwen3.6+/vLLM feature)
    "lean-ai.preserveThinkingPrimary": "LEAN_AI_PRESERVE_THINKING_PRIMARY",
    "lean-ai.preserveThinkingExpert":  "LEAN_AI_PRESERVE_THINKING_EXPERT",
    "lean-ai.preserveThinkingRequest": "LEAN_AI_PRESERVE_THINKING_REQUEST",
    "lean-ai.preserveThinkingWorker":  "LEAN_AI_PRESERVE_THINKING_WORKER",

    // Reasoning effort — per-role soft cap (client-side interrupt on
    // Ollama; native reasoning_effort / thinking.budget_tokens /
    // thinking_budget on cloud providers)
    "lean-ai.reasoningEffortPrimary": "LEAN_AI_REASONING_EFFORT_PRIMARY",
    "lean-ai.reasoningEffortExpert":  "LEAN_AI_REASONING_EFFORT_EXPERT",
    "lean-ai.reasoningEffortRequest": "LEAN_AI_REASONING_EFFORT_REQUEST",
    "lean-ai.reasoningEffortWorker":  "LEAN_AI_REASONING_EFFORT_WORKER",
    "lean-ai.maxThinkingTokens":      "LEAN_AI_MAX_THINKING_TOKENS",

    // Per-model capability flags (image + audio)
    "lean-ai.supportsImagePrimary": "LEAN_AI_SUPPORTS_IMAGE_PRIMARY",
    "lean-ai.supportsImageExpert":  "LEAN_AI_SUPPORTS_IMAGE_EXPERT",
    "lean-ai.supportsImageRequest": "LEAN_AI_SUPPORTS_IMAGE_REQUEST",
    "lean-ai.supportsImageWorker":  "LEAN_AI_SUPPORTS_IMAGE_WORKER",
    "lean-ai.supportsImageInline":  "LEAN_AI_SUPPORTS_IMAGE_INLINE",
    "lean-ai.supportsAudioPrimary": "LEAN_AI_SUPPORTS_AUDIO_PRIMARY",
    "lean-ai.supportsAudioExpert":  "LEAN_AI_SUPPORTS_AUDIO_EXPERT",
    "lean-ai.supportsAudioRequest": "LEAN_AI_SUPPORTS_AUDIO_REQUEST",
    "lean-ai.supportsAudioWorker":  "LEAN_AI_SUPPORTS_AUDIO_WORKER",
    "lean-ai.supportsAudioInline":  "LEAN_AI_SUPPORTS_AUDIO_INLINE",

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

    // Integrations
    "lean-ai.enableIntegrations":       "LEAN_AI_ENABLE_INTEGRATIONS",
    "lean-ai.integrationAutoPush":      "LEAN_AI_INTEGRATION_AUTO_PUSH",
    "lean-ai.githubRepo":               "LEAN_AI_GITHUB_REPO",
    "lean-ai.githubCoauthorEnabled":    "LEAN_AI_GITHUB_COAUTHOR_ENABLED",
    "lean-ai.githubCoauthorName":       "LEAN_AI_GITHUB_COAUTHOR_NAME",
    "lean-ai.githubCoauthorEmail":      "LEAN_AI_GITHUB_COAUTHOR_EMAIL",
    "lean-ai.jiraUrl":                  "LEAN_AI_JIRA_URL",
    "lean-ai.jiraEmail":                "LEAN_AI_JIRA_EMAIL",
    "lean-ai.servicenowUrl":            "LEAN_AI_SERVICENOW_URL",
    "lean-ai.servicenowUsername":       "LEAN_AI_SERVICENOW_USERNAME",
    "lean-ai.servicenowTable":          "LEAN_AI_SERVICENOW_TABLE",
    "lean-ai.wikiUrl":                  "LEAN_AI_WIKI_URL",
    "lean-ai.wikiApiPath":              "LEAN_AI_WIKI_API_PATH",
    "lean-ai.wikiUsername":             "LEAN_AI_WIKI_USERNAME",

    // Advanced / misc
    "lean-ai.enableRequiredCitations":   "LEAN_AI_ENABLE_REQUIRED_CITATIONS",
    "lean-ai.refinerReferenceChunks":    "LEAN_AI_REFINER_REFERENCE_CHUNKS",
    "lean-ai.referenceChunkChars":       "LEAN_AI_REFERENCE_CHUNK_CHARS",
    "lean-ai.referenceNeighborWindow":   "LEAN_AI_REFERENCE_NEIGHBOR_WINDOW",
    "lean-ai.referenceSearchDefaultLimit": "LEAN_AI_REFERENCE_SEARCH_DEFAULT_LIMIT",
    "lean-ai.numParallel":               "LEAN_AI_NUM_PARALLEL",
    "lean-ai.implementationMaxTurns":    "LEAN_AI_IMPLEMENTATION_MAX_TURNS",
    "lean-ai.refreshThreshold":          "LEAN_AI_REFRESH_THRESHOLD",
    "lean-ai.debugPlanning":             "LEAN_AI_DEBUG_PLANNING",
    "lean-ai.enableThinking":            "LEAN_AI_ENABLE_THINKING",
    "lean-ai.enableThinkingExpert":      "LEAN_AI_ENABLE_THINKING_EXPERT",
    "lean-ai.enableThinkingRequest":     "LEAN_AI_ENABLE_THINKING_REQUEST",
};

// ── Zero-value filtering ─────────────────────────────────────────────────────

// Numeric VSCode settings where 0 means "not configured — let the backend use
// its own default". Sampling settings are intentionally excluded because
// values like temperature=0 and min_p=0 are valid explicit choices.
const ZERO_MEANS_UNSET: ReadonlySet<string> = new Set([
    // Primary Ollama model
    "lean-ai.ollamaContextWindow",
    "lean-ai.ollamaMaxTokens",
    // Expert Ollama model
    "lean-ai.ollamaExpertContextWindow",
    "lean-ai.ollamaExpertMaxTokens",
    // Request Ollama model
    "lean-ai.ollamaRequestContextWindow",
    "lean-ai.ollamaRequestMaxTokens",
    // Worker Ollama model
    "lean-ai.ollamaWorkerContextWindow",
    "lean-ai.ollamaWorkerMaxTokens",
    // OpenAI
    "lean-ai.openaiContextWindow",
    "lean-ai.openaiTemperature",
    // Anthropic
    "lean-ai.anthropicContextWindow",
    "lean-ai.anthropicTemperature",
    // Gemini
    "lean-ai.geminiContextWindow",
    "lean-ai.geminiTemperature",
    // Lean AI Serve
    "lean-ai.serveContextWindow",
    "lean-ai.serveTemperature",
    "lean-ai.serveMaxTokens",
    // Embedding model (0 = auto-detect via Ollama show API)
    "lean-ai.embeddingContextWindow",
]);

const OPTIONAL_NUMERIC_SETTINGS: ReadonlySet<string> = new Set([
    "lean-ai.ollamaTemperature",
    "lean-ai.ollamaTopP",
    "lean-ai.ollamaTopK",
    "lean-ai.ollamaRepeatPenalty",
    "lean-ai.ollamaMinP",
    "lean-ai.ollamaPresencePenalty",
    "lean-ai.ollamaExpertTemperature",
    "lean-ai.ollamaExpertTopP",
    "lean-ai.ollamaExpertTopK",
    "lean-ai.ollamaExpertRepeatPenalty",
    "lean-ai.ollamaExpertMinP",
    "lean-ai.ollamaExpertPresencePenalty",
    "lean-ai.ollamaRequestTemperature",
    "lean-ai.ollamaRequestTopP",
    "lean-ai.ollamaRequestTopK",
    "lean-ai.ollamaRequestRepeatPenalty",
    "lean-ai.ollamaRequestMinP",
    "lean-ai.ollamaRequestPresencePenalty",
    "lean-ai.ollamaWorkerTemperature",
    "lean-ai.ollamaWorkerTopP",
    "lean-ai.ollamaWorkerTopK",
    "lean-ai.ollamaWorkerRepeatPenalty",
    "lean-ai.ollamaWorkerMinP",
    "lean-ai.ollamaWorkerPresencePenalty",
]);

function hasExplicitConfiguration(config: vscode.WorkspaceConfiguration, key: string): boolean {
    const inspected = config.inspect(key);
    return inspected?.globalValue !== undefined ||
        inspected?.workspaceValue !== undefined ||
        inspected?.workspaceFolderValue !== undefined;
}

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
            if (val === 0 && ZERO_MEANS_UNSET.has(key)) {
                continue;
            }
            if (val === 0 && OPTIONAL_NUMERIC_SETTINGS.has(key) && !hasExplicitConfiguration(config, key)) {
                continue;
            }
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
    const openaiKey       = await secrets.get(SECRET_KEYS.openaiApiKey);
    const anthropicKey    = await secrets.get(SECRET_KEYS.anthropicApiKey);
    const geminiKey       = await secrets.get(SECRET_KEYS.geminiApiKey);
    const serveKey        = await secrets.get(SECRET_KEYS.serveApiKey);
    const githubToken     = await secrets.get(SECRET_KEYS.githubApiToken);
    const jiraToken       = await secrets.get(SECRET_KEYS.jiraApiToken);
    const servicenowPass  = await secrets.get(SECRET_KEYS.servicenowPassword);
    const wikiPass        = await secrets.get(SECRET_KEYS.wikiPassword);
    if (openaiKey)       { env["LEAN_AI_OPENAI_API_KEY"]       = openaiKey; }
    if (anthropicKey)    { env["LEAN_AI_ANTHROPIC_API_KEY"]    = anthropicKey; }
    if (geminiKey)       { env["LEAN_AI_GEMINI_API_KEY"]       = geminiKey; }
    if (serveKey)        { env["LEAN_AI_SERVE_API_KEY"]        = serveKey; }
    if (githubToken)     { env["LEAN_AI_GITHUB_API_TOKEN"]     = githubToken; }
    if (jiraToken)       { env["LEAN_AI_JIRA_API_TOKEN"]       = jiraToken; }
    if (servicenowPass)  { env["LEAN_AI_SERVICENOW_PASSWORD"]  = servicenowPass; }
    if (wikiPass)        { env["LEAN_AI_WIKI_PASSWORD"]        = wikiPass; }
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
 * Resolve the backend .env file path (legacy fallback).
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

// ── YAML config file support ────────────────────────────────────────────────

/**
 * Convert a LEAN_AI_* env var name to a YAML field name.
 * e.g. "LEAN_AI_OLLAMA_URL" → "ollama_url"
 */
export function envVarToFieldName(envVar: string): string {
    return envVar.replace(/^LEAN_AI_/, "").toLowerCase();
}

/** Return true if *value* needs quoting in YAML. */
function needsYamlQuoting(value: string): boolean {
    if (!value) { return false; }
    if (/[:#{}[\]|>&*!%@`]/.test(value)) { return true; }
    if (["true", "false", "null", "yes", "no", "on", "off"].includes(value.toLowerCase())) {
        return true;
    }
    return false;
}

/** Format a value for YAML output. */
function yamlValue(value: string): string {
    if (needsYamlQuoting(value)) {
        return `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
    }
    return value;
}

/**
 * Write or update a single field in the backend config.yaml file.
 * Preserves existing comments and unrelated lines. Creates the file if missing.
 */
export function writeYamlSetting(
    yamlFilePath: string,
    envVar: string,
    value: string,
): void {
    const fieldName = envVarToFieldName(envVar);
    let lines: string[] = [];
    if (fs.existsSync(yamlFilePath)) {
        lines = fs.readFileSync(yamlFilePath, "utf-8").split("\n");
    }

    const prefix = `${fieldName}:`;
    const commentedRe = new RegExp(`^#\\s*${fieldName}:`);
    const idx = lines.findIndex(
        (l) => l.startsWith(prefix) || commentedRe.test(l),
    );

    const newLine = `${fieldName}: ${yamlValue(value)}`;
    if (idx >= 0) {
        lines[idx] = newLine;
    } else {
        if (lines.length > 0 && lines[lines.length - 1] !== "") {
            lines.push("");
        }
        lines.push(newLine);
    }

    fs.mkdirSync(path.dirname(yamlFilePath), { recursive: true });
    fs.writeFileSync(yamlFilePath, lines.join("\n"), "utf-8");
}

/**
 * Comment out a field in the backend config.yaml file.
 * No-op if the field is already absent or commented.
 */
export function clearYamlSetting(yamlFilePath: string, envVar: string): void {
    if (!fs.existsSync(yamlFilePath)) { return; }
    const fieldName = envVarToFieldName(envVar);
    const lines = fs.readFileSync(yamlFilePath, "utf-8").split("\n");
    const prefix = `${fieldName}:`;
    const idx = lines.findIndex((l) => l.startsWith(prefix));
    if (idx < 0) { return; }
    lines[idx] = `# ${lines[idx]}`;
    fs.writeFileSync(yamlFilePath, lines.join("\n"), "utf-8");
}

/**
 * Resolve the backend config.yaml file path.
 * Priority: explicit backendDir → workspace backend/ → globalStorageDir.
 */
export function resolveConfigFilePath(backendDir?: string, globalStorageDir?: string): string | null {
    if (backendDir) {
        return path.join(backendDir, "config.yaml");
    }
    const folders = vscode.workspace.workspaceFolders;
    if (folders && folders.length > 0) {
        const candidate = path.join(folders[0].uri.fsPath, "backend");
        if (fs.existsSync(candidate)) {
            return path.join(candidate, "config.yaml");
        }
    }
    if (globalStorageDir) {
        return path.join(globalStorageDir, "config.yaml");
    }
    return null;
}

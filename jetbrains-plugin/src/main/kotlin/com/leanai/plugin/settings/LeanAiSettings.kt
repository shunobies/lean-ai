package com.leanai.plugin.settings

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.components.PersistentStateComponent
import com.intellij.openapi.components.Service
import com.intellij.openapi.components.State
import com.intellij.openapi.components.Storage

/**
 * Application-level persistent settings for Lean AI.
 * Stored in IDE config as leanai.xml. Maps 1:1 to the VSCode extension's
 * settings schema in package.json.
 */
@Service(Service.Level.APP)
@State(name = "LeanAiSettings", storages = [Storage("leanai.xml")])
class LeanAiSettings : PersistentStateComponent<LeanAiSettings.State> {

    data class State(
        // ── LLM Provider ─────────────────────────────────────────────────
        var llmProvider: String = "ollama",

        // ── Ollama Primary ───────────────────────────────────────────────
        var ollamaUrl: String = "http://localhost:11434",
        var ollamaModel: String = "qwen3-coder:30b",
        var ollamaContextWindow: Int = 0,
        var ollamaTemperature: Double = 0.0,
        var ollamaTopP: Double = 0.0,
        var ollamaTopK: Int = 0,
        var ollamaRepeatPenalty: Double = 0.0,
        var ollamaMaxTokens: Int = 0,

        // ── Ollama Expert ────────────────────────────────────────────────
        var ollamaModelExpert: String = "",
        var ollamaExpertContextWindow: Int = 0,
        var ollamaExpertTemperature: Double = 0.0,
        var ollamaExpertTopP: Double = 0.0,
        var ollamaExpertTopK: Int = 0,
        var ollamaExpertRepeatPenalty: Double = 0.0,
        var ollamaExpertMaxTokens: Int = 0,
        var expertLlmProvider: String = "",

        // ── Ollama Request ───────────────────────────────────────────────
        var ollamaModelRequest: String = "",
        var ollamaRequestContextWindow: Int = 0,
        var ollamaRequestTemperature: Double = 0.0,
        var ollamaRequestTopP: Double = 0.0,
        var ollamaRequestTopK: Int = 0,
        var ollamaRequestRepeatPenalty: Double = 0.0,
        var ollamaRequestMaxTokens: Int = 0,
        var requestLlmProvider: String = "",

        // ── OpenAI ───────────────────────────────────────────────────────
        var openaiModel: String = "gpt-4o",
        var openaiBaseUrl: String = "",
        var openaiTemperature: Double = 0.0,
        var openaiContextWindow: Int = 0,
        var openaiExpertModel: String = "",
        var openaiRequestModel: String = "",

        // ── Anthropic ────────────────────────────────────────────────────
        var anthropicModel: String = "claude-sonnet-4-20250514",
        var anthropicTemperature: Double = 0.0,
        var anthropicContextWindow: Int = 0,
        var anthropicExpertModel: String = "",
        var anthropicRequestModel: String = "",

        // ── Gemini ───────────────────────────────────────────────────────
        var geminiModel: String = "gemini-2.5-flash",
        var geminiTemperature: Double = 0.0,
        var geminiContextWindow: Int = 0,
        var geminiExpertModel: String = "",
        var geminiRequestModel: String = "",

        // ── Lean AI Serve ────────────────────────────────────────────────
        var serveUrl: String = "http://localhost:8420",
        var serveModel: String = "",
        var serveTemperature: Double = 0.0,
        var serveContextWindow: Int = 0,
        var serveMaxTokens: Int = 0,
        var serveExpertModel: String = "",
        var serveRequestModel: String = "",

        // ── Inline Predictions & Embeddings ──────────────────────────────
        var inlineModel: String = "",
        var inlineOllamaUrl: String = "",
        var embeddingModel: String = "qwen3-embedding:0.6b",
        var enableEmbeddings: Boolean = true,
        var enableInlinePredictions: Boolean = true,

        // ── Vision ───────────────────────────────────────────────────────
        var visionModel: String = "",
        var visionOllamaUrl: String = "",

        // ── Voice ────────────────────────────────────────────────────────
        var enableStt: Boolean = false,
        var sttModel: String = "turbo",
        var sttLanguage: String = "",
        var enableTts: Boolean = false,
        var ttsVoice: String = "af_heart",
        var ttsSpeed: Double = 1.0,
        var ttsCpuThreads: Int = 6,
        var enableWakeWord: Boolean = false,

        // ── Search ───────────────────────────────────────────────────────
        var searchProvider: String = "duckduckgo",
        var searchApiUrl: String = "",
        var searchDelay: Double = 2.0,

        // ── TDD ──────────────────────────────────────────────────────────
        var enableTdd: Boolean = false,

        // ── Post-Validation ──────────────────────────────────────────────
        var enablePostValidation: Boolean = true,
        var postFormatCommand: String = "",
        var postLintFixCommand: String = "",
        var postLintCommand: String = "",
        var postTestCommand: String = "",
        var postValidationMaxRetries: Int = 2,
        var postValidationFixTurns: Int = 30,

        // ── Integrations ─────────────────────────────────────────────────
        var enableIntegrations: Boolean = false,
        var integrationAutoPush: Boolean = true,
        var jiraUrl: String = "",
        var jiraEmail: String = "",
        var servicenowUrl: String = "",
        var servicenowUsername: String = "",
        var servicenowTable: String = "incident",
        var wikiUrl: String = "",
        var wikiApiPath: String = "/w/api.php",
        var wikiUsername: String = "",

        // ── Advanced ─────────────────────────────────────────────────────
        var enableFrameworkGuide: Boolean = true,
        var numParallel: Int = 1,
        var implementationMaxTurns: Int = 0,
        var refreshThreshold: Double = 0.7,
        var debugPlanning: Boolean = false,
        var enableThinking: Boolean = true,
        var enableThinkingExpert: Boolean = true,
        var enableThinkingRequest: Boolean = true,

        // ── Plugin-specific (not mapped to backend env vars) ─────────────
        var port: Int = 8422,
        var backendHost: String = "127.0.0.1",
        var pythonPath: String = "",   // Empty = auto-detect
        var backendDir: String = "",   // Empty = managed mode
    )

    private var state = State()

    override fun getState(): State = state

    override fun loadState(state: State) {
        this.state = state
    }

    companion object {
        fun getInstance(): LeanAiSettings =
            ApplicationManager.getApplication().getService(LeanAiSettings::class.java)
    }
}

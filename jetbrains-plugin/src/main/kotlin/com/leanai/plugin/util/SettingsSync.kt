package com.leanai.plugin.util

import com.intellij.credentialStore.CredentialAttributes
import com.intellij.credentialStore.generateServiceName
import com.intellij.ide.passwordSafe.PasswordSafe
import com.leanai.plugin.settings.LeanAiSettings

/**
 * Maps LeanAiSettings fields to LEAN_AI_* environment variables for the backend.
 * Port of extension/src/settingsSync.ts BACKEND_SETTING_MAP.
 */
object SettingsSync {

    /** Numeric fields where 0 means "not configured — use backend default". */
    private val ZERO_MEANS_UNSET = setOf(
        "LEAN_AI_OLLAMA_CONTEXT_WINDOW", "LEAN_AI_OLLAMA_MAX_TOKENS",
        "LEAN_AI_OLLAMA_TEMPERATURE", "LEAN_AI_OLLAMA_TOP_P",
        "LEAN_AI_OLLAMA_TOP_K", "LEAN_AI_OLLAMA_REPEAT_PENALTY",
        "LEAN_AI_OLLAMA_EXPERT_CONTEXT_WINDOW", "LEAN_AI_OLLAMA_EXPERT_MAX_TOKENS",
        "LEAN_AI_OLLAMA_EXPERT_TEMPERATURE", "LEAN_AI_OLLAMA_EXPERT_TOP_P",
        "LEAN_AI_OLLAMA_EXPERT_TOP_K", "LEAN_AI_OLLAMA_EXPERT_REPEAT_PENALTY",
        "LEAN_AI_OLLAMA_REQUEST_CONTEXT_WINDOW", "LEAN_AI_OLLAMA_REQUEST_MAX_TOKENS",
        "LEAN_AI_OLLAMA_REQUEST_TEMPERATURE", "LEAN_AI_OLLAMA_REQUEST_TOP_P",
        "LEAN_AI_OLLAMA_REQUEST_TOP_K", "LEAN_AI_OLLAMA_REQUEST_REPEAT_PENALTY",
        "LEAN_AI_OPENAI_CONTEXT_WINDOW", "LEAN_AI_OPENAI_TEMPERATURE",
        "LEAN_AI_ANTHROPIC_CONTEXT_WINDOW", "LEAN_AI_ANTHROPIC_TEMPERATURE",
        "LEAN_AI_GEMINI_CONTEXT_WINDOW", "LEAN_AI_GEMINI_TEMPERATURE",
        "LEAN_AI_SERVE_CONTEXT_WINDOW", "LEAN_AI_SERVE_TEMPERATURE",
        "LEAN_AI_SERVE_MAX_TOKENS",
    )

    /**
     * Build a LEAN_AI_* env var map from current settings (non-secret only).
     * Empty/default values are omitted so backend defaults still apply.
     */
    fun buildBackendEnv(): Map<String, String> {
        val s = LeanAiSettings.getInstance().state
        val env = mutableMapOf<String, String>()

        fun putIfSet(envVar: String, value: String) {
            if (value.isNotEmpty()) env[envVar] = value
        }
        fun putIfSet(envVar: String, value: Int) {
            if (value == 0 && envVar in ZERO_MEANS_UNSET) return
            if (value != 0) env[envVar] = value.toString()
        }
        fun putIfSet(envVar: String, value: Double) {
            if (value == 0.0 && envVar in ZERO_MEANS_UNSET) return
            if (value != 0.0) env[envVar] = value.toString()
        }
        fun putBool(envVar: String, value: Boolean) {
            env[envVar] = value.toString()
        }

        // LLM provider
        putIfSet("LEAN_AI_LLM_PROVIDER", s.llmProvider)

        // Ollama primary
        putIfSet("LEAN_AI_OLLAMA_URL", s.ollamaUrl)
        putIfSet("LEAN_AI_OLLAMA_MODEL", s.ollamaModel)
        putIfSet("LEAN_AI_OLLAMA_CONTEXT_WINDOW", s.ollamaContextWindow)
        putIfSet("LEAN_AI_OLLAMA_TEMPERATURE", s.ollamaTemperature)
        putIfSet("LEAN_AI_OLLAMA_TOP_P", s.ollamaTopP)
        putIfSet("LEAN_AI_OLLAMA_TOP_K", s.ollamaTopK)
        putIfSet("LEAN_AI_OLLAMA_REPEAT_PENALTY", s.ollamaRepeatPenalty)
        putIfSet("LEAN_AI_OLLAMA_MAX_TOKENS", s.ollamaMaxTokens)

        // Ollama expert
        putIfSet("LEAN_AI_OLLAMA_MODEL_EXPERT", s.ollamaModelExpert)
        putIfSet("LEAN_AI_OLLAMA_EXPERT_CONTEXT_WINDOW", s.ollamaExpertContextWindow)
        putIfSet("LEAN_AI_OLLAMA_EXPERT_TEMPERATURE", s.ollamaExpertTemperature)
        putIfSet("LEAN_AI_OLLAMA_EXPERT_TOP_P", s.ollamaExpertTopP)
        putIfSet("LEAN_AI_OLLAMA_EXPERT_TOP_K", s.ollamaExpertTopK)
        putIfSet("LEAN_AI_OLLAMA_EXPERT_REPEAT_PENALTY", s.ollamaExpertRepeatPenalty)
        putIfSet("LEAN_AI_OLLAMA_EXPERT_MAX_TOKENS", s.ollamaExpertMaxTokens)
        putIfSet("LEAN_AI_EXPERT_LLM_PROVIDER", s.expertLlmProvider)

        // Ollama request
        putIfSet("LEAN_AI_OLLAMA_MODEL_REQUEST", s.ollamaModelRequest)
        putIfSet("LEAN_AI_OLLAMA_REQUEST_CONTEXT_WINDOW", s.ollamaRequestContextWindow)
        putIfSet("LEAN_AI_OLLAMA_REQUEST_TEMPERATURE", s.ollamaRequestTemperature)
        putIfSet("LEAN_AI_OLLAMA_REQUEST_TOP_P", s.ollamaRequestTopP)
        putIfSet("LEAN_AI_OLLAMA_REQUEST_TOP_K", s.ollamaRequestTopK)
        putIfSet("LEAN_AI_OLLAMA_REQUEST_REPEAT_PENALTY", s.ollamaRequestRepeatPenalty)
        putIfSet("LEAN_AI_OLLAMA_REQUEST_MAX_TOKENS", s.ollamaRequestMaxTokens)
        putIfSet("LEAN_AI_REQUEST_LLM_PROVIDER", s.requestLlmProvider)

        // OpenAI
        putIfSet("LEAN_AI_OPENAI_MODEL", s.openaiModel)
        putIfSet("LEAN_AI_OPENAI_BASE_URL", s.openaiBaseUrl)
        putIfSet("LEAN_AI_OPENAI_TEMPERATURE", s.openaiTemperature)
        putIfSet("LEAN_AI_OPENAI_CONTEXT_WINDOW", s.openaiContextWindow)
        putIfSet("LEAN_AI_OPENAI_EXPERT_MODEL", s.openaiExpertModel)
        putIfSet("LEAN_AI_OPENAI_REQUEST_MODEL", s.openaiRequestModel)

        // Anthropic
        putIfSet("LEAN_AI_ANTHROPIC_MODEL", s.anthropicModel)
        putIfSet("LEAN_AI_ANTHROPIC_TEMPERATURE", s.anthropicTemperature)
        putIfSet("LEAN_AI_ANTHROPIC_CONTEXT_WINDOW", s.anthropicContextWindow)
        putIfSet("LEAN_AI_ANTHROPIC_EXPERT_MODEL", s.anthropicExpertModel)
        putIfSet("LEAN_AI_ANTHROPIC_REQUEST_MODEL", s.anthropicRequestModel)

        // Gemini
        putIfSet("LEAN_AI_GEMINI_MODEL", s.geminiModel)
        putIfSet("LEAN_AI_GEMINI_TEMPERATURE", s.geminiTemperature)
        putIfSet("LEAN_AI_GEMINI_CONTEXT_WINDOW", s.geminiContextWindow)
        putIfSet("LEAN_AI_GEMINI_EXPERT_MODEL", s.geminiExpertModel)
        putIfSet("LEAN_AI_GEMINI_REQUEST_MODEL", s.geminiRequestModel)

        // Lean AI Serve
        putIfSet("LEAN_AI_SERVE_URL", s.serveUrl)
        putIfSet("LEAN_AI_SERVE_MODEL", s.serveModel)
        putIfSet("LEAN_AI_SERVE_TEMPERATURE", s.serveTemperature)
        putIfSet("LEAN_AI_SERVE_CONTEXT_WINDOW", s.serveContextWindow)
        putIfSet("LEAN_AI_SERVE_MAX_TOKENS", s.serveMaxTokens)
        putIfSet("LEAN_AI_SERVE_EXPERT_MODEL", s.serveExpertModel)
        putIfSet("LEAN_AI_SERVE_REQUEST_MODEL", s.serveRequestModel)

        // Inline / embeddings
        putIfSet("LEAN_AI_INLINE_MODEL", s.inlineModel)
        putIfSet("LEAN_AI_INLINE_OLLAMA_URL", s.inlineOllamaUrl)
        putIfSet("LEAN_AI_EMBEDDING_MODEL", s.embeddingModel)
        putBool("LEAN_AI_ENABLE_EMBEDDINGS", s.enableEmbeddings)

        // Vision
        putIfSet("LEAN_AI_VISION_MODEL", s.visionModel)
        putIfSet("LEAN_AI_VISION_OLLAMA_URL", s.visionOllamaUrl)

        // Voice
        putBool("LEAN_AI_ENABLE_STT", s.enableStt)
        putIfSet("LEAN_AI_STT_MODEL", s.sttModel)
        putIfSet("LEAN_AI_STT_LANGUAGE", s.sttLanguage)
        putBool("LEAN_AI_ENABLE_TTS", s.enableTts)
        putIfSet("LEAN_AI_TTS_VOICE", s.ttsVoice)
        if (s.ttsSpeed != 1.0) env["LEAN_AI_TTS_SPEED"] = s.ttsSpeed.toString()
        if (s.ttsCpuThreads != 6) env["LEAN_AI_TTS_CPU_THREADS"] = s.ttsCpuThreads.toString()
        putBool("LEAN_AI_ENABLE_WAKE_WORD", s.enableWakeWord)

        // Search
        putIfSet("LEAN_AI_SEARCH_PROVIDER", s.searchProvider)
        putIfSet("LEAN_AI_SEARCH_API_URL", s.searchApiUrl)
        if (s.searchDelay != 2.0) env["LEAN_AI_SEARCH_DELAY"] = s.searchDelay.toString()

        // TDD
        putBool("LEAN_AI_ENABLE_TDD", s.enableTdd)

        // Post-validation
        putBool("LEAN_AI_ENABLE_POST_VALIDATION", s.enablePostValidation)
        putIfSet("LEAN_AI_POST_FORMAT_COMMAND", s.postFormatCommand)
        putIfSet("LEAN_AI_POST_LINT_FIX_COMMAND", s.postLintFixCommand)
        putIfSet("LEAN_AI_POST_LINT_COMMAND", s.postLintCommand)
        putIfSet("LEAN_AI_POST_TEST_COMMAND", s.postTestCommand)
        if (s.postValidationMaxRetries != 2) env["LEAN_AI_POST_VALIDATION_MAX_RETRIES"] = s.postValidationMaxRetries.toString()
        if (s.postValidationFixTurns != 30) env["LEAN_AI_POST_VALIDATION_FIX_TURNS"] = s.postValidationFixTurns.toString()

        // Integrations
        putBool("LEAN_AI_ENABLE_INTEGRATIONS", s.enableIntegrations)
        putBool("LEAN_AI_INTEGRATION_AUTO_PUSH", s.integrationAutoPush)
        putIfSet("LEAN_AI_JIRA_URL", s.jiraUrl)
        putIfSet("LEAN_AI_JIRA_EMAIL", s.jiraEmail)
        putIfSet("LEAN_AI_SERVICENOW_URL", s.servicenowUrl)
        putIfSet("LEAN_AI_SERVICENOW_USERNAME", s.servicenowUsername)
        putIfSet("LEAN_AI_SERVICENOW_TABLE", s.servicenowTable)
        putIfSet("LEAN_AI_WIKI_URL", s.wikiUrl)
        putIfSet("LEAN_AI_WIKI_API_PATH", s.wikiApiPath)
        putIfSet("LEAN_AI_WIKI_USERNAME", s.wikiUsername)

        // Advanced
        putBool("LEAN_AI_ENABLE_FRAMEWORK_GUIDE", s.enableFrameworkGuide)
        if (s.numParallel != 1) env["LEAN_AI_NUM_PARALLEL"] = s.numParallel.toString()
        if (s.implementationMaxTurns != 0) env["LEAN_AI_IMPLEMENTATION_MAX_TURNS"] = s.implementationMaxTurns.toString()
        if (s.refreshThreshold != 0.7) env["LEAN_AI_REFRESH_THRESHOLD"] = s.refreshThreshold.toString()
        putBool("LEAN_AI_DEBUG_PLANNING", s.debugPlanning)
        putBool("LEAN_AI_ENABLE_THINKING", s.enableThinking)
        putBool("LEAN_AI_ENABLE_THINKING_EXPERT", s.enableThinkingExpert)
        putBool("LEAN_AI_ENABLE_THINKING_REQUEST", s.enableThinkingRequest)

        return env
    }

    /** Secret key identifiers for PasswordSafe. */
    private enum class SecretKey(val service: String, val account: String, val envVar: String) {
        OPENAI_API_KEY("LeanAI", "openai_api_key", "LEAN_AI_OPENAI_API_KEY"),
        ANTHROPIC_API_KEY("LeanAI", "anthropic_api_key", "LEAN_AI_ANTHROPIC_API_KEY"),
        GEMINI_API_KEY("LeanAI", "gemini_api_key", "LEAN_AI_GEMINI_API_KEY"),
        SERVE_API_KEY("LeanAI", "serve_api_key", "LEAN_AI_SERVE_API_KEY"),
        JIRA_API_TOKEN("LeanAI", "jira_api_token", "LEAN_AI_JIRA_API_TOKEN"),
        SERVICENOW_PASSWORD("LeanAI", "servicenow_password", "LEAN_AI_SERVICENOW_PASSWORD"),
        WIKI_PASSWORD("LeanAI", "wiki_password", "LEAN_AI_WIKI_PASSWORD"),
    }

    /** Build full env including API keys from OS keychain. Use when spawning backend. */
    fun buildFullBackendEnv(): Map<String, String> {
        val env = buildBackendEnv().toMutableMap()
        val safe = PasswordSafe.instance

        for (key in SecretKey.entries) {
            val attrs = CredentialAttributes(generateServiceName(key.service, key.account))
            val password = safe.getPassword(attrs)
            if (!password.isNullOrEmpty()) {
                env[key.envVar] = password
            }
        }

        return env
    }

    /** Store a secret in PasswordSafe. */
    private fun storeSecret(key: SecretKey, value: String) {
        val attrs = CredentialAttributes(generateServiceName(key.service, key.account))
        PasswordSafe.instance.setPassword(attrs, value)
    }

    /** Retrieve a secret from PasswordSafe. */
    private fun getSecret(key: SecretKey): String? {
        val attrs = CredentialAttributes(generateServiceName(key.service, key.account))
        return PasswordSafe.instance.getPassword(attrs)
    }
}

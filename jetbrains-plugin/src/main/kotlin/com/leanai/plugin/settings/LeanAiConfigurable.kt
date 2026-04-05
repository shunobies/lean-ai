package com.leanai.plugin.settings

import com.intellij.openapi.options.Configurable
import com.intellij.ui.dsl.builder.*
import javax.swing.JComponent

/**
 * Settings UI page for Lean AI — appears at Settings > Tools > Lean AI.
 * Uses the Kotlin UI DSL for native JetBrains look and feel.
 *
 * This is the native Swing settings page. The JCEF-based settings panel
 * (SettingsToolWindowFactory) provides a richer UI matching the VSCode version.
 */
class LeanAiConfigurable : Configurable {
    private val settings get() = LeanAiSettings.getInstance()
    private var modified = false

    // Temporary state while editing (committed on apply)
    private var llmProvider = settings.state.llmProvider
    private var ollamaUrl = settings.state.ollamaUrl
    private var ollamaModel = settings.state.ollamaModel
    private var port = settings.state.port
    private var backendHost = settings.state.backendHost
    private var enableInlinePredictions = settings.state.enableInlinePredictions
    private var enablePostValidation = settings.state.enablePostValidation
    private var enableTdd = settings.state.enableTdd
    private var enableThinking = settings.state.enableThinking

    override fun getDisplayName(): String = "Lean AI"

    override fun createComponent(): JComponent {
        return panel {
            group("Connection") {
                row("Backend Host:") {
                    textField()
                        .bindText(::backendHost)
                        .columns(COLUMNS_MEDIUM)
                        .comment("Default: 127.0.0.1")
                }
                row("Backend Port:") {
                    intTextField(1024..65535)
                        .bindIntText(::port)
                        .comment("Default: 8422")
                }
            }

            group("LLM Provider") {
                row("Provider:") {
                    comboBox(listOf("ollama", "openai", "anthropic", "gemini", "serve"))
                        .bindItem(::llmProvider.toNullableProperty())
                        .comment("Select your LLM provider")
                }
            }

            group("Ollama") {
                row("Ollama URL:") {
                    textField()
                        .bindText(::ollamaUrl)
                        .columns(COLUMNS_LARGE)
                        .comment("Default: http://localhost:11434")
                }
                row("Model:") {
                    textField()
                        .bindText(::ollamaModel)
                        .columns(COLUMNS_MEDIUM)
                        .comment("Default: qwen3-coder:30b")
                }
            }

            group("Features") {
                row {
                    checkBox("Enable inline predictions")
                        .bindSelected(::enableInlinePredictions)
                }
                row {
                    checkBox("Enable post-validation (lint/test after execution)")
                        .bindSelected(::enablePostValidation)
                }
                row {
                    checkBox("Enable TDD mode")
                        .bindSelected(::enableTdd)
                        .comment("Expert writes tests first, primary implements")
                }
                row {
                    checkBox("Enable thinking mode")
                        .bindSelected(::enableThinking)
                        .comment("Pass think=True to Ollama for reasoning models")
                }
            }

            row {
                comment("For full settings including API keys, model configuration, voice, and integrations, " +
                        "use the Lean AI tool window's settings panel.")
            }
        }
    }

    override fun isModified(): Boolean {
        val s = settings.state
        return llmProvider != s.llmProvider ||
                ollamaUrl != s.ollamaUrl ||
                ollamaModel != s.ollamaModel ||
                port != s.port ||
                backendHost != s.backendHost ||
                enableInlinePredictions != s.enableInlinePredictions ||
                enablePostValidation != s.enablePostValidation ||
                enableTdd != s.enableTdd ||
                enableThinking != s.enableThinking
    }

    override fun apply() {
        val s = settings.state
        s.llmProvider = llmProvider
        s.ollamaUrl = ollamaUrl
        s.ollamaModel = ollamaModel
        s.port = port
        s.backendHost = backendHost
        s.enableInlinePredictions = enableInlinePredictions
        s.enablePostValidation = enablePostValidation
        s.enableTdd = enableTdd
        s.enableThinking = enableThinking
    }

    override fun reset() {
        val s = settings.state
        llmProvider = s.llmProvider
        ollamaUrl = s.ollamaUrl
        ollamaModel = s.ollamaModel
        port = s.port
        backendHost = s.backendHost
        enableInlinePredictions = s.enableInlinePredictions
        enablePostValidation = s.enablePostValidation
        enableTdd = s.enableTdd
        enableThinking = s.enableThinking
    }
}

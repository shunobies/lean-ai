package com.leanai.plugin.ui

import com.google.gson.Gson
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.project.Project
import com.intellij.ui.jcef.JBCefBrowser
import com.intellij.ui.jcef.JBCefBrowserBase
import com.intellij.ui.jcef.JBCefJSQuery
import com.leanai.plugin.backend.BackendClient
import com.leanai.plugin.backend.BackendProcess
import com.leanai.plugin.notifications.NotificationManager
import com.leanai.plugin.ws.WebSocketHandler
import com.leanai.plugin.ws.WsMessage
import org.cef.browser.CefBrowser
import org.cef.handler.CefLoadHandlerAdapter
import javax.swing.JComponent
import javax.swing.UIManager

/**
 * Bridge between JCEF (embedded Chromium) chat UI and Kotlin backend client.
 * Replaces VSCode's acquireVsCodeApi().postMessage() / window.addEventListener('message').
 *
 * Communication:
 *   JS → Kotlin: window.postToPlugin(jsonString) via JBCefJSQuery
 *   Kotlin → JS: window.receiveFromPlugin(jsonObject) via executeJavaScript
 */
class ChatBridge(private val project: Project) {
    private val log = Logger.getInstance(ChatBridge::class.java)
    private val gson = Gson()
    private val browser: JBCefBrowser = JBCefBrowser()
    private lateinit var jsQuery: JBCefJSQuery
    private var wsHandler: WebSocketHandler? = null
    private var currentSessionId: String? = null

    val component: JComponent get() = browser.component

    init {
        setupBridge()
        loadChatHtml()
    }

    /**
     * Set up the bidirectional JS ↔ Kotlin communication bridge.
     */
    private fun setupBridge() {
        jsQuery = JBCefJSQuery.create(browser as JBCefBrowserBase)

        // Handle messages from JS
        jsQuery.addHandler { request: String ->
            handleWebviewMessage(request)
            JBCefJSQuery.Response("ok")
        }

        // Inject the bridge function into JS once the page loads
        browser.jbCefClient.addLoadHandler(object : CefLoadHandlerAdapter() {
            override fun onLoadEnd(browser: CefBrowser?, frame: org.cef.browser.CefFrame?, httpStatusCode: Int) {
                if (frame?.isMain == true) {
                    // Inject postToPlugin function
                    val injectScript = """
                        window.postToPlugin = function(msg) {
                            ${jsQuery.inject("msg")}
                        };
                    """.trimIndent()
                    browser?.executeJavaScript(injectScript, "", 0)

                    // Inject theme colors from IDE
                    injectThemeColors()
                }
            }
        }, browser.cefBrowser)
    }

    /**
     * Load the chat HTML from plugin resources.
     */
    private fun loadChatHtml() {
        val htmlUrl = javaClass.getResource("/webview/chat.html")
        if (htmlUrl != null) {
            browser.loadURL(htmlUrl.toExternalForm())
        } else {
            // Fallback: load a minimal placeholder
            browser.loadHTML(PLACEHOLDER_HTML)
        }
    }

    /**
     * Route messages from the JS webview to appropriate Kotlin handlers.
     */
    private fun handleWebviewMessage(jsonStr: String) {
        try {
            val json = JsonParser.parseString(jsonStr).asJsonObject
            val command = json.get("command")?.asString ?: return

            when (command) {
                // Chat message sent by user
                "sendMessage" -> {
                    val text = json.get("text")?.asString ?: return
                    handleSendMessage(text)
                }
                // Slash command
                "slashCommand" -> {
                    val cmd = json.get("cmd")?.asString ?: return
                    val args = json.get("args")?.asString ?: ""
                    handleSlashCommand(cmd, args)
                }
                // Approve plan
                "approve" -> {
                    val feedback = json.get("feedback")?.asString
                    BackendClient.getInstance().wsSendApprove(feedback)
                }
                // Reject plan
                "reject" -> {
                    val feedback = json.get("feedback")?.asString ?: ""
                    BackendClient.getInstance().wsSendReject(feedback)
                }
                // Cancel workflow
                "cancel" -> {
                    BackendClient.getInstance().wsSendCancel()
                }
                // Approve tool (destructive command gate)
                "approveTool" -> {
                    val token = json.get("token")?.asString ?: return
                    BackendClient.getInstance().wsSendApproveTool(token)
                }
                // Deny tool
                "denyTool" -> {
                    val token = json.get("token")?.asString ?: return
                    BackendClient.getInstance().wsSendDenyTool(token)
                }
            }
        } catch (e: Exception) {
            log.error("Failed to handle webview message: ${e.message}")
        }
    }

    /**
     * Handle a regular chat message — route to /chat REST endpoint.
     */
    private fun handleSendMessage(text: String) {
        val workspacePath = project.basePath ?: return

        ApplicationManager.getApplication().executeOnPooledThread {
            val client = BackendClient.getInstance()
            val response = client.chat(
                BackendClient.ChatRequest(
                    workspace_path = workspacePath,
                    message = text
                ),
                onThinking = { content ->
                    sendToWebview("thinkingContent", mapOf("content" to content, "streaming" to true))
                },
                onContent = { content ->
                    sendToWebview("assistantContent", mapOf("content" to content, "done" to true))
                }
            )

            if (response?.suggested_agent_prompt != null) {
                sendToWebview("suggestedPrompt", mapOf("prompt" to response.suggested_agent_prompt))
            }
        }
    }

    /**
     * Handle slash commands — route to appropriate backend operations.
     */
    private fun handleSlashCommand(cmd: String, args: String) {
        val workspacePath = project.basePath ?: return
        val client = BackendClient.getInstance()

        when (cmd) {
            "init" -> {
                ApplicationManager.getApplication().executeOnPooledThread {
                    sendToWebview("statusMessage", mapOf("text" to "Indexing workspace..."))
                    val success = client.initWorkspace(
                        BackendClient.InitWorkspaceRequest(workspace_path = workspacePath)
                    ) { event ->
                        sendToWebview("statusMessage", mapOf("text" to event))
                    }
                    if (success) {
                        sendToWebview("statusMessage", mapOf("text" to "Workspace initialized."))
                    } else {
                        sendToWebview("errorMessage", mapOf("text" to "Failed to initialize workspace."))
                    }
                }
            }
            "agent", "fix", "request", "skill" -> {
                // Start a workflow session via WebSocket
                startWorkflow(cmd, args, workspacePath)
            }
            "approve" -> {
                client.wsSendApprove(args.ifEmpty { null })
            }
            "reject" -> {
                client.wsSendReject(args.ifEmpty { "No feedback provided" })
            }
            "reboot" -> {
                ApplicationManager.getApplication().executeOnPooledThread {
                    BackendProcess.getInstance().restart(project)
                    sendToWebview("statusMessage", mapOf("text" to "Backend restarted."))
                }
            }
            else -> {
                sendToWebview("errorMessage", mapOf("text" to "Unknown command: /$cmd"))
            }
        }
    }

    /**
     * Start a workflow session (agent, fix, request, or skill alias) via WebSocket.
     */
    private fun startWorkflow(mode: String, task: String, workspacePath: String) {
        ApplicationManager.getApplication().executeOnPooledThread {
            val client = BackendClient.getInstance()

            // Create session
            val wsMode = when (mode) {
                "fix" -> "fix"
                "request", "skill" -> "request"
                else -> "plan"
            }
            val session = client.createSession(
                BackendClient.CreateSessionRequest(
                    mode = wsMode,
                    repo_root = workspacePath,
                    task = task
                )
            )
            if (session == null) {
                sendToWebview("errorMessage", mapOf("text" to "Failed to create session."))
                return@executeOnPooledThread
            }

            currentSessionId = session.session_id

            // Set up WebSocket handler
            val handler = WebSocketHandler(createUIListener())
            wsHandler = handler

            // Connect WebSocket
            client.connectWebSocket(session.session_id, object : BackendClient.WsMessageListener {
                override fun onMessage(message: WsMessage) {
                    handler.handle(message)
                }
                override fun onConnected() {
                    // Send the initial user message to start the workflow
                    client.wsSendUserMessage(task, repoRoot = workspacePath)
                }
                override fun onDisconnected() {
                    sendToWebview("statusMessage", mapOf("text" to "Disconnected from backend."))
                }
                override fun onError(error: String) {
                    sendToWebview("errorMessage", mapOf("text" to error))
                }
            })
        }
    }

    /**
     * Create the WebSocket UI listener that forwards events to the JS webview.
     */
    private fun createUIListener(): WebSocketHandler.WebSocketUIListener {
        return object : WebSocketHandler.WebSocketUIListener {
            override fun onStageChanged(stage: String, status: String, summary: String?) {
                sendToWebview("stageChanged", mapOf("stage" to stage, "status" to status, "summary" to (summary ?: "")))
            }
            override fun onAssistantContent(content: String, streaming: Boolean, done: Boolean) {
                sendToWebview("assistantContent", mapOf("content" to content, "streaming" to streaming, "done" to done))
            }
            override fun onThinkingContent(content: String, streaming: Boolean, done: Boolean) {
                sendToWebview("thinkingContent", mapOf("content" to content, "streaming" to streaming, "done" to done))
            }
            override fun onClarificationNeeded(questions: List<String>, improvedPrompt: String?) {
                sendToWebview("clarificationNeeded", mapOf("questions" to questions, "improvedPrompt" to (improvedPrompt ?: "")))
            }
            override fun onApprovalRequired(plan: String, userSummary: String?, reviewFeedback: String?) {
                sendToWebview("approvalRequired", mapOf("plan" to plan, "userSummary" to (userSummary ?: ""), "reviewFeedback" to (reviewFeedback ?: "")))
                NotificationManager.notifyApprovalNeeded(project)
            }
            override fun onPlanRevision(revisionNumber: Int, reviewFeedback: String?) {
                sendToWebview("planRevision", mapOf("revisionNumber" to revisionNumber, "reviewFeedback" to (reviewFeedback ?: "")))
            }
            override fun onPlanRejected(feedback: String) {
                sendToWebview("planRejected", mapOf("feedback" to feedback))
            }
            override fun onToolProgress(tool: String, status: String, description: String?, output: String?) {
                sendToWebview("toolProgress", mapOf("tool" to tool, "status" to status, "description" to (description ?: ""), "output" to (output ?: "")))
            }
            override fun onToolApprovalRequired(tool: String, command: String, token: String, description: String?) {
                sendToWebview("toolApprovalRequired", mapOf("tool" to tool, "command" to command, "token" to token, "description" to (description ?: "")))
                NotificationManager.notifyToolApprovalNeeded(project, command)
            }
            override fun onDiff(filePath: String, diff: String) {
                sendToWebview("diff", mapOf("filePath" to filePath, "diff" to diff))
            }
            override fun onTestResult(passed: Boolean, output: String) {
                sendToWebview("testResult", mapOf("passed" to passed, "output" to output))
            }
            override fun onIndexStatus(status: String, progress: Float?) {
                sendToWebview("indexStatus", mapOf("status" to status, "progress" to (progress ?: 0f)))
            }
            override fun onBranchCreated(branchName: String) {
                sendToWebview("branchCreated", mapOf("branchName" to branchName))
            }
            override fun onCheckpoint(stepIndex: Int, description: String, status: String) {
                sendToWebview("checkpoint", mapOf("stepIndex" to stepIndex, "description" to description, "status" to status))
            }
            override fun onMergeComplete(mergeSha: String?, branchDeleted: Boolean) {
                sendToWebview("mergeComplete", mapOf("mergeSha" to (mergeSha ?: ""), "branchDeleted" to branchDeleted))
            }
            override fun onMetricsUpdate(contextPercent: Float, promptTokens: Int, contextWindow: Int) {
                sendToWebview("metricsUpdate", mapOf("contextPercent" to contextPercent, "promptTokens" to promptTokens, "contextWindow" to contextWindow))
            }
            override fun onContextRefreshed() {
                sendToWebview("contextRefreshed", emptyMap())
            }
            override fun onVisionDescription(descriptions: List<String>) {
                sendToWebview("visionDescription", mapOf("descriptions" to descriptions))
            }
            override fun onComplete(summary: String?, filesModified: List<String>, tokensPerSecond: Float?) {
                sendToWebview("complete", mapOf("summary" to (summary ?: ""), "filesModified" to filesModified, "tokensPerSecond" to (tokensPerSecond ?: 0f)))
                NotificationManager.notifyComplete(project, summary)
                BackendClient.getInstance().disconnectWebSocket()
            }
            override fun onCancelled() {
                sendToWebview("cancelled", emptyMap())
                BackendClient.getInstance().disconnectWebSocket()
            }
            override fun onError(message: String, recoverable: Boolean) {
                sendToWebview("error", mapOf("message" to message, "recoverable" to recoverable))
                if (!recoverable) {
                    BackendClient.getInstance().disconnectWebSocket()
                }
            }
        }
    }

    /**
     * Send a message from Kotlin to the JS webview.
     */
    private fun sendToWebview(type: String, data: Map<String, Any>) {
        val payload = JsonObject().apply {
            addProperty("type", type)
            for ((key, value) in data) {
                when (value) {
                    is String -> addProperty(key, value)
                    is Number -> addProperty(key, value)
                    is Boolean -> addProperty(key, value)
                    else -> add(key, gson.toJsonTree(value))
                }
            }
        }
        val escapedJson = payload.toString()
            .replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\n", "\\n")
            .replace("\r", "\\r")

        ApplicationManager.getApplication().invokeLater {
            browser.cefBrowser.executeJavaScript(
                "if (window.receiveFromPlugin) window.receiveFromPlugin('$escapedJson');",
                "", 0
            )
        }
    }

    /**
     * Inject IDE theme colors as CSS custom properties so the chat UI matches.
     */
    private fun injectThemeColors() {
        val bg = colorToHex(UIManager.getColor("Panel.background"))
        val fg = colorToHex(UIManager.getColor("Label.foreground"))
        val btnBg = colorToHex(UIManager.getColor("Button.startBackground") ?: UIManager.getColor("Button.background"))
        val btnFg = colorToHex(UIManager.getColor("Button.foreground"))
        val inputBg = colorToHex(UIManager.getColor("TextField.background"))
        val inputBorder = colorToHex(UIManager.getColor("Component.borderColor") ?: UIManager.getColor("TextField.borderColor"))
        val focusBorder = colorToHex(UIManager.getColor("Component.focusedBorderColor"))
        val errorFg = colorToHex(UIManager.getColor("Label.errorForeground") ?: java.awt.Color.RED)
        val linkFg = colorToHex(UIManager.getColor("Link.activeForeground") ?: UIManager.getColor("link.foreground"))

        val css = """
            document.documentElement.style.setProperty('--bg-primary', '$bg');
            document.documentElement.style.setProperty('--fg-primary', '$fg');
            document.documentElement.style.setProperty('--btn-bg', '$btnBg');
            document.documentElement.style.setProperty('--btn-fg', '$btnFg');
            document.documentElement.style.setProperty('--input-bg', '$inputBg');
            document.documentElement.style.setProperty('--input-border', '$inputBorder');
            document.documentElement.style.setProperty('--focus-border', '$focusBorder');
            document.documentElement.style.setProperty('--error-fg', '$errorFg');
            document.documentElement.style.setProperty('--link-fg', '$linkFg');
        """.trimIndent()

        browser.cefBrowser.executeJavaScript(css, "", 0)
    }

    private fun colorToHex(color: java.awt.Color?): String {
        if (color == null) return "#808080"
        return String.format("#%02x%02x%02x", color.red, color.green, color.blue)
    }

    companion object {
        private val PLACEHOLDER_HTML = """
            <!DOCTYPE html>
            <html>
            <head>
                <style>
                    body {
                        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                        display: flex; align-items: center; justify-content: center;
                        height: 100vh; margin: 0;
                        background: var(--bg-primary, #1e1e1e);
                        color: var(--fg-primary, #cccccc);
                    }
                    .loading { text-align: center; }
                    .spinner { font-size: 2em; animation: spin 1s linear infinite; display: inline-block; }
                    @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
                </style>
            </head>
            <body>
                <div class="loading">
                    <div class="spinner">&#x21BB;</div>
                    <p>Loading Lean AI Chat...</p>
                </div>
            </body>
            </html>
        """.trimIndent()
    }
}

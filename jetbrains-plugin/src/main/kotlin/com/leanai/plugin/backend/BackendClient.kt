package com.leanai.plugin.backend

import com.google.gson.Gson
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import com.google.gson.reflect.TypeToken
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.components.Service
import com.intellij.openapi.diagnostic.Logger
import com.leanai.plugin.ws.WsMessage
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/**
 * HTTP + WebSocket client for the Lean AI backend.
 * Port of extension/src/backendClient.ts (~1,900 lines).
 *
 * Application-level singleton; uses OkHttp for all network operations.
 */
@Service(Service.Level.APP)
class BackendClient {
    private val log = Logger.getInstance(BackendClient::class.java)
    private val gson = Gson()
    private val JSON_MEDIA = "application/json".toMediaType()

    /** Standard client with reasonable timeouts for REST calls. */
    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    /** Client with no read timeout for LLM-backed endpoints. */
    private val longPollClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.SECONDS)  // No timeout
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    /** Quick client for health checks and predictions. */
    private val quickClient = OkHttpClient.Builder()
        .connectTimeout(3, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .build()

    private var webSocket: WebSocket? = null
    private var wsListener: WsMessageListener? = null
    private val wsReconnectAttempts = AtomicInteger(0)
    private val wsConnected = AtomicBoolean(false)

    companion object {
        private const val MAX_RECONNECT_ATTEMPTS = 5
        private const val RECONNECT_BASE_DELAY_MS = 2000L
        private const val PING_INTERVAL_MS = 30_000L

        fun getInstance(): BackendClient =
            ApplicationManager.getApplication().getService(BackendClient::class.java)
    }

    private fun baseUrl(): String = BackendProcess.getInstance().getBaseUrl()
    private fun wsBaseUrl(): String = BackendProcess.getInstance().getWsUrl()

    // ── Health Check ──────────────────────────────────────────────────────��──

    fun healthCheck(): Boolean {
        return try {
            val request = Request.Builder().url("${baseUrl()}/api/health").build()
            quickClient.newCall(request).execute().use { it.isSuccessful }
        } catch (e: Exception) {
            false
        }
    }

    // ── Session Management ───────────────────────────────────────────────────

    data class CreateSessionRequest(
        val mode: String = "plan",
        val workspace_path: String? = null,
        val task: String? = null
    )

    data class SessionResponse(
        val session_id: String,
        val status: String? = null,
        val mode: String? = null,
        val task: String? = null,
        val plan_branch: String? = null,
        val base_branch: String? = null,
    )

    fun createSession(request: CreateSessionRequest): SessionResponse? {
        return post("/api/sessions", request, SessionResponse::class.java, useLongPoll = true)
    }

    fun getSession(sessionId: String): SessionResponse? {
        return get("/api/sessions/$sessionId", SessionResponse::class.java)
    }

    data class SessionListResponse(
        val sessions: List<SessionResponse>,
        val total: Int
    )

    fun listSessions(limit: Int = 50, offset: Int = 0): SessionListResponse? {
        return get("/api/sessions?limit=$limit&offset=$offset", SessionListResponse::class.java)
    }

    fun deleteSession(sessionId: String): Boolean {
        return try {
            val request = Request.Builder()
                .url("${baseUrl()}/api/sessions/$sessionId")
                .delete()
                .build()
            httpClient.newCall(request).execute().use { it.isSuccessful }
        } catch (e: Exception) {
            log.error("Failed to delete session $sessionId", e)
            false
        }
    }

    // ── Workspace Operations ─────────────────────────────────────────────────

    data class InitWorkspaceRequest(
        val workspace_path: String,
        val force_reindex: Boolean = false
    )

    fun initWorkspace(request: InitWorkspaceRequest, onEvent: ((String) -> Unit)? = null): Boolean {
        return try {
            val body = gson.toJson(request).toRequestBody(JSON_MEDIA)
            val httpRequest = Request.Builder()
                .url("${baseUrl()}/api/init-workspace")
                .post(body)
                .build()
            longPollClient.newCall(httpRequest).execute().use { response ->
                if (onEvent != null && response.isSuccessful) {
                    // Parse SSE events
                    response.body?.charStream()?.forEachLine { line ->
                        if (line.startsWith("data: ")) {
                            onEvent(line.removePrefix("data: "))
                        }
                    }
                }
                response.isSuccessful
            }
        } catch (e: Exception) {
            log.error("initWorkspace failed", e)
            false
        }
    }

    // ── Chat ─────────────────────────────────────────────────────────────────

    data class ChatRequest(
        val workspace_path: String,
        val message: String,
        val history: List<Map<String, String>>? = null
    )

    data class ChatResponse(
        val response: String? = null,
        val suggested_agent_prompt: String? = null
    )

    /**
     * Multi-turn chat with the request model. Supports SSE streaming.
     * @param onThinking callback for thinking tokens
     * @param onContent callback for content tokens
     */
    fun chat(
        request: ChatRequest,
        onThinking: ((String) -> Unit)? = null,
        onContent: ((String) -> Unit)? = null
    ): ChatResponse? {
        return try {
            val body = gson.toJson(request).toRequestBody(JSON_MEDIA)
            val httpRequest = Request.Builder()
                .url("${baseUrl()}/api/chat")
                .post(body)
                .build()
            longPollClient.newCall(httpRequest).execute().use { response ->
                if (!response.isSuccessful) return null

                var fullResponse = ""
                var suggestedPrompt: String? = null

                response.body?.charStream()?.forEachLine { line ->
                    if (line.startsWith("data: ")) {
                        val data = line.removePrefix("data: ")
                        try {
                            val json = JsonParser.parseString(data).asJsonObject
                            when (json.get("type")?.asString) {
                                "thinking" -> onThinking?.invoke(json.get("content")?.asString ?: "")
                                "result" -> {
                                    fullResponse = json.get("response")?.asString ?: ""
                                    suggestedPrompt = json.get("suggested_agent_prompt")?.asString
                                    onContent?.invoke(fullResponse)
                                }
                                else -> onContent?.invoke(json.get("content")?.asString ?: "")
                            }
                        } catch (_: Exception) {
                            onContent?.invoke(data)
                        }
                    }
                }
                ChatResponse(response = fullResponse, suggested_agent_prompt = suggestedPrompt)
            }
        } catch (e: Exception) {
            log.error("Chat request failed", e)
            null
        }
    }

    // ── Inline Predictions ───────────────────────────────────────────────────

    data class PredictRequest(
        val file_path: String,
        val language: String,
        val prefix: String,
        val suffix: String,
        val cursor_line: Int,
        val cursor_character: Int
    )

    data class PredictResponse(
        val completion: String?,
        val confidence: Float?,
        val error: String?
    )

    fun predict(request: PredictRequest): PredictResponse? {
        return post("/api/predict", request, PredictResponse::class.java, client = quickClient)
    }

    // ── Scaffold ─────────────────────────────────────────────────────────────

    data class ScaffoldRecipe(val name: String, val description: String)

    fun listScaffolds(): List<ScaffoldRecipe>? {
        return try {
            val request = Request.Builder()
                .url("${baseUrl()}/api/scaffold/list")
                .post("{}".toRequestBody(JSON_MEDIA))
                .build()
            httpClient.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return null
                val type = object : TypeToken<List<ScaffoldRecipe>>() {}.type
                gson.fromJson(response.body?.string(), type)
            }
        } catch (e: Exception) {
            log.error("listScaffolds failed", e)
            null
        }
    }

    // ── Session Management (approve, reject, merge, abandon) ─────────────────

    fun approveSession(sessionId: String, feedback: String? = null): Boolean {
        val payload = if (feedback != null) mapOf("feedback" to feedback) else emptyMap<String, String>()
        return postBool("/api/sessions/$sessionId/approve", payload)
    }

    fun rejectSession(sessionId: String, feedback: String): Boolean {
        return postBool("/api/sessions/$sessionId/reject", mapOf("feedback" to feedback))
    }

    fun mergeSession(sessionId: String): Boolean {
        return postBool("/api/sessions/$sessionId/merge", emptyMap<String, String>())
    }

    fun abandonSession(sessionId: String): Boolean {
        return postBool("/api/sessions/$sessionId/abandon", emptyMap<String, String>())
    }

    // ── WebSocket for Workflow Execution ──────────────────────────────────────

    interface WsMessageListener {
        fun onMessage(message: WsMessage)
        fun onConnected()
        fun onDisconnected()
        fun onError(error: String)
    }

    /**
     * Connect a WebSocket for workflow execution on the given session.
     */
    fun connectWebSocket(sessionId: String, listener: WsMessageListener) {
        wsListener = listener
        wsReconnectAttempts.set(0)
        doConnectWebSocket(sessionId)
    }

    private fun doConnectWebSocket(sessionId: String) {
        val url = "${wsBaseUrl()}/api/sessions/$sessionId/stream"
        val request = Request.Builder().url(url).build()

        webSocket = httpClient.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                log.info("WebSocket connected to $url")
                wsConnected.set(true)
                wsReconnectAttempts.set(0)
                wsListener?.onConnected()
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                val message = WsMessage.parse(text)
                if (message != null) {
                    wsListener?.onMessage(message)
                } else {
                    log.warn("Failed to parse WebSocket message: ${text.take(200)}")
                }
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                log.info("WebSocket closing: $code $reason")
                webSocket.close(1000, null)
                wsConnected.set(false)
                wsListener?.onDisconnected()
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                log.warn("WebSocket failure: ${t.message}")
                wsConnected.set(false)

                // Auto-reconnect
                val attempts = wsReconnectAttempts.incrementAndGet()
                if (attempts <= MAX_RECONNECT_ATTEMPTS) {
                    val delay = RECONNECT_BASE_DELAY_MS * attempts
                    log.info("Reconnecting in ${delay}ms (attempt $attempts/$MAX_RECONNECT_ATTEMPTS)")
                    Thread.sleep(delay)
                    doConnectWebSocket(sessionId)
                } else {
                    wsListener?.onError("WebSocket disconnected after $MAX_RECONNECT_ATTEMPTS attempts")
                }
            }
        })
    }

    /** Send a user message via WebSocket. */
    fun wsSendUserMessage(text: String, attachments: List<Map<String, Any>>? = null) {
        val payload = JsonObject().apply {
            addProperty("type", "user_message")
            addProperty("text", text)
            if (attachments != null) {
                add("attachments", gson.toJsonTree(attachments))
            }
        }
        webSocket?.send(payload.toString())
    }

    /** Send approval via WebSocket. */
    fun wsSendApprove(feedback: String? = null) {
        val payload = JsonObject().apply {
            addProperty("type", "approve")
            if (feedback != null) addProperty("feedback", feedback)
        }
        webSocket?.send(payload.toString())
    }

    /** Send rejection via WebSocket. */
    fun wsSendReject(feedback: String) {
        val payload = JsonObject().apply {
            addProperty("type", "reject")
            addProperty("feedback", feedback)
        }
        webSocket?.send(payload.toString())
    }

    /** Send cancel via WebSocket. */
    fun wsSendCancel() {
        webSocket?.send("""{"type":"cancel"}""")
    }

    /** Approve a tool execution (destructive command gate). */
    fun wsSendApproveTool(token: String) {
        val payload = JsonObject().apply {
            addProperty("type", "approve_tool")
            addProperty("token", token)
        }
        webSocket?.send(payload.toString())
    }

    /** Deny a tool execution. */
    fun wsSendDenyTool(token: String) {
        val payload = JsonObject().apply {
            addProperty("type", "deny_tool")
            addProperty("token", token)
        }
        webSocket?.send(payload.toString())
    }

    /** Send ping keepalive. */
    fun wsSendPing() {
        webSocket?.send("""{"type":"ping"}""")
    }

    /** Close the WebSocket connection. */
    fun disconnectWebSocket() {
        webSocket?.close(1000, "Client disconnect")
        webSocket = null
        wsConnected.set(false)
    }

    fun isWebSocketConnected(): Boolean = wsConnected.get()

    // ── HTTP Helpers ─────────────────────────────────────────────────────────

    private fun <T> get(path: String, responseType: Class<T>): T? {
        return try {
            val request = Request.Builder().url("${baseUrl()}$path").build()
            httpClient.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return null
                gson.fromJson(response.body?.string(), responseType)
            }
        } catch (e: Exception) {
            log.error("GET $path failed", e)
            null
        }
    }

    private fun <T> post(
        path: String,
        payload: Any,
        responseType: Class<T>,
        useLongPoll: Boolean = false,
        client: OkHttpClient? = null
    ): T? {
        return try {
            val body = gson.toJson(payload).toRequestBody(JSON_MEDIA)
            val request = Request.Builder().url("${baseUrl()}$path").post(body).build()
            val c = client ?: if (useLongPoll) longPollClient else httpClient
            c.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return null
                gson.fromJson(response.body?.string(), responseType)
            }
        } catch (e: Exception) {
            log.error("POST $path failed", e)
            null
        }
    }

    private fun postBool(path: String, payload: Any): Boolean {
        return try {
            val body = gson.toJson(payload).toRequestBody(JSON_MEDIA)
            val request = Request.Builder().url("${baseUrl()}$path").post(body).build()
            httpClient.newCall(request).execute().use { it.isSuccessful }
        } catch (e: Exception) {
            log.error("POST $path failed", e)
            false
        }
    }
}

/**
 * HTTP + WebSocket client for communicating with the Lean AI Python backend.
 *
 * Core infrastructure (singleton, HTTP helpers, session CRUD, health check,
 * WebSocket) lives here.  Domain-specific methods delegate to:
 *   - `./backendVoiceClient`     — voice / STT / TTS / wake word
 *   - `./backendWorkspaceClient` — workspace ops, scaffolding, prompts, notes, chat, predict
 */

import * as http from "http";
import * as https from "https";
import { URL } from "url";

import * as vscode from "vscode";
import WebSocket from "ws";
import { DEFAULT_BACKEND_URL, WS_RECONNECT_DELAY_MS, WS_MAX_RECONNECT_ATTEMPTS } from "./constants";
import type {
    CheckpointSummary,
    CreateSessionResponse,
    FileTouchSummary,
    GitEventSummary,
    InlinePredictionContext,
    MessageResponse,
    PredictionResult,
    SessionFilters,
    SessionState,
    SessionSummary,
    WSMessage,
} from "./types";

// Voice
import {
    sttWarmup as _sttWarmup,
    sttStart as _sttStart,
    sttStop as _sttStop,
    ttsSynthesize as _ttsSynthesize,
    ttsStream as _ttsStream,
    ttsStreamPcm as _ttsStreamPcm,
    listVoices as _listVoices,
    ensureTtsModels as _ensureTtsModels,
    voiceConfig as _voiceConfig,
    wakeWordStart as _wakeWordStart,
    wakeWordStop as _wakeWordStop,
    connectVoiceEvents as _connectVoiceEvents,
    disconnectVoiceEvents as _disconnectVoiceEvents,
    createVoiceEventState,
    type VoiceEventState,
} from "./backendVoiceClient";

// Workspace
import {
    indexWorkspace as _indexWorkspace,
    generateProjectContext as _generateProjectContext,
    generateStyleGuide as _generateStyleGuide,
    listScaffolds as _listScaffolds,
    scaffold as _scaffold,
    getPrompts as _getPrompts,
    updatePrompts as _updatePrompts,
    resetPrompts as _resetPrompts,
    createNote as _createNote,
    listNotes as _listNotes,
    getNote as _getNote,
    updateNote as _updateNote,
    deleteNote as _deleteNote,
    searchNotes as _searchNotes,
    listNoteProjects as _listNoteProjects,
    updateTodo as _updateTodo,
    deleteTodo as _deleteTodo,
    addTodo as _addTodo,
    chat as _chat,
    chatStream as _chatStream,
    predict as _predict,
} from "./backendWorkspaceClient";

export class BackendClient {
    private static instance: BackendClient | undefined;

    private baseUrl: string;
    private wsBaseUrl: string;

    /** Mutable state for the voice-events SSE connection. */
    private _voiceState: VoiceEventState;

    private constructor() {
        this.baseUrl = this.getBackendUrl();
        this.wsBaseUrl = this.baseUrl.replace(/^http/, "ws");
        this._voiceState = createVoiceEventState();
    }

    static getInstance(): BackendClient {
        if (!BackendClient.instance) {
            BackendClient.instance = new BackendClient();
        }
        return BackendClient.instance;
    }

    private getBackendUrl(): string {
        const config = vscode.workspace.getConfiguration("lean-ai");
        return config.get<string>("backendUrl") || DEFAULT_BACKEND_URL;
    }

    // -----------------------------------------------------------------------
    // Low-level HTTP helpers (used by workspace delegates)
    // -----------------------------------------------------------------------

    /**
     * POST JSON to the backend with NO timeout.
     *
     * Node.js `fetch` (undici) has a hardcoded 5-minute `headersTimeout`
     * that cannot be overridden via the fetch API.  For LLM-backed
     * endpoints (chat, project-context generation) the backend may take
     * 10+ minutes to respond with a large local model.  This helper uses
     * the raw `http`/`https` module with `socket.setTimeout(0)` so the
     * connection stays open indefinitely.
     */
    _postJsonNoTimeout(path: string, body: unknown): Promise<unknown> {
        return new Promise((resolve, reject) => {
            const fullUrl = new URL(`${this.baseUrl}${path}`);
            const isHttps = fullUrl.protocol === "https:";
            const transport = isHttps ? https : http;

            const postData = JSON.stringify(body);

            const options: http.RequestOptions = {
                hostname: fullUrl.hostname,
                port: fullUrl.port || (isHttps ? "443" : "80"),
                path: fullUrl.pathname,
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "Content-Length": Buffer.byteLength(postData),
                },
                timeout: 0,
            };

            const req = transport.request(options, (res) => {
                let data = "";
                let streamError: Error | null = null;

                res.on("data", (chunk: Buffer | string) => {
                    data += chunk.toString();
                });

                res.on("error", (err: Error) => {
                    // Capture the error -- 'end' may still fire after this.
                    // If 'end' never fires, we reject in the fallback below.
                    streamError = err;
                    reject(new Error(`Response stream error: ${err.message}`));
                });

                res.on("end", () => {
                    if (streamError) {
                        // Already rejected via the error handler above.
                        return;
                    }
                    if (
                        res.statusCode &&
                        res.statusCode >= 200 &&
                        res.statusCode < 300
                    ) {
                        try {
                            resolve(JSON.parse(data));
                        } catch {
                            reject(
                                new Error(
                                    `Invalid JSON response: ${data.substring(0, 200)}`,
                                ),
                            );
                        }
                    } else {
                        reject(
                            new Error(
                                `HTTP ${res.statusCode}: ${res.statusMessage}`,
                            ),
                        );
                    }
                });
            });

            // Disable all socket-level timeouts
            req.on("socket", (socket) => {
                socket.setTimeout(0);
            });

            req.on("error", (err) => {
                reject(err);
            });

            req.write(postData);
            req.end();
        });
    }

    /**
     * POST JSON to the backend and consume an SSE (text/event-stream) response.
     *
     * Thinking tokens are forwarded to *onThinking* as they arrive.
     * Resolves with the payload of the ``result`` SSE event.
     * Rejects on ``error`` events or HTTP-level failures.
     */
    _postSseNoTimeout(
        path: string,
        body: unknown,
        onThinking?: (token: string) => void,
    ): Promise<unknown> {
        return new Promise((resolve, reject) => {
            const fullUrl = new URL(`${this.baseUrl}${path}`);
            const isHttps = fullUrl.protocol === "https:";
            const transport = isHttps ? https : http;

            const postData = JSON.stringify(body);

            const options: http.RequestOptions = {
                hostname: fullUrl.hostname,
                port: fullUrl.port || (isHttps ? "443" : "80"),
                path: fullUrl.pathname,
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "Content-Length": Buffer.byteLength(postData),
                },
                timeout: 0,
            };

            let buffer = "";
            let resolved = false;

            const req = transport.request(options, (res) => {
                if (res.statusCode && (res.statusCode < 200 || res.statusCode >= 300)) {
                    reject(new Error(`HTTP ${res.statusCode}: ${res.statusMessage}`));
                    return;
                }

                res.on("data", (chunk: Buffer | string) => {
                    buffer += chunk.toString();
                    const lines = buffer.split("\n");
                    buffer = lines.pop()!; // keep incomplete last line

                    for (const line of lines) {
                        if (!line.startsWith("data: ")) { continue; }
                        try {
                            const data = JSON.parse(line.slice(6)) as Record<string, unknown>;
                            if (data["type"] === "thinking" && data["content"] && onThinking) {
                                onThinking(data["content"] as string);
                            } else if (data["type"] === "result") {
                                resolved = true;
                                resolve(data);
                            } else if (data["type"] === "error") {
                                resolved = true;
                                const status = data["status"] as number | undefined;
                                reject(new Error(
                                    status
                                        ? `HTTP ${status}: ${data["message"] as string}`
                                        : (data["message"] as string) || "Stream error",
                                ));
                            }
                            // "done" is ignored -- we resolve/reject on result/error
                        } catch {
                            // skip malformed SSE lines
                        }
                    }
                });

                res.on("end", () => {
                    if (!resolved) {
                        reject(new Error("SSE stream ended without result event"));
                    }
                });

                res.on("error", (err: Error) => {
                    reject(new Error(`Response stream error: ${err.message}`));
                });
            });

            req.on("socket", (socket) => { socket.setTimeout(0); });
            req.on("error", (err) => { reject(err); });
            req.write(postData);
            req.end();
        });
    }

    // -----------------------------------------------------------------------
    // Session CRUD
    // -----------------------------------------------------------------------

    async createSession(repoRoot: string): Promise<CreateSessionResponse> {
        const resp = await fetch(`${this.baseUrl}/api/sessions`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ repo_root: repoRoot }),
        });
        if (!resp.ok) {
            throw new Error(`Failed to create session: ${resp.statusText}`);
        }
        return resp.json() as Promise<CreateSessionResponse>;
    }

    async getSession(sessionId: string, repoRoot?: string): Promise<SessionState> {
        const params = repoRoot ? `?${new URLSearchParams({ repo_root: repoRoot })}` : "";
        const resp = await fetch(`${this.baseUrl}/api/sessions/${sessionId}${params}`);
        if (!resp.ok) {
            throw new Error(`Failed to get session: ${resp.statusText}`);
        }
        return resp.json() as Promise<SessionState>;
    }

    async sendMessage(sessionId: string, content: string): Promise<MessageResponse> {
        const resp = await fetch(`${this.baseUrl}/api/sessions/${sessionId}/message`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content }),
        });
        if (!resp.ok) {
            throw new Error(`Failed to send message: ${resp.statusText}`);
        }
        return resp.json() as Promise<MessageResponse>;
    }

    async approve(sessionId: string): Promise<void> {
        const resp = await fetch(`${this.baseUrl}/api/sessions/${sessionId}/approve`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ decision: "APPROVED" }),
        });
        if (!resp.ok) {
            throw new Error(`Failed to approve: ${resp.statusText}`);
        }
    }

    async reject(sessionId: string, feedback: string): Promise<void> {
        const resp = await fetch(`${this.baseUrl}/api/sessions/${sessionId}/approve`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ decision: "REJECTED", feedback }),
        });
        if (!resp.ok) {
            throw new Error(`Failed to reject: ${resp.statusText}`);
        }
    }

    // -----------------------------------------------------------------------
    // Session History
    // -----------------------------------------------------------------------

    async listSessions(filters?: SessionFilters, limit?: number, offset?: number): Promise<SessionSummary[]> {
        const params = new URLSearchParams();
        if (filters?.repo_root) { params.set("repo_root", filters.repo_root); }
        if (filters?.status) { params.set("status", filters.status); }
        if (filters?.branch) { params.set("branch", filters.branch); }
        if (filters?.since) { params.set("since", filters.since); }
        if (filters?.until) { params.set("until", filters.until); }
        if (limit !== undefined) { params.set("limit", String(limit)); }
        if (offset !== undefined) { params.set("offset", String(offset)); }
        const qs = params.toString();
        const url = `${this.baseUrl}/api/sessions${qs ? `?${qs}` : ""}`;
        const resp = await fetch(url);
        if (!resp.ok) {
            throw new Error(`Failed to list sessions: ${resp.statusText}`);
        }
        return resp.json() as Promise<SessionSummary[]>;
    }

    async updateSessionTitle(sessionId: string, title: string): Promise<void> {
        const resp = await fetch(`${this.baseUrl}/api/sessions/${sessionId}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title }),
        });
        if (!resp.ok) {
            throw new Error(`Failed to update session title: ${resp.statusText}`);
        }
    }

    async listCheckpoints(sessionId: string): Promise<CheckpointSummary[]> {
        const resp = await fetch(`${this.baseUrl}/api/sessions/${sessionId}/checkpoints`);
        if (!resp.ok) {
            throw new Error(`Failed to list checkpoints: ${resp.statusText}`);
        }
        return resp.json() as Promise<CheckpointSummary[]>;
    }

    async getCheckpointResumeContext(sessionId: string, checkpointId: string): Promise<Record<string, unknown>> {
        const resp = await fetch(`${this.baseUrl}/api/sessions/${sessionId}/checkpoints/${checkpointId}`);
        if (!resp.ok) {
            throw new Error(`Failed to get checkpoint: ${resp.statusText}`);
        }
        return resp.json() as Promise<Record<string, unknown>>;
    }

    async listGitEvents(sessionId: string): Promise<GitEventSummary[]> {
        const resp = await fetch(`${this.baseUrl}/api/sessions/${sessionId}/git-events`);
        if (!resp.ok) {
            throw new Error(`Failed to list git events: ${resp.statusText}`);
        }
        return resp.json() as Promise<GitEventSummary[]>;
    }

    async traceCommit(sha: string): Promise<Record<string, unknown> | null> {
        const resp = await fetch(`${this.baseUrl}/api/traceability/commit/${sha}`);
        if (!resp.ok) {
            if (resp.status === 404) { return null; }
            throw new Error(`Failed to trace commit: ${resp.statusText}`);
        }
        return resp.json() as Promise<Record<string, unknown>>;
    }

    async traceFile(filePath: string): Promise<FileTouchSummary[]> {
        const encoded = encodeURIComponent(filePath);
        const resp = await fetch(`${this.baseUrl}/api/traceability/file/${encoded}`);
        if (!resp.ok) {
            throw new Error(`Failed to trace file: ${resp.statusText}`);
        }
        return resp.json() as Promise<FileTouchSummary[]>;
    }

    async resumeSession(sessionId: string, repoRoot: string): Promise<{
        status: string;
        session_id: string;
        branch_name: string;
        scratchpad_exists: boolean;
    }> {
        const resp = await fetch(`${this.baseUrl}/api/sessions/${sessionId}/resume`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ repo_root: repoRoot }),
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText })) as { detail?: string };
            throw new Error(err.detail ?? resp.statusText);
        }
        return resp.json() as Promise<{
            status: string;
            session_id: string;
            branch_name: string;
            scratchpad_exists: boolean;
        }>;
    }

    async searchSessions(repoRoot: string, query: string): Promise<SessionSummary[]> {
        const params = new URLSearchParams({ repo_root: repoRoot, q: query });
        const resp = await fetch(`${this.baseUrl}/api/sessions/search?${params}`);
        if (!resp.ok) {
            throw new Error(`Failed to search sessions: ${resp.statusText}`);
        }
        return resp.json() as Promise<SessionSummary[]>;
    }

    async mergeSession(sessionId: string, repoRoot: string): Promise<Record<string, unknown>> {
        const params = new URLSearchParams({ repo_root: repoRoot });
        const resp = await fetch(`${this.baseUrl}/api/sessions/${sessionId}/merge?${params}`, {
            method: "POST",
        });
        if (!resp.ok) {
            throw new Error(`Failed to merge session: ${resp.statusText}`);
        }
        return resp.json() as Promise<Record<string, unknown>>;
    }

    async abandonSession(sessionId: string, repoRoot: string): Promise<Record<string, unknown>> {
        const params = new URLSearchParams({ repo_root: repoRoot });
        const resp = await fetch(`${this.baseUrl}/api/sessions/${sessionId}/abandon?${params}`, {
            method: "POST",
        });
        if (!resp.ok) {
            throw new Error(`Failed to abandon session: ${resp.statusText}`);
        }
        return resp.json() as Promise<Record<string, unknown>>;
    }

    async getLatestRejectableSession(repoRoot: string): Promise<string | undefined> {
        const params = new URLSearchParams({ repo_root: repoRoot });
        const resp = await fetch(`${this.baseUrl}/api/sessions?${params}`);
        if (!resp.ok) { return undefined; }
        const sessions = await resp.json() as Array<Record<string, unknown>>;
        const rejectable = sessions.find(
            (s) =>
                s.plan_branch &&
                ["completed", "cancelled", "active"].includes(s.session_status as string),
        );
        return rejectable?.session_id as string | undefined;
    }

    async deleteSession(sessionId: string, repoRoot: string): Promise<void> {
        const params = new URLSearchParams({ repo_root: repoRoot });
        const resp = await fetch(`${this.baseUrl}/api/sessions/${sessionId}?${params}`, {
            method: "DELETE",
        });
        if (!resp.ok) {
            throw new Error(`Failed to delete session: ${resp.statusText}`);
        }
    }

    async getConversationLog(
        sessionId: string,
        repoRoot: string,
    ): Promise<{
        session_id: string;
        entries: Array<{
            role: string;
            content: string;
            tool_name: string | null;
            tool_args: string | null;
            created_at: string;
        }>;
    }> {
        const params = new URLSearchParams({ repo_root: repoRoot });
        const resp = await fetch(`${this.baseUrl}/api/sessions/${sessionId}/conversation?${params}`);
        if (!resp.ok) {
            throw new Error(`Failed to get conversation log: ${resp.statusText}`);
        }
        return resp.json() as Promise<{
            session_id: string;
            entries: Array<{
                role: string;
                content: string;
                tool_name: string | null;
                tool_args: string | null;
                created_at: string;
            }>;
        }>;
    }

    // -----------------------------------------------------------------------
    // Health Check
    // -----------------------------------------------------------------------

    /** Last known vision capability from the backend. */
    visionAvailable = false;

    /** Last known voice capabilities from the backend. */
    sttAvailable = false;
    ttsAvailable = false;
    wakeWordAvailable = false;

    async healthCheck(): Promise<boolean> {
        try {
            const resp = await fetch(`${this.baseUrl}/api/health`);
            if (resp.ok) {
                try {
                    const data = await resp.json() as {
                        vision_available?: boolean;
                        stt_available?: boolean;
                        tts_available?: boolean;
                        wake_word_available?: boolean;
                    };
                    this.visionAvailable = !!data.vision_available;
                    this.sttAvailable = !!data.stt_available;
                    this.ttsAvailable = !!data.tts_available;
                    this.wakeWordAvailable = !!data.wake_word_available;
                } catch {
                    // Older backends may not return JSON
                }
            }
            return resp.ok;
        } catch {
            return false;
        }
    }

    // -----------------------------------------------------------------------
    // Workspace delegates  (→ backendWorkspaceClient)
    // -----------------------------------------------------------------------

    indexWorkspace(repoRoot: string, forceReindex = false) {
        return _indexWorkspace(this.baseUrl, repoRoot, forceReindex);
    }

    generateProjectContext(repoRoot: string, force = false, onThinking?: (token: string) => void) {
        return _generateProjectContext(
            repoRoot, force,
            (p, b) => this._postJsonNoTimeout(p, b),
            (p, b, cb) => this._postSseNoTimeout(p, b, cb),
            onThinking,
        );
    }

    generateStyleGuide(repoRoot: string, force = false, onThinking?: (token: string) => void) {
        return _generateStyleGuide(
            repoRoot, force,
            (p, b) => this._postJsonNoTimeout(p, b),
            (p, b, cb) => this._postSseNoTimeout(p, b, cb),
            onThinking,
        );
    }

    listScaffolds() { return _listScaffolds(this.baseUrl); }

    scaffold(scaffoldName: string, projectName: string, parentDir: string) {
        return _scaffold(this.baseUrl, scaffoldName, projectName, parentDir);
    }

    getPrompts(repoRoot: string) { return _getPrompts(this.baseUrl, repoRoot); }
    updatePrompts(repoRoot: string, overrides: Record<string, string>) { return _updatePrompts(this.baseUrl, repoRoot, overrides); }
    resetPrompts(repoRoot: string, keys?: string[]) { return _resetPrompts(this.baseUrl, repoRoot, keys); }

    createNote(content: string, sourceWorkspace?: string) { return _createNote(this.baseUrl, content, sourceWorkspace); }
    listNotes(project?: string) { return _listNotes(this.baseUrl, project); }
    getNote(noteId: string) { return _getNote(this.baseUrl, noteId); }
    updateNote(noteId: string, data: { content?: string; project?: string; tags?: string[] }) { return _updateNote(this.baseUrl, noteId, data); }
    deleteNote(noteId: string) { return _deleteNote(this.baseUrl, noteId); }
    searchNotes(query: string) { return _searchNotes(this.baseUrl, query); }
    listNoteProjects() { return _listNoteProjects(this.baseUrl); }
    updateTodo(todoId: number, data: { description?: string; completed?: boolean }) { return _updateTodo(this.baseUrl, todoId, data); }
    deleteTodo(todoId: number) { return _deleteTodo(this.baseUrl, todoId); }
    addTodo(noteId: string, description: string) { return _addTodo(this.baseUrl, noteId, description); }

    chat(
        message: string,
        history: Array<{ role: string; content: string }>,
        workspace?: {
            workspace_name?: string;
            workspace_root?: string;
            active_file?: string;
            active_language?: string;
            active_selection?: string;
        },
        userName?: string,
    ) {
        return _chat((p, b) => this._postJsonNoTimeout(p, b), message, history, workspace, userName);
    }

    chatStream(
        message: string,
        history: Array<{ role: string; content: string }>,
        workspace: {
            workspace_name?: string;
            workspace_root?: string;
            active_file?: string;
            active_language?: string;
            active_selection?: string;
        } | undefined,
        onToken: (token: string, isFirst: boolean) => void,
        attachments?: Array<{ data: string; filename?: string; mime_type?: string }>,
        onThinking?: (token: string) => void,
        userName?: string,
        skipWebSearch?: boolean,
        onVisionDescription?: (desc: string) => void,
        onToolCall?: (name: string, description: string) => void,
        onToolResult?: (name: string, success: boolean) => void,
    ) {
        return _chatStream(
            this.baseUrl, message, history, workspace, onToken,
            attachments, onThinking, userName, skipWebSearch,
            onVisionDescription, onToolCall, onToolResult,
        );
    }

    predict(context: InlinePredictionContext) { return _predict(this.baseUrl, context); }

    // -----------------------------------------------------------------------
    // Voice delegates  (→ backendVoiceClient)
    // -----------------------------------------------------------------------

    sttWarmup() { return _sttWarmup(this.baseUrl); }
    sttStart(autoStop = false) { return _sttStart(this.baseUrl, autoStop); }
    sttStop() { return _sttStop(this.baseUrl); }

    ttsSynthesize(text: string, voice?: string, speed?: number, signal?: AbortSignal) {
        return _ttsSynthesize(this.baseUrl, text, voice, speed, signal);
    }
    ttsStream(
        text: string,
        voice: string | undefined,
        speed: number | undefined,
        onChunk: (base64: string) => void,
        signal?: AbortSignal,
    ) {
        return _ttsStream(this.baseUrl, text, voice, speed, onChunk, signal);
    }
    ttsStreamPcm(
        text: string,
        voice: string | undefined,
        speed: number | undefined,
        onChunk: (pcmBase64: string, sampleRate: number) => void,
        signal?: AbortSignal,
    ) {
        return _ttsStreamPcm(this.baseUrl, text, voice, speed, onChunk, signal);
    }

    wakeWordStart() { return _wakeWordStart(this.baseUrl); }
    wakeWordStop() { return _wakeWordStop(this.baseUrl); }

    listVoices() { return _listVoices(this.baseUrl); }
    ensureTtsModels() { return _ensureTtsModels(this.baseUrl); }
    voiceConfig(voice?: string, speed?: number) { return _voiceConfig(this.baseUrl, voice, speed); }

    connectVoiceEvents(
        onWakeWord: () => void,
        onSttAutoStop?: () => void,
        onError?: () => void,
    ) {
        _connectVoiceEvents(this.baseUrl, this._voiceState, onWakeWord, onSttAutoStop, onError);
    }

    disconnectVoiceEvents() {
        _disconnectVoiceEvents(this._voiceState);
    }

    // -----------------------------------------------------------------------
    // WebSocket
    // -----------------------------------------------------------------------

    connectWebSocket(
        sessionId: string,
        onMessage: (msg: WSMessage) => void,
        onError?: (err: Error) => void,
        onClose?: () => void,
    ): WebSocket {
        const url = `${this.wsBaseUrl}/api/sessions/${sessionId}/stream`;
        const ws = new WebSocket(url);

        ws.on("message", (data: WebSocket.Data) => {
            try {
                const msg = JSON.parse(data.toString()) as WSMessage;
                onMessage(msg);
            } catch (e) {
                console.error("Failed to parse WebSocket message:", e);
            }
        });

        ws.on("error", (err: Error) => {
            console.error("WebSocket error:", err);
            onError?.(err);
        });

        ws.on("close", () => {
            onClose?.();
        });

        return ws;
    }
}

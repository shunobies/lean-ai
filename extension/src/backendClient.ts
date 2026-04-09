/**
 * HTTP + WebSocket client for communicating with the Lean AI Python backend.
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

export class BackendClient {
    private static instance: BackendClient | undefined;

    private baseUrl: string;
    private wsBaseUrl: string;

    private constructor() {
        this.baseUrl = this.getBackendUrl();
        this.wsBaseUrl = this.baseUrl.replace(/^http/, "ws");
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
    private _postJsonNoTimeout(path: string, body: unknown): Promise<unknown> {
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
                    // Capture the error — 'end' may still fire after this.
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
    private _postSseNoTimeout(
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
                            // "done" is ignored — we resolve/reject on result/error
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

    // --- REST Methods ---

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

    async predict(context: InlinePredictionContext): Promise<PredictionResult> {
        const resp = await fetch(`${this.baseUrl}/api/predict`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(context),
        });
        if (!resp.ok) {
            return { completion: "", confidence: 0 };
        }
        return resp.json() as Promise<PredictionResult>;
    }

    async chat(
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
    ): Promise<{ reply: string; tokens_per_second?: number | null; eval_count?: number | null }> {
        const body: Record<string, unknown> = { message, history };
        if (workspace) {
            body.workspace = workspace;
        }
        if (userName) { body.user_name = userName; }
        // Uses http module — fetch (undici) has a hardcoded 5-min timeout
        // that kills long-running LLM calls with large local models.
        const data = (await this._postJsonNoTimeout("/api/chat", body)) as {
            reply: string;
            tokens_per_second?: number | null;
            eval_count?: number | null;
        };
        return data;
    }

    /**
     * Stream chat tokens from the backend via Server-Sent Events.
     *
     * Uses the raw http/https module (same as _postJsonNoTimeout) to avoid
     * Node/undici's hardcoded 5-minute headersTimeout.
     *
     * @param onToken  Called for each token as it arrives. `isFirst` is true
     *                 only for the very first token in the response.
     * @returns        Promise that resolves with `{ receivedDone }` when the stream ends.
     */
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
    ): Promise<{ receivedDone: boolean }> {
        return new Promise((resolve, reject) => {
            const fullUrl = new URL(`${this.baseUrl}/api/chat/stream`);
            const isHttps = fullUrl.protocol === "https:";
            const transport = isHttps ? https : http;

            const body: Record<string, unknown> = { message, history };
            if (workspace) { body.workspace = workspace; }
            if (attachments && attachments.length > 0) { body.attachments = attachments; }
            if (userName) { body.user_name = userName; }
            if (skipWebSearch) { body.skip_web_search = true; }
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
            let isFirst = true;
            let resolved = false;

            const req = transport.request(options, (res) => {
                if (res.statusCode && (res.statusCode < 200 || res.statusCode >= 300)) {
                    reject(new Error(`HTTP ${res.statusCode}: ${res.statusMessage}`));
                    return;
                }

                res.on("data", (chunk: Buffer | string) => {
                    buffer += chunk.toString();
                    const lines = buffer.split("\n");
                    buffer = lines.pop()!; // keep incomplete last line in buffer

                    for (const line of lines) {
                        if (!line.startsWith("data: ")) { continue; }
                        try {
                            const data = JSON.parse(line.slice(6)) as Record<string, unknown>;
                            if (data["type"] === "token" && data["content"]) {
                                onToken(data["content"] as string, isFirst);
                                isFirst = false;
                            } else if (data["type"] === "thinking" && data["content"] && onThinking) {
                                onThinking(data["content"] as string);
                            } else if (data["type"] === "vision_description" && data["descriptions"] && onVisionDescription) {
                                onVisionDescription(data["descriptions"] as string);
                            } else if (data["type"] === "tool_call" && data["name"] && onToolCall) {
                                onToolCall(data["name"] as string, (data["description"] as string) || "");
                            } else if (data["type"] === "tool_result" && data["name"] && onToolResult) {
                                onToolResult(data["name"] as string, data["success"] as boolean);
                            } else if (data["type"] === "done") {
                                resolved = true;
                                resolve({ receivedDone: true });
                            } else if (data["type"] === "error") {
                                reject(new Error((data["message"] as string) || "Stream error"));
                            }
                        } catch {
                            // skip malformed SSE lines
                        }
                    }
                });

                res.on("end", () => {
                    if (!resolved) {
                        console.warn("[Lean AI] Chat stream ended without 'done' event — response may be truncated");
                        resolve({ receivedDone: false });
                    }
                });

                res.on("error", (err) => { reject(err); });
            });

            req.on("socket", (socket) => { socket.setTimeout(0); });
            req.on("error", (err) => { reject(err); });
            req.write(postData);
            req.end();
        });
    }

    // --- Session History Methods ---

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

    /**
     * Find the most recent session that can be rejected/approved (has a branch,
     * status is completed/cancelled/active). Used as fallback when the extension
     * lost track of the session ID.
     */
    async getLatestRejectableSession(repoRoot: string): Promise<string | undefined> {
        const params = new URLSearchParams({ repo_root: repoRoot });
        const resp = await fetch(`${this.baseUrl}/api/sessions?${params}`);
        if (!resp.ok) { return undefined; }
        const sessions = await resp.json() as Array<Record<string, unknown>>;
        // Sessions are returned newest-first. Find the first with a branch
        // in a rejectable state.
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
        return resp.json() as Promise<ReturnType<typeof this.getConversationLog> extends Promise<infer T> ? T : never>;
    }

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

    // --- Init Workspace ---

    async indexWorkspace(
        repoRoot: string,
        forceReindex = false,
    ): Promise<{
        index_status: string;
        index_file_count?: number;
        index_chunk_count?: number;
        num_parallel?: number;
    }> {
        // 60s timeout — indexing is local file I/O, should be fast
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 60_000);
        try {
            const resp = await fetch(`${this.baseUrl}/api/init-workspace`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    repo_root: repoRoot,
                    force_reindex: forceReindex,
                }),
                signal: controller.signal,
            });
            if (!resp.ok) {
                throw new Error(`Index workspace failed: ${resp.statusText}`);
            }
            return resp.json() as Promise<{
                index_status: string;
                index_file_count?: number;
                index_chunk_count?: number;
                num_parallel?: number;
            }>;
        } finally {
            clearTimeout(timeout);
        }
    }

    async generateProjectContext(
        repoRoot: string,
        force: boolean = false,
        onThinking?: (token: string) => void,
    ): Promise<{ path: string; chars: number; skipped?: boolean }> {
        const body = { repo_root: repoRoot, skip_if_exists: !force, stream: !!onThinking };
        if (onThinking) {
            return (await this._postSseNoTimeout(
                "/api/generate-project-context", body, onThinking,
            )) as { path: string; chars: number; skipped?: boolean };
        }
        return (await this._postJsonNoTimeout(
            "/api/generate-project-context", body,
        )) as { path: string; chars: number; skipped?: boolean };
    }

    async generateStyleGuide(
        repoRoot: string,
        force: boolean = false,
        onThinking?: (token: string) => void,
    ): Promise<{ path: string; chars: number; skipped?: boolean }> {
        const body = { repo_root: repoRoot, skip_if_exists: !force, stream: !!onThinking };
        if (onThinking) {
            return (await this._postSseNoTimeout(
                "/api/generate-style-guide", body, onThinking,
            )) as { path: string; chars: number; skipped?: boolean };
        }
        return (await this._postJsonNoTimeout(
            "/api/generate-style-guide", body,
        )) as { path: string; chars: number; skipped?: boolean };
    }

    async listScaffolds(): Promise<{
        scaffolds: Array<{
            name: string;
            display_name: string;
            description: string;
            language: string;
            framework: string | null;
            aliases: string[];
            setup_type: string;
        }>;
    }> {
        const resp = await fetch(`${this.baseUrl}/api/scaffold/list`);
        if (!resp.ok) {
            throw new Error(`List scaffolds failed: ${resp.statusText}`);
        }
        return resp.json() as Promise<ReturnType<typeof this.listScaffolds> extends Promise<infer T> ? T : never>;
    }

    async scaffold(
        scaffoldName: string,
        projectName: string,
        parentDir: string,
    ): Promise<{
        scaffold_name: string;
        project_dir: string;
        files_created: string[];
        command_output: string;
        message: string;
    }> {
        const resp = await fetch(`${this.baseUrl}/api/scaffold`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                scaffold_name: scaffoldName,
                project_name: projectName,
                parent_dir: parentDir,
            }),
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText })) as { detail?: string };
            throw new Error(err.detail ?? resp.statusText);
        }
        return resp.json() as Promise<ReturnType<typeof this.scaffold> extends Promise<infer T> ? T : never>;
    }

    // --- Voice ---

    async sttWarmup(): Promise<void> {
        try {
            await fetch(`${this.baseUrl}/api/voice/stt/warmup`, { method: "POST" });
        } catch { /* fire-and-forget */ }
    }

    async sttStart(autoStop = false): Promise<void> {
        const resp = await fetch(`${this.baseUrl}/api/voice/stt/start`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ auto_stop: autoStop }),
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText })) as { detail?: string };
            throw new Error(err.detail ?? resp.statusText);
        }
    }

    async sttStop(): Promise<{ text: string; language?: string; duration_seconds: number }> {
        const resp = await fetch(`${this.baseUrl}/api/voice/stt/stop`, {
            method: "POST",
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText })) as { detail?: string };
            throw new Error(err.detail ?? resp.statusText);
        }
        return resp.json() as Promise<{ text: string; language?: string; duration_seconds: number }>;
    }

    async ttsSynthesize(
        text: string,
        voice?: string,
        speed?: number,
        signal?: AbortSignal,
    ): Promise<{ audio_base64: string; duration_seconds: number }> {
        const resp = await fetch(`${this.baseUrl}/api/voice/tts`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text, voice: voice || "", speed: speed || 0 }),
            signal,
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText })) as { detail?: string };
            throw new Error(err.detail ?? resp.statusText);
        }
        return resp.json() as Promise<{ audio_base64: string; duration_seconds: number }>;
    }

    async ttsStream(
        text: string,
        voice: string | undefined,
        speed: number | undefined,
        onChunk: (base64: string) => void,
        signal?: AbortSignal,
    ): Promise<void> {
        const resp = await fetch(`${this.baseUrl}/api/voice/tts/stream`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text, voice: voice || "", speed: speed || 0 }),
            signal,
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText })) as { detail?: string };
            throw new Error(err.detail ?? resp.statusText);
        }
        const reader = resp.body?.getReader();
        if (!reader) { return; }
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
            const { done, value } = await reader.read();
            if (done) { break; }
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop() || "";
            for (const line of lines) {
                if (line.startsWith("data: ")) {
                    try {
                        const data = JSON.parse(line.slice(6)) as { type?: string; audio_base64?: string };
                        if (data.type === "done") { return; }
                        if (data.audio_base64) { onChunk(data.audio_base64); }
                    } catch { /* skip malformed */ }
                }
            }
        }
    }

    async ttsStreamPcm(
        text: string,
        voice: string | undefined,
        speed: number | undefined,
        onChunk: (pcmBase64: string, sampleRate: number) => void,
        signal?: AbortSignal,
    ): Promise<void> {
        const resp = await fetch(`${this.baseUrl}/api/voice/tts/stream-pcm`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text, voice: voice || "", speed: speed || 0 }),
            signal,
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText })) as { detail?: string };
            throw new Error(err.detail ?? resp.statusText);
        }
        const reader = resp.body?.getReader();
        if (!reader) { return; }
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
            const { done, value } = await reader.read();
            if (done) { break; }
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop() || "";
            for (const line of lines) {
                if (line.startsWith("data: ")) {
                    try {
                        const data = JSON.parse(line.slice(6)) as {
                            type?: string; pcm_base64?: string; sample_rate?: number;
                        };
                        if (data.type === "done") { return; }
                        if (data.pcm_base64 && data.sample_rate) {
                            onChunk(data.pcm_base64, data.sample_rate);
                        }
                    } catch { /* skip malformed */ }
                }
            }
        }
    }

    async wakeWordStart(): Promise<void> {
        const resp = await fetch(`${this.baseUrl}/api/voice/wakeword/start`, {
            method: "POST",
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText })) as { detail?: string };
            throw new Error(err.detail ?? resp.statusText);
        }
    }

    async wakeWordStop(): Promise<void> {
        const resp = await fetch(`${this.baseUrl}/api/voice/wakeword/stop`, {
            method: "POST",
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText })) as { detail?: string };
            throw new Error(err.detail ?? resp.statusText);
        }
    }

    async listVoices(): Promise<Array<{ id: string; name: string; language: string; gender?: string }>> {
        const resp = await fetch(`${this.baseUrl}/api/voice/tts/voices`);
        if (!resp.ok) { return []; }
        const data = await resp.json() as { voices: Array<{ id: string; name: string; language: string; gender?: string }> };
        return data.voices || [];
    }

    async ensureTtsModels(): Promise<{ downloaded: boolean; size_mb: number }> {
        const resp = await fetch(`${this.baseUrl}/api/voice/tts/ensure-models`, {
            method: "POST",
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText })) as { detail?: string };
            throw new Error(err.detail ?? resp.statusText);
        }
        return resp.json() as Promise<{ downloaded: boolean; size_mb: number }>;
    }

    async voiceConfig(voice?: string, speed?: number): Promise<void> {
        await fetch(`${this.baseUrl}/api/voice/config`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ voice: voice || "", speed: speed || 0 }),
        });
    }

    /**
     * SSE connection for wake word events.
     *
     * Uses http.request (not fetch) to avoid Node/undici's hardcoded
     * 5-minute headersTimeout — wake word may sit idle for long periods.
     * Auto-reconnects on connection loss.
     */
    private _voiceEventReq: http.ClientRequest | null = null;
    private _voiceReconnectTimer: ReturnType<typeof setTimeout> | null = null;

    connectVoiceEvents(
        onWakeWord: () => void,
        onSttAutoStop?: () => void,
        onError?: () => void,
    ): void {
        this.disconnectVoiceEvents();

        const connect = () => {
            const fullUrl = new URL(`${this.baseUrl}/api/voice/events`);
            const isHttps = fullUrl.protocol === "https:";
            const transport = isHttps ? https : http;

            const options: http.RequestOptions = {
                hostname: fullUrl.hostname,
                port: fullUrl.port || (isHttps ? "443" : "80"),
                path: fullUrl.pathname,
                method: "GET",
                timeout: 0,
            };

            let buffer = "";

            const req = transport.request(options, (res) => {
                if (res.statusCode && (res.statusCode < 200 || res.statusCode >= 300)) {
                    console.error(`[Lean AI] Voice events SSE: HTTP ${res.statusCode}`);
                    scheduleReconnect();
                    return;
                }

                console.log("[Lean AI] Voice events SSE: connected");

                res.on("data", (chunk: Buffer | string) => {
                    buffer += chunk.toString();
                    const lines = buffer.split("\n");
                    buffer = lines.pop()!;

                    for (const line of lines) {
                        if (line.startsWith(":") || line === "") { continue; }
                        if (!line.startsWith("data: ")) { continue; }
                        try {
                            const data = JSON.parse(line.slice(6)) as { type?: string };
                            if (data.type === "wake_word_detected") {
                                onWakeWord();
                            } else if (data.type === "stt_auto_stopped" && onSttAutoStop) {
                                onSttAutoStop();
                            } else if (data.type === "wake_word_error" && onError) {
                                onError();
                            }
                        } catch { /* skip malformed SSE lines */ }
                    }
                });

                res.on("end", () => {
                    console.warn("[Lean AI] Voice events SSE: connection ended, reconnecting...");
                    scheduleReconnect();
                });

                res.on("error", (err) => {
                    console.error("[Lean AI] Voice events SSE: stream error:", err.message);
                    scheduleReconnect();
                });
            });

            req.on("socket", (socket) => { socket.setTimeout(0); });
            req.on("error", (err) => {
                console.error("[Lean AI] Voice events SSE: request error:", err.message);
                scheduleReconnect();
            });
            req.end();

            this._voiceEventReq = req;
        };

        const scheduleReconnect = () => {
            if (this._voiceEventReq === null && this._voiceReconnectTimer === null) { return; }
            this._voiceEventReq = null;
            this._voiceReconnectTimer = setTimeout(() => {
                this._voiceReconnectTimer = null;
                connect();
            }, 3000);
        };

        connect();
    }

    disconnectVoiceEvents(): void {
        if (this._voiceReconnectTimer !== null) {
            clearTimeout(this._voiceReconnectTimer);
            this._voiceReconnectTimer = null;
        }
        const req = this._voiceEventReq;
        this._voiceEventReq = null;
        if (req) {
            req.destroy();
        }
    }

    // --- Prompts ---

    async getPrompts(repoRoot: string): Promise<{ prompts: unknown[]; categories: string[] }> {
        const params = new URLSearchParams({ repo_root: repoRoot });
        const resp = await fetch(`${this.baseUrl}/api/prompts?${params}`);
        if (!resp.ok) {
            throw new Error(`Failed to load prompts: ${resp.statusText}`);
        }
        return resp.json() as Promise<{ prompts: unknown[]; categories: string[] }>;
    }

    async updatePrompts(repoRoot: string, overrides: Record<string, string>): Promise<void> {
        const resp = await fetch(`${this.baseUrl}/api/prompts`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ repo_root: repoRoot, overrides }),
        });
        if (!resp.ok) {
            const body = await resp.text();
            throw new Error(`Failed to save prompts: ${resp.statusText} — ${body}`);
        }
    }

    async resetPrompts(repoRoot: string, keys?: string[]): Promise<void> {
        const resp = await fetch(`${this.baseUrl}/api/prompts/reset`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ repo_root: repoRoot, keys: keys ?? null }),
        });
        if (!resp.ok) {
            throw new Error(`Failed to reset prompts: ${resp.statusText}`);
        }
    }

    // --- Notes ---

    async createNote(
        content: string,
        sourceWorkspace?: string,
    ): Promise<{ id: string; content: string; project: string | null; tags: string[] }> {
        const resp = await fetch(`${this.baseUrl}/api/notes`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content, source_workspace: sourceWorkspace || null }),
        });
        if (!resp.ok) {
            throw new Error(`Failed to create note: ${resp.statusText}`);
        }
        return resp.json() as Promise<{ id: string; content: string; project: string | null; tags: string[] }>;
    }

    async listNotes(project?: string): Promise<unknown[]> {
        const params = new URLSearchParams();
        if (project) { params.set("project", project); }
        const qs = params.toString();
        const resp = await fetch(`${this.baseUrl}/api/notes${qs ? `?${qs}` : ""}`);
        if (!resp.ok) {
            throw new Error(`Failed to list notes: ${resp.statusText}`);
        }
        return resp.json() as Promise<unknown[]>;
    }

    async getNote(noteId: string): Promise<unknown> {
        const resp = await fetch(`${this.baseUrl}/api/notes/${noteId}`);
        if (!resp.ok) {
            throw new Error(`Failed to get note: ${resp.statusText}`);
        }
        return resp.json();
    }

    async updateNote(
        noteId: string,
        data: { content?: string; project?: string; tags?: string[] },
    ): Promise<unknown> {
        const resp = await fetch(`${this.baseUrl}/api/notes/${noteId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
        });
        if (!resp.ok) {
            throw new Error(`Failed to update note: ${resp.statusText}`);
        }
        return resp.json();
    }

    async deleteNote(noteId: string): Promise<void> {
        const resp = await fetch(`${this.baseUrl}/api/notes/${noteId}`, {
            method: "DELETE",
        });
        if (!resp.ok) {
            throw new Error(`Failed to delete note: ${resp.statusText}`);
        }
    }

    async searchNotes(query: string): Promise<unknown[]> {
        const params = new URLSearchParams({ q: query });
        const resp = await fetch(`${this.baseUrl}/api/notes/search?${params}`);
        if (!resp.ok) {
            throw new Error(`Failed to search notes: ${resp.statusText}`);
        }
        return resp.json() as Promise<unknown[]>;
    }

    async listNoteProjects(): Promise<string[]> {
        const resp = await fetch(`${this.baseUrl}/api/notes/projects`);
        if (!resp.ok) {
            throw new Error(`Failed to list projects: ${resp.statusText}`);
        }
        return resp.json() as Promise<string[]>;
    }

    async updateTodo(
        todoId: number,
        data: { description?: string; completed?: boolean },
    ): Promise<unknown> {
        const resp = await fetch(`${this.baseUrl}/api/notes/todos/${todoId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
        });
        if (!resp.ok) {
            throw new Error(`Failed to update todo: ${resp.statusText}`);
        }
        return resp.json();
    }

    async deleteTodo(todoId: number): Promise<void> {
        const resp = await fetch(`${this.baseUrl}/api/notes/todos/${todoId}`, {
            method: "DELETE",
        });
        if (!resp.ok) {
            throw new Error(`Failed to delete todo: ${resp.statusText}`);
        }
    }

    async addTodo(noteId: string, description: string): Promise<unknown> {
        const resp = await fetch(`${this.baseUrl}/api/notes/${noteId}/todos`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ description }),
        });
        if (!resp.ok) {
            throw new Error(`Failed to add todo: ${resp.statusText}`);
        }
        return resp.json();
    }

    // --- WebSocket ---

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

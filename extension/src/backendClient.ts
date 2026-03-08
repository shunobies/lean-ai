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
                res.on("data", (chunk: Buffer | string) => {
                    data += chunk.toString();
                });
                res.on("end", () => {
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
    ): Promise<{ reply: string; tokens_per_second?: number | null; eval_count?: number | null }> {
        const body: Record<string, unknown> = { message, history };
        if (workspace) {
            body.workspace = workspace;
        }
        // Uses http module — fetch (undici) has a hardcoded 5-min timeout
        // that kills long-running LLM calls with large local models.
        const data = (await this._postJsonNoTimeout("/api/chat", body)) as {
            reply: string;
            tokens_per_second?: number | null;
            eval_count?: number | null;
        };
        return data;
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

    async healthCheck(): Promise<boolean> {
        try {
            const resp = await fetch(`${this.baseUrl}/api/health`);
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
            return resp.json();
        } finally {
            clearTimeout(timeout);
        }
    }

    async generateProjectContext(
        repoRoot: string,
        force: boolean = false,
    ): Promise<{ path: string; chars: number; skipped?: boolean }> {
        // Uses http module — fetch (undici) has a hardcoded 5-min timeout
        // that kills long-running LLM calls with large local models.
        // The caller shows a "working..." indicator while waiting.
        // skip_if_exists=true by default; pass force=true on /init --force
        // to always regenerate even when the file already exists.
        return (await this._postJsonNoTimeout(
            "/api/generate-project-context",
            { repo_root: repoRoot, skip_if_exists: !force },
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

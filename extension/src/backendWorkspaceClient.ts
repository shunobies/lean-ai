/**
 * Workspace operation client functions.
 *
 * Standalone functions for workspace indexing, project context generation,
 * scaffolding, prompts, notes, chat, and predictions.  Each receives
 * `baseUrl` (and any other needed params) so they can be called from the
 * main BackendClient without coupling.
 */

import * as http from "http";
import * as https from "https";
import { URL } from "url";

import type { InlinePredictionContext, PredictionResult } from "./types";

// Re-usable type aliases for the two low-level HTTP helpers that live in
// BackendClient.  The workspace functions that need them accept the helpers
// as parameters rather than importing them, keeping the dependency one-way.
export type PostJsonFn = (path: string, body: unknown) => Promise<unknown>;
export type PostSseFn = (
    path: string,
    body: unknown,
    onThinking?: (token: string) => void,
    onProgress?: (event: Record<string, unknown>) => void,
) => Promise<unknown>;

// ---------------------------------------------------------------------------
// Workspace Indexing
// ---------------------------------------------------------------------------

export interface IndexWorkspaceResponse {
    index_status: string;
    index_file_count?: number;
    index_chunk_count?: number;
    num_parallel?: number;
    reference_status?: string;
    reference_doc_count?: number;
    reference_chunk_count?: number;
    reference_skipped_extensions?: string[];
    // embedding_status: "skipped" | "success" | "up_to_date" | "partial" | "failed"
    embedding_status?: string;
    embedding_code_count?: number;
    embedding_reference_count?: number;
    embedding_code_unchanged?: number;
    embedding_reference_unchanged?: number;
    embedding_failed_batches?: number;
    embedding_total_batches?: number;
    embedding_message?: string;
}

export async function indexWorkspace(
    postJson: PostJsonFn,
    repoRoot: string,
    forceReindex = false,
): Promise<IndexWorkspaceResponse> {
    // No timeout — embedding generation can take minutes for large repos.
    // Uses PostJsonFn which goes through _postJsonNoTimeout (no timeout).
    return (await postJson("/api/init-workspace", {
        repo_root: repoRoot,
        force_reindex: forceReindex,
    })) as IndexWorkspaceResponse;
}

// ---------------------------------------------------------------------------
// Project Context / Style Guide
// ---------------------------------------------------------------------------

export async function generateProjectContext(
    repoRoot: string,
    force: boolean,
    postJson: PostJsonFn,
    postSse: PostSseFn,
    onThinking?: (token: string) => void,
    onProgress?: (event: Record<string, unknown>) => void,
): Promise<{ path: string; chars: number; skipped?: boolean }> {
    const body = { repo_root: repoRoot, skip_if_exists: !force, stream: !!(onThinking || onProgress) };
    if (onThinking || onProgress) {
        return (await postSse(
            "/api/generate-project-context", body, onThinking, onProgress,
        )) as { path: string; chars: number; skipped?: boolean };
    }
    return (await postJson(
        "/api/generate-project-context", body,
    )) as { path: string; chars: number; skipped?: boolean };
}

export async function generateStyleGuide(
    repoRoot: string,
    force: boolean,
    postJson: PostJsonFn,
    postSse: PostSseFn,
    onThinking?: (token: string) => void,
): Promise<{ path: string; chars: number; skipped?: boolean }> {
    const body = { repo_root: repoRoot, skip_if_exists: !force, stream: !!onThinking };
    if (onThinking) {
        return (await postSse(
            "/api/generate-style-guide", body, onThinking,
        )) as { path: string; chars: number; skipped?: boolean };
    }
    return (await postJson(
        "/api/generate-style-guide", body,
    )) as { path: string; chars: number; skipped?: boolean };
}

// ---------------------------------------------------------------------------
// Word document conversion
// ---------------------------------------------------------------------------

export interface ConvertDocxResponse {
    success: boolean;
    output_path: string;
    content_length: number;
    line_count: number;
}

/**
 * Error thrown when the backend rejects a convert-docx call because the
 * output file already exists and ``overwrite`` was not set.  The caller
 * can catch this specifically to prompt the user.
 */
export class DocxOutputExistsError extends Error {
    readonly outputPath: string;
    constructor(outputPath: string, message: string) {
        super(message);
        this.name = "DocxOutputExistsError";
        this.outputPath = outputPath;
    }
}

export async function convertDocx(
    baseUrl: string,
    repoRoot: string,
    sourcePath: string,
    outputFilename: string,
    overwrite = false,
): Promise<ConvertDocxResponse> {
    const resp = await fetch(`${baseUrl}/api/workspace/convert-docx`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            repo_root: repoRoot,
            source_path: sourcePath,
            output_filename: outputFilename,
            overwrite,
        }),
    });

    if (resp.status === 409) {
        const payload = (await resp.json().catch(() => ({}))) as {
            detail?: { output_path?: string; message?: string };
        };
        const detail = payload.detail ?? {};
        throw new DocxOutputExistsError(
            detail.output_path ?? outputFilename,
            detail.message ?? `${outputFilename} already exists.`,
        );
    }

    if (!resp.ok) {
        const body = await resp.text().catch(() => "");
        throw new Error(`convert-docx failed (${resp.status}): ${body || resp.statusText}`);
    }

    return (await resp.json()) as ConvertDocxResponse;
}

// ---------------------------------------------------------------------------
// Application logging
// ---------------------------------------------------------------------------

export interface LogAppliedResponse {
    success: boolean;
    tracker_updated: boolean;
    tracker_path: string | null;
    commit_attempted: boolean;
    commit_sha: string | null;
    commit_error: string | null;
}

export async function logApplied(
    baseUrl: string,
    repoRoot: string,
    slug: string,
    company: string,
    role: string,
    source = "other",
    nextAction = "Follow up in 7 days",
): Promise<LogAppliedResponse> {
    const resp = await fetch(`${baseUrl}/api/workspace/log-applied`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            repo_root: repoRoot,
            slug,
            company,
            role,
            source,
            next_action: nextAction,
        }),
    });
    if (!resp.ok) {
        const body = await resp.text().catch(() => "");
        throw new Error(`log-applied failed (${resp.status}): ${body || resp.statusText}`);
    }
    return (await resp.json()) as LogAppliedResponse;
}

// ---------------------------------------------------------------------------
// Scaffolding
// ---------------------------------------------------------------------------

export async function listScaffolds(baseUrl: string): Promise<{
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
    const resp = await fetch(`${baseUrl}/api/scaffold/list`);
    if (!resp.ok) {
        throw new Error(`List scaffolds failed: ${resp.statusText}`);
    }
    return resp.json() as Promise<{
        scaffolds: Array<{
            name: string;
            display_name: string;
            description: string;
            language: string;
            framework: string | null;
            aliases: string[];
            setup_type: string;
        }>;
    }>;
}

export async function scaffold(
    baseUrl: string,
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
    const resp = await fetch(`${baseUrl}/api/scaffold`, {
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
    return resp.json() as Promise<{
        scaffold_name: string;
        project_dir: string;
        files_created: string[];
        command_output: string;
        message: string;
    }>;
}

// ---------------------------------------------------------------------------
// Prompts
// ---------------------------------------------------------------------------

export async function getPrompts(baseUrl: string, repoRoot: string): Promise<{ prompts: unknown[]; categories: string[] }> {
    const params = new URLSearchParams({ repo_root: repoRoot });
    const resp = await fetch(`${baseUrl}/api/prompts?${params}`);
    if (!resp.ok) {
        throw new Error(`Failed to load prompts: ${resp.statusText}`);
    }
    return resp.json() as Promise<{ prompts: unknown[]; categories: string[] }>;
}

export async function updatePrompts(baseUrl: string, repoRoot: string, overrides: Record<string, string>): Promise<void> {
    const resp = await fetch(`${baseUrl}/api/prompts`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo_root: repoRoot, overrides }),
    });
    if (!resp.ok) {
        const body = await resp.text();
        throw new Error(`Failed to save prompts: ${resp.statusText} — ${body}`);
    }
}

export async function resetPrompts(baseUrl: string, repoRoot: string, keys?: string[]): Promise<void> {
    const resp = await fetch(`${baseUrl}/api/prompts/reset`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo_root: repoRoot, keys: keys ?? null }),
    });
    if (!resp.ok) {
        throw new Error(`Failed to reset prompts: ${resp.statusText}`);
    }
}

export interface RoleTuningPrewarmResult {
    role: string;
    model_id: string;
    status: "tuned" | "skipped";
    profile_path: string;
    prompts_path: string;
    warning?: string | null;
}

export interface RoleTuningPrewarmResponse {
    results: RoleTuningPrewarmResult[];
}

export async function prewarmRoleTuning(
    postJson: PostJsonFn,
    repoRoot: string,
): Promise<RoleTuningPrewarmResponse> {
    return (await postJson("/api/role-tuning/prewarm", {
        repo_root: repoRoot,
    })) as RoleTuningPrewarmResponse;
}

// ---------------------------------------------------------------------------
// Notes
// ---------------------------------------------------------------------------

export async function createNote(
    baseUrl: string,
    content: string,
    sourceWorkspace?: string,
): Promise<{ id: string; content: string; project: string | null; tags: string[] }> {
    const resp = await fetch(`${baseUrl}/api/notes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, source_workspace: sourceWorkspace || null }),
    });
    if (!resp.ok) {
        throw new Error(`Failed to create note: ${resp.statusText}`);
    }
    return resp.json() as Promise<{ id: string; content: string; project: string | null; tags: string[] }>;
}

export async function listNotes(baseUrl: string, project?: string): Promise<unknown[]> {
    const params = new URLSearchParams();
    if (project) { params.set("project", project); }
    const qs = params.toString();
    const resp = await fetch(`${baseUrl}/api/notes${qs ? `?${qs}` : ""}`);
    if (!resp.ok) {
        throw new Error(`Failed to list notes: ${resp.statusText}`);
    }
    return resp.json() as Promise<unknown[]>;
}

export async function getNote(baseUrl: string, noteId: string): Promise<unknown> {
    const resp = await fetch(`${baseUrl}/api/notes/${noteId}`);
    if (!resp.ok) {
        throw new Error(`Failed to get note: ${resp.statusText}`);
    }
    return resp.json();
}

export async function updateNote(
    baseUrl: string,
    noteId: string,
    data: { content?: string; project?: string; tags?: string[] },
): Promise<unknown> {
    const resp = await fetch(`${baseUrl}/api/notes/${noteId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
    });
    if (!resp.ok) {
        throw new Error(`Failed to update note: ${resp.statusText}`);
    }
    return resp.json();
}

export async function deleteNote(baseUrl: string, noteId: string): Promise<void> {
    const resp = await fetch(`${baseUrl}/api/notes/${noteId}`, {
        method: "DELETE",
    });
    if (!resp.ok) {
        throw new Error(`Failed to delete note: ${resp.statusText}`);
    }
}

export async function searchNotes(baseUrl: string, query: string): Promise<unknown[]> {
    const params = new URLSearchParams({ q: query });
    const resp = await fetch(`${baseUrl}/api/notes/search?${params}`);
    if (!resp.ok) {
        throw new Error(`Failed to search notes: ${resp.statusText}`);
    }
    return resp.json() as Promise<unknown[]>;
}

export async function listNoteProjects(baseUrl: string): Promise<string[]> {
    const resp = await fetch(`${baseUrl}/api/notes/projects`);
    if (!resp.ok) {
        throw new Error(`Failed to list projects: ${resp.statusText}`);
    }
    return resp.json() as Promise<string[]>;
}

export async function updateTodo(
    baseUrl: string,
    todoId: number,
    data: { description?: string; completed?: boolean },
): Promise<unknown> {
    const resp = await fetch(`${baseUrl}/api/notes/todos/${todoId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
    });
    if (!resp.ok) {
        throw new Error(`Failed to update todo: ${resp.statusText}`);
    }
    return resp.json();
}

export async function deleteTodo(baseUrl: string, todoId: number): Promise<void> {
    const resp = await fetch(`${baseUrl}/api/notes/todos/${todoId}`, {
        method: "DELETE",
    });
    if (!resp.ok) {
        throw new Error(`Failed to delete todo: ${resp.statusText}`);
    }
}

export async function addTodo(baseUrl: string, noteId: string, description: string): Promise<unknown> {
    const resp = await fetch(`${baseUrl}/api/notes/${noteId}/todos`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description }),
    });
    if (!resp.ok) {
        throw new Error(`Failed to add todo: ${resp.statusText}`);
    }
    return resp.json();
}

// ---------------------------------------------------------------------------
// Memories (cross-session curated memory)
// ---------------------------------------------------------------------------

export interface MemoryRow {
    id: string;
    session_id: string;
    category: string;
    content: string;
    tags: string[];
    source_task?: string | null;
    created_at: string;
    curation_status: string;
    confidence: number;
    expires_at?: string | null;
    source_phase?: string | null;
    model_name?: string | null;
    seen_count: number;
    last_seen_at?: string | null;
}

export interface ListMemoriesParams {
    repo_root: string;
    category?: string;
    curation_status?: string;
    limit?: number;
    include_expired?: boolean;
}

export async function listMemories(
    baseUrl: string,
    params: ListMemoriesParams,
): Promise<MemoryRow[]> {
    const qs = new URLSearchParams();
    qs.set("repo_root", params.repo_root);
    if (params.category) { qs.set("category", params.category); }
    if (params.curation_status) { qs.set("curation_status", params.curation_status); }
    if (params.limit != null) { qs.set("limit", String(params.limit)); }
    if (params.include_expired) { qs.set("include_expired", "true"); }
    const resp = await fetch(`${baseUrl}/api/memories?${qs.toString()}`);
    if (!resp.ok) {
        throw new Error(`Failed to list memories: ${resp.statusText}`);
    }
    return resp.json() as Promise<MemoryRow[]>;
}

export async function createMemory(
    baseUrl: string,
    req: {
        repo_root: string;
        category: string;
        content: string;
        tags?: string[];
        source_task?: string;
    },
): Promise<MemoryRow> {
    const resp = await fetch(`${baseUrl}/api/memories`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(req),
    });
    if (!resp.ok) {
        throw new Error(`Failed to create memory: ${resp.statusText}`);
    }
    return resp.json() as Promise<MemoryRow>;
}

export async function confirmMemory(
    baseUrl: string, memoryId: string, repoRoot: string,
): Promise<MemoryRow> {
    const resp = await fetch(`${baseUrl}/api/memories/${memoryId}/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo_root: repoRoot }),
    });
    if (!resp.ok) {
        throw new Error(`Failed to confirm memory: ${resp.statusText}`);
    }
    return resp.json() as Promise<MemoryRow>;
}

export async function rejectMemory(
    baseUrl: string, memoryId: string, repoRoot: string,
): Promise<MemoryRow> {
    const resp = await fetch(`${baseUrl}/api/memories/${memoryId}/reject`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo_root: repoRoot }),
    });
    if (!resp.ok) {
        throw new Error(`Failed to reject memory: ${resp.statusText}`);
    }
    return resp.json() as Promise<MemoryRow>;
}

export async function deleteMemory(
    baseUrl: string, memoryId: string, repoRoot: string,
): Promise<void> {
    const qs = new URLSearchParams({ repo_root: repoRoot });
    const resp = await fetch(
        `${baseUrl}/api/memories/${memoryId}?${qs.toString()}`,
        { method: "DELETE" },
    );
    if (!resp.ok) {
        throw new Error(`Failed to delete memory: ${resp.statusText}`);
    }
}

export interface ExtractMemoriesResponse {
    session_id: string;
    memories_extracted: number;
    categories: string[];
}

export async function extractMemories(
    baseUrl: string,
    req: {
        session_id: string;
        repo_root: string;
        task: string;
        session_summary?: string;
        source_phase?: string;
    },
): Promise<ExtractMemoriesResponse> {
    const resp = await fetch(`${baseUrl}/api/memories/extract`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(req),
    });
    if (!resp.ok) {
        const body = await resp.text().catch(() => "");
        throw new Error(`Failed to extract memories: ${resp.statusText}${body ? ` — ${body}` : ""}`);
    }
    return resp.json() as Promise<ExtractMemoriesResponse>;
}

// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------

export async function chat(
    postJson: PostJsonFn,
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
    chatMode?: "architecture_review",
): Promise<{ reply: string; tokens_per_second?: number | null; eval_count?: number | null }> {
    const body: Record<string, unknown> = { message, history };
    if (workspace) {
        body.workspace = workspace;
    }
    if (userName) { body.user_name = userName; }
    if (chatMode) { body.chat_mode = chatMode; }
    const data = (await postJson("/api/chat", body)) as {
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
export function chatStream(
    baseUrl: string,
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
    extendedTurns?: number,
    chatMode?: "architecture_review",
): Promise<{ receivedDone: boolean }> {
    return new Promise((resolve, reject) => {
        const fullUrl = new URL(`${baseUrl}/api/chat/stream`);
        const isHttps = fullUrl.protocol === "https:";
        const transport = isHttps ? https : http;

        const body: Record<string, unknown> = { message, history };
        if (workspace) { body.workspace = workspace; }
        if (attachments && attachments.length > 0) { body.attachments = attachments; }
        if (userName) { body.user_name = userName; }
        if (skipWebSearch) { body.skip_web_search = true; }
        if (typeof extendedTurns === "number" && extendedTurns > 0) {
            body.extended_turns = extendedTurns;
        }
        if (chatMode) { body.chat_mode = chatMode; }
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

        let sseBuffer = "";
        let isFirst = true;
        let resolved = false;

        const req = transport.request(options, (res) => {
            if (res.statusCode && (res.statusCode < 200 || res.statusCode >= 300)) {
                reject(new Error(`HTTP ${res.statusCode}: ${res.statusMessage}`));
                return;
            }

            res.on("data", (chunk: Buffer | string) => {
                sseBuffer += chunk.toString();
                const lines = sseBuffer.split("\n");
                sseBuffer = lines.pop()!; // keep incomplete last line in buffer

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

// ---------------------------------------------------------------------------
// Predictions
// ---------------------------------------------------------------------------

export async function predict(baseUrl: string, context: InlinePredictionContext): Promise<PredictionResult> {
    const resp = await fetch(`${baseUrl}/api/predict`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(context),
    });
    if (!resp.ok) {
        return { completion: "", confidence: 0 };
    }
    return resp.json() as Promise<PredictionResult>;
}

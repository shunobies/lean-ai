/**
 * Chat, agent, greeting and diagnostics helpers extracted from
 * LeanAISidebarProvider.
 *
 * Every function receives a `ChatContext` that exposes the subset of
 * provider state it needs.
 */

import * as vscode from "vscode";
import type { BackendClient } from "./backendClient";
import type { ConversationManager } from "./conversationManager";
import type { SessionTreeProvider } from "./sessionTreeProvider";
import { BACKEND_SETTING_MAP, clearYamlSetting, resolveConfigFilePath, writeYamlSetting } from "./settingsSync";
import { restartBackend } from "./backendProcess";
import type { Attachment } from "./types";
import type { VoiceContext } from "./sidebarVoice";
import {
    feedTtsToken,
    resetTtsAccumulator,
    speakSentence,
    stripCodeForTts,
} from "./sidebarVoice";
import WebSocket from "ws";

// ── Context interface ────────────────────────────────────────────

export interface ChatContext {
    client: BackendClient;
    postMessage(msg: Record<string, unknown>): void;
    getRepoRoot(): string;
    ensureSession(): Promise<string>;
    ensureWebSocket(sessionId: string): WebSocket;
    getWs(): WebSocket | undefined;
    setWsSessionId(id: string): void;
    closeWebSocket(): void;
    /** Reset session/workflow state (sessionId, wsSessionId, lastCompletedSessionId, globalState). */
    resetSessionState(): void;

    chatHistory: Array<{ role: string; content: string; timestamp: string }>;
    conversations: ConversationManager;
    sessionTreeProvider: SessionTreeProvider | undefined;

    includeProblems: boolean;
    includeDebug: boolean;

    slashCommands: Map<string, (args: string) => Promise<void>>;

    /** Voice context accessor — returns the provider's VoiceContext so TTS
     *  helpers can read/write the mutable TTS pipeline state. */
    voiceCtx(): VoiceContext;

    lastDebugStop: { reason: string; text?: string; threadId?: number } | undefined;
}

// ── Workspace context ────────────────────────────────────────────

export function getWorkspaceContext(): {
    workspace_name?: string;
    workspace_root?: string;
    active_file?: string;
    active_language?: string;
    active_selection?: string;
} {
    const folders = vscode.workspace.workspaceFolders;
    const editor = vscode.window.activeTextEditor;

    const ctx: {
        workspace_name?: string;
        workspace_root?: string;
        active_file?: string;
        active_language?: string;
        active_selection?: string;
    } = {};

    if (folders && folders.length > 0) {
        ctx.workspace_name = folders[0].name;
        ctx.workspace_root = folders[0].uri.fsPath;
    }

    if (editor) {
        const docUri = editor.document.uri;
        if (folders && folders.length > 0) {
            const rel = vscode.workspace.asRelativePath(docUri, false);
            ctx.active_file = rel;
        } else {
            ctx.active_file = docUri.fsPath;
        }
        ctx.active_language = editor.document.languageId;

        const selection = editor.selection;
        if (!selection.isEmpty) {
            ctx.active_selection = editor.document.getText(selection);
        }
    }

    return ctx;
}

// ── Diagnostics ──────────────────────────────────────────────────

export function sendDiagnosticsUpdate(
    postMessage: (msg: Record<string, unknown>) => void,
): void {
    const allDiags = vscode.languages.getDiagnostics();
    let errorCount = 0;
    let warningCount = 0;
    for (const [, diags] of allDiags) {
        for (const d of diags) {
            if (d.severity === vscode.DiagnosticSeverity.Error) errorCount++;
            else if (d.severity === vscode.DiagnosticSeverity.Warning) warningCount++;
        }
    }
    postMessage({ type: 'diagnosticsUpdate', errorCount, warningCount });
}

export function collectDiagnosticsContext(scope: 'workspace' | 'file'): string {
    const editor = vscode.window.activeTextEditor;
    const folders = vscode.workspace.workspaceFolders;

    let entries: [vscode.Uri, readonly vscode.Diagnostic[]][];
    if (scope === 'file' && editor) {
        entries = [[editor.document.uri, vscode.languages.getDiagnostics(editor.document.uri)]];
    } else {
        entries = vscode.languages.getDiagnostics();
    }

    const errors: string[] = [];
    const warnings: string[] = [];
    for (const [uri, diags] of entries) {
        const rel = folders ? vscode.workspace.asRelativePath(uri, false) : uri.fsPath;
        for (const d of diags) {
            const loc = `${rel}:${d.range.start.line + 1}:${d.range.start.character + 1}`;
            const src = d.source ? `[${d.source}] ` : '';
            const line = `  - ${loc} ${src}${d.message}`;
            if (d.severity === vscode.DiagnosticSeverity.Error) errors.push(line);
            else if (d.severity === vscode.DiagnosticSeverity.Warning) warnings.push(line);
        }
    }

    if (errors.length === 0 && warnings.length === 0) { return ''; }

    const parts = ['', '---', 'Current Problems (VSCode diagnostics):'];
    if (errors.length > 0) {
        parts.push(`Errors (${errors.length}):`);
        parts.push(...errors.slice(0, 40));
    }
    if (warnings.length > 0) {
        parts.push(`Warnings (${warnings.length}):`);
        parts.push(...warnings.slice(0, 20));
    }
    parts.push('---');
    return parts.join('\n');
}

// ── Debug context ────────────────────────────────────────────────

export async function collectDebugContext(
    lastDebugStop: { reason: string; text?: string; threadId?: number } | undefined,
): Promise<string> {
    const session = vscode.debug.activeDebugSession;
    if (!session) { return ''; }

    const parts: string[] = ['', '---', `Active Debug Session: "${session.name}" (${session.type})`];

    if (lastDebugStop) {
        const stopDesc = lastDebugStop.text
            ? `${lastDebugStop.reason} — ${lastDebugStop.text}`
            : lastDebugStop.reason;
        parts.push(`Stopped: ${stopDesc}`);
    }

    try {
        // Determine the thread to inspect
        let threadId = lastDebugStop?.threadId;
        if (threadId === undefined) {
            const threadsResp = await session.customRequest('threads') as { threads: { id: number; name: string }[] };
            if (threadsResp.threads.length > 0) {
                threadId = threadsResp.threads[0].id;
            }
        }

        if (threadId !== undefined) {
            // Get stack frames
            const stackResp = await session.customRequest('stackTrace', { threadId, levels: 5 }) as {
                stackFrames: { id: number; name: string; source?: { path?: string }; line: number }[];
            };
            if (stackResp.stackFrames.length > 0) {
                parts.push('Call Stack:');
                for (let i = 0; i < stackResp.stackFrames.length; i++) {
                    const f = stackResp.stackFrames[i];
                    const src = f.source?.path
                        ? (vscode.workspace.workspaceFolders
                            ? vscode.workspace.asRelativePath(f.source.path, false)
                            : f.source.path)
                        : '<unknown>';
                    parts.push(`  [${i}] ${f.name}  ${src}:${f.line}`);
                }

                // Get local variables from the top frame
                const topFrameId = stackResp.stackFrames[0].id;
                const scopesResp = await session.customRequest('scopes', { frameId: topFrameId }) as {
                    scopes: { name: string; variablesReference: number; expensive: boolean }[];
                };
                const localScope = scopesResp.scopes.find(s => !s.expensive && s.variablesReference > 0);
                if (localScope) {
                    const varsResp = await session.customRequest('variables', {
                        variablesReference: localScope.variablesReference,
                        count: 20,
                    }) as { variables: { name: string; value: string }[] };
                    if (varsResp.variables.length > 0) {
                        parts.push(`Local Variables (${stackResp.stackFrames[0].name}):`);
                        for (const v of varsResp.variables) {
                            // Truncate very long values
                            const val = v.value.length > 120 ? v.value.slice(0, 120) + '\u2026' : v.value;
                            parts.push(`  ${v.name} = ${val}`);
                        }
                    }
                }
            }
        }
    } catch {
        // DAP queries can fail if the debugger is not paused — that's fine
    }

    parts.push('---');
    return parts.join('\n');
}

// ── User message routing ─────────────────────────────────────────

export async function handleUserMessage(
    ctx: ChatContext,
    text: string,
    attachments?: Attachment[],
): Promise<void> {
    // --- Slash command interception (before chat/agent routing) ---
    const trimmed = text.trim();
    const slashMatch = trimmed.match(/^(\/\w+)(?:\s+(.*))?$/s);
    if (slashMatch) {
        const command = slashMatch[1].toLowerCase();
        const args = slashMatch[2] || "";
        const handler = ctx.slashCommands.get(command);
        if (handler) {
            try {
                await handler(args);
            } catch (e) {
                const error = e instanceof Error ? e.message : String(e);
                ctx.postMessage({ type: "error", text: error });
            }
            ctx.postMessage({ type: "sendEnabled" });
            return;
        }
        // Unknown slash command — fall through to normal chat
    }

    // If agent workflow is active (WebSocket open), send as WS message.
    const ws = ctx.getWs();
    if (ws && ws.readyState === WebSocket.OPEN) {
        ctx.postMessage({ type: "thinking", show: true, text: "Sending feedback..." });
        ws.send(JSON.stringify({ type: "user_message", content: text, repo_root: ctx.getRepoRoot() }));
        return;
    }

    // Otherwise, default to chat mode
    ctx.postMessage({ type: "thinking", show: true, text: "Thinking..." });

    try {
        // Check backend health
        const healthy = await ctx.client.healthCheck();
        // Notify webview of vision capability
        ctx.postMessage({ type: "visionAvailable", available: ctx.client.visionAvailable });
        if (!healthy) {
            ctx.postMessage({ type: "thinking", show: false });
            ctx.postMessage({
                type: "error",
                text: "Backend not available. Start the server:\ncd backend && uvicorn lean_ai.main:app --reload --port 8422",
            });
            ctx.postMessage({ type: "sendEnabled" });
            return;
        }

        await handleChatMessage(ctx, text, attachments);
    } catch (e) {
        ctx.postMessage({ type: "thinking", show: false });
        const error = e instanceof Error ? e.message : String(e);
        ctx.postMessage({ type: "error", text: error });
        ctx.postMessage({ type: "sendEnabled" });
    }
}

// ── Chat mode: streaming LLM call with workspace context ─────────

export async function handleChatMessage(
    ctx: ChatContext,
    text: string,
    attachments?: Attachment[],
): Promise<void> {
    const now = new Date().toISOString();
    const vCtx = ctx.voiceCtx();

    // Append any enabled context pills to the outgoing message
    let chatText = text;
    if (ctx.includeProblems) {
        chatText += collectDiagnosticsContext('workspace');
    }
    if (ctx.includeDebug && vscode.debug.activeDebugSession) {
        chatText += await collectDebugContext(ctx.lastDebugStop);
    }

    // Convert attachments to backend format
    const apiAttachments = attachments?.map(a => ({
        data: a.data,
        filename: a.filename,
        mime_type: a.mimeType,
    }));

    // Cancel any in-progress TTS from previous message
    if (vCtx.ttsEnabled) { resetTtsAccumulator(vCtx); }

    // Add user message to history with timestamp (store original text, not augmented)
    ctx.chatHistory.push({ role: "user", content: text, timestamp: now });

    // Gather workspace context from VSCode
    const workspace = getWorkspaceContext();

    // Stream tokens from /api/chat/stream — strip timestamps before sending
    const historyForApi = ctx.chatHistory.slice(0, -1).map(({ role, content }) => ({ role, content }));
    const userName = vscode.workspace.getConfiguration("lean-ai").get<string>("userName", "") || undefined;

    let fullReply = "";
    let isFirst = true;
    let streamStartTime: number | null = null;
    let tokenCount = 0;
    ctx.sessionTreeProvider?.pauseRefresh();
    let receivedDone = false;
    try {
        ({ receivedDone } = await ctx.client.chatStream(chatText, historyForApi, workspace, (token) => {
            if (streamStartTime === null) { streamStartTime = Date.now(); }
            tokenCount++;
            fullReply += token;
            ctx.postMessage({ type: "chatToken", content: token, isFirst });
            isFirst = false;
            if (vCtx.ttsEnabled) { feedTtsToken(vCtx, token); }
        }, apiAttachments, (thinkingToken) => {
            ctx.postMessage({ type: "llmThinking", text: thinkingToken, streaming: true });
        }, userName, undefined, (desc) => {
            ctx.postMessage({
                type: "reply",
                text: `<details class="vision-desc"><summary>Vision model description</summary>\n\n${desc}\n</details>`,
                cls: "msg-system",
            });
        }, (name, description) => {
            ctx.postMessage({ type: "chatToolActivity", name, description });
        }, (name, success) => {
            ctx.postMessage({ type: "chatToolResult", name, success });
        }));
    } finally {
        ctx.sessionTreeProvider?.resumeRefresh();
    }

    // TTS: flush any remaining accumulated text
    if (vCtx.ttsEnabled && vCtx.ttsSentenceAccumulator.trim()) {
        const remainder = stripCodeForTts(vCtx.ttsSentenceAccumulator);
        if (remainder) { speakSentence(vCtx, remainder); }
        vCtx.ttsSentenceAccumulator = "";
        vCtx.ttsInCodeFence = false;
    }

    // Compute tok/s from first-token to last-token (excludes context-gathering latency)
    const tps = (streamStartTime !== null && tokenCount > 0)
        ? Math.round(tokenCount / ((Date.now() - streamStartTime) / 1000) * 10) / 10
        : null;

    // Stream completed — update history and notify webview
    ctx.chatHistory.push({ role: "assistant", content: fullReply, timestamp: new Date().toISOString() });

    // Keep history manageable (last 40 messages = 20 exchanges)
    if (ctx.chatHistory.length > 40) {
        ctx.chatHistory = ctx.chatHistory.slice(-40);
    }

    // Send full text so the webview can apply markdown formatting, plus tok/s metrics
    const truncated = !receivedDone && tokenCount > 0;
    ctx.postMessage({ type: "chatDone", fullText: fullReply, tps, evalCount: tokenCount, truncated });

    // Persist conversation after each exchange
    await ctx.conversations.persistCurrentConversation(ctx.chatHistory);
}

// ── Startup greeting ─────────────────────────────────────────────

export async function sendGreeting(ctx: ChatContext): Promise<void> {
    const config = vscode.workspace.getConfiguration("lean-ai");
    const userName = config.get<string>("userName", "") || undefined;
    const workspace = getWorkspaceContext();
    const projectName = workspace?.workspace_name || "your project";
    const vCtx = ctx.voiceCtx();

    const greetingPrompt = userName
        ? `Greet ${userName} warmly and ask what we're going to work on in ${projectName} today. Keep it brief and friendly — one or two sentences.`
        : `Greet the user warmly and ask what we're going to work on in ${projectName} today. Keep it brief and friendly — one or two sentences.`;

    let fullReply = "";
    let isFirst = true;
    if (vCtx.ttsEnabled) { resetTtsAccumulator(vCtx); }

    try {
        await ctx.client.chatStream(
            greetingPrompt, [], workspace,
            (token) => {
                fullReply += token;
                ctx.postMessage({ type: "chatToken", content: token, isFirst });
                isFirst = false;
                if (vCtx.ttsEnabled) { feedTtsToken(vCtx, token); }
            },
            undefined,
            (thinkingToken) => {
                ctx.postMessage({ type: "llmThinking", text: thinkingToken, streaming: true });
            },
            userName,
            true, // skipWebSearch — greeting doesn't need internet
        );
    } catch {
        // Greeting failed — not critical, user can type
        return;
    }

    if (fullReply.trim()) {
        ctx.chatHistory.push({
            role: "assistant",
            content: fullReply,
            timestamp: new Date().toISOString(),
        });
        ctx.postMessage({ type: "chatDone", fullText: fullReply });

        // TTS: flush any remaining accumulated text
        if (vCtx.ttsEnabled && vCtx.ttsSentenceAccumulator.trim()) {
            const remainder = stripCodeForTts(vCtx.ttsSentenceAccumulator);
            if (remainder) { speakSentence(vCtx, remainder); }
            vCtx.ttsSentenceAccumulator = "";
            vCtx.ttsInCodeFence = false;
        }
    }
}

// ── First-boot setup guide ───────────────────────────────────────

export function showFirstBootSetup(
    postMessage: (msg: Record<string, unknown>) => void,
): void {
    const platform = process.platform; // 'linux', 'darwin', 'win32'

    let installCmd: string;
    if (platform === "linux") {
        installCmd = "curl -fsSL https://ollama.com/install.sh | sh";
    } else if (platform === "darwin") {
        installCmd = "brew install ollama";
    } else {
        installCmd = "winget install Ollama.Ollama";
    }

    const model = vscode.workspace.getConfiguration("lean-ai")
        .get<string>("ollamaModel", "qwen3-coder:30b");

    postMessage({
        type: "setupGuide",
        platform,
        installCmd,
        pullCmd: `ollama pull ${model}`,
        model,
    });
}

// ── Agent mode: full WebSocket workflow ──────────────────────────

export async function handleAgentMessage(ctx: ChatContext, text: string): Promise<void> {
    ctx.postMessage({ type: "thinking", show: true, text: "Starting workflow..." });

    // Get or create session
    const sessionId = await ctx.ensureSession();
    ctx.setWsSessionId(sessionId);

    // Ensure WebSocket is connected
    const ws = ctx.ensureWebSocket(sessionId);

    // Wait for WS to be open before sending
    if (ws.readyState === WebSocket.CONNECTING) {
        await new Promise<void>((resolve, reject) => {
            const onOpen = () => {
                ws.removeListener("error", onError);
                resolve();
            };
            const onError = (err: Error) => {
                ws.removeListener("open", onOpen);
                reject(err);
            };
            ws.once("open", onOpen);
            ws.once("error", onError);
        });
    }

    // Append any enabled context pills before sending
    let content = text;
    if (ctx.includeProblems) {
        content += collectDiagnosticsContext('workspace');
    }
    if (ctx.includeDebug && vscode.debug.activeDebugSession) {
        content += await collectDebugContext(ctx.lastDebugStop);
    }

    // Send message over WebSocket — the workflow runs server-side
    ws.send(JSON.stringify({ type: "user_message", content, repo_root: ctx.getRepoRoot() }));
}

// ── Plan approval ────────────────────────────────────────────────

export function handleApprove(ctx: ChatContext): void {
    const ws = ctx.getWs();
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        ctx.postMessage({ type: "error", text: "WebSocket not connected." });
        return;
    }

    ctx.postMessage({ type: "hideApproval" });
    ctx.postMessage({ type: "thinking", show: true, text: "Approving plan..." });
    ws.send(JSON.stringify({ type: "approve" }));
}

// ── New chat ─────────────────────────────────────────────────────

export async function handleNewChat(
    ctx: ChatContext,
    messagesHtml?: string,
): Promise<void> {
    await ctx.conversations.persistCurrentConversation(ctx.chatHistory);
    const archiveTabId = ctx.conversations.currentConversationId
        ? `chat-${ctx.conversations.currentConversationId}`
        : undefined;
    const firstUserMsg = ctx.chatHistory.find(m => m.role === "user");
    const archiveTitle = firstUserMsg
        ? firstUserMsg.content.slice(0, 80).replace(/\n/g, " ")
        : "";
    const hasMessages = ctx.chatHistory.length > 0;

    ctx.closeWebSocket();
    ctx.resetSessionState();
    ctx.chatHistory.length = 0;
    ctx.conversations.currentConversationId = undefined;
    ctx.conversations.viewingHistoricConversation = false;

    ctx.postMessage({
        type: "chatArchived",
        tabId: hasMessages ? archiveTabId : undefined,
        title: hasMessages ? archiveTitle : undefined,
        messagesHtml: hasMessages ? messagesHtml : undefined,
    });
}

// ── Configuration sync ───────────────────────────────────────────

/**
 * Handle VS Code configuration changes: sync backend settings to config.yaml
 * and offer to restart. Also handles font-size live updates.
 */
export async function handleConfigurationChange(
    e: vscode.ConfigurationChangeEvent,
    globalStoragePath: string,
    postMessage: (msg: Record<string, unknown>) => void,
): Promise<void> {
    if (e.affectsConfiguration("lean-ai.chatFontSize")) {
        const newSize = vscode.workspace.getConfiguration("lean-ai").get<number>("chatFontSize", 13);
        postMessage({ type: "setFontSize", size: newSize });
    }

    const changedKeys = Object.keys(BACKEND_SETTING_MAP).filter(k => e.affectsConfiguration(k));
    if (changedKeys.length > 0) {
        const config = vscode.workspace.getConfiguration();
        const backendDirSetting = config.get<string>("lean-ai.backendDir", "");
        const configPath = resolveConfigFilePath(backendDirSetting || undefined, globalStoragePath);
        if (configPath) {
            for (const key of changedKeys) {
                const envVar = BACKEND_SETTING_MAP[key];
                const val = config.get<unknown>(key);
                if (val !== undefined && val !== null && String(val) !== "") {
                    writeYamlSetting(configPath, envVar, String(val));
                } else {
                    clearYamlSetting(configPath, envVar);
                }
            }
        }
        const action = await vscode.window.showInformationMessage(
            "Lean AI settings changed. Restart the backend to apply.",
            "Restart Now",
            "Later",
        );
        if (action === "Restart Now") {
            await restartBackend();
        }
    }
}

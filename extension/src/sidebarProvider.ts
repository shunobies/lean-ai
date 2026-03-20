/**
 * Webview sidebar provider — chat-first panel for Lean AI.
 *
 * All user input starts in chat mode (direct LLM with workspace context).
 * When the LLM produces a "Suggested Agent Prompt" block, a "Send to Agent"
 * button appears on hover. Clicking it routes the refined prompt to the full
 * FSM workflow (clarification → plan → approval → implementation).
 *
 * After the agent workflow completes, the WebSocket is closed and the user
 * automatically returns to chat mode.
 */

import * as path from "path";
import * as vscode from "vscode";
import { BackendClient } from "./backendClient";
import { ConversationManager } from "./conversationManager";
import { getWebviewHtml } from "./sidebarHtml";
import { createSlashCommands } from "./slashCommands";
import type { SlashCommandContext } from "./slashCommands";
import type { WSMessage } from "./types";
import { handleWsMessage } from "./wsHandler";
import type { WsHandlerContext } from "./wsHandler";
import { SettingsPanel } from "./settingsPanel";
import { BACKEND_SETTING_MAP, resolveEnvFilePath, writeEnvSetting } from "./settingsSync";
import { restartBackend } from "./backendProcess";
import {
    notifyApprovalNeeded,
    notifyComplete,
    notifyError,
    notifyToolApprovalNeeded,
} from "./notifications";
import WebSocket from "ws";

export class LeanAISidebarProvider implements vscode.WebviewViewProvider {
    public static readonly viewType = "lean-ai.chatView";

    private webviewView?: vscode.WebviewView;
    private client: BackendClient;
    private sessionId: string | undefined;
    private ws: WebSocket | undefined;

    // Chat mode state — includes timestamps for persistence
    private chatHistory: Array<{ role: string; content: string; timestamp: string }> = [];

    // Slash command registry
    private slashCommands: Map<string, (args: string) => Promise<void>>;

    // Conversation persistence
    private conversations: ConversationManager;

    // Tracks the most recently completed workflow session for /approve and /reject
    // Persisted in globalState so it survives window reloads.
    private lastCompletedSessionId: string | undefined;

    // Set by extension.ts when a scaffold was just created and this window is the new project
    private _pendingInit = false;

    // Context pill state — whether to append diagnostics/debug data to outgoing messages
    private _includeProblems = false;
    private _includeDebug = false;
    private _lastDebugStop?: { reason: string; text?: string; threadId?: number };

    constructor(
        private readonly extensionUri: vscode.Uri,
        private readonly context: vscode.ExtensionContext,
    ) {
        this.client = BackendClient.getInstance();

        // Restore last completed session from globalState (survives reloads)
        this.lastCompletedSessionId = this.context.globalState.get<string>(
            "lean-ai.lastCompletedSessionId",
        );

        // Conversation persistence manager
        this.conversations = new ConversationManager(
            this.context,
            (msg) => this.postMessage(msg),
            () => this.getRepoRoot(),
        );

        // Build slash command context — closures read current state each call
        const cmdCtx: SlashCommandContext = {
            postMessage: (msg) => this.postMessage(msg),
            client: this.client,
            getRepoRoot: () => this.getRepoRoot(),
            ensureSession: () => this.ensureSession(),
            ensureWebSocket: (sid) => this.ensureWebSocket(sid),
            handleAgentMessage: (text) => this.handleAgentMessage(text),
            getWs: () => this.ws,
            getLastCompletedSessionId: () => this.lastCompletedSessionId,
            setSessionId: (id) => { this.sessionId = id; },
            setLastCompletedSessionId: (id) => {
                this.lastCompletedSessionId = id;
            },
            extensionContext: this.context,
            getFileDiagnostics: () => this.collectDiagnosticsContext('file'),
        };
        this.slashCommands = createSlashCommands(cmdCtx);
    }

    /** Load a past session's conversation into the chat panel for review. */
    async loadSessionConversation(sessionId: string): Promise<void> {
        return this.conversations.loadSessionConversation(sessionId);
    }

    resolveWebviewView(
        webviewView: vscode.WebviewView,
        _context: vscode.WebviewViewResolveContext,
        _token: vscode.CancellationToken,
    ): void {
        this.webviewView = webviewView;

        webviewView.webview.options = {
            enableScripts: true,
            localResourceRoots: [this.extensionUri],
        };

        this.conversations.ensureStorageDir().catch(() => {});
        webviewView.webview.html = this.getHtml();

        // Check if a workflow completed while the panel was disposed
        if (this.lastCompletedSessionId) {
            setTimeout(() => {
                this.postMessage({
                    type: "reply",
                    text: "The agent workflow completed while the panel was closed.\n\nUse `/approve` to merge the changes or `/reject` to discard them.",
                    cls: "msg-ai",
                });
                this.postMessage({ type: "sendEnabled" });
            }, 500);
        }

        webviewView.webview.onDidReceiveMessage(async (msg) => {
            switch (msg.type) {
                case "sendMessage":
                    try {
                        await this.handleUserMessage(msg.text);
                    } catch {
                        // Safety: always re-enable send on unexpected errors
                        this.postMessage({ type: "sendEnabled" });
                    }
                    break;
                case "sendToAgent":
                    // User clicked "Send to Agent" on a refined prompt block
                    this.postMessage({
                        type: "reply",
                        text: "Sending to agent...",
                        cls: "msg-system",
                    });
                    await this.handleAgentMessage(msg.text as string);
                    break;
                case "newChat": {
                    // Persist current conversation, then archive it as a tab
                    await this.conversations.persistCurrentConversation(this.chatHistory);

                    // Build archive info before clearing state
                    const archiveTabId = this.conversations.currentConversationId
                        ? `chat-${this.conversations.currentConversationId}`
                        : undefined;
                    const firstUserMsg = this.chatHistory.find(m => m.role === "user");
                    const archiveTitle = firstUserMsg
                        ? firstUserMsg.content.slice(0, 80).replace(/\n/g, " ")
                        : "";
                    const hasMessages = this.chatHistory.length > 0;

                    this.closeWebSocket();
                    this.sessionId = undefined;
                    this.lastCompletedSessionId = undefined;
                    this.context.globalState.update("lean-ai.lastCompletedSessionId", undefined);
                    this.chatHistory = [];
                    this.conversations.currentConversationId = undefined;
                    this.conversations.viewingHistoricConversation = false;

                    // Send archived tab info + reset in one message
                    this.postMessage({
                        type: "chatArchived",
                        tabId: hasMessages ? archiveTabId : undefined,
                        title: hasMessages ? archiveTitle : undefined,
                        messagesHtml: hasMessages ? (msg.messagesHtml as string) : undefined,
                    });
                    break;
                }
                case "approve":
                    this.handleApprove();
                    break;
                case "searchConversations":
                    this.conversations.handleSearchConversations(msg.query as string);
                    break;
                case "loadConversation":
                    this.conversations.handleLoadConversation(msg.id as string);
                    break;
                case "backToCurrentChat":
                    this.conversations.handleBackToCurrentChat(this.chatHistory);
                    break;
                case "webviewReady":
                    // Webview (re)initialised — replay chat history so messages aren't
                    // lost when the panel is destroyed and recreated (e.g. open-in-editor).
                    if (this.chatHistory.length > 0) {
                        this.conversations.handleBackToCurrentChat(this.chatHistory);
                    }
                    // Push current diagnostics count and debug session state
                    this.sendDiagnosticsUpdate();
                    {
                        const dbgSession = vscode.debug.activeDebugSession;
                        this.postMessage({ type: 'debugStateUpdate', active: !!dbgSession, name: dbgSession?.name });
                    }
                    break;
                case "approve_tool":
                case "deny_tool":
                    // Forward tool approval/denial straight to the backend WebSocket.
                    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                        this.ws.send(JSON.stringify({
                            type: msg.type,
                            token: msg.token as string,
                        }));
                    }
                    break;
                case "toggleProblems":
                    this._includeProblems = msg.enabled as boolean;
                    break;
                case "toggleDebug":
                    this._includeDebug = msg.enabled as boolean;
                    break;
                case "openSettings":
                    SettingsPanel.createOrShow(this.context);
                    break;
                case "openFileDiff":
                    this.openFileDiff(
                        msg.file as string,
                        msg.baseBranch as string,
                    );
                    break;
            }
        });

        // Live font-size updates when user changes the setting
        vscode.workspace.onDidChangeConfiguration(async (e) => {
            if (e.affectsConfiguration("lean-ai.chatFontSize")) {
                const newSize = vscode.workspace.getConfiguration("lean-ai").get<number>("chatFontSize", 13);
                this.postMessage({ type: "setFontSize", size: newSize });
            }

            // Sync any changed backend settings to .env and offer restart
            const changedKeys = Object.keys(BACKEND_SETTING_MAP).filter(k => e.affectsConfiguration(k));
            if (changedKeys.length > 0) {
                const config = vscode.workspace.getConfiguration();
                const backendDirSetting = config.get<string>("lean-ai.backendDir", "");
                const envPath = resolveEnvFilePath(backendDirSetting || undefined, this.context.globalStorageUri.fsPath);
                if (envPath) {
                    for (const key of changedKeys) {
                        const envVar = BACKEND_SETTING_MAP[key];
                        const val = config.get<unknown>(key);
                        if (val !== undefined && val !== null && String(val) !== "") {
                            writeEnvSetting(envPath, envVar, String(val));
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
        }, null, this.context.subscriptions);

        // Live diagnostics count updates → Problems pill in webview
        vscode.languages.onDidChangeDiagnostics(() => {
            this.sendDiagnosticsUpdate();
        }, null, this.context.subscriptions);

        // Debug session changes → show/hide Debug pill in webview
        vscode.debug.onDidChangeActiveDebugSession((session) => {
            this.postMessage({ type: 'debugStateUpdate', active: !!session, name: session?.name });
            if (!session) {
                this._lastDebugStop = undefined;
            }
        }, null, this.context.subscriptions);

        // Capture stopped events (breakpoint hit, exception, etc.) for debug context
        vscode.debug.onDidReceiveDebugSessionCustomEvent((e) => {
            if (e.event === 'stopped') {
                this._lastDebugStop = {
                    reason: (e.body as { reason?: string })?.reason ?? 'unknown',
                    text: (e.body as { text?: string })?.text,
                    threadId: (e.body as { threadId?: number })?.threadId,
                };
                this.postMessage({
                    type: 'debugStateUpdate',
                    active: true,
                    name: e.session.name,
                    stopped: true,
                    reason: this._lastDebugStop.reason,
                });
            }
        }, null, this.context.subscriptions);

        // Persist conversation when view is disposed, but do NOT close the
        // WebSocket. If a workflow is running, closing the WS would kill it.
        // The WS 'complete' handler will clean up when the workflow finishes.
        webviewView.onDidDispose(() => {
            this.conversations.persistCurrentConversation(this.chatHistory);
            this.webviewView = undefined;
        });

        // If this window was opened for a freshly scaffolded project, auto-run /init
        if (this._pendingInit) {
            this._pendingInit = false;
            // Delay slightly so the webview HTML has time to fully initialize
            const initHandler = this.slashCommands.get("/init");
            if (initHandler) {
                setTimeout(() => initHandler(""), 1000);
            }
        }
    }

    private postMessage(msg: Record<string, unknown>): void {
        this.webviewView?.webview.postMessage(msg);
    }

    private getRepoRoot(): string {
        const folders = vscode.workspace.workspaceFolders;
        if (folders && folders.length > 0) {
            return folders[0].uri.fsPath;
        }
        return ".";
    }

    private async openFileDiff(filePath: string, baseBranch: string): Promise<void> {
        const repoRoot = this.getRepoRoot();
        const absolutePath = path.isAbsolute(filePath)
            ? filePath
            : path.join(repoRoot, filePath);
        const rightUri = vscode.Uri.file(absolutePath);

        if (!baseBranch) {
            await vscode.commands.executeCommand("vscode.open", rightUri);
            return;
        }

        // Use VS Code's built-in git extension URI scheme (same as Timeline/SCM)
        const leftUri = rightUri.with({
            scheme: "git",
            query: JSON.stringify({ path: absolutePath, ref: baseBranch }),
        });
        const title = `${path.basename(filePath)} (${baseBranch} \u2194 working)`;
        await vscode.commands.executeCommand("vscode.diff", leftUri, rightUri, title);
    }

    private getWorkspaceContext(): {
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
            // Get relative path from workspace root
            const docUri = editor.document.uri;
            if (folders && folders.length > 0) {
                const rel = vscode.workspace.asRelativePath(docUri, false);
                ctx.active_file = rel;
            } else {
                ctx.active_file = docUri.fsPath;
            }
            ctx.active_language = editor.document.languageId;

            // Get selected text if any
            const selection = editor.selection;
            if (!selection.isEmpty) {
                ctx.active_selection = editor.document.getText(selection);
            }
        }

        return ctx;
    }

    private sendDiagnosticsUpdate(): void {
        const allDiags = vscode.languages.getDiagnostics();
        let errorCount = 0;
        let warningCount = 0;
        for (const [, diags] of allDiags) {
            for (const d of diags) {
                if (d.severity === vscode.DiagnosticSeverity.Error) errorCount++;
                else if (d.severity === vscode.DiagnosticSeverity.Warning) warningCount++;
            }
        }
        this.postMessage({ type: 'diagnosticsUpdate', errorCount, warningCount });
    }

    collectDiagnosticsContext(scope: 'workspace' | 'file'): string {
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

    private async collectDebugContext(): Promise<string> {
        const session = vscode.debug.activeDebugSession;
        if (!session) { return ''; }

        const parts: string[] = ['', '---', `Active Debug Session: "${session.name}" (${session.type})`];

        if (this._lastDebugStop) {
            const stopDesc = this._lastDebugStop.text
                ? `${this._lastDebugStop.reason} — ${this._lastDebugStop.text}`
                : this._lastDebugStop.reason;
            parts.push(`Stopped: ${stopDesc}`);
        }

        try {
            // Determine the thread to inspect
            let threadId = this._lastDebugStop?.threadId;
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
                                const val = v.value.length > 120 ? v.value.slice(0, 120) + '…' : v.value;
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

    private async ensureSession(): Promise<string> {
        if (this.sessionId) {
            return this.sessionId;
        }
        const repoRoot = this.getRepoRoot();
        const response = await this.client.createSession(repoRoot);
        this.sessionId = response.session_id;
        return this.sessionId;
    }

    private ensureWebSocket(sessionId: string): WebSocket {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            return this.ws;
        }

        this.closeWebSocket();

        this.ws = this.client.connectWebSocket(
            sessionId,
            (msg) => this.handleWsMessage(msg),
            (err) => {
                console.error("WS error:", err);
                this.postMessage({
                    type: "error",
                    text: `WebSocket error: ${err.message}`,
                });
            },
            () => {
                console.log("WS closed for session", sessionId);
            },
        );

        return this.ws;
    }

    private closeWebSocket(): void {
        if (this.ws) {
            try {
                this.ws.close();
            } catch {
                // ignore
            }
            this.ws = undefined;
        }
    }

    private handleWsMessage(msg: WSMessage): void {
        // Trigger OS/badge notifications for key events before the handler runs
        const raw = msg as unknown as Record<string, unknown>;
        switch (raw.type) {
            case "approval_required":
                notifyApprovalNeeded();
                break;
            case "tool_approval_required":
                notifyToolApprovalNeeded();
                break;
            case "complete": {
                const summary = (raw.summary as string | undefined) ?? "Task complete";
                notifyComplete(summary);
                break;
            }
            case "error":
                if (!(raw.recoverable as boolean | undefined)) {
                    notifyError((raw.message as string | undefined) ?? "Unknown error");
                }
                break;
        }

        const ctx: WsHandlerContext = {
            postMessage: (m) => this.postMessage(m),
            closeWebSocket: () => this.closeWebSocket(),
            clearSession: () => {
                this.lastCompletedSessionId = this.sessionId;
                this.sessionId = undefined;
                this.context.globalState.update(
                    "lean-ai.lastCompletedSessionId",
                    this.lastCompletedSessionId,
                );
            },
        };
        handleWsMessage(msg, ctx);
    }

    // ── Message routing ──────────────────────────────────────────────

    private async handleUserMessage(text: string): Promise<void> {
        // --- Slash command interception (before chat/agent routing) ---
        const trimmed = text.trim();
        const slashMatch = trimmed.match(/^(\/\w+)(?:\s+(.*))?$/s);
        if (slashMatch) {
            const command = slashMatch[1].toLowerCase();
            const args = slashMatch[2] || "";
            const handler = this.slashCommands.get(command);
            if (handler) {
                try {
                    await handler(args);
                } catch (e) {
                    const error = e instanceof Error ? e.message : String(e);
                    this.postMessage({ type: "error", text: error });
                }
                this.postMessage({ type: "sendEnabled" });
                return;
            }
            // Unknown slash command — fall through to normal chat
        }

        // If agent workflow is active (WebSocket open), send as WS message.
        // This is how plan feedback/rejection works — user types, it goes
        // over the existing WebSocket to the running workflow.
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.postMessage({ type: "thinking", show: true, text: "Sending feedback..." });
            this.ws.send(JSON.stringify({ type: "user_message", content: text, repo_root: this.getRepoRoot() }));
            return;
        }

        // Otherwise, default to chat mode
        this.postMessage({ type: "thinking", show: true, text: "Thinking..." });

        try {
            // Check backend health
            const healthy = await this.client.healthCheck();
            if (!healthy) {
                this.postMessage({ type: "thinking", show: false });
                this.postMessage({
                    type: "error",
                    text: "Backend not available. Start the server:\ncd backend && uvicorn lean_ai.main:app --reload --port 8422",
                });
                this.postMessage({ type: "sendEnabled" });
                return;
            }

            await this.handleChatMessage(text);
        } catch (e) {
            this.postMessage({ type: "thinking", show: false });
            const error = e instanceof Error ? e.message : String(e);
            this.postMessage({ type: "error", text: error });
            this.postMessage({ type: "sendEnabled" });
        }
    }

    // ── Chat mode: streaming LLM call with workspace context ─────────

    private async handleChatMessage(text: string): Promise<void> {
        const now = new Date().toISOString();

        // Append any enabled context pills to the outgoing message
        let chatText = text;
        if (this._includeProblems) {
            chatText += this.collectDiagnosticsContext('workspace');
        }
        if (this._includeDebug && vscode.debug.activeDebugSession) {
            chatText += await this.collectDebugContext();
        }

        // Add user message to history with timestamp (store original text, not augmented)
        this.chatHistory.push({ role: "user", content: text, timestamp: now });

        // Gather workspace context from VSCode
        const workspace = this.getWorkspaceContext();

        // Stream tokens from /api/chat/stream — strip timestamps before sending
        const historyForApi = this.chatHistory.slice(0, -1).map(({ role, content }) => ({ role, content }));

        let fullReply = "";
        let isFirst = true;
        let streamStartTime: number | null = null;
        let tokenCount = 0;

        await this.client.chatStream(chatText, historyForApi, workspace, (token) => {
            if (streamStartTime === null) { streamStartTime = Date.now(); }
            tokenCount++;
            fullReply += token;
            this.postMessage({ type: "chatToken", content: token, isFirst });
            isFirst = false;
        });

        // Compute tok/s from first-token to last-token (excludes context-gathering latency)
        const tps = (streamStartTime !== null && tokenCount > 0)
            ? Math.round(tokenCount / ((Date.now() - streamStartTime) / 1000) * 10) / 10
            : null;

        // Stream completed — update history and notify webview
        this.chatHistory.push({ role: "assistant", content: fullReply, timestamp: new Date().toISOString() });

        // Keep history manageable (last 40 messages = 20 exchanges)
        if (this.chatHistory.length > 40) {
            this.chatHistory = this.chatHistory.slice(-40);
        }

        // Send full text so the webview can apply markdown formatting, plus tok/s metrics
        this.postMessage({ type: "chatDone", fullText: fullReply, tps, evalCount: tokenCount });

        // Persist conversation after each exchange
        await this.conversations.persistCurrentConversation(this.chatHistory);
    }

    // ── Agent mode: full WebSocket FSM workflow ──────────────────────

    private async handleAgentMessage(text: string): Promise<void> {
        this.postMessage({ type: "thinking", show: true, text: "Starting workflow..." });

        // Get or create session
        const sessionId = await this.ensureSession();

        // Ensure WebSocket is connected
        const ws = this.ensureWebSocket(sessionId);

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
        if (this._includeProblems) {
            content += this.collectDiagnosticsContext('workspace');
        }
        if (this._includeDebug && vscode.debug.activeDebugSession) {
            content += await this.collectDebugContext();
        }

        // Send message over WebSocket — the workflow runs server-side
        ws.send(JSON.stringify({ type: "user_message", content, repo_root: this.getRepoRoot() }));
    }

    private handleApprove(): void {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            this.postMessage({ type: "error", text: "WebSocket not connected." });
            return;
        }

        this.postMessage({ type: "hideApproval" });
        this.postMessage({ type: "thinking", show: true, text: "Approving plan..." });
        this.ws.send(JSON.stringify({ type: "approve" }));
    }

    /** Provide session ID for approval commands (workflow mode) */
    getSessionId(): string | undefined {
        return this.sessionId;
    }

    /** Called by extension.ts when this window was opened for a freshly scaffolded project */
    setPendingInit(): void {
        this._pendingInit = true;
    }

    private getHtml(): string {
        const chatFontSize = vscode.workspace.getConfiguration("lean-ai").get<number>("chatFontSize", 13);
        return getWebviewHtml(chatFontSize);
    }
}

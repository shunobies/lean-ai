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
import type { Attachment, WSMessage } from "./types";
import { handleWsMessage } from "./wsHandler";
import type { WsHandlerContext } from "./wsHandler";
import { SettingsPanel } from "./settingsPanel";
import { BACKEND_SETTING_MAP, clearEnvSetting, resolveEnvFilePath, writeEnvSetting } from "./settingsSync";
import { addExtras } from "./backendInstaller";
import { restartBackend } from "./backendProcess";
import {
    notifyApprovalNeeded,
    notifyComplete,
    notifyError,
    notifyToolApprovalNeeded,
} from "./notifications";
import WebSocket from "ws";
import type { SessionTreeProvider } from "./sessionTreeProvider";

export class LeanAISidebarProvider implements vscode.WebviewViewProvider {
    public static readonly viewType = "lean-ai.chatView";

    private webviewView?: vscode.WebviewView;
    private detachedPanel?: vscode.WebviewPanel;
    private client: BackendClient;
    private sessionTreeProvider?: SessionTreeProvider;
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

    // Backup of the session ID set at workflow start — used by clearSession as
    // fallback when this.sessionId has already been cleared by the time the
    // "complete" WebSocket message arrives.
    private _wsSessionId: string | undefined;

    // Set by extension.ts when a scaffold was just created and this window is the new project
    private _pendingInit = false;

    // Context pill state — whether to append diagnostics/debug data to outgoing messages
    private _includeProblems = false;
    private _includeDebug = false;
    private _ttsEnabled = false;
    private _wakeWordActive = false;
    private _ttsSentenceQueue: string[] = [];
    private _ttsSpeaking = false;
    private _ttsCancelled = false;
    private _ttsAbortController: AbortController | undefined;
    private _ttsSentenceAccumulator = "";
    private _ttsInCodeFence = false;
    private _lastDebugStop?: { reason: string; text?: string; threadId?: number };
    private _currentStage: string | null = null;

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

        // Probe backend for vision and voice capabilities on startup
        this.client.healthCheck().then(async () => {
            this.postMessage({ type: "visionAvailable", available: this.client.visionAvailable });

            // Pre-load STT model in background so first voice use is instant
            if (this.client.sttAvailable) {
                this.client.sttWarmup();
            }

            // Detect voice settings enabled but deps not installed
            const voiceConfig = vscode.workspace.getConfiguration("lean-ai");
            const sttWanted = voiceConfig.get<boolean>("enableStt", false);
            const ttsWanted = voiceConfig.get<boolean>("enableTts", false);
            const wakeWanted = voiceConfig.get<boolean>("enableWakeWord", false);
            const anyWanted = sttWanted || ttsWanted || wakeWanted;
            const anyAvailable = this.client.sttAvailable
                || this.client.ttsAvailable
                || this.client.wakeWordAvailable;

            if (anyWanted && !anyAvailable) {
                // Voice settings enabled but deps missing — offer to install
                const choice = await vscode.window.showWarningMessage(
                    "Voice features are enabled but dependencies are not installed.",
                    "Install Now",
                    "Show Instructions",
                );
                if (choice === "Install Now") {
                    const ok = await addExtras(this.context, ["voice"]);
                    if (ok) {
                        vscode.window.showInformationMessage(
                            "Voice dependencies installed. Restarting backend...",
                        );
                        await restartBackend();
                        // Re-probe after restart
                        setTimeout(() => {
                            this.client.healthCheck().then(() => {
                                this._sendVoiceAvailable();
                            }).catch(() => {});
                        }, 3000);
                        return;
                    } else {
                        this._showVoiceSetupInstructions();
                    }
                } else if (choice === "Show Instructions") {
                    this._showVoiceSetupInstructions();
                }
                // Still missing — show hint in voice controls bar
                this._sendVoiceAvailable(true);
                return;
            }

            this._sendVoiceAvailable();

            // Mark setup complete — backend is healthy
            if (!this.context.globalState.get<boolean>("lean-ai.hasCompletedSetup")) {
                this.context.globalState.update("lean-ai.hasCompletedSetup", true);
            }

            // Auto-greet on fresh startup (no existing conversation, no pending workflow)
            if (this.chatHistory.length === 0 && !this.lastCompletedSessionId && !this._pendingInit) {
                this.sendGreeting();
            }
        }).catch(() => {
            // Backend not reachable — show first-boot setup if never completed
            if (!this.context.globalState.get<boolean>("lean-ai.hasCompletedSetup")) {
                setTimeout(() => this.showFirstBootSetup(), 500);
            }
        });

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
            await this.handleWebviewMessage(msg);
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
                        } else {
                            clearEnvSetting(envPath, envVar);
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
        // Track current workflow stage for webviewReady re-sync
        if (msg.type === "stage") {
            this._currentStage = (msg.stage as string) || null;
        }
        this.webviewView?.webview.postMessage(msg);
        this.detachedPanel?.webview.postMessage(msg);
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
                const sid = this.sessionId || this._wsSessionId;
                if (!sid) {
                    console.warn("[Lean AI] clearSession: no sessionId available");
                }
                this.lastCompletedSessionId = sid;
                this.sessionId = undefined;
                this._wsSessionId = undefined;
                this.context.globalState.update(
                    "lean-ai.lastCompletedSessionId",
                    this.lastCompletedSessionId,
                );
            },
            onTtsContent: this._ttsEnabled
                ? (text) => this.speakText(text)
                : undefined,
        };
        handleWsMessage(msg, ctx);
    }

    // ── Message routing ──────────────────────────────────────────────

    private async handleUserMessage(text: string, attachments?: Attachment[]): Promise<void> {
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
            // Notify webview of vision capability
            this.postMessage({ type: "visionAvailable", available: this.client.visionAvailable });
            if (!healthy) {
                this.postMessage({ type: "thinking", show: false });
                this.postMessage({
                    type: "error",
                    text: "Backend not available. Start the server:\ncd backend && uvicorn lean_ai.main:app --reload --port 8422",
                });
                this.postMessage({ type: "sendEnabled" });
                return;
            }

            await this.handleChatMessage(text, attachments);
        } catch (e) {
            this.postMessage({ type: "thinking", show: false });
            const error = e instanceof Error ? e.message : String(e);
            this.postMessage({ type: "error", text: error });
            this.postMessage({ type: "sendEnabled" });
        }
    }

    // ── Chat mode: streaming LLM call with workspace context ─────────

    private async handleChatMessage(text: string, attachments?: Attachment[]): Promise<void> {
        const now = new Date().toISOString();

        // Append any enabled context pills to the outgoing message
        let chatText = text;
        if (this._includeProblems) {
            chatText += this.collectDiagnosticsContext('workspace');
        }
        if (this._includeDebug && vscode.debug.activeDebugSession) {
            chatText += await this.collectDebugContext();
        }

        // Convert attachments to backend format
        const apiAttachments = attachments?.map(a => ({
            data: a.data,
            filename: a.filename,
            mime_type: a.mimeType,
        }));

        // Cancel any in-progress TTS from previous message
        if (this._ttsEnabled) { this._resetTtsAccumulator(); }

        // Add user message to history with timestamp (store original text, not augmented)
        this.chatHistory.push({ role: "user", content: text, timestamp: now });

        // Gather workspace context from VSCode
        const workspace = this.getWorkspaceContext();

        // Stream tokens from /api/chat/stream — strip timestamps before sending
        const historyForApi = this.chatHistory.slice(0, -1).map(({ role, content }) => ({ role, content }));
        const userName = vscode.workspace.getConfiguration("lean-ai").get<string>("userName", "") || undefined;

        let fullReply = "";
        let isFirst = true;
        let streamStartTime: number | null = null;
        let tokenCount = 0;
        this.sessionTreeProvider?.pauseRefresh();
        let receivedDone = false;
        try {
            ({ receivedDone } = await this.client.chatStream(chatText, historyForApi, workspace, (token) => {
                if (streamStartTime === null) { streamStartTime = Date.now(); }
                tokenCount++;
                fullReply += token;
                this.postMessage({ type: "chatToken", content: token, isFirst });
                isFirst = false;
                if (this._ttsEnabled) { this._feedTtsToken(token); }
            }, apiAttachments, (thinkingToken) => {
                this.postMessage({ type: "llmThinking", text: thinkingToken, streaming: true });
            }, userName, undefined, (desc) => {
                this.postMessage({
                    type: "reply",
                    text: `<details class="vision-desc"><summary>Vision model description</summary>\n\n${desc}\n</details>`,
                    cls: "msg-system",
                });
            }));
        } finally {
            this.sessionTreeProvider?.resumeRefresh();
        }

        // TTS: flush any remaining accumulated text
        if (this._ttsEnabled && this._ttsSentenceAccumulator.trim()) {
            const remainder = LeanAISidebarProvider._stripCodeForTts(this._ttsSentenceAccumulator);
            if (remainder) { this.speakSentence(remainder); }
            this._ttsSentenceAccumulator = "";
            this._ttsInCodeFence = false;
        }

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
        const truncated = !receivedDone && tokenCount > 0;
        this.postMessage({ type: "chatDone", fullText: fullReply, tps, evalCount: tokenCount, truncated });

        // Persist conversation after each exchange
        await this.conversations.persistCurrentConversation(this.chatHistory);
    }

    // ── Startup greeting: LLM greets user on fresh chat ────────────

    private async sendGreeting(): Promise<void> {
        const config = vscode.workspace.getConfiguration("lean-ai");
        const userName = config.get<string>("userName", "") || undefined;
        const workspace = this.getWorkspaceContext();
        const projectName = workspace?.workspace_name || "your project";

        const greetingPrompt = userName
            ? `Greet ${userName} warmly and ask what we're going to work on in ${projectName} today. Keep it brief and friendly — one or two sentences.`
            : `Greet the user warmly and ask what we're going to work on in ${projectName} today. Keep it brief and friendly — one or two sentences.`;

        let fullReply = "";
        let isFirst = true;
        if (this._ttsEnabled) { this._resetTtsAccumulator(); }

        try {
            await this.client.chatStream(
                greetingPrompt, [], workspace,
                (token) => {
                    fullReply += token;
                    this.postMessage({ type: "chatToken", content: token, isFirst });
                    isFirst = false;
                    if (this._ttsEnabled) { this._feedTtsToken(token); }
                },
                undefined,
                (thinkingToken) => {
                    this.postMessage({ type: "llmThinking", text: thinkingToken, streaming: true });
                },
                userName,
                true, // skipWebSearch — greeting doesn't need internet
            );
        } catch {
            // Greeting failed — not critical, user can type
            return;
        }

        if (fullReply.trim()) {
            this.chatHistory.push({
                role: "assistant",
                content: fullReply,
                timestamp: new Date().toISOString(),
            });
            this.postMessage({ type: "chatDone", fullText: fullReply });

            // TTS: flush any remaining accumulated text
            if (this._ttsEnabled && this._ttsSentenceAccumulator.trim()) {
                const remainder = LeanAISidebarProvider._stripCodeForTts(this._ttsSentenceAccumulator);
                if (remainder) { this.speakSentence(remainder); }
                this._ttsSentenceAccumulator = "";
                this._ttsInCodeFence = false;
            }
        }
    }

    // ── First-boot setup guide ───────────────────────────────────────

    private showFirstBootSetup(): void {
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

        this.postMessage({
            type: "setupGuide",
            platform,
            installCmd,
            pullCmd: `ollama pull ${model}`,
            model,
        });
    }

    // ── Agent mode: full WebSocket FSM workflow ──────────────────────

    private async handleAgentMessage(text: string): Promise<void> {
        this.postMessage({ type: "thinking", show: true, text: "Starting workflow..." });

        // Get or create session
        const sessionId = await this.ensureSession();
        this._wsSessionId = sessionId;

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

    /** Called by extension.ts to allow pausing session tree refresh during chat. */
    setSessionTreeProvider(provider: SessionTreeProvider): void {
        this.sessionTreeProvider = provider;
    }

    private getHtml(): string {
        const chatFontSize = vscode.workspace.getConfiguration("lean-ai").get<number>("chatFontSize", 13);
        return getWebviewHtml(chatFontSize);
    }

    // ── Webview message handler (shared by sidebar + detached panel) ──

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    private async handleWebviewMessage(msg: any): Promise<void> {
        switch (msg.type) {
            case "sendMessage":
                try {
                    await this.handleUserMessage(msg.text, msg.attachments);
                } catch {
                    this.postMessage({ type: "sendEnabled" });
                }
                break;
            case "sendToAgent":
                this.postMessage({
                    type: "reply",
                    text: "Sending to agent...",
                    cls: "msg-system",
                });
                await this.handleAgentMessage(msg.text as string);
                break;
            case "newChat": {
                await this.conversations.persistCurrentConversation(this.chatHistory);
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
                this._wsSessionId = undefined;
                this.lastCompletedSessionId = undefined;
                this.context.globalState.update("lean-ai.lastCompletedSessionId", undefined);
                this.chatHistory = [];
                this.conversations.currentConversationId = undefined;
                this.conversations.viewingHistoricConversation = false;
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
                if (this.chatHistory.length > 0) {
                    this.conversations.handleBackToCurrentChat(this.chatHistory);
                }
                this.sendDiagnosticsUpdate();
                {
                    const dbgSession = vscode.debug.activeDebugSession;
                    this.postMessage({ type: "debugStateUpdate", active: !!dbgSession, name: dbgSession?.name });
                }
                // Re-send voice/vision state (fixes pop-out timing race)
                this._sendVoiceAvailable();
                this.postMessage({ type: "visionAvailable", available: this.client.visionAvailable });
                // Re-send workflow stage so stop button appears if active
                if (this._currentStage) {
                    this.postMessage({ type: "stage", stage: this._currentStage });
                }
                break;
            case "approve_tool":
            case "deny_tool":
                if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                    this.ws.send(JSON.stringify({
                        type: msg.type,
                        token: msg.token as string,
                    }));
                }
                break;
            case "stopWorkflow":
                if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                    this.ws.send(JSON.stringify({ type: "cancel" }));
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
            case "openExternal":
                vscode.env.openExternal(vscode.Uri.parse(msg.url as string));
                break;
            case "sendToTerminal": {
                const terminal = vscode.window.activeTerminal
                    || vscode.window.createTerminal("Lean AI");
                terminal.show();
                terminal.sendText(msg.code as string);
                break;
            }
            case "openChatInNewWindow":
                this.openChatInNewWindow();
                break;
            case "returnToSidebar":
                if (this.detachedPanel) {
                    this.detachedPanel.dispose();
                }
                // Re-open the secondary sidebar and focus the chat view
                vscode.commands.executeCommand("workbench.action.focusAuxiliaryBar");
                vscode.commands.executeCommand("lean-ai.chatView.focus");
                break;
            case "openFileDiff":
                this.openFileDiff(
                    msg.file as string,
                    msg.baseBranch as string,
                );
                break;
            // --- Voice ---
            case "sttStart":
                this.handleSttStart(!!(msg.autoStop));
                break;
            case "sttStop":
                this.handleSttStop();
                break;
            case "voiceToggle":
                this.handleVoiceToggle(msg.feature as string, !!(msg.enabled));
                break;
            case "ttsStop":
                // Kill the entire TTS pipeline — sentence queue + active stream
                this._ttsCancelled = true;
                this._ttsSentenceQueue = [];
                this._ttsSpeaking = false;
                this._ttsSentenceAccumulator = "";
                this._ttsInCodeFence = false;
                if (this._ttsAbortController) {
                    this._ttsAbortController.abort();
                    this._ttsAbortController = undefined;
                }
                break;
            case "voiceConfigChange":
                if (msg.voice) {
                    this.client.voiceConfig(msg.voice as string).catch(() => {});
                }
                if (msg.speed) {
                    this.client.voiceConfig(undefined, msg.speed as number).catch(() => {});
                }
                break;
            case "voiceShowSetup":
                this._showVoiceSetupInstructions();
                break;
        }
    }

    // ── Detached chat panel ──────────────────────────────────────────

    openChatInNewWindow(): void {
        if (this.detachedPanel) {
            this.detachedPanel.reveal(vscode.ViewColumn.Two);
            return;
        }

        const panel = vscode.window.createWebviewPanel(
            "lean-ai.chatPanel",
            "Lean AI Chat",
            vscode.ViewColumn.Two,
            {
                enableScripts: true,
                retainContextWhenHidden: true,
            },
        );

        this.detachedPanel = panel;
        panel.webview.html = this.getHtml();

        panel.webview.onDidReceiveMessage(async (msg) => {
            await this.handleWebviewMessage(msg);
        });

        panel.onDidDispose(() => {
            this.detachedPanel = undefined;
        });

        // Tell the pop-out to show the "return to sidebar" button
        panel.webview.postMessage({ type: "setViewMode", mode: "popout" });

        // Close the secondary sidebar since the chat is now in the pop-out panel
        vscode.commands.executeCommand("workbench.action.closeAuxiliaryBar");
    }

    // ── Voice helpers ───────────────────────────────────────────────

    private async handleSttStart(autoStop: boolean): Promise<void> {
        try {
            await this.client.sttStart(autoStop);
        } catch (err) {
            this.postMessage({ type: "reply", text: `STT start failed: ${err}`, cls: "msg-error" });
            this.postMessage({ type: "sttRecording", active: false });
        }
    }

    private async handleSttStop(): Promise<void> {
        try {
            const result = await this.client.sttStop();
            const autoSubmit = vscode.workspace.getConfiguration("lean-ai")
                .get<boolean>("wakeWordAutoSubmit", false);
            this.postMessage({ type: "sttResult", text: result.text, autoSubmit });
        } catch (err) {
            this.postMessage({ type: "reply", text: `STT failed: ${err}`, cls: "msg-error" });
            this.postMessage({ type: "sttRecording", active: false });
        }
    }

    private async handleVoiceToggle(feature: string, enabled: boolean): Promise<void> {
        if (feature === "tts") {
            this._ttsEnabled = enabled;
        } else if (feature === "stt") {
            // STT toggle is UI-only (mic button visibility)
        } else if (feature === "wakeWord") {
            if (enabled) {
                try {
                    await this.client.wakeWordStart();
                    this.client.connectVoiceEvents(
                        () => this.postMessage({ type: "wakeWordDetected" }),
                        () => this.postMessage({ type: "sttAutoStopped" }),
                        () => {
                            this._wakeWordActive = false;
                            this.postMessage({ type: "reply", text: "Wake word listener stopped unexpectedly.", cls: "msg-error" });
                            this.postMessage({ type: "wakeWordStopped" });
                        },
                    );
                    this._wakeWordActive = true;
                } catch (err) {
                    this.postMessage({ type: "reply", text: `Wake word failed: ${err}`, cls: "msg-error" });
                }
            } else {
                try {
                    await this.client.wakeWordStop();
                    this.client.disconnectVoiceEvents();
                    this._wakeWordActive = false;
                } catch {
                    // Best effort
                }
            }
        }
    }

    /** Strip markdown formatting, special chars, and non-prose content so TTS reads naturally. */
    private static _stripCodeForTts(text: string): string {
        // Remove fenced code blocks (```...```)
        let cleaned = text.replace(/```[\s\S]*?```/g, "");
        // Remove inline code (`...`)
        cleaned = cleaned.replace(/`[^`]+`/g, "");
        // Remove HTML tags (<br>, <div>, etc.)
        cleaned = cleaned.replace(/<[^>]+>/g, "");
        // Remove markdown image syntax ![alt](url) before link syntax
        cleaned = cleaned.replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1");
        // Remove markdown link syntax [text](url) → keep text
        cleaned = cleaned.replace(/\[([^\]]*)\]\([^)]*\)/g, "$1");
        // Remove footnote references [^1], [^note]
        cleaned = cleaned.replace(/\[\^[^\]]*\]/g, "");
        // Remove raw URLs (https://..., http://..., www.)
        cleaned = cleaned.replace(/https?:\/\/\S+/g, "");
        cleaned = cleaned.replace(/www\.\S+/g, "");
        // Remove markdown bold/italic markers (* and _)
        cleaned = cleaned.replace(/[*_]{1,3}/g, "");
        // Remove markdown table pipes and heading markers
        cleaned = cleaned.replace(/[|#]/g, "");
        // Remove markdown horizontal rules (--- or ***)
        cleaned = cleaned.replace(/^[-*]{3,}\s*$/gm, "");
        // Remove blockquote markers
        cleaned = cleaned.replace(/^\s*>\s*/gm, "");
        // Remove bullet markers (-, +, numbered lists)
        cleaned = cleaned.replace(/^\s*[-+]\s+/gm, "");
        cleaned = cleaned.replace(/^\s*\d+\.\s+/gm, "");
        // Replace arrows with natural words
        cleaned = cleaned.replace(/-->/g, " to ");
        cleaned = cleaned.replace(/=>/g, " to ");
        cleaned = cleaned.replace(/->/g, " to ");
        cleaned = cleaned.replace(/<--/g, " from ");
        cleaned = cleaned.replace(/<-/g, " from ");
        // Replace & with "and"
        cleaned = cleaned.replace(/\s&\s/g, " and ");
        // Replace triple dots with a pause-friendly comma
        cleaned = cleaned.replace(/\.{3}/g, ",");
        // Remove remaining special characters that TTS reads literally
        cleaned = cleaned.replace(/[~^\\{}[\]<>]/g, "");
        // Remove emoji (Unicode emoji ranges)
        cleaned = cleaned.replace(/[\u{1F600}-\u{1F64F}\u{1F300}-\u{1F5FF}\u{1F680}-\u{1F6FF}\u{1F1E0}-\u{1F1FF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}\u{FE00}-\u{FE0F}\u{1F900}-\u{1F9FF}\u{1FA00}-\u{1FA6F}\u{1FA70}-\u{1FAFF}\u{200D}\u{20E3}\u{E0020}-\u{E007F}]/gu, "");
        // Collapse multiple blank lines/spaces into one space
        cleaned = cleaned.replace(/\n{2,}/g, " ");
        cleaned = cleaned.replace(/\s{2,}/g, " ");
        return cleaned.trim();
    }

    /** Speak text aloud via TTS. Prefers raw PCM streaming for lower latency. */
    async speakText(text: string): Promise<void> {
        try {
            const cleaned = LeanAISidebarProvider._stripCodeForTts(text);
            if (!cleaned || this._ttsCancelled) { return; }

            // Create an AbortController so the stop button can kill the active fetch
            const ac = new AbortController();
            this._ttsAbortController = ac;

            // Always use PCM streaming for lower latency — falls back to WAV on error
            try {
                await this.client.ttsStreamPcm(cleaned, undefined, undefined, (pcmBase64, sampleRate) => {
                    this.postMessage({ type: "ttsAudio", audio: pcmBase64, isPcm: true, sampleRate });
                }, ac.signal);
            } catch (e) {
                if (ac.signal.aborted) { return; } // User pressed stop — not an error
                // Fallback: WAV streaming or non-streaming
                if (cleaned.length < 500) {
                    const result = await this.client.ttsSynthesize(cleaned, undefined, undefined, ac.signal);
                    if (result.audio_base64) {
                        this.postMessage({ type: "ttsAudio", audio: result.audio_base64 });
                    }
                } else {
                    await this.client.ttsStream(cleaned, undefined, undefined, (chunk) => {
                        this.postMessage({ type: "ttsAudio", audio: chunk });
                    }, ac.signal);
                }
            }
        } catch (err) {
            console.warn("[Lean AI] TTS failed:", err);
        }
    }

    /** Queue a sentence for sequential TTS playback. Fire-and-forget safe. */
    speakSentence(text: string): void {
        if (this._ttsCancelled) { return; }
        this._ttsSentenceQueue.push(text);
        if (!this._ttsSpeaking) {
            this._ttsCancelled = false;
            this._processTtsSentenceQueue();
        }
    }

    private async _processTtsSentenceQueue(): Promise<void> {
        if (this._ttsSpeaking) { return; }
        this._ttsSpeaking = true;
        try {
            while (this._ttsSentenceQueue.length > 0 && !this._ttsCancelled) {
                const sentence = this._ttsSentenceQueue.shift()!;
                await this.speakText(sentence);
            }
        } finally {
            this._ttsSpeaking = false;
        }
    }

    /** Feed a streaming token into the TTS sentence accumulator.
     *  Detects sentence boundaries and dispatches complete sentences to speakSentence(). */
    private _feedTtsToken(token: string): void {
        this._ttsSentenceAccumulator += token;

        // Track code fences — don't speak code blocks
        const fenceCount = (token.match(/```/g) || []).length;
        if (fenceCount % 2 !== 0) {
            this._ttsInCodeFence = !this._ttsInCodeFence;
        }
        if (this._ttsInCodeFence) { return; }

        // Check for sentence boundaries in the accumulated text
        const sentenceEndPattern = /(?<=[.!?])\s+/;
        const match = sentenceEndPattern.exec(this._ttsSentenceAccumulator);
        if (!match) { return; }

        // Extract all complete sentences
        let remaining = this._ttsSentenceAccumulator;
        let lastEnd = 0;
        const re = /(?<=[.!?])\s+/g;
        let m: RegExpExecArray | null;
        while ((m = re.exec(remaining)) !== null) {
            const sentence = remaining.slice(lastEnd, m.index).trim();
            if (sentence) {
                // Strip fenced code blocks and inline code
                const cleaned = LeanAISidebarProvider._stripCodeForTts(sentence);
                if (cleaned) { this.speakSentence(cleaned); }
            }
            lastEnd = m.index + m[0].length;
        }
        this._ttsSentenceAccumulator = remaining.slice(lastEnd);
    }

    /** Reset TTS accumulator state for a fresh streaming session. */
    private _resetTtsAccumulator(): void {
        this._ttsSentenceAccumulator = "";
        this._ttsInCodeFence = false;
        this._ttsSentenceQueue = [];
        this._ttsSpeaking = false;
        this._ttsCancelled = false;
        if (this._ttsAbortController) {
            this._ttsAbortController.abort();
            this._ttsAbortController = undefined;
        }
        this.postMessage({ type: "ttsStopPlayback" });
        this.postMessage({ type: "ttsClearReplay" });
    }

    /** Send voiceAvailable + voice list to the webview. */
    private _sendVoiceAvailable(setupNeeded = false): void {
        // Sync provider TTS state with backend availability
        if (this.client.ttsAvailable) {
            this._ttsEnabled = true;
        }

        this.postMessage({
            type: "voiceAvailable",
            stt: this.client.sttAvailable,
            tts: this.client.ttsAvailable,
            wakeWord: this.client.wakeWordAvailable,
            setupNeeded,
        });
        if (this.client.ttsAvailable) {
            const voiceConfig = vscode.workspace.getConfiguration("lean-ai");
            this.client.listVoices().then((voices) => {
                this.postMessage({
                    type: "voiceVoices",
                    voices,
                    current: voiceConfig.get<string>("ttsVoice", "af_heart"),
                });
            }).catch(() => {});

            // Proactively download TTS model files if not yet cached
            (async () => {
                try {
                    const result = await vscode.window.withProgress(
                        {
                            location: vscode.ProgressLocation.Notification,
                            title: "Downloading TTS models (~310MB)...",
                            cancellable: false,
                        },
                        () => this.client.ensureTtsModels(),
                    );
                    if (result.downloaded) {
                        vscode.window.showInformationMessage(
                            "TTS models downloaded. Voice is ready!",
                        );
                    }
                } catch { /* best effort */ }
            })();
        }
    }

    /** Show voice setup instructions in the chat panel. */
    private _showVoiceSetupInstructions(): void {
        const isLinux = process.platform === "linux";
        const isMac = process.platform === "darwin";
        const portaudioCmd = isLinux
            ? "sudo apt install portaudio19-dev"
            : isMac
                ? "brew install portaudio"
                : "Install portaudio for your platform";

        const instructions = [
            "**Voice Setup Instructions:**",
            "",
            "1. Install system dependency (needed for STT & wake word):",
            "```",
            portaudioCmd,
            "```",
            "",
            "2. Run the **Lean AI: Reinstall Backend** command from the",
            "   Command Palette, or manually run in the backend venv:",
            "```",
            "pip install lean-ai[voice]",
            "```",
            "",
            "3. Restart the backend",
        ].join("\n");

        this.postMessage({ type: "reply", text: instructions, cls: "msg-ai" });
    }
}

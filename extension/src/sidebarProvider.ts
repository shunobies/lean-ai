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
import { PromptsPanel } from "./promptsPanel";
import { NotesPanel } from "./notesPanel";
import {
    notifyApprovalNeeded,
    notifyComplete,
    notifyError,
    notifyToolApprovalNeeded,
} from "./notifications";
import WebSocket from "ws";
import type { SessionTreeProvider } from "./sessionTreeProvider";

// Extracted modules
import type { VoiceContext } from "./sidebarVoice";
import {
    handleSttStart,
    handleSttStop,
    handleVoiceToggle,
    probeVoiceOnStartup,
    sendVoiceAvailable,
    showVoiceSetupInstructions,
    speakText,
    stopTts,
} from "./sidebarVoice";
import type { ChatContext } from "./sidebarChat";
import {
    collectDiagnosticsContext,
    handleAgentMessage,
    handleApprove,
    handleConfigurationChange,
    handleNewChat,
    handleUserMessage,
    sendDiagnosticsUpdate,
    sendGreeting,
    showFirstBootSetup,
} from "./sidebarChat";

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
            handleAgentMessage: (text) => handleAgentMessage(this.chatCtx(), text),
            getWs: () => this.ws,
            getLastCompletedSessionId: () => this.lastCompletedSessionId,
            setSessionId: (id) => { this.sessionId = id; },
            setLastCompletedSessionId: (id) => {
                this.lastCompletedSessionId = id;
            },
            extensionContext: this.context,
            getFileDiagnostics: () => collectDiagnosticsContext('file'),
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
        this.client.healthCheck().then(() => {
            probeVoiceOnStartup({
                voiceCtx: this.voiceCtx(),
                extensionContext: this.context,
                postMessage: (msg) => this.postMessage(msg),
                onReady: () => {
                    if (this.chatHistory.length === 0 && !this.lastCompletedSessionId && !this._pendingInit) {
                        sendGreeting(this.chatCtx());
                    }
                },
            });
        }).catch(() => {
            // Backend not reachable — show first-boot setup if never completed
            if (!this.context.globalState.get<boolean>("lean-ai.hasCompletedSetup")) {
                setTimeout(() => showFirstBootSetup((msg) => this.postMessage(msg)), 500);
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

        // Live font-size + backend settings sync
        vscode.workspace.onDidChangeConfiguration(async (e) => {
            await handleConfigurationChange(
                e, this.context.globalStorageUri.fsPath,
                (msg) => this.postMessage(msg),
            );
        }, null, this.context.subscriptions);

        // Live diagnostics count updates -> Problems pill in webview
        vscode.languages.onDidChangeDiagnostics(() => {
            sendDiagnosticsUpdate((msg) => this.postMessage(msg));
        }, null, this.context.subscriptions);

        // Debug session changes -> show/hide Debug pill in webview
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
            (msg) => this.onWsMessage(msg),
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

    private onWsMessage(msg: WSMessage): void {
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
                ? (text) => speakText(this.voiceCtx(), text)
                : undefined,
        };
        handleWsMessage(msg, ctx);
    }

    // ── Context builders for extracted modules ───────────────────

    private voiceCtx(): VoiceContext {
        // eslint-disable-next-line @typescript-eslint/no-this-alias
        const self = this;
        return {
            client: this.client,
            postMessage: (msg) => this.postMessage(msg),
            get ttsEnabled() { return self._ttsEnabled; },
            set ttsEnabled(v) { self._ttsEnabled = v; },
            get wakeWordActive() { return self._wakeWordActive; },
            set wakeWordActive(v) { self._wakeWordActive = v; },
            get ttsSentenceQueue() { return self._ttsSentenceQueue; },
            set ttsSentenceQueue(v) { self._ttsSentenceQueue = v; },
            get ttsSpeaking() { return self._ttsSpeaking; },
            set ttsSpeaking(v) { self._ttsSpeaking = v; },
            get ttsCancelled() { return self._ttsCancelled; },
            set ttsCancelled(v) { self._ttsCancelled = v; },
            get ttsAbortController() { return self._ttsAbortController; },
            set ttsAbortController(v) { self._ttsAbortController = v; },
            get ttsSentenceAccumulator() { return self._ttsSentenceAccumulator; },
            set ttsSentenceAccumulator(v) { self._ttsSentenceAccumulator = v; },
            get ttsInCodeFence() { return self._ttsInCodeFence; },
            set ttsInCodeFence(v) { self._ttsInCodeFence = v; },
        };
    }

    private chatCtx(): ChatContext {
        return {
            client: this.client,
            postMessage: (msg) => this.postMessage(msg),
            getRepoRoot: () => this.getRepoRoot(),
            ensureSession: () => this.ensureSession(),
            ensureWebSocket: (sid) => this.ensureWebSocket(sid),
            getWs: () => this.ws,
            setWsSessionId: (id) => { this._wsSessionId = id; },
            closeWebSocket: () => this.closeWebSocket(),
            resetSessionState: () => {
                this.sessionId = undefined;
                this._wsSessionId = undefined;
                this.lastCompletedSessionId = undefined;
                this.context.globalState.update("lean-ai.lastCompletedSessionId", undefined);
            },
            chatHistory: this.chatHistory,
            conversations: this.conversations,
            sessionTreeProvider: this.sessionTreeProvider,
            includeProblems: this._includeProblems,
            includeDebug: this._includeDebug,
            slashCommands: this.slashCommands,
            voiceCtx: () => this.voiceCtx(),
            lastDebugStop: this._lastDebugStop,
        };
    }

    // ── Webview message handler (shared by sidebar + detached panel) ──

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    private async handleWebviewMessage(msg: any): Promise<void> {
        switch (msg.type) {
            case "sendMessage":
                try {
                    await handleUserMessage(this.chatCtx(), msg.text, msg.attachments);
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
                await handleAgentMessage(this.chatCtx(), msg.text as string);
                break;
            case "newChat":
                await handleNewChat(this.chatCtx(), msg.messagesHtml as string | undefined);
                break;
            case "approve":
                handleApprove(this.chatCtx());
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
                sendDiagnosticsUpdate((m) => this.postMessage(m));
                {
                    const dbgSession = vscode.debug.activeDebugSession;
                    this.postMessage({ type: "debugStateUpdate", active: !!dbgSession, name: dbgSession?.name });
                }
                // Re-send voice/vision state (fixes pop-out timing race)
                sendVoiceAvailable(this.voiceCtx());
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
            case "openEditPrompts":
                PromptsPanel.createOrShow(this.context);
                break;
            case "openNotes":
                NotesPanel.createOrShow(this.context);
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
                handleSttStart(this.voiceCtx(), !!(msg.autoStop));
                break;
            case "sttStop":
                handleSttStop(this.voiceCtx());
                break;
            case "voiceToggle":
                handleVoiceToggle(this.voiceCtx(), msg.feature as string, !!(msg.enabled));
                break;
            case "ttsStop":
                stopTts(this.voiceCtx());
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
                showVoiceSetupInstructions(this.voiceCtx());
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

        // Move the editor tab to a separate OS window
        vscode.commands.executeCommand("workbench.action.moveEditorToNewWindow");
    }

    // ── Public accessors ─────────────────────────────────────────────

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
}

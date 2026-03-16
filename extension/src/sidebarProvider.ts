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

import * as vscode from "vscode";
import { BackendClient } from "./backendClient";
import { ConversationManager } from "./conversationManager";
import { getWebviewHtml } from "./sidebarHtml";
import { createSlashCommands } from "./slashCommands";
import type { SlashCommandContext } from "./slashCommands";
import type { WSMessage } from "./types";
import { handleWsMessage } from "./wsHandler";
import type { WsHandlerContext } from "./wsHandler";
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

        // Preserve JS/DOM state when the panel is hidden (e.g. Sessions expands to fill
        // the sidebar, or user collapses Chat). Without this, VSCode destroys the webview
        // context and all in-memory message history is lost.
        webviewView.options = { retainContextWhenHidden: true };

        webviewView.webview.options = {
            enableScripts: true,
            localResourceRoots: [this.extensionUri],
        };

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
            }
        });

        // Live font-size updates when user changes the setting
        vscode.workspace.onDidChangeConfiguration(e => {
            if (e.affectsConfiguration("lean-ai.chatFontSize")) {
                const newSize = vscode.workspace.getConfiguration("lean-ai").get<number>("chatFontSize", 13);
                this.postMessage({ type: "setFontSize", size: newSize });
            }
        });

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

    // ── Chat mode: direct LLM call with workspace context ────────────

    private async handleChatMessage(text: string): Promise<void> {
        const now = new Date().toISOString();

        // Add user message to history with timestamp
        this.chatHistory.push({ role: "user", content: text, timestamp: now });

        // Gather workspace context from VSCode
        const workspace = this.getWorkspaceContext();

        // Call the /api/chat endpoint — strip timestamps before sending
        const historyForApi = this.chatHistory.slice(0, -1).map(({ role, content }) => ({ role, content }));
        const result = await this.client.chat(text, historyForApi, workspace);
        const { reply, tokens_per_second: tps, eval_count: evalCount } = result;

        // Add assistant reply to history with timestamp
        this.chatHistory.push({ role: "assistant", content: reply, timestamp: new Date().toISOString() });

        // Keep history manageable (last 40 messages = 20 exchanges)
        if (this.chatHistory.length > 40) {
            this.chatHistory = this.chatHistory.slice(-40);
        }

        // Show the reply, then a small tok/s footer matching the agent workflow style
        this.postMessage({ type: "thinking", show: false });
        this.postMessage({ type: "reply", text: reply, cls: "msg-ai" });
        if (tps != null) {
            const countStr = evalCount != null ? ` · ${evalCount.toLocaleString()} tokens` : "";
            this.postMessage({
                type: "reply",
                text: `*${tps} tok/s${countStr}*`,
                cls: "msg-system",
            });
        }
        this.postMessage({ type: "sendEnabled" });

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

        // Send message over WebSocket — the workflow runs server-side
        ws.send(JSON.stringify({ type: "user_message", content: text, repo_root: this.getRepoRoot() }));
    }

    private handleApprove(): void {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            this.postMessage({ type: "error", text: "WebSocket not connected." });
            return;
        }

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

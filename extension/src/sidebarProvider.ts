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
import { restartBackend } from "./backendProcess";
import { getWebviewHtml } from "./sidebarHtml";
import type { StoredConversation, WSMessage } from "./types";
import { handleWsMessage } from "./wsHandler";
import type { WsHandlerContext } from "./wsHandler";
import WebSocket from "ws";

export class LeanAISidebarProvider implements vscode.WebviewViewProvider {
    public static readonly viewType = "lean-ai.chatView";

    private static readonly STORAGE_KEY = "lean-ai.chatConversations";
    private static readonly MAX_CONVERSATIONS = 200;
    private static readonly MAX_MESSAGES_PER_CONVERSATION = 100;

    private webviewView?: vscode.WebviewView;
    private client: BackendClient;
    private sessionId: string | undefined;
    private ws: WebSocket | undefined;

    // Chat mode state — includes timestamps for persistence
    private chatHistory: Array<{ role: string; content: string; timestamp: string }> = [];

    // Conversation persistence
    private currentConversationId: string | undefined;
    private viewingHistoricConversation = false;

    // Slash command registry
    private slashCommands: Map<string, (args: string) => Promise<void>>;

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

        // Register slash commands
        this.slashCommands = new Map();
        this.slashCommands.set("/init",     (args) => this.handleInitCommand(args));
        this.slashCommands.set("/scaffold", (args) => this.handleScaffoldCommand(args));
        this.slashCommands.set("/agent",    (args) => this.handleAgentCommand(args));
        this.slashCommands.set("/fix",      (args) => this.handleFixCommand(args));
        this.slashCommands.set("/reboot",   (args) => this.handleRebootCommand(args));
        this.slashCommands.set("/approve",  (args) => this.handleApproveCommand(args));
        this.slashCommands.set("/reject",   (args) => this.handleRejectCommand(args));
        this.slashCommands.set("/resume",   (args) => this.handleResumeCommand(args));
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
                case "newChat":
                    // Persist current conversation before clearing
                    await this.persistCurrentConversation();
                    this.closeWebSocket();
                    this.sessionId = undefined;
                    this.lastCompletedSessionId = undefined;
                    this.context.globalState.update("lean-ai.lastCompletedSessionId", undefined);
                    this.chatHistory = [];
                    this.currentConversationId = undefined;
                    this.viewingHistoricConversation = false;
                    this.postMessage({ type: "chatReset" });
                    break;
                case "approve":
                    this.handleApprove();
                    break;
                case "searchConversations":
                    this.handleSearchConversations(msg.query as string);
                    break;
                case "loadConversation":
                    this.handleLoadConversation(msg.id as string);
                    break;
                case "backToCurrentChat":
                    this.handleBackToCurrentChat();
                    break;
                case "webviewReady":
                    // Webview (re)initialised — replay chat history so messages aren't
                    // lost when the panel is destroyed and recreated (e.g. open-in-editor).
                    if (this.chatHistory.length > 0) {
                        this.handleBackToCurrentChat();
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
            this.persistCurrentConversation();
            this.webviewView = undefined;
        });

        // If this window was opened for a freshly scaffolded project, auto-run /init
        if (this._pendingInit) {
            this._pendingInit = false;
            // Delay slightly so the webview HTML has time to fully initialize
            setTimeout(() => this.handleInitCommand(""), 1000);
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
        await this.persistCurrentConversation();
    }

    // ── Slash command: /init — workspace indexing + project context ──

    private async handleInitCommand(args: string): Promise<void> {
        // Check backend health first
        this.postMessage({ type: "thinking", show: true, text: "Checking backend..." });

        const healthy = await this.client.healthCheck();
        if (!healthy) {
            this.postMessage({ type: "thinking", show: false });
            this.postMessage({
                type: "error",
                text: "Backend not available. Start the server:\ncd backend && uvicorn lean_ai.main:app --reload --port 8422",
            });
            return;
        }

        // Parse flags
        const force = args.includes("--force");
        const repoRoot = this.getRepoRoot();
        let anyFailure = false;

        // ── Step 1: Index workspace (fast — local file I/O) ──
        this.postMessage({
            type: "thinking",
            show: true,
            text: "Indexing workspace...",
        });

        try {
            const indexResult = await this.client.indexWorkspace(repoRoot, force);

            if (indexResult.index_status === "failed") {
                anyFailure = true;
                this.postMessage({
                    type: "reply",
                    text: "Indexing failed. The workspace search index could not be built.",
                    cls: "msg-system",
                });
            } else {
                const fileCount = indexResult.index_file_count ?? "?";
                const chunkCount = indexResult.index_chunk_count ?? "?";
                const mode = indexResult.index_status === "already_indexed"
                    ? "already up to date"
                    : "complete";
                this.postMessage({
                    type: "reply",
                    text: `Search index ${mode}: ${fileCount} files, ${chunkCount} chunks.`,
                    cls: "msg-system",
                });
            }
        } catch (e) {
            anyFailure = true;
            const error = e instanceof Error ? e.message : String(e);
            this.postMessage({
                type: "reply",
                text: `Indexing failed: ${error}`,
                cls: "msg-system",
            });
        }

        // ── Step 2: Generate project context (slow — LLM call) ──
        // Show elapsed time so the user knows it's still working
        const startTime = Date.now();
        const ticker = setInterval(() => {
            const elapsed = Math.floor((Date.now() - startTime) / 1000);
            const mins = Math.floor(elapsed / 60);
            const secs = elapsed % 60;
            const timeStr = mins > 0
                ? `${mins}m ${secs.toString().padStart(2, "0")}s`
                : `${secs}s`;
            this.postMessage({
                type: "thinking",
                show: true,
                text: `Generating project context... (${timeStr})`,
            });
        }, 5_000);

        this.postMessage({
            type: "thinking",
            show: true,
            text: "Generating project context...",
        });

        try {
            const ctxResult = await this.client.generateProjectContext(repoRoot, force);
            clearInterval(ticker);
            if (ctxResult.skipped) {
                this.postMessage({
                    type: "reply",
                    text: `Project context already exists (${ctxResult.chars.toLocaleString()} bytes). Use \`/init --force\` to regenerate.`,
                    cls: "msg-system",
                });
            } else {
                this.postMessage({
                    type: "reply",
                    text: `Project context generated (${ctxResult.chars.toLocaleString()} chars).`,
                    cls: "msg-system",
                });
            }
        } catch (e) {
            clearInterval(ticker);
            anyFailure = true;
            const error = e instanceof Error ? e.message : String(e);
            this.postMessage({
                type: "reply",
                text: `Project context generation failed: ${error}`,
                cls: "msg-system",
            });
        }

        // ── Done ──
        this.postMessage({ type: "thinking", show: false });
        this.postMessage({
            type: "reply",
            text: anyFailure
                ? "Workspace initialization completed with errors."
                : "Workspace initialized successfully! Chat and agent modes now have full context.",
            cls: "msg-system",
        });
    }

    // ── Slash command: /scaffold — create a new project from a recipe ──

    private async handleScaffoldCommand(args: string): Promise<void> {
        const trimmed = args.trim();

        // /scaffold  or  /scaffold list  → list available scaffolds
        if (!trimmed || trimmed.toLowerCase() === "list") {
            this.postMessage({ type: "thinking", show: true, text: "Fetching scaffold list..." });
            try {
                const { scaffolds } = await this.client.listScaffolds();
                this.postMessage({ type: "thinking", show: false });
                const lines = scaffolds.map((s) => {
                    const al = s.aliases.length ? ` (aliases: ${s.aliases.join(", ")})` : "";
                    const kind = s.setup_type === "command" ? "CLI" : "files";
                    return `  **${s.name}** [${kind}] — ${s.description}${al}`;
                });
                this.postMessage({
                    type: "reply",
                    text: `Available scaffolds:\n\n${lines.join("\n")}\n\nUsage: \`/scaffold <name> <project-name> ['/target/dir']\`\nOmit the directory to pick a folder via dialog.`,
                    cls: "msg-system",
                });
            } catch (e) {
                this.postMessage({ type: "thinking", show: false });
                this.postMessage({ type: "error", text: `Failed to list scaffolds: ${e}` });
            }
            return;
        }

            // /scaffold <name> <project-name> ['/optional/target/dir']
        //
        // Directory argument formats accepted:
        //   Quoted (supports spaces): /scaffold laravel my-blog 'C:\www\my-blog'
        //                             /scaffold laravel my-blog "C:\www\my-blog"
        //   Unquoted absolute path:   /scaffold laravel my-blog C:\www\my-blog
        //                             /scaffold laravel my-blog /home/user/projects
        //
        // If no directory is given a folder-picker dialog is shown instead.

        let scaffoldName: string;
        let projectName: string;
        let targetDir: string | null = null;

        // Check for a quoted directory at the end of the args string
        const quotedMatch = trimmed.match(/^(\S+)\s+(.+?)\s+(['"])(.+?)\3\s*$/);
        if (quotedMatch) {
            scaffoldName = quotedMatch[1];
            projectName  = quotedMatch[2];
            targetDir    = quotedMatch[4];
        } else {
            const parts = trimmed.split(/\s+/);
            scaffoldName = parts[0];
            // Detect an unquoted absolute path as the last token
            if (parts.length > 2) {
                const last = parts[parts.length - 1];
                const isAbsPath =
                    /^[A-Za-z]:[\\/]/.test(last) || // Windows: C:\ or C:/
                    last.startsWith("/")            || // Unix absolute
                    last.startsWith("~");              // Home dir shorthand
                if (isAbsPath) {
                    projectName = parts.slice(1, -1).join(" ");
                    targetDir   = last;
                } else {
                    projectName = parts.slice(1).join(" ");
                }
            } else {
                projectName = parts.slice(1).join(" ");
            }
        }

        if (!projectName) {
            this.postMessage({
                type: "error",
                text: "Usage: `/scaffold <name> <project-name> ['/target/dir']`\nRun `/scaffold list` to see available scaffolds.",
            });
            return;
        }

        // Resolve the parent directory ─────────────────────────────────────────
        let parentDir: string;
        if (targetDir) {
            // Path was supplied inline — use it directly
            parentDir = targetDir;
        } else {
            // No path supplied — show a folder-picker so the user can click
            const picked = await vscode.window.showOpenDialog({
                canSelectFolders: true,
                canSelectFiles:   false,
                canSelectMany:    false,
                openLabel: "Select Parent Folder",
                title:    `Where should "${projectName}" be created?`,
            });
            if (picked && picked.length > 0) {
                parentDir = picked[0].fsPath;
            } else {
                // User cancelled the picker
                this.postMessage({
                    type: "reply",
                    text: "Scaffold cancelled — no folder selected.",
                    cls:  "msg-system",
                });
                return;
            }
        }

        this.postMessage({
            type: "thinking",
            show: true,
            text: `Creating ${scaffoldName} project "${projectName}" in ${parentDir}...`,
        });

        let projectDir: string;
        try {
            const result = await this.client.scaffold(scaffoldName, projectName, parentDir);
            this.postMessage({ type: "thinking", show: false });

            const detail = result.files_created.length > 0
                ? `\n\nFiles created:\n${result.files_created.map((f) => `  - \`${f}\``).join("\n")}`
                : result.command_output
                    ? `\n\nCommand output:\n\`\`\`\n${result.command_output.slice(0, 1000)}\n\`\`\``
                    : "";

            this.postMessage({
                type: "reply",
                text: `${result.message}${detail}`,
                cls: "msg-system",
            });
            projectDir = result.project_dir;
        } catch (e) {
            this.postMessage({ type: "thinking", show: false });
            this.postMessage({ type: "error", text: `Scaffold failed: ${e}` });
            return;
        }

        // Store the project dir so the new window knows to auto-run /init after opening
        await this.context.globalState.update("lean-ai.pendingScaffoldInit", projectDir);

        this.postMessage({
            type: "reply",
            text: `Opening \`${projectName}\` in a new window — \`/init\` will run automatically...`,
            cls: "msg-system",
        });

        await vscode.commands.executeCommand(
            "vscode.openFolder",
            vscode.Uri.file(projectDir),
            true, // open in new window
        );
    }

    // ── Slash command: /agent — send a prompt directly to the FSM workflow ──

    private async handleAgentCommand(args: string): Promise<void> {
        const prompt = args.trim();
        if (!prompt) {
            this.postMessage({
                type: "error",
                text: "Usage: `/agent <task description>`\nSend a task directly to the agent workflow — skips the chat endpoint and goes straight to plan creation.\n\nExample: `/agent Add input validation to the contact form`",
            });
            return;
        }

        // Guard: don't start a second workflow over an active WebSocket
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.postMessage({
                type: "error",
                text: "An agent workflow is already running. Wait for it to complete, or start a new chat first.",
            });
            return;
        }

        // Echo the prompt so it's visible in the conversation
        this.postMessage({ type: "reply", text: prompt, cls: "msg-user" });
        await this.handleAgentMessage(prompt);
    }

    // ── Slash command: /fix — skip planning, fix directly ──────────

    private async handleFixCommand(args: string): Promise<void> {
        const prompt = args.trim();
        if (!prompt) {
            this.postMessage({
                type: "error",
                text: "Usage: `/fix <description>`\nSkip planning and let the agent explore, diagnose, and fix directly.\n\nExample: `/fix The search index crashes when the repo has no Python files`",
            });
            return;
        }

        // Guard: don't start a second workflow over an active WebSocket
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.postMessage({
                type: "error",
                text: "An agent workflow is already running. Wait for it to complete, or start a new chat first.",
            });
            return;
        }

        // Echo the prompt so it's visible in the conversation
        this.postMessage({ type: "reply", text: `🔧 ${prompt}`, cls: "msg-user" });

        // Send with /fix prefix so the backend skips planning
        await this.handleAgentMessage(`/fix ${prompt}`);
    }

    // ── Slash command: /reboot — restart the backend server ──────────

    private async handleRebootCommand(_args: string): Promise<void> {
        this.postMessage({ type: "thinking", show: true, text: "Restarting backend server..." });
        try {
            const success = await restartBackend();
            this.postMessage({ type: "thinking", show: false });
            if (success) {
                this.postMessage({
                    type: "reply",
                    text: "Backend server restarted successfully.",
                    cls: "msg-system",
                });
            } else {
                this.postMessage({
                    type: "error",
                    text: "Failed to restart backend server. Check the **Lean AI Backend** output panel for details.",
                });
            }
        } catch (e) {
            this.postMessage({ type: "thinking", show: false });
            const error = e instanceof Error ? e.message : String(e);
            this.postMessage({ type: "error", text: `Reboot failed: ${error}` });
        }
    }

    // ── Slash command: /approve — merge agent branch into base ────────

    private async handleApproveCommand(_args: string): Promise<void> {
        const sessionId = this.lastCompletedSessionId;
        if (!sessionId) {
            this.postMessage({
                type: "error",
                text: "No completed workflow to approve. Run `/agent` first.",
            });
            return;
        }

        this.postMessage({ type: "thinking", show: true, text: "Merging branch..." });
        try {
            const result = await this.client.mergeSession(sessionId, this.getRepoRoot());
            this.postMessage({ type: "thinking", show: false });
            const sha = ((result.merge_sha as string) || "").slice(0, 7);
            this.postMessage({
                type: "reply",
                text: `Branch merged successfully${sha ? ` (${sha})` : ""}. Back on base branch.`,
                cls: "msg-system",
            });
            this.lastCompletedSessionId = undefined;
            this.context.globalState.update("lean-ai.lastCompletedSessionId", undefined);
        } catch (e) {
            this.postMessage({ type: "thinking", show: false });
            const error = e instanceof Error ? e.message : String(e);
            this.postMessage({ type: "error", text: `Merge failed: ${error}` });
        }
    }

    // ── Slash command: /reject — abandon agent branch ────────────────

    private async handleRejectCommand(_args: string): Promise<void> {
        const sessionId = this.lastCompletedSessionId;
        if (!sessionId) {
            this.postMessage({
                type: "error",
                text: "No completed workflow to reject. Run `/agent` first.",
            });
            return;
        }

        this.postMessage({ type: "thinking", show: true, text: "Abandoning branch..." });
        try {
            await this.client.abandonSession(sessionId, this.getRepoRoot());
            this.postMessage({ type: "thinking", show: false });
            this.postMessage({
                type: "reply",
                text: "Branch discarded. Back on base branch, changes reverted.",
                cls: "msg-system",
            });
            this.lastCompletedSessionId = undefined;
            this.context.globalState.update("lean-ai.lastCompletedSessionId", undefined);
        } catch (e) {
            this.postMessage({ type: "thinking", show: false });
            const error = e instanceof Error ? e.message : String(e);
            this.postMessage({ type: "error", text: `Reject failed: ${error}` });
        }
    }

    // ── Slash command: /resume — resume a previous session ────────────

    private async handleResumeCommand(args: string): Promise<void> {
        // Determine which session to resume
        const sessionId = args.trim() || this.lastCompletedSessionId;
        if (!sessionId) {
            this.postMessage({
                type: "error",
                text: "Usage: `/resume [session_id]`\nResumes a previous session from where it left off.\n\nOmit session_id to resume the last completed session.",
            });
            return;
        }

        // Guard: don't start a second workflow over an active WebSocket
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.postMessage({
                type: "error",
                text: "An agent workflow is already running. Wait for it to complete, or start a new chat first.",
            });
            return;
        }

        this.postMessage({ type: "thinking", show: true, text: "Preparing session resume..." });

        try {
            const repoRoot = this.getRepoRoot();

            // Call the resume REST endpoint (validates state, switches git branch)
            const result = await this.client.resumeSession(sessionId, repoRoot);

            this.postMessage({ type: "thinking", show: false });
            this.postMessage({
                type: "reply",
                text: `Resuming session \`${sessionId}\` on branch \`${result.branch_name || "unknown"}\`${result.scratchpad_exists ? " (scratchpad found)" : ""}...`,
                cls: "msg-system",
            });

            // Set this as the active session
            this.sessionId = sessionId;
            this.lastCompletedSessionId = sessionId;
            this.context.globalState.update("lean-ai.lastCompletedSessionId", sessionId);

            // Open WebSocket and send resume message
            const ws = this.ensureWebSocket(sessionId);

            if (ws.readyState === WebSocket.CONNECTING) {
                await new Promise<void>((resolve, reject) => {
                    const onOpen = () => { ws.removeListener("error", onError); resolve(); };
                    const onError = (err: Error) => { ws.removeListener("open", onOpen); reject(err); };
                    ws.once("open", onOpen);
                    ws.once("error", onError);
                });
            }

            ws.send(JSON.stringify({ type: "resume", repo_root: repoRoot }));
        } catch (e) {
            this.postMessage({ type: "thinking", show: false });
            const error = e instanceof Error ? e.message : String(e);
            this.postMessage({ type: "error", text: `Resume failed: ${error}` });
        }
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

    // ── Conversation persistence ──────────────────────────────────────

    private loadConversations(): StoredConversation[] {
        return this.context.globalState.get<StoredConversation[]>(
            LeanAISidebarProvider.STORAGE_KEY,
            [],
        );
    }

    private async saveConversations(conversations: StoredConversation[]): Promise<void> {
        const trimmed = conversations.slice(-LeanAISidebarProvider.MAX_CONVERSATIONS);
        await this.context.globalState.update(LeanAISidebarProvider.STORAGE_KEY, trimmed);
    }

    private async persistCurrentConversation(): Promise<void> {
        if (this.chatHistory.length === 0) {
            return;
        }

        const conversations = this.loadConversations();
        const now = new Date().toISOString();
        const repoRoot = this.getRepoRoot();

        const messages = this.chatHistory
            .slice(-LeanAISidebarProvider.MAX_MESSAGES_PER_CONVERSATION)
            .map((m) => ({ role: m.role, content: m.content, timestamp: m.timestamp }));

        const existing = conversations.findIndex((c) => c.id === this.currentConversationId);

        if (existing >= 0) {
            conversations[existing].messages = messages;
            conversations[existing].updatedAt = now;
        } else {
            const id =
                Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
            this.currentConversationId = id;
            const firstUserMsg = this.chatHistory.find((m) => m.role === "user");
            const title = firstUserMsg
                ? firstUserMsg.content.slice(0, 80).replace(/\n/g, " ")
                : "New conversation";

            conversations.push({
                id,
                title,
                messages,
                createdAt: now,
                updatedAt: now,
                repoRoot,
            });
        }

        await this.saveConversations(conversations);
    }

    // ── Search handlers ─────────────────────────────────────────────

    private async handleSearchConversations(query: string): Promise<void> {
        const q = (query || "").toLowerCase().trim();
        if (!q) {
            this.postMessage({ type: "searchResults", results: [] });
            return;
        }

        // Search local chat conversations
        const conversations = this.loadConversations();
        const results: Array<{
            id: string;
            title: string;
            createdAt: string;
            matchSnippet: string;
            matchRole: string;
            source: string;
        }> = [];

        for (const conv of conversations) {
            for (const m of conv.messages) {
                if (m.content.toLowerCase().includes(q)) {
                    const idx = m.content.toLowerCase().indexOf(q);
                    const start = Math.max(0, idx - 40);
                    const end = Math.min(m.content.length, idx + q.length + 40);
                    let snippet = m.content.slice(start, end);
                    if (start > 0) {
                        snippet = "..." + snippet;
                    }
                    if (end < m.content.length) {
                        snippet = snippet + "...";
                    }

                    results.push({
                        id: conv.id,
                        title: conv.title,
                        createdAt: conv.createdAt,
                        matchSnippet: snippet,
                        matchRole: m.role,
                        source: "chat",
                    });
                    break; // One result per conversation
                }
            }
        }

        // Search backend sessions (task text, plan, conversation logs, commits)
        try {
            const repoRoot = this.getRepoRoot();
            const sessionResults = await this.client.searchSessions(repoRoot, query);
            for (const s of sessionResults) {
                // Avoid duplicates if already in chat results
                if (!results.some(r => r.id === `session-${s.session_id}`)) {
                    results.push({
                        id: `session-${s.session_id}`,
                        title: s.title || `Session ${s.session_id.slice(0, 8)}`,
                        createdAt: s.created_at,
                        matchSnippet: `[${s.session_status}] ${s.title || ""}`,
                        matchRole: "session",
                        source: "session",
                    });
                }
            }
        } catch {
            // Backend search failure is non-fatal — local results still shown
        }

        results.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
        this.postMessage({ type: "searchResults", results: results.slice(0, 50) });
    }

    private handleLoadConversation(convId: string): void {
        // Handle session search results (id starts with "session-")
        if (convId.startsWith("session-")) {
            const sessionId = convId.replace("session-", "");
            this.loadSessionConversation(sessionId);
            return;
        }

        const conversations = this.loadConversations();
        const conv = conversations.find((c) => c.id === convId);
        if (conv) {
            // Open chat conversations as read-only tabs too
            this.postMessage({
                type: "openSessionTab",
                sessionId: convId,
                tabId: `chat-${convId}`,
                title: conv.title,
                messages: conv.messages,
            });
        }
    }

    private handleBackToCurrentChat(): void {
        this.viewingHistoricConversation = false;
        this.postMessage({
            type: "restoreCurrentChat",
            messages: this.chatHistory,
        });
    }

    /** Load a session's conversation log into the chat sidebar for review */
    async loadSessionConversation(sessionId: string): Promise<void> {
        const repoRoot = this.getRepoRoot();
        const client = BackendClient.getInstance();

        try {
            const [convLog, sessionInfo] = await Promise.all([
                client.getConversationLog(sessionId, repoRoot),
                client.getSession(sessionId, repoRoot).catch(() => null),
            ]);

            const title = sessionInfo?.title
                || `Session ${sessionId.slice(0, 8)}`;

            // Map conversation log entries to message format for the tab
            let messages: Array<{ role: string; content: string; timestamp: string }>;

            if (!convLog.entries || convLog.entries.length === 0) {
                const taskDesc = sessionInfo?.task_track || null;
                messages = [{
                    role: "system",
                    content: taskDesc
                        ? `**Task:** ${taskDesc}\n\nNo conversation log available — this session was created before conversation logging was enabled.`
                        : "No conversation log available for this session.\n\nConversation logging was added after this session was created. New sessions will have full chain-of-thought logs.",
                    timestamp: sessionInfo?.created_at || new Date().toISOString(),
                }];
            } else {
                messages = convLog.entries.map((entry) => {
                    let role: string;
                    let content: string;

                    switch (entry.role) {
                        case "user":
                            role = "user";
                            content = entry.content;
                            break;
                        case "assistant":
                            role = "assistant";
                            content = entry.content;
                            break;
                        case "tool_call":
                            role = "system";
                            content = `**${entry.tool_name || "tool"}**\n${entry.content}`;
                            break;
                        case "tool_result":
                            role = "system";
                            content = `**${entry.tool_name || "tool"} result**\n${entry.content.slice(0, 2000)}`;
                            break;
                        default:
                            role = "system";
                            content = entry.content;
                    }

                    return { role, content, timestamp: entry.created_at };
                });
            }

            // Open as a read-only tab
            this.postMessage({
                type: "openSessionTab",
                sessionId,
                tabId: `session-${sessionId}`,
                title,
                messages,
            });

            // Focus the chat panel
            vscode.commands.executeCommand("lean-ai.chatView.focus");
        } catch (e) {
            const error = e instanceof Error ? e.message : String(e);
            vscode.window.showErrorMessage(`Failed to load session: ${error}`);
        }
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


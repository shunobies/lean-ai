/**
 * Conversation persistence and search for the Lean AI sidebar.
 *
 * Stores chat conversations as a JSON file on disk via globalStorageUri
 * (not globalState, which has size limits). Includes one-time migration
 * from the old globalState key on first load.
 */

import * as vscode from "vscode";
import { BackendClient } from "./backendClient";
import type { StoredConversation } from "./types";

// ── Constants ────────────────────────────────────────────────────────

const OLD_STORAGE_KEY = "lean-ai.chatConversations";
const CONVERSATIONS_FILE = "conversations.json";
const MAX_CONVERSATIONS = 200;

// ── Manager class ────────────────────────────────────────────────────

export class ConversationManager {
    currentConversationId: string | undefined;
    viewingHistoricConversation = false;

    private readonly storageUri: vscode.Uri;
    private migrated = false;

    constructor(
        private readonly extensionContext: vscode.ExtensionContext,
        private readonly postMessage: (msg: Record<string, unknown>) => void,
        private readonly getRepoRoot: () => string,
    ) {
        this.storageUri = vscode.Uri.joinPath(
            extensionContext.globalStorageUri,
            CONVERSATIONS_FILE,
        );
    }

    /** Ensure the storage directory exists (call once during activation). */
    async ensureStorageDir(): Promise<void> {
        await vscode.workspace.fs.createDirectory(
            this.extensionContext.globalStorageUri,
        );
    }

    loadConversations(): StoredConversation[] {
        // Synchronous load from globalState — used as fallback only.
        // After migration, the globalState key is cleared.
        // For the async disk path, use loadConversationsAsync().
        return this.extensionContext.globalState.get<StoredConversation[]>(
            OLD_STORAGE_KEY,
            [],
        );
    }

    async loadConversationsAsync(): Promise<StoredConversation[]> {
        // Try disk first
        try {
            const data = await vscode.workspace.fs.readFile(this.storageUri);
            const text = Buffer.from(data).toString("utf-8");
            return JSON.parse(text) as StoredConversation[];
        } catch {
            // File doesn't exist yet — try one-time migration from globalState
        }

        if (!this.migrated) {
            const old = this.extensionContext.globalState.get<StoredConversation[]>(
                OLD_STORAGE_KEY,
                [],
            );
            if (old.length > 0) {
                await this.saveConversations(old);
                await this.extensionContext.globalState.update(OLD_STORAGE_KEY, undefined);
            }
            this.migrated = true;
            return old;
        }

        return [];
    }

    async saveConversations(conversations: StoredConversation[]): Promise<void> {
        const trimmed = conversations.slice(-MAX_CONVERSATIONS);
        const json = JSON.stringify(trimmed);
        await vscode.workspace.fs.writeFile(
            this.storageUri,
            Buffer.from(json, "utf-8"),
        );
    }

    async persistCurrentConversation(
        chatHistory: Array<{ role: string; content: string; timestamp: string }>,
    ): Promise<void> {
        if (chatHistory.length === 0) {
            return;
        }

        const conversations = await this.loadConversationsAsync();
        const now = new Date().toISOString();
        const repoRoot = this.getRepoRoot();

        const messages = chatHistory.map((m) => ({
            role: m.role,
            content: m.content,
            timestamp: m.timestamp,
        }));

        const existing = conversations.findIndex((c) => c.id === this.currentConversationId);

        if (existing >= 0) {
            conversations[existing].messages = messages;
            conversations[existing].updatedAt = now;
        } else {
            const id =
                Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
            this.currentConversationId = id;
            const firstUserMsg = chatHistory.find((m) => m.role === "user");
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

    // ── Search ───────────────────────────────────────────────────────

    async handleSearchConversations(query: string): Promise<void> {
        const q = (query || "").toLowerCase().trim();
        if (!q) {
            this.postMessage({ type: "searchResults", results: [] });
            return;
        }

        // Search local chat conversations
        const conversations = await this.loadConversationsAsync();
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
            const client = BackendClient.getInstance();
            const sessionResults = await client.searchSessions(repoRoot, query);
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

    handleLoadConversation(convId: string): void {
        // Handle session search results (id starts with "session-")
        if (convId.startsWith("session-")) {
            const sessionId = convId.replace("session-", "");
            this.loadSessionConversation(sessionId);
            return;
        }

        // Load from disk (async, but fire-and-forget for the UI callback)
        this.loadConversationsAsync().then((conversations) => {
            const conv = conversations.find((c) => c.id === convId);
            if (conv) {
                this.postMessage({
                    type: "openSessionTab",
                    sessionId: convId,
                    tabId: `chat-${convId}`,
                    title: conv.title,
                    messages: conv.messages,
                });
            }
        });
    }

    handleBackToCurrentChat(
        chatHistory: Array<{ role: string; content: string; timestamp: string }>,
    ): void {
        this.viewingHistoricConversation = false;
        this.postMessage({
            type: "restoreCurrentChat",
            messages: chatHistory,
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
}

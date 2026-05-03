/**
 * Slash command handlers extracted from LeanAISidebarProvider.
 *
 * Each handler receives a {@link SlashCommandContext} providing access
 * to the provider's state and helpers, keeping the handlers decoupled
 * from the class itself.
 *
 * Complex workspace-related handlers (init, style, scaffold, agent, fix,
 * request) live in {@link ./slashCommandsWorkspace}.
 */

import * as vscode from "vscode";
import { BackendClient } from "./backendClient";
import { restartBackend } from "./backendProcess";
import WebSocket from "ws";
import {
    handleInitCommand,
    handleStyleCommand,
    handleScaffoldCommand,
    handleAgentCommand,
    handleFixCommand,
    handleRequestCommand,
    handleInterviewPrepCommand,
    handleThankYouCommand,
    handleRecruiterReplyCommand,
    handleNegotiateCommand,
    handleAnalyseRejectionCommand,
    handleAtsCheckCommand,
    handleBatchPrepCommand,
    handleLogAppliedCommand,
    handleMockInterviewCommand,
    handleHelpCommand,
    handleMemoriesCommand,
    handleSkillCommand,
} from "./slashCommandsWorkspace";

// ── Context interface ────────────────────────────────────────────────

export interface SlashCommandContext {
    postMessage(msg: Record<string, unknown>): void;
    client: BackendClient;
    getRepoRoot(): string;
    ensureSession(): Promise<string>;
    ensureWebSocket(sessionId: string): WebSocket;
    handleAgentMessage(text: string): Promise<void>;
    handleChatDispatch(text: string, opts?: { extendedTurns?: number }): Promise<void>;
    getWs(): WebSocket | undefined;
    getLastCompletedSessionId(): string | undefined;
    setSessionId(id: string | undefined): void;
    setLastCompletedSessionId(id: string | undefined): void;
    extensionContext: vscode.ExtensionContext;
    getFileDiagnostics(): string;
}

// ── Factory ──────────────────────────────────────────────────────────

export function createSlashCommands(
    ctx: SlashCommandContext,
): Map<string, (args: string) => Promise<void>> {
    const map = new Map<string, (args: string) => Promise<void>>();
    map.set("/init",     (args) => handleInitCommand(ctx, args));
    map.set("/scaffold", (args) => handleScaffoldCommand(ctx, args));
    map.set("/agent",    (args) => handleAgentCommand(ctx, args));
    map.set("/fix",      (args) => handleFixCommand(ctx, args));
    map.set("/request",  (args) => handleRequestCommand(ctx, args));
    map.set("/style",    (args) => handleStyleCommand(ctx, args));
    map.set("/reboot",   (args) => handleRebootCommand(ctx, args));
    map.set("/approve",  (args) => handleApproveCommand(ctx, args));
    map.set("/reject",   (args) => handleRejectCommand(ctx, args));
    map.set("/resume",   (args) => handleResumeCommand(ctx, args));
    map.set("/note",     (args) => handleNoteCommand(ctx, args));
    map.set("/interview-prep", (args) => handleInterviewPrepCommand(ctx, args));
    map.set("/thank-you", (args) => handleThankYouCommand(ctx, args));
    map.set("/recruiter-reply", (args) => handleRecruiterReplyCommand(ctx, args));
    map.set("/negotiate", (args) => handleNegotiateCommand(ctx, args));
    map.set("/analyse-rejection", (args) => handleAnalyseRejectionCommand(ctx, args));
    map.set("/ats-check", (args) => handleAtsCheckCommand(ctx, args));
    map.set("/batch-prep", (args) => handleBatchPrepCommand(ctx, args));
    map.set("/log-applied", (args) => handleLogAppliedCommand(ctx, args));
    map.set("/mock-interview", (args) => handleMockInterviewCommand(ctx, args));
    map.set("/help", (args) => handleHelpCommand(ctx, args));
    map.set("/memories", (args) => handleMemoriesCommand(ctx, args));
    map.set("/skill", (args) => handleSkillCommand(ctx, args));
    return map;
}

// ── /reboot — restart the backend server ─────────────────────────────

export async function handleRebootCommand(
    ctx: SlashCommandContext,
    _args: string,
): Promise<void> {
    ctx.postMessage({ type: "thinking", show: true, text: "Restarting backend server..." });
    try {
        const success = await restartBackend();
        ctx.postMessage({ type: "thinking", show: false });
        if (success) {
            ctx.postMessage({
                type: "reply",
                text: "Backend server restarted successfully.",
                cls: "msg-system",
            });
        } else {
            ctx.postMessage({
                type: "error",
                text: "Failed to restart backend server. Check the **Lean AI Backend** output panel for details.",
            });
        }
    } catch (e) {
        ctx.postMessage({ type: "thinking", show: false });
        const error = e instanceof Error ? e.message : String(e);
        ctx.postMessage({ type: "error", text: `Reboot failed: ${error}` });
    }
}

// ── /approve — merge agent branch into base ──────────────────────────

export async function handleApproveCommand(
    ctx: SlashCommandContext,
    _args: string,
): Promise<void> {
    let sessionId = ctx.getLastCompletedSessionId();
    if (!sessionId) {
        // Fallback: query backend for the most recent rejectable session
        sessionId = await ctx.client.getLatestRejectableSession(ctx.getRepoRoot());
    }
    if (!sessionId) {
        ctx.postMessage({
            type: "error",
            text: "No completed workflow to approve. Run `/agent` first.",
        });
        return;
    }

    ctx.postMessage({ type: "thinking", show: true, text: "Merging branch..." });
    try {
        const result = await ctx.client.mergeSession(sessionId, ctx.getRepoRoot());
        ctx.postMessage({ type: "thinking", show: false });
        const sha = ((result.merge_sha as string) || "").slice(0, 7);
        ctx.postMessage({
            type: "reply",
            text: `Branch merged successfully${sha ? ` (${sha})` : ""}. Back on base branch.`,
            cls: "msg-system",
        });
        ctx.setLastCompletedSessionId(undefined);
        ctx.extensionContext.globalState.update("lean-ai.lastCompletedSessionId", undefined);
    } catch (e) {
        ctx.postMessage({ type: "thinking", show: false });
        const error = e instanceof Error ? e.message : String(e);
        ctx.postMessage({ type: "error", text: `Merge failed: ${error}` });
    }
}

// ── /reject — abandon agent branch ──────────────────────────────────

export async function handleRejectCommand(
    ctx: SlashCommandContext,
    _args: string,
): Promise<void> {
    let sessionId = ctx.getLastCompletedSessionId();
    if (!sessionId) {
        // Fallback: query backend for the most recent rejectable session
        sessionId = await ctx.client.getLatestRejectableSession(ctx.getRepoRoot());
    }
    if (!sessionId) {
        ctx.postMessage({
            type: "error",
            text: "No completed workflow to reject. Run `/agent` first.",
        });
        return;
    }

    ctx.postMessage({ type: "thinking", show: true, text: "Abandoning branch..." });
    try {
        await ctx.client.abandonSession(sessionId, ctx.getRepoRoot());
        ctx.postMessage({ type: "thinking", show: false });
        ctx.postMessage({
            type: "reply",
            text: "Branch discarded. Back on base branch, changes reverted.",
            cls: "msg-system",
        });
        ctx.setLastCompletedSessionId(undefined);
        ctx.extensionContext.globalState.update("lean-ai.lastCompletedSessionId", undefined);
    } catch (e) {
        ctx.postMessage({ type: "thinking", show: false });
        const error = e instanceof Error ? e.message : String(e);
        ctx.postMessage({ type: "error", text: `Reject failed: ${error}` });
    }
}

// ── /resume — resume a previous session ─────────────────────────────

export async function handleResumeCommand(
    ctx: SlashCommandContext,
    args: string,
): Promise<void> {
    // Determine which session to resume
    const sessionId = args.trim() || ctx.getLastCompletedSessionId();
    if (!sessionId) {
        ctx.postMessage({
            type: "error",
            text: "Usage: `/resume [session_id]`\nResumes a previous session from where it left off.\n\nOmit session_id to resume the last completed session.",
        });
        return;
    }

    // Guard: don't start a second workflow over an active WebSocket
    const ws = ctx.getWs();
    if (ws && ws.readyState === WebSocket.OPEN) {
        ctx.postMessage({
            type: "error",
            text: "An agent workflow is already running. Wait for it to complete, or start a new chat first.",
        });
        return;
    }

    ctx.postMessage({ type: "thinking", show: true, text: "Preparing session resume..." });

    try {
        const repoRoot = ctx.getRepoRoot();

        // Call the resume REST endpoint (validates state, switches git branch)
        const result = await ctx.client.resumeSession(sessionId, repoRoot);

        ctx.postMessage({ type: "thinking", show: false });
        ctx.postMessage({
            type: "reply",
            text: `Resuming session \`${sessionId}\` on branch \`${result.branch_name || "unknown"}\`${result.scratchpad_exists ? " (scratchpad found)" : ""}...`,
            cls: "msg-system",
        });

        // Set this as the active session
        ctx.setSessionId(sessionId);
        ctx.setLastCompletedSessionId(sessionId);
        ctx.extensionContext.globalState.update("lean-ai.lastCompletedSessionId", sessionId);

        // Open WebSocket and send resume message
        const resumeWs = ctx.ensureWebSocket(sessionId);

        if (resumeWs.readyState === WebSocket.CONNECTING) {
            await new Promise<void>((resolve, reject) => {
                const onOpen = () => { resumeWs.removeListener("error", onError); resolve(); };
                const onError = (err: Error) => { resumeWs.removeListener("open", onOpen); reject(err); };
                resumeWs.once("open", onOpen);
                resumeWs.once("error", onError);
            });
        }

        resumeWs.send(JSON.stringify({ type: "resume", repo_root: repoRoot }));
    } catch (e) {
        ctx.postMessage({ type: "thinking", show: false });
        const error = e instanceof Error ? e.message : String(e);
        ctx.postMessage({ type: "error", text: `Resume failed: ${error}` });
    }
}

// ── /note — save a quick note ─────────────────────────────────────────

export async function handleNoteCommand(
    ctx: SlashCommandContext,
    args: string,
): Promise<void> {
    const content = args.trim();
    if (!content) {
        ctx.postMessage({
            type: "reply",
            text: "Usage: `/note <your note text>`\n\nExample: `/note Remember to add rate limiting to the API`",
            cls: "msg-system",
        });
        return;
    }

    ctx.postMessage({ type: "thinking", show: true, text: "Saving note..." });

    const healthy = await ctx.client.healthCheck();
    if (!healthy) {
        ctx.postMessage({ type: "thinking", show: false });
        ctx.postMessage({
            type: "error",
            text: "Backend not available. Start the server:\ncd backend && uvicorn lean_ai.main:app --reload --port 8422",
        });
        return;
    }

    try {
        const repoRoot = ctx.getRepoRoot();
        const note = await ctx.client.createNote(content, repoRoot);
        ctx.postMessage({ type: "thinking", show: false });
        ctx.postMessage({
            type: "reply",
            text: `Note saved (id: ${note.id}). AI is categorizing it in the background.`,
            cls: "msg-system",
        });
    } catch (e) {
        ctx.postMessage({ type: "thinking", show: false });
        const error = e instanceof Error ? e.message : String(e);
        ctx.postMessage({ type: "error", text: `Failed to save note: ${error}` });
    }
}

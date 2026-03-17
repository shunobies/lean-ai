/**
 * Slash command handlers extracted from LeanAISidebarProvider.
 *
 * Each handler receives a {@link SlashCommandContext} providing access
 * to the provider's state and helpers, keeping the handlers decoupled
 * from the class itself.
 */

import * as vscode from "vscode";
import { BackendClient } from "./backendClient";
import { restartBackend } from "./backendProcess";
import WebSocket from "ws";

// ── Context interface ────────────────────────────────────────────────

export interface SlashCommandContext {
    postMessage(msg: Record<string, unknown>): void;
    client: BackendClient;
    getRepoRoot(): string;
    ensureSession(): Promise<string>;
    ensureWebSocket(sessionId: string): WebSocket;
    handleAgentMessage(text: string): Promise<void>;
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
    map.set("/guide",    (args) => handleGuideCommand(ctx, args));
    map.set("/style",    (args) => handleStyleCommand(ctx, args));
    map.set("/reboot",   (args) => handleRebootCommand(ctx, args));
    map.set("/approve",  (args) => handleApproveCommand(ctx, args));
    map.set("/reject",   (args) => handleRejectCommand(ctx, args));
    map.set("/resume",   (args) => handleResumeCommand(ctx, args));
    return map;
}

// ── /init — workspace indexing + project context ─────────────────────

export async function handleInitCommand(
    ctx: SlashCommandContext,
    args: string,
): Promise<void> {
    // Check backend health first
    ctx.postMessage({ type: "thinking", show: true, text: "Checking backend..." });

    const healthy = await ctx.client.healthCheck();
    if (!healthy) {
        ctx.postMessage({ type: "thinking", show: false });
        ctx.postMessage({
            type: "error",
            text: "Backend not available. Start the server:\ncd backend && uvicorn lean_ai.main:app --reload --port 8422",
        });
        return;
    }

    // Parse flags
    const force = args.includes("--force");
    const repoRoot = ctx.getRepoRoot();
    let anyFailure = false;

    // ── Step 1: Index workspace (fast — local file I/O) ──
    ctx.postMessage({
        type: "thinking",
        show: true,
        text: "Indexing workspace...",
    });

    try {
        const indexResult = await ctx.client.indexWorkspace(repoRoot, force);

        if (indexResult.index_status === "failed") {
            anyFailure = true;
            ctx.postMessage({
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
            ctx.postMessage({
                type: "reply",
                text: `Search index ${mode}: ${fileCount} files, ${chunkCount} chunks.`,
                cls: "msg-system",
            });
        }
    } catch (e) {
        anyFailure = true;
        const error = e instanceof Error ? e.message : String(e);
        ctx.postMessage({
            type: "reply",
            text: `Indexing failed: ${error}`,
            cls: "msg-system",
        });
    }

    // ── Steps 2 + 3: Generate project context and framework guide in parallel ──
    // Both are independent LLM-heavy operations — running them concurrently
    // leverages Ollama's parallel request support for significant speedup.
    const startTime = Date.now();
    const ticker = setInterval(() => {
        const elapsed = Math.floor((Date.now() - startTime) / 1000);
        const mins = Math.floor(elapsed / 60);
        const secs = elapsed % 60;
        const timeStr = mins > 0
            ? `${mins}m ${secs.toString().padStart(2, "0")}s`
            : `${secs}s`;
        ctx.postMessage({
            type: "thinking",
            show: true,
            text: `Generating project context + framework guide... (${timeStr})`,
        });
    }, 5_000);

    ctx.postMessage({
        type: "thinking",
        show: true,
        text: "Generating project context + framework guide...",
    });

    const [ctxSettled, guideSettled] = await Promise.allSettled([
        ctx.client.generateProjectContext(repoRoot, force),
        ctx.client.generateFrameworkGuide(repoRoot, force),
    ]);

    clearInterval(ticker);

    // Handle project context result
    if (ctxSettled.status === "fulfilled") {
        const ctxResult = ctxSettled.value;
        if (ctxResult.skipped) {
            ctx.postMessage({
                type: "reply",
                text: `Project context already exists (${ctxResult.chars.toLocaleString()} bytes). Use \`/init --force\` to regenerate.`,
                cls: "msg-system",
            });
        } else {
            ctx.postMessage({
                type: "reply",
                text: `Project context generated (${ctxResult.chars.toLocaleString()} chars).`,
                cls: "msg-system",
            });
        }
    } else {
        anyFailure = true;
        const error = ctxSettled.reason instanceof Error
            ? ctxSettled.reason.message
            : String(ctxSettled.reason);
        ctx.postMessage({
            type: "reply",
            text: `Project context generation failed: ${error}`,
            cls: "msg-system",
        });
    }

    // Handle framework guide result
    if (guideSettled.status === "fulfilled") {
        const guideResult = guideSettled.value;
        if (guideResult.skipped) {
            ctx.postMessage({
                type: "reply",
                text: `Framework guide already exists (${guideResult.chars.toLocaleString()} bytes). Use \`/init --force\` to regenerate.`,
                cls: "msg-system",
            });
        } else {
            ctx.postMessage({
                type: "reply",
                text: `Framework guide generated (${guideResult.chars.toLocaleString()} chars).`,
                cls: "msg-system",
            });
        }
    } else {
        // Non-fatal — 404 means no frameworks detected, anything else is an error
        const error = guideSettled.reason instanceof Error
            ? guideSettled.reason.message
            : String(guideSettled.reason);
        if (error.includes("404")) {
            ctx.postMessage({
                type: "reply",
                text: "No frameworks detected — framework guide skipped.",
                cls: "msg-system",
            });
        } else {
            ctx.postMessage({
                type: "reply",
                text: `Framework guide generation failed: ${error}`,
                cls: "msg-system",
            });
        }
    }

    // ── Done ──
    ctx.postMessage({ type: "thinking", show: false });
    ctx.postMessage({
        type: "reply",
        text: anyFailure
            ? "Workspace initialization completed with errors."
            : "Workspace initialized successfully! Chat and agent modes now have full context.",
        cls: "msg-system",
    });
}

// ── /guide — regenerate framework guide only ─────────────────────────

export async function handleGuideCommand(
    ctx: SlashCommandContext,
    _args: string,
): Promise<void> {
    // Health check
    ctx.postMessage({ type: "thinking", show: true, text: "Checking backend..." });
    const healthy = await ctx.client.healthCheck();
    if (!healthy) {
        ctx.postMessage({ type: "thinking", show: false });
        ctx.postMessage({
            type: "error",
            text: "Backend not available. Start the server:\ncd backend && uvicorn lean_ai.main:app --reload --port 8422",
        });
        return;
    }

    const repoRoot = ctx.getRepoRoot();

    // Elapsed-time ticker
    const guideStart = Date.now();
    const guideTicker = setInterval(() => {
        const elapsed = Math.floor((Date.now() - guideStart) / 1000);
        const mins = Math.floor(elapsed / 60);
        const secs = elapsed % 60;
        const timeStr = mins > 0
            ? `${mins}m ${secs.toString().padStart(2, "0")}s`
            : `${secs}s`;
        ctx.postMessage({
            type: "thinking",
            show: true,
            text: `Generating framework guide... (${timeStr})`,
        });
    }, 5_000);

    ctx.postMessage({
        type: "thinking",
        show: true,
        text: "Generating framework guide...",
    });

    try {
        // Always force-regenerate — the whole point of /guide
        const guideResult = await ctx.client.generateFrameworkGuide(repoRoot, true);
        clearInterval(guideTicker);
        ctx.postMessage({
            type: "reply",
            text: `Framework guide generated (${guideResult.chars.toLocaleString()} chars).`,
            cls: "msg-system",
        });
    } catch (e) {
        clearInterval(guideTicker);
        const error = e instanceof Error ? e.message : String(e);
        if (error.includes("404")) {
            ctx.postMessage({
                type: "reply",
                text: "No frameworks detected — framework guide skipped.",
                cls: "msg-system",
            });
        } else {
            ctx.postMessage({
                type: "reply",
                text: `Framework guide generation failed: ${error}`,
                cls: "msg-system",
            });
        }
    }

    ctx.postMessage({ type: "thinking", show: false });
}

// ── /style — generate style guide from CSS and template files ────────

export async function handleStyleCommand(
    ctx: SlashCommandContext,
    _args: string,
): Promise<void> {
    // Health check
    ctx.postMessage({ type: "thinking", show: true, text: "Checking backend..." });
    const healthy = await ctx.client.healthCheck();
    if (!healthy) {
        ctx.postMessage({ type: "thinking", show: false });
        ctx.postMessage({
            type: "error",
            text: "Backend not available. Start the server:\ncd backend && uvicorn lean_ai.main:app --reload --port 8422",
        });
        return;
    }

    const repoRoot = ctx.getRepoRoot();

    // Elapsed-time ticker
    const styleStart = Date.now();
    const styleTicker = setInterval(() => {
        const elapsed = Math.floor((Date.now() - styleStart) / 1000);
        const mins = Math.floor(elapsed / 60);
        const secs = elapsed % 60;
        const timeStr = mins > 0
            ? `${mins}m ${secs.toString().padStart(2, "0")}s`
            : `${secs}s`;
        ctx.postMessage({
            type: "thinking",
            show: true,
            text: `Generating style guide... (${timeStr})`,
        });
    }, 5_000);

    ctx.postMessage({
        type: "thinking",
        show: true,
        text: "Generating style guide...",
    });

    try {
        // Always force-regenerate — the whole point of /style
        const styleResult = await ctx.client.generateStyleGuide(repoRoot, true);
        clearInterval(styleTicker);
        ctx.postMessage({
            type: "reply",
            text: `Style guide generated (${styleResult.chars.toLocaleString()} chars).`,
            cls: "msg-system",
        });
    } catch (e) {
        clearInterval(styleTicker);
        const error = e instanceof Error ? e.message : String(e);
        if (error.includes("404")) {
            ctx.postMessage({
                type: "reply",
                text: "No style files detected — style guide skipped.",
                cls: "msg-system",
            });
        } else {
            ctx.postMessage({
                type: "reply",
                text: `Style guide generation failed: ${error}`,
                cls: "msg-system",
            });
        }
    }

    ctx.postMessage({ type: "thinking", show: false });
}

// ── /scaffold — create a new project from a recipe ───────────────────

export async function handleScaffoldCommand(
    ctx: SlashCommandContext,
    args: string,
): Promise<void> {
    const trimmed = args.trim();

    // /scaffold  or  /scaffold list  → list available scaffolds
    if (!trimmed || trimmed.toLowerCase() === "list") {
        ctx.postMessage({ type: "thinking", show: true, text: "Fetching scaffold list..." });
        try {
            const { scaffolds } = await ctx.client.listScaffolds();
            ctx.postMessage({ type: "thinking", show: false });
            const lines = scaffolds.map((s) => {
                const al = s.aliases.length ? ` (aliases: ${s.aliases.join(", ")})` : "";
                const kind = s.setup_type === "command" ? "CLI" : "files";
                return `  **${s.name}** [${kind}] — ${s.description}${al}`;
            });
            ctx.postMessage({
                type: "reply",
                text: `Available scaffolds:\n\n${lines.join("\n")}\n\nUsage: \`/scaffold <name> <project-name> ['/target/dir']\`\nOmit the directory to pick a folder via dialog.`,
                cls: "msg-system",
            });
        } catch (e) {
            ctx.postMessage({ type: "thinking", show: false });
            ctx.postMessage({ type: "error", text: `Failed to list scaffolds: ${e}` });
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
        ctx.postMessage({
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
            ctx.postMessage({
                type: "reply",
                text: "Scaffold cancelled — no folder selected.",
                cls:  "msg-system",
            });
            return;
        }
    }

    ctx.postMessage({
        type: "thinking",
        show: true,
        text: `Creating ${scaffoldName} project "${projectName}" in ${parentDir}...`,
    });

    let projectDir: string;
    try {
        const result = await ctx.client.scaffold(scaffoldName, projectName, parentDir);
        ctx.postMessage({ type: "thinking", show: false });

        const detail = result.files_created.length > 0
            ? `\n\nFiles created:\n${result.files_created.map((f) => `  - \`${f}\``).join("\n")}`
            : result.command_output
                ? `\n\nCommand output:\n\`\`\`\n${result.command_output.slice(0, 1000)}\n\`\`\``
                : "";

        ctx.postMessage({
            type: "reply",
            text: `${result.message}${detail}`,
            cls: "msg-system",
        });
        projectDir = result.project_dir;
    } catch (e) {
        ctx.postMessage({ type: "thinking", show: false });
        ctx.postMessage({ type: "error", text: `Scaffold failed: ${e}` });
        return;
    }

    // Store the project dir so the new window knows to auto-run /init after opening
    await ctx.extensionContext.globalState.update("lean-ai.pendingScaffoldInit", projectDir);

    ctx.postMessage({
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

// ── /agent — send a prompt directly to the FSM workflow ──────────────

export async function handleAgentCommand(
    ctx: SlashCommandContext,
    args: string,
): Promise<void> {
    const prompt = args.trim();
    if (!prompt) {
        ctx.postMessage({
            type: "error",
            text: "Usage: `/agent <task description>`\nSend a task directly to the agent workflow — skips the chat endpoint and goes straight to plan creation.\n\nExample: `/agent Add input validation to the contact form`",
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

    // Echo the prompt so it's visible in the conversation
    ctx.postMessage({ type: "reply", text: prompt, cls: "msg-user" });
    await ctx.handleAgentMessage(prompt);
}

// ── /fix — skip planning, fix directly ───────────────────────────────

export async function handleFixCommand(
    ctx: SlashCommandContext,
    args: string,
): Promise<void> {
    const prompt = args.trim();
    if (!prompt) {
        ctx.postMessage({
            type: "error",
            text: "Usage: `/fix <description>`\nSkip planning and let the agent explore, diagnose, and fix directly.\n\nExample: `/fix The search index crashes when the repo has no Python files`",
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

    // Echo the prompt so it's visible in the conversation
    ctx.postMessage({ type: "reply", text: `🔧 ${prompt}`, cls: "msg-user" });

    // Auto-include errors/warnings from the current file so the agent has concrete data
    const diagCtx = ctx.getFileDiagnostics();

    // Send with /fix prefix so the backend skips planning
    await ctx.handleAgentMessage(`/fix ${prompt}${diagCtx}`);
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
    const sessionId = ctx.getLastCompletedSessionId();
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
    const sessionId = ctx.getLastCompletedSessionId();
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

/**
 * Workspace-related slash command handlers — complex, multi-step operations
 * that interact with the backend for indexing, context generation, scaffolding,
 * and agent workflows.
 *
 * Extracted from slashCommands.ts to keep each module focused.
 */

import * as vscode from "vscode";
import WebSocket from "ws";
import { SlashCommandContext } from "./slashCommands";

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

    // ── Step 1: Index workspace + embeddings ──
    ctx.postMessage({
        type: "thinking",
        show: true,
        text: "Indexing workspace and generating embeddings...",
    });

    let indexResult: any = { num_parallel: 1 };
    try {
        indexResult = await ctx.client.indexWorkspace(repoRoot, force);

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

    // ── Knowledge index status ──
    const ks = indexResult.knowledge_status;
    if (ks && ks !== "no_knowledge_dir" && ks !== "empty") {
        if (ks === "failed") {
            anyFailure = true;
            ctx.postMessage({
                type: "reply",
                text: "Knowledge indexing failed.",
                cls: "msg-system",
            });
        } else if (ks === "unsupported_files") {
            const exts = (indexResult.knowledge_skipped_extensions ?? []).join(", ");
            ctx.postMessage({
                type: "reply",
                text: `Knowledge: found files but no reader for ${exts}. Install deps: pip install 'lean-ai[knowledge]'`,
                cls: "msg-system",
            });
        } else {
            const docCount = indexResult.knowledge_doc_count ?? 0;
            const chunkCount = indexResult.knowledge_chunk_count ?? 0;
            if (docCount > 0) {
                const kMode = ks === "already_indexed" ? "already up to date" : "complete";
                ctx.postMessage({
                    type: "reply",
                    text: `Knowledge index ${kMode}: ${docCount} docs, ${chunkCount} chunks.`,
                    cls: "msg-system",
                });
            }
        }
    }

    // ── Embedding status ──
    // Statuses: success | up_to_date | partial | failed | skipped
    // The breakdown fields let us distinguish "nothing to do" (up_to_date)
    // from "silently broken" (failed/partial), which the previous
    // collapsed-to-success message could not.
    const es = indexResult.embedding_status;
    const codeCnt = indexResult.embedding_code_count ?? 0;
    const knowledgeCnt = indexResult.embedding_knowledge_count ?? 0;
    const codeUnchanged = indexResult.embedding_code_unchanged ?? 0;
    const knowledgeUnchanged = indexResult.embedding_knowledge_unchanged ?? 0;
    const failedBatches = indexResult.embedding_failed_batches ?? 0;
    const totalBatches = indexResult.embedding_total_batches ?? 0;
    const detail = indexResult.embedding_message ?? "";

    if (es === "success") {
        const parts: string[] = [];
        if (codeCnt > 0) { parts.push(`${codeCnt} code chunks (${codeUnchanged} unchanged)`); }
        if (knowledgeCnt > 0) { parts.push(`${knowledgeCnt} knowledge chunks (${knowledgeUnchanged} unchanged)`); }
        ctx.postMessage({
            type: "reply",
            text: `Embeddings generated: ${parts.join(" + ")}.`,
            cls: "msg-system",
        });
    } else if (es === "up_to_date") {
        const parts: string[] = [];
        if (codeUnchanged > 0) { parts.push(`${codeUnchanged} code chunks`); }
        if (knowledgeUnchanged > 0) { parts.push(`${knowledgeUnchanged} knowledge chunks`); }
        const body = parts.length
            ? `${parts.join(" + ")} already up to date — no embed calls needed.`
            : "No chunks to embed.";
        ctx.postMessage({
            type: "reply",
            text: `Embeddings: ${body}`,
            cls: "msg-system",
        });
    } else if (es === "partial") {
        anyFailure = true;
        ctx.postMessage({
            type: "reply",
            text: `Embeddings partial: ${detail || `${failedBatches}/${totalBatches} batches failed`}. Check backend logs for details.`,
            cls: "msg-system",
        });
    } else if (es === "failed") {
        anyFailure = true;
        ctx.postMessage({
            type: "reply",
            text: `Embedding generation failed: ${detail || "unknown error"}. Check backend logs.`,
            cls: "msg-system",
        });
    } else if (es === "skipped" && detail) {
        ctx.postMessage({
            type: "reply",
            text: `Embeddings skipped: ${detail}`,
            cls: "msg-system",
        });
    }

    // ── Step 2: Generate project context ──
    const startTime = Date.now();

    const formatElapsed = (): string => {
        const elapsed = Math.floor((Date.now() - startTime) / 1000);
        const mins = Math.floor(elapsed / 60);
        const secs = elapsed % 60;
        return mins > 0
            ? `${mins}m ${secs.toString().padStart(2, "0")}s`
            : `${secs}s`;
    };

    // Progress message updated by both the ticker and progress events.
    let lastProgressMsg = "Generating project context...";

    const ticker = setInterval(() => {
        ctx.postMessage({
            type: "thinking",
            show: true,
            text: `${lastProgressMsg} (${formatElapsed()})`,
        });
    }, 5_000);

    ctx.postMessage({
        type: "thinking",
        show: true,
        text: lastProgressMsg,
    });

    const thinkingCb = (token: string) => {
        ctx.postMessage({ type: "llmThinking", text: token, streaming: true });
    };

    const progressCb = (event: Record<string, unknown>) => {
        const msg = (event["message"] as string) || `${event["phase"]}...`;
        lastProgressMsg = msg;
        ctx.postMessage({
            type: "thinking",
            show: true,
            text: `${msg} (${formatElapsed()})`,
        });
    };

    try {
        const ctxResult = await ctx.client.generateProjectContext(repoRoot, force, thinkingCb, progressCb);
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
    } catch (e) {
        anyFailure = true;
        const error = e instanceof Error ? e.message : String(e);
        ctx.postMessage({
            type: "reply",
            text: `Project context generation failed: ${error}`,
            cls: "msg-system",
        });
    }

    clearInterval(ticker);

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
        const styleResult = await ctx.client.generateStyleGuide(repoRoot, true, (token) => {
            ctx.postMessage({ type: "llmThinking", text: token, streaming: true });
        });
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

// ── /agent — send a prompt directly to the agent workflow ─────────────

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

// ── /request — skip planning, neutral prompt with search ─────────────

export async function handleRequestCommand(
    ctx: SlashCommandContext,
    args: string,
): Promise<void> {
    const prompt = args.trim();
    if (!prompt) {
        ctx.postMessage({
            type: "error",
            text: "Usage: `/request <description>`\nSkip planning and let the agent work directly on the task with internet search.\n\nExample: `/request Write a comprehensive guide on our authentication system`",
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

    // Send with /request prefix so the backend uses neutral prompt + search tools
    await ctx.handleAgentMessage(`/request ${prompt}`);
}

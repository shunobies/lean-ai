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
import { DocxOutputExistsError } from "./backendClient";
import { slugify } from "./slugify";

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

    // ── Reference library index status ──
    const ks = indexResult.reference_status;
    if (ks && ks !== "no_reference_dir" && ks !== "empty") {
        if (ks === "failed") {
            anyFailure = true;
            ctx.postMessage({
                type: "reply",
                text: "Reference library indexing failed.",
                cls: "msg-system",
            });
        } else if (ks === "unsupported_files") {
            const exts = (indexResult.reference_skipped_extensions ?? []).join(", ");
            ctx.postMessage({
                type: "reply",
                text: `Reference library: found files but no reader for ${exts}. Install deps: pip install 'lean-ai[reference]'`,
                cls: "msg-system",
            });
        } else {
            const docCount = indexResult.reference_doc_count ?? 0;
            const chunkCount = indexResult.reference_chunk_count ?? 0;
            if (docCount > 0) {
                const kMode = ks === "already_indexed" ? "already up to date" : "complete";
                ctx.postMessage({
                    type: "reply",
                    text: `Reference library ${kMode}: ${docCount} docs, ${chunkCount} chunks.`,
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
    const referenceCnt = indexResult.embedding_reference_count ?? 0;
    const codeUnchanged = indexResult.embedding_code_unchanged ?? 0;
    const referenceUnchanged = indexResult.embedding_reference_unchanged ?? 0;
    const failedBatches = indexResult.embedding_failed_batches ?? 0;
    const totalBatches = indexResult.embedding_total_batches ?? 0;
    const detail = indexResult.embedding_message ?? "";

    if (es === "success") {
        const parts: string[] = [];
        if (codeCnt > 0) { parts.push(`${codeCnt} code chunks (${codeUnchanged} unchanged)`); }
        if (referenceCnt > 0) { parts.push(`${referenceCnt} reference chunks (${referenceUnchanged} unchanged)`); }
        ctx.postMessage({
            type: "reply",
            text: `Embeddings generated: ${parts.join(" + ")}.`,
            cls: "msg-system",
        });
    } else if (es === "up_to_date") {
        const parts: string[] = [];
        if (codeUnchanged > 0) { parts.push(`${codeUnchanged} code chunks`); }
        if (referenceUnchanged > 0) { parts.push(`${referenceUnchanged} reference chunks`); }
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

// ── /interview-prep — tailored resume + cover letter workflow ────────

/**
 * Prompt the user for a docx resume, company, job title, and optional
 * job posting URL; convert the resume to markdown deterministically,
 * then kick off a ``/request`` session that researches the company,
 * asks clarifying questions, and writes a tailored resume, cover
 * letter, research notes, and a 20+ question interview prep file into
 * ``applications/{slug}/``.
 *
 * Designed to pair with the ``job-search`` scaffold: each application
 * becomes its own subfolder under ``applications/``, and the command
 * appends a row to ``applications.md`` (the tracker) when that file
 * exists at the workspace root.
 */
export async function handleInterviewPrepCommand(
    ctx: SlashCommandContext,
    _args: string,
): Promise<void> {
    const ws = ctx.getWs();
    if (ws && ws.readyState === WebSocket.OPEN) {
        ctx.postMessage({
            type: "error",
            text: "An agent workflow is already running. Wait for it to complete, or start a new chat first.",
        });
        return;
    }

    const healthy = await ctx.client.healthCheck();
    if (!healthy) {
        ctx.postMessage({
            type: "error",
            text: "Backend not available. Start the server:\ncd backend && uvicorn lean_ai.main:app --reload --port 8422",
        });
        return;
    }

    // Step 1: resume picker
    const pick = await vscode.window.showOpenDialog({
        canSelectFiles: true,
        canSelectFolders: false,
        canSelectMany: false,
        filters: { "Word documents": ["docx"] },
        openLabel: "Select resume (.docx)",
        title: "Interview Prep — Select your resume",
    });
    if (!pick || pick.length === 0) {
        return;
    }
    const resumePath = pick[0].fsPath;

    // Step 2: company
    const company = await vscode.window.showInputBox({
        title: "Interview Prep — Company",
        prompt: "Company name",
        placeHolder: "e.g. Acme Corp",
        ignoreFocusOut: true,
        validateInput: (v) => (v && v.trim() ? null : "Company name is required."),
    });
    if (company === undefined) return;

    // Step 3: job title
    const jobTitle = await vscode.window.showInputBox({
        title: "Interview Prep — Job title",
        prompt: "Job title",
        placeHolder: "e.g. Senior Software Engineer",
        ignoreFocusOut: true,
        validateInput: (v) => (v && v.trim() ? null : "Job title is required."),
    });
    if (jobTitle === undefined) return;

    // Step 4: optional job URL
    const jobUrl = await vscode.window.showInputBox({
        title: "Interview Prep — Job posting URL (optional)",
        prompt: "Job posting URL (leave blank if you'd rather paste the description later)",
        placeHolder: "https://...",
        ignoreFocusOut: true,
    });
    if (jobUrl === undefined) return;

    const companySlug = slugify(company);
    const jobSlug = slugify(jobTitle);
    if (!companySlug || !jobSlug) {
        ctx.postMessage({
            type: "error",
            text: "Company and job title must contain at least one alphanumeric character.",
        });
        return;
    }

    const slug = `${companySlug}_${jobSlug}`;
    const appDir = `applications/${slug}`;
    const resumeFile = `${appDir}/resume.md`;
    const coverFile = `${appDir}/cover_letter.md`;
    const researchFile = `${appDir}/research.md`;
    const questionsFile = `${appDir}/interview_questions.md`;

    ctx.postMessage({
        type: "thinking",
        show: true,
        text: `Converting resume to markdown: ${resumeFile}...`,
    });

    const repoRoot = ctx.getRepoRoot();
    try {
        await ctx.client.convertDocx(repoRoot, resumePath, resumeFile, false);
    } catch (e) {
        if (e instanceof DocxOutputExistsError) {
            ctx.postMessage({ type: "thinking", show: false });
            const choice = await vscode.window.showWarningMessage(
                `${resumeFile} already exists. Overwrite the existing markdown resume, or cancel?`,
                { modal: true },
                "Overwrite",
            );
            if (choice === "Overwrite") {
                ctx.postMessage({
                    type: "thinking",
                    show: true,
                    text: `Overwriting ${resumeFile}...`,
                });
                try {
                    await ctx.client.convertDocx(repoRoot, resumePath, resumeFile, true);
                } catch (err) {
                    ctx.postMessage({ type: "thinking", show: false });
                    const msg = err instanceof Error ? err.message : String(err);
                    ctx.postMessage({ type: "error", text: `Resume conversion failed: ${msg}` });
                    return;
                }
            } else {
                return;
            }
        } else {
            ctx.postMessage({ type: "thinking", show: false });
            const msg = e instanceof Error ? e.message : String(e);
            ctx.postMessage({
                type: "error",
                text: (
                    `Resume conversion failed: ${msg}\n\n` +
                    "If python-docx is missing, install with: pip install 'lean-ai[reference]'"
                ),
            });
            return;
        }
    }

    ctx.postMessage({ type: "thinking", show: false });
    ctx.postMessage({
        type: "reply",
        text: (
            `Converted resume to [${resumeFile}](${resumeFile}). ` +
            `Starting tailored research, cover letter, and interview prep workflow — ` +
            `watch this chat for clarifying questions; reply to them in chat as you ` +
            `would any agent message.`
        ),
        cls: "msg-system",
    });

    const today = new Date().toISOString().slice(0, 10);

    const jobUrlLine = jobUrl.trim()
        ? `- Job posting: ${jobUrl.trim()} (fetch it with fetch_url before anything else).`
        : "- No job URL was provided — ask me to paste the job description before you begin research.";

    const jobStep2 = jobUrl.trim()
        ? `2. Fetch the job posting at ${jobUrl.trim()} with fetch_url and note the key requirements and ATS-relevant keywords.`
        : "2. Ask me to paste the job description. Wait for my reply before moving on.";

    const prompt = [
        `I have an interview with ${company.trim()} for the ${jobTitle.trim()} role.`,
        "",
        "Artifacts already in the workspace:",
        `- \`${resumeFile}\` — faithful markdown copy of my master resume. Treat it as the starting point; update it IN PLACE for THIS role.`,
        "- `star_stories.md` (workspace root, optional) — if it exists, read it BEFORE writing the cover letter or interview questions; the stories there are reusable evidence for behavioural answers.",
        "- `applications.md` (workspace root, optional) — the application tracker. If it exists, append one row for this application after you've created the per-application folder (see step 8).",
        jobUrlLine,
        "",
        "Please do the following:",
        "",
        `1. Read \`${resumeFile}\` to understand my background. If \`star_stories.md\` exists at the workspace root, read it too.`,
        jobStep2,
        `3. Research ${company.trim()} using search_internet and fetch_url — mission, core products/services, recent news, culture signals. Save a concise summary to \`${researchFile}\` (company overview, why it matters for THIS role, 3-5 talking points you would surface in the interview, and any red flags you noticed).`,
        "4. Compare my resume against the job requirements. Identify gaps, strengths to emphasise, and ATS-relevant keywords missing from my resume.",
        "5. WHERE YOU HAVE IDEAS BUT LACK REAL INFORMATION, ASK ME CLARIFYING QUESTIONS in your text response before writing anything to disk. Examples:",
        "   - \"Your resume mentions Python — did you use Django or Flask at Acme? The job description emphasises Django.\"",
        "   - \"I see five years of backend work but no leadership signal. Have you led any projects or mentored juniors?\"",
        "   Wait for my answers before updating the resume. My chat replies will be injected as normal messages.",
        `6. Once you have enough real information, update \`${resumeFile}\` in place — keep every factual claim truthful (no invented experience), but re-order, re-word, and re-weight to emphasise what matters for this role. Work ATS-relevant keywords in naturally where they are honestly supported.`,
        `7. Write \`${coverFile}\` — a cover letter tied to the research and the clarifications I provided; use the STAR method where appropriate and pull from \`star_stories.md\` if available.`,
        `8. Write \`${questionsFile}\` — interview preparation with AT LEAST 20 questions total, grouped into sections:`,
        "   - **Common role-specific questions** (minimum 5) — standard questions for this role.",
        "   - **Behavioural / STAR questions** (minimum 5) — questions probing leadership, conflict, failure, ownership. If `star_stories.md` has matching stories, reference them by title.",
        "   - **Technical or domain-specific questions** (minimum 5) — based on the job description's required skills. Include expected depth.",
        "   - **Company-specific questions** (minimum 3) — grounded in the research from step 3.",
        "   - **Questions I should ask THEM** (minimum 5) — substantive, specific, tied to the research. Avoid generic \"what's the culture like?\" questions.",
        "   For each prep question, include a one-paragraph suggested answer outline (not a word-for-word script — just the key points and which experience from my resume to reach for).",
        `9. If \`applications.md\` exists at the workspace root, append one row to its markdown table with today's date (${today}), the company, the role, source (LinkedIn / company website / referral / other — ask if unclear), status "applied", last contact "—", next action "Follow up in 7 days", and folder "\`${appDir}/\`". Preserve the existing table formatting.`,
        "",
        "DO NOT fabricate experience. If something is missing from my resume and I have not confirmed it in chat, leave it out. The goal is an honest, well-tailored application — not a creative-writing exercise.",
    ].join("\n");

    await ctx.handleAgentMessage(`/request ${prompt}`);
}

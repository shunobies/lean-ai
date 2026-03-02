/**
 * Chat Participant — @lean-ai chat agent for the full workflow.
 *
 * Uses WebSocket streaming for real-time stage updates, token streaming,
 * and interactive approval flows. Falls back to REST for initial session
 * creation and health checks.
 */

import * as vscode from "vscode";
import type WebSocket from "ws";
import { BackendClient } from "./backendClient";
import { handleStreamMessage, StreamCallbacks } from "./streamHandler";
import type { WSMessage } from "./types";

// Track active session + WebSocket per workspace
const activeSessions = new Map<string, string>();
const activeWebSockets = new Map<string, WebSocket>();

function getRepoRoot(): string {
    const folders = vscode.workspace.workspaceFolders;
    if (folders && folders.length > 0) {
        return folders[0].uri.fsPath;
    }
    return ".";
}

async function getOrCreateSession(client: BackendClient): Promise<string> {
    const repoRoot = getRepoRoot();
    const existing = activeSessions.get(repoRoot);
    if (existing) {
        return existing;
    }

    const { session_id } = await client.createSession(repoRoot);
    activeSessions.set(repoRoot, session_id);
    return session_id;
}

function ensureWebSocket(
    client: BackendClient,
    sessionId: string,
    onMessage: (msg: WSMessage) => void,
): WebSocket {
    const existing = activeWebSockets.get(sessionId);
    if (existing && existing.readyState === 1 /* WebSocket.OPEN */) {
        return existing;
    }

    // Close stale connection
    if (existing) {
        try { existing.close(); } catch { /* ignore */ }
        activeWebSockets.delete(sessionId);
    }

    const ws = client.connectWebSocket(
        sessionId,
        onMessage,
        (err) => console.error("Chat participant WS error:", err),
        () => activeWebSockets.delete(sessionId),
    );
    activeWebSockets.set(sessionId, ws);
    return ws;
}

/**
 * Wait for the WebSocket to reach OPEN state (readyState === 1).
 */
function waitForOpen(ws: WebSocket, timeoutMs: number = 5000): Promise<void> {
    return new Promise((resolve, reject) => {
        if (ws.readyState === 1) {
            resolve();
            return;
        }
        const timer = setTimeout(() => {
            reject(new Error("WebSocket connection timed out"));
        }, timeoutMs);
        ws.once("open", () => {
            clearTimeout(timer);
            resolve();
        });
        ws.once("error", (err: Error) => {
            clearTimeout(timer);
            reject(err);
        });
    });
}

export async function handleChatRequest(
    request: vscode.ChatRequest,
    context: vscode.ChatContext,
    stream: vscode.ChatResponseStream,
    token: vscode.CancellationToken,
): Promise<vscode.ChatResult> {
    const client = BackendClient.getInstance();

    // Check backend health
    const healthy = await client.healthCheck();
    if (!healthy) {
        stream.markdown(
            "**Backend not available.** Start the Lean AI backend with:\n\n" +
            "```\ncd backend && uvicorn lean_ai.main:app --reload\n```",
        );
        return {};
    }

    try {
        const sessionId = await getOrCreateSession(client);

        // Run the workflow over WebSocket and collect results via a promise
        const result = await runWebSocketWorkflow(client, sessionId, request.prompt, stream, token);

        return { metadata: { sessionId, ...result } };
    } catch (e) {
        const error = e instanceof Error ? e.message : String(e);
        stream.markdown(`**Error:** ${error}`);
        return {};
    }
}

interface WorkflowResult {
    stage?: string;
    completed?: boolean;
    needsApproval?: boolean;
}

/**
 * Send a user message over WebSocket and stream all stage updates,
 * tokens, diffs, test results etc. back through the Chat Response Stream.
 *
 * Resolves when the workflow reaches a pause point (approval required,
 * clarification needed) or completes/errors.
 */
function runWebSocketWorkflow(
    client: BackendClient,
    sessionId: string,
    content: string,
    stream: vscode.ChatResponseStream,
    token: vscode.CancellationToken,
): Promise<WorkflowResult> {
    return new Promise((resolve, reject) => {
        let currentStage = "";
        let settled = false;

        const finish = (result: WorkflowResult) => {
            if (!settled) {
                settled = true;
                resolve(result);
            }
        };

        const callbacks: StreamCallbacks = {
            onToken(data) {
                stream.markdown(data);
            },

            onStageChange(stage, _previousStage) {
                currentStage = stage;
                stream.progress(`Stage: ${formatStageName(stage)}`);
            },

            onStageStatus(stage, status, summary) {
                if (status === "running") {
                    stream.progress(`${formatStageName(stage)}...`);
                } else if (status === "done" && summary) {
                    stream.markdown(`\n\n**${formatStageName(stage)}:** ${summary}\n\n`);
                }
            },

            onApprovalRequired(artifactId, artifactType, preview) {
                stream.markdown("\n\n---\n**Plan requires your approval.**\n\n");
                if (preview) {
                    stream.markdown(`${preview.slice(0, 1000)}\n\n`);
                }
                stream.button({
                    command: "lean-ai.approve",
                    title: "Approve Plan",
                });
                stream.button({
                    command: "lean-ai.reject",
                    title: "Reject Plan",
                });
                finish({ stage: currentStage, needsApproval: true });
            },

            onClarificationNeeded(questions, improvedPrompt) {
                stream.markdown("**Clarification needed:**\n\n");
                for (const q of questions) {
                    stream.markdown(`- ${q}\n`);
                }
                if (improvedPrompt) {
                    stream.markdown(`\n**Improved prompt:** ${improvedPrompt}\n`);
                }
                stream.markdown("\nPlease answer the questions above and send another message.");
                finish({ stage: "CLARIFICATION" });
            },

            onToolProgress(toolName, status, error) {
                if (status === "started") {
                    stream.progress(`Running tool: ${toolName}`);
                } else if (status === "completed") {
                    stream.markdown(`\n\u2705 Tool \`${toolName}\` completed\n`);
                } else if (status === "failed") {
                    const errMsg = error ? `: ${error}` : "";
                    stream.markdown(`\n\u274c Tool \`${toolName}\` failed${errMsg}\n`);
                }
            },

            onDiff(filePath, diff) {
                stream.markdown(`\n**File changed:** \`${filePath}\`\n\`\`\`diff\n${diff}\n\`\`\`\n`);
            },

            onTestResult(passed, output) {
                const icon = passed ? "\u2705" : "\u274c";
                stream.markdown(`\n${icon} **Tests ${passed ? "passed" : "failed"}**\n\`\`\`\n${output}\n\`\`\`\n`);
            },

            onPlanRevision(reviewFeedback, revisionNumber) {
                stream.markdown(`\n**Plan revision #${revisionNumber}:** ${reviewFeedback}\n`);
            },

            onPlanRejected(feedback, stage) {
                stream.markdown(`\n**Plan rejected** at ${formatStageName(stage)}: ${feedback}\n`);
            },

            onError(message, recoverable) {
                stream.markdown(`\n**Error${recoverable ? " (recoverable)" : ""}:** ${message}\n`);
                if (!recoverable) {
                    finish({ stage: currentStage, completed: false });
                }
            },

            onComplete(summary, bundleId, filesModified) {
                stream.markdown(`\n\n---\n**Complete!** ${summary}\n`);
                if (filesModified && filesModified.length > 0) {
                    stream.markdown(`\n**Files modified:** ${filesModified.join(", ")}\n`);
                }
                finish({ stage: "COMPLETED", completed: true });
            },

            onIndexStatus(status, progress) {
                if (status === "indexing") {
                    const pct = progress !== undefined ? ` (${Math.round(progress * 100)}%)` : "";
                    stream.progress(`Indexing workspace${pct}...`);
                }
            },

            onBranchCreated(branchName, baseBranch, _baseCommitSha) {
                stream.markdown(`\n🌿 Created plan branch \`${branchName}\` from \`${baseBranch}\`\n`);
            },

            onCheckpoint(stepIndex, stepDescription, status, _headCommitSha) {
                if (status === "completed") {
                    stream.markdown(`\n✅ Checkpoint ${stepIndex + 1}: ${stepDescription}\n`);
                } else if (status === "started") {
                    stream.progress(`Step ${stepIndex + 1}: ${stepDescription}`);
                }
            },

            onMergeComplete(mergeSha, branchDeleted) {
                const shortSha = mergeSha.slice(0, 7);
                let text = `\n🔀 Plan branch merged (${shortSha})`;
                if (branchDeleted) {
                    text += " — branch cleaned up";
                }
                stream.markdown(text + "\n");
            },

            onPong() {
                // keepalive response — no user-facing action
            },
        };

        const ws = ensureWebSocket(client, sessionId, (msg) => {
            handleStreamMessage(msg, callbacks);
        });

        // Handle cancellation
        token.onCancellationRequested(() => {
            finish({ stage: currentStage, completed: false });
        });

        // Send the user message once the socket is open
        waitForOpen(ws).then(() => {
            ws.send(JSON.stringify({ type: "user_message", content }));
        }).catch((err) => {
            if (!settled) {
                settled = true;
                reject(err);
            }
        });

        // Safety timeout: resolve after 5 minutes if nothing else does
        setTimeout(() => {
            finish({ stage: currentStage, completed: false });
        }, 5 * 60 * 1000);
    });
}

function formatStageName(stage: string): string {
    const names: Record<string, string> = {
        CLARIFICATION: "Clarification",
        ROUTING: "Routing",
        ENSURE_INDEX: "Indexing",
        PLAN_CREATION: "Planning",
        PLAN_REVIEW: "Plan Review",
        SECURITY_REVIEW: "Security Review",
        DEVOPS_REVIEW: "DevOps Review",
        USER_APPROVAL: "Awaiting Approval",
        IMPLEMENTATION: "Implementation",
        FINALIZATION: "Finalization",
        COMPLETED: "Completed",
    };
    return names[stage] || stage;
}

export function registerApprovalCommands(context: vscode.ExtensionContext): void {
    const client = BackendClient.getInstance();

    context.subscriptions.push(
        vscode.commands.registerCommand("lean-ai.approve", async () => {
            const repoRoot = getRepoRoot();
            const sessionId = activeSessions.get(repoRoot);
            if (!sessionId) {
                vscode.window.showWarningMessage("No active session to approve.");
                return;
            }

            // Send approval over WebSocket if connected, else fall back to REST
            const ws = activeWebSockets.get(sessionId);
            if (ws && ws.readyState === 1) {
                ws.send(JSON.stringify({ type: "approve" }));
                vscode.window.showInformationMessage("Plan approved. Implementation starting...");
            } else {
                try {
                    await client.approve(sessionId);
                    vscode.window.showInformationMessage("Plan approved. Implementation starting...");
                } catch (e) {
                    const error = e instanceof Error ? e.message : String(e);
                    vscode.window.showErrorMessage(`Approval failed: ${error}`);
                }
            }
        }),
    );

    context.subscriptions.push(
        vscode.commands.registerCommand("lean-ai.reject", async () => {
            const repoRoot = getRepoRoot();
            const sessionId = activeSessions.get(repoRoot);
            if (!sessionId) {
                vscode.window.showWarningMessage("No active session to reject.");
                return;
            }

            const feedback = await vscode.window.showInputBox({
                prompt: "Provide feedback for the rejection",
                placeHolder: "What should be changed?",
            });

            if (feedback === undefined) {
                return; // Cancelled
            }

            // Send rejection as a user message over WebSocket (backend treats
            // messages during USER_APPROVAL as rejections with feedback)
            const ws = activeWebSockets.get(sessionId);
            if (ws && ws.readyState === 1) {
                ws.send(JSON.stringify({
                    type: "user_message",
                    content: feedback || "Rejected without feedback",
                }));
                vscode.window.showInformationMessage("Plan rejected. Returning to clarification.");
            } else {
                try {
                    await client.reject(sessionId, feedback || "Rejected without feedback");
                    vscode.window.showInformationMessage("Plan rejected. Returning to clarification.");
                } catch (e) {
                    const error = e instanceof Error ? e.message : String(e);
                    vscode.window.showErrorMessage(`Rejection failed: ${error}`);
                }
            }
        }),
    );
}

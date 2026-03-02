/**
 * WebSocket message handler and formatting helpers for the sidebar.
 * Extracted from sidebarProvider.ts for maintainability.
 */

import type { WSMessage } from "./types";

export interface WsHandlerContext {
    postMessage(msg: Record<string, unknown>): void;
    closeWebSocket(): void;
    clearSession(): void;
}

export function formatStageName(stage: string): string {
    const names: Record<string, string> = {
        CLARIFICATION: "Understanding your request",
        ROUTING: "Determining task type",
        PLAN_CREATION: "Creating plan",
        PLAN_REVIEW: "Reviewing plan",
        SECURITY_REVIEW: "Security review",
        USER_APPROVAL: "Awaiting approval",
        IMPLEMENTATION: "Implementing",
        FINALIZATION: "Finalizing",
    };
    return names[stage] || stage.replace(/_/g, " ").toLowerCase();
}

export function formatApprovalMessage(msg: Record<string, unknown>): string {
    const parts: string[] = [];
    parts.push("**Plan review passed!** Approve to proceed, or type feedback to revise.\n");

    const plan = msg.plan as Record<string, unknown> | undefined;
    if (plan) {
        if (plan.steps && Array.isArray(plan.steps)) {
            parts.push("**Plan steps:**");
            for (const step of plan.steps) {
                const s = step as Record<string, unknown>;
                parts.push(`${s.order}. ${s.description}`);
            }
        }
        if (plan.affected_files && Array.isArray(plan.affected_files)) {
            parts.push(`\n**Files:** ${(plan.affected_files as string[]).join(", ")}`);
        }
    }

    if (msg.review_feedback) {
        parts.push(`\n**Reviewer notes:** ${msg.review_feedback}`);
    }

    return parts.join("\n");
}

export function handleWsMessage(msg: WSMessage, ctx: WsHandlerContext): void {
    // All messages come as raw objects; we switch on type to handle them.
    const raw = msg as unknown as Record<string, unknown>;

    switch (raw.type) {
        // --- Stage status: brief progress indicator per stage ---
        case "stage_status": {
            const stage = raw.stage as string;
            const status = raw.status as string;
            const summary = raw.summary as string | undefined;

            if (status === "running") {
                ctx.postMessage({ type: "stage", stage });
                ctx.postMessage({
                    type: "thinking",
                    show: true,
                    text: `${formatStageName(stage)}...`,
                });
                // Hide approval buttons once implementation starts
                if (stage === "IMPLEMENTATION" || stage === "FINALIZATION") {
                    ctx.postMessage({ type: "hideApproval" });
                }
            } else if (status === "done") {
                ctx.postMessage({ type: "thinking", show: false });
                if (summary) {
                    ctx.postMessage({
                        type: "reply",
                        text: `**${formatStageName(stage)}:** ${summary}`,
                        cls: "msg-system",
                    });
                }
            } else if (status === "needs_input") {
                // Stage paused, waiting for user input (handled by specific message types below)
                ctx.postMessage({ type: "thinking", show: false });
            }
            break;
        }

        // --- Clarification pauses and asks the user ---
        case "clarification_needed": {
            ctx.postMessage({ type: "thinking", show: false });
            const questions = (raw.questions as string[]) || [];
            const improved = (raw.improved_prompt as string) || "";
            let text = "**Clarification needed:**\n";
            for (const q of questions) {
                text += `- ${q}\n`;
            }
            if (improved) {
                text += `\n**Understood as:** ${improved}`;
            }
            ctx.postMessage({ type: "reply", text });
            ctx.postMessage({
                type: "reply",
                text: "Please answer the questions above and send another message.",
                cls: "msg-system",
            });
            ctx.postMessage({ type: "sendEnabled" });
            break;
        }

        // --- Plan approved by reviewer, show to user for approval ---
        case "approval_required": {
            ctx.postMessage({ type: "thinking", show: false });
            ctx.postMessage({
                type: "reply",
                text: formatApprovalMessage(raw),
            });
            ctx.postMessage({ type: "showApproval" });
            ctx.postMessage({ type: "sendEnabled" });
            break;
        }

        // --- Plan revision: reviewer rejected, auto-retrying ---
        case "plan_revision": {
            ctx.postMessage({
                type: "reply",
                text: `**Plan revision #${raw.revision_number}** — reviewer feedback: ${raw.review_feedback || "needs improvement"}`,
                cls: "msg-system",
            });
            break;
        }

        // --- User provided feedback instead of approving ---
        case "plan_rejected": {
            ctx.postMessage({ type: "thinking", show: false });
            ctx.postMessage({ type: "hideApproval" });
            const rejFeedback = raw.feedback as string;
            ctx.postMessage({
                type: "reply",
                text: rejFeedback
                    ? `Revising plan based on your feedback: "${rejFeedback}"`
                    : "Revising plan based on your feedback...",
                cls: "msg-system",
            });
            // Don't re-enable send — the workflow continues automatically
            break;
        }

        // --- Workflow complete → close WS, return to chat ---
        case "complete": {
            ctx.postMessage({ type: "thinking", show: false });
            ctx.postMessage({ type: "hideApproval" });
            let completeText = (raw.summary as string) || "Workflow complete.";
            const filesModified = raw.files_modified as string[] | undefined;
            if (filesModified && filesModified.length > 0) {
                completeText += `\n\n**Files modified:** ${filesModified.join(", ")}`;
            }
            const planBranch = raw.plan_branch as string | undefined;
            const mergeCommitSha = raw.merge_commit_sha as string | undefined;
            if (planBranch) {
                completeText += `\n**Branch:** \`${planBranch}\``;
            }
            if (mergeCommitSha) {
                completeText += ` (merged: ${mergeCommitSha.slice(0, 7)})`;
            }
            const tps = raw.tokens_per_second as number | null | undefined;
            const evalCount = raw.eval_count as number | null | undefined;
            if (tps != null) {
                const countStr = evalCount != null ? ` · ${evalCount.toLocaleString()} tokens` : "";
                completeText += `\n\n*${tps} tok/s${countStr}*`;
            }
            ctx.postMessage({
                type: "reply",
                text: completeText,
                cls: "msg-ai",
            });
            ctx.postMessage({ type: "sendEnabled" });
            // Close WS so user returns to chat mode automatically
            ctx.closeWebSocket();
            ctx.clearSession();
            ctx.postMessage({ type: "stage", stage: null });
            break;
        }

        // --- Error (terminal if non-recoverable) ---
        case "error": {
            ctx.postMessage({ type: "thinking", show: false });
            ctx.postMessage({
                type: "error",
                text: raw.message as string || "Unknown error",
            });
            ctx.postMessage({ type: "sendEnabled" });
            // Close WS on non-recoverable errors so user returns to chat
            if (!raw.recoverable) {
                ctx.closeWebSocket();
                ctx.clearSession();
                ctx.postMessage({ type: "stage", stage: null });
            }
            break;
        }

        // --- Tool progress: show tool execution status ---
        case "tool_progress": {
            const tool = raw.tool_name as string;
            const toolStatus = raw.status as string;
            if (toolStatus === "started") {
                ctx.postMessage({
                    type: "reply",
                    text: `Running tool: \`${tool}\`...`,
                    cls: "msg-system",
                });
            } else if (toolStatus === "completed") {
                ctx.postMessage({
                    type: "reply",
                    text: `Tool \`${tool}\` completed.`,
                    cls: "msg-system",
                });
            } else if (toolStatus === "failed") {
                ctx.postMessage({
                    type: "reply",
                    text: `Tool \`${tool}\` failed.`,
                    cls: "msg-system",
                });
            }
            break;
        }

        // --- Diff: show file modification ---
        case "diff": {
            const filePath = raw.file_path as string;
            const diffContent = raw.diff as string;
            const preview = diffContent
                .split("\n")
                .slice(0, 10)
                .join("\n");
            ctx.postMessage({
                type: "reply",
                text: `**Modified:** \`${filePath}\`\n\`\`\`diff\n${preview}\n\`\`\``,
                cls: "msg-ai",
            });
            break;
        }

        // --- Test result: show pass/fail ---
        case "test_result": {
            const passed = raw.passed as boolean;
            const output = ((raw.output as string) || "").slice(0, 300);
            ctx.postMessage({
                type: "reply",
                text: `**Tests ${passed ? "PASSED" : "FAILED"}**\n\`\`\`\n${output}\n\`\`\``,
                cls: passed ? "msg-system" : "msg-error",
            });
            break;
        }

        // --- Index status: workspace re-indexing progress ---
        case "index_status": {
            const indexStatus = raw.status as string;
            if (indexStatus === "indexing") {
                ctx.postMessage({
                    type: "reply",
                    text: "Re-indexing workspace...",
                    cls: "msg-system",
                });
            } else if (indexStatus === "ready") {
                ctx.postMessage({
                    type: "reply",
                    text: "Workspace index updated.",
                    cls: "msg-system",
                });
            }
            break;
        }

        // --- Stage change (legacy/future) ---
        case "stage_change": {
            ctx.postMessage({ type: "stage", stage: raw.stage as string });
            break;
        }

        // --- Session History: branch created ---
        case "branch_created": {
            const branchName = raw.branch_name as string;
            const baseBranch = raw.base_branch as string;
            ctx.postMessage({
                type: "reply",
                text: `Created plan branch \`${branchName}\` from \`${baseBranch}\``,
                cls: "msg-system",
            });
            break;
        }

        // --- Session History: checkpoint reached ---
        case "checkpoint": {
            const stepIdx = raw.step_index as number;
            const stepDesc = raw.step_description as string;
            const cpStatus = raw.status as string;
            if (cpStatus === "completed") {
                ctx.postMessage({
                    type: "reply",
                    text: `Checkpoint ${stepIdx + 1}: ${stepDesc} ✓`,
                    cls: "msg-system",
                });
            }
            break;
        }

        // --- Session History: merge complete ---
        case "merge_complete": {
            const mergeSha = (raw.merge_sha as string).slice(0, 7);
            const branchDeleted = raw.branch_deleted as boolean;
            let mergeText = `Plan branch merged (${mergeSha})`;
            if (branchDeleted) {
                mergeText += " — branch cleaned up";
            }
            ctx.postMessage({
                type: "reply",
                text: mergeText,
                cls: "msg-system",
            });
            break;
        }

        case "pong":
            break;

        // --- Tool approval gate: destructive command needs user confirmation ---
        case "tool_approval_required": {
            // Forward to the webview which renders the inline approve/deny card.
            ctx.postMessage({
                type: "tool_approval_required",
                token: raw.token as string,
                tool_name: raw.tool_name as string,
                command: raw.command as string,
                reason: raw.reason as string,
            });
            break;
        }

        default:
            console.log("Unhandled WS message type:", raw.type);
            break;
    }
}

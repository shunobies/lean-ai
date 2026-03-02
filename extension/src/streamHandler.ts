/**
 * WebSocket message stream handler — parses typed messages and dispatches to callbacks.
 */

import type { WSMessage } from "./types";

export interface StreamCallbacks {
    onToken?: (data: string) => void;
    onStageChange?: (stage: string, previousStage: string) => void;
    onApprovalRequired?: (artifactId: string, artifactType: string, preview: string) => void;
    onToolProgress?: (toolName: string, status: string, error?: string) => void;
    onDiff?: (filePath: string, diff: string) => void;
    onTestResult?: (passed: boolean, output: string) => void;
    onError?: (message: string, recoverable: boolean) => void;
    onComplete?: (summary: string, bundleId?: string, filesModified?: string[]) => void;
    onIndexStatus?: (status: string, progress?: number) => void;
    onStageStatus?: (stage: string, status: string, summary?: string) => void;
    onClarificationNeeded?: (questions: string[], improvedPrompt?: string) => void;
    onPlanRevision?: (reviewFeedback: string, revisionNumber: number) => void;
    onPlanRejected?: (feedback: string, stage: string) => void;
    onPong?: () => void;
    // Session history callbacks (FR-G1, FR-C1, FR-G5)
    onBranchCreated?: (branchName: string, baseBranch: string, baseCommitSha: string) => void;
    onCheckpoint?: (stepIndex: number, stepDescription: string, status: string, headCommitSha: string | null) => void;
    onMergeComplete?: (mergeSha: string, branchDeleted: boolean) => void;
}

export function handleStreamMessage(msg: WSMessage, callbacks: StreamCallbacks): void {
    switch (msg.type) {
        case "token":
            callbacks.onToken?.(msg.data);
            break;
        case "stage_change":
            callbacks.onStageChange?.(msg.stage, msg.previous_stage);
            break;
        case "approval_required":
            callbacks.onApprovalRequired?.(msg.artifact_id, msg.artifact_type, msg.content_preview);
            break;
        case "tool_progress":
            callbacks.onToolProgress?.(msg.tool_name, msg.status, msg.error);
            break;
        case "diff":
            callbacks.onDiff?.(msg.file_path, msg.diff);
            break;
        case "test_result":
            callbacks.onTestResult?.(msg.passed, msg.output);
            break;
        case "error":
            callbacks.onError?.(msg.message, msg.recoverable);
            break;
        case "complete":
            callbacks.onComplete?.(msg.summary, msg.bundle_id, msg.files_modified);
            break;
        case "index_status":
            callbacks.onIndexStatus?.(msg.status, msg.progress);
            break;
        case "stage_status":
            callbacks.onStageStatus?.(msg.stage, msg.status, msg.summary);
            break;
        case "clarification_needed":
            callbacks.onClarificationNeeded?.(msg.questions, msg.improved_prompt);
            break;
        case "plan_revision":
            callbacks.onPlanRevision?.(msg.review_feedback, msg.revision_number);
            break;
        case "plan_rejected":
            callbacks.onPlanRejected?.(msg.feedback, msg.stage);
            break;
        case "pong":
            callbacks.onPong?.();
            break;
        // Session history messages
        case "branch_created":
            callbacks.onBranchCreated?.(msg.branch_name, msg.base_branch, msg.base_commit_sha);
            break;
        case "checkpoint":
            callbacks.onCheckpoint?.(msg.step_index, msg.step_description, msg.status, msg.head_commit_sha);
            break;
        case "merge_complete":
            callbacks.onMergeComplete?.(msg.merge_sha, msg.branch_deleted);
            break;
    }
}

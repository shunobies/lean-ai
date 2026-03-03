/**
 * Shared TypeScript interfaces for the Lean AI VSCode extension.
 */

// --- WebSocket Message Types ---

export type WSMessage =
    | TokenMessage
    | StageChangeMessage
    | ApprovalRequiredMessage
    | ToolProgressMessage
    | DiffMessage
    | TestResultMessage
    | ErrorMessage
    | CompleteMessage
    | IndexStatusMessage
    | StageStatusMessage
    | ClarificationNeededMessage
    | PlanRevisionMessage
    | PlanRejectedMessage
    | PongMessage
    | BranchCreatedMessage
    | CheckpointMessage
    | MergeCompleteMessage
    | AssistantContentMessage;

export interface TokenMessage {
    type: "token";
    data: string;
}

export interface StageChangeMessage {
    type: "stage_change";
    stage: string;
    previous_stage: string;
}

export interface ApprovalRequiredMessage {
    type: "approval_required";
    artifact_id: string;
    artifact_type: string;
    content_preview: string;
}

export interface ToolProgressMessage {
    type: "tool_progress";
    tool_name: string;
    status: "started" | "running" | "completed" | "failed";
    error?: string;
}

export interface DiffMessage {
    type: "diff";
    file_path: string;
    diff: string;
}

export interface TestResultMessage {
    type: "test_result";
    passed: boolean;
    output: string;
}

export interface ErrorMessage {
    type: "error";
    message: string;
    recoverable: boolean;
}

export interface CompleteMessage {
    type: "complete";
    summary: string;
    bundle_id?: string;
    files_modified?: string[];
    plan_branch?: string;
    merge_commit_sha?: string;
}

export interface IndexStatusMessage {
    type: "index_status";
    status: "indexing" | "ready";
    progress?: number;
}

export interface StageStatusMessage {
    type: "stage_status";
    stage: string;
    status: "running" | "done" | "needs_input" | "unknown";
    summary?: string;
}

export interface ClarificationNeededMessage {
    type: "clarification_needed";
    questions: string[];
    improved_prompt?: string;
}

export interface PlanRevisionMessage {
    type: "plan_revision";
    review_feedback: string;
    revision_number: number;
}

export interface PlanRejectedMessage {
    type: "plan_rejected";
    feedback: string;
    stage: string;
}

export interface PongMessage {
    type: "pong";
}

// --- Session History WS Message Types (FR-G1, FR-C1, FR-G5) ---

export interface BranchCreatedMessage {
    type: "branch_created";
    branch_name: string;
    base_branch: string;
    base_commit_sha: string;
}

export interface CheckpointMessage {
    type: "checkpoint";
    step_index: number;
    step_description: string;
    status: string;
    head_commit_sha: string | null;
}

export interface MergeCompleteMessage {
    type: "merge_complete";
    merge_sha: string;
    branch_deleted: boolean;
}

export interface AssistantContentMessage {
    type: "assistant_content";
    content: string;
}

// --- REST API Types ---

export interface CreateSessionResponse {
    session_id: string;
    stage: string;
}

export interface SessionState {
    session_id: string;
    title: string | null;
    stage: string;
    session_status: string;
    task_track: string | null;
    improved_prompt: string | null;
    index_state: string;
    base_branch: string | null;
    base_commit_sha: string | null;
    plan_branch: string | null;
    merge_commit_sha: string | null;
    created_at: string;
    updated_at: string;
}

export interface MessageResponse {
    stage: string;
    response: Record<string, unknown>;
    needs_user_action: boolean;
    action_type?: string;
}

export interface InlinePredictionContext {
    file_path: string;
    language: string;
    prefix: string;
    suffix: string;
    cursor_line: number;
    cursor_character: number;
}

export interface PredictionResult {
    completion: string;
    confidence: number;
    error?: string;
}

// --- Session History Types (FR-S1, FR-S2, FR-C1, FR-D1) ---

export interface SessionSummary {
    session_id: string;
    title: string | null;
    session_status: string;
    workflow_stage: string;
    task_track: string | null;
    base_branch: string | null;
    plan_branch: string | null;
    merge_commit_sha: string | null;
    created_at: string;
    updated_at: string;
}

export interface SessionFilters {
    repo_root?: string;
    status?: string;
    branch?: string;
    since?: string;
    until?: string;
}

export interface CheckpointSummary {
    id: string;
    step_index: number;
    step_description: string | null;
    status: string;
    branch_name: string | null;
    head_commit_sha: string | null;
    created_at: string;
    completed_at: string | null;
}

export interface GitEventSummary {
    id: string;
    event_type: string;
    ref_name: string | null;
    commit_sha: string | null;
    parent_sha: string | null;
    message: string | null;
    plan_artifact_id: string | null;
    checkpoint_id: string | null;
    created_at: string;
}

export interface FileTouchSummary {
    id: string;
    bundle_id: string;
    plan_artifact_id: string;
    git_event_id: string | null;
    file_path: string;
    change_type: string;
    created_at: string;
}

// --- Chat History Persistence Types ---

export interface StoredMessage {
    role: string;
    content: string;
    timestamp: string; // ISO 8601
}

export interface StoredConversation {
    id: string;
    title: string;
    messages: StoredMessage[];
    createdAt: string;  // ISO 8601
    updatedAt: string;  // ISO 8601
    repoRoot: string;
}

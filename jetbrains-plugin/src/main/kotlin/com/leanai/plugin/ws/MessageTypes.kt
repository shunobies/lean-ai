package com.leanai.plugin.ws

import com.google.gson.JsonObject
import com.google.gson.JsonParser

/**
 * Kotlin sealed hierarchy for all WebSocket message types from the backend.
 * Maps 1:1 to the protocol defined in extension/src/types.ts.
 */
sealed class WsMessage {
    companion object {
        /** Parse a raw JSON string into a typed WsMessage. */
        fun parse(raw: String): WsMessage? {
            return try {
                val json = JsonParser.parseString(raw).asJsonObject
                val type = json.get("type")?.asString ?: return null
                when (type) {
                    "stage_status" -> StageStatus(
                        stage = json.str("stage"),
                        status = json.str("status"),
                        summary = json.strOrNull("summary"),
                        phase = json.strOrNull("phase")
                    )
                    "token" -> Token(data = json.str("data"))
                    "assistant_content" -> AssistantContent(
                        content = json.str("content"),
                        streaming = json.boolOrNull("streaming"),
                        done = json.boolOrNull("done")
                    )
                    "thinking_content" -> ThinkingContent(
                        content = json.str("content"),
                        streaming = json.boolOrNull("streaming"),
                        done = json.boolOrNull("done")
                    )
                    "clarification_needed" -> ClarificationNeeded(
                        questions = json.getAsJsonArray("questions")?.map { it.asString } ?: emptyList(),
                        improvedPrompt = json.strOrNull("improved_prompt")
                    )
                    "approval_required" -> ApprovalRequired(
                        plan = json.str("plan"),
                        userSummary = json.strOrNull("user_summary"),
                        reviewFeedback = json.strOrNull("review_feedback")
                    )
                    "plan_revision" -> PlanRevision(
                        revisionNumber = json.get("revision_number")?.asInt ?: 0,
                        reviewFeedback = json.strOrNull("review_feedback")
                    )
                    "plan_rejected" -> PlanRejected(
                        feedback = json.str("feedback"),
                        stage = json.strOrNull("stage")
                    )
                    "tool_progress" -> ToolProgress(
                        tool = json.str("tool"),
                        status = json.str("status"),
                        description = json.strOrNull("description"),
                        output = json.strOrNull("output")
                    )
                    "tool_approval_required" -> ToolApprovalRequired(
                        tool = json.str("tool"),
                        command = json.str("command"),
                        token = json.str("token"),
                        description = json.strOrNull("description")
                    )
                    "diff" -> Diff(
                        filePath = json.str("file_path"),
                        diff = json.str("diff")
                    )
                    "test_result" -> TestResult(
                        passed = json.get("passed")?.asBoolean ?: false,
                        output = json.str("output")
                    )
                    "index_status" -> IndexStatus(
                        status = json.str("status"),
                        progress = json.get("progress")?.asFloat
                    )
                    "branch_created" -> BranchCreated(
                        branchName = json.str("branch_name"),
                        baseBranch = json.strOrNull("base_branch"),
                        baseCommitSha = json.strOrNull("base_commit_sha")
                    )
                    "checkpoint" -> Checkpoint(
                        stepIndex = json.get("step_index")?.asInt ?: 0,
                        stepDescription = json.str("step_description"),
                        status = json.str("status"),
                        headCommitSha = json.strOrNull("head_commit_sha")
                    )
                    "merge_complete" -> MergeComplete(
                        mergeSha = json.strOrNull("merge_sha"),
                        branchDeleted = json.get("branch_deleted")?.asBoolean ?: false
                    )
                    "metrics_update" -> MetricsUpdate(
                        contextPercent = json.get("context_percent")?.asFloat ?: 0f,
                        promptTokens = json.get("prompt_tokens")?.asInt ?: 0,
                        contextWindow = json.get("context_window")?.asInt ?: 0
                    )
                    "context_refreshed" -> ContextRefreshed
                    "vision_description" -> VisionDescription(
                        descriptions = json.getAsJsonArray("descriptions")?.map { it.asString } ?: emptyList()
                    )
                    "complete" -> Complete(
                        summary = json.strOrNull("summary"),
                        filesModified = json.getAsJsonArray("files_modified")?.map { it.asString } ?: emptyList(),
                        planBranch = json.strOrNull("plan_branch"),
                        baseBranch = json.strOrNull("base_branch"),
                        mergeCommitSha = json.strOrNull("merge_commit_sha"),
                        tokensPerSecond = json.get("tokens_per_second")?.asFloat
                    )
                    "cancelled" -> Cancelled
                    "error" -> Error(
                        message = json.str("message"),
                        recoverable = json.get("recoverable")?.asBoolean ?: false
                    )
                    "pong" -> Pong
                    else -> Unknown(type, raw)
                }
            } catch (e: Exception) {
                null
            }
        }

        // Helper extensions for concise JSON parsing
        private fun JsonObject.str(key: String): String =
            get(key)?.asString ?: ""

        private fun JsonObject.strOrNull(key: String): String? =
            get(key)?.takeIf { !it.isJsonNull }?.asString

        private fun JsonObject.boolOrNull(key: String): Boolean? =
            get(key)?.takeIf { !it.isJsonNull }?.asBoolean
    }

    // ── Inbound message types ────────────────────────────────────────────────

    data class StageStatus(
        val stage: String, val status: String,
        val summary: String?, val phase: String?
    ) : WsMessage()

    data class Token(val data: String) : WsMessage()

    data class AssistantContent(
        val content: String, val streaming: Boolean?, val done: Boolean?
    ) : WsMessage()

    data class ThinkingContent(
        val content: String, val streaming: Boolean?, val done: Boolean?
    ) : WsMessage()

    data class ClarificationNeeded(
        val questions: List<String>, val improvedPrompt: String?
    ) : WsMessage()

    data class ApprovalRequired(
        val plan: String, val userSummary: String?, val reviewFeedback: String?
    ) : WsMessage()

    data class PlanRevision(val revisionNumber: Int, val reviewFeedback: String?) : WsMessage()

    data class PlanRejected(val feedback: String, val stage: String?) : WsMessage()

    data class ToolProgress(
        val tool: String, val status: String,
        val description: String?, val output: String?
    ) : WsMessage()

    data class ToolApprovalRequired(
        val tool: String, val command: String,
        val token: String, val description: String?
    ) : WsMessage()

    data class Diff(val filePath: String, val diff: String) : WsMessage()

    data class TestResult(val passed: Boolean, val output: String) : WsMessage()

    data class IndexStatus(val status: String, val progress: Float?) : WsMessage()

    data class BranchCreated(
        val branchName: String, val baseBranch: String?, val baseCommitSha: String?
    ) : WsMessage()

    data class Checkpoint(
        val stepIndex: Int, val stepDescription: String,
        val status: String, val headCommitSha: String?
    ) : WsMessage()

    data class MergeComplete(val mergeSha: String?, val branchDeleted: Boolean) : WsMessage()

    data class MetricsUpdate(
        val contextPercent: Float, val promptTokens: Int, val contextWindow: Int
    ) : WsMessage()

    data object ContextRefreshed : WsMessage()

    data class VisionDescription(val descriptions: List<String>) : WsMessage()

    data class Complete(
        val summary: String?,
        val filesModified: List<String>,
        val planBranch: String?,
        val baseBranch: String?,
        val mergeCommitSha: String?,
        val tokensPerSecond: Float?
    ) : WsMessage()

    data object Cancelled : WsMessage()

    data class Error(val message: String, val recoverable: Boolean) : WsMessage()

    data object Pong : WsMessage()

    data class Unknown(val type: String, val raw: String) : WsMessage()
}

package com.leanai.plugin.ws

import com.intellij.openapi.diagnostic.Logger

/**
 * Routes parsed WebSocket messages to UI callbacks.
 * Port of extension/src/wsHandler.ts (~492 lines).
 *
 * The handler maintains workflow state and delegates rendering to a listener interface.
 */
class WebSocketHandler(private val listener: WebSocketUIListener) {
    private val log = Logger.getInstance(WebSocketHandler::class.java)

    /** Workflow stage tracking. */
    enum class WorkflowStage { IDLE, PLANNING, AWAITING_APPROVAL, IMPLEMENTING, COMPLETE, ERROR }

    var currentStage: WorkflowStage = WorkflowStage.IDLE
        private set

    /** Callback interface for UI updates — implemented by ChatBridge. */
    interface WebSocketUIListener {
        fun onStageChanged(stage: String, status: String, summary: String?)
        fun onAssistantContent(content: String, streaming: Boolean, done: Boolean)
        fun onThinkingContent(content: String, streaming: Boolean, done: Boolean)
        fun onClarificationNeeded(questions: List<String>, improvedPrompt: String?)
        fun onApprovalRequired(plan: String, userSummary: String?, reviewFeedback: String?)
        fun onPlanRevision(revisionNumber: Int, reviewFeedback: String?)
        fun onPlanRejected(feedback: String)
        fun onToolProgress(tool: String, status: String, description: String?, output: String?)
        fun onToolApprovalRequired(tool: String, command: String, token: String, description: String?)
        fun onDiff(filePath: String, diff: String)
        fun onTestResult(passed: Boolean, output: String)
        fun onIndexStatus(status: String, progress: Float?)
        fun onBranchCreated(branchName: String)
        fun onCheckpoint(stepIndex: Int, description: String, status: String)
        fun onMergeComplete(mergeSha: String?, branchDeleted: Boolean)
        fun onMetricsUpdate(contextPercent: Float, promptTokens: Int, contextWindow: Int)
        fun onContextRefreshed()
        fun onVisionDescription(descriptions: List<String>)
        fun onComplete(summary: String?, filesModified: List<String>, tokensPerSecond: Float?)
        fun onCancelled()
        fun onError(message: String, recoverable: Boolean)
    }

    /** Route a parsed WebSocket message to the appropriate UI callback. */
    fun handle(message: WsMessage) {
        when (message) {
            is WsMessage.StageStatus -> {
                currentStage = mapStage(message.stage, message.status)
                listener.onStageChanged(
                    formatStageName(message.stage),
                    message.status,
                    message.summary
                )
            }
            is WsMessage.Token -> {
                // Legacy token streaming — treat as assistant content
                listener.onAssistantContent(message.data, streaming = true, done = false)
            }
            is WsMessage.AssistantContent -> {
                listener.onAssistantContent(
                    message.content,
                    streaming = message.streaming ?: false,
                    done = message.done ?: false
                )
            }
            is WsMessage.ThinkingContent -> {
                listener.onThinkingContent(
                    message.content,
                    streaming = message.streaming ?: false,
                    done = message.done ?: false
                )
            }
            is WsMessage.ClarificationNeeded -> {
                listener.onClarificationNeeded(message.questions, message.improvedPrompt)
            }
            is WsMessage.ApprovalRequired -> {
                currentStage = WorkflowStage.AWAITING_APPROVAL
                listener.onApprovalRequired(message.plan, message.userSummary, message.reviewFeedback)
            }
            is WsMessage.PlanRevision -> {
                listener.onPlanRevision(message.revisionNumber, message.reviewFeedback)
            }
            is WsMessage.PlanRejected -> {
                listener.onPlanRejected(message.feedback)
            }
            is WsMessage.ToolProgress -> {
                listener.onToolProgress(message.tool, message.status, message.description, message.output)
            }
            is WsMessage.ToolApprovalRequired -> {
                listener.onToolApprovalRequired(message.tool, message.command, message.token, message.description)
            }
            is WsMessage.Diff -> {
                listener.onDiff(message.filePath, message.diff)
            }
            is WsMessage.TestResult -> {
                listener.onTestResult(message.passed, message.output)
            }
            is WsMessage.IndexStatus -> {
                listener.onIndexStatus(message.status, message.progress)
            }
            is WsMessage.BranchCreated -> {
                listener.onBranchCreated(message.branchName)
            }
            is WsMessage.Checkpoint -> {
                listener.onCheckpoint(message.stepIndex, message.stepDescription, message.status)
            }
            is WsMessage.MergeComplete -> {
                listener.onMergeComplete(message.mergeSha, message.branchDeleted)
            }
            is WsMessage.MetricsUpdate -> {
                listener.onMetricsUpdate(message.contextPercent, message.promptTokens, message.contextWindow)
            }
            is WsMessage.ContextRefreshed -> {
                listener.onContextRefreshed()
            }
            is WsMessage.VisionDescription -> {
                listener.onVisionDescription(message.descriptions)
            }
            is WsMessage.Complete -> {
                currentStage = WorkflowStage.COMPLETE
                listener.onComplete(message.summary, message.filesModified, message.tokensPerSecond)
            }
            is WsMessage.Cancelled -> {
                currentStage = WorkflowStage.IDLE
                listener.onCancelled()
            }
            is WsMessage.Error -> {
                if (!message.recoverable) currentStage = WorkflowStage.ERROR
                listener.onError(message.message, message.recoverable)
            }
            is WsMessage.Pong -> { /* Heartbeat response, no UI update */ }
            is WsMessage.Unknown -> {
                log.debug("Unknown WS message type: ${message.type}")
            }
        }
    }

    /** Reset handler state for a new workflow. */
    fun reset() {
        currentStage = WorkflowStage.IDLE
    }

    companion object {
        /** Convert backend stage names to human-readable labels. */
        fun formatStageName(stage: String): String = when (stage) {
            "PLAN_CREATION" -> "Creating plan"
            "PLAN_REVIEW" -> "Reviewing plan"
            "AWAITING_APPROVAL" -> "Awaiting approval"
            "IMPLEMENTATION" -> "Implementing"
            "POST_VALIDATION" -> "Validating"
            "VALIDATION_FIX" -> "Fixing validation issues"
            "INDEXING" -> "Indexing workspace"
            "CONTEXT_GENERATION" -> "Generating context"
            "FRAMEWORK_GUIDE" -> "Generating framework guide"
            "INVESTIGATION" -> "Investigating"
            "FIX_EXECUTION" -> "Fixing"
            "REQUEST_EXECUTION" -> "Working"
            "TDD_TEST_WRITING" -> "Writing tests (TDD)"
            "TDD_TEST_REVIEW" -> "Reviewing tests (TDD)"
            "TDD_IMPLEMENTATION" -> "Implementing (TDD)"
            else -> stage.lowercase().replace("_", " ").replaceFirstChar { it.uppercase() }
        }

        private fun mapStage(stage: String, status: String): WorkflowStage = when {
            status == "error" -> WorkflowStage.ERROR
            stage.contains("PLAN") && stage != "PLAN_REVIEW" -> WorkflowStage.PLANNING
            stage == "AWAITING_APPROVAL" -> WorkflowStage.AWAITING_APPROVAL
            stage.contains("IMPLEMENTATION") || stage.contains("EXECUTION") || stage.contains("FIX") -> WorkflowStage.IMPLEMENTING
            else -> WorkflowStage.PLANNING
        }
    }
}

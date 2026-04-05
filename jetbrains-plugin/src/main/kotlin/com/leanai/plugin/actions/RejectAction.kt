package com.leanai.plugin.actions

import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.ui.Messages
import com.leanai.plugin.backend.BackendClient

/**
 * Action to reject the current plan with feedback.
 */
class RejectAction : AnAction() {
    override fun actionPerformed(e: AnActionEvent) {
        val client = BackendClient.getInstance()
        if (!client.isWebSocketConnected()) return

        val feedback = Messages.showInputDialog(
            e.project,
            "Provide feedback for plan rejection:",
            "Reject Plan",
            Messages.getQuestionIcon()
        )

        if (feedback != null) {
            client.wsSendReject(feedback)
        }
    }

    override fun update(e: AnActionEvent) {
        e.presentation.isEnabled = BackendClient.getInstance().isWebSocketConnected()
    }
}

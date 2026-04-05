package com.leanai.plugin.actions

import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.leanai.plugin.backend.BackendClient

/**
 * Action to approve the current plan in an active workflow.
 */
class ApproveAction : AnAction() {
    override fun actionPerformed(e: AnActionEvent) {
        val client = BackendClient.getInstance()
        if (client.isWebSocketConnected()) {
            client.wsSendApprove()
        }
    }

    override fun update(e: AnActionEvent) {
        // Only enable when WebSocket is connected and awaiting approval
        e.presentation.isEnabled = BackendClient.getInstance().isWebSocketConnected()
    }
}

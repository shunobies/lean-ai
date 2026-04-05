package com.leanai.plugin.notifications

import com.intellij.notification.NotificationAction
import com.intellij.notification.NotificationGroupManager
import com.intellij.notification.NotificationType
import com.intellij.openapi.project.Project
import com.intellij.openapi.wm.ToolWindowManager

/**
 * IDE notification manager for Lean AI events.
 * Port of extension/src/notifications.ts (~106 lines).
 *
 * Uses JetBrains' Notifications.Bus — balloon notifications automatically
 * appear in the system tray when the IDE is not focused (OS-level notifications
 * are handled by the platform, no platform-specific shell commands needed).
 */
object NotificationManager {

    private fun group() = NotificationGroupManager.getInstance().getNotificationGroup("Lean AI")

    /** Notify that a plan is ready for approval. */
    fun notifyApprovalNeeded(project: Project) {
        group()
            .createNotification(
                "Plan Ready for Approval",
                "A plan is ready for your review in Lean AI.",
                NotificationType.INFORMATION
            )
            .addAction(NotificationAction.createSimpleExpiring("Go to Lean AI") {
                focusToolWindow(project)
            })
            .notify(project)
    }

    /** Notify that a destructive command needs approval. */
    fun notifyToolApprovalNeeded(project: Project, command: String) {
        group()
            .createNotification(
                "Command Approval Needed",
                "Lean AI wants to run: $command",
                NotificationType.WARNING
            )
            .addAction(NotificationAction.createSimpleExpiring("Go to Lean AI") {
                focusToolWindow(project)
            })
            .notify(project)
    }

    /** Notify that a workflow completed successfully. */
    fun notifyComplete(project: Project, summary: String?) {
        val content = summary ?: "Workflow completed successfully."
        group()
            .createNotification("Lean AI Complete", content, NotificationType.INFORMATION)
            .notify(project)
    }

    /** Notify a terminal error. */
    fun notifyError(project: Project, message: String) {
        group()
            .createNotification("Lean AI Error", message, NotificationType.ERROR)
            .notify(project)
    }

    /** Informational notification. */
    fun notifyInfo(project: Project, message: String) {
        group()
            .createNotification("Lean AI", message, NotificationType.INFORMATION)
            .notify(project)
    }

    /** Focus the Lean AI chat tool window. */
    private fun focusToolWindow(project: Project) {
        val toolWindow = ToolWindowManager.getInstance(project).getToolWindow("Lean AI")
        toolWindow?.show()
    }
}

package com.leanai.plugin.actions

import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.progress.ProgressIndicator
import com.intellij.openapi.progress.ProgressManager
import com.intellij.openapi.progress.Task
import com.leanai.plugin.backend.BackendProcess
import com.leanai.plugin.notifications.NotificationManager

/**
 * Action to restart the Lean AI backend server.
 */
class RestartBackendAction : AnAction() {
    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return

        ProgressManager.getInstance().run(object : Task.Backgroundable(
            project, "Restarting Lean AI Backend", false
        ) {
            override fun run(indicator: ProgressIndicator) {
                try {
                    indicator.text = "Stopping backend..."
                    indicator.fraction = 0.3
                    BackendProcess.getInstance().stop()

                    indicator.text = "Starting backend..."
                    indicator.fraction = 0.6
                    BackendProcess.getInstance().start(project)

                    indicator.fraction = 1.0
                    NotificationManager.notifyInfo(project, "Backend restarted successfully.")
                } catch (ex: Exception) {
                    NotificationManager.notifyError(project, "Failed to restart backend: ${ex.message}")
                }
            }
        })
    }
}

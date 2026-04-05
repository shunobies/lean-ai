package com.leanai.plugin

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.project.Project
import com.intellij.openapi.startup.ProjectActivity
import com.leanai.plugin.backend.BackendInstaller
import com.leanai.plugin.backend.BackendProcess
import com.leanai.plugin.settings.LeanAiSettings

/**
 * Plugin startup activity — runs once per project when the IDE opens.
 * Ensures the backend is installed and running.
 */
class LeanAiPlugin : ProjectActivity {
    private val log = Logger.getInstance(LeanAiPlugin::class.java)

    override suspend fun execute(project: Project) {
        log.info("Lean AI plugin starting for project: ${project.name}")

        val settings = LeanAiSettings.getInstance()
        val backendProcess = BackendProcess.getInstance()

        // Only the first project window installs/starts the backend
        if (!backendProcess.isOwned()) {
            try {
                // Ensure backend is installed (creates venv, pip install if needed)
                val installer = BackendInstaller.getInstance()
                installer.ensureInstalled(project)

                // Start backend process
                backendProcess.start(project)
            } catch (e: Exception) {
                log.error("Failed to start Lean AI backend", e)
                com.leanai.plugin.notifications.NotificationManager.notifyError(
                    project, "Failed to start backend: ${e.message}"
                )
            }
        } else {
            log.info("Backend already owned by another project window, connecting to existing instance")
        }
    }
}

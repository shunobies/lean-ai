package com.leanai.plugin.ui

import com.intellij.openapi.project.DumbAware
import com.intellij.openapi.project.Project
import com.intellij.openapi.wm.ToolWindow
import com.intellij.openapi.wm.ToolWindowFactory
import com.intellij.ui.content.ContentFactory

/**
 * Placeholder for the JCEF-based settings panel.
 * In the initial release, settings are handled by LeanAiConfigurable (native Swing).
 * This factory will be enabled in a future version for the rich settings UI
 * matching the VSCode extension's tabbed provider selection.
 */
class SettingsToolWindowFactory : ToolWindowFactory, DumbAware {
    override fun createToolWindowContent(project: Project, toolWindow: ToolWindow) {
        // For now, delegate to the native settings page
        val panel = javax.swing.JLabel(
            "<html><center>Settings are available at<br>" +
            "Settings > Tools > Lean AI</center></html>",
            javax.swing.SwingConstants.CENTER
        )
        val content = ContentFactory.getInstance().createContent(panel, "Settings", false)
        toolWindow.contentManager.addContent(content)
    }

    // Not registered in plugin.xml for now — will be added when JCEF settings are built
    override fun shouldBeAvailable(project: Project): Boolean = false
}

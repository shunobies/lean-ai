package com.leanai.plugin.ui

import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.project.DumbAware
import com.intellij.openapi.project.Project
import com.intellij.openapi.wm.ToolWindow
import com.intellij.openapi.wm.ToolWindowFactory
import com.intellij.ui.content.ContentFactory
import com.intellij.ui.jcef.JBCefApp
import javax.swing.JComponent
import javax.swing.JLabel
import javax.swing.SwingConstants

/**
 * Factory for the Lean AI Chat tool window.
 * Uses JCEF (JetBrains Chromium Embedded Framework) to render the chat UI,
 * allowing us to reuse ~80% of the existing HTML/CSS/JS from the VSCode extension.
 *
 * Port of extension/src/sidebarProvider.ts + sidebarHtml.ts.
 */
class ChatToolWindowFactory : ToolWindowFactory, DumbAware {
    private val log = Logger.getInstance(ChatToolWindowFactory::class.java)

    override fun createToolWindowContent(project: Project, toolWindow: ToolWindow) {
        val component = createChatPanel(project)
        val content = ContentFactory.getInstance().createContent(component, "Chat", false)
        toolWindow.contentManager.addContent(content)
    }

    private fun createChatPanel(project: Project): JComponent {
        // Check if JCEF is available
        return if (JBCefApp.isSupported()) {
            createJcefPanel(project)
        } else {
            // Fallback: simple label directing user to enable JCEF
            JLabel(
                "<html><center>JCEF (Chromium) is required for the chat UI.<br>" +
                "Please enable it in Help > Find Action > 'Registry' > ide.browser.jcef.enabled</center></html>",
                SwingConstants.CENTER
            )
        }
    }

    private fun createJcefPanel(project: Project): JComponent {
        val chatBridge = ChatBridge(project)
        return chatBridge.component
    }
}

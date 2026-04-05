package com.leanai.plugin.ui

import com.intellij.openapi.actionSystem.ActionManager
import com.intellij.openapi.actionSystem.DefaultActionGroup
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.project.DumbAware
import com.intellij.openapi.project.Project
import com.intellij.openapi.wm.ToolWindow
import com.intellij.openapi.wm.ToolWindowFactory
import com.intellij.ui.content.ContentFactory
import com.intellij.ui.treeStructure.Tree
import com.leanai.plugin.backend.BackendClient
import java.awt.BorderLayout
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit
import javax.swing.*
import javax.swing.tree.DefaultMutableTreeNode
import javax.swing.tree.DefaultTreeModel

/**
 * Session history tree panel.
 * Port of extension/src/sessionTreeProvider.ts (~326 lines).
 *
 * Shows: Sessions → Checkpoints + Git Events
 * Auto-refreshes every 2 minutes.
 */
class SessionsToolWindowFactory : ToolWindowFactory, DumbAware {
    private val log = Logger.getInstance(SessionsToolWindowFactory::class.java)

    override fun createToolWindowContent(project: Project, toolWindow: ToolWindow) {
        val panel = SessionsPanel(project)
        val content = ContentFactory.getInstance().createContent(panel, "Sessions", false)
        toolWindow.contentManager.addContent(content)
    }
}

/**
 * The sessions panel with a JTree showing workflow session history.
 */
class SessionsPanel(private val project: Project) : JPanel(BorderLayout()) {
    private val log = Logger.getInstance(SessionsPanel::class.java)
    private val rootNode = DefaultMutableTreeNode("Sessions")
    private val treeModel = DefaultTreeModel(rootNode)
    private val tree = Tree(treeModel)
    private val scheduler = Executors.newSingleThreadScheduledExecutor { r ->
        Thread(r, "lean-ai-sessions-refresh").apply { isDaemon = true }
    }
    private var refreshFuture: ScheduledFuture<*>? = null

    init {
        // Configure tree
        tree.isRootVisible = false
        tree.showsRootHandles = true
        tree.cellRenderer = SessionTreeCellRenderer()

        // Add context menu
        tree.componentPopupMenu = createContextMenu()

        // Layout
        val scrollPane = JScrollPane(tree)
        add(scrollPane, BorderLayout.CENTER)

        // Toolbar with refresh button
        val toolbar = JPanel().apply {
            layout = BoxLayout(this, BoxLayout.X_AXIS)
            val refreshButton = JButton("Refresh").apply {
                addActionListener { refreshSessions() }
            }
            add(refreshButton)
        }
        add(toolbar, BorderLayout.NORTH)

        // Initial load and auto-refresh
        refreshSessions()
        startAutoRefresh()
    }

    /** Fetch sessions from the backend and rebuild the tree. */
    fun refreshSessions() {
        ApplicationManager.getApplication().executeOnPooledThread {
            val client = BackendClient.getInstance()
            val response = client.listSessions(limit = 50)

            ApplicationManager.getApplication().invokeLater {
                rootNode.removeAllChildren()

                if (response == null || response.sessions.isEmpty()) {
                    rootNode.add(DefaultMutableTreeNode("No sessions yet"))
                } else {
                    for (session in response.sessions) {
                        val sessionNode = DefaultMutableTreeNode(SessionNodeData(session))
                        rootNode.add(sessionNode)
                    }
                }

                treeModel.reload()
            }
        }
    }

    private fun startAutoRefresh() {
        refreshFuture = scheduler.scheduleAtFixedRate({
            refreshSessions()
        }, 120, 120, TimeUnit.SECONDS)
    }

    private fun createContextMenu(): JPopupMenu {
        return JPopupMenu().apply {
            add(JMenuItem("View Session").apply {
                addActionListener {
                    val node = tree.lastSelectedPathComponent as? DefaultMutableTreeNode ?: return@addActionListener
                    val data = node.userObject as? SessionNodeData ?: return@addActionListener
                    log.info("View session: ${data.session.session_id}")
                    // TODO: Open session detail panel
                }
            })
            add(JMenuItem("Merge Branch").apply {
                addActionListener {
                    val node = tree.lastSelectedPathComponent as? DefaultMutableTreeNode ?: return@addActionListener
                    val data = node.userObject as? SessionNodeData ?: return@addActionListener
                    ApplicationManager.getApplication().executeOnPooledThread {
                        BackendClient.getInstance().mergeSession(data.session.session_id)
                        refreshSessions()
                    }
                }
            })
            add(JMenuItem("Abandon Session").apply {
                addActionListener {
                    val node = tree.lastSelectedPathComponent as? DefaultMutableTreeNode ?: return@addActionListener
                    val data = node.userObject as? SessionNodeData ?: return@addActionListener
                    val confirm = JOptionPane.showConfirmDialog(
                        this@SessionsPanel,
                        "Abandon session '${data.session.task ?: data.session.session_id}'?",
                        "Confirm Abandon",
                        JOptionPane.YES_NO_OPTION
                    )
                    if (confirm == JOptionPane.YES_OPTION) {
                        ApplicationManager.getApplication().executeOnPooledThread {
                            BackendClient.getInstance().abandonSession(data.session.session_id)
                            refreshSessions()
                        }
                    }
                }
            })
            addSeparator()
            add(JMenuItem("Delete Session").apply {
                addActionListener {
                    val node = tree.lastSelectedPathComponent as? DefaultMutableTreeNode ?: return@addActionListener
                    val data = node.userObject as? SessionNodeData ?: return@addActionListener
                    val confirm = JOptionPane.showConfirmDialog(
                        this@SessionsPanel,
                        "Delete session '${data.session.task ?: data.session.session_id}'? This cannot be undone.",
                        "Confirm Delete",
                        JOptionPane.YES_NO_OPTION,
                        JOptionPane.WARNING_MESSAGE
                    )
                    if (confirm == JOptionPane.YES_OPTION) {
                        ApplicationManager.getApplication().executeOnPooledThread {
                            BackendClient.getInstance().deleteSession(data.session.session_id)
                            refreshSessions()
                        }
                    }
                }
            })
        }
    }
}

/** Wrapper for session data displayed in tree nodes. */
data class SessionNodeData(val session: BackendClient.SessionResponse) {
    override fun toString(): String {
        val status = session.status ?: "unknown"
        val task = session.task?.take(60) ?: session.session_id.take(8)
        val statusIcon = when (status) {
            "active" -> "\u25B6"      // ▶
            "completed" -> "\u2713"   // ✓
            "failed" -> "\u2717"      // ✗
            "merged" -> "\u21C4"      // ⇄
            "abandoned" -> "\u2014"   // —
            else -> "\u25CB"          // ○
        }
        return "$statusIcon $task"
    }
}

/** Custom cell renderer for session tree nodes. */
class SessionTreeCellRenderer : javax.swing.tree.DefaultTreeCellRenderer() {
    override fun getTreeCellRendererComponent(
        tree: JTree, value: Any?, selected: Boolean, expanded: Boolean,
        leaf: Boolean, row: Int, hasFocus: Boolean
    ): java.awt.Component {
        val component = super.getTreeCellRendererComponent(tree, value, selected, expanded, leaf, row, hasFocus)
        val node = value as? DefaultMutableTreeNode
        val data = node?.userObject as? SessionNodeData

        if (data != null) {
            toolTipText = "Session: ${data.session.session_id}\nStatus: ${data.session.status}\nBranch: ${data.session.plan_branch ?: "none"}"
        }

        return component
    }
}

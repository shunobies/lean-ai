/**
 * Lean AI VSCode Extension — entry point.
 *
 * Registers:
 * - Sidebar Webview chat panel (standalone, no Copilot dependency)
 * - Inline Completion Provider (Copilot-style predictions)
 * - Approval/rejection commands
 * - Auto-starts the Python backend server (configurable)
 */

import * as path from "path";
import * as vscode from "vscode";
import { LeanAISidebarProvider } from "./sidebarProvider";
import { LeanAIInlineProvider } from "./inlineProvider";
import { SessionTreeProvider } from "./sessionTreeProvider";
import { SessionDetailProvider } from "./sessionDetailProvider";
import { BackendClient } from "./backendClient";
import {
    startBackend,
    stopBackend,
    restartBackend,
    clearManagedInstallCache,
    consumePendingManagedInstallReboot,
} from "./backendProcess";
import { resetBackend } from "./backendInstaller";
import { SettingsPanel } from "./settingsPanel";
import { NotesPanel } from "./notesPanel";
import { MemoriesPanel } from "./memoriesPanel";
import { PromptsPanel } from "./promptsPanel";
import { ObservabilityPanel } from "./observabilityPanel";
import { initNotifications } from "./notifications";
import {
    installUiVerificationCommand,
    testUiVerificationCommand,
    registerUiVerificationWatcher,
} from "./uiVerification";

export async function activate(context: vscode.ExtensionContext): Promise<void> {
    console.log("Lean AI extension activating...");

    // Register Sidebar Webview Provider (Activity Bar chat panel)
    const sidebarProvider = new LeanAISidebarProvider(context.extensionUri, context);
    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider(
            LeanAISidebarProvider.viewType,
            sidebarProvider,
            { webviewOptions: { retainContextWhenHidden: true } },
        ),
    );

    // If this window was opened for a freshly scaffolded project, queue auto-init
    const pendingScaffoldDir = context.globalState.get<string>("lean-ai.pendingScaffoldInit");
    if (pendingScaffoldDir) {
        const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
        if (workspaceRoot &&
            path.normalize(workspaceRoot).toLowerCase() === path.normalize(pendingScaffoldDir).toLowerCase()) {
            await context.globalState.update("lean-ai.pendingScaffoldInit", undefined);
            sidebarProvider.setPendingInit();
            // Focus the chat view so resolveWebviewView fires and the init can run
            setTimeout(() => {
                vscode.commands.executeCommand("lean-ai.chatView.focus");
            }, 500);
        }
    }

    // Register Inline Completion Provider
    const inlineProvider = new LeanAIInlineProvider();
    context.subscriptions.push(
        vscode.languages.registerInlineCompletionItemProvider(
            { pattern: "**" },
            inlineProvider,
        ),
    );

    // Register Session Tree View Provider
    const sessionTreeProvider = new SessionTreeProvider();
    sidebarProvider.setSessionTreeProvider(sessionTreeProvider);
    const sessionsTreeView = vscode.window.createTreeView("lean-ai.sessionsView", {
        treeDataProvider: sessionTreeProvider,
    });
    context.subscriptions.push(sessionsTreeView);
    initNotifications(sessionsTreeView);

    // Session detail webview provider
    const sessionDetailProvider = new SessionDetailProvider();

    // Register approval/rejection commands (delegate to sidebar provider)
    context.subscriptions.push(
        vscode.commands.registerCommand("lean-ai.approve", () => {
            const sessionId = sidebarProvider.getSessionId();
            if (!sessionId) {
                vscode.window.showWarningMessage("No active Lean AI session.");
                return;
            }
            // Forward to the webview
            vscode.commands.executeCommand("lean-ai.chatView.focus");
        }),
        vscode.commands.registerCommand("lean-ai.reject", () => {
            const sessionId = sidebarProvider.getSessionId();
            if (!sessionId) {
                vscode.window.showWarningMessage("No active Lean AI session.");
                return;
            }
            vscode.commands.executeCommand("lean-ai.chatView.focus");
        }),
        vscode.commands.registerCommand("lean-ai.focus", () => {
            vscode.commands.executeCommand("lean-ai.chatView.focus");
        }),
        vscode.commands.registerCommand("lean-ai.restartBackend", async () => {
            const success = await restartBackend();
            if (!success) {
                vscode.window.showErrorMessage("Lean AI: Failed to restart backend.");
            }
        }),
        vscode.commands.registerCommand("lean-ai.stopBackend", () => {
            stopBackend();
            vscode.window.showInformationMessage("Lean AI backend stopped.");
        }),
        vscode.commands.registerCommand("lean-ai.reinstallBackend", async () => {
            stopBackend();
            await resetBackend(context);
            clearManagedInstallCache();
            const success = await startBackend(context.secrets, context);
            if (success) {
                vscode.window.showInformationMessage("Lean AI: Backend reinstalled successfully.");
            }
        }),
        vscode.commands.registerCommand("lean-ai.openSettings", () => {
            SettingsPanel.createOrShow(context);
        }),
        vscode.commands.registerCommand("lean-ai.editPrompts", () => {
            PromptsPanel.createOrShow(context);
        }),
        vscode.commands.registerCommand("lean-ai.openNotes", () => {
            NotesPanel.createOrShow(context);
        }),
        vscode.commands.registerCommand("lean-ai.openMemories", () => {
            MemoriesPanel.createOrShow(context);
        }),
        vscode.commands.registerCommand("lean-ai.openObservabilityDashboard", () => {
            ObservabilityPanel.createOrShow(context);
        }),
        vscode.commands.registerCommand("lean-ai.openChatInNewWindow", () => {
            sidebarProvider.openChatInNewWindow();
        }),
        vscode.commands.registerCommand(
            "lean-ai.installUiVerification",
            installUiVerificationCommand,
        ),
        vscode.commands.registerCommand(
            "lean-ai.testUiVerification",
            testUiVerificationCommand,
        ),
        registerUiVerificationWatcher(context),
    );

    // Session history commands
    context.subscriptions.push(
        vscode.commands.registerCommand("lean-ai.refreshSessions", () => {
            sessionTreeProvider.refresh();
        }),
        vscode.commands.registerCommand("lean-ai.viewSession", async (item: unknown) => {
            // item is a SessionItem from the tree view with a .session property
            const sessionItem = item as { session?: { session_id: string } };
            if (sessionItem?.session?.session_id) {
                await sessionDetailProvider.show(sessionItem.session.session_id);
            }
        }),
        vscode.commands.registerCommand("lean-ai.restoreCheckpoint", async (item: unknown) => {
            const checkpointItem = item as {
                checkpoint?: { id: string };
                sessionId?: string;
            };
            if (!checkpointItem?.checkpoint?.id || !checkpointItem.sessionId) { return; }
            const confirm = await vscode.window.showWarningMessage(
                "Restore the session to this checkpoint?",
                { modal: true },
                "Restore",
            );
            if (confirm !== "Restore") { return; }
            try {
                const client = BackendClient.getInstance();
                const repoRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || ".";
                await client.restoreCheckpoint(
                    checkpointItem.sessionId,
                    checkpointItem.checkpoint.id,
                    repoRoot,
                );
                vscode.window.showInformationMessage("Session restored to checkpoint.");
                sessionTreeProvider.refresh();
                await sessionDetailProvider.show(checkpointItem.sessionId);
            } catch (e) {
                const error = e instanceof Error ? e.message : String(e);
                vscode.window.showErrorMessage(`Restore failed: ${error}`);
            }
        }),
        vscode.commands.registerCommand("lean-ai.mergeSession", async (item: unknown) => {
            const sessionItem = item as { session?: { session_id: string; plan_branch?: string } };
            if (!sessionItem?.session?.session_id) { return; }
            const confirm = await vscode.window.showWarningMessage(
                `Merge plan branch for this session?`,
                { modal: true },
                "Merge",
            );
            if (confirm === "Merge") {
                try {
                    const client = BackendClient.getInstance();
                    const repoRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || "";
                    await client.mergeSession(sessionItem.session.session_id, repoRoot);
                    vscode.window.showInformationMessage("Session merged successfully.");
                    sessionTreeProvider.refresh();
                } catch (e) {
                    const error = e instanceof Error ? e.message : String(e);
                    vscode.window.showErrorMessage(`Merge failed: ${error}`);
                }
            }
        }),
        vscode.commands.registerCommand("lean-ai.abandonSession", async (item: unknown) => {
            const sessionItem = item as { session?: { session_id: string } };
            if (!sessionItem?.session?.session_id) { return; }
            const confirm = await vscode.window.showWarningMessage(
                "Abandon this session? The plan branch will be cleaned up.",
                { modal: true },
                "Abandon",
            );
            if (confirm === "Abandon") {
                try {
                    const client = BackendClient.getInstance();
                    const repoRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || "";
                    await client.abandonSession(sessionItem.session.session_id, repoRoot);
                    vscode.window.showInformationMessage("Session abandoned.");
                    sessionTreeProvider.refresh();
                } catch (e) {
                    const error = e instanceof Error ? e.message : String(e);
                    vscode.window.showErrorMessage(`Abandon failed: ${error}`);
                }
            }
        }),
        vscode.commands.registerCommand("lean-ai.deleteSession", async (item: unknown) => {
            const sessionItem = item as { session?: { session_id: string; title?: string | null } };
            if (!sessionItem?.session?.session_id) { return; }
            const label = sessionItem.session.title || sessionItem.session.session_id.slice(0, 8);
            const confirm = await vscode.window.showWarningMessage(
                `Permanently delete session "${label}"? This cannot be undone.`,
                { modal: true },
                "Delete",
            );
            if (confirm === "Delete") {
                try {
                    const client = BackendClient.getInstance();
                    const repoRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || "";
                    await client.deleteSession(sessionItem.session.session_id, repoRoot);
                    vscode.window.showInformationMessage("Session deleted.");
                    sessionTreeProvider.refresh();
                } catch (e) {
                    const error = e instanceof Error ? e.message : String(e);
                    vscode.window.showErrorMessage(`Delete failed: ${error}`);
                }
            }
        }),
    );

    // Start setup only after the UI and recovery commands are available.
    const backendStarted = await startBackend(context.secrets, context);
    if (backendStarted && consumePendingManagedInstallReboot()) {
        await restartBackend();
    }

    console.log("Lean AI extension activated.");
}

export function deactivate(): void {
    stopBackend();
    console.log("Lean AI extension deactivated.");
}

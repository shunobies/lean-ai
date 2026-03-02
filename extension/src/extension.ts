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
import { startBackend, stopBackend, restartBackend } from "./backendProcess";

export async function activate(context: vscode.ExtensionContext): Promise<void> {
    console.log("Lean AI extension activating...");

    // Start backend server (checks if already running first)
    await startBackend();

    // Register Sidebar Webview Provider (Activity Bar chat panel)
    const sidebarProvider = new LeanAISidebarProvider(context.extensionUri, context);
    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider(
            LeanAISidebarProvider.viewType,
            sidebarProvider,
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
    context.subscriptions.push(
        vscode.window.registerTreeDataProvider("lean-ai.sessionsView", sessionTreeProvider),
    );

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
                    await client.mergeSession(sessionItem.session.session_id);
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
                    await client.abandonSession(sessionItem.session.session_id);
                    vscode.window.showInformationMessage("Session abandoned.");
                    sessionTreeProvider.refresh();
                } catch (e) {
                    const error = e instanceof Error ? e.message : String(e);
                    vscode.window.showErrorMessage(`Abandon failed: ${error}`);
                }
            }
        }),
    );

    console.log("Lean AI extension activated.");
}

export function deactivate(): void {
    stopBackend();
    console.log("Lean AI extension deactivated.");
}

/**
 * Memories panel — singleton WebviewPanel for reviewing, confirming, and
 * archiving cross-session memories extracted by the planner.  Backs the
 * user-curation loop: auto-extracted memories appear in "Pending Review"
 * until the user confirms them (promoting to user_confirmed) or rejects
 * them (excluding from future retrieval).
 */

import * as vscode from "vscode";
import { BackendClient } from "./backendClient";
import { getMemoriesPanelHtml } from "./memoriesPanelHtml";

export class MemoriesPanel {
    static currentPanel: MemoriesPanel | undefined;
    private static readonly viewType = "lean-ai.memoriesPanel";

    private readonly _panel: vscode.WebviewPanel;
    private _disposables: vscode.Disposable[] = [];

    static createOrShow(_context: vscode.ExtensionContext): void {
        const column = vscode.window.activeTextEditor
            ? vscode.window.activeTextEditor.viewColumn
            : undefined;

        if (MemoriesPanel.currentPanel) {
            MemoriesPanel.currentPanel._panel.reveal(column);
            return;
        }

        const panel = vscode.window.createWebviewPanel(
            MemoriesPanel.viewType,
            "Memories",
            column || vscode.ViewColumn.One,
            {
                enableScripts: true,
                retainContextWhenHidden: true,
                localResourceRoots: [],
            },
        );

        MemoriesPanel.currentPanel = new MemoriesPanel(panel);
    }

    /** Public: refresh after an out-of-band event (e.g. WS memory_suggested). */
    static refreshIfOpen(): void {
        if (MemoriesPanel.currentPanel) {
            void MemoriesPanel.currentPanel._loadAll();
        }
    }

    private constructor(panel: vscode.WebviewPanel) {
        this._panel = panel;
        this._panel.webview.html = getMemoriesPanelHtml();

        this._panel.webview.onDidReceiveMessage(
            async (message: Record<string, unknown>) => {
                await this._handleMessage(message);
            },
            null,
            this._disposables,
        );

        this._panel.onDidDispose(() => this._dispose(), null, this._disposables);

        setTimeout(() => {
            void this._loadAll();
        }, 200);
    }

    private _getRepoRoot(): string {
        const folders = vscode.workspace.workspaceFolders;
        return folders?.[0]?.uri.fsPath || "";
    }

    private async _handleMessage(msg: Record<string, unknown>): Promise<void> {
        const client = BackendClient.getInstance();
        const repoRoot = this._getRepoRoot();
        if (!repoRoot) {
            void vscode.window.showWarningMessage(
                "Lean AI: open a workspace folder to manage memories.",
            );
            return;
        }

        switch (msg.type) {
            case "webviewReady":
            case "refresh":
                await this._loadAll();
                break;

            case "confirm": {
                const memoryId = msg.memoryId as string;
                try {
                    await client.confirmMemory(memoryId, repoRoot);
                    await this._loadAll();
                } catch (err) {
                    this._showError("confirm memory", err);
                }
                break;
            }

            case "reject": {
                const memoryId = msg.memoryId as string;
                try {
                    await client.rejectMemory(memoryId, repoRoot);
                    await this._loadAll();
                } catch (err) {
                    this._showError("reject memory", err);
                }
                break;
            }

            case "delete": {
                const memoryId = msg.memoryId as string;
                const confirmed = await vscode.window.showWarningMessage(
                    "Permanently delete this memory? This cannot be undone.",
                    { modal: true },
                    "Delete",
                );
                if (confirmed !== "Delete") {
                    break;
                }
                try {
                    await client.deleteMemory(memoryId, repoRoot);
                    await this._loadAll();
                } catch (err) {
                    this._showError("delete memory", err);
                }
                break;
            }

            case "createManual": {
                const content = (msg.content as string || "").trim();
                const category = (msg.category as string) || "convention";
                const tagsRaw = (msg.tags as string) || "";
                const tags = tagsRaw
                    .split(",")
                    .map((t) => t.trim())
                    .filter(Boolean);
                if (!content) {
                    break;
                }
                try {
                    await client.createMemory(
                        {
                            repo_root: repoRoot,
                            category,
                            content,
                            tags: tags.length ? tags : undefined,
                        },
                    );
                    await this._loadAll();
                } catch (err) {
                    this._showError("save memory", err);
                }
                break;
            }
        }
    }

    private async _loadAll(): Promise<void> {
        try {
            const client = BackendClient.getInstance();
            const repoRoot = this._getRepoRoot();
            if (!repoRoot) {
                return;
            }
            const [pending, confirmed, archived] = await Promise.all([
                client.listMemories({
                    repo_root: repoRoot,
                    curation_status: "auto",
                    limit: 200,
                    include_expired: true,
                }),
                client.listMemories({
                    repo_root: repoRoot,
                    curation_status: "user_confirmed,high_confidence_auto",
                    limit: 200,
                    include_expired: true,
                }),
                client.listMemories({
                    repo_root: repoRoot,
                    curation_status: "user_rejected,superseded",
                    limit: 200,
                    include_expired: true,
                }),
            ]);
            await this._panel.webview.postMessage({
                type: "loadMemories",
                pending,
                confirmed,
                archived,
            });
        } catch (err) {
            this._showError("load memories", err);
        }
    }

    private _showError(action: string, err: unknown): void {
        const errMsg = err instanceof Error ? err.message : String(err);
        void vscode.window.showErrorMessage(
            `Lean AI: failed to ${action} — ${errMsg}`,
        );
    }

    private _dispose(): void {
        MemoriesPanel.currentPanel = undefined;
        this._panel.dispose();
        while (this._disposables.length) {
            const d = this._disposables.pop();
            if (d) { d.dispose(); }
        }
    }
}

/**
 * Prompts editor panel — a singleton WebviewPanel for viewing and editing
 * LLM prompts.  Communicates with the backend /api/prompts endpoints.
 */

import * as vscode from "vscode";
import { BackendClient } from "./backendClient";
import { getPromptsPanelHtml } from "./promptsPanelHtml";

export class PromptsPanel {
    static currentPanel: PromptsPanel | undefined;
    private static readonly viewType = "lean-ai.promptsPanel";

    private readonly _panel: vscode.WebviewPanel;
    private _disposables: vscode.Disposable[] = [];

    static createOrShow(context: vscode.ExtensionContext): void {
        const column = vscode.window.activeTextEditor
            ? vscode.window.activeTextEditor.viewColumn
            : undefined;

        if (PromptsPanel.currentPanel) {
            PromptsPanel.currentPanel._panel.reveal(column);
            return;
        }

        const panel = vscode.window.createWebviewPanel(
            PromptsPanel.viewType,
            "Edit Prompts",
            column || vscode.ViewColumn.One,
            {
                enableScripts: true,
                retainContextWhenHidden: true,
                localResourceRoots: [],
            },
        );

        PromptsPanel.currentPanel = new PromptsPanel(panel);
    }

    private constructor(panel: vscode.WebviewPanel) {
        this._panel = panel;
        this._panel.webview.html = getPromptsPanelHtml();

        this._panel.webview.onDidReceiveMessage(
            async (message: Record<string, unknown>) => {
                await this._handleMessage(message);
            },
            null,
            this._disposables,
        );

        this._panel.onDidDispose(() => this._dispose(), null, this._disposables);

        // Load prompts after a short delay
        setTimeout(() => {
            void this._loadPrompts();
        }, 200);
    }

    private async _handleMessage(msg: Record<string, unknown>): Promise<void> {
        switch (msg.type) {
            case "webviewReady":
                await this._loadPrompts();
                break;

            case "savePrompts": {
                const overrides = msg.overrides as Record<string, string>;
                await this._savePrompts(overrides);
                break;
            }

            case "resetPrompt": {
                const keys = msg.keys as string[];
                await this._resetPrompts(keys);
                break;
            }

            case "resetAll":
                await this._resetPrompts(undefined);
                break;
        }
    }

    private _getRepoRoot(): string {
        const folders = vscode.workspace.workspaceFolders;
        return folders?.[0]?.uri.fsPath || "";
    }

    private async _loadPrompts(): Promise<void> {
        try {
            const client = BackendClient.getInstance();
            const data = await client.getPrompts(this._getRepoRoot());
            await this._panel.webview.postMessage({
                type: "loadPrompts",
                ...data,
            });
        } catch (err) {
            const errMsg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(
                `Lean AI: failed to load prompts — ${errMsg}`,
            );
        }
    }

    private async _savePrompts(overrides: Record<string, string>): Promise<void> {
        try {
            const client = BackendClient.getInstance();
            await client.updatePrompts(this._getRepoRoot(), overrides);
            void vscode.window.showInformationMessage("Lean AI: Prompts saved.");
            await this._loadPrompts();
        } catch (err) {
            const errMsg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(
                `Lean AI: failed to save prompts — ${errMsg}`,
            );
        }
    }

    private async _resetPrompts(keys: string[] | undefined): Promise<void> {
        try {
            const client = BackendClient.getInstance();
            await client.resetPrompts(this._getRepoRoot(), keys);
            void vscode.window.showInformationMessage("Lean AI: Prompts reset to defaults.");
            await this._loadPrompts();
        } catch (err) {
            const errMsg = err instanceof Error ? err.message : String(err);
            void vscode.window.showErrorMessage(
                `Lean AI: failed to reset prompts — ${errMsg}`,
            );
        }
    }

    private _dispose(): void {
        PromptsPanel.currentPanel = undefined;
        this._panel.dispose();
        while (this._disposables.length) {
            const d = this._disposables.pop();
            if (d) { d.dispose(); }
        }
    }
}

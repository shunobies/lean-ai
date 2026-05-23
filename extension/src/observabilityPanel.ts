/**
 * Observability panel — singleton WebviewPanel for monitoring sessions,
 * trace execution, reviewing feedback, and viewing metrics.  Follows the
 * same singleton / message-handler pattern as {@link MemoriesPanel}.
 */

import * as vscode from "vscode";
import { BackendClient } from "./backendClient";
import { getObservabilityPanelHtml } from "./observabilityPanelHtml";

export class ObservabilityPanel {
    static currentPanel: ObservabilityPanel | undefined;
    private static readonly viewType = "lean-ai.observabilityPanel";

    private readonly _panel: vscode.WebviewPanel;
    private _disposables: vscode.Disposable[] = [];

    static createOrShow(_context: vscode.ExtensionContext): void {
        const column = vscode.window.activeTextEditor
            ? vscode.window.activeTextEditor.viewColumn
            : undefined;

        if (ObservabilityPanel.currentPanel) {
            ObservabilityPanel.currentPanel._panel.reveal(column);
            return;
        }

        const panel = vscode.window.createWebviewPanel(
            ObservabilityPanel.viewType,
            "Observability",
            column || vscode.ViewColumn.One,
            {
                enableScripts: true,
                retainContextWhenHidden: true,
                localResourceRoots: [],
            },
        );

        ObservabilityPanel.currentPanel = new ObservabilityPanel(panel);
    }

    /** Public: refresh after an out-of-band event. */
    static refreshIfOpen(): void {
        if (ObservabilityPanel.currentPanel) {
            void ObservabilityPanel.currentPanel._loadAll();
        }
    }

    private constructor(panel: vscode.WebviewPanel) {
        this._panel = panel;
        this._panel.webview.html = getObservabilityPanelHtml();

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
                "Lean AI: open a workspace folder to view observability data.",
            );
            return;
        }

        switch (msg.type) {
            case "webviewReady":
            case "refresh":
                await this._loadAll();
                break;

            case "loadSessions": {
                try {
                    const sessions = await client.getObservabilitySessions(repoRoot);
                    await this._panel.webview.postMessage({
                        type: "sessionsData",
                        sessions,
                    });
                } catch (err) {
                    this._showError("load sessions", err);
                }
                break;
            }

            case "loadTraceTree": {
                const sessionId = msg.sessionId as string;
                if (!sessionId) break;
                try {
                    const tree = await client.getTraceTree(repoRoot, sessionId);
                    await this._panel.webview.postMessage({
                        type: "traceTreeData",
                        tree,
                    });
                } catch (err) {
                    this._showError("load trace tree", err);
                }
                break;
            }

            case "submitFeedback": {
                const feedback = msg.feedback as {
                    session_id: string;
                    thumbs_up?: boolean;
                    rating?: number;
                    comment?: string;
                    tags?: string[];
                    trace_span_uuid?: string;
                };
                if (!feedback?.session_id) break;
                try {
                    await client.submitFeedback(repoRoot, feedback);
                    await this._loadAll();
                } catch (err) {
                    this._showError("submit feedback", err);
                }
                break;
            }

            case "loadMetrics": {
                try {
                    const metrics = await client.getMetricsSummary(repoRoot);
                    await this._panel.webview.postMessage({
                        type: "metricsData",
                        metrics,
                    });
                } catch (err) {
                    this._showError("load metrics", err);
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
            const [sessions, metrics] = await Promise.all([
                client.getObservabilitySessions(repoRoot),
                client.getMetricsSummary(repoRoot),
            ]);
            await this._panel.webview.postMessage({
                type: "loadAllData",
                sessions,
                metrics,
            });
        } catch (err) {
            this._showError("load observability data", err);
        }
    }

    private _showError(action: string, err: unknown): void {
        const errMsg = err instanceof Error ? err.message : String(err);
        void vscode.window.showErrorMessage(
            `Lean AI: failed to ${action} — ${errMsg}`,
        );
    }

    private _dispose(): void {
        ObservabilityPanel.currentPanel = undefined;
        this._panel.dispose();
        while (this._disposables.length) {
            const d = this._disposables.pop();
            if (d) { d.dispose(); }
        }
    }
}

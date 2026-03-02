/**
 * Session Detail Webview — shows detailed session info, checkpoints,
 * git events, and provides merge/resume/abandon actions.
 *
 * Opened via command `lean-ai.viewSession` when a session is selected
 * in the SessionTreeProvider.
 */

import * as vscode from "vscode";
import { BackendClient } from "./backendClient";
import type { SessionSummary, CheckpointSummary, GitEventSummary } from "./types";

export class SessionDetailProvider {
    private static panels = new Map<string, vscode.WebviewPanel>();
    private client: BackendClient;

    constructor() {
        this.client = BackendClient.getInstance();
    }

    async show(sessionId: string): Promise<void> {
        // Reuse existing panel for this session
        const existing = SessionDetailProvider.panels.get(sessionId);
        if (existing) {
            existing.reveal(vscode.ViewColumn.One);
            await this.updatePanel(existing, sessionId);
            return;
        }

        const panel = vscode.window.createWebviewPanel(
            "lean-ai.sessionDetail",
            "Session Detail",
            vscode.ViewColumn.One,
            {
                enableScripts: true,
                retainContextWhenHidden: true,
            },
        );

        SessionDetailProvider.panels.set(sessionId, panel);
        panel.onDidDispose(() => {
            SessionDetailProvider.panels.delete(sessionId);
        });

        // Handle messages from the webview
        panel.webview.onDidReceiveMessage(async (msg) => {
            try {
                switch (msg.command) {
                    case "merge":
                        await this.client.mergeSession(sessionId);
                        vscode.window.showInformationMessage("Session merged successfully.");
                        await this.updatePanel(panel, sessionId);
                        break;
                    case "abandon":
                        await this.client.abandonSession(sessionId);
                        vscode.window.showInformationMessage("Session abandoned.");
                        await this.updatePanel(panel, sessionId);
                        break;
                    case "resume": {
                        const checkpointId = msg.checkpointId as string;
                        await this.client.resumeSession(sessionId, checkpointId);
                        vscode.window.showInformationMessage("Session resumed from checkpoint.");
                        await this.updatePanel(panel, sessionId);
                        break;
                    }
                    case "rename": {
                        const title = await vscode.window.showInputBox({
                            prompt: "Enter new session title",
                            value: msg.currentTitle || "",
                        });
                        if (title !== undefined) {
                            await this.client.updateSessionTitle(sessionId, title);
                            await this.updatePanel(panel, sessionId);
                        }
                        break;
                    }
                    case "refresh":
                        await this.updatePanel(panel, sessionId);
                        break;
                }
            } catch (e) {
                const error = e instanceof Error ? e.message : String(e);
                vscode.window.showErrorMessage(`Action failed: ${error}`);
            }
        });

        await this.updatePanel(panel, sessionId);
    }

    private async updatePanel(panel: vscode.WebviewPanel, sessionId: string): Promise<void> {
        try {
            const [session, checkpoints, gitEvents] = await Promise.all([
                this.client.getSession(sessionId),
                this.client.listCheckpoints(sessionId).catch(() => [] as CheckpointSummary[]),
                this.client.listGitEvents(sessionId).catch(() => [] as GitEventSummary[]),
            ]);

            const sessionSummary: SessionSummary = {
                session_id: session.session_id,
                title: session.title,
                session_status: session.session_status,
                workflow_stage: session.stage,
                task_track: session.task_track,
                base_branch: session.base_branch,
                plan_branch: session.plan_branch,
                merge_commit_sha: session.merge_commit_sha,
                created_at: session.created_at,
                updated_at: session.updated_at,
            };

            panel.title = session.title || `Session ${sessionId.slice(0, 8)}`;
            panel.webview.html = this.getHtml(sessionSummary, checkpoints, gitEvents);
        } catch (e) {
            const error = e instanceof Error ? e.message : String(e);
            panel.webview.html = this.getErrorHtml(error);
        }
    }

    private getHtml(
        session: SessionSummary,
        checkpoints: CheckpointSummary[],
        gitEvents: GitEventSummary[],
    ): string {
        const title = escapeHtml(session.title || `Session ${session.session_id.slice(0, 8)}`);
        const canMerge = session.session_status === "active" && session.plan_branch && !session.merge_commit_sha;
        const canAbandon = session.session_status === "active";
        const canResume = checkpoints.some((cp) => cp.status === "completed");

        return /*html*/ `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
        font-family: var(--vscode-font-family);
        font-size: var(--vscode-font-size);
        color: var(--vscode-foreground);
        background: var(--vscode-editor-background);
        padding: 16px;
        line-height: 1.6;
    }
    h1 { font-size: 18px; margin-bottom: 4px; }
    h2 {
        font-size: 14px;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        opacity: 0.7;
        margin: 20px 0 8px;
        border-bottom: 1px solid var(--vscode-panel-border);
        padding-bottom: 4px;
    }
    .meta { font-size: 12px; opacity: 0.7; margin-bottom: 12px; }
    .meta span { margin-right: 16px; }
    .badge {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 10px;
        font-size: 11px;
        font-weight: 600;
    }
    .badge-active { background: #28a745; color: #fff; }
    .badge-completed { background: #17a2b8; color: #fff; }
    .badge-merged { background: #6f42c1; color: #fff; }
    .badge-abandoned { background: #dc3545; color: #fff; }
    .actions {
        display: flex;
        gap: 8px;
        margin: 12px 0;
    }
    button {
        padding: 6px 14px;
        border: 1px solid var(--vscode-button-border, transparent);
        border-radius: 4px;
        cursor: pointer;
        font-family: inherit;
        font-size: 12px;
    }
    .btn-primary {
        background: var(--vscode-button-background);
        color: var(--vscode-button-foreground);
    }
    .btn-primary:hover { background: var(--vscode-button-hoverBackground); }
    .btn-danger {
        background: var(--vscode-inputValidation-errorBackground);
        color: var(--vscode-errorForeground);
        border-color: var(--vscode-inputValidation-errorBorder);
    }
    .btn-secondary {
        background: var(--vscode-button-secondaryBackground);
        color: var(--vscode-button-secondaryForeground);
    }
    table {
        width: 100%;
        border-collapse: collapse;
        font-size: 12px;
        margin-bottom: 16px;
    }
    th, td {
        text-align: left;
        padding: 4px 8px;
        border-bottom: 1px solid var(--vscode-panel-border);
    }
    th {
        opacity: 0.7;
        font-weight: 600;
        text-transform: uppercase;
        font-size: 10px;
        letter-spacing: 0.5px;
    }
    .info-grid {
        display: grid;
        grid-template-columns: 140px 1fr;
        gap: 4px 12px;
        font-size: 12px;
        margin-bottom: 12px;
    }
    .info-label { opacity: 0.6; font-weight: 600; }
    .info-value { word-break: break-all; }
    code {
        background: var(--vscode-textCodeBlock-background);
        padding: 1px 4px;
        border-radius: 3px;
        font-family: var(--vscode-editor-font-family);
        font-size: var(--vscode-editor-font-size);
    }
    .empty { opacity: 0.5; font-style: italic; font-size: 12px; }
</style>
</head>
<body>
    <h1>${title} <button class="btn-secondary" onclick="rename()" title="Rename session">&#9998;</button></h1>
    <div class="meta">
        <span class="badge badge-${escapeHtml(session.session_status)}">${escapeHtml(session.session_status)}</span>
        <span>Stage: <code>${escapeHtml(session.workflow_stage)}</code></span>
        ${session.task_track ? `<span>Track: <code>${escapeHtml(session.task_track)}</code></span>` : ""}
    </div>

    <div class="info-grid">
        <span class="info-label">Session ID</span>
        <span class="info-value"><code>${escapeHtml(session.session_id)}</code></span>
        ${session.base_branch ? `
            <span class="info-label">Base Branch</span>
            <span class="info-value"><code>${escapeHtml(session.base_branch)}</code></span>
        ` : ""}
        ${session.plan_branch ? `
            <span class="info-label">Plan Branch</span>
            <span class="info-value"><code>${escapeHtml(session.plan_branch)}</code></span>
        ` : ""}
        ${session.merge_commit_sha ? `
            <span class="info-label">Merge Commit</span>
            <span class="info-value"><code>${escapeHtml(session.merge_commit_sha)}</code></span>
        ` : ""}
        <span class="info-label">Created</span>
        <span class="info-value">${escapeHtml(session.created_at)}</span>
        <span class="info-label">Updated</span>
        <span class="info-value">${escapeHtml(session.updated_at)}</span>
    </div>

    <div class="actions">
        ${canMerge ? '<button class="btn-primary" onclick="merge()">Merge Plan Branch</button>' : ""}
        ${canResume ? '<button class="btn-secondary" onclick="resumeLatest()">Resume from Checkpoint</button>' : ""}
        ${canAbandon ? '<button class="btn-danger" onclick="abandon()">Abandon Session</button>' : ""}
        <button class="btn-secondary" onclick="refresh()">Refresh</button>
    </div>

    <h2>Checkpoints (${checkpoints.length})</h2>
    ${checkpoints.length > 0 ? `
    <table>
        <thead><tr><th>#</th><th>Description</th><th>Status</th><th>Commit</th><th>Time</th></tr></thead>
        <tbody>
            ${checkpoints.map((cp) => `
                <tr>
                    <td>${cp.step_index + 1}</td>
                    <td>${escapeHtml(cp.step_description || "—")}</td>
                    <td>${escapeHtml(cp.status)}</td>
                    <td>${cp.head_commit_sha ? `<code>${escapeHtml(cp.head_commit_sha.slice(0, 7))}</code>` : "—"}</td>
                    <td>${escapeHtml(cp.created_at)}</td>
                </tr>
            `).join("")}
        </tbody>
    </table>
    ` : '<p class="empty">No checkpoints recorded.</p>'}

    <h2>Git Events (${gitEvents.length})</h2>
    ${gitEvents.length > 0 ? `
    <table>
        <thead><tr><th>Type</th><th>Ref</th><th>SHA</th><th>Message</th><th>Time</th></tr></thead>
        <tbody>
            ${gitEvents.map((ev) => `
                <tr>
                    <td>${escapeHtml(ev.event_type)}</td>
                    <td>${ev.ref_name ? `<code>${escapeHtml(ev.ref_name)}</code>` : "—"}</td>
                    <td>${ev.commit_sha ? `<code>${escapeHtml(ev.commit_sha.slice(0, 7))}</code>` : "—"}</td>
                    <td>${ev.message ? escapeHtml(ev.message.split("\\n")[0].slice(0, 60)) : "—"}</td>
                    <td>${escapeHtml(ev.created_at)}</td>
                </tr>
            `).join("")}
        </tbody>
    </table>
    ` : '<p class="empty">No git events recorded.</p>'}

<script>
    const vscode = acquireVsCodeApi();
    const sessionTitle = ${JSON.stringify(session.title || "")};
    const lastCompletedCheckpointId = ${JSON.stringify(
            checkpoints.filter((cp) => cp.status === "completed").pop()?.id || null,
        )};

    function merge() {
        if (confirm("Merge the plan branch back to the base branch?")) {
            vscode.postMessage({ command: "merge" });
        }
    }
    function abandon() {
        if (confirm("Abandon this session? The plan branch will be cleaned up.")) {
            vscode.postMessage({ command: "abandon" });
        }
    }
    function resumeLatest() {
        if (lastCompletedCheckpointId) {
            vscode.postMessage({ command: "resume", checkpointId: lastCompletedCheckpointId });
        }
    }
    function rename() {
        vscode.postMessage({ command: "rename", currentTitle: sessionTitle });
    }
    function refresh() {
        vscode.postMessage({ command: "refresh" });
    }
</script>
</body>
</html>`;
    }

    private getErrorHtml(error: string): string {
        return /*html*/ `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="font-family: var(--vscode-font-family); color: var(--vscode-errorForeground); padding: 20px;">
    <h2>Failed to load session</h2>
    <p>${escapeHtml(error)}</p>
    <button onclick="acquireVsCodeApi().postMessage({ command: 'refresh' })">Retry</button>
</body>
</html>`;
    }
}

function escapeHtml(str: string): string {
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

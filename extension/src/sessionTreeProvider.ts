/**
 * Session History Tree View — TreeDataProvider that lists sessions,
 * their checkpoints, and git events in the Lean AI activity bar.
 *
 * Tree structure:
 *   Session "Fix login bug" (active, main)
 *     ├─ Checkpoints
 *     │   ├─ Step 1: Parse request ✓
 *     │   └─ Step 2: Implement handler ✓
 *     └─ Git Events
 *         ├─ branch_create: agent/9f3c/a4d7-fix-login
 *         ├─ commit: abc1234 "Implement handler"
 *         └─ merge: def5678
 *
 * Refreshes automatically or on demand via command.
 */

import * as vscode from "vscode";
import { BackendClient } from "./backendClient";
import type { SessionSummary, CheckpointSummary, GitEventSummary } from "./types";

// ── Tree item types ─────────────────────────────────────────────

type TreeElement =
    | SessionItem
    | GroupItem
    | CheckpointItem
    | GitEventItem;

class SessionItem extends vscode.TreeItem {
    constructor(public readonly session: SessionSummary) {
        const label = session.title || `Session ${session.session_id.slice(0, 8)}`;
        super(label, vscode.TreeItemCollapsibleState.Collapsed);

        this.contextValue = "session";
        this.description = formatSessionDescription(session);
        this.tooltip = formatSessionTooltip(session);
        this.iconPath = getSessionIcon(session.session_status);

        // Clicking the row loads the conversation in the chat sidebar
        this.command = {
            command: "lean-ai.viewSession",
            title: "View Session",
            arguments: [this],
        };
    }
}

class GroupItem extends vscode.TreeItem {
    constructor(
        label: string,
        public readonly groupType: "checkpoints" | "git-events",
        public readonly sessionId: string,
    ) {
        super(label, vscode.TreeItemCollapsibleState.Collapsed);
        this.contextValue = `group-${groupType}`;
        this.iconPath = groupType === "checkpoints"
            ? new vscode.ThemeIcon("list-ordered")
            : new vscode.ThemeIcon("git-commit");
    }
}

class CheckpointItem extends vscode.TreeItem {
    constructor(public readonly checkpoint: CheckpointSummary, public readonly sessionId: string) {
        const statusIcon = checkpoint.status === "completed" ? "$(pass)" : checkpoint.status === "failed" ? "$(error)" : "$(watch)";
        const label = `Step ${checkpoint.step_index + 1}: ${checkpoint.step_description || "unnamed"}`;
        super(label, vscode.TreeItemCollapsibleState.None);

        this.contextValue = "checkpoint";
        this.description = statusIcon;
        this.tooltip = `Status: ${checkpoint.status}\nCreated: ${checkpoint.created_at}${checkpoint.head_commit_sha ? `\nCommit: ${checkpoint.head_commit_sha.slice(0, 7)}` : ""}`;
        this.iconPath = checkpoint.status === "completed"
            ? new vscode.ThemeIcon("pass", new vscode.ThemeColor("testing.iconPassed"))
            : checkpoint.status === "failed"
                ? new vscode.ThemeIcon("error", new vscode.ThemeColor("testing.iconFailed"))
                : new vscode.ThemeIcon("watch");
    }
}

class GitEventItem extends vscode.TreeItem {
    constructor(public readonly event: GitEventSummary) {
        const label = formatGitEventLabel(event);
        super(label, vscode.TreeItemCollapsibleState.None);

        this.contextValue = "git-event";
        this.description = event.commit_sha ? event.commit_sha.slice(0, 7) : undefined;
        this.tooltip = formatGitEventTooltip(event);
        this.iconPath = getGitEventIcon(event.event_type);
    }
}

// ── TreeDataProvider ────────────────────────────────────────────

export class SessionTreeProvider implements vscode.TreeDataProvider<TreeElement> {
    private _onDidChangeTreeData = new vscode.EventEmitter<TreeElement | undefined | void>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    private client: BackendClient;
    private repoRoot: string;
    private refreshTimer: ReturnType<typeof setInterval> | undefined;
    private cachedSessions: SessionSummary[] = [];

    constructor() {
        this.client = BackendClient.getInstance();
        this.repoRoot = this.getRepoRoot();

        // Auto-refresh every 30 seconds while the view is visible
        this.refreshTimer = setInterval(() => {
            this._onDidChangeTreeData.fire();
        }, 30_000);
    }

    dispose(): void {
        if (this.refreshTimer) {
            clearInterval(this.refreshTimer);
            this.refreshTimer = undefined;
        }
        this._onDidChangeTreeData.dispose();
    }

    refresh(): void {
        this._onDidChangeTreeData.fire();
    }

    getTreeItem(element: TreeElement): vscode.TreeItem {
        return element;
    }

    async getChildren(element?: TreeElement): Promise<TreeElement[]> {
        if (!element) {
            return this.getSessions();
        }

        if (element instanceof SessionItem) {
            return this.getSessionGroups(element.session.session_id);
        }

        if (element instanceof GroupItem) {
            if (element.groupType === "checkpoints") {
                return this.getCheckpoints(element.sessionId);
            }
            if (element.groupType === "git-events") {
                return this.getGitEvents(element.sessionId);
            }
        }

        return [];
    }

    private async getSessions(): Promise<TreeElement[]> {
        try {
            const healthy = await this.client.healthCheck();
            if (!healthy) {
                if (this.cachedSessions.length > 0) {
                    return [
                        createMessageItem("Backend offline — showing cached sessions"),
                        ...this.cachedSessions.map((s) => new SessionItem(s)),
                    ];
                }
                return [createMessageItem("Backend not available")];
            }

            const sessions = await this.client.listSessions(
                { repo_root: this.repoRoot },
                50,
            );

            if (sessions.length > 0) {
                this.cachedSessions = sessions;
            }

            if (sessions.length === 0) {
                return [createMessageItem("No sessions yet")];
            }

            return sessions.map((s) => new SessionItem(s));
        } catch (e) {
            const msg = e instanceof Error ? e.message : String(e);
            if (this.cachedSessions.length > 0) {
                return [
                    createMessageItem(`Backend error — showing cached sessions`),
                    ...this.cachedSessions.map((s) => new SessionItem(s)),
                ];
            }
            return [createMessageItem(`Error: ${msg}`)];
        }
    }

    private getSessionGroups(sessionId: string): TreeElement[] {
        return [
            new GroupItem("Checkpoints", "checkpoints", sessionId),
            new GroupItem("Git Events", "git-events", sessionId),
        ];
    }

    private async getCheckpoints(sessionId: string): Promise<TreeElement[]> {
        try {
            const checkpoints = await this.client.listCheckpoints(sessionId);
            if (checkpoints.length === 0) {
                return [createMessageItem("No checkpoints")];
            }
            return checkpoints.map((cp) => new CheckpointItem(cp, sessionId));
        } catch {
            return [createMessageItem("Failed to load checkpoints")];
        }
    }

    private async getGitEvents(sessionId: string): Promise<TreeElement[]> {
        try {
            const events = await this.client.listGitEvents(sessionId);
            if (events.length === 0) {
                return [createMessageItem("No git events")];
            }
            return events.map((ev) => new GitEventItem(ev));
        } catch {
            return [createMessageItem("Failed to load git events")];
        }
    }

    private getRepoRoot(): string {
        const folders = vscode.workspace.workspaceFolders;
        if (folders && folders.length > 0) {
            return folders[0].uri.fsPath;
        }
        return ".";
    }
}

// ── Helper functions ────────────────────────────────────────────

function createMessageItem(text: string): vscode.TreeItem {
    const item = new vscode.TreeItem(text, vscode.TreeItemCollapsibleState.None);
    item.contextValue = "message";
    return item;
}

function formatSessionDescription(session: SessionSummary): string {
    const parts: string[] = [];
    if (session.session_status !== "active") {
        parts.push(session.session_status);
    }
    if (session.plan_branch) {
        parts.push(session.plan_branch.split("/").pop() || session.plan_branch);
    }
    if (session.workflow_stage) {
        parts.push(session.workflow_stage.toLowerCase().replace(/_/g, " "));
    }
    return parts.join(" | ");
}

function formatSessionTooltip(session: SessionSummary): string {
    const lines: string[] = [
        `ID: ${session.session_id}`,
        `Status: ${session.session_status}`,
        `Stage: ${session.workflow_stage}`,
    ];
    if (session.task_track) { lines.push(`Track: ${session.task_track}`); }
    if (session.base_branch) { lines.push(`Base: ${session.base_branch}`); }
    if (session.plan_branch) { lines.push(`Branch: ${session.plan_branch}`); }
    if (session.merge_commit_sha) { lines.push(`Merge: ${session.merge_commit_sha.slice(0, 7)}`); }
    lines.push(`Created: ${session.created_at}`);
    lines.push(`Updated: ${session.updated_at}`);
    return lines.join("\n");
}

function getSessionIcon(status: string): vscode.ThemeIcon {
    switch (status) {
        case "active": return new vscode.ThemeIcon("play-circle", new vscode.ThemeColor("charts.green"));
        case "completed": return new vscode.ThemeIcon("question", new vscode.ThemeColor("charts.yellow"));
        case "failed": return new vscode.ThemeIcon("error", new vscode.ThemeColor("testing.iconFailed"));
        case "abandoned": return new vscode.ThemeIcon("circle-slash", new vscode.ThemeColor("charts.red"));
        case "merged": return new vscode.ThemeIcon("git-merge", new vscode.ThemeColor("charts.blue"));
        default: return new vscode.ThemeIcon("circle-outline");
    }
}

function formatGitEventLabel(event: GitEventSummary): string {
    switch (event.event_type) {
        case "branch_create": return `Branch: ${event.ref_name || "unnamed"}`;
        case "commit": return event.message ? `Commit: ${event.message.split("\n")[0].slice(0, 60)}` : "Commit";
        case "merge": return `Merge${event.ref_name ? ` ${event.ref_name}` : ""}`;
        case "branch_delete": return `Delete: ${event.ref_name || "unnamed"}`;
        default: return event.event_type;
    }
}

function formatGitEventTooltip(event: GitEventSummary): string {
    const lines: string[] = [`Type: ${event.event_type}`];
    if (event.ref_name) { lines.push(`Ref: ${event.ref_name}`); }
    if (event.commit_sha) { lines.push(`SHA: ${event.commit_sha}`); }
    if (event.parent_sha) { lines.push(`Parent: ${event.parent_sha.slice(0, 7)}`); }
    if (event.message) { lines.push(`Message: ${event.message}`); }
    lines.push(`Time: ${event.created_at}`);
    return lines.join("\n");
}

function getGitEventIcon(eventType: string): vscode.ThemeIcon {
    switch (eventType) {
        case "branch_create": return new vscode.ThemeIcon("git-branch");
        case "commit": return new vscode.ThemeIcon("git-commit");
        case "merge": return new vscode.ThemeIcon("git-merge");
        case "branch_delete": return new vscode.ThemeIcon("trash");
        default: return new vscode.ThemeIcon("circle-outline");
    }
}

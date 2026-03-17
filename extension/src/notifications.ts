/**
 * Notification helpers for Lean AI.
 *
 * Three layers:
 * 1. Activity bar badge — visible in VSCode without the panel being open.
 * 2. VSCode toast (showInformationMessage) — in-app pop-up with "Go to Lean AI" action.
 * 3. OS-level notification — best-effort shell command, platform-specific.
 *    Requires: notify-send (Linux), osascript (macOS), mshta (Windows).
 *    Silently ignored if the command is unavailable.
 */

import * as vscode from "vscode";
import { exec } from "child_process";

// Held so we can set / clear the badge on the sessions tree view.
let _treeView: vscode.TreeView<unknown> | undefined;

export function initNotifications(treeView: vscode.TreeView<unknown>): void {
    _treeView = treeView;
}

/** Show a numbered badge on the Lean AI activity bar icon. */
function setActivityBadge(value: number | undefined, tooltip?: string): void {
    if (!_treeView) { return; }
    if (value === undefined) {
        _treeView.badge = undefined;
    } else {
        _treeView.badge = { value, tooltip: tooltip ?? "Lean AI" };
    }
}

/** VSCode toast with a focus button. */
function showToast(message: string): void {
    vscode.window.showInformationMessage(message, "Go to Lean AI").then((choice) => {
        if (choice === "Go to Lean AI") {
            vscode.commands.executeCommand("lean-ai.chatView.focus");
        }
    });
}

/** Platform-specific OS notification. Best-effort — silently drops on failure. */
function sendOsNotification(title: string, body: string): void {
    if (!isOsNotificationsEnabled()) { return; }

    // Sanitize inputs to prevent shell injection
    const safeTitle = title.replace(/['"\\`$]/g, "");
    const safeBody = body.replace(/['"\\`$]/g, "");

    const platform = process.platform;
    try {
        if (platform === "linux") {
            exec(`notify-send "${safeTitle}" "${safeBody}"`);
        } else if (platform === "darwin") {
            exec(
                `osascript -e 'display notification "${safeBody}" with title "${safeTitle}"'`,
            );
        } else if (platform === "win32") {
            // mshta-based popup: non-blocking, auto-closes after 5 s
            exec(
                `mshta vbscript:Execute("CreateObject(""WScript.Shell"").Popup(""${safeBody}"",5,""${safeTitle}"",64)(window.close)")`,
            );
        }
    } catch {
        // Silently ignore — OS notification is best-effort
    }
}

function isOsNotificationsEnabled(): boolean {
    return vscode.workspace.getConfiguration("lean-ai").get<boolean>(
        "enableOsNotifications", true,
    );
}

// ── Public API ────────────────────────────────────────────────────────────────

/** Call when `approval_required` is received. */
export function notifyApprovalNeeded(): void {
    const msg = "Lean AI: Plan ready for review";
    setActivityBadge(1, "Lean AI: plan approval needed");
    showToast(msg);
    sendOsNotification("Lean AI", "Plan ready — click to review and approve");
}

/** Call when `tool_approval_required` is received. */
export function notifyToolApprovalNeeded(): void {
    const msg = "Lean AI: Command approval needed";
    setActivityBadge(1, "Lean AI: command approval needed");
    showToast(msg);
    sendOsNotification("Lean AI", "Approval required before running a command");
}

/** Call when `complete` is received. */
export function notifyComplete(summary: string): void {
    setActivityBadge(undefined);
    const shortSummary = summary.length > 80 ? summary.slice(0, 77) + "…" : summary;
    showToast(`Lean AI: Done — ${shortSummary}`);
    sendOsNotification("Lean AI: Done", shortSummary);
}

/** Call when a non-recoverable `error` is received. */
export function notifyError(message: string): void {
    setActivityBadge(undefined);
    const shortMsg = message.length > 80 ? message.slice(0, 77) + "…" : message;
    sendOsNotification("Lean AI: Error", shortMsg);
}

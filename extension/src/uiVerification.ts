/**
 * UI Verification commands — extension-side glue for the
 * /api/ui-verification/* endpoints on the backend.
 *
 * Exposes two commands:
 * - lean-ai.installUiVerification: install the workspace-local Chromium.
 * - lean-ai.testUiVerification:    one-shot capture of a URL to sanity-
 *                                  check the full pipeline.
 *
 * Also wires a configuration-change listener that prompts the user to
 * install Chromium the first time they toggle `lean-ai.enableUiVerification`
 * on, so they don't have to hunt for the command.
 */

import * as vscode from "vscode";
import * as http from "http";
import * as https from "https";
import { URL } from "url";
import { DEFAULT_BACKEND_URL } from "./constants";

interface UIVerificationStatus {
    enabled: boolean;
    platform: string;
    vision_model_configured: boolean;
    analysis_available: boolean;
    analysis_reason: string | null;
    playwright_installed: boolean;
    chromium_installed: boolean;
    chromium_path: string | null;
    desktop_backend: string;
    missing_system_deps: string[];
    macos_screen_recording_granted: boolean | null;
    wayland_compositor: string | null;
}

interface InstallResponse {
    success: boolean;
    output: string;
}

interface TestResponse {
    success: boolean;
    screenshot_path: string | null;
    report: string;
    error: string | null;
}

function getBackendUrl(): string {
    const config = vscode.workspace.getConfiguration("lean-ai");
    return config.get<string>("backendUrl") || DEFAULT_BACKEND_URL;
}

function getRepoRoot(): string | undefined {
    return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}

/**
 * POST helper with an arbitrarily long timeout.  Playwright install takes
 * 1-2 minutes; default Node fetch headersTimeout would cut it off.
 */
function postJsonLongRunning<T>(path: string, body: unknown): Promise<T> {
    return new Promise((resolve, reject) => {
        const fullUrl = new URL(`${getBackendUrl()}${path}`);
        const transport = fullUrl.protocol === "https:" ? https : http;
        const postData = JSON.stringify(body);

        const req = transport.request(
            {
                hostname: fullUrl.hostname,
                port: fullUrl.port,
                path: fullUrl.pathname + fullUrl.search,
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "Content-Length": Buffer.byteLength(postData),
                },
            },
            (res) => {
                let raw = "";
                res.setEncoding("utf8");
                res.on("data", (chunk) => (raw += chunk));
                res.on("end", () => {
                    try {
                        if (res.statusCode && res.statusCode >= 400) {
                            reject(new Error(`HTTP ${res.statusCode}: ${raw}`));
                            return;
                        }
                        resolve(JSON.parse(raw) as T);
                    } catch (e) {
                        reject(e);
                    }
                });
            },
        );

        req.on("socket", (socket) => {
            // Disable socket timeout so Playwright install never gets cut off.
            socket.setTimeout(0);
        });

        req.on("error", (e) => reject(e));
        req.write(postData);
        req.end();
    });
}

async function getStatus(repoRoot: string): Promise<UIVerificationStatus> {
    const url = `${getBackendUrl()}/api/ui-verification/status?repo_root=${encodeURIComponent(repoRoot)}`;
    const resp = await fetch(url);
    if (!resp.ok) {
        throw new Error(`Status endpoint returned ${resp.status}: ${await resp.text()}`);
    }
    return (await resp.json()) as UIVerificationStatus;
}

/**
 * Command: lean-ai.installUiVerification
 *
 * Download Chromium into <repo_root>/.lean_ai/browsers via the backend's
 * Playwright install endpoint.  Shows a progress notification (indeterminate)
 * while the ~300MB download runs.
 */
export async function installUiVerificationCommand(): Promise<void> {
    const repoRoot = getRepoRoot();
    if (!repoRoot) {
        vscode.window.showErrorMessage(
            "Lean AI: open a workspace folder before installing UI verification.",
        );
        return;
    }

    try {
        const status = await getStatus(repoRoot);
        if (!status.playwright_installed) {
            const action = await vscode.window.showErrorMessage(
                "Lean AI: Playwright is not installed in the backend environment. " +
                    'Install the backend extras (`pip install -e ".[ui-verification]"`) ' +
                    "and restart the backend before running this command.",
                "Restart Backend",
            );
            if (action === "Restart Backend") {
                await vscode.commands.executeCommand("lean-ai.restartBackend");
            }
            return;
        }
        if (status.chromium_installed) {
            const action = await vscode.window.showInformationMessage(
                `Lean AI: Chromium is already installed at ${status.chromium_path}. ` +
                    "Reinstall anyway?",
                "Reinstall",
                "Cancel",
            );
            if (action !== "Reinstall") {
                return;
            }
        }
    } catch (e) {
        vscode.window.showWarningMessage(
            `Lean AI: Could not reach the backend to check install status (${e}). ` +
                "Attempting install anyway.",
        );
    }

    await vscode.window.withProgress(
        {
            location: vscode.ProgressLocation.Notification,
            title: "Lean AI: Installing Chromium (~300MB, 1-2 minutes)",
            cancellable: false,
        },
        async (progress) => {
            progress.report({ message: "Downloading from Playwright..." });
            try {
                const result = await postJsonLongRunning<InstallResponse>(
                    "/api/ui-verification/install",
                    { repo_root: repoRoot },
                );
                if (result.success) {
                    vscode.window.showInformationMessage(
                        "Lean AI: Chromium installed successfully. UI verification is ready to use.",
                        "Run Test Capture",
                    ).then((action) => {
                        if (action === "Run Test Capture") {
                            vscode.commands.executeCommand("lean-ai.testUiVerification");
                        }
                    });
                } else {
                    // Truncate output to avoid overwhelming the notification
                    const tail = result.output.split("\n").slice(-6).join("\n");
                    vscode.window.showErrorMessage(
                        `Lean AI: Chromium install failed.\n${tail}`,
                        "View Full Output",
                    ).then((action) => {
                        if (action === "View Full Output") {
                            const doc = vscode.workspace.openTextDocument({
                                content: result.output,
                                language: "log",
                            });
                            doc.then((d) => vscode.window.showTextDocument(d));
                        }
                    });
                }
            } catch (e) {
                vscode.window.showErrorMessage(`Lean AI: Chromium install errored: ${e}`);
            }
        },
    );
}

/**
 * Command: lean-ai.testUiVerification
 *
 * Run a one-shot verify_web_ui against a URL (default example.com) to
 * confirm the pipeline works end-to-end.
 */
export async function testUiVerificationCommand(): Promise<void> {
    const repoRoot = getRepoRoot();
    if (!repoRoot) {
        vscode.window.showErrorMessage(
            "Lean AI: open a workspace folder before running a UI verification test.",
        );
        return;
    }

    const url = await vscode.window.showInputBox({
        title: "Test UI Verification",
        prompt: "URL to capture",
        value: "https://example.com",
        ignoreFocusOut: true,
    });
    if (!url) {
        return;
    }

    const question = await vscode.window.showInputBox({
        title: "Test UI Verification",
        prompt: "Question for the vision model",
        value: "Describe the layout and main elements visible on this page.",
        ignoreFocusOut: true,
    });
    if (!question) {
        return;
    }

    await vscode.window.withProgress(
        {
            location: vscode.ProgressLocation.Notification,
            title: "Lean AI: Running UI verification test (10-40s)",
            cancellable: false,
        },
        async () => {
            try {
                const result = await postJsonLongRunning<TestResponse>(
                    "/api/ui-verification/test",
                    { repo_root: repoRoot, url, question },
                );
                if (!result.success) {
                    vscode.window.showErrorMessage(
                        `Lean AI: Test failed — ${result.error ?? "unknown error"}`,
                    );
                    return;
                }
                const doc = await vscode.workspace.openTextDocument({
                    content: result.report,
                    language: "markdown",
                });
                await vscode.window.showTextDocument(doc, { preview: true });
            } catch (e) {
                vscode.window.showErrorMessage(`Lean AI: Test errored: ${e}`);
            }
        },
    );
}

/**
 * Register a listener on the `lean-ai.enableUiVerification` setting so the
 * first time a user flips it on, we prompt them to install Chromium if it
 * isn't already present.  Dismissible — we don't badger.
 */
export function registerUiVerificationWatcher(
    context: vscode.ExtensionContext,
): vscode.Disposable {
    return vscode.workspace.onDidChangeConfiguration(async (event) => {
        if (!event.affectsConfiguration("lean-ai.enableUiVerification")) {
            return;
        }
        const enabled = vscode.workspace
            .getConfiguration("lean-ai")
            .get<boolean>("enableUiVerification", false);
        if (!enabled) {
            return;
        }

        const repoRoot = getRepoRoot();
        if (!repoRoot) {
            return;
        }

        // Avoid asking more than once per session — if the user dismissed
        // the prompt, respect that.
        const asked = context.workspaceState.get<boolean>(
            "lean-ai.uiVerificationInstallPrompted",
            false,
        );
        if (asked) {
            return;
        }

        try {
            const status = await getStatus(repoRoot);
            if (status.chromium_installed && status.analysis_available) {
                return;
            }

            const reasons: string[] = [];
            if (!status.playwright_installed) {
                reasons.push("Playwright is missing — install backend extras first.");
            } else if (!status.chromium_installed) {
                reasons.push(`Chromium is not installed in .lean_ai/browsers.`);
            }
            if (!status.analysis_available && status.analysis_reason) {
                reasons.push(status.analysis_reason);
            }
            if (status.missing_system_deps.length > 0) {
                reasons.push(
                    `Missing system tools for desktop capture: ${status.missing_system_deps.join(", ")}`,
                );
            }

            const message =
                "Lean AI: UI verification enabled. " + reasons.join("  ");
            const action = await vscode.window.showInformationMessage(
                message,
                "Install Chromium",
                "Don't Ask Again",
            );
            if (action === "Install Chromium") {
                await vscode.commands.executeCommand("lean-ai.installUiVerification");
            } else if (action === "Don't Ask Again") {
                await context.workspaceState.update(
                    "lean-ai.uiVerificationInstallPrompted",
                    true,
                );
            }
        } catch {
            // Silent: the backend may be starting up or offline. The user
            // can run the install command manually whenever they're ready.
        }
    });
}

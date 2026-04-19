/**
 * Manages the Lean AI backend server process lifecycle.
 *
 * Spawns uvicorn as a child process on extension activation,
 * polls /health until ready, and kills on deactivation.
 *
 * Windows-specific handling:
 *   - Uses `netstat` + PowerShell to kill processes by port
 *   - Avoids `shell: true` so PID tracking works correctly
 *   - Cleans up zombie servers from previous sessions on startup
 *   - Auto-detects Python executable (python / python3 / py launcher)
 */

import * as fs from "fs";
import * as vscode from "vscode";
import { spawn, execSync, ChildProcess } from "child_process";
import { DEFAULT_BACKEND_URL } from "./constants";
import { buildBackendEnv, buildFullBackendEnv } from "./settingsSync";
import { ensureBackendInstalled, type BackendInstallResult } from "./backendInstaller";

const HEALTH_POLL_INTERVAL_MS = 1000;
const HEALTH_POLL_MAX_ATTEMPTS = 30; // 30 seconds max wait
const HEALTH_MONITOR_INTERVAL_MS = 20_000; // Check every 20 s
// Generous per-probe timeout so a backend that is busy (e.g. running
// embedding batches during /init) doesn't get misread as dead. A truly
// dead backend surfaces as ECONNREFUSED within ms — see the fast-path
// probe below.
const HEALTH_PROBE_TIMEOUT_MS = 30_000;
// Fast probe used to distinguish a slow-but-alive backend from a crashed
// one. Short timeout — if it succeeds we proceed; if it fails with
// connection-refused we short-circuit to immediate restart.
const FAST_PROBE_TIMEOUT_MS = 2_000;
// Number of consecutive failed probes required before auto-restart.
// One failure is a hiccup; three across ~60 s is signal.
const RESTART_AFTER_CONSECUTIVE_FAILURES = 3;

let serverProcess: ChildProcess | undefined;
let outputChannel: vscode.OutputChannel | undefined;
let managedPort: string | undefined;
let _secrets: vscode.SecretStorage | undefined;
let _context: vscode.ExtensionContext | undefined;
let _managedInstall: BackendInstallResult | null | undefined;

// Health monitor state
let healthMonitorInterval: NodeJS.Timeout | undefined;
let monitorServerDownNotified = false; // guards one-time "server down" notification
let monitorRestartInProgress = false;  // prevents concurrent restart attempts
let monitorConsecutiveFailures = 0;    // tracks threshold before auto-restart

function getConfig() {
    const config = vscode.workspace.getConfiguration("lean-ai");
    return {
        autoStart: config.get<boolean>("autoStartBackend", true),
        pythonPath: config.get<string>("pythonPath", "python"),
        backendDir: config.get<string>("backendDir", ""),
        backendUrl: config.get<string>("backendUrl") || DEFAULT_BACKEND_URL,
    };
}

function getOutputChannel(): vscode.OutputChannel {
    if (!outputChannel) {
        outputChannel = vscode.window.createOutputChannel("Lean AI Backend");
    }
    return outputChannel;
}

/**
 * Resolve the backend directory. Checks (in order):
 * 1. Explicit `lean-ai.backendDir` setting
 * 2. `{workspaceFolder}/backend` if it exists
 * 3. null (cannot auto-detect)
 */
function resolveBackendDir(): string | null {
    const { backendDir } = getConfig();
    if (backendDir) {
        return backendDir;
    }

    // Try workspace folder — only if the backend sub-directory actually exists on disk
    const folders = vscode.workspace.workspaceFolders;
    if (folders && folders.length > 0) {
        const candidate = vscode.Uri.joinPath(folders[0].uri, "backend").fsPath;
        if (fs.existsSync(candidate)) {
            return candidate;
        }
    }

    return null;
}

/**
 * Parse host and port from the backend URL.
 */
function parseHostPort(backendUrl: string): { host: string; port: string } {
    let host = "127.0.0.1";
    let port = "8422";
    try {
        const parsed = new URL(backendUrl);
        host = parsed.hostname;
        port = parsed.port || "8422";
    } catch {
        // Use defaults
    }
    return { host, port };
}

/**
 * Resolve a working Python executable.
 *
 * If the user explicitly configured a non-default pythonPath, use it as-is.
 * Otherwise probe candidates in preference order so the extension works even
 * when only `python3` or the Windows `py` launcher is on the PATH.
 */
function resolvePythonPath(configured: string): string {
    if (configured !== "python") {
        // User made an explicit choice — honour it without probing
        return configured;
    }

    const candidates =
        process.platform === "win32"
            ? ["python", "py", "python3"]   // py = Windows Python Launcher
            : ["python3", "python"];

    for (const candidate of candidates) {
        try {
            const probe =
                process.platform === "win32"
                    ? `where ${candidate}`
                    : `which ${candidate}`;
            execSync(probe, { timeout: 3000, stdio: "pipe" });
            return candidate;
        } catch {
            // Not found on PATH — try next candidate
        }
    }

    return configured; // Nothing found — fall back and let spawn surface the error
}

/**
 * Kill any process listening on the given port.
 * Works on Windows (netstat + PowerShell) and Unix (lsof + kill).
 */
function killProcessOnPort(port: string, channel: vscode.OutputChannel): void {
    try {
        if (process.platform === "win32") {
            // Find PID using netstat, then kill with PowerShell
            const result = execSync(
                `netstat -ano | findstr :${port} | findstr LISTENING`,
                { encoding: "utf-8", timeout: 5000 },
            ).trim();

            // Parse PIDs from netstat output (last column)
            const pids = new Set<string>();
            for (const line of result.split("\n")) {
                const parts = line.trim().split(/\s+/);
                const pid = parts[parts.length - 1];
                if (pid && /^\d+$/.test(pid) && pid !== "0") {
                    pids.add(pid);
                }
            }

            for (const pid of pids) {
                channel.appendLine(`[Lean AI] Killing process ${pid} on port ${port}`);
                try {
                    execSync(
                        `powershell.exe -Command "Stop-Process -Id ${pid} -Force -ErrorAction SilentlyContinue"`,
                        { timeout: 5000 },
                    );
                } catch {
                    // Process may have already exited
                }
            }
        } else {
            // Unix: lsof + kill
            try {
                const result = execSync(
                    `lsof -ti :${port}`,
                    { encoding: "utf-8", timeout: 5000 },
                ).trim();
                for (const pid of result.split("\n")) {
                    if (pid) {
                        execSync(`kill -9 ${pid}`, { timeout: 5000 });
                    }
                }
            } catch {
                // No process on port
            }
        }
    } catch {
        // No process found on port — that's fine
    }
}

async function pollHealth(url: string): Promise<boolean> {
    for (let i = 0; i < HEALTH_POLL_MAX_ATTEMPTS; i++) {
        try {
            const resp = await fetch(`${url}/api/health`);
            if (resp.ok) {
                return true;
            }
        } catch {
            // Server not ready yet
        }
        await new Promise((r) => setTimeout(r, HEALTH_POLL_INTERVAL_MS));
    }
    return false;
}

// ---------------------------------------------------------------------------
// Health monitor
// ---------------------------------------------------------------------------

/**
 * Start a background health monitor that polls /health every 20 s.
 *
 * Behaviour when the server goes down:
 *   - This window owns the server (managedPort is set): silently restart.
 *   - This window does NOT own the server: show a one-time notification with
 *     a "Start Backend" button so the user can bring it back without opening
 *     a different window.
 *
 * When the server comes back (either path) a brief "back online" notification
 * is shown and the "server down" guard is cleared.
 */
function startHealthMonitor(): void {
    stopHealthMonitor();
    monitorServerDownNotified = false;
    monitorRestartInProgress = false;
    monitorConsecutiveFailures = 0;

    const { backendUrl, autoStart } = getConfig();
    if (!autoStart) {
        return; // User manages the server manually — no monitor needed
    }

    const channel = getOutputChannel();

    healthMonitorInterval = setInterval(async () => {
        if (monitorRestartInProgress) {
            return; // A restart is already in flight — skip this tick
        }

        // Main probe — generous timeout so a backend that's busy (e.g.
        // running embedding batches during /init) doesn't get misread
        // as dead. The backend's ``busy`` field is still returned and
        // visible to humans inspecting the health endpoint directly;
        // the monitor itself only needs the 200 OK to know the backend
        // is alive.
        let isUp = false;
        let probeError: unknown = undefined;
        try {
            const resp = await fetch(`${backendUrl}/api/health`, {
                signal: AbortSignal.timeout(HEALTH_PROBE_TIMEOUT_MS),
            });
            isUp = resp.ok;
        } catch (err) {
            probeError = err;
        }

        if (isUp) {
            // Successful probe — reset failure counter and restore notification state.
            monitorConsecutiveFailures = 0;
            if (monitorServerDownNotified) {
                monitorServerDownNotified = false;
                channel.appendLine("[Lean AI] Backend reconnected.");
                vscode.window.showInformationMessage("Lean AI: Backend server is back online.");
            }
            return;
        }

        // Probe failed. Distinguish timeout (slow but alive) from
        // connection-refused (definitely dead) via a fast-path probe.
        let isDeadFast = false;
        try {
            await fetch(`${backendUrl}/api/health`, {
                signal: AbortSignal.timeout(FAST_PROBE_TIMEOUT_MS),
            });
            // If the fast probe somehow succeeds, the backend is back —
            // treat as transient; skip restart this tick.
            monitorConsecutiveFailures = 0;
            return;
        } catch (fastErr) {
            const code =
                (fastErr as NodeJS.ErrnoException | undefined)?.code
                ?? (fastErr as { cause?: NodeJS.ErrnoException } | undefined)?.cause?.code
                ?? "";
            if (code === "ECONNREFUSED" || code === "ECONNRESET") {
                isDeadFast = true;
            }
        }

        monitorConsecutiveFailures += 1;
        if (isDeadFast) {
            // Definitely dead — restart immediately without waiting for the
            // consecutive-failure threshold.
            monitorConsecutiveFailures = RESTART_AFTER_CONSECUTIVE_FAILURES;
            channel.appendLine(
                "[Lean AI] Health monitor: backend process is not reachable (connection refused).",
            );
        } else {
            channel.appendLine(
                `[Lean AI] Health probe failed ` +
                `(${monitorConsecutiveFailures}/${RESTART_AFTER_CONSECUTIVE_FAILURES}) ` +
                `— probe error: ${(probeError as Error)?.name ?? probeError}`,
            );
        }

        if (monitorConsecutiveFailures < RESTART_AFTER_CONSECUTIVE_FAILURES) {
            return; // Waiting for the backend to recover on its own.
        }

        if (managedPort) {
            // We own the server — restart it automatically.
            monitorRestartInProgress = true;
            channel.appendLine("[Lean AI] Health monitor: restarting backend...");
            startBackend()
                .catch((err) => {
                    channel.appendLine(`[Lean AI] Restart attempt failed: ${err}`);
                })
                .finally(() => {
                    monitorRestartInProgress = false;
                    monitorConsecutiveFailures = 0;
                });
        } else if (!monitorServerDownNotified) {
            // We don't own the server — show one-time notification.
            monitorServerDownNotified = true;
            channel.appendLine("[Lean AI] Health monitor: external backend no longer available.");
            vscode.window.showWarningMessage(
                "Lean AI: The backend server stopped. The window that started it may have been closed.",
                "Start Backend Here",
            ).then(async (choice) => {
                if (choice === "Start Backend Here") {
                    monitorRestartInProgress = true;
                    try {
                        await startBackend();
                    } finally {
                        monitorRestartInProgress = false;
                        monitorConsecutiveFailures = 0;
                    }
                }
            });
        }

    }, HEALTH_MONITOR_INTERVAL_MS);
}

/**
 * Stop the health monitor (called from stopBackend and deactivate).
 */
export function stopHealthMonitor(): void {
    if (healthMonitorInterval) {
        clearInterval(healthMonitorInterval);
        healthMonitorInterval = undefined;
    }
}

// ---------------------------------------------------------------------------

/**
 * Start the backend server if auto-start is enabled.
 * Returns true if the server is healthy (either already running or just started).
 *
 * When `context` is provided, attempts managed installation first: creates a
 * venv in globalStorageUri, pip-installs the bundled backend, and uses that
 * venv Python to spawn uvicorn. Falls through to manual mode when the user
 * has explicit `backendDir` or `pythonPath` settings.
 */
export async function startBackend(
    secrets?: vscode.SecretStorage,
    context?: vscode.ExtensionContext,
): Promise<boolean> {
    if (secrets) { _secrets = secrets; }
    if (context) { _context = context; }
    const { autoStart, pythonPath, backendUrl } = getConfig();
    const channel = getOutputChannel();
    const { host, port } = parseHostPort(backendUrl);

    // Check if a server is already running and healthy.
    // Retry up to 3 times (1 s apart) to tolerate brief startup delays from
    // another window that may be launching the process concurrently.
    for (let attempt = 0; attempt < 3; attempt++) {
        try {
            const resp = await fetch(`${backendUrl}/api/health`, {
                signal: AbortSignal.timeout(3000),
            });
            if (resp.ok) {
                channel.appendLine("[Lean AI] Backend already running — not managed by this window.");
                // Do NOT set managedPort: we didn't start this process, so we
                // must not kill it when this window closes.
                startHealthMonitor();
                return true;
            }
        } catch {
            // Not responding yet — retry
        }
        if (attempt < 2) {
            await new Promise((r) => setTimeout(r, 1000));
        }
    }

    if (!autoStart) {
        channel.appendLine("[Lean AI] Auto-start disabled. Start the backend manually.");
        return false;
    }

    // ── Managed install: venv in globalStorageUri ─────────────────────────
    // Try managed installation first (if context is available). This creates
    // a venv, pip-installs the bundled backend, and returns the venv Python.
    // Returns null when the user has explicit settings (manual mode).
    if (_context && _managedInstall === undefined) {
        _managedInstall = await ensureBackendInstalled(_context);
    }

    let resolvedPython: string;
    let resolvedCwd: string;

    if (_managedInstall) {
        // Managed mode — use the venv Python, cwd is globalStorageUri
        resolvedPython = _managedInstall.pythonPath;
        resolvedCwd = _managedInstall.backendDir;
    } else {
        // Manual mode — resolve backend directory from settings / workspace
        const backendDir = resolveBackendDir();
        if (!backendDir) {
            channel.appendLine(
                "[Lean AI] Cannot detect backend directory. Set lean-ai.backendDir in settings.",
            );
            vscode.window.showWarningMessage(
                "Lean AI: Cannot find backend directory. Set 'lean-ai.backendDir' in settings, or start the server manually.",
            );
            return false;
        }
        resolvedPython = resolvePythonPath(pythonPath);
        resolvedCwd = backendDir;
    }

    // Kill any zombie process on the port from a previous session
    channel.appendLine(`[Lean AI] Cleaning up port ${port}...`);
    killProcessOnPort(port, channel);

    // Brief pause to let the port fully release
    await new Promise((r) => setTimeout(r, 500));

    channel.appendLine(`[Lean AI] Starting backend in: ${resolvedCwd}`);
    channel.appendLine(`[Lean AI] Python: ${resolvedPython}`);
    channel.show(true);

    // Spawn uvicorn directly (no shell: true) so PID tracking works
    serverProcess = spawn(
        resolvedPython,
        [
            "-m",
            "uvicorn",
            "lean_ai.main:app",
            "--host",
            host,
            "--port",
            port,
        ],
        {
            cwd: resolvedCwd,
            stdio: ["ignore", "pipe", "pipe"],
            env: _secrets
                ? { ...process.env, ...(await buildFullBackendEnv(_secrets)) }
                : { ...process.env, ...buildBackendEnv() },
            // No shell: true — we want the actual uvicorn PID
        },
    );
    managedPort = port;

    serverProcess.stdout?.on("data", (data: Buffer) => {
        channel.append(data.toString());
    });

    serverProcess.stderr?.on("data", (data: Buffer) => {
        channel.append(data.toString());
    });

    serverProcess.on("exit", (code) => {
        channel.appendLine(`[Lean AI] Backend process exited with code ${code}`);
        serverProcess = undefined;
    });

    serverProcess.on("error", (err) => {
        const isNotFound = (err as NodeJS.ErrnoException).code === "ENOENT";
        if (isNotFound) {
            channel.appendLine(
                `[Lean AI] Python executable not found: "${resolvedPython}". ` +
                `Set 'lean-ai.pythonPath' in VSCode settings to the full path of your Python interpreter.`,
            );
            vscode.window.showErrorMessage(
                `Lean AI: Python not found ("${resolvedPython}"). Set 'lean-ai.pythonPath' in settings.`,
                "Open Settings",
            ).then((choice) => {
                if (choice === "Open Settings") {
                    vscode.commands.executeCommand(
                        "workbench.action.openSettings",
                        "lean-ai.pythonPath",
                    );
                }
            });
        } else {
            channel.appendLine(`[Lean AI] Failed to start backend: ${err.message}`);
        }
        serverProcess = undefined;
    });

    // Poll health endpoint until ready
    channel.appendLine("[Lean AI] Waiting for backend to be ready...");
    const ready = await pollHealth(backendUrl);

    if (ready) {
        channel.appendLine("[Lean AI] Backend is ready.");
        vscode.window.showInformationMessage("Lean AI backend started successfully.");
        startHealthMonitor();
        return true;
    } else {
        channel.appendLine("[Lean AI] Backend did not become ready in time.");
        vscode.window.showWarningMessage(
            "Lean AI backend did not start within 30 seconds. Check the 'Lean AI Backend' output panel for details.",
        );
        return false;
    }
}

/**
 * Stop the backend server process.
 * Uses port-based killing as a fallback to ensure cleanup.
 */
export function stopBackend(): void {
    stopHealthMonitor();
    const channel = getOutputChannel();
    channel.appendLine("[Lean AI] Stopping backend server...");

    // First: try to kill by PID if we have a tracked process
    if (serverProcess && serverProcess.pid) {
        try {
            if (process.platform === "win32") {
                execSync(
                    `powershell.exe -Command "Stop-Process -Id ${serverProcess.pid} -Force -ErrorAction SilentlyContinue"`,
                    { timeout: 5000 },
                );
            } else {
                serverProcess.kill("SIGTERM");
            }
        } catch {
            // Process may have already exited
        }
        serverProcess = undefined;
    }

    // Second: kill by port as a safety net (catches zombie processes)
    if (managedPort) {
        killProcessOnPort(managedPort, channel);
        managedPort = undefined;
    }

    channel.appendLine("[Lean AI] Backend server stopped.");
}

/**
 * Restart the backend server (stop + start).
 */
export async function restartBackend(): Promise<boolean> {
    const channel = getOutputChannel();
    channel.appendLine("[Lean AI] Restarting backend server...");
    stopBackend();
    // Wait for port to be fully released
    await new Promise((r) => setTimeout(r, 1000));
    // Re-use cached install result (don't re-run pip install on restart)
    return startBackend();
}

/**
 * Clear the cached managed install result so the next startBackend re-checks.
 * Called by the "Reinstall Backend" command after resetting the venv.
 */
export function clearManagedInstallCache(): void {
    _managedInstall = undefined;
}

/**
 * Check if we are managing a backend process.
 */
export function isBackendManaged(): boolean {
    return serverProcess !== undefined || managedPort !== undefined;
}

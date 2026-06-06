/**
 * Managed backend installation — creates a .venv beside the backend source,
 * pip-installs the bundled Python backend, verifies core imports, and
 * offers optional extras (openai, anthropic, reference).
 *
 * Manual mode: when the user explicitly sets `lean-ai.backendDir` or a
 * non-managed `lean-ai.pythonPath`, all auto-install logic is skipped.
 */

import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { execFile, execFileSync } from "child_process";
import { SECRET_KEYS } from "./settingsSync";
import {
    discoverSupportedPython,
    MAX_PYTHON_EXCLUSIVE,
    MIN_PYTHON,
    pythonDownloadUrl,
    pythonInstallGuidance,
    type PythonCommand,
    type PythonDiscoveryResult,
} from "./pythonDiscovery";

// ── Constants ────────────────────────────────────────────────────────────────

const VENV_DIR = ".venv";
const VERSION_KEY = "lean-ai.installedBackendVersion";
const EXTRAS_KEY = "lean-ai.installedExtras";
const EXTRAS_PROMPTED_KEY = "lean-ai.extrasPrompted";
const MANAGED_PYTHON_PATH_KEY = "lean-ai.managedPythonPath";
const MANAGED_BACKEND_DIR_KEY = "lean-ai.managedBackendDir";
const LEGACY_VENV_DIR = "backend-venv";
const VERIFY_IMPORTS = ["lean_ai", "tree_sitter", "fastapi", "uvicorn", "ollama"];
const BACKEND_COPY_EXCLUDE_NAMES = new Set([
    ".env",
    ".git",
    ".venv",
    "venv",
    "tests",
]);
const BACKEND_COPY_EXCLUDE_PATTERNS = [
    "__pycache__",
    ".egg-info",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
];

export interface BackendInstallResult {
    pythonPath: string;
    backendDir: string;
    freshInstall: boolean;
    pythonPathUpdated: boolean;
}

// ── Public API ───────────────────────────────────────────────────────────────

/**
 * Ensure the managed backend is installed and up-to-date.
 *
 * Returns the venv Python path and backendDir on success, or `null` when the
 * user is in manual mode (explicit settings override managed installation).
 */
export async function ensureBackendInstalled(
    context: vscode.ExtensionContext,
): Promise<BackendInstallResult | null> {
    // ── Manual mode detection ────────────────────────────────────────────
    const config = vscode.workspace.getConfiguration("lean-ai");
    const explicitBackendDir = config.get<string>("backendDir", "");
    const explicitPythonPath = config.get<string>("pythonPath", "python");
    const previousManagedPython = context.globalState.get<string>(MANAGED_PYTHON_PATH_KEY, "");

    if (
        explicitBackendDir ||
        (explicitPythonPath !== "python" &&
            !isManagedPythonPath(explicitPythonPath, previousManagedPython, context))
    ) {
        // User has made explicit choices — skip managed installation
        return null;
    }

    const channel = getOutputChannel();
    const target = resolveManagedBackendTarget(context, channel);
    const venvPath = path.join(target.backendDir, VENV_DIR);
    const venvPython = getVenvPythonPath(venvPath);
    const extensionVersion = context.extension.packageJSON.version as string;
    const installedVersion = context.globalState.get<string>(VERSION_KEY);
    const installedBackendDir = context.globalState.get<string>(MANAGED_BACKEND_DIR_KEY);

    // ── Already installed and up-to-date ─────────────────────────────────
    if (
        installedVersion === extensionVersion &&
        installedBackendDir === target.backendDir &&
        fs.existsSync(venvPython)
    ) {
        const pythonPathUpdated = await updateManagedPythonSetting(
            config,
            explicitPythonPath,
            previousManagedPython,
            venvPython,
            channel,
        );
        return {
            pythonPath: venvPython,
            backendDir: target.backendDir,
            freshInstall: false,
            pythonPathUpdated,
        };
    }

    // ── Install or upgrade ───────────────────────────────────────────────
    const isUpgrade = installedVersion !== undefined && installedVersion !== extensionVersion;
    const label = isUpgrade
        ? `Lean AI: Updating backend to v${extensionVersion}...`
        : "Lean AI: Setting up backend (one-time setup)...";

    return vscode.window.withProgress(
        {
            location: vscode.ProgressLocation.Notification,
            title: label,
            cancellable: false,
        },
        async (progress) => {
            try {
                if (target.copiedFrom) {
                    progress.report({ message: "Preparing bundled backend..." });
                    copyBackendSource(target.copiedFrom, target.backendDir, channel);
                }

                // Step 1: Resolve system Python (for venv creation)
                if (!fs.existsSync(venvPython)) {
                    progress.report({ message: "Locating Python..." });
                    const discovery = discoverSupportedPython(process.platform);
                    channel.appendLine(
                        `[Lean AI] Detected operating system: ${discovery.platformName} ` +
                        `(${process.platform})`,
                    );
                    if (!discovery.selected) {
                        showUnsupportedPythonError(discovery);
                        throw new Error("No supported Python 3.10-3.13 interpreter found");
                    }

                    const systemPython = discovery.selected;
                    channel.appendLine(
                        `[Lean AI] System Python: ${systemPython.label} ` +
                        `(${systemPython.version.display})`,
                    );

                    // Step 2: Create venv
                    progress.report({ message: "Creating virtual environment..." });
                    fs.mkdirSync(target.backendDir, { recursive: true });
                    await createVenv(systemPython, venvPath, channel);
                }

                // Step 3: Install / upgrade backend
                progress.report({ message: isUpgrade ? "Upgrading backend..." : "Installing backend (this may take a minute)..." });
                progress.report({ message: "Upgrading Python packaging tools..." });
                await upgradePackagingTools(venvPython, channel);

                const extras = await detectExtras(context);
                await installBackend(venvPython, target.backendDir, extras, isUpgrade, channel);

                // Step 4: Verify core imports
                progress.report({ message: "Verifying installation..." });
                const verify = verifyInstallation(venvPython, channel);
                if (!verify.ok) {
                    const msg = `Backend installation incomplete — missing: ${verify.missing.join(", ")}`;
                    channel.appendLine(`[Lean AI] ${msg}`);
                    const choice = await vscode.window.showErrorMessage(
                        `Lean AI: ${msg}`,
                        "Reinstall",
                        "View Output",
                    );
                    if (choice === "Reinstall") {
                        await deleteVenv(venvPath, channel);
                        // Recursive retry
                        return ensureBackendInstalled(context);
                    }
                    if (choice === "View Output") {
                        channel.show();
                    }
                    throw new Error(msg);
                }

                // Step 5: Store version
                await context.globalState.update(VERSION_KEY, extensionVersion);
                await context.globalState.update(MANAGED_PYTHON_PATH_KEY, venvPython);
                await context.globalState.update(MANAGED_BACKEND_DIR_KEY, target.backendDir);
                const pythonPathUpdated = await updateManagedPythonSetting(
                    config,
                    explicitPythonPath,
                    previousManagedPython,
                    venvPython,
                    channel,
                );
                channel.appendLine(`[Lean AI] Backend v${extensionVersion} installed successfully.`);

                // Step 6: Prompt for optional extras (only on first install, non-blocking)
                if (!isUpgrade) {
                    // Fire and forget — don't block server startup
                    promptOptionalExtras(context).catch(() => {});
                }

                return {
                    pythonPath: venvPython,
                    backendDir: target.backendDir,
                    freshInstall: !isUpgrade,
                    pythonPathUpdated,
                };
            } catch (err) {
                const message = err instanceof Error ? err.message : String(err);
                channel.appendLine(`[Lean AI] Installation failed: ${message}`);
                // Don't re-show if we already showed a specific error
                if (!message.includes("Python not found") && !message.includes("Python ")) {
                    const choice = await vscode.window.showErrorMessage(
                        `Lean AI: Backend installation failed. ${message}`,
                        "Retry",
                        "View Output",
                    );
                    if (choice === "Retry") {
                        return ensureBackendInstalled(context);
                    }
                    if (choice === "View Output") {
                        channel.show();
                    }
                }
                return null;
            }
        },
    );
}

/**
 * Delete the managed venv and clear stored version.
 * Used by the "Reinstall Backend" command.
 */
export async function resetBackend(context: vscode.ExtensionContext): Promise<void> {
    const channel = getOutputChannel();
    const managedBackendDir = context.globalState.get<string>(MANAGED_BACKEND_DIR_KEY);
    const venvs = new Set<string>();
    if (managedBackendDir) {
        venvs.add(path.join(managedBackendDir, VENV_DIR));
    }
    venvs.add(path.join(getBundledBackendPath(context), VENV_DIR));
    venvs.add(path.join(context.globalStorageUri.fsPath, "backend", VENV_DIR));
    venvs.add(path.join(context.globalStorageUri.fsPath, LEGACY_VENV_DIR));
    for (const venvPath of venvs) {
        await deleteVenv(venvPath, channel);
    }
    await context.globalState.update(VERSION_KEY, undefined);
    await context.globalState.update(EXTRAS_KEY, undefined);
    await context.globalState.update(EXTRAS_PROMPTED_KEY, undefined);
    await context.globalState.update(MANAGED_PYTHON_PATH_KEY, undefined);
    await context.globalState.update(MANAGED_BACKEND_DIR_KEY, undefined);
    channel.appendLine("[Lean AI] Backend reset complete. Will reinstall on next activation.");
}

/**
 * Incrementally install additional pip extras into the existing venv.
 * Does NOT delete/recreate the venv — just runs pip install with the extras.
 * Returns true on success.
 */
export async function addExtras(
    context: vscode.ExtensionContext,
    extras: string[],
): Promise<boolean> {
    if (extras.length === 0) { return true; }

    const channel = getOutputChannel();
    const backendDir = context.globalState.get<string>(MANAGED_BACKEND_DIR_KEY)
        || resolveManagedBackendTarget(context, channel).backendDir;
    const venvPath = path.join(backendDir, VENV_DIR);
    const venvPython = getVenvPythonPath(venvPath);

    if (!fs.existsSync(venvPython)) { return false; }

    try {
        await vscode.window.withProgress(
            {
                location: vscode.ProgressLocation.Notification,
                title: `Installing ${extras.join(", ")} extras...`,
                cancellable: false,
            },
            () => installBackend(venvPython, backendDir, extras, false, channel),
        );
        // Update stored extras
        const prev = context.globalState.get<string[]>(EXTRAS_KEY, []);
        const merged = [...new Set([...prev, ...extras])];
        await context.globalState.update(EXTRAS_KEY, merged);
        return true;
    } catch {
        channel.appendLine("[Lean AI] Failed to install extras: " + extras.join(", "));
        return false;
    }
}

// ── Internal helpers ─────────────────────────────────────────────────────────

let _outputChannel: vscode.OutputChannel | undefined;

function getOutputChannel(): vscode.OutputChannel {
    if (!_outputChannel) {
        _outputChannel = vscode.window.createOutputChannel("Lean AI Backend");
    }
    return _outputChannel;
}

/** Platform-aware path to the Python executable inside a venv. */
function getVenvPythonPath(venvPath: string): string {
    return process.platform === "win32"
        ? path.join(venvPath, "Scripts", "python.exe")
        : path.join(venvPath, "bin", "python");
}

/** Path to the bundled backend source within the extension. */
function getBundledBackendPath(context: vscode.ExtensionContext): string {
    return path.join(context.extensionPath, "backend");
}
interface ManagedBackendTarget {
    backendDir: string;
    copiedFrom?: string;
}

function resolveManagedBackendTarget(
    context: vscode.ExtensionContext,
    channel: vscode.OutputChannel,
): ManagedBackendTarget {
    const bundledBackend = getBundledBackendPath(context);
    if (!isUsableBackendDir(bundledBackend)) {
        throw new Error(`Bundled backend source not found at ${bundledBackend}`);
    }

    const fallbackBackend = path.join(context.globalStorageUri.fsPath, "backend");
    channel.appendLine(
        `[Lean AI] Using managed backend at ${fallbackBackend}`,
    );
    return {
        backendDir: fallbackBackend,
        copiedFrom: bundledBackend,
    };
}

function isUsableBackendDir(dir: string): boolean {
    try {
        return fs.statSync(dir).isDirectory()
            && fs.existsSync(path.join(dir, "pyproject.toml"));
    } catch {
        return false;
    }
}

function isManagedPythonPath(
    value: string,
    previousManagedPython: string,
    context?: vscode.ExtensionContext,
): boolean {
    if (!value) {
        return true;
    }
    const normalized = path.normalize(value);
    if (previousManagedPython && normalized === path.normalize(previousManagedPython)) {
        return true;
    }
    if (!context) {
        return false;
    }

    const managedCandidates = [
        getVenvPythonPath(path.join(getBundledBackendPath(context), VENV_DIR)),
        getVenvPythonPath(path.join(context.globalStorageUri.fsPath, "backend", VENV_DIR)),
        getVenvPythonPath(path.join(context.globalStorageUri.fsPath, LEGACY_VENV_DIR)),
    ];
    return managedCandidates.some((candidate) => normalized === path.normalize(candidate));
}

async function updateManagedPythonSetting(
    config: vscode.WorkspaceConfiguration,
    currentPythonPath: string,
    previousManagedPython: string,
    venvPython: string,
    channel: vscode.OutputChannel,
): Promise<boolean> {
    if (
        currentPythonPath !== "python" &&
        path.normalize(currentPythonPath) === path.normalize(venvPython)
    ) {
        return false;
    }
    if (currentPythonPath !== "python" && !isManagedPythonPath(currentPythonPath, previousManagedPython)) {
        return false;
    }

    await config.update("pythonPath", venvPython, vscode.ConfigurationTarget.Global);
    channel.appendLine(`[Lean AI] Updated lean-ai.pythonPath to managed venv: ${venvPython}`);
    return true;
}

function shouldExcludeBackendCopyEntry(name: string): boolean {
    if (BACKEND_COPY_EXCLUDE_NAMES.has(name)) {
        return true;
    }
    return BACKEND_COPY_EXCLUDE_PATTERNS.some((pattern) => name.includes(pattern));
}

function copyBackendSource(src: string, dst: string, channel: vscode.OutputChannel): void {
    if (!isUsableBackendDir(src)) {
        throw new Error(`Bundled backend source not found at ${src}`);
    }

    channel.appendLine(`[Lean AI] Copying bundled backend from ${src} to ${dst}`);
    fs.mkdirSync(dst, { recursive: true });
    copyRecursive(src, dst);
}

function copyRecursive(src: string, dst: string): void {
    const stat = fs.statSync(src);
    if (stat.isDirectory()) {
        fs.mkdirSync(dst, { recursive: true });
        for (const entry of fs.readdirSync(src)) {
            if (shouldExcludeBackendCopyEntry(entry)) {
                continue;
            }
            copyRecursive(path.join(src, entry), path.join(dst, entry));
        }
        return;
    }
    fs.copyFileSync(src, dst);
}

/** Create a Python virtual environment. */
function createVenv(
    systemPython: PythonCommand,
    venvPath: string,
    channel: vscode.OutputChannel,
): Promise<void> {
    return new Promise((resolve, reject) => {
        channel.appendLine(`[Lean AI] Creating venv at ${venvPath}`);
        const proc = execFile(
            systemPython.command,
            [...systemPython.args, "-m", "venv", venvPath],
            { timeout: 60_000 },
            (err) => {
                if (err) {
                    channel.appendLine(`[Lean AI] venv creation failed: ${err.message}`);
                    reject(err);
                    return;
                }
                // Verify the venv python exists
                const venvPython = getVenvPythonPath(venvPath);
                if (!fs.existsSync(venvPython)) {
                    const msg = `venv created but Python not found at ${venvPython}`;
                    channel.appendLine(`[Lean AI] ${msg}`);
                    reject(new Error(msg));
                    return;
                }
                channel.appendLine("[Lean AI] venv created successfully.");
                resolve();
            },
        );
        proc.stdout?.on("data", (d: Buffer) => channel.append(d.toString()));
        proc.stderr?.on("data", (d: Buffer) => channel.append(d.toString()));
    });
}

/** pip install the backend from the bundled source. */
function installBackend(
    venvPython: string,
    backendPath: string,
    extras: string[],
    upgrade: boolean,
    channel: vscode.OutputChannel,
): Promise<void> {
    const allExtras = [...new Set(extras)];
    const target = allExtras.length > 0
        ? `${backendPath}[${allExtras.join(",")}]`
        : backendPath;

    const args = ["-m", "pip", "install"];
    if (upgrade) {
        args.push("--upgrade");
    }
    args.push(target);

    channel.appendLine(`[Lean AI] Running: ${venvPython} ${args.join(" ")}`);

    return new Promise((resolve, reject) => {
        const proc = execFile(
            venvPython,
            args,
            { timeout: 300_000 }, // 5 min for slow networks
            (err) => {
                if (err) {
                    channel.appendLine(`[Lean AI] pip install failed: ${err.message}`);
                    reject(err);
                    return;
                }
                channel.appendLine("[Lean AI] pip install completed.");
                resolve();
            },
        );
        proc.stdout?.on("data", (d: Buffer) => channel.append(d.toString()));
        proc.stderr?.on("data", (d: Buffer) => channel.append(d.toString()));
    });
}

function upgradePackagingTools(
    venvPython: string,
    channel: vscode.OutputChannel,
): Promise<void> {
    const args = ["-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"];
    channel.appendLine(`[Lean AI] Running: ${venvPython} ${args.join(" ")}`);

    return new Promise((resolve, reject) => {
        const proc = execFile(
            venvPython,
            args,
            { timeout: 300_000 },
            (err) => {
                if (err) {
                    channel.appendLine(`[Lean AI] packaging tool upgrade failed: ${err.message}`);
                    reject(err);
                    return;
                }
                channel.appendLine("[Lean AI] Python packaging tools upgraded.");
                resolve();
            },
        );
        proc.stdout?.on("data", (d: Buffer) => channel.append(d.toString()));
        proc.stderr?.on("data", (d: Buffer) => channel.append(d.toString()));
    });
}

/** Verify core packages are importable. */
function verifyInstallation(
    venvPython: string,
    channel: vscode.OutputChannel,
): { ok: boolean; missing: string[] } {
    const missing: string[] = [];
    for (const mod of VERIFY_IMPORTS) {
        try {
            execFileSync(venvPython, ["-c", `import ${mod}`], {
                timeout: 10_000,
                stdio: "pipe",
            });
        } catch {
            missing.push(mod);
        }
    }
    if (missing.length > 0) {
        channel.appendLine(`[Lean AI] Verification failed — missing: ${missing.join(", ")}`);
    } else {
        channel.appendLine("[Lean AI] All core imports verified.");
    }
    return { ok: missing.length === 0, missing };
}

/** Delete the venv directory. */
async function deleteVenv(venvPath: string, channel: vscode.OutputChannel): Promise<void> {
    if (fs.existsSync(venvPath)) {
        channel.appendLine(`[Lean AI] Removing venv at ${venvPath}`);
        fs.rmSync(venvPath, { recursive: true, force: true });
        channel.appendLine("[Lean AI] venv removed.");
    }
}

/**
 * Auto-detect which optional extras to install based on current config.
 * For example, if the user has an OpenAI API key stored, include "openai".
 */
async function detectExtras(context: vscode.ExtensionContext): Promise<string[]> {
    const extras: string[] = [];
    const config = vscode.workspace.getConfiguration("lean-ai");
    const provider = config.get<string>("llmProvider", "ollama");
    const expertProvider = config.get<string>("expertLlmProvider", "");
    const requestProvider = config.get<string>("requestLlmProvider", "");

    // Check API keys in SecretStorage
    const openaiKey = await context.secrets.get(SECRET_KEYS.openaiApiKey);
    const anthropicKey = await context.secrets.get(SECRET_KEYS.anthropicApiKey);

    if (openaiKey || provider === "openai" || expertProvider === "openai" || requestProvider === "openai") {
        extras.push("openai");
    }
    if (anthropicKey || provider === "anthropic" || expertProvider === "anthropic" || requestProvider === "anthropic") {
        extras.push("anthropic");
    }

    // Auto-detect voice extras if any voice feature is enabled
    const sttEnabled = config.get<boolean>("enableStt", false);
    const ttsEnabled = config.get<boolean>("enableTts", false);
    const wakeWordEnabled = config.get<boolean>("enableWakeWord", false);
    if (sttEnabled || ttsEnabled || wakeWordEnabled) {
        extras.push("voice");
    }

    return extras;
}

/**
 * After first install, prompt the user about optional extras they might want.
 * Non-blocking — runs asynchronously after server startup.
 */
async function promptOptionalExtras(context: vscode.ExtensionContext): Promise<void> {
    // Don't prompt again if we already asked
    if (context.globalState.get<boolean>(EXTRAS_PROMPTED_KEY)) {
        return;
    }
    await context.globalState.update(EXTRAS_PROMPTED_KEY, true);

    // Small delay so the server startup notification clears first
    await new Promise((r) => setTimeout(r, 3000));

    const choice = await vscode.window.showInformationMessage(
        "Lean AI is ready! Want to add cloud LLM support or document indexing?",
        "Configure Extras",
        "Not Now",
    );

    if (choice !== "Configure Extras") {
        return;
    }

    const items: vscode.QuickPickItem[] = [
        {
            label: "openai",
            description: "OpenAI API support (GPT-4o, etc.)",
            picked: false,
        },
        {
            label: "anthropic",
            description: "Anthropic API support (Claude, etc.)",
            picked: false,
        },
        {
            label: "reference",
            description: "Reference library document indexing (EPUB, PDF, Word)",
            picked: false,
        },
        {
            label: "voice",
            description: "Voice interaction — STT, TTS, wake word (requires portaudio system lib)",
            picked: false,
        },
    ];

    const selected = await vscode.window.showQuickPick(items, {
        canPickMany: true,
        title: "Select optional features to install",
        placeHolder: "Pick extras to install (or press Escape to skip)",
    });

    if (!selected || selected.length === 0) {
        return;
    }

    const extras = selected.map((s) => s.label);
    const backendDir = context.globalState.get<string>(MANAGED_BACKEND_DIR_KEY)
        || resolveManagedBackendTarget(context, getOutputChannel()).backendDir;
    const venvPath = path.join(backendDir, VENV_DIR);
    const venvPython = getVenvPythonPath(venvPath);

    await vscode.window.withProgress(
        {
            location: vscode.ProgressLocation.Notification,
            title: `Lean AI: Installing extras (${extras.join(", ")})...`,
            cancellable: false,
        },
        async () => {
            const channel = getOutputChannel();
            try {
                await installBackend(venvPython, backendDir, extras, false, channel);
                await context.globalState.update(EXTRAS_KEY, extras);
                vscode.window.showInformationMessage(
                    `Lean AI: Installed extras: ${extras.join(", ")}. Restart the backend to use them.`,
                );
            } catch (err) {
                const msg = err instanceof Error ? err.message : String(err);
                vscode.window.showErrorMessage(
                    `Lean AI: Failed to install extras. ${msg}`,
                );
                channel.show();
            }
        },
    );
}

function showUnsupportedPythonError(discovery: PythonDiscoveryResult): void {
    const found = discovery.detected.length > 0
        ? ` Found: ${discovery.detected.map((item) => item.version.display).join(", ")}.`
        : "";
    const supported = `${MIN_PYTHON.join(".")} through ` +
        `${MAX_PYTHON_EXCLUSIVE[0]}.${MAX_PYTHON_EXCLUSIVE[1] - 1}`;
    vscode.window.showErrorMessage(
        `Lean AI requires Python ${supported}; Python 3.14 is not supported because ` +
        `voice dependencies require Python 3.13 or earlier.${found} ` +
        pythonInstallGuidance(process.platform),
        "Install Python",
        "Open Settings",
    ).then((choice) => {
        if (choice === "Install Python") {
            vscode.env.openExternal(vscode.Uri.parse(pythonDownloadUrl()));
        } else if (choice === "Open Settings") {
            vscode.commands.executeCommand("workbench.action.openSettings", "lean-ai.pythonPath");
        }
    });
}

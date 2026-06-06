import { execFileSync } from "child_process";

export const MIN_PYTHON: [number, number] = [3, 10];
export const MAX_PYTHON_EXCLUSIVE: [number, number] = [3, 14];

export interface PythonCommand {
    command: string;
    args: string[];
    label: string;
}

export interface PythonVersion {
    major: number;
    minor: number;
    patch?: number;
    display: string;
}

export interface PythonDiscoveryResult {
    platformName: string;
    selected?: PythonCommand & { version: PythonVersion };
    detected: Array<PythonCommand & { version: PythonVersion }>;
}

type VersionRunner = (candidate: PythonCommand) => string;

export function platformDisplayName(platform: NodeJS.Platform): string {
    if (platform === "win32") {
        return "Windows";
    }
    if (platform === "darwin") {
        return "macOS";
    }
    if (platform === "linux") {
        return "Linux";
    }
    return platform;
}

export function getPythonCandidates(platform: NodeJS.Platform): PythonCommand[] {
    if (platform === "win32") {
        return [
            launcherCandidate("3.13"),
            launcherCandidate("3.12"),
            launcherCandidate("3.11"),
            launcherCandidate("3.10"),
            commandCandidate("python.exe"),
            commandCandidate("python3.exe"),
            commandCandidate("python"),
            commandCandidate("python3"),
        ];
    }

    const versioned = ["3.13", "3.12", "3.11", "3.10"]
        .map((version) => commandCandidate(`python${version}`));

    if (platform === "darwin") {
        return [
            commandCandidate("/opt/homebrew/bin/python3.13"),
            commandCandidate("/usr/local/bin/python3.13"),
            ...versioned,
            commandCandidate("python3"),
            commandCandidate("python"),
        ];
    }

    return [
        ...versioned,
        commandCandidate("python3"),
        commandCandidate("python"),
    ];
}

export function discoverSupportedPython(
    platform: NodeJS.Platform,
    runVersion: VersionRunner = runPythonVersion,
): PythonDiscoveryResult {
    const detected: Array<PythonCommand & { version: PythonVersion }> = [];

    for (const candidate of getPythonCandidates(platform)) {
        try {
            const version = parsePythonVersion(runVersion(candidate));
            if (!version) {
                continue;
            }
            const found = { ...candidate, version };
            detected.push(found);
            if (isSupportedPythonVersion(version)) {
                return {
                    platformName: platformDisplayName(platform),
                    selected: found,
                    detected,
                };
            }
        } catch {
            // Candidate is unavailable or cannot execute.
        }
    }

    return {
        platformName: platformDisplayName(platform),
        detected,
    };
}

export function parsePythonVersion(output: string): PythonVersion | null {
    const match = output.trim().match(/Python\s+(\d+)\.(\d+)(?:\.(\d+))?/i);
    if (!match) {
        return null;
    }
    return {
        major: Number(match[1]),
        minor: Number(match[2]),
        patch: match[3] === undefined ? undefined : Number(match[3]),
        display: [match[1], match[2], match[3]].filter(Boolean).join("."),
    };
}

export function isSupportedPythonVersion(version: PythonVersion): boolean {
    const current = version.major * 100 + version.minor;
    const minimum = MIN_PYTHON[0] * 100 + MIN_PYTHON[1];
    const maximum = MAX_PYTHON_EXCLUSIVE[0] * 100 + MAX_PYTHON_EXCLUSIVE[1];
    return current >= minimum && current < maximum;
}

export function pythonInstallGuidance(platform: NodeJS.Platform): string {
    if (platform === "win32") {
        return "Install Python 3.13 from python.org and enable the Python launcher.";
    }
    if (platform === "darwin") {
        return "Install Python 3.13 with Homebrew (`brew install python@3.13`) or python.org.";
    }
    if (platform === "linux") {
        return "Install a Python 3.10-3.13 package for your distribution (Python 3.13 recommended).";
    }
    return "Install Python 3.10-3.13 (Python 3.13 recommended).";
}

export function pythonDownloadUrl(): string {
    return "https://www.python.org/downloads/release/python-31312/";
}

function launcherCandidate(version: string): PythonCommand {
    return {
        command: "py",
        args: [`-${version}`],
        label: `py -${version}`,
    };
}

function commandCandidate(command: string): PythonCommand {
    return { command, args: [], label: command };
}

function runPythonVersion(candidate: PythonCommand): string {
    return execFileSync(candidate.command, [...candidate.args, "--version"], {
        timeout: 5000,
        stdio: "pipe",
        encoding: "utf-8",
    }).trim();
}

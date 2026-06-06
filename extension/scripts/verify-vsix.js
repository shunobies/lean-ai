const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const vsixPath = process.argv[2];
if (!vsixPath || !fs.existsSync(vsixPath)) {
    console.error("Usage: node scripts/verify-vsix.js <extension.vsix>");
    process.exit(1);
}

const maxBytes = 15 * 1024 * 1024;
const size = fs.statSync(vsixPath).size;
if (size > maxBytes) {
    throw new Error(`VSIX is ${(size / 1024 / 1024).toFixed(1)} MB; maximum is 15 MB`);
}

const entries = execFileSync("unzip", ["-Z1", vsixPath], {
    encoding: "utf-8",
}).trim().split(/\r?\n/);

const required = [
    "extension/dist/extension.js",
    "extension/backend/pyproject.toml",
    "extension/backend/src/lean_ai/main.py",
];
for (const entry of required) {
    if (!entries.includes(entry)) {
        throw new Error(`VSIX is missing required entry: ${entry}`);
    }
}

const forbidden = [
    /\/\.venv[^/]*\//,
    /\/\.lean_ai\//,
    /\/\.ruff_cache\//,
    /\/__pycache__\//,
    /\.db(?:-shm|-wal)?$/,
    /\/tests?\//,
];
for (const entry of entries) {
    if (forbidden.some((pattern) => pattern.test(entry))) {
        throw new Error(`VSIX contains forbidden generated content: ${entry}`);
    }
}

const pyproject = execFileSync(
    "unzip",
    ["-p", vsixPath, "extension/backend/pyproject.toml"],
    { encoding: "utf-8" },
);
if (!pyproject.includes('requires-python = ">=3.10,<3.14"')) {
    throw new Error("Bundled backend must require Python >=3.10,<3.14");
}

console.log(
    `Verified ${path.basename(vsixPath)}: ${entries.length} files, ` +
    `${(size / 1024 / 1024).toFixed(1)} MB`,
);

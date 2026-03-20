/**
 * Copy the backend Python source into the extension directory for VSIX bundling.
 *
 * Excludes tests, dev artifacts, __pycache__, .env, and .egg-info so the
 * VSIX stays lean (~2 MB of Python source).
 */

const fs = require("fs");
const path = require("path");

const SRC = path.resolve(__dirname, "../../backend");
const DST = path.resolve(__dirname, "../backend");

const EXCLUDE_PATTERNS = [
    "__pycache__",
    ".egg-info",
    ".ruff_cache",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
];

const EXCLUDE_NAMES = new Set([
    "tests",
    ".env",
    ".git",
    ".venv",
    "venv",
]);

function shouldExclude(name, fullPath) {
    if (EXCLUDE_NAMES.has(name)) return true;
    for (const pattern of EXCLUDE_PATTERNS) {
        if (name.includes(pattern)) return true;
    }
    return false;
}

function copyRecursive(src, dst) {
    const stat = fs.statSync(src);
    if (stat.isDirectory()) {
        fs.mkdirSync(dst, { recursive: true });
        for (const entry of fs.readdirSync(src)) {
            if (shouldExclude(entry, path.join(src, entry))) continue;
            copyRecursive(path.join(src, entry), path.join(dst, entry));
        }
    } else {
        fs.copyFileSync(src, dst);
    }
}

// Clean previous copy
if (fs.existsSync(DST)) {
    fs.rmSync(DST, { recursive: true, force: true });
}

if (!fs.existsSync(SRC)) {
    console.error(`Backend source not found at ${SRC}`);
    process.exit(1);
}

console.log(`Copying backend from ${SRC} to ${DST}...`);
copyRecursive(SRC, DST);
console.log("Backend copied successfully.");

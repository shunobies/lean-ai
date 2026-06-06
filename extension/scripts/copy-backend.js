/**
 * Copy the backend Python source into the extension directory for VSIX bundling.
 *
 * Uses an allowlist so local databases, virtualenvs, checkpoints, and caches
 * can never leak into a published VSIX.
 */

const fs = require("fs");
const path = require("path");

const SRC = path.resolve(__dirname, "../../backend");
const DST = path.resolve(__dirname, "../backend");

function copyRecursive(src, dst) {
    const stat = fs.statSync(src);
    if (stat.isDirectory()) {
        fs.mkdirSync(dst, { recursive: true });
        for (const entry of fs.readdirSync(src)) {
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
fs.mkdirSync(DST, { recursive: true });
for (const entry of ["pyproject.toml", "uv.lock", "src"]) {
    const source = path.join(SRC, entry);
    if (!fs.existsSync(source)) {
        console.error(`Required backend entry not found: ${source}`);
        process.exit(1);
    }
    copyRecursive(source, path.join(DST, entry));
}
console.log("Backend copied successfully.");

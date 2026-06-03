#!/usr/bin/env bash
set -euo pipefail

# Build and install a local Lean AI VSIX into VSCodium.
#
# Usage:
#   ./local_build.sh [patch|minor|major|<exact-version>]
#
# Defaults to a patch version bump. This updates package.json and
# package-lock.json, builds a VSIX, and installs it into VSCodium with --force.
# It does not commit, tag, or push.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

BUMP="${1:-patch}"

if command -v codium >/dev/null 2>&1; then
    CODIUM_BIN="codium"
elif command -v vscodium >/dev/null 2>&1; then
    CODIUM_BIN="vscodium"
else
    echo "ERROR: VSCodium CLI not found. Expected 'codium' or 'vscodium' on PATH." >&2
    exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
    echo "ERROR: npm is required but was not found on PATH." >&2
    exit 1
fi

echo "==> Bumping extension version (${BUMP})..."
NEW_VERSION="$(npm version "${BUMP}" --no-git-tag-version)"
NEW_VERSION="${NEW_VERSION#v}"

VSIX="lean-ai-${NEW_VERSION}.vsix"

echo "==> Installing npm dependencies if needed..."
if [[ ! -d node_modules ]]; then
    npm install
fi

echo "==> Packaging ${VSIX}..."
npm run package

if [[ ! -f "${VSIX}" ]]; then
    echo "ERROR: Expected VSIX was not created: ${SCRIPT_DIR}/${VSIX}" >&2
    exit 1
fi

echo "==> Installing ${VSIX} into VSCodium..."
"${CODIUM_BIN}" --install-extension "${VSIX}" --force

echo
echo "Installed Lean AI ${NEW_VERSION} into VSCodium."
echo "Reload VSCodium if it is already running."

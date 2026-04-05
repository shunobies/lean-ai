#!/usr/bin/env bash
set -euo pipefail

# Usage: ./release.sh <version> <message>
# Example: ./release.sh 0.5.0 "Add voice features and fix TTS crash"

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <version> <message>"
    echo "Example: $0 0.5.0 \"Add voice features and fix TTS crash\""
    exit 1
fi

VERSION="$1"
shift
MESSAGE="$*"
TAG="v${VERSION}"

echo "==> Pushing current commits to main..."
git push origin main

echo "==> Bumping extension version to ${VERSION}..."
cd extension/
npm version "${VERSION}" --no-git-tag-version
cd ..

echo "==> Bumping JetBrains plugin version to ${VERSION}..."
sed -i "s/^pluginVersion = .*/pluginVersion = ${VERSION}/" jetbrains-plugin/gradle.properties

echo "==> Committing version bump..."
git add extension/package.json extension/package-lock.json jetbrains-plugin/gradle.properties
git commit -m "${MESSAGE}"

echo "==> Tagging ${TAG}..."
git tag "${TAG}"

echo "==> Pushing commit and tag..."
git push origin main
git push origin "${TAG}"

echo "==> Done! Released ${TAG}"

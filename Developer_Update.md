# Developer Update Notes

Quick-reference notes for publishing, building, and maintaining the Lean AI extension.

## Publishing to Open VSX (VSCodium)

**First-time setup:**
1. Sign in at https://open-vsx.org with your GitHub account
2. Claim the `lean-ai` namespace (must match `"publisher"` in `package.json`)
3. Generate an access token from your Open VSX account settings

**Publish:**
```bash
cd extension

# Install ovsx CLI (first time only)
npm install -g ovsx

# Publish existing VSIX
npx ovsx publish lean-ai-0.2.4.vsix -p YOUR_OPENVSX_TOKEN

# Or build and publish in one step
npx ovsx publish -p YOUR_OPENVSX_TOKEN
```

**Patch updates:**
```bash
npm version patch        # 0.2.4 -> 0.2.5
npm run build
npx ovsx publish -p YOUR_OPENVSX_TOKEN
```

**Store token (optional):**
```bash
export OVSX_PAT=your_token_here
npx ovsx publish
```

Add `export OVSX_PAT=...` to `~/.bashrc` or `~/.zshrc` to persist.

---

## Publishing to VSCode Marketplace

**First-time setup:**
1. Create an Azure DevOps org at https://dev.azure.com (sign in with Microsoft account)
2. Create a Personal Access Token (PAT):
   - Profile icon > Personal Access Tokens > New Token
   - Organization: **All accessible organizations**
   - Scopes: Custom > **Marketplace > Manage**
   - Copy the token immediately
3. Create publisher at https://marketplace.visualstudio.com/manage
   - Publisher ID: `lean-ai` (must match `package.json`)

**Publish:**
```bash
cd extension
npx @vscode/vsce login lean-ai    # paste PAT when prompted
npx @vscode/vsce publish
```

**Patch updates:**
```bash
npx @vscode/vsce publish patch    # bumps version automatically
```

---

## Automated Publishing (GitHub Actions)

Publishing is automated via `.github/workflows/deploy-extension.yml`. Pushing a version tag triggers deployment to both Open VSX and VS Code Marketplace.

**Deploy a new version:**
```bash
git tag vX.Y.Z && git push origin vX.Y.Z
```

**Secrets required** (GitHub repo → Settings → Secrets → Actions):
- `OPEN_VSX_TOKEN` — from https://open-vsx.org → Settings → Access Tokens
- `VS_MARKETPLACE_TOKEN` — Azure DevOps PAT with Marketplace > Manage scope

---

## Building the Extension

```bash
cd extension

# Build
npm run build

# Type check (catches errors esbuild ignores)
npx tsc --noEmit

# Package as VSIX
npx @vscode/vsce package --no-dependencies

# Install locally in VSCodium
codium --install-extension lean-ai-X.Y.Z.vsix --force
```

**Important:** Always bump the version number before reinstalling locally. VSCodium caches JS/CSS by version - same version = stale cache = broken UI.

---

## Webview Script Gotchas

The file `sidebarHtml.ts` uses a TypeScript template literal (backticks) to generate HTML with inline `<script>`. Escape sequences are double-interpreted:

| What you write in TS | What esbuild outputs | Browser sees |
|----------------------|---------------------|--------------|
| `'hello'` | `'hello'` | `hello` |
| `'it\\'s'` | `'it\\'s'` | **SyntaxError** (backslash + closing quote) |
| `'it&#39;s'` | `'it&#39;s'` | `it's` (HTML entity, safe) |
| `'\\n'` | `'\n'` | **SyntaxError** (literal newline in string) |
| `String.fromCharCode(10)` | `String.fromCharCode(10)` | newline (safe) |

**Rules for JS inside `sidebarHtml.ts`:**
- Never use `\n` or `\\n` in JS strings - use `String.fromCharCode(10)`
- Never use apostrophes in single-quoted innerHTML strings - use `&#39;`
- Prefer DOM API (`createElement`) over `innerHTML` for dynamic content
- Always verify with: `node -e "const b=require('fs').readFileSync('dist/extension.js','utf8'); const m=b.match(/<script>([\s\S]*?)<\/script>/); try{new Function(m[1]);console.log('OK')}catch(e){console.log('FAIL:',e.message)}"`

---

## Running Tests

```bash
cd backend

# All tests
.venv/bin/python -m pytest tests/ -v

# Just the LLM client tests (fastest)
.venv/bin/python -m pytest tests/unit/test_client.py -v

# Lint
.venv/bin/ruff check src/ tests/
```

Note: 8 tests in `test_config.py` may fail due to local `.env` overriding defaults - these are environment-specific, not real failures.

---

## /request Mode Prompt Tips

For research-heavy `/request` tasks, structure prompts to prevent infinite search loops:

```
/request Create a file called [name].md covering [topic].

Research phase (8-10 searches, then fetch top 3-5 URLs):
- [specific search 1]
- [specific search 2]
- [specific search 3]
- Choose 3-5 additional searches based on gaps you find

After fetching, use the scratchpad to track key findings and which
sections you have enough material for. Fetched pages are saved
automatically - you can read_file on them later for deeper review.

Then create [filename] covering:
1. [section 1]
2. [section 2]
...

Write as authoritative instructions. Include code examples.
Target [line count] lines. Split across multiple create_file/edit_file
calls if needed.

After creating the file, call task_complete.
```

Key elements:
- Cap the research ("8-10 searches max")
- Tell it to fetch specific number of URLs
- Give explicit section structure
- Set a line target
- End with "call task_complete"

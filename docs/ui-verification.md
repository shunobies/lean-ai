# UI Verification

Vision-backed screenshot analysis for web pages and desktop GUI apps. Adds two tools the LLM can call during planning, chat, execution, and fix-mode investigation:

- **`verify_web_ui`** — captures a headless Chromium screenshot of a URL and analyses it.
- **`verify_desktop_ui`** — launches a desktop app (Tkinter, Qt, Swing, JavaFX, Electron, native Win32/Cocoa/GTK), captures its window, kills the subprocess.

Each call returns a markdown report with a focused answer to your question, a structured inventory of regions and components, verbatim text transcription, a pixel-sampled color palette, and any warnings. The underlying vision model runs a 4-pass pipeline so a small local model (e.g. `qwen3.5:4b-q8_0`) produces reliable structured output.

**Disabled by default.** Every tool list is byte-identical to pre-feature behaviour until you flip the toggle.

> ⚠️ Not a deterministic regression gate. Vision models are reliable for "does the layout look right / is the login form present / did rendering obviously break" but can miss pixel-level diffs. Treat these tools as a complement to E2E assertions, not a replacement.

## Prerequisites

Before enabling the feature, make sure you have:

1. **A configured vision model** (`LEAN_AI_VISION_MODEL`). Any Ollama vision-capable model works — `qwen3.5:4b-q8_0`, `qwen3-vl:8b`, `llama3.2-vision:11b`, etc. Pull it with `ollama pull <model>`.
2. **The `ui-verification` backend extras installed** (see below).
3. **Chromium** (for `verify_web_ui`) — installed by running a one-time command from the extension panel.
4. **Platform-specific system packages** (for `verify_desktop_ui` on Linux X11 — see [Per-Platform Setup](#per-platform-setup)).

## Installation

### 1. Backend extras

```bash
cd backend && pip install -e ".[dev,ui-verification]"
```

Pulls in Playwright, Pillow, numpy, mss, and — via PEP 508 environment markers — the per-platform window libraries:

| Marker | Package | Purpose |
|---|---|---|
| `sys_platform == 'win32'` | `pywin32`, `pygetwindow` | Windows window lookup + capture |
| `sys_platform == 'darwin'` | `pyobjc-framework-Quartz` | macOS `CGWindowListCopyWindowInfo` |
| `sys_platform == 'linux'` | `dbus-next` | Reserved for future XDG Portal Wayland support |

pip handles the platform selection automatically — the same install command works on every OS.

### 2. Chromium browser

Chromium is installed **workspace-locally** into `<workspace>/.lean_ai/browsers` via `PLAYWRIGHT_BROWSERS_PATH`. This keeps workspaces isolated and makes uninstall a simple `rm -rf .lean_ai/browsers`.

**From the extension:**

1. Open the VS Code command palette (`Ctrl+Shift+P` / `Cmd+Shift+P`).
2. Run **Lean AI: Install UI Verification (Chromium)**.
3. Wait for the progress notification to complete (~300MB, typically 1–2 minutes).
4. On success you'll be offered a **Run Test Capture** button — click it to smoke-test the pipeline against `https://example.com`.

**Command line (equivalent):**

```bash
PLAYWRIGHT_BROWSERS_PATH="<workspace>/.lean_ai/browsers" \
python -m playwright install chromium
```

**REST equivalent** (for scripted / CI setups):

```bash
curl -X POST http://localhost:8422/api/ui-verification/install \
  -H 'Content-Type: application/json' \
  -d '{"repo_root": "/path/to/your/workspace"}'
```

A system-wide install of Chromium or Google Chrome does **not** replace this step under normal conditions — Playwright pins specific browser versions for reproducibility.

**Exception: system browser fallback.** If Playwright refuses to install Chromium because your OS isn't in its supported matrix (common on freshly released distros — e.g. Ubuntu 26.04 within a few days of release), the install command looks for a system-installed `chromium`, `chromium-browser`, or `google-chrome` on `PATH` and, if one is present, reports success with a note that the system browser will be used via Playwright's `channel=` parameter. `verify_web_ui` then launches that browser transparently. You can preempt the problem by running `sudo apt install chromium-browser` (or equivalent) before enabling the feature.

The status endpoint's `system_browser_channel` field tells you which fallback Playwright will use when the managed install isn't present; `web_capture_available` is `true` whenever either the managed install or a system browser is usable.

### 3. Enable the feature

Either flip the toggle in VS Code Settings (`lean-ai.enableUiVerification`) or set the env var:

```bash
LEAN_AI_ENABLE_UI_VERIFICATION=true
```

The first time you enable it in the extension, a one-time prompt offers to run the Chromium install if it isn't already present. Dismiss with **Don't Ask Again** to suppress per workspace.

## Per-Platform Setup

### Windows

Nothing extra. `pygetwindow` and `mss` ship via the extras install, and Windows subprocesses are put into their own process group for clean termination.

### macOS

`screencapture` is already present. The one gotcha: **Screen Recording permission**.

1. The first `verify_desktop_ui` call will fail with a specific error pointing at System Settings.
2. Open **System Settings → Privacy & Security → Screen Recording**.
3. Enable the toggle for the process running the lean-ai backend — usually **VSCode** (if you're using the managed backend install) or your **terminal emulator** (if you launched `uvicorn` manually).
4. Fully restart VSCode or the terminal for the permission to take effect.

macOS capture uses `screencapture -l <window_id>`, which captures even occluded windows correctly.

### Linux X11

Install `wmctrl` and `xdotool`:

```bash
# Debian / Ubuntu
sudo apt install wmctrl xdotool

# Fedora
sudo dnf install wmctrl xdotool

# Arch
sudo pacman -S wmctrl xdotool
```

`/api/ui-verification/status` reports any missing tools in `missing_system_deps`.

### Linux Wayland

**Install `grim`:**

```bash
# Debian / Ubuntu
sudo apt install grim

# Fedora
sudo dnf install grim

# Arch
sudo pacman -S grim
```

**Known limitation:** window-by-title lookup is not generally recoverable on Wayland without per-compositor APIs. `verify_desktop_ui` captures the full display on Wayland; `window_title` is accepted for API parity but logged as ignored. If you need window-specific capture on Wayland, run X11 (`startx`) or use a compositor-specific tool outside of lean-ai.

An XDG Desktop Portal path is a documented follow-up (the `dbus-next` dep is already in the extras group for that reason).

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `LEAN_AI_ENABLE_UI_VERIFICATION` | `false` | Master switch. Also exposed as `lean-ai.enableUiVerification` in the extension. |
| `LEAN_AI_UI_VERIFICATION_TIMEOUT` | `180.0` | Outer timeout in seconds wrapping the whole tool call (capture + 3 vision passes + color sampling + focused answer). |
| `LEAN_AI_UI_VERIFICATION_VISION_TIMEOUT` | `180.0` | Per-pass vision timeout override. Small local models can take ~30–60s per structured pass. |
| `LEAN_AI_UI_VERIFICATION_WAIT_SECONDS` | `3.0` | Post-render settling time before the screenshot fires. Covers transitions, lazy content, font loading. |
| `LEAN_AI_UI_VERIFICATION_VIEWPORT` | `1280x800` | Default `verify_web_ui` viewport. Examples: `1280x800` (desktop), `375x812` (mobile). |
| `LEAN_AI_UI_VERIFICATION_MAX_COLOR_SAMPLES` | `5` | Number of dominant colors to return from the pixel-sampled palette. |
| `LEAN_AI_UI_VERIFICATION_CAPTURE_BACKEND_OVERRIDE` | *(empty — auto-detect)* | Force a backend: `mss-win32`, `mac-screencapture`, `mss-x11`, or `grim`. Useful for debugging unusual environments. |

## Using the Tools

Once enabled, the LLM can call these tools during planning (Phase 2 exploration, Phase 3 design), chat, execution, and fix-mode investigation. You typically don't invoke them directly — you describe what you want and the LLM picks up that it should verify visually.

### Examples

**During planning / chat:**

> *"Look at the login page layout and suggest where to add the password-strength indicator."*

The LLM calls `verify_web_ui(url="http://localhost:3000/login", question="Where is there empty space near the password field that could fit a strength indicator?")` and uses the returned inventory to propose placement.

**During execution (from a Phase 5 verification step):**

> Plan step: *"After implementing the settings panel, call `verify_web_ui` with question='Does the new Dark Mode toggle appear in the Appearance section and is its label legible?' to confirm the layout."*

**In the fix loop (visual diagnosis):**

A UI test fails. The executor enters the fix loop, reads the failure, and calls `verify_web_ui` to check what the page actually looks like before deciding whether the test assertion is wrong or the rendering broke.

### Tool Parameters

**`verify_web_ui`**:

| Param | Required | Notes |
|---|---|---|
| `url` | yes | Supports `http://`, `https://`, `file://`, `data:` |
| `question` | yes | Specific question — drives the focused answer pass. Good prompts are narrow: *"Is the Save button visible and labeled correctly?"* rather than *"Describe this page"*. |
| `viewport` | no | `WxH` override, e.g. `"375x812"` for mobile. |
| `wait_for_selector` | no | CSS selector to wait for (up to 10s) before capture — useful for SPAs. |
| `wait_seconds` | no | Post-render settle time (default from config). |
| `full_page` | no | When `true`, capture the whole scrollable page, not just the viewport. |

**`verify_desktop_ui`**:

| Param | Required | Notes |
|---|---|---|
| `launch_command` | yes | **List of strings** (no shell interpolation). Example: `["python", "app.py"]` or `["java", "-jar", "build/app.jar"]`. |
| `window_title` | yes | Substring to match against window titles (case-insensitive). Ignored on Wayland. |
| `question` | yes | Same as `verify_web_ui.question`. |
| `wait_seconds` | no | Post-window-appearance settling time. |
| `window_timeout` | no | How long to wait for the window to appear (default 30s). |

The subprocess is **always** terminated after capture — don't use these tools to keep an app running.

## Output Format

Every tool call returns a markdown string with four parts:

```markdown
## Focused Answer
<direct answer to your question>

## Inventory
### Regions
- header (top-left,top-center,top-right)
- main-content (center)
- footer (bottom-center)
### Components
- `button` @ top-right "Save" [visible, conf=high]
- `input` @ center "Email" [visible, conf=medium]
...

## Visible Text (verbatim)
- [header] My App
- [main-content] Sign in to continue
...

## Sampled Colors (from pixel analysis)
- Background: `#FFFFFF`
- Palette (ranked): `#FFFFFF`, `#333333`, `#0066CC`, ...

## Screenshot
Saved to `/workspace/.lean_ai/ui_captures/web_xyz.png`

## Warnings
- <any pass failures or truncations>
```

Color hex codes come from Pillow + NumPy k-means on the actual pixels, **not the vision model** — hex codes a vision model guesses are unreliable.

## Troubleshooting

### "UI verification is disabled"

Set `LEAN_AI_ENABLE_UI_VERIFICATION=true` or toggle `lean-ai.enableUiVerification` in VS Code Settings, then restart the backend.

### "Pillow and numpy are not installed"

Run `pip install -e ".[dev,ui-verification]"` in `backend/` and restart the backend.

### "Chromium is not installed in .lean_ai/browsers"

Run **Lean AI: Install UI Verification (Chromium)** from the command palette. If the install fails, open a terminal in the workspace root and run:

```bash
PLAYWRIGHT_BROWSERS_PATH="$(pwd)/.lean_ai/browsers" python -m playwright install chromium
```

### "Playwright does not support chromium on ubuntu26.04-x64" (or similar)

Playwright ships browser binaries for a fixed list of OS/arch combinations. Very new distro releases (like Ubuntu 26.04 within a few days of its release) often aren't on the list yet. The install endpoint detects this and falls back to a system browser:

- If you see *"UI verification ready — will use a system-installed browser"* after the install, it worked via fallback. Nothing else to do.
- If you see the install failure with a list of `apt install` commands, install one of them and run the command again. On Ubuntu/Debian: `sudo apt install chromium-browser`.

Once a system browser is on `PATH`, `verify_web_ui` uses it via Playwright's `channel="chromium"` (or `channel="chrome"`). Watch Playwright's release notes — they usually catch up within a release or two.

### `verify_desktop_ui` on macOS returns empty capture

Screen Recording permission is missing. See [macOS setup](#macos). After granting, **fully restart VSCode** — permission changes don't take effect in already-running processes.

### `verify_desktop_ui` on Linux reports "No window matching ... appeared"

Check that the app subprocess is actually opening a window. If your launch command expects a display, verify `DISPLAY` (X11) or `WAYLAND_DISPLAY` is set in the environment the backend runs in. On Wayland, remember that `window_title` is ignored — the tool captures the full display.

### "verify_web_ui timed out after 180s"

Either the vision model is genuinely slow (e.g. cold-loading into VRAM) or the page is slow to render. Try:

- Raising `LEAN_AI_UI_VERIFICATION_TIMEOUT` to 300.
- Setting `LEAN_AI_UI_VERIFICATION_VISION_TIMEOUT` higher for the structured passes.
- Warming the vision model first: `ollama run qwen3.5:4b-q8_0 ""` before invoking the tool.

### Status check

The status endpoint summarises everything lean-ai can detect about your setup:

```bash
curl "http://localhost:8422/api/ui-verification/status?repo_root=/path/to/workspace"
```

Returns the platform, selected backend, Chromium install state, missing system deps, Wayland compositor (if applicable), and the reason analysis is unavailable (if it is).

## Uninstalling

```bash
# Remove Chromium (workspace-local)
rm -rf <workspace>/.lean_ai/browsers

# Disable the feature
# Either in VS Code Settings, or:
unset LEAN_AI_ENABLE_UI_VERIFICATION

# Optionally remove extras
pip uninstall playwright mss Pillow numpy
```

No global OS state is modified — uninstall is fully reversible.

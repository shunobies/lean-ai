"""Headless browser screenshot capture for ``verify_web_ui``.

Thin Playwright wrapper that:

- Isolates the Chromium install to ``<repo_root>/.lean_ai/browsers`` via
  ``PLAYWRIGHT_BROWSERS_PATH`` so the browser can be cleanly uninstalled
  by removing the workspace's ``.lean_ai`` directory.  A single system
  install of lean-ai serving multiple workspaces therefore maintains
  independent browser caches per workspace (~300MB each, disk-cheap,
  worth the isolation).
- Lazy-imports ``playwright`` so the module stays importable when the
  ``ui-verification`` extras group is not installed.
- Surfaces a clear, actionable error when Chromium isn't installed yet,
  telling the caller exactly what to run (or click in the extension
  panel) to fix it.

Callers must pass ``repo_root``; the browser path is deterministic from
there.  The returned PNG lives in ``<repo_root>/.lean_ai/ui_captures/``
so it's both cleanable and visible to the user if they want to inspect
what the analysis pipeline saw.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


# ── Browser isolation ───────────────────────────────────────────────────


def browsers_dir(repo_root: str) -> Path:
    """Return the workspace-local Playwright browsers directory."""
    return Path(repo_root) / ".lean_ai" / "browsers"


def captures_dir(repo_root: str) -> Path:
    """Return the workspace-local directory for captured screenshots."""
    return Path(repo_root) / ".lean_ai" / "ui_captures"


def is_playwright_installed() -> bool:
    """Check whether the ``playwright`` Python package is importable."""
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


def is_chromium_installed(repo_root: str) -> bool:
    """Check whether Chromium is installed in the workspace browser cache.

    Playwright creates subdirectories named like ``chromium-1234/`` under
    the browsers path when ``playwright install chromium`` runs.
    """
    d = browsers_dir(repo_root)
    if not d.is_dir():
        return False
    try:
        return any(
            p.is_dir() and p.name.startswith("chromium")
            for p in d.iterdir()
        )
    except OSError:
        return False


def _set_browsers_env(repo_root: str) -> Path:
    """Set ``PLAYWRIGHT_BROWSERS_PATH`` and return the directory."""
    d = browsers_dir(repo_root)
    d.mkdir(parents=True, exist_ok=True)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(d)
    return d


# ── Installation ────────────────────────────────────────────────────────


async def install_chromium(repo_root: str) -> tuple[bool, str]:
    """Run ``playwright install chromium`` into the workspace browser cache.

    Used by the REST install endpoint.  Uses the current Python interpreter
    so the call matches the Playwright Python package installed in the
    active environment.

    Returns:
        ``(success, combined_output)``.  ``success`` is False when the
        subprocess exits non-zero or Playwright isn't installed at all.
    """
    if not is_playwright_installed():
        return False, (
            "The playwright Python package is not installed. "
            'Run `pip install "lean-ai[ui-verification]"` first.'
        )

    browsers_path = _set_browsers_env(repo_root)
    import sys

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "playwright",
        "install",
        "chromium",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(browsers_path)},
    )
    assert proc.stdout is not None
    output_chunks: list[bytes] = []
    async for line in proc.stdout:
        output_chunks.append(line)
    returncode = await proc.wait()

    output = b"".join(output_chunks).decode("utf-8", errors="replace")
    logger.info("playwright install chromium exit=%d", returncode)
    return returncode == 0, output


# ── Capture ─────────────────────────────────────────────────────────────


class WebCaptureError(RuntimeError):
    """Raised when web capture fails in a way worth propagating verbatim."""


def _parse_viewport(viewport: str) -> tuple[int, int]:
    """Parse 'WxH' into (width, height).  Raises ValueError on malformed input."""
    try:
        parts = viewport.lower().replace(" ", "").split("x")
        if len(parts) != 2:
            raise ValueError
        w, h = int(parts[0]), int(parts[1])
        if w <= 0 or h <= 0:
            raise ValueError
    except ValueError as e:
        raise ValueError(
            f"Invalid viewport {viewport!r}, expected 'WxH' e.g. '1280x800'"
        ) from e
    return w, h


async def capture_web(
    url: str,
    *,
    repo_root: str,
    viewport: str = "1280x800",
    wait_for_selector: str | None = None,
    wait_seconds: float = 3.0,
    full_page: bool = False,
    navigation_timeout_ms: int = 30000,
) -> Path:
    """Capture a headless Chromium screenshot of a URL.

    Args:
        url: The URL to capture.  ``file://`` and ``data:`` URLs are supported.
        repo_root: Workspace root; the browser install and capture output
            live under ``<repo_root>/.lean_ai``.
        viewport: Viewport size as ``'WxH'``.  Default 1280x800.
        wait_for_selector: Optional CSS selector to wait for before capture,
            e.g. ``'[data-testid=login-form]'``.  10s timeout.
        wait_seconds: Post-render settling time before the screenshot
            fires.  Covers transitions, fonts loading, lazy content.
        full_page: When True, capture the entire scrollable page rather
            than just the viewport.
        navigation_timeout_ms: Navigation timeout for ``page.goto``.

    Returns:
        Absolute path to the captured PNG file.

    Raises:
        WebCaptureError: Playwright or Chromium not installed, or capture
            failed in a way the caller should surface.
        ValueError: Malformed viewport.
    """
    if not is_playwright_installed():
        raise WebCaptureError(
            "Playwright is not installed. "
            'Run `pip install "lean-ai[ui-verification]"` and then install '
            "Chromium via the extension's UI Verification panel."
        )

    _set_browsers_env(repo_root)
    if not is_chromium_installed(repo_root):
        raise WebCaptureError(
            f"Chromium is not installed in {browsers_dir(repo_root)}. "
            "Open the UI Verification panel in the extension and click "
            "Install, or run "
            f"`PLAYWRIGHT_BROWSERS_PATH={browsers_dir(repo_root)} "
            "python -m playwright install chromium`."
        )

    w, h = _parse_viewport(viewport)

    # Prepare output path under the workspace so the user can inspect captures.
    out_dir = captures_dir(repo_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    fd, png_path_str = tempfile.mkstemp(
        prefix="web_", suffix=".png", dir=str(out_dir),
    )
    os.close(fd)  # Playwright will write the file itself.
    png_path = Path(png_path_str)

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True)
        except Exception as e:
            png_path.unlink(missing_ok=True)
            msg = str(e).lower()
            if "executable doesn't exist" in msg or "browsers are not installed" in msg:
                raise WebCaptureError(
                    "Playwright launched but could not find Chromium. "
                    f"Browser cache at {browsers_dir(repo_root)} appears incomplete. "
                    "Re-run the install from the extension panel."
                ) from e
            raise WebCaptureError(f"Failed to launch Chromium: {e}") from e

        try:
            context = await browser.new_context(
                viewport={"width": w, "height": h},
                ignore_https_errors=True,
            )
            page = await context.new_page()

            try:
                await page.goto(
                    url,
                    timeout=navigation_timeout_ms,
                    wait_until="domcontentloaded",
                )
            except Exception as e:
                png_path.unlink(missing_ok=True)
                raise WebCaptureError(
                    f"Navigation to {url!r} failed: {e}"
                ) from e

            if wait_for_selector:
                try:
                    await page.wait_for_selector(
                        wait_for_selector, timeout=10000,
                    )
                except Exception as e:
                    png_path.unlink(missing_ok=True)
                    raise WebCaptureError(
                        f"Selector {wait_for_selector!r} not found within 10s: {e}"
                    ) from e

            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)

            await page.screenshot(path=str(png_path), full_page=full_page)

        finally:
            await browser.close()

    logger.info(
        "Captured web screenshot: url=%s viewport=%dx%d full_page=%s path=%s",
        url, w, h, full_page, png_path,
    )
    return png_path

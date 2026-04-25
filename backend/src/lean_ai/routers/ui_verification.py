"""REST endpoints for the UI verification feature.

Three endpoints:

- ``GET  /ui-verification/status``       — feature availability + diagnostics.
- ``POST /ui-verification/install``      — install the Chromium browser into
                                           the workspace-local browser cache.
- ``POST /ui-verification/test``         — one-shot capture of a supplied URL
                                           so the user can validate the full
                                           pipeline from the extension panel.

All three are safe when the optional ``ui-verification`` extras group is not
installed — the status endpoint reports what's missing, and the install/test
endpoints return actionable error messages rather than raising.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from lean_ai.config import settings
from lean_ai.tools.ui_analysis import availability_reason, is_analysis_available
from lean_ai.tools.ui_capture_desktop import (
    detect_backend,
    missing_system_deps,
    wayland_compositor,
)
from lean_ai.tools.ui_capture_web import (
    browsers_dir,
    detect_system_browser_channel,
    install_chromium,
    is_chromium_installed,
    is_playwright_installed,
)

logger = logging.getLogger(__name__)

ui_verification_router = APIRouter(
    prefix="/ui-verification",
    tags=["ui-verification"],
)


# ── Request / response models ──────────────────────────────────────────


class UIVerificationStatus(BaseModel):
    enabled: bool
    platform: str  # 'win32' | 'darwin' | 'linux-x11' | 'linux-wayland'
    vision_model_configured: bool
    analysis_available: bool
    analysis_reason: str | None
    playwright_installed: bool
    chromium_installed: bool
    chromium_path: str | None
    system_browser_channel: str | None  # "chrome" | "chromium" | None
    web_capture_available: bool  # True if either managed Chromium or system browser is usable
    desktop_backend: str
    missing_system_deps: list[str]
    macos_screen_recording_granted: bool | None
    wayland_compositor: str | None


class InstallChromiumRequest(BaseModel):
    repo_root: str


class InstallChromiumResponse(BaseModel):
    success: bool
    output: str


class TestCaptureRequest(BaseModel):
    repo_root: str
    url: str = "https://example.com"
    question: str = "Describe the layout and main elements visible on this page."


class TestCaptureResponse(BaseModel):
    success: bool
    screenshot_path: str | None = None
    report: str
    error: str | None = None


# ── Status ─────────────────────────────────────────────────────────────


def _platform_label() -> str:
    if sys.platform == "win32":
        return "win32"
    if sys.platform == "darwin":
        return "darwin"
    # Linux — distinguish Wayland from X11
    import os

    if os.environ.get("WAYLAND_DISPLAY"):
        return "linux-wayland"
    return "linux-x11"


def _chromium_path(repo_root: str) -> str | None:
    """Return the first chromium-* subdirectory path, or None."""
    d = browsers_dir(repo_root)
    if not d.is_dir():
        return None
    try:
        for sub in d.iterdir():
            if sub.is_dir() and sub.name.startswith("chromium"):
                return str(sub)
    except OSError:
        return None
    return None


@ui_verification_router.get("/status")
async def get_status(repo_root: str = "") -> UIVerificationStatus:
    """Feature availability + platform diagnostics.

    ``repo_root`` is a query parameter so the extension can ask about a
    specific workspace without committing to a single global path.  When
    empty, Chromium-path checks are skipped.
    """
    chromium_installed = False
    chromium_path: str | None = None
    if repo_root:
        chromium_installed = is_chromium_installed(repo_root)
        chromium_path = _chromium_path(repo_root)

    system_channel = detect_system_browser_channel()
    web_capture_available = is_playwright_installed() and (
        chromium_installed or system_channel is not None
    )

    return UIVerificationStatus(
        enabled=settings.enable_ui_verification,
        platform=_platform_label(),
        vision_model_configured=bool(settings.vision_model),
        analysis_available=is_analysis_available(),
        analysis_reason=availability_reason(),
        playwright_installed=is_playwright_installed(),
        chromium_installed=chromium_installed,
        chromium_path=chromium_path,
        system_browser_channel=system_channel,
        web_capture_available=web_capture_available,
        desktop_backend=detect_backend(),
        missing_system_deps=missing_system_deps(),
        macos_screen_recording_granted=None,  # not programmatically checkable
        wayland_compositor=wayland_compositor(),
    )


# ── Install ────────────────────────────────────────────────────────────


@ui_verification_router.post("/install")
async def post_install(request: InstallChromiumRequest) -> InstallChromiumResponse:
    """Run ``python -m playwright install chromium`` into the workspace browser cache.

    Blocking — the extension should set a long client-side timeout
    (~3 minutes).  Chromium is ~300MB on first install.
    """
    if not request.repo_root:
        raise HTTPException(400, "repo_root is required")

    repo_path = Path(request.repo_root)
    if not repo_path.is_dir():
        raise HTTPException(
            400,
            f"repo_root does not exist or is not a directory: {request.repo_root}",
        )

    if not is_playwright_installed():
        return InstallChromiumResponse(
            success=False,
            output=(
                "The playwright Python package is not installed.  "
                'Run `pip install "lean-ai[ui-verification]"` first, then '
                "try Install again."
            ),
        )

    logger.info("Installing Chromium to %s", browsers_dir(request.repo_root))
    success, output = await install_chromium(request.repo_root)
    return InstallChromiumResponse(success=success, output=output)


# ── Test capture (smoke-test the whole pipeline) ───────────────────────


@ui_verification_router.post("/test")
async def post_test(request: TestCaptureRequest) -> TestCaptureResponse:
    """One-shot capture + analysis so the user can sanity-check the flow.

    The extension panel calls this after an install completes to confirm
    the whole pipeline works before the tools are used in anger.
    """
    if not request.repo_root:
        raise HTTPException(400, "repo_root is required")

    if not settings.enable_ui_verification:
        return TestCaptureResponse(
            success=False,
            report="",
            error=(
                "UI verification is disabled. Set "
                "LEAN_AI_ENABLE_UI_VERIFICATION=true or toggle it in the "
                "extension panel, then retry."
            ),
        )

    reason = availability_reason()
    if reason is not None:
        return TestCaptureResponse(success=False, report="", error=reason)

    from lean_ai.tools.ui_verification import verify_web_ui

    try:
        report = await verify_web_ui(
            url=request.url,
            question=request.question,
            repo_root=request.repo_root,
        )
    except Exception as e:
        logger.exception("Test capture raised unexpectedly")
        return TestCaptureResponse(success=False, report="", error=str(e))

    # Error outputs from the tool handler are prefixed "ERROR:" — surface
    # as error + success=False so the extension can render them clearly.
    if report.startswith("ERROR:"):
        return TestCaptureResponse(
            success=False,
            report="",
            error=report.removeprefix("ERROR:").strip(),
        )

    # Extract the captured screenshot path from the "Saved to `...`" line so
    # the extension can offer a preview link.
    screenshot_path: str | None = None
    for line in report.splitlines():
        if line.startswith("Saved to `") and line.endswith("`"):
            screenshot_path = line[len("Saved to `") : -1]
            break

    return TestCaptureResponse(
        success=True,
        screenshot_path=screenshot_path,
        report=report,
    )

"""Top-level ``verify_web_ui`` and ``verify_desktop_ui`` tool handlers.

Each handler wires three pieces together:

1. Capture (``ui_capture_web.capture_web`` or ``ui_capture_desktop.capture_desktop``).
2. Analysis (``ui_analysis.analyze_screenshot`` — the 4-pass vision pipeline).
3. Result formatting — a single markdown string the calling LLM can read.

The whole flow runs under ``settings.ui_verification_timeout`` so a slow
vision model or stuck browser never hangs the workflow.  On timeout or
error, the handler returns a string prefixed ``ERROR:`` so the dispatch
layer surfaces it consistently with other tools.

These helpers are imported by ``workflow/tool_executor.py``'s dispatch
chain.  They don't need to know about the WebSocket or any workflow
state — they just take primitives in and return a formatted string.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from lean_ai.config import settings
from lean_ai.tools.ui_analysis import (
    UIAnalysis,
    analyze_screenshot,
    availability_reason,
)
from lean_ai.tools.ui_capture_desktop import (
    DesktopCaptureError,
    capture_desktop,
)
from lean_ai.tools.ui_capture_web import WebCaptureError, capture_web

logger = logging.getLogger(__name__)


# ── Result formatting ──────────────────────────────────────────────────


def _format_analysis(analysis: UIAnalysis, screenshot_path: Path) -> str:
    """Render a ``UIAnalysis`` as a markdown block for consumption by an LLM.

    Structure: focused answer first (that's what the caller asked), then
    supporting evidence (inventory, text, colors), then warnings.
    """
    lines: list[str] = []

    if analysis.answer.strip():
        lines.append("## Focused Answer")
        lines.append(analysis.answer.strip())
        lines.append("")

    inv = analysis.inventory
    if inv.regions or inv.components:
        lines.append("## Inventory")
        if inv.regions:
            lines.append("### Regions")
            for r in inv.regions:
                loc = f" ({r.grid_cell})" if r.grid_cell else ""
                note = f" — {r.notes}" if r.notes else ""
                lines.append(f"- **{r.name}**{loc}{note}")
        if inv.components:
            lines.append("### Components")
            for c in inv.components:
                label = f" \"{c.label_text}\"" if c.label_text else ""
                style = f" — {c.styling_notes}" if c.styling_notes else ""
                lines.append(
                    f"- `{c.type}` @ {c.location}{label} "
                    f"[{c.state}, conf={c.confidence}]{style}"
                )
        lines.append("")

    if analysis.text.lines:
        lines.append("## Visible Text (verbatim)")
        for ln in analysis.text.lines:
            region = f"[{ln.region}] " if ln.region else ""
            conf = "" if ln.confidence == "high" else f" ({ln.confidence} confidence)"
            lines.append(f"- {region}{ln.verbatim}{conf}")
        lines.append("")

    if analysis.colors:
        lines.append("## Sampled Colors (from pixel analysis)")
        bg = analysis.colors.get("background_guess")
        palette = analysis.colors.get("palette") or []
        if bg:
            lines.append(f"- Background: `{bg}`")
        if palette:
            lines.append(f"- Palette (ranked): {', '.join(f'`{c}`' for c in palette)}")
        lines.append("")

    lines.append(f"## Screenshot\nSaved to `{screenshot_path}`")

    if analysis.warnings:
        lines.append("")
        lines.append("## Warnings")
        for w in analysis.warnings:
            lines.append(f"- {w}")

    return "\n".join(lines)


def _format_disabled_error() -> str:
    reason = availability_reason()
    if reason is None:
        return (
            "ERROR: UI verification is unavailable for an unknown reason. "
            "Check the /api/ui-verification/status endpoint."
        )
    return f"ERROR: {reason}"


# ── Public handlers (called from the tool dispatch chain) ──────────────


async def verify_web_ui(
    *,
    url: str,
    question: str,
    repo_root: str,
    viewport: str | None = None,
    wait_for_selector: str | None = None,
    wait_seconds: float | None = None,
    full_page: bool = False,
) -> str:
    """Capture a URL + run the vision pipeline + format a markdown report."""
    if not settings.enable_ui_verification:
        return _format_disabled_error()

    effective_viewport = viewport or settings.ui_verification_viewport
    effective_wait = (
        wait_seconds
        if wait_seconds is not None
        else settings.ui_verification_wait_seconds
    )

    async def _run() -> str:
        try:
            png = await capture_web(
                url,
                repo_root=repo_root,
                viewport=effective_viewport,
                wait_for_selector=wait_for_selector,
                wait_seconds=effective_wait,
                full_page=full_page,
            )
        except (WebCaptureError, ValueError) as e:
            return f"ERROR: Web capture failed: {e}"

        analysis = await analyze_screenshot(
            png, question, vision_timeout=settings.ui_verification_vision_timeout,
        )
        return _format_analysis(analysis, png)

    try:
        return await asyncio.wait_for(
            _run(), timeout=settings.ui_verification_timeout,
        )
    except asyncio.TimeoutError:
        return (
            f"ERROR: verify_web_ui timed out after "
            f"{settings.ui_verification_timeout:.0f}s. "
            "Consider increasing LEAN_AI_UI_VERIFICATION_TIMEOUT or splitting "
            "the work into smaller captures."
        )


async def verify_desktop_ui(
    *,
    launch_command: list[str],
    window_title: str,
    question: str,
    repo_root: str,
    wait_seconds: float | None = None,
    window_timeout: float | None = None,
) -> str:
    """Launch + capture a desktop window + run the vision pipeline."""
    if not settings.enable_ui_verification:
        return _format_disabled_error()

    effective_wait = (
        wait_seconds
        if wait_seconds is not None
        else settings.ui_verification_wait_seconds
    )
    effective_window_timeout = window_timeout if window_timeout is not None else 30.0

    async def _run() -> str:
        try:
            png = await capture_desktop(
                launch_command,
                window_title,
                repo_root=repo_root,
                wait_seconds=effective_wait,
                window_timeout=effective_window_timeout,
            )
        except DesktopCaptureError as e:
            return f"ERROR: Desktop capture failed: {e}"

        analysis = await analyze_screenshot(
            png, question, vision_timeout=settings.ui_verification_vision_timeout,
        )
        return _format_analysis(analysis, png)

    try:
        return await asyncio.wait_for(
            _run(), timeout=settings.ui_verification_timeout,
        )
    except asyncio.TimeoutError:
        return (
            f"ERROR: verify_desktop_ui timed out after "
            f"{settings.ui_verification_timeout:.0f}s. "
            "Consider increasing LEAN_AI_UI_VERIFICATION_TIMEOUT."
        )

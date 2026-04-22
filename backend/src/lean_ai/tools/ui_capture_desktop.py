"""Desktop GUI screenshot capture for ``verify_desktop_ui``.

Four platform adapters share a common scaffold:

1. Launch the app as a subprocess (``shell=False`` — ``launch_command`` is a list).
2. Poll for a window matching ``window_title`` (substring match, case-insensitive).
3. Capture just that window's region.
4. Terminate the subprocess in ``finally`` (and any children it spawned).

Platform-specific pieces:

- **Windows** — window lookup via ``pygetwindow``; capture via ``mss`` region grab.
- **macOS** — window lookup via Quartz's ``CGWindowListCopyWindowInfo``; capture
  via the ``screencapture`` CLI (``-l <window_id>``, captures even occluded
  windows).  The caller's host process (VSCode, a terminal, etc.) must have
  Screen Recording permission granted — first-run failures are detected and
  surface a clear permission-error message.
- **Linux X11** — window lookup via ``wmctrl -l``; geometry via
  ``xdotool getwindowgeometry --shell``; capture via ``mss``.  Requires both
  ``wmctrl`` and ``xdotool`` as system packages.
- **Linux Wayland** — captured full-screen via ``grim``.  Window-by-title
  lookup is not generally recoverable on Wayland without compositor-specific
  APIs; the ``window_title`` argument is accepted for API parity but logged
  as ignored.  The XDG Desktop Portal path is a documented follow-up.

Auto-detection picks the backend from ``sys.platform`` plus ``WAYLAND_DISPLAY``;
``settings.ui_verification_capture_backend_override`` forces a specific one
for debugging / unusual environments.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from lean_ai.config import settings

logger = logging.getLogger(__name__)


# ── Errors ─────────────────────────────────────────────────────────────


class DesktopCaptureError(RuntimeError):
    """Raised when desktop capture fails in a way worth surfacing verbatim."""


# ── Backend detection ──────────────────────────────────────────────────


def detect_backend() -> str:
    """Return the backend identifier to use on this host.

    Order: explicit override -> sys.platform match -> Wayland detection.
    """
    override = settings.ui_verification_capture_backend_override.strip()
    if override:
        return override

    if sys.platform == "win32":
        return "mss-win32"
    if sys.platform == "darwin":
        return "mac-screencapture"
    # Linux: distinguish Wayland from X11 via env var.
    if os.environ.get("WAYLAND_DISPLAY"):
        return "grim"
    return "mss-x11"


def wayland_compositor() -> str | None:
    """Best-effort compositor name detection.  Used by the status endpoint."""
    if not os.environ.get("WAYLAND_DISPLAY"):
        return None
    xdg = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if "gnome" in xdg:
        return "gnome"
    if "kde" in xdg or "plasma" in xdg:
        return "kde"
    if "sway" in xdg:
        return "sway"
    if "hyprland" in xdg:
        return "hyprland"
    return "unknown"


def missing_system_deps() -> list[str]:
    """Return the list of system packages the current backend needs but
    cannot find on PATH.  Used by ``/api/ui-verification/status``."""
    backend = detect_backend()
    required: list[str] = []
    if backend == "mss-x11":
        required = ["wmctrl", "xdotool"]
    elif backend == "grim":
        required = ["grim"]
    elif backend == "mac-screencapture":
        required = ["screencapture"]  # should always be present on macOS
    return [cmd for cmd in required if not shutil.which(cmd)]


# ── Shared scaffolding ─────────────────────────────────────────────────


def _captures_dir(repo_root: str) -> Path:
    return Path(repo_root) / ".lean_ai" / "ui_captures"


def _new_capture_path(repo_root: str) -> Path:
    out_dir = _captures_dir(repo_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="desktop_", suffix=".png", dir=str(out_dir))
    os.close(fd)
    return Path(path)


def _validate_launch_command(launch_command: Any) -> list[str]:
    """Enforce list-only launch commands.  Rejects strings to avoid shell
    injection and surprises.  Returns the validated list."""
    if not isinstance(launch_command, list) or not launch_command:
        raise DesktopCaptureError(
            "launch_command must be a non-empty list of strings "
            "(no shell interpolation)"
        )
    if not all(isinstance(arg, str) for arg in launch_command):
        raise DesktopCaptureError(
            "launch_command entries must all be strings"
        )
    return launch_command


async def _launch_subprocess(
    launch_command: list[str],
) -> asyncio.subprocess.Process:
    """Launch the app as a new process group / session so termination
    kills children too."""
    kwargs: dict[str, Any] = {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    if sys.platform == "win32":
        # Create a new process group so we can send CTRL_BREAK to all descendants.
        import subprocess as _sp
        kwargs["creationflags"] = _sp.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        kwargs["start_new_session"] = True

    try:
        return await asyncio.create_subprocess_exec(*launch_command, **kwargs)
    except FileNotFoundError as e:
        raise DesktopCaptureError(
            f"launch_command executable not found: {launch_command[0]!r}"
        ) from e


async def _terminate_subprocess(proc: asyncio.subprocess.Process) -> None:
    """Kill the subprocess and its process group.  Never raises."""
    if proc.returncode is not None:
        return

    try:
        if sys.platform == "win32":
            proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                proc.terminate()

        try:
            await asyncio.wait_for(proc.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            if sys.platform == "win32":
                proc.kill()
            else:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning("Subprocess %d did not exit after SIGKILL", proc.pid)
    except Exception:
        logger.exception("Error terminating subprocess %d", proc.pid)


async def _check_subprocess_alive(proc: asyncio.subprocess.Process) -> None:
    """Raise ``DesktopCaptureError`` if the subprocess has exited.  Used
    during window polling so fast failures surface with helpful stderr."""
    if proc.returncode is None:
        return
    stderr_bytes = b""
    if proc.stderr is not None:
        try:
            stderr_bytes = await asyncio.wait_for(proc.stderr.read(), timeout=1.0)
        except asyncio.TimeoutError:
            pass
    stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
    raise DesktopCaptureError(
        f"App subprocess exited with code {proc.returncode} before its window appeared. "
        + (f"stderr (truncated): {stderr[:500]}" if stderr else "No stderr output.")
    )


def _mss_capture_region(region: dict[str, int], output_path: Path) -> None:
    """Capture a screen region (left/top/width/height) via mss + Pillow.

    Runs synchronously; call via ``asyncio.to_thread``.
    """
    import mss
    from PIL import Image

    with mss.mss() as sct:
        raw = sct.grab(region)
        img = Image.frombytes("RGB", raw.size, raw.rgb)
        img.save(str(output_path), "PNG")


# ── Windows adapter ────────────────────────────────────────────────────


async def _capture_windows(
    launch_command: list[str],
    window_title: str,
    repo_root: str,
    wait_seconds: float,
    window_timeout: float,
) -> Path:
    try:
        import pygetwindow  # noqa: F401
    except ImportError as e:
        raise DesktopCaptureError(
            "pygetwindow is not installed. "
            'Run `pip install "lean-ai[ui-verification]"`'
        ) from e
    try:
        import mss  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError as e:
        raise DesktopCaptureError(
            "mss and Pillow are required. "
            'Run `pip install "lean-ai[ui-verification]"`'
        ) from e

    proc = await _launch_subprocess(launch_command)
    try:
        window = await _wait_for_window_pygetwindow(window_title, window_timeout, proc)

        # Best-effort: bring the window to the foreground before capture.
        try:
            window.activate()
            await asyncio.sleep(0.3)
        except Exception:
            logger.debug("Could not activate window %r", window.title, exc_info=True)

        await asyncio.sleep(wait_seconds)

        region = {
            "left": int(window.left),
            "top": int(window.top),
            "width": int(window.width),
            "height": int(window.height),
        }
        if region["width"] <= 0 or region["height"] <= 0:
            raise DesktopCaptureError(
                f"Window {window.title!r} has non-positive dimensions: {region}"
            )

        out_path = _new_capture_path(repo_root)
        await asyncio.to_thread(_mss_capture_region, region, out_path)
        logger.info(
            "Captured Windows window %r (%dx%d) to %s",
            window.title, region["width"], region["height"], out_path,
        )
        return out_path
    finally:
        await _terminate_subprocess(proc)


async def _wait_for_window_pygetwindow(
    title: str,
    timeout: float,
    proc: asyncio.subprocess.Process,
) -> Any:
    import pygetwindow as gw

    deadline = time.monotonic() + timeout
    title_lower = title.lower()
    while time.monotonic() < deadline:
        await _check_subprocess_alive(proc)
        try:
            windows = gw.getWindowsWithTitle("")  # all titled windows
        except Exception:
            windows = []
        matches = [w for w in windows if w.title and title_lower in w.title.lower()]
        if matches:
            if len(matches) > 1:
                logger.warning(
                    "Multiple windows match %r, using first: %s",
                    title, [w.title for w in matches[:5]],
                )
            return matches[0]
        await asyncio.sleep(0.5)

    raise DesktopCaptureError(
        f"No window matching {title!r} appeared within {timeout:.0f}s. "
        f"Check that the app actually opens a window with that title (substring match)."
    )


# ── macOS adapter ──────────────────────────────────────────────────────


async def _capture_macos(
    launch_command: list[str],
    window_title: str,
    repo_root: str,
    wait_seconds: float,
    window_timeout: float,
) -> Path:
    try:
        from Quartz import (  # noqa: F401
            CGWindowListCopyWindowInfo,
            kCGNullWindowID,
            kCGWindowListOptionOnScreenOnly,
        )
    except ImportError as e:
        raise DesktopCaptureError(
            "pyobjc-framework-Quartz is not installed. "
            'Run `pip install "lean-ai[ui-verification]"`'
        ) from e

    proc = await _launch_subprocess(launch_command)
    try:
        window_id = await _wait_for_window_quartz(window_title, window_timeout, proc)
        await asyncio.sleep(wait_seconds)

        out_path = _new_capture_path(repo_root)
        cap_proc = await asyncio.create_subprocess_exec(
            "screencapture",
            "-x",  # no sound
            "-o",  # don't capture shadow
            "-l",
            str(window_id),
            str(out_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_bytes = await cap_proc.communicate()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

        if cap_proc.returncode != 0:
            out_path.unlink(missing_ok=True)
            raise DesktopCaptureError(
                f"screencapture failed (exit {cap_proc.returncode}): "
                f"{stderr[:500] or '<no stderr>'}"
            )

        # Permission denial pattern: screencapture exits 0 but emits an
        # empty / tiny PNG.  A valid capture is always well over 1KB.
        if not out_path.exists() or out_path.stat().st_size < 1024:
            out_path.unlink(missing_ok=True)
            raise DesktopCaptureError(
                "screencapture produced no valid output.  This usually means "
                "Screen Recording permission is not granted to the host process. "
                "Open System Settings -> Privacy & Security -> Screen Recording "
                "and enable the toggle for VSCode (or the process running the "
                "lean-ai backend), then restart it."
            )

        logger.info("Captured macOS window id=%s to %s", window_id, out_path)
        return out_path
    finally:
        await _terminate_subprocess(proc)


async def _wait_for_window_quartz(
    title: str,
    timeout: float,
    proc: asyncio.subprocess.Process,
) -> int:
    from Quartz import (
        CGWindowListCopyWindowInfo,
        kCGNullWindowID,
        kCGWindowListOptionOnScreenOnly,
    )

    deadline = time.monotonic() + timeout
    title_lower = title.lower()
    while time.monotonic() < deadline:
        await _check_subprocess_alive(proc)
        windows = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly, kCGNullWindowID
        ) or []
        matches: list[tuple[int, str]] = []
        for w in windows:
            wtitle = w.get("kCGWindowName") or ""
            if wtitle and title_lower in wtitle.lower():
                matches.append((int(w["kCGWindowNumber"]), wtitle))
        if matches:
            if len(matches) > 1:
                logger.warning(
                    "Multiple macOS windows match %r, using first: %s",
                    title, [t for _, t in matches[:5]],
                )
            return matches[0][0]
        await asyncio.sleep(0.5)

    raise DesktopCaptureError(
        f"No window matching {title!r} appeared within {timeout:.0f}s"
    )


# ── Linux X11 adapter ──────────────────────────────────────────────────


async def _capture_x11(
    launch_command: list[str],
    window_title: str,
    repo_root: str,
    wait_seconds: float,
    window_timeout: float,
) -> Path:
    missing = [cmd for cmd in ("wmctrl", "xdotool") if not shutil.which(cmd)]
    if missing:
        raise DesktopCaptureError(
            f"Missing X11 tools: {', '.join(missing)}.  On Debian/Ubuntu: "
            f"`sudo apt install {' '.join(missing)}`.  On Fedora: "
            f"`sudo dnf install {' '.join(missing)}`.  On Arch: "
            f"`sudo pacman -S {' '.join(missing)}`."
        )
    try:
        import mss  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError as e:
        raise DesktopCaptureError(
            "mss and Pillow are required. "
            'Run `pip install "lean-ai[ui-verification]"`'
        ) from e

    proc = await _launch_subprocess(launch_command)
    try:
        window_id = await _wait_for_window_wmctrl(window_title, window_timeout, proc)
        region = await _xdotool_geometry(window_id)
        await asyncio.sleep(wait_seconds)

        out_path = _new_capture_path(repo_root)
        await asyncio.to_thread(_mss_capture_region, region, out_path)
        logger.info(
            "Captured X11 window id=%s (%dx%d) to %s",
            window_id, region["width"], region["height"], out_path,
        )
        return out_path
    finally:
        await _terminate_subprocess(proc)


async def _wait_for_window_wmctrl(
    title: str,
    timeout: float,
    proc: asyncio.subprocess.Process,
) -> str:
    deadline = time.monotonic() + timeout
    title_lower = title.lower()
    while time.monotonic() < deadline:
        await _check_subprocess_alive(proc)

        wmctrl = await asyncio.create_subprocess_exec(
            "wmctrl", "-l",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, _ = await wmctrl.communicate()
        matches: list[tuple[str, str]] = []
        for line in stdout_bytes.decode("utf-8", errors="replace").splitlines():
            # Format: "0x02000003  0 hostname App Window Title"
            parts = line.split(None, 3)
            if len(parts) >= 4:
                wid = parts[0]
                wtitle = parts[3]
                if title_lower in wtitle.lower():
                    matches.append((wid, wtitle))
        if matches:
            if len(matches) > 1:
                logger.warning(
                    "Multiple X11 windows match %r, using first: %s",
                    title, [t for _, t in matches[:5]],
                )
            return matches[0][0]
        await asyncio.sleep(0.5)

    raise DesktopCaptureError(
        f"No X11 window matching {title!r} appeared within {timeout:.0f}s"
    )


async def _xdotool_geometry(window_id: str) -> dict[str, int]:
    xdo = await asyncio.create_subprocess_exec(
        "xdotool", "getwindowgeometry", "--shell", window_id,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await xdo.communicate()
    if xdo.returncode != 0:
        raise DesktopCaptureError(
            f"xdotool getwindowgeometry failed (exit {xdo.returncode}): "
            f"{stderr_bytes.decode('utf-8', errors='replace')[:300]}"
        )

    parsed: dict[str, int] = {}
    for line in stdout_bytes.decode("utf-8", errors="replace").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            try:
                parsed[k.strip()] = int(v.strip())
            except ValueError:
                continue

    for required in ("X", "Y", "WIDTH", "HEIGHT"):
        if required not in parsed:
            raise DesktopCaptureError(
                f"xdotool output missing {required}: {stdout_bytes.decode()[:200]}"
            )
    return {
        "left": parsed["X"],
        "top": parsed["Y"],
        "width": parsed["WIDTH"],
        "height": parsed["HEIGHT"],
    }


# ── Linux Wayland adapter (grim) ───────────────────────────────────────


async def _capture_wayland_grim(
    launch_command: list[str],
    window_title: str,
    repo_root: str,
    wait_seconds: float,
    window_timeout: float,
) -> Path:
    """Wayland capture via ``grim``.

    Window-by-title lookup is not generally recoverable on Wayland without
    compositor-specific APIs (Sway has ``swaymsg``, GNOME uses dbus, etc.),
    so this captures the full display.  ``window_title`` is logged and
    accepted for API parity but does not constrain the capture.
    """
    if not shutil.which("grim"):
        raise DesktopCaptureError(
            "grim is not installed. On Debian/Ubuntu: `sudo apt install grim`.  "
            "On Fedora: `sudo dnf install grim`.  On Arch: `sudo pacman -S grim`."
        )

    proc = await _launch_subprocess(launch_command)
    try:
        # No programmatic window-wait on Wayland — give the app at least
        # window_timeout's worth of breathing room before we snap.  Keep the
        # subprocess-alive check during the wait so we surface fast failures.
        total_wait = max(wait_seconds, min(window_timeout, 10.0))
        waited = 0.0
        step = 0.5
        while waited < total_wait:
            await _check_subprocess_alive(proc)
            await asyncio.sleep(step)
            waited += step

        if window_title:
            logger.info(
                "Wayland: capturing full screen (window title %r not enforceable "
                "via grim; XDG Portal path would be needed per-compositor).",
                window_title,
            )

        out_path = _new_capture_path(repo_root)
        grim = await asyncio.create_subprocess_exec(
            "grim", str(out_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_bytes = await grim.communicate()
        if grim.returncode != 0:
            out_path.unlink(missing_ok=True)
            raise DesktopCaptureError(
                f"grim failed (exit {grim.returncode}): "
                f"{stderr_bytes.decode('utf-8', errors='replace')[:300]}"
            )

        logger.info("Captured Wayland full screen to %s", out_path)
        return out_path
    finally:
        await _terminate_subprocess(proc)


# ── Public entry point ─────────────────────────────────────────────────


_BACKENDS = {
    "mss-win32": _capture_windows,
    "mac-screencapture": _capture_macos,
    "mss-x11": _capture_x11,
    "grim": _capture_wayland_grim,
}


async def capture_desktop(
    launch_command: list[str],
    window_title: str,
    *,
    repo_root: str,
    wait_seconds: float = 3.0,
    window_timeout: float = 30.0,
) -> Path:
    """Launch an app, find its window, capture it, terminate the app.

    Args:
        launch_command: Argument list (not a shell string).  First element
            is the executable; remainder are args.
        window_title: Substring to match against window titles
            (case-insensitive).  On Wayland, accepted but logged as ignored
            since compositor APIs aren't uniformly exposed for lookup.
        repo_root: Workspace root; captures are written to
            ``<repo_root>/.lean_ai/ui_captures/``.
        wait_seconds: Post-window-appearance settling time before capture.
        window_timeout: How long to wait for the window to appear.

    Returns:
        Absolute path to the captured PNG.

    Raises:
        DesktopCaptureError: Missing deps, window not found in time,
            capture failed, or (macOS) Screen Recording permission denied.
    """
    launch_command = _validate_launch_command(launch_command)
    backend = detect_backend()
    impl = _BACKENDS.get(backend)
    if impl is None:
        raise DesktopCaptureError(
            f"Unknown desktop capture backend: {backend!r}.  "
            f"Known: {sorted(_BACKENDS)}"
        )
    return await impl(
        launch_command, window_title, repo_root, wait_seconds, window_timeout,
    )

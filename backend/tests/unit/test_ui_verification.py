"""Unit tests for the UI verification feature.

Focuses on the pure-Python logic that doesn't require the optional
`ui-verification` extras (Playwright, mss, Pillow, numpy, platform-
specific window libs) or a running vision model.  Things that *do*
require those live in integration tests gated on
``LEAN_AI_UI_VERIFICATION_LIVE=1``.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from lean_ai.config import settings

# ── ui_capture_web ─────────────────────────────────────────────────────


def test_parse_viewport_normal():
    from lean_ai.tools.ui_capture_web import _parse_viewport

    assert _parse_viewport("1280x800") == (1280, 800)
    assert _parse_viewport("1920x1080") == (1920, 1080)


def test_parse_viewport_spaces():
    from lean_ai.tools.ui_capture_web import _parse_viewport

    assert _parse_viewport("1280 x 800") == (1280, 800)


def test_parse_viewport_case_insensitive():
    from lean_ai.tools.ui_capture_web import _parse_viewport

    assert _parse_viewport("1280X800") == (1280, 800)


@pytest.mark.parametrize("bad", ["bogus", "1280", "1280x", "0x800", "-10x800", "1280x-10"])
def test_parse_viewport_rejects_malformed(bad: str):
    from lean_ai.tools.ui_capture_web import _parse_viewport

    with pytest.raises(ValueError, match="Invalid viewport"):
        _parse_viewport(bad)


def test_browsers_dir_is_workspace_local(tmp_path: Path):
    from lean_ai.tools.ui_capture_web import browsers_dir

    d = browsers_dir(str(tmp_path))
    assert d == tmp_path / ".lean_ai" / "browsers"


def test_captures_dir_is_workspace_local(tmp_path: Path):
    from lean_ai.tools.ui_capture_web import captures_dir

    d = captures_dir(str(tmp_path))
    assert d == tmp_path / ".lean_ai" / "ui_captures"


def test_is_chromium_installed_false_when_dir_absent(tmp_path: Path):
    from lean_ai.tools.ui_capture_web import is_chromium_installed

    # Empty workspace has no .lean_ai/browsers yet
    assert is_chromium_installed(str(tmp_path)) is False


def test_is_chromium_installed_true_when_chromium_subdir_exists(tmp_path: Path):
    from lean_ai.tools.ui_capture_web import browsers_dir, is_chromium_installed

    d = browsers_dir(str(tmp_path))
    (d / "chromium-1234").mkdir(parents=True)
    assert is_chromium_installed(str(tmp_path)) is True


def test_is_chromium_installed_false_when_only_unrelated_dirs(tmp_path: Path):
    from lean_ai.tools.ui_capture_web import browsers_dir, is_chromium_installed

    d = browsers_dir(str(tmp_path))
    (d / "firefox-1234").mkdir(parents=True)  # Other browsers don't count
    assert is_chromium_installed(str(tmp_path)) is False


# ── System browser channel detection ──


def test_detect_system_browser_channel_chromium_found(monkeypatch):
    """Linux with chromium on PATH → channel='chromium'."""
    from lean_ai.tools import ui_capture_web as mod

    monkeypatch.setattr(mod.sys, "platform", "linux")
    # First two calls (chromium, chromium-browser) hit, Chrome lookups skipped
    calls = iter(["/usr/bin/chromium", None, None, None])
    monkeypatch.setattr(mod.shutil, "which", lambda _cmd: next(calls))
    assert mod.detect_system_browser_channel() == "chromium"


def test_detect_system_browser_channel_chrome_fallback(monkeypatch):
    """Linux with only chrome on PATH → channel='chrome'."""
    from lean_ai.tools import ui_capture_web as mod

    monkeypatch.setattr(mod.sys, "platform", "linux")
    # chromium probes return None; chrome probe hits
    def fake_which(cmd):
        return "/usr/bin/google-chrome" if "chrome" in cmd else None
    monkeypatch.setattr(mod.shutil, "which", fake_which)
    assert mod.detect_system_browser_channel() == "chrome"


def test_detect_system_browser_channel_none_on_bare_linux(monkeypatch):
    """Linux with no browsers on PATH → None (neither managed nor system)."""
    from lean_ai.tools import ui_capture_web as mod

    monkeypatch.setattr(mod.sys, "platform", "linux")
    monkeypatch.setattr(mod.shutil, "which", lambda _cmd: None)
    assert mod.detect_system_browser_channel() is None


def test_detect_system_browser_channel_windows_returns_chrome(monkeypatch):
    """Windows: defer to Playwright's registry lookup — return 'chrome'."""
    from lean_ai.tools import ui_capture_web as mod

    monkeypatch.setattr(mod.sys, "platform", "win32")
    # shutil.which isn't consulted — Windows path returns unconditionally
    assert mod.detect_system_browser_channel() == "chrome"


def test_detect_system_browser_channel_macos_returns_chrome(monkeypatch):
    """macOS: return 'chrome' unconditionally — Playwright checks /Applications."""
    from lean_ai.tools import ui_capture_web as mod

    monkeypatch.setattr(mod.sys, "platform", "darwin")
    assert mod.detect_system_browser_channel() == "chrome"


# ── ui_capture_desktop ─────────────────────────────────────────────────


def test_validate_launch_command_accepts_valid_list():
    from lean_ai.tools.ui_capture_desktop import _validate_launch_command

    assert _validate_launch_command(["python", "app.py"]) == ["python", "app.py"]
    assert _validate_launch_command(["java", "-jar", "app.jar"]) == [
        "java", "-jar", "app.jar",
    ]


def test_validate_launch_command_rejects_string():
    """Strings imply shell interpolation — reject to avoid injection."""
    from lean_ai.tools.ui_capture_desktop import (
        DesktopCaptureError,
        _validate_launch_command,
    )

    with pytest.raises(DesktopCaptureError, match="non-empty list"):
        _validate_launch_command("python app.py")


def test_validate_launch_command_rejects_empty_list():
    from lean_ai.tools.ui_capture_desktop import (
        DesktopCaptureError,
        _validate_launch_command,
    )

    with pytest.raises(DesktopCaptureError, match="non-empty list"):
        _validate_launch_command([])


def test_validate_launch_command_rejects_non_string_entries():
    from lean_ai.tools.ui_capture_desktop import (
        DesktopCaptureError,
        _validate_launch_command,
    )

    with pytest.raises(DesktopCaptureError, match="entries must all be strings"):
        _validate_launch_command(["python", 123])


def test_validate_launch_command_rejects_none():
    from lean_ai.tools.ui_capture_desktop import (
        DesktopCaptureError,
        _validate_launch_command,
    )

    with pytest.raises(DesktopCaptureError, match="non-empty list"):
        _validate_launch_command(None)


def test_detect_backend_respects_override():
    from lean_ai.tools.ui_capture_desktop import detect_backend

    with patch.object(settings, "ui_verification_capture_backend_override", "grim"):
        assert detect_backend() == "grim"


def test_detect_backend_dispatches_by_platform():
    """When no override is set, backend is chosen from sys.platform + Wayland env."""
    from lean_ai.tools import ui_capture_desktop as mod

    with (
        patch.object(settings, "ui_verification_capture_backend_override", ""),
        patch.object(mod, "sys") as fake_sys,
    ):
        # Windows
        fake_sys.platform = "win32"
        assert mod.detect_backend() == "mss-win32"
        # macOS
        fake_sys.platform = "darwin"
        assert mod.detect_backend() == "mac-screencapture"
        # Linux X11 (no WAYLAND_DISPLAY)
        fake_sys.platform = "linux"
        env = {k: v for k, v in os.environ.items() if k != "WAYLAND_DISPLAY"}
        with patch.dict(os.environ, env, clear=True):
            assert mod.detect_backend() == "mss-x11"
        # Linux Wayland
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}):
            assert mod.detect_backend() == "grim"


def test_wayland_compositor_none_when_not_wayland():
    from lean_ai.tools.ui_capture_desktop import wayland_compositor

    env = {k: v for k, v in os.environ.items() if k != "WAYLAND_DISPLAY"}
    with patch.dict(os.environ, env, clear=True):
        assert wayland_compositor() is None


def test_wayland_compositor_detects_gnome():
    from lean_ai.tools.ui_capture_desktop import wayland_compositor

    with patch.dict(os.environ, {
        "WAYLAND_DISPLAY": "wayland-0",
        "XDG_CURRENT_DESKTOP": "GNOME",
    }):
        assert wayland_compositor() == "gnome"


def test_wayland_compositor_detects_kde():
    from lean_ai.tools.ui_capture_desktop import wayland_compositor

    with patch.dict(os.environ, {
        "WAYLAND_DISPLAY": "wayland-0",
        "XDG_CURRENT_DESKTOP": "KDE",
    }):
        assert wayland_compositor() == "kde"


def test_missing_system_deps_for_x11(tmp_path: Path):
    from lean_ai.tools import ui_capture_desktop as mod

    # Force X11 backend
    with (
        patch.object(settings, "ui_verification_capture_backend_override", "mss-x11"),
        patch.object(mod.shutil, "which", return_value=None),
    ):
        missing = mod.missing_system_deps()
        assert "wmctrl" in missing
        assert "xdotool" in missing


def test_missing_system_deps_for_wayland():
    from lean_ai.tools import ui_capture_desktop as mod

    with (
        patch.object(settings, "ui_verification_capture_backend_override", "grim"),
        patch.object(mod.shutil, "which", return_value=None),
    ):
        assert mod.missing_system_deps() == ["grim"]


def test_missing_system_deps_empty_when_all_on_path():
    from lean_ai.tools import ui_capture_desktop as mod

    with (
        patch.object(settings, "ui_verification_capture_backend_override", "mss-x11"),
        patch.object(mod.shutil, "which", return_value="/usr/bin/fake"),
    ):
        assert mod.missing_system_deps() == []


# ── ui_analysis availability gating ────────────────────────────────────


def test_analysis_unavailable_when_feature_disabled():
    from lean_ai.tools.ui_analysis import availability_reason, is_analysis_available

    with patch.object(settings, "enable_ui_verification", False):
        assert is_analysis_available() is False
        reason = availability_reason()
        assert reason is not None
        assert "disabled" in reason.lower()


def test_analysis_unavailable_when_no_vision_model():
    from lean_ai.tools.ui_analysis import availability_reason, is_analysis_available

    with patch.object(settings, "enable_ui_verification", True), patch.object(
        settings, "vision_model", "",
    ):
        assert is_analysis_available() is False
        reason = availability_reason()
        assert reason is not None
        assert "vision model" in reason.lower()


# ── Analysis result formatting ─────────────────────────────────────────


def test_format_analysis_includes_answer_first():
    from lean_ai.tools.ui_analysis import (
        UIAnalysis,
        UIComponent,
        UIInventory,
        UITextLine,
        UITextTranscript,
    )
    from lean_ai.tools.ui_verification import _format_analysis

    analysis = UIAnalysis(
        inventory=UIInventory(
            regions=[],
            components=[UIComponent(type="button", location="top-right", label_text="Save")],
        ),
        text=UITextTranscript(lines=[UITextLine(region="header", verbatim="My App")]),
        colors={"palette": ["#FFFFFF"], "background_guess": "#FFFFFF"},
        answer="The Save button is visible in the top-right corner.",
        warnings=[],
    )
    out = _format_analysis(analysis, Path("/tmp/fake.png"))

    # Answer is the lead because callers care about that most
    first_section_idx = out.index("## Focused Answer")
    inventory_idx = out.index("## Inventory")
    text_idx = out.index("## Visible Text")
    assert first_section_idx < inventory_idx < text_idx
    assert "Save button is visible" in out
    assert "`button`" in out
    assert "My App" in out
    assert "#FFFFFF" in out


def test_format_analysis_surfaces_warnings():
    from lean_ai.tools.ui_analysis import UIAnalysis, UIInventory, UITextTranscript
    from lean_ai.tools.ui_verification import _format_analysis

    analysis = UIAnalysis(
        inventory=UIInventory(),
        text=UITextTranscript(),
        colors={},
        answer="",
        warnings=["inventory pass failed: schema validation failed"],
    )
    out = _format_analysis(analysis, Path("/tmp/x.png"))
    assert "## Warnings" in out
    assert "inventory pass failed" in out


def test_format_analysis_includes_screenshot_path():
    from lean_ai.tools.ui_analysis import UIAnalysis, UIInventory, UITextTranscript
    from lean_ai.tools.ui_verification import _format_analysis

    analysis = UIAnalysis(
        inventory=UIInventory(), text=UITextTranscript(),
        colors={}, answer="ok", warnings=[],
    )
    out = _format_analysis(analysis, Path("/abs/path/to/screenshot.png"))
    assert "`/abs/path/to/screenshot.png`" in out


# ── Tool registration gating ───────────────────────────────────────────


def test_ui_tools_absent_when_feature_disabled():
    from lean_ai.llm.tool_definitions import (
        _maybe_ui_verification_tools,
        build_chat_tools,
        build_design_tools,
        build_implementation_tools,
        build_investigation_tools,
        build_planning_tools,
    )

    with patch.object(settings, "enable_ui_verification", False):
        assert _maybe_ui_verification_tools() == []

        for builder in (
            build_planning_tools,
            build_design_tools,
            build_implementation_tools,
            build_chat_tools,
            build_investigation_tools,
        ):
            names = [t["function"]["name"] for t in builder()]
            assert "verify_web_ui" not in names
            assert "verify_desktop_ui" not in names


def test_ui_tools_present_when_feature_enabled():
    from lean_ai.llm.tool_definitions import (
        _maybe_ui_verification_tools,
        build_chat_tools,
        build_design_tools,
        build_implementation_tools,
        build_investigation_tools,
        build_planning_tools,
    )

    with patch.object(settings, "enable_ui_verification", True):
        ui = _maybe_ui_verification_tools()
        assert [t["function"]["name"] for t in ui] == [
            "verify_web_ui", "verify_desktop_ui",
        ]

        for builder in (
            build_planning_tools,
            build_design_tools,
            build_implementation_tools,
            build_chat_tools,
            build_investigation_tools,
        ):
            names = [t["function"]["name"] for t in builder()]
            assert "verify_web_ui" in names, f"{builder.__name__} missing verify_web_ui"
            assert "verify_desktop_ui" in names, (
                f"{builder.__name__} missing verify_desktop_ui"
            )


def test_verify_web_ui_schema_required_params():
    from lean_ai.llm.tool_definitions import VERIFY_WEB_UI_TOOL

    fn = VERIFY_WEB_UI_TOOL["function"]
    assert fn["name"] == "verify_web_ui"
    assert set(fn["parameters"]["required"]) == {"url", "question"}
    # Optional params that exist so the LLM can use them
    props = fn["parameters"]["properties"]
    for opt in ("viewport", "wait_for_selector", "wait_seconds", "full_page"):
        assert opt in props, f"missing optional param {opt}"


def test_verify_desktop_ui_schema_required_params():
    from lean_ai.llm.tool_definitions import VERIFY_DESKTOP_UI_TOOL

    fn = VERIFY_DESKTOP_UI_TOOL["function"]
    assert fn["name"] == "verify_desktop_ui"
    assert set(fn["parameters"]["required"]) == {
        "launch_command", "window_title", "question",
    }
    # launch_command must be an array (enforces list → blocks shell injection)
    assert fn["parameters"]["properties"]["launch_command"]["type"] == "array"

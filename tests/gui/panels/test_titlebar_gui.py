"""S6 custom title bar: one merged caption row (brand + command bar + window buttons),
menuWidget placement, and the VIKE_NATIVE_TITLEBAR fallback. Native drag/snap/resize behavior
is Win32-message-driven (verified live on a real display); these tests cover composition."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("PySide6QtAds")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.ui import titlebar as tb_mod  # noqa: E402
from vike_trader_app.ui.app import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_titlebar_is_the_menu_widget_and_hosts_commandbar(app):
    win = MainWindow(session_path=None)
    assert win.menuWidget() is win.titlebar
    assert win.titlebar.isAncestorOf(win.topbar)      # ≡ + box + launchers live IN the bar
    assert win.titlebar.height() == tb_mod.TITLEBAR_H
    win.close()


def test_window_buttons_match_mode(app):
    win = MainWindow(session_path=None)
    if tb_mod.frameless_enabled():
        assert len(win.titlebar._win_buttons) == 3    # ─ □ ✕
        tips = [b.toolTip() for b in win.titlebar._win_buttons]
        assert tips == ["Minimize", "Maximize", "Close"]
    else:
        assert win.titlebar._win_buttons == []        # native caption supplies them
    win.close()


def test_native_titlebar_escape_hatch(app, monkeypatch):
    monkeypatch.setenv("VIKE_NATIVE_TITLEBAR", "1")
    assert not tb_mod.frameless_enabled()
    win = MainWindow(session_path=None)
    assert win.menuWidget() is win.titlebar           # bar still present (below the OS caption)
    assert win.titlebar._win_buttons == []            # but without window buttons
    assert win._frameless_filter is None
    win.close()

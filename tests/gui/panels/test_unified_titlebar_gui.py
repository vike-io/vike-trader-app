"""Unified title bar (stage 1): the CDockComponentsFactory renders a single-title chart
header on the central spaces area and the SAME UnifiedTitleBar on side panels.

GUI suite (offscreen Qt). Uses the full MainWindow (matching the other dock GUI tests) so
ADS sets up + tears down safely — a bare CDockManager built/destroyed without an event loop
segfaults on Python 3.14.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("PySide6QtAds")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.ui.app import MainWindow  # noqa: E402
from vike_trader_app.ui.dockshell import VikeDockTitleBar  # noqa: E402
from vike_trader_app.ui.unifiedbar import BAR_H, UnifiedTitleBar, bar_button  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_bar_button_and_unified_bar(app):
    hits = []
    b = bar_button("✕", "Close", lambda: hits.append(1), danger=True)
    assert b.text() == "✕" and b.height() == BAR_H
    b.click()
    assert hits == [1]

    bar = UnifiedTitleBar(title="X")
    mx = bar.add_button("max", "□", "Max", lambda: None)
    assert bar.button("max") is mx and bar.button("nope") is None
    bar.set_title("Y")
    assert bar._title.text() == "Y"
    bar.set_title_rich("<span>Z · 1.23 ▲0.10%</span>")   # the live-ticker path
    assert "Z" in bar._title.text()
    bar.set_active(True)
    assert "#unifiedBar" in bar.styleSheet()             # scoped — never bare background


def test_central_area_gets_single_title_chart_header(app):
    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    tb = win.tabs._resolve_area().titleBar()
    assert isinstance(tb, VikeDockTitleBar)
    assert tb.is_chart_header()          # single-title header, NOT a 9-tab strip
    assert not tb._is_panel
    assert tb._header is not None
    assert {"detach", "min", "max", "close"} <= set(tb._header._buttons)
    # the live-ticker title flows through the persistent model + header widget
    win.tabs.set_header_title("Chart · BTCUSDT · 1m")
    assert win.tabs._header_title == "Chart · BTCUSDT · 1m"
    win.close()


def test_panel_area_gets_unified_bar(app):
    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()                  # let the panels' singleShot(0) auto-detect run
    app.processEvents()
    tb = win._market_dock.dockAreaWidget().titleBar()
    assert isinstance(tb, VikeDockTitleBar)
    assert tb._is_panel                  # detected the 'panel:' dock
    assert tb._header is not None
    assert {"detach", "min", "max", "close"} <= set(tb._header._buttons)
    win.close()

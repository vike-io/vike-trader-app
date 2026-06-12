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


def test_central_chart_is_link_member_with_header_dots(app):
    """Stage 2: the central chart space joins the symbol-link bus and its header carries the
    ● symbol + ◆ interval link dots; recolouring via the setters updates the model."""
    from vike_trader_app.ui.panels import LinkDot

    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    assert win in win._link_bus._members            # bus member (duck-typed link_group/apply_link)
    assert hasattr(win, "link_group") and callable(win.apply_link)
    bar = win.tabs.header_widget()
    assert len(bar._statusbox.findChildren(LinkDot)) >= 2   # ● + ◆
    win._set_central_link_group(2)
    assert win.link_group == 2
    win._set_central_interval_link_group(3)
    assert win.interval_link_group == 3
    win._set_central_interval_link_group(-1)        # -1 = follow symbol
    assert win.interval_link_group is None
    win.close()


def test_central_link_survives_session_roundtrip(app, tmp_path):
    path = tmp_path / "s.json"
    w1 = MainWindow(session_path=str(path))
    w1._set_central_link_group(4)
    w1._set_central_interval_link_group(5)
    w1.close()                                       # persists on close
    w2 = MainWindow(session_path=str(path))
    assert w2.link_group == 4
    assert w2.interval_link_group == 5
    w2.close()


def test_header_feed_badge_tracks_feed_health(app):
    """Stage 2: the chart-space header carries a feed badge that mirrors _set_feed_health."""
    from vike_trader_app.ui.unifiedbar import FeedBadge

    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    assert isinstance(win._header_feed, FeedBadge)
    win._set_feed_health("live")
    assert win._feed_state == "live" and "LIVE" in win._header_feed.text()
    win._set_feed_health("cached")
    assert "CACHED" in win._header_feed.text()
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
    # Stage A1: dock-only panels carry just ─ (auto-hide/pin) + ✕ (close) — no ⧉ detach or □
    # maximize (both depended on ADS floating, which is disabled).
    assert {"min", "close"} == set(tb._header._buttons)
    win.close()


def test_tool_dock_gets_detach_button(app):
    """Stage A2: a TOOL dock (tool:…) carries a ⧉ 'open as window' verb that a side panel
    (panel:…) does not — they share the unified bar but only tools tear out."""
    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    dock = win.open_tool("screener")
    app.processEvents()
    app.processEvents()                  # let mark_as_panel's singleShot(0) re-hide run
    tb = dock.dockAreaWidget().titleBar()
    assert isinstance(tb, VikeDockTitleBar)
    assert tb._is_panel                  # tool docks share the panel bar flavor
    assert {"detach", "min", "close"} == set(tb._header._buttons)
    win.close()


def test_tool_detach_redock_close_lifecycle(app):
    """Stage A2: ⧉ tears the LIVE tool widget into a clean ToolWindowFrame (no rebuild, no state
    loss, alias intact), 'Dock into workspace' reparents it back, and the window ✕ runs the full
    teardown (alias nil'd)."""
    from vike_trader_app.ui.chartwin import ToolWindowFrame

    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    dock = win.open_tool("screener")
    app.processEvents()
    widget = dock.widget()
    assert win.screener is widget        # legacy alias set while open

    # detach -> clean tool window hosting the SAME widget; dock ref gone; alias intact
    win._detach_tool("screener")
    app.processEvents()
    frame = win._tool_frames.get("screener")
    assert isinstance(frame, ToolWindowFrame)
    assert frame.doc is widget
    assert "screener" not in win._tool_docks
    assert not win._tool_detaching       # the detach-close flag was consumed
    assert frame._feed_badge is None and frame._bar.button("clone") is None
    assert win.screener is widget        # teardown skipped on detach

    # re-dock -> back as a dock hosting the SAME widget
    win._redock_tool("screener")
    app.processEvents()
    dock2 = win._tool_docks.get("screener")
    assert dock2 is not None and dock2.widget() is widget
    assert "screener" not in win._tool_frames
    assert win.screener is widget

    # detach again, then close the WINDOW -> full teardown (alias nil'd, no dock left)
    win._detach_tool("screener")
    app.processEvents()
    win._tool_frames["screener"].close_window()
    app.processEvents()
    assert "screener" not in win._tool_frames
    assert "screener" not in win._tool_docks
    assert win.screener is None
    win.close()

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


def test_chart_frame_gets_single_title_unified_bar(app):
    """Keystone: the docked central chart header is GONE — there's no chart space anymore
    (win.tabs.count() == 0). The chart-header chrome now lives on each chart FRAME's own
    UnifiedTitleBar: a single MC-style title (NOT a tab strip) with the ─ □ ✕ window controls
    (⧉ dropped — float/attach via the right-click menu)."""
    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    assert win.tabs.count() == 0                     # no chart space / no central chart header
    assert win.price is None                          # nothing focused on a bare window

    doc = win._new_chart_document("BTCUSDT", "1m", network=False, make_current=True)
    app.processEvents()
    frame = win._chart_frames[-1]
    assert isinstance(frame._bar, UnifiedTitleBar)
    assert {"min", "max", "close"} <= set(frame._bar._buttons)   # ⧉ dropped (float by menu)
    # the live-ticker title flows through the frame's own bar (one MC title, not a 9-tab strip)
    assert frame._bar._title.text() == doc.title() == "BTCUSDT · 1m"
    win.close()


def test_chart_frame_is_link_member_with_header_dots(app):
    """Keystone: there's no central chart link-bus member with header dots anymore. Each chart
    FRAME's bar adopts the doc's ● symbol + ◆ interval link dots, and the doc is the bus member;
    recolouring via the doc's setters updates the model."""
    from vike_trader_app.ui.panels import LinkDot

    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    doc = win._new_chart_document("BTCUSDT", "1m", network=False, make_current=True)
    app.processEvents()
    frame = win._chart_frames[-1]

    assert doc in win._link_bus._members            # the DOC is the bus member (not the window)
    assert hasattr(doc, "link_group") and callable(doc.apply_link)
    # the frame's title bar carries the doc's ● symbol + ◆ interval link dots
    assert len(frame._bar._statusbox.findChildren(LinkDot)) >= 2   # ● + ◆
    doc._set_link_group(2)
    assert doc.link_group == 2
    doc._set_interval_link_group(3)
    assert doc.interval_link_group == 3
    doc._set_interval_link_group(-1)                # -1 = follow symbol
    assert doc.interval_link_group is None
    win.close()


def test_chart_frame_feed_badge_tracks_feed_health(app):
    """Keystone: the feed badge lived on the gone central-chart header — it now lives on each
    chart FRAME's bar. The frame carries a FeedBadge that the host paints via frame.set_feed
    (the live/cached state -> colour + text mapping)."""
    from vike_trader_app.ui.unifiedbar import FeedBadge

    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    doc = win._new_chart_document("BTCUSDT", "1m", network=False, make_current=True)
    app.processEvents()
    frame = win._chart_frames[-1]
    assert isinstance(frame._feed_badge, FeedBadge)
    frame.set_feed("#26a69a", "LIVE")
    assert "LIVE" in frame._feed_badge.text()
    frame.set_feed("#787b86", "CACHED")
    assert "CACHED" in frame._feed_badge.text()
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
    # Unified title bar: ─ □ ✕ (MC/VS). ⧉ dropped — float a panel by dragging its title bar out.
    assert {"min", "max", "close"} == set(tb._header._buttons)
    win.close()


def test_tool_opens_as_window_with_unified_bar(app):
    """MT-style: a tool opens as its OWN window (ToolWindowFrame) with the unified ─ □ ✕ bar
    (⧉ dropped, no chart-only ＋ — float/attach via the right-click menu or dragging the bar)."""
    from vike_trader_app.ui.chartwin import ToolWindowFrame

    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    frame = win.open_tool("screener")
    app.processEvents()
    assert isinstance(frame, ToolWindowFrame)
    assert win._tool_frames.get("screener") is frame
    assert {"min", "max", "close"} == set(frame._bar._buttons)
    win.close()


def test_tool_window_redock_detach_close_lifecycle(app):
    """MT-style: a tool opens as a window; 'Dock into workspace' reparents the LIVE widget into a
    dock (no rebuild, alias intact); ⧉ tears it back out; the window ✕ runs the full teardown."""
    from vike_trader_app.ui.chartwin import ToolWindowFrame

    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    frame = win.open_tool("screener")            # opens as a window
    app.processEvents()
    assert isinstance(frame, ToolWindowFrame)
    widget = frame.doc
    assert win.screener is widget                # legacy alias set while open

    # Dock into workspace -> back as a dock hosting the SAME widget
    win._redock_tool("screener")
    app.processEvents()
    dock = win._tool_docks.get("screener")
    assert dock is not None and dock.widget() is widget
    assert "screener" not in win._tool_frames
    assert win.screener is widget

    # ⧉ detach again -> clean tool window hosting the SAME widget; dock ref gone; alias intact
    win._detach_tool("screener")
    app.processEvents()
    frame2 = win._tool_frames.get("screener")
    assert isinstance(frame2, ToolWindowFrame)
    assert frame2.doc is widget
    assert "screener" not in win._tool_docks
    assert not win._tool_detaching               # the detach-close flag was consumed
    assert frame2._feed_badge is None and frame2._bar.button("clone") is None
    assert win.screener is widget                # teardown skipped on detach

    # close the WINDOW -> full teardown (alias nil'd, no dock left)
    win._tool_frames["screener"].close_window()
    app.processEvents()
    assert "screener" not in win._tool_frames
    assert "screener" not in win._tool_docks
    assert win.screener is None
    win.close()


def test_panel_detach_redock_close_lifecycle(app):
    """A side panel (Market watch) floats out into a clean ToolWindowFrame hosting the SAME live
    widget; unlike a tool, the panel dock is NOT DeleteOnClose, so the SAME (emptied, hidden) dock
    is reused on every round-trip. 'Dock into workspace' re-homes the widget; the window ✕ also
    re-homes it (never orphaned) and shows the panel closed on the rail. Guards the panel reparent
    trio (_detach_panel / _redock_panel / _on_panel_window_closed) dispatched only from
    dockshell.py — previously the one lifecycle path in the shell with no direct test."""
    from vike_trader_app.ui.chartwin import ToolWindowFrame

    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    app.processEvents()                          # let the panels' singleShot(0) auto-detect run

    dock = win._panel_dock_map["market"]
    widget = dock.widget()
    assert widget is not None

    # ⧉ detach -> a clean tool window hosting the SAME widget; the reused dock is emptied + hidden
    frame = win._detach_panel(dock)
    app.processEvents()
    assert isinstance(frame, ToolWindowFrame)
    assert win._panel_frames.get("market") is frame
    assert frame.doc is widget
    assert dock.widget() is None and dock.isClosed()

    # detaching the SAME panel again is a no-op that just raises the existing window
    assert win._detach_panel(dock) is frame
    assert win._panel_frames.get("market") is frame

    # 'Dock into workspace' -> the SAME widget back in the reused dock, dock re-shown, frame gone
    win._redock_panel("market")
    app.processEvents()
    assert "market" not in win._panel_frames
    assert dock.widget() is widget
    assert not dock.isClosed()

    # detach once more, then close the WINDOW (✕) -> widget re-homed (not orphaned) + dock shown closed
    frame2 = win._detach_panel(dock)
    app.processEvents()
    assert dock.widget() is None
    frame2.close_window()
    app.processEvents()
    assert "market" not in win._panel_frames
    assert dock.widget() is widget               # re-homed into the reused dock, never orphaned
    assert dock.isClosed()                        # the rail reflects the panel closed
    win.close()

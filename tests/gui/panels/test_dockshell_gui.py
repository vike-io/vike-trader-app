"""Offscreen tests for the ADS dock shell (SpaceDeck facade + unlockable panel docks)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("PySide6QtAds")

import PySide6QtAds as QtAds  # noqa: E402
from PySide6 import QtWidgets  # noqa: E402

import vike_trader_app.ui.chartdoc as chartdoc  # noqa: E402
from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.ui.app import MainWindow  # noqa: E402
from vike_trader_app.ui.dataload import LoadResult  # noqa: E402
from vike_trader_app.ui.dockshell import SpaceDeck  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_window_chrome_config_flags(app):
    """S1 per-window chrome (MC16 parity): the manager runs with focus highlighting,
    middle-click tab close, double-click detach, equal splits, and widget-titled floats."""
    win = MainWindow(session_path=None)  # construction runs configure_dock_manager_defaults
    M = QtAds.CDockManager
    for flag in (M.FocusHighlighting, M.MiddleMouseButtonClosesTab,
                 M.DoubleClickUndocksWidget, M.EqualSplitOnInsertion,
                 M.FloatingContainerHasWidgetTitle, M.DockAreaHideDisabledButtons):
        assert M.testConfigFlag(flag), flag
    win.close()


def test_four_charts_tile_and_autohide_to_edges(app, monkeypatch):
    """MC-style live layout: open 4 floating chart WINDOWS, tile them 2x2 with the arrange
    verb (geometry math, no docking — the user rejected dock-tiling), auto-hide Market watch
    to the LEFT edge and Trades to the BOTTOM edge, then reveal one again."""
    bars = [Bar(ts=i * 60_000, open=100 + i, high=101 + i, low=99 + i, close=100 + i)
            for i in range(30)]
    monkeypatch.setattr(chartdoc, "load_symbol_bars", lambda *a, **k: LoadResult(list(bars)))

    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT"):
        win._new_chart_document(s, "1h")
    assert len(win._chart_frames) == 4

    win._arrange_chart_windows("grid")
    app.processEvents()
    geos = [f.geometry() for f in win._chart_frames]
    # a real 2x2 tiling: four distinct, pairwise non-overlapping rectangles
    assert len({(g.x(), g.y(), g.width(), g.height()) for g in geos}) == 4
    for i in range(4):
        for j in range(i + 1, 4):
            assert not geos[i].intersects(geos[j])

    # auto-hide the panels to edges (the AmiBroker side-rail)
    win._market_dock.setAutoHide(True, QtAds.SideBarLeft)
    win._trades_dock.setAutoHide(True, QtAds.SideBarBottom)
    app.processEvents()
    assert win._market_dock.isAutoHide()
    assert win._market_dock.autoHideLocation() == QtAds.SideBarLeft
    assert win._trades_dock.isAutoHide()
    assert win._trades_dock.autoHideLocation() == QtAds.SideBarBottom

    # reveal the Market watch again (un-pin back into the layout)
    win._market_dock.setAutoHide(False)
    app.processEvents()
    assert not win._market_dock.isAutoHide()
    win.close()


def test_spacedeck_mirrors_qtabwidget_api(app):
    win = MainWindow()
    deck = win.tabs
    assert isinstance(deck, SpaceDeck)
    assert deck.count() == len(win._SPACE_ITEMS)   # only Chart + Studio remain spaces (the 7
    # non-Studio tools open on demand as docks now, not eager SpaceDeck spaces)
    # construction leaves the CHART space current (ADS would otherwise sit on the last-added)
    assert deck.currentIndex() == 0
    assert deck.currentWidget() is win._backtester
    # identity round-trips
    assert deck.widget(deck.indexOf(win.studio)) is win.studio
    assert deck.tabText(0) == "Chart"
    assert deck.isAncestorOf(win.studio)
    assert not deck.isAncestorOf(win.watchlist)  # panels are NOT in the spaces area
    win.close()


def test_spacedeck_current_changed_drives_rail(app):
    # Switching to a SPACE (only Chart/Studio remain) drives the rail + title bar. (Screener and
    # the other tools are docks now, not spaces, so navigate to Studio here.)
    win = MainWindow()
    got = []
    win.tabs.currentChanged.connect(got.append)
    idx = win.tabs.indexOf(win.studio)
    win.tabs.setCurrentIndex(idx)
    assert got and got[-1] == idx
    assert win._rail_group.button(idx).isChecked()          # rail mirrors the deck
    assert win.windowTitle().endswith("Studio")             # title bar tracks the space
    win.close()


def test_panel_docks_are_ads_and_unlockable(app):
    win = MainWindow()
    for dock in win._docks:
        assert isinstance(dock, QtAds.CDockWidget)
        feats = dock.features()
        assert feats & QtAds.CDockWidget.DockWidgetClosable
        assert feats & QtAds.CDockWidget.DockWidgetMovable
        assert feats & QtAds.CDockWidget.DockWidgetFloatable
        assert feats & QtAds.CDockWidget.DockWidgetPinnable  # auto-hide pin (edge tabs)
    # spaces, by contrast, are pinned in place (stable rail indices until Phase 2)
    assert win.tabs.dock(0).features() == QtAds.CDockWidget.NoDockWidgetFeatures
    win.close()


def test_user_closing_panel_syncs_rail_toggle(app):
    win = MainWindow()
    win._panel_btns["market"].setChecked(True)              # open Market watch via the rail
    assert not win._market_dock.isClosed()
    # simulate the user hitting the panel's own close button (unguarded toggleView)
    win._market_dock.toggleView(False)
    assert win._panel_btns["market"].isChecked() is False   # rail toggle mirrored the close
    assert win._panel_visible["market"] is False            # remembered intent updated
    # ...and a space round-trip must NOT resurrect the closed panel
    win.tabs.setCurrentIndex(win.tabs.indexOf(win.studio))
    win.tabs.setCurrentIndex(0)
    assert win._market_dock.isClosed()
    win.close()


def test_on_tab_changed_is_non_reentrant(app):
    """A re-entrant _on_tab_changed call bails instead of looping (stack-overflow guard)."""
    win = MainWindow()
    win._in_tab_change = True            # simulate being mid-dispatch
    before = win.windowTitle()
    win._on_tab_changed(win.tabs.indexOf(win.studio))  # must no-op, not recurse
    assert win.windowTitle() == before
    win._in_tab_change = False
    win.close()


def test_panel_drop_into_spaces_area_does_not_crash(app):
    """Regression: tabbing a floatable panel into the spaces area used to recurse to a stack
    overflow. With the central-widget area + the re-entrancy guard it must stay alive."""
    import PySide6QtAds as QtAds

    win = MainWindow()
    win._panel_btns["market"].setChecked(True)  # open Market watch on the Chart space
    # force the exact insertion the drag path performs (verified crash repro in review)
    QtAds.CDockManager  # noqa: B018 - ensure import side effects
    try:
        win.dock_manager.addDockWidgetTabToArea(
            win._market_dock, win.tabs.dock(0).dockAreaWidget()
        )
    except Exception:  # noqa: BLE001 - ADS may itself reject the drop; either way: no crash
        pass
    win.tabs.setCurrentIndex(win.tabs.indexOf(win.studio))  # exercise a space switch after
    win.tabs.setCurrentIndex(0)
    assert win.tabs.count() == len(win._SPACE_ITEMS)  # still alive, spaces intact
    win.close()


def test_out_of_range_saved_space_clamps_and_resyncs(app, tmp_path):
    """A saved space index past the end (e.g. an old session saved on a now-removed tool space)
    clamps to Chart (0) and still re-syncs the rail/title, rather than leaving the shell
    disconnected. (Empty-workspace re-arch: out-of-range lands on Chart, not the last space.)"""
    import json

    path = tmp_path / "session.json"
    first = MainWindow(session_path=str(path))
    first.close()
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["space"] = 999                      # simulate a removed/reordered space
    path.write_text(json.dumps(raw), encoding="utf-8")

    second = MainWindow(session_path=str(path))
    idx = second.tabs.currentIndex()
    assert idx == 0                         # out-of-range (old tool-space index) clamps to Chart
    assert second._rail_group.button(idx).isChecked()       # rail re-synced (not stuck/disconnected)
    assert second.windowTitle().endswith(second._SPACE_ITEMS[idx][1])
    second.close()


# --- Arrange (MultiCharts Window->Arrange parity) + keep-on-top pin -------------------------


@pytest.fixture
def _synthetic_load(monkeypatch):
    bars = [Bar(ts=i * 60_000, open=100 + i, high=101 + i, low=99 + i, close=100 + i)
            for i in range(30)]
    monkeypatch.setattr(chartdoc, "load_symbol_bars", lambda *a, **k: LoadResult(list(bars)))


def _open_docs(win, n):
    syms = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT", "XRPUSDT", "DOGEUSDT")
    return [win._new_chart_document(s, "1h") for s in syms[:n]]


@pytest.mark.parametrize(("n", "mode"), [(3, "grid"), (3, "columns"), (3, "rows"),
                                         (4, "grid"), (5, "grid"), (6, "grid")])
def test_arrange_modes_tile_frames_without_overlap(app, _synthetic_load, n, mode):
    """S7 arrange = geometry math over the floating frames: every window gets a distinct,
    pairwise non-overlapping rectangle."""
    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    _open_docs(win, n)
    win._arrange_chart_windows(mode)
    app.processEvents()
    geos = [f.geometry() for f in win._chart_frames]
    assert len({(g.x(), g.y(), g.width(), g.height()) for g in geos}) == n
    for i in range(n):
        for j in range(i + 1, n):
            assert not geos[i].intersects(geos[j]), (mode, i, j)
    win.close()


def test_arrange_cascade_staggers(app, _synthetic_load):
    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    _open_docs(win, 3)
    win._arrange_chart_windows("cascade")
    pos = [(f.x(), f.y()) for f in win._chart_frames]
    assert pos == sorted(pos)               # marching down-right
    assert len(set(pos)) == 3
    win.close()


def test_arrange_skips_detached_frames(app, _synthetic_load):
    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    _open_docs(win, 2)
    win._chart_frames[1].toggle_detach()     # now its own OS window
    assert win._chart_frames[1].is_detached()
    win._arrange_chart_windows("grid")       # must not touch the detached one, must not raise
    app.processEvents()
    assert win._chart_frames[1].is_detached()
    win._chart_frames[1].toggle_detach()     # re-attach for clean teardown
    win.close()


def test_arrange_with_no_windows_is_noop(app):
    win = MainWindow(session_path=None)
    win._arrange_chart_windows("grid")       # no frames — must not raise
    win.close()


def test_chart_window_resize_is_throttled_and_shadowless(app, _synthetic_load):
    """Perf: the floating chart frame carries NO drop-shadow effect (it forced a full re-render
    per move), and a live edge-resize is THROTTLED — moves coalesce onto a timer instead of a
    synchronous setGeometry+relayout per event — yet the final geometry is applied exactly."""
    from PySide6 import QtCore, QtGui

    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    win._new_chart_document("BTCUSDT", "1h")
    f = win._chart_frames[0]
    assert f.graphicsEffect() is None              # shadow removed
    assert f._resize_timer.interval() == 16

    def evt(lx, ly, et, btn, bts):
        gp = f.mapToGlobal(QtCore.QPoint(int(lx), int(ly)))
        return QtGui.QMouseEvent(et, QtCore.QPointF(lx, ly), QtCore.QPointF(gp), btn, bts,
                                 QtCore.Qt.NoModifier)

    w0, h0 = f.width(), f.height()
    f.mousePressEvent(evt(w0 - 2, h0 - 2, QtCore.QEvent.MouseButtonPress,
                          QtCore.Qt.LeftButton, QtCore.Qt.LeftButton))
    assert f._resize_edge is not None
    for d in (40, 90, 150):                        # a burst of resize moves
        f.mouseMoveEvent(evt(w0 - 2 + d, h0 - 2 + d, QtCore.QEvent.MouseMove,
                             QtCore.Qt.NoButton, QtCore.Qt.LeftButton))
    assert f._resize_timer.isActive()              # coalescing via the timer, not per-move
    assert f._pending_geo is not None              # latest move stored, not yet applied inline
    f.mouseReleaseEvent(evt(0, 0, QtCore.QEvent.MouseButtonRelease,
                            QtCore.Qt.NoButton, QtCore.Qt.NoButton))
    assert not f._resize_timer.isActive()          # stopped on release
    assert f._resize_edge is None
    assert f._pending_geo is None                  # final geometry flushed
    assert f.width() > w0 and f.height() > h0      # it actually grew to the last move
    win.close()


def test_palette_has_arrange_commands(app):
    win = MainWindow(session_path=None)
    labels = [label for label, _cb in win._commands()]
    for want in ("Arrange charts: tile grid", "Arrange charts: side by side",
                 "Arrange charts: stacked", "Arrange charts: cascade"):
        assert want in labels
    win.close()


def test_pin_appears_on_detach_and_sets_stays_on_top(app, _synthetic_load, monkeypatch):
    """Pin chrome (in the chart window's TITLE BAR now) + native z-order seam: pin shows when
    the frame is detached to its own OS window, drives ``_set_topmost``, and resets on attach."""
    calls = []
    monkeypatch.setattr(chartdoc, "_set_topmost",
                        lambda wid, on: calls.append((int(wid), on)) or True)
    win = MainWindow(session_path=None)
    doc = _open_docs(win, 1)[0]
    frame = win._chart_frames[0]
    assert doc._pin_btn.isHidden()                           # attached: no pin
    frame.toggle_detach()
    app.processEvents()
    assert not doc._pin_btn.isHidden()                       # detached: pin shown
    doc._pin_btn.setChecked(True)
    assert [on for _w, on in calls] == [True]
    doc._pin_btn.setChecked(False)
    assert [on for _w, on in calls] == [True, False]
    app.processEvents()
    assert len(win._chart_frames) == 1 and doc.symbol == "BTCUSDT"
    frame.toggle_detach()                                    # re-attach -> pin hides + resets
    app.processEvents()
    assert doc._pin_btn.isHidden() and not doc._pin_btn.isChecked()
    win.close()


# --- launcher-floated spaces stay navigable (regression: review wjs9t80fy) ------------------


def test_floating_a_space_does_not_wedge_navigation(app):
    """A launcher floats a space out of the central area; navigating to OTHER docked spaces
    must still work. Regression: _resolve_area was keyed on _docks[0], so floating Chart
    (index 0) stranded every other space (silent setCurrentIndex no-op)."""
    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    win.tabs.float_space(0)                  # float Chart — the worst case (was index 0)
    app.processEvents()
    studio = win.tabs.indexOf(win.studio)    # the only OTHER docked space now (tools are docks)
    win.tabs.setCurrentIndex(studio)         # navigate to a still-docked space
    app.processEvents()
    assert win.tabs.currentIndex() == studio
    assert win.tabs.currentWidget() is win.studio
    win.close()


def test_navigating_to_a_floated_space_raises_its_window(app):
    """setCurrentIndex on a floated space is float-aware: it raises the window instead of a
    dead no-op, and the space stays floating (reachable from Go menu / palette)."""
    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    studio = win.tabs.indexOf(win.studio)
    win.tabs.float_space(studio)
    app.processEvents()
    assert win.tabs.dock(studio).isFloating()
    win.tabs.setCurrentIndex(studio)         # the Go-menu / palette path
    app.processEvents()
    assert win.tabs.dock(studio).isFloating()    # still a window, not a dead tab
    win.close()


def test_floating_a_space_keeps_the_strip_hidden(app):
    """float_space must keep the native space TAB strip hidden (ADS re-shows it on the tab-bar
    rebuild). The central area's title bar is now the unified chart HEADER — intentionally
    visible (it replaces the strip), so we assert the tabs stay hidden + the header is present."""
    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    win.tabs.float_space(win.tabs.indexOf(win.studio))   # any space (tools are docks now)
    app.processEvents()
    app.processEvents()                       # the singleShot(0) re-hide
    area = win.tabs._resolve_area()
    assert area is not None
    assert all(not d.tabWidget().isVisible() for d in win.tabs._docks)
    tb = area.titleBar()
    assert getattr(tb, "is_chart_header", lambda: False)()   # header, not the raw tab strip
    win.close()


def test_dock_layout_round_trips_through_session(app, tmp_path):
    path = tmp_path / "session.json"
    first = MainWindow(session_path=str(path))
    first._panel_btns["market"].setChecked(True)            # leave Market watch open
    first.close()

    second = MainWindow(session_path=str(path))
    assert second._session.dock_state_hex                    # layout blob persisted
    assert second._panel_btns["market"].isChecked()          # toggle restored (panels dict)
    assert not second._market_dock.isClosed()                # ...and the dock actually open
    assert second._trades_dock.isClosed()                    # untouched panel stays closed
    second.close()

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
    """Per-window chrome: the manager runs with focus highlighting, middle-click tab close,
    equal splits, and widget-titled floats. Stage A1: ADS floating is disabled, so
    DoubleClickUndocksWidget is OFF (double-clicking a title bar must NOT float a dock) —
    charts float via chartwin instead."""
    win = MainWindow(session_path=None)  # construction runs configure_dock_manager_defaults
    M = QtAds.CDockManager
    for flag in (M.FocusHighlighting, M.MiddleMouseButtonClosesTab,
                 M.EqualSplitOnInsertion,
                 M.FloatingContainerHasWidgetTitle, M.DockAreaHideDisabledButtons):
        assert M.testConfigFlag(flag), flag
    # Stage A1: double-click-undock is OFF (no broken ADS floats)
    assert not M.testConfigFlag(M.DoubleClickUndocksWidget)
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
    assert deck.count() == len(win._SPACE_ITEMS)   # only Chart remains a space — Studio + the 7
    # tools open on demand as docks now, not eager SpaceDeck spaces
    assert deck.count() == 1
    # construction leaves the CHART space current
    assert deck.currentIndex() == 0
    assert deck.currentWidget() is win._backtester
    # identity round-trips for the one remaining space
    assert deck.widget(0) is win._backtester
    assert deck.tabText(0) == "Chart"
    assert deck.isAncestorOf(win._backtester)
    assert not deck.isAncestorOf(win.watchlist)  # panels are NOT in the spaces area
    win.close()


def test_spacedeck_current_changed_drives_rail(app):
    # Re-selecting the one Chart SPACE drives the rail + title bar. (Studio and the other tools are
    # docks now, so they no longer participate in space navigation.)
    win = MainWindow()
    win.tabs.setCurrentIndex(0)
    win._on_tab_changed(0)
    assert win._rail_group.button(0).isChecked()            # rail mirrors the deck
    assert win.windowTitle().endswith("Chart")              # title bar tracks the space
    win.close()


def test_panel_docks_are_dock_only(app):
    """Stage A1: panels are dock-only — closable / movable (tile+tab) / pinnable (auto-hide edge
    tabs), but NOT floatable (ADS tear-out floating is disabled; it produced broken chrome)."""
    win = MainWindow()
    for dock in win._docks:
        assert isinstance(dock, QtAds.CDockWidget)
        feats = dock.features()
        assert feats & QtAds.CDockWidget.DockWidgetClosable
        assert feats & QtAds.CDockWidget.DockWidgetMovable
        assert feats & QtAds.CDockWidget.DockWidgetPinnable  # auto-hide pin (edge tabs)
        assert not (feats & QtAds.CDockWidget.DockWidgetFloatable)  # no tear-out float (A1)
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
    # ...and re-selecting the Chart space must NOT resurrect the closed panel
    win.tabs.setCurrentIndex(0)
    win._on_tab_changed(0)
    assert win._market_dock.isClosed()
    win.close()


def test_on_tab_changed_is_non_reentrant(app):
    """A re-entrant _on_tab_changed call bails instead of looping (stack-overflow guard)."""
    win = MainWindow()
    win._in_tab_change = True            # simulate being mid-dispatch
    before = win.windowTitle()
    win._on_tab_changed(0)              # must no-op, not recurse
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
    win.tabs.setCurrentIndex(0)        # re-select the Chart space after the drop
    win._on_tab_changed(0)
    assert win.tabs.count() == len(win._SPACE_ITEMS)  # still alive, the Chart space intact
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


def test_vike_dock_titlebar_attrs_exist_before_init():
    """Regression: Qt's C++ base can fire resizeEvent DURING super().__init__(), before the
    instance attrs are set — our resizeEvent/refresh_native_hidden touch self._header. Class-level
    defaults must exist so that early resize can't raise AttributeError (a real-platform crash on
    float-restore that offscreen event timing doesn't reproduce). Guard the defaults here."""
    from vike_trader_app.ui.dockshell import VikeDockTitleBar
    assert VikeDockTitleBar._header is None
    assert VikeDockTitleBar._is_panel is False
    assert VikeDockTitleBar._deck is None
    assert VikeDockTitleBar._area_w is None


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


# --- spaces never become native floats (title-bar / float re-arch) --------------------------


def test_space_cannot_float_natively(app):
    """Native ADS floating is retired: float_space is removed, no space is DockWidgetFloatable, and
    setCurrentIndex no longer floats. Navigating to the Chart space keeps it DOCKED (never a native
    CFloatingDockContainer) — charts float only via chartwin."""
    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    assert not hasattr(win.tabs, "float_space")          # the native-float entry point is gone
    win.tabs.setCurrentIndex(0)                          # the Go-menu / palette nav path
    app.processEvents()
    assert not win.tabs.dock(0).isFloating()             # docked, never a native float
    assert len(list(win.dock_manager.floatingWidgets())) == 0
    win.close()


def test_show_space_reshows_a_hidden_space_docked(app):
    """Hiding the Chart space (header ✕) then re-showing it (rail/menu launcher) brings it back
    DOCKED and current — no native float involved."""
    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    win.tabs.close_current_document()                    # hide the Chart space
    app.processEvents()
    assert win.tabs.dock(0).isClosed()
    win.tabs.show_space(0)                               # the launcher counterpart
    app.processEvents()
    assert not win.tabs.dock(0).isClosed()
    assert not win.tabs.dock(0).isFloating()
    assert len(list(win.dock_manager.floatingWidgets())) == 0
    win.close()


def test_nav_keeps_space_strip_hidden(app):
    """Navigating to the Chart space keeps the native space TAB strip hidden (the unified chart
    HEADER replaces it), verified after a setCurrentIndex nav."""
    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    win.tabs.setCurrentIndex(0)
    app.processEvents()
    app.processEvents()                       # the singleShot(0) re-hide
    area = win.tabs._resolve_area()
    assert area is not None
    assert all(not d.tabWidget().isVisible() for d in win.tabs._docks)
    tb = area.titleBar()
    assert getattr(tb, "is_chart_header", lambda: False)()   # header, not the raw tab strip
    win.close()


def test_reclaim_unfloats_a_restored_native_float(app, tmp_path):
    """A stale/legacy session blob can describe a dock as a native ADS float; restoreState
    resurrects it. _reclaim_floating_docks must pull it back so NO visible native float survives
    (covers spaces AND tools — the user's 'avoid the same issue for other tools')."""
    import PySide6QtAds as QtAds
    from vike_trader_app.ui.session import SessionState, save_session

    # 1) simulate a legacy blob: force the Chart space into a native float, capture the layout.
    w1 = MainWindow(session_path=None); w1.show(); app.processEvents()
    d = w1.tabs.dock(0)
    d.setFeatures(QtAds.CDockWidget.DockWidgetMovable | QtAds.CDockWidget.DockWidgetFloatable
                  | QtAds.CDockWidget.DockWidgetClosable)
    d.setFloating(); app.processEvents()
    assert len(list(w1.dock_manager.floatingWidgets())) == 1   # a native float now exists
    blob = bytes(w1.dock_manager.saveState().toHex()).decode("ascii")
    w1.close(); app.processEvents()

    # 2) write it at the CURRENT version so the migration KEEPS the blob -> reclaim is what saves us.
    sp = tmp_path / "s.json"
    save_session(str(sp), SessionState(dock_state_hex=blob))
    w2 = MainWindow(session_path=str(sp)); w2.show()
    for _ in range(30):
        app.processEvents()
    visible_floats = [c for c in w2.dock_manager.floatingWidgets()
                      if c.isVisible() and list(c.dockWidgets())]
    assert visible_floats == []                                 # reclaim un-floated it
    assert not w2.tabs.dock(0).isFloating()                     # Chart space is back, docked
    w2.close()


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

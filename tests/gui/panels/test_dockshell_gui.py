"""Offscreen tests for the ADS dock shell (SpaceDeck facade + unlockable panel docks)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("PySide6QtAds")

import PySide6QtAds as QtAds  # noqa: E402
from PySide6 import QtCore, QtWidgets  # noqa: E402

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


def test_four_charts_tile_2x2(app, monkeypatch):
    """MC-style live layout: open 4 floating chart WINDOWS and tile them 2x2 with the arrange verb
    (geometry math, no docking — the user rejected dock-tiling).

    (The old version also exercised ADS ``setAutoHide`` to pin panels to the edges. That path is
    retired — minimize is the custom left rail now (ui/minrail.py), covered by the rail tests — and
    ADS auto-hide with several containers is exactly the unstable mechanism the rail replaced, so
    those direct setAutoHide calls were a CI flake source. Dropped.)"""
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
    win.close()


def test_spacedeck_has_no_eager_spaces(app):
    """Chart-unify keystone: there is NO docked central chart space. SpaceDeck holds ZERO eager
    spaces (``_SPACE_ITEMS == []``), so ``count() == 0`` and there is no current space; the chart
    surface is the set of floating ChartWindowFrame peers instead. The deck is still a SpaceDeck
    and panels are still NOT hosted in the (now-empty) spaces area."""
    win = MainWindow()
    deck = win.tabs
    assert isinstance(deck, SpaceDeck)
    assert win._SPACE_ITEMS == []                   # no eager spaces after the keystone
    assert deck.count() == 0                         # ...so the deck is empty
    assert deck.currentIndex() == -1                 # nothing is current
    assert win.price is None                         # no focused chart frame on a bare window
    assert not deck.isAncestorOf(win.watchlist)      # panels are NOT in the spaces area
    win.close()


def test_panel_docks_are_dock_only(app):
    """Stage A1: panels are dock-only — closable / movable (tile+tab) / pinnable (auto-hide edge
    tabs), but NOT floatable (ADS tear-out floating is disabled; it produced broken chrome). With
    the central chart gone there is no pinned-in-place space dock to contrast against."""
    win = MainWindow()
    for dock in win._docks:
        assert isinstance(dock, QtAds.CDockWidget)
        feats = dock.features()
        assert feats & QtAds.CDockWidget.DockWidgetClosable
        assert feats & QtAds.CDockWidget.DockWidgetMovable
        assert feats & QtAds.CDockWidget.DockWidgetPinnable  # auto-hide pin (edge tabs)
        assert not (feats & QtAds.CDockWidget.DockWidgetFloatable)  # no tear-out float (A1)
    # no central chart space exists any more
    assert win.tabs.count() == 0
    assert win._chart_space_dock() is None
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


def test_panel_maximize_fills_and_restores_other_panels(app):
    """LOCK current behavior on the unified maximize: a side panel's □ maximizes it to FILL the
    workspace by hiding every OTHER live dock, and toggling restores them. With the central chart
    gone the 'other' dock is a second panel (there is nothing to park on the rail), so this also
    guards that a panel-maximize works with no chart present (was: chart parked on the rail)."""
    win = MainWindow(session_path=None)
    win.resize(1200, 800)
    win.show()
    QtWidgets.QApplication.processEvents()
    win._panel_btns["market"].setChecked(True)
    win._panel_btns["trades"].setChecked(True)
    QtWidgets.QApplication.processEvents()
    mkt = win._panel_dock_map["market"]
    trd = win._panel_dock_map["trades"]
    win._toggle_panel_maximize(mkt)
    QtWidgets.QApplication.processEvents()
    assert win._panel_maxed == mkt.objectName()
    assert win._maximized is mkt
    assert trd.isClosed()                                  # the other panel hidden
    assert not mkt.isClosed()                              # the maximized panel stays open
    win._toggle_panel_maximize(mkt)
    QtWidgets.QApplication.processEvents()
    assert win._panel_maxed is None
    assert win._maximized is None
    assert not trd.isClosed()                              # other panel back
    win.close()


def test_arrange_docks_tiles_two_docked_tools(app):
    """Chart-unify keystone: there is no privileged central-chart anchor any more, so the docked
    layout is tiled among the docks THEMSELVES via SpaceDeck.arrange_docks. Two docked tools tile
    Vertically (columns) with the second RIGHT of the first, Horizontally (rows) stacked BELOW it —
    each gets a distinct, non-overlapping cell. (This is the primitive the Window>Arrange verb
    delegates to for the docked layer now that the chart is no longer the anchor.)"""
    win = MainWindow(session_path=None)
    win.resize(1200, 800)
    win.show()
    QtWidgets.QApplication.processEvents()
    # two DOCKED tools (open as windows, then dock into the workspace)
    for k in ("screener", "journal"):
        win.open_tool(k)
        QtWidgets.QApplication.processEvents()
        win._redock_tool(k)
        QtWidgets.QApplication.processEvents()
    a = win._tool_docks.get("screener")
    b = win._tool_docks.get("journal")
    assert a is not None and b is not None

    def _alive(d):
        try:
            return not d.isClosed() and d.widget() is not None
        except RuntimeError:        # C++ object freed
            return False

    # Under parallel xdist a rare ADS teardown race can free one of these docks' C++ object between
    # redock and arrange (the `CDockWidget already deleted` class — upstream pyside6_qtads#31; NOT a
    # product bug, it can't happen in single-process use). arrange_docks itself is guarded against a
    # dead dock; here we just can't verify the GEOMETRY if a dock vanished, so skip that rare run
    # rather than flake the parallel suite.
    if not (_alive(a) and _alive(b)):
        pytest.skip("rare ADS teardown race freed a tool dock under parallel xdist (upstream #31)")

    def tl(dock):
        return dock.dockAreaWidget().mapTo(win.dock_manager, QtCore.QPoint(0, 0))

    win.tabs.arrange_docks([a, b], "columns")      # Tile Vertically -> side by side
    QtWidgets.QApplication.processEvents()
    assert tl(b).x() > tl(a).x() + 50              # journal now RIGHT of screener

    win.tabs.arrange_docks([a, b], "rows")         # Tile Horizontally -> stacked
    QtWidgets.QApplication.processEvents()
    assert tl(b).y() > tl(a).y() + 50              # journal now BELOW screener
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


def test_tab_changed_after_panel_open_does_not_crash(app):
    """Regression-keeper for the re-entrancy guard: tabbing a panel used to recurse to a stack
    overflow via the center area's currentChanged. The central spaces area is gone (no chart
    space), but the guard + an inert _on_tab_changed must still keep the shell alive after a panel
    is opened and the deck is poked."""
    win = MainWindow()
    win._panel_btns["market"].setChecked(True)  # open Market watch
    app.processEvents()
    win.tabs.setCurrentIndex(0)        # no-op on the empty deck
    win._on_tab_changed(0)             # must not recurse / crash
    assert win.tabs.count() == 0       # still alive, deck still empty
    assert not win._market_dock.isClosed()
    win.close()


def test_out_of_range_saved_space_does_not_crash_startup(app, tmp_path):
    """Chart-unify keystone: there are no spaces, so a saved (legacy) space index can no longer
    select anything. A stale ``space`` value in the session blob — from an old session saved when
    spaces still existed — must NOT crash startup; the deck simply comes up empty (no current
    space, count 0)."""
    import json

    path = tmp_path / "session.json"
    first = MainWindow(session_path=str(path))
    first.close()
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["space"] = 999                      # simulate a removed/reordered space from an old session
    path.write_text(json.dumps(raw), encoding="utf-8")

    second = MainWindow(session_path=str(path))    # must not raise
    assert second.tabs.count() == 0                # no spaces exist to clamp to
    assert second.tabs.currentIndex() == -1        # nothing current
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
    assert VikeDockTitleBar._rolled is False
    assert VikeDockTitleBar._roll_maxh is None


# --- panel window verbs: ─ minimize-to-rail + □ maximize (AmiBroker-style, never the OS window) ---


def test_panel_max_hides_then_restores_other_panels_not_os_minimize(app):
    """Chart-unify keystone successor to the old chart-header □ test: maximizing one panel hides the
    OTHER open panels (it must NOT minimize the OS window), and toggling again restores exactly the
    panels that were open. (The central-chart header is gone; the unified maximize now anchors on a
    panel.)"""
    win = MainWindow(session_path=None)
    win.show()
    win._panel_btns["market"].setChecked(True)
    win._panel_btns["trades"].setChecked(True)
    app.processEvents()
    open_before = {k for k, d in win._panel_dock_map.items() if not d.isClosed()}
    assert {"market", "trades"} <= open_before
    mkt = win._panel_dock_map["market"]
    others = open_before - {"market"}
    win._toggle_panel_maximize(mkt)
    app.processEvents()
    assert not win.isMinimized()
    assert all(win._panel_dock_map[k].isClosed() for k in others)
    assert not mkt.isClosed()
    win._toggle_panel_maximize(mkt)
    app.processEvents()
    assert {k for k, d in win._panel_dock_map.items() if not d.isClosed()} >= open_before
    win.close()


def test_tool_launcher_opens_dock_despite_checked_arg(app):
    """Regression: a tool launcher icon connects to QAction.triggered, which emits a `checked`
    bool. A ``lambda k=key:`` captured that bool into ``k`` -> open_tool(False) -> KeyError, so
    the icon silently did nothing. ``lambda *_a, k=key:`` absorbs it. Triggering the Screener
    launcher (emits checked=False, like a real click) must open the Screener tool — MT-style, as
    its own window."""
    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    act = next((a for a in win.topbar.launchers.actions()
                if "Screener" in (a.toolTip() or a.text())), None)
    assert act is not None, "no Screener launcher"
    act.trigger()                              # emits triggered(checked=False) — the real-click path
    app.processEvents()
    assert win._tool_frames.get("screener") is not None   # opened as its own window
    win.close()


def test_panel_min_button_parks_on_left_rail_via_real_click(app):
    """The panel ─ button HIDES the dock and parks a vertical restore tab on the custom left rail
    (AmiBroker-style). Replaces ADS auto-hide (whose fixed-width slide-out flyout left empty space on
    restore). A real ─ click must hide + rail; restoring brings the panel back full-size.
    (Regression-keeper: _panel_min once read a stale cached area and did nothing on a real click.)"""
    win = MainWindow(session_path=None)
    win.show()
    win._panel_btns["market"].setChecked(True)
    app.processEvents()
    app.processEvents()
    assert not win._market_dock.isClosed()
    key = win._market_dock.objectName()
    tb = win._market_dock.dockAreaWidget().titleBar()
    tb._header.button("min").click()             # the real ─ click path (used to be a no-op)
    app.processEvents()
    assert win._market_dock.isClosed()           # hidden
    assert win._min_rail.has(key)                # parked on the left rail
    win._restore_panel_from_rail(win._market_dock)
    app.processEvents()
    assert not win._market_dock.isClosed()       # restored full-size (no empty space)
    assert not win._min_rail.has(key)            # rail tab dropped
    win.close()


def test_panel_close_button_closes_via_real_click(app):
    """Companion regression: the panel ✕ button also went through the stale _cur_dock(); a real
    click must actually close the dock."""
    win = MainWindow(session_path=None)
    win.show()
    win._panel_btns["trades"].setChecked(True)
    app.processEvents()
    app.processEvents()
    assert not win._trades_dock.isClosed()
    tb = win._trades_dock.dockAreaWidget().titleBar()
    tb._header.button("close").click()
    app.processEvents()
    assert win._trades_dock.isClosed()
    win.close()


# NOTE: the old `test_reclaim_unpins_autohidden_docks` was REMOVED — it created a real ADS auto-hide
# container (`setAutoHide(True)`), which is a deterministic teardown use-after-free (0xC0000409,
# upstream mborgerson/pyside6_qtads#31). Under parallel xdist that corrupted the worker's ADS state and
# surfaced as a crash during a LATER test's teardown — the sole blocker to running panels parallel.
# The app never creates auto-hide containers (minimize = custom MinimizedRail; the dead
# addAutoHideDockWidget path was removed in #181), and the v4 session migration drops pre-rail blobs,
# so the scenario it guarded is unreachable — the matching un-pin pass in _reclaim_floating_docks was
# dropped with it. With this gone the suite creates ZERO auto-hide containers, so panels run parallel.


def test_panel_titlebar_has_no_native_chrome_leak(app):
    """Regression: ADS re-shows its native dockAreaCloseButton on a deferred tick AFTER our
    refresh_native_hidden() child.hide() ran, leaking a 2nd ✕ onto the Market-Watch bar next to our
    own ─ □ ✕ (measured: fresh panel had dockAreaCloseButton VISIBLE). _set_native_hidden() now
    drives ADS's setShowInTitleBar(False) so the hide sticks — NO native title-bar button visible."""
    win = MainWindow(session_path=None)
    win.show()
    win._panel_btns["market"].setChecked(True)
    app.processEvents()
    app.processEvents()
    tb = win._market_dock.dockAreaWidget().titleBar()
    leaking = [b.objectName() for b in tb.findChildren(QtAds.CTitleBarButton) if b.isVisible()]
    assert leaking == [], f"native title-bar chrome leaked onto the panel: {leaking}"
    win.close()


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


def test_no_native_space_float_entry_points(app):
    """Native ADS floating is retired AND the central chart space is gone: ``float_space`` is
    removed, the deck holds zero spaces, and a bare empty workspace has NO native floating
    containers — charts float only via chartwin frames."""
    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    assert not hasattr(win.tabs, "float_space")          # the native-float entry point is gone
    assert win.tabs.count() == 0                          # no spaces to float
    assert len(list(win.dock_manager.floatingWidgets())) == 0
    win.close()


def test_empty_deck_nav_is_inert_and_strip_stays_hidden(app):
    """Chart-unify keystone: with zero spaces, navigating the deck is inert and never raises —
    setCurrentIndex on the empty deck no-ops, hide_space_tabs is a safe no-op, and no native
    space tab strip exists (there are no space docks to carry one)."""
    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    win.tabs.setCurrentIndex(0)               # no-op on an empty deck (no dock at index 0)
    win.tabs.hide_space_tabs()                # safe no-op with no spaces
    app.processEvents()
    app.processEvents()                       # the singleShot(0) re-hide
    assert win.tabs._docks == []              # no space docks
    assert win.tabs.currentIndex() == -1
    win.close()


def test_arrange_tiles_open_tool_windows(app):
    """MT-style: tools open as their OWN windows; Window ▸ Arrange tiles those tool windows into a
    grid of distinct, non-overlapping rectangles (the same geometry path as chart windows)."""
    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    for k in ("screener", "journal", "alerts"):
        win.open_tool(k)
        app.processEvents()
    frames = [win._tool_frames.get(k) for k in ("screener", "journal", "alerts")]
    assert all(f is not None for f in frames)     # each tool is its own window, not a dock
    win._arrange_chart_windows("grid")            # the Window ▸ Arrange All path (tiles frames)
    app.processEvents()
    geos = [f.geometry() for f in frames]
    assert len({(g.x(), g.y(), g.width(), g.height()) for g in geos}) == 3
    for i in range(3):
        for j in range(i + 1, 3):
            assert not geos[i].intersects(geos[j])
    win.close()


def test_chart_dock_undock_round_trip(app):
    """A chart window 'Dock into workspace' becomes a clean ADS dock (NOT a native float) hosting
    the SAME live doc; its ⧉ tears it back out to a clean window; a real close unregisters the doc.
    Charts are now symmetric with tools — no native CFloatingDockContainer at any step."""
    from vike_trader_app.ui.chartwin import ChartWindowFrame

    win = MainWindow(session_path=None)
    win.show()
    app.processEvents()
    doc = win._new_chart_document("BTCUSDT", network=False)
    app.processEvents()
    frame = win._frame_of(doc)
    assert frame in win._chart_frames

    # Dock into workspace -> clean ADS dock, same live doc, no native float
    win._redock_chart(frame)
    app.processEvents()
    assert frame not in win._chart_frames
    names = [n for n, d in win._chart_docks.items() if d.widget() is doc]
    assert names and doc in win._doc_widgets
    assert len(list(win.dock_manager.floatingWidgets())) == 0

    # Tear back out -> clean window, doc still live
    win._detach_chart_dock(names[0])
    app.processEvents()
    assert names[0] not in win._chart_docks
    assert isinstance(win._frame_of(doc), ChartWindowFrame)
    assert doc in win._doc_widgets
    assert not win._chart_detaching

    # Redock + real close -> doc unregistered (full teardown)
    win._redock_chart(win._frame_of(doc))
    app.processEvents()
    name2 = [n for n, d in win._chart_docks.items() if d.widget() is doc][0]
    win._chart_docks[name2].closeDockWidget()
    app.processEvents()
    assert name2 not in win._chart_docks
    assert doc not in win._doc_widgets
    win.close()


def test_reclaim_unfloats_a_restored_native_float(app, tmp_path):
    """A stale/legacy session blob can describe a dock as a native ADS float; restoreState
    resurrects it. _reclaim_floating_docks must pull it back so NO visible native float survives.
    (The central chart space is gone, so a side PANEL dock stands in here — the reclaim must still
    cover panels AND tools, the user's 'avoid the same issue for other tools'.)"""
    import PySide6QtAds as QtAds
    from vike_trader_app.ui.session import SessionState, save_session

    # 1) simulate a legacy blob: force a PANEL dock into a native float, capture the layout.
    w1 = MainWindow(session_path=None); w1.show()
    w1._panel_btns["market"].setChecked(True)
    app.processEvents(); app.processEvents()
    d = w1._market_dock
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
    assert not w2._market_dock.isFloating()                     # panel is back, docked
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

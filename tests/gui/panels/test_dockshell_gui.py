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


def test_four_charts_tile_and_autohide_to_edges(app, monkeypatch):
    """AmiBroker-style live layout (regression for the docking core): open 4 chart documents,
    tile them 2x2 (distinct dock areas, not tabbed), auto-hide Market watch to the LEFT edge and
    Trades to the BOTTOM edge, then reveal one again."""
    bars = [Bar(ts=i * 60_000, open=100 + i, high=101 + i, low=99 + i, close=100 + i)
            for i in range(30)]
    monkeypatch.setattr(chartdoc, "load_symbol_bars", lambda *a, **k: LoadResult(list(bars)))

    win = MainWindow(session_path=None)
    docs = [win._new_chart_document(s, "1h")
            for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT")]
    assert win.tabs.document_count() == 4

    # tile 2x2 by splitting off the first doc's area (they start tabbed in the centre)
    mgr = win.dock_manager
    d = [win.tabs._documents[i] for i in range(4)]
    mgr.addDockWidget(QtAds.RightDockWidgetArea, d[1], d[0].dockAreaWidget())
    mgr.addDockWidget(QtAds.BottomDockWidgetArea, d[2], d[0].dockAreaWidget())
    mgr.addDockWidget(QtAds.BottomDockWidgetArea, d[3], d[1].dockAreaWidget())
    app.processEvents()
    # the four charts now live in distinct dock areas (a real tiling, not one tab stack)
    areas = {id(dock.dockAreaWidget()) for dock in d}
    assert len(areas) == 4

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
    assert deck.count() == len(win._RAIL_ITEMS)
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
    win = MainWindow()
    got = []
    win.tabs.currentChanged.connect(got.append)
    idx = win.tabs.indexOf(win.screener)
    win.tabs.setCurrentIndex(idx)
    assert got and got[-1] == idx
    assert win._rail_group.button(idx).isChecked()          # rail mirrors the deck
    assert win.windowTitle().endswith("Screener")           # title bar tracks the space
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
    assert win.tabs.count() == len(win._RAIL_ITEMS)  # still alive, spaces intact
    win.close()


def test_out_of_range_saved_space_clamps_and_resyncs(app, tmp_path):
    """A saved space index past the end (a build dropped a space) clamps to the last space and
    still re-syncs the rail/title, rather than leaving the shell disconnected."""
    import json

    path = tmp_path / "session.json"
    first = MainWindow(session_path=str(path))
    first.close()
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["space"] = 999                      # simulate a removed/reordered space
    path.write_text(json.dumps(raw), encoding="utf-8")

    second = MainWindow(session_path=str(path))
    idx = second.tabs.currentIndex()
    assert idx == second.tabs.count() - 1   # clamped to the last valid space
    assert second._rail_group.button(idx).isChecked()       # rail re-synced (not stuck on 0)
    assert second.windowTitle().endswith(second._RAIL_ITEMS[idx][1])
    second.close()


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

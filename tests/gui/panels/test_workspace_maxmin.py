"""Comprehensive multi-layout regression net for workspace MAXIMIZE / MINIMIZE / ARRANGE.

Drives the REAL title-bar + menu actions (QTest clicks / real QActions) and asserts only OBSERVABLE
outcomes — maximize glyph (□/❐), dock visibility, left-rail tabs, fill geometry, and restore — NOT
internal flags. That makes it the safety net for the max/min state-machine unification: whatever the
internals become, clicking the buttons must keep producing these outcomes. Run offscreen (CI-safe);
geometry is computed by ADS so fill/position assertions are meaningful even without a display.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("PySide6QtAds")

from PySide6 import QtCore, QtWidgets  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from vike_trader_app.ui.app import MainWindow  # noqa: E402

LB = QtCore.Qt.LeftButton


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _pe(n=6):
    for _ in range(n):
        QtWidgets.QApplication.processEvents()


def _win(*open_panels, size=(1200, 800)):
    """A shown MainWindow with exactly `open_panels` open (everything else closed)."""
    w = MainWindow(session_path=None)
    w.resize(*size)
    w.show()
    _pe(8)
    for key in list(w._panel_dock_map):
        w._panel_btns[key].setChecked(key in open_panels)
    _pe(8)
    return w


def _chart_btn(win, key):
    h = win.tabs.header_widget()
    return h.button(key) if (h is not None and hasattr(h, "button")) else None


def _panel_btn(dock, key):
    try:
        h = dock.dockAreaWidget().titleBar()._header
        return h.button(key) if hasattr(h, "button") else None
    except (RuntimeError, AttributeError):
        return None


def _click(b):
    assert b is not None, "button missing"
    QTest.mouseClick(b, LB, QtCore.Qt.NoModifier, b.rect().center())
    _pe()


def _click_rail(win, key):
    item = win._min_rail._items.get(key)
    assert item is not None, f"rail tab {key!r} missing"
    _click(item[1])


def _fills(dock, win, tol=44):
    """The dock's area spans (nearly) the whole workspace in BOTH dims (tol covers the left rail
    strip + 1px borders + the 2px gap)."""
    a = dock.dockAreaWidget()
    return (a.width() >= win.dock_manager.width() - tol
            and a.height() >= win.dock_manager.height() - tol)


# ---------------------------------------------------------------- chart maximize

@pytest.mark.parametrize("panels", [("market",), ("market", "trades")])
def test_chart_maximize_hides_panels_fills_and_restores(app, panels):
    win = _win(*panels)
    chart = win._chart_space_dock()
    mb = _chart_btn(win, "max")
    _click(mb)
    assert mb.text() == "❐"                                   # glyph -> restore
    assert _fills(chart, win)                                 # chart fills the workspace
    for k in panels:
        assert win._panel_dock_map[k].isClosed()              # every side panel hidden
    _click(_chart_btn(win, "max"))
    assert _chart_btn(win, "max").text() == "□"               # glyph -> maximize
    for k in panels:
        assert not win._panel_dock_map[k].isClosed()          # panels back
    win.close()


def test_chart_maximize_is_noop_with_no_panels(app):
    """LOCK current behavior: with no side panel open the docked chart already fills the workspace,
    so the box is a deliberate no-op (stays □) rather than a false maximized state."""
    win = _win()                                              # no panels
    chart = win._chart_space_dock()
    mb = _chart_btn(win, "max")
    assert _fills(chart, win)                                 # already full
    _click(mb)
    assert mb.text() == "□"                                   # no-op: glyph unchanged
    win.close()


# ---------------------------------------------------------------- panel maximize

@pytest.mark.parametrize("panels", [("market",), ("market", "trades")])
def test_panel_maximize_fills_parks_chart_on_rail_and_restores(app, panels):
    win = _win(*panels)
    mkt = win._panel_dock_map["market"]
    _click(_panel_btn(mkt, "max"))
    assert _panel_btn(mkt, "max").text() == "❐"               # glyph -> restore
    assert _fills(mkt, win)                                   # panel fills the workspace
    assert win._chart_space_dock().isClosed()                 # chart hidden
    assert win._min_rail.has("__central_chart__")             # chart parked on the rail
    for k in panels:
        if k != "market":
            assert win._panel_dock_map[k].isClosed()          # other panels hidden too
    _click(_panel_btn(mkt, "max"))
    assert _panel_btn(mkt, "max").text() == "□"               # glyph -> maximize
    assert not win._chart_space_dock().isClosed()             # chart back
    assert not win._min_rail.has("__central_chart__")         # rail cleared
    for k in panels:
        assert not win._panel_dock_map[k].isClosed()          # all panels back
    win.close()


def test_panel_maximize_restore_via_rail_chart_tab(app):
    """The parked 'Chart' rail tab un-maximizes the panel (brings chart + panel back)."""
    win = _win("market")
    mkt = win._panel_dock_map["market"]
    _click(_panel_btn(mkt, "max"))
    assert win._min_rail.has("__central_chart__")
    _click_rail(win, "__central_chart__")                     # click the parked Chart tab
    assert not win._chart_space_dock().isClosed()             # chart restored
    assert not mkt.isClosed()                                 # panel restored
    assert not win._min_rail.has("__central_chart__")
    assert _panel_btn(mkt, "max").text() == "□"               # glyph synced back
    win.close()


# ---------------------------------------------------------------- minimize -> rail

@pytest.mark.parametrize("panels", [("market",), ("market", "trades")])
def test_chart_minimize_parks_on_rail_and_restores(app, panels):
    win = _win(*panels)
    _click(_chart_btn(win, "min"))
    assert win._chart_space_dock().isClosed()                 # chart hidden
    assert win._min_rail.has("__central_chart__")             # parked on rail
    _click_rail(win, "__central_chart__")
    assert not win._chart_space_dock().isClosed()             # restored
    assert not win._min_rail.has("__central_chart__")
    win.close()


def test_panel_minimize_parks_on_rail_and_restores(app):
    win = _win("market")
    mkt = win._panel_dock_map["market"]
    key = mkt.objectName()
    _click(_panel_btn(mkt, "min"))
    assert mkt.isClosed()                                     # panel hidden
    assert win._min_rail.has(key)                             # parked on rail
    _click_rail(win, key)
    assert not mkt.isClosed()                                 # restored
    assert not win._min_rail.has(key)
    win.close()


# ---------------------------------------------------------------- arrange (docked)

def test_arrange_horizontally_then_vertically_retiles_docked(app):
    win = _win("market")
    mkt = win._panel_dock_map["market"]
    chart = win._chart_space_dock()

    def tl(d):
        return d.dockAreaWidget().mapTo(win.dock_manager, QtCore.QPoint(0, 0))

    win._arrange_chart_windows("rows")                        # Horizontally -> stacked
    _pe()
    assert tl(mkt).y() > tl(chart).y() + 50                   # MW below the chart
    win._arrange_chart_windows("columns")                     # Vertically -> side by side
    _pe()
    assert tl(mkt).x() > tl(chart).x() + 50                   # MW right of the chart
    win._arrange_chart_windows("grid")                        # All -> still side by side (grid of 2)
    _pe()
    assert not chart.dockAreaWidget().geometry().intersects(mkt.dockAreaWidget().geometry()) or \
        tl(mkt) != tl(chart)
    win.close()


# ---------------------------------------------------------------- arrange (floating)

def test_arrange_tiles_floating_windows_without_overlap(app):
    win = _win("market")
    for _ in range(3):
        win._new_chart_document("BTCUSDT", "1m")
    _pe(8)
    win._arrange_chart_windows("grid")
    _pe()
    fr = [f for f in win._chart_frames if not f.is_detached() and f.isVisible()]
    assert len(fr) == 3
    for i in range(len(fr)):
        for j in range(i + 1, len(fr)):
            assert not fr[i].geometry().intersects(fr[j].geometry())   # grid: no overlap
    win._arrange_chart_windows("cascade")
    _pe()
    xs = [f.geometry().x() for f in fr]
    assert len(set(xs)) > 1                                    # cascade: staggered
    win.close()

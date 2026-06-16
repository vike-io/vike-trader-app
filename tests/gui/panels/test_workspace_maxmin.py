"""Comprehensive multi-layout regression net for workspace MAXIMIZE / MINIMIZE / ARRANGE.

Drives the REAL title-bar + menu actions (QTest clicks / real QActions) and asserts only OBSERVABLE
outcomes — maximize glyph (□/❐), dock visibility, left-rail tabs, fill geometry, and restore — NOT
internal flags. That makes it the safety net for the max/min state-machine unification: whatever the
internals become, clicking the buttons must keep producing these outcomes. Run offscreen (CI-safe);
geometry is computed by ADS so fill/position assertions are meaningful even without a display.

Post chart-unify keystone: there is NO docked central chart any more (``_chart_space_dock()`` is
``None``, ``tabs.count() == 0``, ``win.price`` starts ``None``). Every chart is a floating
``ChartWindowFrame`` opened via ``_new_chart_document`` and tiled by ``_arrange_chart_windows``. So
the surviving max/min behaviour lives entirely on the SIDE PANELS: ``_maximize_dock(panel_dock)``
fills the workspace by hiding the OTHER panels (no chart is ever parked on the rail), and a panel's
─ parks it on the left rail under its OWN key. The old central-chart maximize/minimize/rail-park
tests describe behaviour that no longer exists and were dropped; the panel equivalents below carry
the intent forward.
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


# ---------------------------------------------------------------- panel maximize

@pytest.mark.parametrize("panels", [("market", "trades"), ("market", "trades", "movers")])
def test_panel_maximize_hides_other_panels_fills_and_restores(app, panels):
    """A panel's □ fills the workspace by hiding every OTHER live panel (there is no central chart to
    park on the rail any more), and toggling it back (□↔❐) restores them all. Needs ≥2 panels open —
    with a single panel the box is a deliberate no-op (covered below)."""
    win = _win(*panels)
    mkt = win._panel_dock_map["market"]
    _click(_panel_btn(mkt, "max"))
    assert _panel_btn(mkt, "max").text() == "❐"               # glyph -> restore
    assert _fills(mkt, win)                                   # panel fills the workspace
    for k in panels:
        if k != "market":
            assert win._panel_dock_map[k].isClosed()          # other panels hidden
    _click(_panel_btn(mkt, "max"))
    assert _panel_btn(mkt, "max").text() == "□"               # glyph -> maximize
    for k in panels:
        assert not win._panel_dock_map[k].isClosed()          # all panels back
    win.close()


def test_panel_maximize_is_noop_with_single_panel(app):
    """LOCK current behavior: with only one panel open it already fills the workspace, so the box is
    a deliberate no-op (stays □) rather than landing in a false maximized state."""
    win = _win("market")                                      # one panel
    mkt = win._panel_dock_map["market"]
    mb = _panel_btn(mkt, "max")
    assert _fills(mkt, win)                                   # already full
    _click(mb)
    assert _panel_btn(mkt, "max").text() == "□"               # no-op: glyph unchanged
    assert not mkt.isClosed()                                 # still open
    win.close()


# ---------------------------------------------------------------- minimize -> rail

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


# ---------------------------------------------------------------- arrange (rows vs columns)

def test_arrange_horizontally_then_vertically_retiles_charts(app):
    """Charts are floating windows now (no docked central chart), so 'Tile Horizontally' (rows)
    stacks them vertically (shared x, marching y) and 'Tile Vertically' (columns) lays them side by
    side (shared y, marching x). Re-arranging re-tiles in place."""
    win = _win("market")
    for _ in range(2):
        win._new_chart_document("BTCUSDT", "1m", network=False)
    _pe(8)
    fr = [f for f in win._chart_frames if not f.is_detached() and f.isVisible()]
    assert len(fr) == 2

    win._arrange_chart_windows("rows")                        # Horizontally -> stacked
    _pe()
    g = [f.geometry() for f in fr]
    assert g[0].x() == g[1].x()                               # same column
    assert g[1].y() > g[0].y() + 50                           # second BELOW the first

    win._arrange_chart_windows("columns")                     # Vertically -> side by side
    _pe()
    g = [f.geometry() for f in fr]
    assert g[0].y() == g[1].y()                               # same row
    assert g[1].x() > g[0].x() + 50                           # second RIGHT of the first
    win.close()


# ---------------------------------------------------------------- arrange (floating)

def test_arrange_tiles_floating_windows_without_overlap(app):
    win = _win("market")
    for _ in range(3):
        win._new_chart_document("BTCUSDT", "1m", network=False)
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


def test_arrange_with_floating_charts_leaves_docked_panels_alone(app):
    """Regression: with floating chart windows open (Go > New chart), Arrange tiles THEM and must
    NOT churn the docked panels behind them — it used to also re-dock the panels and crash on a
    deleted panel dock. The panel layout must be untouched and Arrange must not raise."""
    win = _win("market")
    for _ in range(3):
        win._new_chart_document("BTCUSDT", "1m", network=False)
    _pe(8)
    mkt = win._panel_dock_map["market"]
    before = mkt.dockAreaWidget().mapTo(win.dock_manager, QtCore.QPoint(0, 0))
    win._arrange_chart_windows("grid")        # must not raise; floating frames are the target
    _pe()
    after = mkt.dockAreaWidget().mapTo(win.dock_manager, QtCore.QPoint(0, 0))
    assert (before.x(), before.y()) == (after.x(), after.y())   # panel layout untouched
    fr = [f for f in win._chart_frames if not f.is_detached() and f.isVisible()]
    assert len(fr) == 3
    for i in range(len(fr)):
        for j in range(i + 1, len(fr)):
            assert not fr[i].geometry().intersects(fr[j].geometry())
    win.close()

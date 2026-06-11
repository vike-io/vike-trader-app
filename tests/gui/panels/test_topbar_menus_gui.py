"""S2-S4 shell chrome: top command bar (classify + routing), hamburger menus, workspace
recents, copy/paste window, and the documents-only center tab strip."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("PySide6QtAds")

from PySide6 import QtWidgets  # noqa: E402

import vike_trader_app.ui.chartdoc as chartdoc  # noqa: E402
from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.ui.app import MainWindow  # noqa: E402
from vike_trader_app.ui.dataload import LoadResult  # noqa: E402
from vike_trader_app.ui.topbar import classify  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def _synthetic_load(monkeypatch):
    bars = [Bar(ts=i * 60_000, open=100 + i, high=101 + i, low=99 + i, close=100 + i)
            for i in range(30)]
    monkeypatch.setattr(chartdoc, "load_symbol_bars", lambda *a, **k: LoadResult(list(bars)))


# --- classify (the box's resolver) ------------------------------------------------------------

def test_classify_intervals_beat_symbols():
    assert classify("5m") == ("interval", "5m")
    assert classify("1D") == ("interval", "1d")


def test_classify_ticker_shapes_are_symbols_not_commands():
    assert classify("ETHUSDT", ["Go to News"]) == ("symbol", "ETHUSDT")
    assert classify("btcusdt") == ("symbol", "BTCUSDT")
    assert classify("IB:AAPL") == ("symbol", "IB:AAPL")


def test_classify_phrases_run_commands():
    kind, label = classify("go to news", ["Go to News", "Save workspace as…"])
    assert (kind, label) == ("command", "Go to News")


def test_classify_garbage_is_none():
    assert classify("!!! ???", ["Go to News"]) == ("none", "")


# --- the bar + menus --------------------------------------------------------------------------

def test_topbar_exists_with_menu_and_launchers(app):
    win = MainWindow(session_path=None)
    assert [a.text() for a in win.topbar.menubar.actions()] == \
        ["File", "Go", "Window", "Help"]
    assert len(win.topbar.launchers.actions()) == 7   # new chart + 6 space launchers
    win.close()


def test_topbar_symbol_and_interval_route(app, monkeypatch):
    """The bar routes to the SHELL's load/interval entry points (the data outcome itself is
    environment-dependent — CI has no parquet cache — so assert the routing contract)."""
    win = MainWindow(session_path=None)
    calls = []
    monkeypatch.setattr(win, "_load_symbol", lambda sym, interval=None: calls.append(sym))
    monkeypatch.setattr(win, "_on_interval_chosen", lambda iv: calls.append(iv))
    win.topbar.box.setText("ETHUSDT")
    win.topbar._submit()
    assert calls == ["ETHUSDT"]
    assert win.tabs.currentIndex() == 0               # routed to (and showed) the Chart space
    win.topbar.box.setText("5m")
    win.topbar._submit()
    assert calls == ["ETHUSDT", "5m"]
    win.close()


def test_topbar_symbol_routes_to_focused_document(app):
    win = MainWindow(session_path=None)
    doc = win._new_chart_document("SOLUSDT", "4h")
    win.topbar.box.setText("ADAUSDT")
    win.topbar._submit()
    assert doc.symbol == "ADAUSDT"                    # the doc, not the Chart space, got it
    win.close()


def test_menus_populate_on_show(app):
    win = MainWindow(session_path=None)
    for sub in [a.menu() for a in win.topbar.menubar.actions()]:
        sub.aboutToShow.emit()                        # triggers the fill
        assert sub.actions(), sub.title()
    win.close()


def test_space_tabs_and_strip_hidden_windows_float(app):
    """S7: no tab strip at all — space tabs hidden AND the area title bar reclaimed; charts
    float as windows over the workspace instead of tabbing."""
    win = MainWindow(session_path=None)
    win.show()
    QtWidgets.QApplication.processEvents()
    win._new_chart_document("ETHUSDT", "1h")
    QtWidgets.QApplication.processEvents()
    QtWidgets.QApplication.processEvents()            # the singleShot(0) re-hide
    assert all(not d.tabWidget().isVisible() for d in win.tabs._docks)
    area = win.tabs._resolve_area()
    assert not area.titleBar().isVisible()            # the whole strip row is gone
    assert len(win._chart_frames) == 1
    assert win._chart_frames[0].isVisible()
    win.close()


# --- S4: recents + copy/paste window ----------------------------------------------------------

def test_recents_recorded_and_persisted(app, tmp_path):
    path = tmp_path / "session.json"
    first = MainWindow(session_path=str(path))
    first._apply_workspace("Research")
    first._apply_workspace("Trading")
    assert first._workspaces.recents()[:2] == ["Trading", "Research"]
    first.close()
    second = MainWindow(session_path=str(path))
    assert second._workspaces.recents()[:2] == ["Trading", "Research"]
    second.close()


def test_copy_paste_window_roundtrip(app):
    win = MainWindow(session_path=None)
    doc = win._new_chart_document("SOLUSDT", "4h")
    doc._set_link_group(2)
    win._copy_active_document()
    payload = json.loads(QtWidgets.QApplication.clipboard().text())["vike_window"]
    assert payload["symbol"] == "SOLUSDT" and payload["interval"] == "4h"
    win._paste_document()
    assert len(win._chart_frames) == 2
    pasted = win._doc_widgets[-1]
    assert (pasted.symbol, pasted.interval, pasted.link_group) == ("SOLUSDT", "4h", 2)
    win.close()


def test_paste_ignores_foreign_clipboard(app):
    win = MainWindow(session_path=None)
    QtWidgets.QApplication.clipboard().setText("not a window")
    win._paste_document()
    assert win._chart_frames == []
    win.close()

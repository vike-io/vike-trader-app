"""Offscreen tests for multi-instance chart documents (Phase 2): ChartDocument, LiveHub,
and the MainWindow wiring (open / current / close / session persist+restore).

load_symbol_bars is monkeypatched to synthetic bars so nothing hits the cache or network;
the live round-robin is disabled suite-wide (VIKE_DISABLE_LIVE in tests/conftest.py).
"""

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
from vike_trader_app.ui.chartdoc import ChartDocument, LiveHub  # noqa: E402
from vike_trader_app.ui.dataload import LoadResult  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _bars(n=60, base=100.0):
    return [Bar(ts=i * 60_000, open=base + i, high=base + 1 + i, low=base - 1 + i,
                close=base + i) for i in range(n)]


@pytest.fixture
def _synthetic_load(monkeypatch):
    """Make ChartDocument.load deterministic + offline (synthetic bars, no cache/network)."""
    monkeypatch.setattr(chartdoc, "load_symbol_bars",
                        lambda *a, **k: LoadResult(_bars()))


# --- ChartDocument --------------------------------------------------------------------------


def test_document_loads_own_symbol(app, _synthetic_load):
    doc = ChartDocument("ETHUSDT", "4h")
    assert doc.load()
    assert doc.symbol == "ETHUSDT" and doc.interval == "4h"
    assert doc.title() == "ETHUSDT · 4h"
    assert doc.chart._bars  # bars landed on the chart


def test_document_state_round_trip(app, _synthetic_load):
    doc = ChartDocument("BTCUSDT", "1h")
    doc.load()
    doc.chart.add_indicator("rsi", params={"period": 21})
    st = doc.state()
    assert st["symbol"] == "BTCUSDT" and st["interval"] == "1h"
    assert [i["name"] for i in st["indicators"]] == ["rsi"]

    clone = ChartDocument(st["symbol"], st["interval"])
    clone.load()
    clone.apply_state(st)
    assert [i.name for i in clone.chart._indicators.values()] == ["rsi"]


def test_document_merge_live_appends(app, _synthetic_load):
    doc = ChartDocument("BTCUSDT", "1m")
    doc.load()
    n0 = len(doc._bars)
    nxt = doc._bars[-1].ts + 60_000
    doc.merge_live([Bar(ts=nxt, open=200, high=201, low=199, close=200)])
    assert len(doc._bars) == n0 + 1


def test_ensure_loaded_tops_up_cache_only_once(app, monkeypatch):
    """A cache-only (restore) load leaves the doc un-"loaded", so the first ensure_loaded does
    one NETWORK top-up; subsequent calls (and a network load) no-op."""
    calls = []
    monkeypatch.setattr(chartdoc, "load_symbol_bars",
                        lambda *a, **k: calls.append(k.get("network")) or LoadResult(_bars()))
    doc = ChartDocument("BTCUSDT", "1m")
    doc.load(network=False)          # restore-style cache-only load -> _loaded stays False
    doc.ensure_loaded()              # first focus -> one network top-up
    doc.ensure_loaded()              # now loaded -> no-op
    assert calls == [False, True]


def test_network_load_marks_loaded(app, monkeypatch):
    calls = []
    monkeypatch.setattr(chartdoc, "load_symbol_bars",
                        lambda *a, **k: calls.append(k.get("network")) or LoadResult(_bars()))
    doc = ChartDocument("BTCUSDT", "1m")
    doc.load()                       # network load (the "+New chart" path) -> _loaded True
    doc.ensure_loaded()              # must NOT reload
    assert calls == [True]


def test_failed_network_load_does_not_latch_loaded(app, monkeypatch):
    """A network load that returns no bars (bad symbol / offline, res.ok False) must NOT latch
    _loaded — otherwise the doc is stuck empty forever and ensure_loaded never retries."""
    results = [LoadResult([]), LoadResult(_bars())]   # 1st load fails, retry succeeds
    monkeypatch.setattr(chartdoc, "load_symbol_bars", lambda *a, **k: results.pop(0))
    doc = ChartDocument("BADSYM", "1m")
    assert doc.load() is False
    assert doc._loaded is False                       # not latched -> retry stays possible
    doc.ensure_loaded()                               # retries, now succeeds
    assert doc._bars and doc._loaded is True


# --- LiveHub --------------------------------------------------------------------------------


def test_livehub_timer_disabled_under_env(app, _synthetic_load):
    # VIKE_DISABLE_LIVE is set by the suite conftest -> the round-robin timer never arms.
    hub = LiveHub()
    doc = ChartDocument("BTCUSDT", "1m")
    doc.load()
    hub.register(doc)
    assert not hub._timer.isActive()
    hub.unregister(doc)
    hub.shutdown()


def test_livehub_register_unregister(app, _synthetic_load):
    hub = LiveHub()
    d1, d2 = ChartDocument("BTCUSDT", "1m"), ChartDocument("ETHUSDT", "1m")
    hub.register(d1)
    hub.register(d2)
    assert d1 in hub._docs and d2 in hub._docs
    hub.unregister(d1)
    assert d1 not in hub._docs and d2 in hub._docs
    hub.shutdown()


# --- MainWindow wiring ----------------------------------------------------------------------


def test_new_chart_document_adds_tab(app, _synthetic_load):
    win = MainWindow(session_path=None)
    assert win.tabs.document_count() == 0
    doc = win._new_chart_document("ETHUSDT", "1h")
    assert win.tabs.document_count() == 1
    assert doc in win._doc_widgets
    assert doc in win._live_hub._docs
    assert win.tabs.currentWidget() is doc
    assert win.tabs.currentIndex() == -1            # a document is current, not a space
    win.close()


def test_open_in_new_chart_signal(app, _synthetic_load):
    win = MainWindow(session_path=None)
    win.watchlist.openInNewChart.emit("SOLUSDT")
    assert win.tabs.document_count() == 1
    assert win._doc_widgets[0].symbol == "SOLUSDT"
    win.close()


def test_current_document_sets_title_and_keeps_rail(app, _synthetic_load):
    win = MainWindow(session_path=None)
    win._new_chart_document("ETHUSDT", "2h")
    win._on_tab_changed(win.tabs.currentIndex())
    assert win.windowTitle().endswith("ETHUSDT · 2h")
    # switching back to a space restores the space title + rail
    win.tabs.setCurrentIndex(0)
    win._on_tab_changed(0)
    assert win.windowTitle().endswith("Chart")
    assert win._rail_group.button(0).isChecked()
    win.close()


def test_closing_document_unregisters(app, _synthetic_load):
    win = MainWindow(session_path=None)
    win._new_chart_document("ETHUSDT", "1h")
    dock = win.tabs._documents[0]
    dock.closeDockWidget()
    app.processEvents()
    assert win.tabs.document_count() == 0
    assert win._doc_widgets == []
    assert win._live_hub._docs == []
    win.close()


def test_interval_change_updates_tab_title(app, _synthetic_load):
    """The symbolChanged -> tab-title sync works on a live doc (and is crash-safe post-close)."""
    win = MainWindow(session_path=None)
    doc = win._new_chart_document("ETHUSDT", "1h")
    dock = win.tabs._documents[0]
    doc.load("ETHUSDT", "4h")                 # emits symbolChanged -> _sync_title
    assert dock.windowTitle() == "ETHUSDT · 4h"
    # after close the guarded slot must not raise even if a stale emission arrives
    dock.closeDockWidget()
    app.processEvents()
    assert win.tabs.document_count() == 0
    win.close()


def test_link_group_syncs_symbol_and_interval(app, _synthetic_load):
    win = MainWindow(session_path=None)
    d1 = win._new_chart_document("ETHUSDT", "1h")
    d2 = win._new_chart_document("SOLUSDT", "1h")
    d3 = win._new_chart_document("ADAUSDT", "1h")
    d1._set_link_group(3); d2._set_link_group(3); d3._set_link_group(1)   # blue, blue, red

    # watchlist (blue) picks a symbol -> blue charts follow, red one does not
    win._set_watchlist_link(3)
    win.watchlist.symbolChosen.emit("BTCUSDT")
    assert (d1.symbol, d2.symbol, d3.symbol) == ("BTCUSDT", "BTCUSDT", "ADAUSDT")

    # changing one blue chart's interval syncs the other blue chart (symbol + interval), not red
    d1.load(interval="4h")
    assert (d2.symbol, d2.interval) == ("BTCUSDT", "4h")
    assert (d3.symbol, d3.interval) == ("ADAUSDT", "1h")
    win.close()


def test_link_group_set_via_dot_signal_syncs_attr_and_dot(app, _synthetic_load):
    """Picking a colour on the dot updates both the doc's link_group and the dot visual."""
    win = MainWindow(session_path=None)
    d = win._new_chart_document("ETHUSDT", "1h")
    d._link_dot.set_group(4, emit=True)          # simulates the menu pick
    assert d.link_group == 4 and d._link_dot.group() == 4
    win.close()


def test_focusing_restored_linked_doc_does_not_broadcast(app, monkeypatch):
    """ensure_loaded (focus top-up of a restored doc) must NOT overwrite same-group peers."""
    monkeypatch.setattr(chartdoc, "load_symbol_bars", lambda *a, **k: LoadResult(_bars()))
    win = MainWindow(session_path=None)
    # peer in blue with its own symbol
    peer = win._new_chart_document("SOLUSDT", "1h")
    peer._set_link_group(3)
    # a "restored" doc: cache-only load (so _loaded stays False -> ensure_loaded will top up)
    restored = win._new_chart_document("ETHUSDT", "1h", network=False, make_current=False)
    restored._set_link_group(3)
    # focusing it triggers ensure_loaded -> must not broadcast ETHUSDT onto the peer
    restored.ensure_loaded()
    assert peer.symbol == "SOLUSDT"
    win.close()


def test_failed_link_load_rolls_back_symbol(app, monkeypatch):
    """A failed apply_link (bad symbol) must leave the doc on its real symbol, not corrupt it."""
    monkeypatch.setattr(chartdoc, "load_symbol_bars", lambda *a, **k: LoadResult(_bars()))
    doc = ChartDocument("ETHUSDT", "1h")
    doc.load()
    monkeypatch.setattr(chartdoc, "load_symbol_bars", lambda *a, **k: LoadResult([]))  # now fails
    assert doc.apply_link("BADSYM", "1h") is None      # apply_link calls load (returns False)
    assert doc.symbol == "ETHUSDT" and doc.title() == "ETHUSDT · 1h"


def test_out_of_range_link_group_clamps_to_unlinked(app, _synthetic_load):
    doc = ChartDocument("ETHUSDT", "1h")
    doc.load()
    doc.apply_state({"link_group": 99, "indicators": []})
    assert doc.link_group == 0


def test_unlinked_documents_do_not_follow(app, _synthetic_load):
    win = MainWindow(session_path=None)
    d1 = win._new_chart_document("ETHUSDT", "1h")     # group 0 (unlinked) by default
    win._set_watchlist_link(0)
    win.watchlist.symbolChosen.emit("BTCUSDT")
    assert d1.symbol == "ETHUSDT"                      # unlinked -> unaffected
    win.close()


def test_link_group_persists_and_restores(app, _synthetic_load, tmp_path):
    path = tmp_path / "session.json"
    first = MainWindow(session_path=str(path))
    d = first._new_chart_document("ETHUSDT", "4h")
    d._set_link_group(2)                               # green
    first._set_watchlist_link(2)
    first.close()

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["documents"][0]["link_group"] == 2
    assert saved["watchlist_link"] == 2

    second = MainWindow(session_path=str(path))
    assert second._doc_widgets[0].link_group == 2
    assert second._watchlist_link == 2
    second.close()


def test_documents_persist_and_restore(app, _synthetic_load, tmp_path):
    path = tmp_path / "session.json"
    first = MainWindow(session_path=str(path))
    first._new_chart_document("ETHUSDT", "4h")
    d = first._new_chart_document("SOLUSDT", "1h")
    d.chart.add_indicator("rsi", params={"period": 14})
    first.close()

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [doc["symbol"] for doc in saved["documents"]] == ["ETHUSDT", "SOLUSDT"]

    second = MainWindow(session_path=str(path))
    assert second.tabs.document_count() == 2
    syms = [doc.symbol for doc in second._doc_widgets]
    assert syms == ["ETHUSDT", "SOLUSDT"]
    # the SOL doc's indicator was restored (cache-only load produced bars, so it re-attached)
    sol = second._doc_widgets[1]
    assert [i.name for i in sol.chart._indicators.values()] == ["rsi"]
    second.close()

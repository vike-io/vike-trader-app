"""Offscreen tests for the Screener tab (reads a temp Parquet cache)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtGui, QtWidgets  # noqa: E402

from vike_trader_app.analysis import screener as S  # noqa: E402
from vike_trader_app.analysis.screener import CompositeRule, CompositeStore, Condition  # noqa: E402
from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.data.catalog import Catalog  # noqa: E402
from vike_trader_app.data.parquet_source import write_bars_parquet  # noqa: E402
from vike_trader_app.ui.screener import ScreenerTab  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _write(tmp_path, sym, *, slope=0.5, vol=100.0, n=60):
    bars = [Bar(ts=i * 60_000, open=100 + slope * i, high=101 + slope * i,
                low=99 + slope * i, close=100 + slope * i, volume=vol) for i in range(n)]
    write_bars_parquet(bars, tmp_path / sym / "1m.parquet")


def _wire(tmp_path, monkeypatch):
    """Point the tab at a temp cache + a temp composite store (no real-disk pollution)."""
    monkeypatch.setattr(ScreenerTab, "_catalog", lambda self: Catalog(str(tmp_path)))
    monkeypatch.setattr(ScreenerTab, "_make_store",
                        lambda self: CompositeStore(str(tmp_path / "composites.json")))


def test_screener_builds_and_empty_scan_is_graceful(app, tmp_path, monkeypatch):
    monkeypatch.setattr(ScreenerTab, "_catalog", lambda self: Catalog(str(tmp_path)))
    tab = ScreenerTab()
    assert tab._rule.count() >= 4          # rule dropdown populated
    tab.scan()                              # empty cache -> no crash
    assert tab._table.rowCount() == 0


def test_screener_scan_populates_and_classifies(app, tmp_path, monkeypatch):
    for sym, slope in (("UPUSD", 0.5), ("DNUSD", -0.3)):
        bars = [Bar(ts=i * 60_000, open=100 + slope * i, high=101 + slope * i,
                    low=99 + slope * i, close=100 + slope * i) for i in range(60)]
        write_bars_parquet(bars, tmp_path / sym / "1m.parquet")
    monkeypatch.setattr(ScreenerTab, "_catalog", lambda self: Catalog(str(tmp_path)))
    tab = ScreenerTab()                     # __init__ populates intervals from the temp cache
    tab.scan()
    assert tab._table.rowCount() == 2
    sigs = {tab._table.item(r, 0).text(): tab._table.item(r, 1).text() for r in range(2)}
    # default rule is RSI(14): a monotonic riser is overbought (short), a faller oversold (long)
    assert sigs["UPUSD"] == "SHORT"
    assert sigs["DNUSD"] == "LONG"


def test_composite_rule_registers_and_scans(app, tmp_path, monkeypatch):
    S._COMPOSITES.clear()
    _write(tmp_path, "UPUSD", slope=0.5)
    _write(tmp_path, "DNUSD", slope=-0.3)
    _wire(tmp_path, monkeypatch)
    tab = ScreenerTab()
    base = tab._rule.count()
    comp = CompositeRule(name="RSI+SMA long", description="x",
                         conditions=(Condition("RSI(14) 30/70", "long"), Condition("SMA(50) trend", "long")),
                         combine="AND", direction="long")
    tab._store.add(comp)
    tab._refresh_rules()
    assert tab._rule.count() == base + 1
    tab._rule.setCurrentIndex(tab._rule.findText("RSI+SMA long"))
    tab.scan()
    assert tab._table.rowCount() == 2          # both symbols evaluated by the composite


def test_composite_store_persists_across_tabs(app, tmp_path, monkeypatch):
    S._COMPOSITES.clear()
    _wire(tmp_path, monkeypatch)
    tab1 = ScreenerTab()
    tab1._store.add(CompositeRule(name="Persisted", description="x",
                                  conditions=(Condition("ROC(30) momentum", "long"),),
                                  combine="AND", direction="long"))
    S._COMPOSITES.clear()                       # simulate a fresh process (forget in-memory registry)
    tab2 = ScreenerTab()                        # _make_store -> load() re-registers from disk
    names = [tab2._rule.itemText(i) for i in range(tab2._rule.count())]
    assert "Persisted" in names


def test_volume_filter_drops_low_volume(app, tmp_path, monkeypatch):
    S._COMPOSITES.clear()
    _write(tmp_path, "HIVOL", vol=5000.0)
    _write(tmp_path, "LOVOL", vol=10.0)
    _wire(tmp_path, monkeypatch)
    tab = ScreenerTab()
    tab._min_vol.setValue(1000.0)
    tab.scan()
    syms = {tab._table.item(r, 0).text() for r in range(tab._table.rowCount())}
    assert "HIVOL" in syms and "LOVOL" not in syms


def test_export_csv_writes_file(app, tmp_path, monkeypatch):
    S._COMPOSITES.clear()
    _write(tmp_path, "AAUSD")
    _write(tmp_path, "BBUSD", slope=-0.2)
    _wire(tmp_path, monkeypatch)
    tab = ScreenerTab()
    tab.scan()
    out = tmp_path / "out.csv"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(out), "CSV (*.csv)"))
    tab._export_csv()
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "Symbol" in text and "AAUSD" in text


def test_live_toggle_starts_timer_and_hide_stops_it(app, tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    tab = ScreenerTab()
    assert not tab._timer.isActive()
    tab._live.setChecked(True)                  # -> _on_live_toggled -> scan + start
    assert tab._timer.isActive()
    tab.hideEvent(QtGui.QHideEvent())           # leaving the tab stops background cache reads
    assert not tab._timer.isActive()


def test_scan_survives_catalog_read_error(app, tmp_path, monkeypatch):
    # A corrupt/locked shard raising mid-read must NOT crash the scan (the live-timer slot).
    # read_series_many isolates per-symbol errors: the failing symbol maps to [] and is skipped
    # by screen(), so the scan completes gracefully with 0 results rather than "Scan failed".
    _wire(tmp_path, monkeypatch)
    tab = ScreenerTab()

    class _Boom:
        def symbols(self):
            return ["XUSD"]

        def intervals(self, _s):
            return ["1m"]

        def query(self, _s, _iv, start=None, end=None):
            raise RuntimeError("corrupt shard")

    monkeypatch.setattr(tab, "_catalog", lambda: _Boom())
    tab.scan()  # must not raise
    status = tab._status.text()
    assert "Scan failed" not in status           # graceful: no hard failure surfaced
    assert status.startswith("0 symbols")        # empty result, not a crash
    assert QtWidgets.QApplication.overrideCursor() is None  # cursor balanced

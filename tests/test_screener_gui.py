"""Offscreen tests for the Screener tab (reads a temp Parquet cache)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.data.catalog import Catalog  # noqa: E402
from vike_trader_app.data.parquet_source import write_bars_parquet  # noqa: E402
from vike_trader_app.ui.screener import ScreenerTab  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


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

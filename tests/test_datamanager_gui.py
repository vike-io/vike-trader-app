"""Offscreen tests for the Data Manager panel — lists the cache, pins, and deletes."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.data import parquet_source as ps  # noqa: E402
from vike_trader_app.data.rollup import load_pins  # noqa: E402
from vike_trader_app.ui.datamanager import DataManagerTab  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _seed(root: str):
    nov = 1_700_000_000_000
    ps.append_series([Bar(ts=nov + i * 60_000, open=1, high=1, low=1, close=1, volume=1.0)
                      for i in range(5)], root, "BTCUSDT", "1m")


def test_datamanager_lists_cached_series(app, tmp_path):
    _seed(str(tmp_path))
    tab = DataManagerTab(root=str(tmp_path), pins_path=str(tmp_path / "pins.json"))
    tab.refresh()  # populates lazily (normally on first show)
    assert tab._table.rowCount() == 1
    assert tab._table.item(0, 0).text() == "BTCUSDT"
    assert tab._table.item(0, 1).text() == "1m"
    assert tab._table.item(0, 2).text() == "5"        # 5 bars
    assert tab._table.item(0, 6).text() == ""         # not pinned


def test_datamanager_pin_toggle_persists_and_marks_row(app, tmp_path):
    _seed(str(tmp_path))
    pins = str(tmp_path / "pins.json")
    tab = DataManagerTab(root=str(tmp_path), pins_path=pins)
    tab.refresh()
    tab._table.setCurrentCell(0, 0)
    tab._on_pin()
    assert load_pins(pins) == [["BTCUSDT", "1m"]]
    assert tab._table.item(0, 6).text() == "📌"
    tab._on_pin()
    assert load_pins(pins) == []
    assert tab._table.item(0, 6).text() == ""


def test_datamanager_inspect_logs_quality_report(app, tmp_path):
    _seed(str(tmp_path))
    tab = DataManagerTab(root=str(tmp_path), pins_path=str(tmp_path / "pins.json"))
    tab.refresh()
    tab._table.setCurrentCell(0, 0)
    tab._on_inspect()
    log = tab._log_view.toPlainText()
    assert "Inspect BTCUSDT 1m" in log
    assert "clean" in log  # 5 contiguous valid bars
    assert "instrument:" in log and "Binance" in log  # self-describing spec line


def test_datamanager_shows_instrument_spec_column(app, tmp_path):
    _seed(str(tmp_path))
    tab = DataManagerTab(root=str(tmp_path), pins_path=str(tmp_path / "pins.json"))
    tab.refresh()
    last = tab._table.columnCount() - 1
    assert tab._table.horizontalHeaderItem(last).text() == "Instrument"
    assert tab._table.item(0, last).text() == "crypto · tick 0.01"


def test_datamanager_refresh_seeds_broker_presets(app, tmp_path):
    from vike_trader_app.data.instruments import list_profiles

    _seed(str(tmp_path))
    tab = DataManagerTab(root=str(tmp_path), pins_path=str(tmp_path / "pins.json"))
    tab.refresh()
    assert set(list_profiles(str(tmp_path))) == {
        "Binance", "Bybit", "Coinbase", "US Equities", "Generic",
    }


def test_datamanager_update_all_extends_each_series(app, tmp_path, monkeypatch):
    import vike_trader_app.ui.datamanager as dm

    _seed(str(tmp_path))  # one series: BTCUSDT 1m
    calls = []

    def fake_get_bars(symbol, interval, start, end, root=None, fetcher=None, progress=None):  # noqa: ARG001
        calls.append((symbol, interval))
        return []  # pretend nothing new (no network)

    monkeypatch.setattr(dm, "get_bars", fake_get_bars)
    tab = dm.DataManagerTab(root=str(tmp_path), pins_path=str(tmp_path / "pins.json"))
    tab.refresh()
    tab._on_update_all()
    assert ("BTCUSDT", "1m") in calls
    assert "Update all: done" in tab._log_view.toPlainText()


def test_datamanager_delete_removes_series(app, tmp_path):
    _seed(str(tmp_path))
    tab = DataManagerTab(root=str(tmp_path), pins_path=str(tmp_path / "pins.json"))
    tab.refresh()
    assert tab._table.rowCount() == 1
    tab._delete("BTCUSDT", "1m")           # the no-prompt path used by the confirm dialog
    assert tab._table.rowCount() == 0
    assert ps.read_series(str(tmp_path), "BTCUSDT", "1m") == []

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


def test_datamanager_import_csv_adds_series(app, tmp_path):
    tab = DataManagerTab(root=str(tmp_path), pins_path=str(tmp_path / "pins.json"))
    tab.refresh()
    csv = tmp_path / "eur.csv"
    csv.write_text("time,open,high,low,close,volume\n"
                   "2024-01-02 15:04:00,1.10,1.20,1.00,1.15,100\n"
                   "2024-01-02 15:05:00,1.15,1.25,1.10,1.20,80\n")
    n = tab.import_csv_file(str(csv), "EURUSD")          # no dialog (detected 1m)
    assert n == 2
    assert ps.read_series(str(tmp_path), "EURUSD", "1m")  # series written to cache
    assert "Imported EURUSD 1m" in tab._log_view.toPlainText()


def test_datamanager_import_csv_aggregates_to_target(app, tmp_path):
    tab = DataManagerTab(root=str(tmp_path), pins_path=str(tmp_path / "pins.json"))
    tab.refresh()
    rows = ["time,open,high,low,close,volume"]
    for i in range(5):
        rows.append(f"2024-01-02 15:0{i}:00,{i},{i + 1},{i - 1},{i},1")
    csv = tmp_path / "agg.csv"
    csv.write_text("\n".join(rows))
    n = tab.import_csv_file(str(csv), "BTCUSDT", target_interval="5m")
    assert n == 1                                        # five 1m -> one 5m
    assert len(ps.read_series(str(tmp_path), "BTCUSDT", "5m")) == 1


def test_datamanager_download_series_routes_to_chosen_provider(app, tmp_path, monkeypatch):
    import vike_trader_app.ui.datamanager as dm

    captured = {}

    class _Src:
        name = "bybit"

        def fetch_bars_range(self, *a, **k):
            return []

    def fake_select(symbol, provider=None):
        captured["provider"] = provider
        return _Src()

    monkeypatch.setattr(dm, "select_source", fake_select)
    monkeypatch.setattr(dm, "get_bars", lambda *a, **k: [])
    tab = dm.DataManagerTab(root=str(tmp_path), pins_path=str(tmp_path / "pins.json"))
    tab.refresh()
    tab.download_series("BTCUSDT", "1m", 5, provider="bybit")
    assert captured["provider"] == "bybit"
    assert "via bybit" in tab._log_view.toPlainText()


def test_datamanager_download_dataset_iterates_symbols(app, tmp_path, monkeypatch):
    import vike_trader_app.ui.datamanager as dm
    from vike_trader_app.data.datasets import DataSet

    calls = []
    monkeypatch.setattr(dm.DataManagerTab, "download_series",
                        lambda self, s, i, d, p=None: calls.append((s, i, d, p)))
    tab = dm.DataManagerTab(root=str(tmp_path), pins_path=str(tmp_path / "pins.json"))
    tab.refresh()
    n = tab.download_dataset(DataSet("X", ["BTCUSDT", "ETHUSDT"], provider="bybit", interval="5m"), 10)
    assert n == 2
    assert ("BTCUSDT", "5m", 10, "bybit") in calls


def test_datamanager_delete_removes_series(app, tmp_path):
    _seed(str(tmp_path))
    tab = DataManagerTab(root=str(tmp_path), pins_path=str(tmp_path / "pins.json"))
    tab.refresh()
    assert tab._table.rowCount() == 1
    tab._delete("BTCUSDT", "1m")           # the no-prompt path used by the confirm dialog
    assert tab._table.rowCount() == 0
    assert ps.read_series(str(tmp_path), "BTCUSDT", "1m") == []


def test_config_root_for_maps_parquet_to_sibling():
    from pathlib import Path

    from vike_trader_app.ui.datamanager import config_root_for

    assert Path(config_root_for(str(Path("storage") / "parquet"))) == Path("storage")
    assert Path(config_root_for(str(Path("data") / "custom"))) == Path("data") / "custom"


def test_datamanager_profiles_live_beside_parquet_not_inside(app, tmp_path):
    data_root = tmp_path / "parquet"
    data_root.mkdir()
    tab = DataManagerTab(root=str(data_root), pins_path=str(tmp_path / "pins.json"))
    tab.refresh()  # seeds presets at the config root
    assert (tmp_path / "profiles").is_dir()          # storage/profiles — beside the cache
    assert not (data_root / "profiles").exists()     # not storage/parquet/profiles

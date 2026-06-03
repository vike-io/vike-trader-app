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


def test_datamanager_download_series_routes_through_chain(app, tmp_path, monkeypatch):
    import vike_trader_app.ui.datamanager as dm
    from vike_trader_app.core.model import Bar

    captured = {}

    def fake_fetch_for(sym, iv, s, e, root=None, linked_provider=None, progress=None):
        captured["sym"] = sym
        captured["linked"] = linked_provider
        return [Bar(ts=1, open=1, high=1, low=1, close=1)], "bybit"

    # download_series builds a fetcher closure that calls fetch_for; make get_bars invoke it once.
    monkeypatch.setattr("vike_trader_app.data.provider_chain.fetch_for", fake_fetch_for)
    monkeypatch.setattr(dm, "get_bars",
                        lambda symbol, interval, start, end, root=None, fetcher=None, progress=None:
                        fetcher(symbol, interval, start, end) if fetcher else [])
    tab = dm.DataManagerTab(root=str(tmp_path), pins_path=str(tmp_path / "pins.json"))
    tab.refresh()
    n = tab.download_series("BTCUSDT", "1m", 5, provider="bybit")
    assert n == 1
    assert captured["sym"] == "BTCUSDT"
    assert captured["linked"] == "bybit"          # the chosen provider becomes the linked-first
    assert "via provider chain" in tab._log_view.toPlainText()


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


# --- Task 12: tree + sub-tabs structural tests ---

def test_data_tab_has_tree_and_subtabs(app, tmp_path):
    from vike_trader_app.ui.datamanager import DataManagerTab
    tab = DataManagerTab(root=str(tmp_path), config_root=str(tmp_path))
    assert tab.tree is not None
    titles = [tab.subtabs.tabText(i) for i in range(tab.subtabs.count())]
    assert titles == ["Symbols", "Cached Series", "Historical Providers"]


def test_selecting_dataset_loads_symbols_panel(app, tmp_path):
    from vike_trader_app.data.datasets import DataSet, save_dataset
    from vike_trader_app.ui.datamanager import DataManagerTab
    save_dataset(DataSet("Sel", ["BTCUSDT"], provider="binance"), str(tmp_path))
    tab = DataManagerTab(root=str(tmp_path), config_root=str(tmp_path))
    tab.tree.reload()
    tab.tree.dataset_selected.emit("Sel")
    assert "BTCUSDT" in tab.panel._symbols.toPlainText()


# --- Task 13: Cached-Series filter test ---

def test_cached_table_filters_to_selected_dataset(app, tmp_path, monkeypatch):
    from vike_trader_app.ui.datamanager import DataManagerTab
    tab = DataManagerTab(root=str(tmp_path), config_root=str(tmp_path))

    class _Info:
        def __init__(self, symbol):
            self.symbol, self.interval, self.n_bars, self.start_ts, self.end_ts = (
                symbol, "1m", 10, 0, 60_000
            )

    monkeypatch.setattr(tab, "_catalog", lambda: type("C", (), {
        "list_datasets": staticmethod(lambda: [_Info("BTCUSDT"), _Info("ETHUSDT"), _Info("XRPUSDT")])})())
    tab.set_symbol_filter(["BTCUSDT", "ETHUSDT"])
    tab.refresh()
    shown = {tab._table.item(r, 0).text() for r in range(tab._table.rowCount())}
    assert shown == {"BTCUSDT", "ETHUSDT"}
    tab.set_symbol_filter(None)
    tab.refresh()
    assert tab._table.rowCount() == 3


# --- Task 14: Test symbol / Test DataSet glue ---

def test_test_symbol_loads_bars_and_emits(app, tmp_path, monkeypatch):
    from vike_trader_app.core.model import Bar
    from vike_trader_app.ui.datamanager import DataManagerTab
    tab = DataManagerTab(root=str(tmp_path), config_root=str(tmp_path))
    fake = [Bar(ts=1, open=1, high=1, low=1, close=1)]
    monkeypatch.setattr("vike_trader_app.ui.datamanager.get_bars", lambda *a, **k: fake)
    got = {}
    tab.test_symbol_requested.connect(lambda sym, bars: got.update(sym=sym, bars=bars))
    tab.panel.test_symbol_requested.emit("BTCUSDT", "1m")
    assert got["sym"] == "BTCUSDT" and got["bars"] == fake


def test_test_dataset_runs_portfolio_and_emits_report(app, tmp_path, monkeypatch):
    from vike_trader_app.core.model import Bar
    from vike_trader_app.data.datasets import DataSet
    from vike_trader_app.ui.datamanager import DataManagerTab
    tab = DataManagerTab(root=str(tmp_path), config_root=str(tmp_path))
    monkeypatch.setattr("vike_trader_app.ui.datamanager.get_bars",
                        lambda sym, *a, **k: [Bar(ts=1, open=1, high=1, low=1, close=1)])
    reports = []
    tab.test_dataset_requested.connect(lambda ds, bars_by_symbol: reports.append((ds, bars_by_symbol)))
    tab.panel.test_dataset_requested.emit(DataSet("DS", ["BTCUSDT", "ETHUSDT"], interval="1m"))
    assert reports[0][0].name == "DS"
    assert set(reports[0][1]) == {"BTCUSDT", "ETHUSDT"}


def test_datamanager_truncate_removes_bars(app, tmp_path):
    from vike_trader_app.ui.datamanager import DataManagerTab
    from vike_trader_app.core.model import Bar
    from vike_trader_app.data import parquet_source as ps
    root = str(tmp_path)
    ps.append_series([Bar(ts=i * 86_400_000, open=1, high=1, low=1, close=1, volume=1.0) for i in range(1, 11)],
                     root, "BTCUSDT", "1m")
    tab = DataManagerTab(root=root, pins_path=str(tmp_path / "pins.json"))
    tab.refresh()
    n = tab.truncate_series("BTCUSDT", "1m", before_ms=4 * 86_400_000)   # dialog-free
    assert n == 3
    assert [b.ts for b in ps.read_series(root, "BTCUSDT", "1m")] == [i * 86_400_000 for i in range(4, 11)]
    assert "Truncated BTCUSDT 1m" in tab._log_view.toPlainText()


def test_datamanager_remove_inactive(app, tmp_path, monkeypatch):
    from vike_trader_app.ui.datamanager import DataManagerTab
    tab = DataManagerTab(root=str(tmp_path), pins_path=str(tmp_path / "pins.json"))

    class _Info:
        def __init__(self, symbol, n_bars, end_ts):
            self.symbol, self.interval, self.n_bars, self.start_ts, self.end_ts = symbol, "1m", n_bars, 0, end_ts

    monkeypatch.setattr(tab, "_catalog", lambda: type("C", (), {
        "list_datasets": staticmethod(lambda: [_Info("DEAD", 0, 0), _Info("LIVE", 100, 9_000)])})())
    deleted = []
    monkeypatch.setattr(tab, "_delete", lambda s, i: deleted.append((s, i)))
    removed = tab.remove_inactive()                 # dialog-free: prune 0-bar series
    assert removed == [("DEAD", "1m")]
    assert deleted == [("DEAD", "1m")]
    assert "Removed 1 inactive" in tab._log_view.toPlainText()

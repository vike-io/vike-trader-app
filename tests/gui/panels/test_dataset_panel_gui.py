# tests/test_dataset_panel_gui.py
"""Offscreen tests for the DataSet Symbols panel (right pane)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.data.datasets import DataSet, load_dataset, save_dataset  # noqa: E402
from vike_trader_app.ui.dataset_panel import DataSetPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_load_then_edit_and_save(app, tmp_path):
    save_dataset(DataSet("Set1", ["BTCUSDT"], provider="binance", interval="5m"), str(tmp_path))
    panel = DataSetPanel(str(tmp_path))
    panel.load_dataset("Set1")
    assert "BTCUSDT" in panel._symbols.toPlainText()
    panel._symbols.setPlainText("AAA, BBB")
    panel._provider.setCurrentText("dukascopy")
    panel.save()
    back = load_dataset("Set1", str(tmp_path))
    assert back.symbols == ["AAA", "BBB"] and back.provider == "dukascopy"


def test_benchmark_field_round_trips(app, tmp_path):
    # Set a benchmark in the form -> it persists; loading a dataset with a benchmark shows it.
    save_dataset(DataSet("SetB", ["BTCUSDT"], interval="1d"), str(tmp_path))
    panel = DataSetPanel(str(tmp_path))
    panel.load_dataset("SetB")
    assert panel._benchmark.text() == ""          # none set yet
    panel._benchmark.setText("SPY")
    panel.save()
    back = load_dataset("SetB", str(tmp_path))
    assert back.benchmark == "SPY"
    # a fresh panel loading it shows the benchmark in the field
    panel2 = DataSetPanel(str(tmp_path))
    panel2.load_dataset("SetB")
    assert panel2._benchmark.text() == "SPY"
    assert panel2.current_dataset().benchmark == "SPY"


def test_test_buttons_emit_requests(app, tmp_path):
    save_dataset(DataSet("Set2", ["BTCUSDT", "ETHUSDT"], interval="1m"), str(tmp_path))
    panel = DataSetPanel(str(tmp_path))
    panel.load_dataset("Set2")
    sym_req, ds_req = [], []
    panel.test_symbol_requested.connect(lambda s, i: sym_req.append((s, i)))
    panel.test_dataset_requested.connect(lambda d: ds_req.append(d))
    panel._symbols_list.setCurrentRow(1)   # ETHUSDT
    panel._on_test_symbol()
    panel._on_test_dataset()
    assert sym_req == [("ETHUSDT", "1m")]
    assert ds_req[0].name == "Set2" and ds_req[0].symbols == ["BTCUSDT", "ETHUSDT"]


def test_ask_ai_appends_suggested_symbols(app, tmp_path):
    from vike_trader_app.data.datasets import DataSet, save_dataset
    save_dataset(DataSet("AiSet", ["BTCUSDT"]), str(tmp_path))
    panel = DataSetPanel(str(tmp_path))
    panel.load_dataset("AiSet")
    panel.apply_ai_suggestion("ETHUSDT, SOLUSDT")  # dialog-free: parse + append, deduped
    text = panel._symbols.toPlainText()
    assert "ETHUSDT" in text and "SOLUSDT" in text and text.count("BTCUSDT") == 1


def test_test_symbol_does_not_emit_without_selection(app, tmp_path):
    save_dataset(DataSet("Set3", ["BTCUSDT", "ETHUSDT"]), str(tmp_path))
    panel = DataSetPanel(str(tmp_path))
    panel.load_dataset("Set3")
    panel._symbols_list.setCurrentRow(-1)  # nothing selected
    fired = []
    panel.test_symbol_requested.connect(lambda *a: fired.append(a))
    panel._on_test_symbol()
    assert fired == []


def test_ask_ai_shows_error_without_crashing(app, tmp_path, monkeypatch):
    save_dataset(DataSet("Set4", ["BTCUSDT"]), str(tmp_path))
    panel = DataSetPanel(str(tmp_path))
    panel.load_dataset("Set4")
    panel._ai_query.setText("anything")
    import vike_trader_app.ai.symbol_suggest as mod

    def _boom(*a, **k):
        raise RuntimeError("no AI here")

    monkeypatch.setattr(mod, "suggest_symbols", _boom)
    panel._on_ask_ai()  # must not raise
    assert "AI unavailable" in panel._ai_status.text()
    assert panel.btn_ai.isEnabled()  # re-enabled in finally


def test_import_membership_sets_ranges_and_persists(app, tmp_path):
    save_dataset(DataSet("M", ["AAA", "BBB"], interval="1d"), str(tmp_path))
    panel = DataSetPanel(str(tmp_path))
    panel.load_dataset("M")
    panel.import_membership_csv("AAA,2020-01-01,2020-12-31\nBBB,2021-06-01,\n")
    back = load_dataset("M", str(tmp_path))
    assert back.is_dynamic() and "AAA" in back.ranges and "BBB" in back.ranges
    assert back.ranges["BBB"][0].end_ts is None
    # a subsequent Save must not wipe the imported ranges
    panel.save()
    assert load_dataset("M", str(tmp_path)).is_dynamic()


def test_membership_summary_shows_windows(app, tmp_path):
    from vike_trader_app.data.datasets import DateRange
    d = DataSet("S", ["AAA"], interval="1d",
                ranges={"AAA": [DateRange(1577836800000, 1609459200000)]})
    save_dataset(d, str(tmp_path))
    panel = DataSetPanel(str(tmp_path))
    panel.load_dataset("S")
    summary = panel.membership_summary()
    assert "AAA" in summary
    assert "2020-01-01" in summary
    assert "2021-01-01" in summary


def test_membership_summary_open_ended(app, tmp_path):
    from vike_trader_app.data.datasets import DateRange
    d = DataSet("T", ["BBB"], interval="1d",
                ranges={"BBB": [DateRange(1577836800000, None)]})
    save_dataset(d, str(tmp_path))
    panel = DataSetPanel(str(tmp_path))
    panel.load_dataset("T")
    summary = panel.membership_summary()
    assert "open" in summary


def test_save_preserves_ranges_after_load(app, tmp_path):
    """Ensure save() does not wipe membership even if called before import."""
    from vike_trader_app.data.datasets import DateRange
    d = DataSet("R", ["AAA"], interval="1d",
                ranges={"AAA": [DateRange(1577836800000, None)]})
    save_dataset(d, str(tmp_path))
    panel = DataSetPanel(str(tmp_path))
    panel.load_dataset("R")
    panel.save()
    back = load_dataset("R", str(tmp_path))
    assert back.is_dynamic()
    assert back.ranges["AAA"][0].end_ts is None

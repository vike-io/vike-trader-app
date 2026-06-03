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

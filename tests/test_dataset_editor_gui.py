"""Offscreen tests for the DataSets manager dialog."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.data.datasets import ensure_examples, load_dataset  # noqa: E402
from vike_trader_app.ui.dataset_editor import DataSetEditorDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_loads_example_dataset(app, tmp_path):
    ensure_examples(str(tmp_path))
    dlg = DataSetEditorDialog(root=str(tmp_path))
    dlg._combo.setCurrentText("Crypto Majors")
    assert "BTCUSDT" in dlg._symbols.toPlainText()


def test_save_roundtrip(app, tmp_path):
    ensure_examples(str(tmp_path))
    dlg = DataSetEditorDialog(root=str(tmp_path))
    dlg._combo.setCurrentText("Crypto Majors")
    dlg._symbols.setPlainText("AAA, BBB")
    dlg._provider.setCurrentText("bybit")
    dlg._interval.setCurrentText("5m")
    dlg.save()
    back = load_dataset("Crypto Majors", str(tmp_path))
    assert back.symbols == ["AAA", "BBB"] and back.provider == "bybit" and back.interval == "5m"


def test_new_dataset_persists_and_selects(app, tmp_path):
    dlg = DataSetEditorDialog(root=str(tmp_path))
    dlg.new_dataset("My Set")
    assert dlg._combo.currentText() == "My Set"
    assert load_dataset("My Set", str(tmp_path)) is not None


def test_download_all_invokes_callback_with_days(app, tmp_path):
    ensure_examples(str(tmp_path))
    got = {}
    dlg = DataSetEditorDialog(root=str(tmp_path),
                              on_download=lambda ds, days: got.update(ds=ds, days=days))
    dlg._combo.setCurrentText("Crypto Majors")
    dlg._days.setValue(7)
    dlg._on_download_all()
    assert got["days"] == 7 and "BTCUSDT" in got["ds"].symbols

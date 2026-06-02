"""Offscreen tests for the broker-profile editor — load, edit, mass-edit, add, save round-trip."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.data.instruments import ensure_presets, load_profile  # noqa: E402
from vike_trader_app.ui.profile_editor import ProfileEditorDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_editor_loads_preset_instruments(app, tmp_path):
    ensure_presets(str(tmp_path))
    dlg = ProfileEditorDialog(root=str(tmp_path))
    dlg._combo.setCurrentText("Binance")
    syms = {dlg._table.item(r, 0).text() for r in range(dlg._table.rowCount())}
    assert "BTCUSDT" in syms and "ETHUSDT" in syms


def test_editor_edit_cell_then_save_persists(app, tmp_path):
    ensure_presets(str(tmp_path))
    dlg = ProfileEditorDialog(root=str(tmp_path))
    dlg._combo.setCurrentText("Binance")
    row = next(r for r in range(dlg._table.rowCount()) if dlg._table.item(r, 0).text() == "BTCUSDT")
    dlg._table.item(row, 2).setText("0.5")   # Tick column
    dlg.save()
    assert load_profile("Binance", str(tmp_path)).instruments["BTCUSDT"].tick_size == 0.5


def test_editor_mass_edit_changes_selected_rows(app, tmp_path):
    ensure_presets(str(tmp_path))
    dlg = ProfileEditorDialog(root=str(tmp_path))
    dlg._combo.setCurrentText("Binance")
    rows = list(range(dlg._table.rowCount()))
    dlg.apply_mass_edit({"volume_step": 7.0}, rows)
    dlg.save()
    specs = load_profile("Binance", str(tmp_path)).instruments
    assert all(s.volume_step == 7.0 for s in specs.values())


def test_editor_add_instrument_then_save(app, tmp_path):
    ensure_presets(str(tmp_path))
    dlg = ProfileEditorDialog(root=str(tmp_path))
    dlg._combo.setCurrentText("US Equities")          # starts empty (only a default spec)
    assert dlg._table.rowCount() == 0
    dlg.add_row()
    dlg._table.item(0, 0).setText("AAPL")
    dlg._table.item(0, 2).setText("0.01")
    saved = dlg.save()
    assert "AAPL" in saved.instruments and saved.instruments["AAPL"].decimals == 2


def test_editor_new_profile_persists_and_selects(app, tmp_path):
    ensure_presets(str(tmp_path))
    dlg = ProfileEditorDialog(root=str(tmp_path))
    dlg.new_profile("My FX", asset_class="forex", timezone="Europe/London", postfix=".fx")
    assert dlg._combo.currentText() == "My FX"
    p = load_profile("My FX", str(tmp_path))
    assert p is not None and p.timezone == "Europe/London" and p.postfix == ".fx"

"""Offscreen GUI tests for _SymbolMappingsDialog (W3-B, Part 3)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.data.symbol_mappings import load_mappings, save_mappings, MappingRule, SymbolMappings  # noqa: E402
from vike_trader_app.ui.providers_panel import ProvidersPanel, _SymbolMappingsDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# ---------------------------------------------------------------------------
# Dialog set_rows / current_mappings round-trip (in-memory)
# ---------------------------------------------------------------------------

def test_dialog_set_and_read_literal_rule(app, tmp_path):
    dlg = _SymbolMappingsDialog(str(tmp_path))
    dlg.set_rows([("yahoo", "BRK.B", "BRK-B", False)])
    m = dlg.current_mappings()
    assert len(m.rules) == 1
    r = m.rules[0]
    assert r.provider == "yahoo"
    assert r.pattern == "BRK.B"
    assert r.replacement == "BRK-B"
    assert r.is_regex is False


def test_dialog_set_and_read_regex_rule(app, tmp_path):
    dlg = _SymbolMappingsDialog(str(tmp_path))
    dlg.set_rows([("yahoo", r"(\w+)\.(\w+)", r"\1-\2", True)])
    m = dlg.current_mappings()
    assert len(m.rules) == 1
    assert m.rules[0].is_regex is True


def test_dialog_set_multiple_rows(app, tmp_path):
    dlg = _SymbolMappingsDialog(str(tmp_path))
    dlg.set_rows([
        ("yahoo", "BRK.B", "BRK-B", False),
        ("yahoo", "BF.B", "BF-B", False),
        ("binance", "BTCUSDT", "BTC-USDT", False),
    ])
    m = dlg.current_mappings()
    assert len(m.rules) == 3
    assert m.rules[2].provider == "binance"


def test_dialog_empty_pattern_row_excluded(app, tmp_path):
    """Rows with empty pattern are silently dropped from current_mappings."""
    dlg = _SymbolMappingsDialog(str(tmp_path))
    dlg.set_rows([
        ("yahoo", "", "BRK-B", False),   # empty pattern — should be excluded
        ("yahoo", "BRK.B", "BRK-B", False),
    ])
    m = dlg.current_mappings()
    assert len(m.rules) == 1


# ---------------------------------------------------------------------------
# Persist via _on_accept (simulate OK click without exec)
# ---------------------------------------------------------------------------

def test_dialog_on_accept_saves_to_disk(app, tmp_path):
    dlg = _SymbolMappingsDialog(str(tmp_path))
    dlg.set_rows([("yahoo", "BRK.B", "BRK-B", False)])
    dlg._on_accept()  # trigger save without entering event loop
    loaded = load_mappings(str(tmp_path))
    assert len(loaded.rules) == 1
    assert loaded.rules[0].replacement == "BRK-B"


# ---------------------------------------------------------------------------
# Full round-trip: set rows → save → reload from disk → verify
# ---------------------------------------------------------------------------

def test_round_trip_via_dialog(app, tmp_path):
    dlg = _SymbolMappingsDialog(str(tmp_path))
    dlg.set_rows([
        ("yahoo", "BRK.B", "BRK-B", False),
        ("yahoo", "BF.B", "BF-B", False),
    ])
    dlg._on_accept()

    # Fresh dialog loads from disk
    dlg2 = _SymbolMappingsDialog(str(tmp_path))
    m = dlg2.current_mappings()
    assert len(m.rules) == 2
    patterns = {r.pattern for r in m.rules}
    assert "BRK.B" in patterns
    assert "BF.B" in patterns


def test_round_trip_regex_rule_via_dialog(app, tmp_path):
    dlg = _SymbolMappingsDialog(str(tmp_path))
    dlg.set_rows([("yahoo", r"(\w+)\.(\w+)", r"\1-\2", True)])
    dlg._on_accept()
    loaded = load_mappings(str(tmp_path))
    assert loaded.rules[0].is_regex is True


# ---------------------------------------------------------------------------
# Dialog loads pre-existing mappings from disk
# ---------------------------------------------------------------------------

def test_dialog_loads_existing_mappings(app, tmp_path):
    save_mappings(
        SymbolMappings([MappingRule("yahoo", "BRK.B", "BRK-B")]),
        str(tmp_path),
    )
    dlg = _SymbolMappingsDialog(str(tmp_path))
    m = dlg.current_mappings()
    assert len(m.rules) == 1
    assert m.rules[0].pattern == "BRK.B"


# ---------------------------------------------------------------------------
# Panel integration: button exists + open_symbol_mappings_dialog helper
# ---------------------------------------------------------------------------

def test_panel_has_symbol_mappings_button(app, tmp_path):
    panel = ProvidersPanel(str(tmp_path))
    assert hasattr(panel, "btn_symbol_mappings")


def test_panel_open_dialog_returns_dialog_instance(app, tmp_path):
    panel = ProvidersPanel(str(tmp_path))
    dlg = panel.open_symbol_mappings_dialog()
    assert isinstance(dlg, _SymbolMappingsDialog)


def test_panel_dialog_rule_round_trips_through_disk(app, tmp_path):
    """Open via panel helper, set a row, save, reload, confirm rule survived."""
    panel = ProvidersPanel(str(tmp_path))
    dlg = panel.open_symbol_mappings_dialog()
    dlg.set_rows([("yahoo", "BRK.B", "BRK-B", False)])
    dlg._on_accept()

    loaded = load_mappings(str(tmp_path))
    assert len(loaded.rules) == 1
    assert loaded.rules[0].provider == "yahoo"
    assert loaded.rules[0].replacement == "BRK-B"

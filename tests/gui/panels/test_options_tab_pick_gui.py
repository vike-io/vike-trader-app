"""6e GUI tests — OptionsTab.instrumentChosen signal fired on double-click.
Offscreen, drives OptionsTab directly (no full MainWindow needed here).

The context-menu path (menu.exec()) is interactive-only / UNTESTED-by-design (like the
live legend-gear): it opens only on an explicit user right-click and must never be triggered
in a headless test loop. The shared _name_at read IS covered by the double-click tests below —
both code paths call _name_at so the name-resolution logic is exercised headlessly.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PySide6")
from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.data.options.deribit import build_chain_from_summary  # noqa: E402
from vike_trader_app.ui.options_tab import OptionsTab  # noqa: E402


def _ms(y, m, d, h=8):
    from datetime import datetime, timezone
    return int(datetime(y, m, d, h, tzinfo=timezone.utc).timestamp() * 1000)


def _deribit_chain():
    rows = [
        {"instrument_name": "BTC-27JUN26-100000-C", "bid_price": 0.05, "ask_price": 0.06,
         "mark_price": 0.055, "mark_iv": 62.5, "open_interest": 120.0, "volume": 8.0,
         "underlying_price": 104000.0},
        {"instrument_name": "BTC-27JUN26-100000-P", "bid_price": 0.04, "ask_price": 0.05,
         "mark_price": 0.045, "mark_iv": 61.0, "open_interest": 90.0, "volume": 3.0,
         "underlying_price": 104000.0},
    ]
    return build_chain_from_summary("BTC", rows, "2026-06-27", _ms(2026, 6, 2))


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _find_name_cell(tab):
    """Find the first cell with a stashed instrument_name; return (row, col, name)."""
    for r in range(tab.table.rowCount()):
        for c in range(tab.table.columnCount()):
            it = tab.table.item(r, c)
            if it is not None and it.data(tab._NAME_ROLE):
                return r, c, it.data(tab._NAME_ROLE)
    return None


def test_double_click_emits_instrument_chosen(app):
    tab = OptionsTab()
    tab.set_chain(_deribit_chain())
    got = []
    tab.instrumentChosen.connect(got.append)
    hit = _find_name_cell(tab)
    assert hit is not None, "expected at least one tradable cell carrying an instrument_name"
    r, c, name = hit
    tab.table.cellDoubleClicked.emit(r, c)
    assert got == [name]
    assert name in ("BTC-27JUN26-100000-C", "BTC-27JUN26-100000-P")


def test_marker_row_double_click_is_inert(app):
    tab = OptionsTab()
    tab.set_chain(_deribit_chain())
    got = []
    tab.instrumentChosen.connect(got.append)
    # the ATM marker row carries no instrument_name on its spanned cell
    marker = tab._bar.atm_row
    assert marker >= 0
    tab.table.cellDoubleClicked.emit(marker, 0)
    assert got == []   # no emit for the marker row


def test_double_click_blank_cell_is_inert(app):
    tab = OptionsTab()
    tab.set_chain(_deribit_chain())
    got = []
    tab.instrumentChosen.connect(got.append)
    # find cells with NO stashed name (strike spine, blank cells) and double-click each
    for r in range(tab.table.rowCount()):
        for c in range(tab.table.columnCount()):
            it = tab.table.item(r, c)
            if it is not None and not it.data(tab._NAME_ROLE):
                tab.table.cellDoubleClicked.emit(r, c)
    assert got == []


def test_name_role_stashed_on_all_call_cells(app):
    """Every cell in the call half of a Deribit strike carries the call instrument_name."""
    tab = OptionsTab()
    chain = _deribit_chain()
    tab.set_chain(chain)
    # find any cell carrying the call name and confirm the name matches
    call_name = "BTC-27JUN26-100000-C"
    found = False
    for r in range(tab.table.rowCount()):
        for c in range(tab.table.columnCount()):
            it = tab.table.item(r, c)
            if it is not None and it.data(tab._NAME_ROLE) == call_name:
                found = True
    assert found, f"expected at least one cell with instrument_name={call_name!r}"


def test_yfinance_chain_cells_carry_no_name(app):
    """yfinance OptionQuote rows have instrument_name=None -> no _NAME_ROLE -> double-click inert."""
    from vike_trader_app.data.options.model import OptionChain, OptionQuote, StrikeRow, Expiry
    # Construct a minimal yfinance-style chain with instrument_name=None (the default)
    q_call = OptionQuote(strike=100.0, type="C", bid=1.0, ask=1.1)
    q_put = OptionQuote(strike=100.0, type="P", bid=0.9, ask=1.0)
    row = StrikeRow(strike=100.0, call=q_call, put=q_put)
    chain = OptionChain(
        underlying="SPY", asset_class="equity", underlying_price=100.0,
        expiry=Expiry(date="2026-07-18", dte=21, label="18 Jul"),
        asof_ms=_ms(2026, 6, 27), source="yfinance",
        rows=(row,),
    )
    tab = OptionsTab()
    tab.set_chain(chain)
    got = []
    tab.instrumentChosen.connect(got.append)
    for r in range(tab.table.rowCount()):
        for c in range(tab.table.columnCount()):
            tab.table.cellDoubleClicked.emit(r, c)
    assert got == [], "yfinance cells (instrument_name=None) must never emit instrumentChosen"

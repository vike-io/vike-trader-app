from PySide6 import QtGui, QtWidgets

from vike_trader_app.data.options.model import Expiry, OptionChain, OptionQuote, StrikeRow
from vike_trader_app.ui import theme
from vike_trader_app.ui.options_tab import CALL_COLS, COLS, OptionsTab


def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _chain():
    exp = Expiry(date="2026-07-02", dte=30, label="02 Jul")
    rows = (
        StrikeRow(strike=100.0,
                  call=OptionQuote(strike=100.0, type="C", bid=0.05, ask=0.06, mark=0.055,
                                   iv=0.62, delta=0.54, gamma=0.02, theta=-0.01, vega=0.4,
                                   open_interest=120, volume=8),
                  put=OptionQuote(strike=100.0, type="P", bid=0.04, ask=0.05, mark=0.045,
                                  iv=0.61, delta=-0.46, open_interest=90, volume=3)),
        StrikeRow(strike=110.0,
                  call=OptionQuote(strike=110.0, type="C", bid=0.02, ask=0.03, iv=0.64,
                                   open_interest=50, volume=1)),
    )
    return OptionChain("BTC", "crypto", 104.0, exp, 1, "deribit", rows)


def test_grid_renders_rows_and_columns():
    _app()
    tab = OptionsTab()
    tab.set_chain(_chain())
    assert tab.table.columnCount() == len(COLS)
    assert tab.table.rowCount() == 2
    strike_col = COLS.index("Strike")
    assert tab.table.item(0, strike_col).text() == "100.00"
    # a missing value renders as the em-dash placeholder
    put_bid_col = len(CALL_COLS) + 1 + COLS[len(CALL_COLS) + 1:].index("Bid")
    assert tab.table.item(1, put_bid_col).text() == "—"
    # a present quote with an unset greek (row-0 put has no theta) also shows the placeholder
    put_theta_col = len(CALL_COLS) + 1 + COLS[len(CALL_COLS) + 1:].index("Θ")
    assert tab.table.item(0, put_theta_col).text() == "—"


def test_call_and_put_cells_are_colored():
    _app()
    tab = OptionsTab()
    tab.set_chain(_chain())
    call_bid = tab.table.item(0, CALL_COLS.index("Bid"))
    put_bid_col = len(CALL_COLS) + 1 + COLS[len(CALL_COLS) + 1:].index("Bid")
    put_bid = tab.table.item(0, put_bid_col)
    assert call_bid.foreground().color() == QtGui.QColor(theme.UP)
    assert put_bid.foreground().color() == QtGui.QColor(theme.DOWN)


def test_status_message_no_modal():
    _app()
    tab = OptionsTab()
    tab.set_status("Deribit unreachable")  # must not raise / pop a dialog
    assert "Deribit" in tab.status_label.text()

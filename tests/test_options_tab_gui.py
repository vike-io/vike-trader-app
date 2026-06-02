import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")  # skip cleanly in the non-UI CI job (no PySide6 there)

from PySide6 import QtCore, QtWidgets  # noqa: E402

from vike_trader_app.data.options import columns as C  # noqa: E402
from vike_trader_app.data.options.model import Expiry, OptionChain, OptionQuote, StrikeRow  # noqa: E402
from vike_trader_app.ui.options_tab import OptionsTab  # noqa: E402


def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _chain():
    exp = Expiry(date="2026-07-02", dte=30, label="02 Jul")
    rows = (
        StrikeRow(strike=7595.0,
                  call=OptionQuote(strike=7595.0, type="C", bid=16.5, ask=16.9, last=15.1,
                                   mark=16.7, iv=0.1817, open_interest=10, volume=1324),
                  put=OptionQuote(strike=7595.0, type="P", bid=10.5, ask=10.7, last=11.4,
                                  mark=10.6, iv=0.1817, open_interest=5, volume=1089)),
        StrikeRow(strike=7605.0,
                  call=OptionQuote(strike=7605.0, type="C", bid=11.0, ask=11.2, iv=0.1764,
                                   volume=1242)),  # no put at 7605
    )
    return OptionChain("ES", "equity", 7600.75, exp, 1, "polygon", rows)


def _cols(table, label):
    return [c for c in range(table.columnCount())
            if table.horizontalHeaderItem(c).text() == label]


def test_chain_view_columns_and_atm_marker_row():
    _app()
    tab = OptionsTab()
    tab.set_chain(_chain())
    # calls + [Strike, IV] + puts
    assert tab.table.columnCount() == 2 * len(C.CHAIN_FIELDS) + 2
    for label in ("Theor", "Spread", "Distance", "Rel dist", "Bid %", "Ann bid %", "Ann ask %",
                  "LTP", "Strike", "IV"):
        assert _cols(tab.table, label), f"missing column {label}"
    # 2 strikes + 1 spanned ATM marker row, inserted at the first strike >= spot (7605)
    assert tab.table.rowCount() == 3
    atm = next(r for r in range(tab.table.rowCount())
               if tab.table.columnSpan(r, 0) == tab.table.columnCount())
    assert atm == 1 and "7,600.75" in tab.table.item(atm, 0).text()


def test_volume_columns_have_bars_and_missing_cells_dash():
    _app()
    tab = OptionsTab()
    tab.set_chain(_chain())
    vcols = _cols(tab.table, "Volume")
    assert len(vcols) == 2
    assert tab._bar.call_col in vcols and tab._bar.put_col in vcols
    # call volume on the first strike (row 0) is the max -> bar fraction 1.0
    assert tab.table.item(0, tab._bar.call_col).data(QtCore.Qt.UserRole) == pytest.approx(1.0)
    # 7605 (row 2, after the ATM row at 1) has no put -> put Bid renders as the em-dash
    put_bid = 2 + len(C.CHAIN_FIELDS) + C.CHAIN_FIELDS.index("bid")  # strike_col(=11)+2 + idx
    assert tab.table.item(2, put_bid).text() == "—"


def test_greeks_toggle_switches_column_set():
    _app()
    tab = OptionsTab()
    tab.set_chain(_chain())
    tab.view_toggle.setCurrentText("Greeks")
    tab._on_view_changed()
    assert tab.table.columnCount() == 2 * len(C.GREEKS_FIELDS) + 2
    for label in ("Δ", "Γ", "Θ", "V", "Strike", "IV"):
        assert _cols(tab.table, label), f"missing column {label}"


def test_grouped_view_collapsible_expiry_sections():
    _app()
    tab = OptionsTab()
    ch1 = _chain()  # 02 Jul, 2 strikes
    ch2 = OptionChain("ES", "equity", 7600.75, Expiry("2026-08-01", 60, "01 Aug"), 1, "polygon",
                      (StrikeRow(strike=7600.0,
                                 call=OptionQuote(strike=7600.0, type="C", bid=20.0, ask=21.0,
                                                  iv=0.20, volume=100)),))
    tab.set_chains([ch1, ch2])
    # two group headers, each a full-width spanned row tracked in _group_rows
    assert len(tab._group_rows) == 2
    g1, g2 = sorted(tab._group_rows)
    assert tab.table.columnSpan(g1, 0) == tab.table.columnCount()
    # first expiry expanded, the rest collapsed by default
    assert not tab.table.isRowHidden(tab._group_rows[g1][0])
    assert tab.table.isRowHidden(tab._group_rows[g2][0])
    # clicking the first header collapses its section
    tab._on_cell_clicked(g1, 0)
    assert tab.table.isRowHidden(tab._group_rows[g1][0])
    assert "▸" in tab.table.item(g1, 0).text()


def test_column_header_sort_reorders_and_drops_atm_marker():
    _app()
    tab = OptionsTab()
    exp = Expiry(date="2026-07-02", dte=30, label="02 Jul")
    rows = (  # call volumes deliberately NOT in strike order
        StrikeRow(strike=100.0, call=OptionQuote(strike=100.0, type="C", volume=10)),
        StrikeRow(strike=110.0, call=OptionQuote(strike=110.0, type="C", volume=99)),
        StrikeRow(strike=120.0, call=OptionQuote(strike=120.0, type="C", volume=50)),
    )
    tab.set_chain(OptionChain("X", "equity", 105.0, exp, 1, "polygon", rows))
    assert tab.table.rowCount() == 4  # 3 strikes + ATM marker (spot 105 between 100 and 110)
    strike_col = _cols(tab.table, "Strike")[0]

    tab._on_header_clicked(tab._bar.call_col)  # sort by call Volume (desc)
    assert tab._sort[0] == "volume"
    spanned = [r for r in range(tab.table.rowCount())
               if tab.table.columnSpan(r, 0) == tab.table.columnCount()]
    assert spanned == [] and tab.table.rowCount() == 3  # ATM marker dropped while sorted
    assert [tab.table.item(r, strike_col).text() for r in range(3)] == ["110.00", "120.00", "100.00"]

    tab._on_header_clicked(strike_col)  # back to strike order
    assert tab._sort is None
    assert any(tab.table.columnSpan(r, 0) == tab.table.columnCount()
               for r in range(tab.table.rowCount()))  # ATM marker restored


def test_exp_range_days_mapping():
    _app()
    tab = OptionsTab()
    assert tab.exp_range_days() == 30  # default "Next 30d"
    tab.exp_range.setCurrentText("All")
    assert tab.exp_range_days() is None
    tab.exp_range.setCurrentText("Next 90d")
    assert tab.exp_range_days() == 90


def test_status_message_no_modal():
    _app()
    tab = OptionsTab()
    tab.set_status("Polygon unreachable")  # must not raise / pop a dialog
    assert "Polygon" in tab.status_label.text()


def test_status_flags_free_yfinance_feed_only():
    _app()
    tab = OptionsTab()
    exp = Expiry(date="2026-07-02", dte=30, label="02 Jul")
    yf = OptionChain("MSFT", "equity", 460.0, exp, 1, "yfinance",
                     (StrikeRow(strike=460.0, call=OptionQuote(strike=460.0, type="C")),))
    tab.set_chain(yf)
    assert "free feed" in tab.status_label.text()           # nags on the free fallback
    tab.set_chain(_chain())                                  # source="polygon"
    assert "free feed" not in tab.status_label.text()        # silent on a real backend

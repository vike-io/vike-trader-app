"""Trades-table row formatting (Qt-free)."""

from vike_trader_app.core.model import Trade
from vike_trader_app.ui.tables import TRADE_HEADERS, trade_rows


def test_headers_present():
    assert TRADE_HEADERS[0] == "#"
    assert "PnL" in TRADE_HEADERS


def test_trade_row_formats_fields():
    t = Trade(
        entry_price=100.0,
        exit_price=110.0,
        size=2.0,
        pnl=20.0,
        fees=0.5,
        entry_ts=0,
        exit_ts=60_000,
    )
    rows = trade_rows([t])
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == "1"  # trade number
    assert row[1] == "1970-01-01 00:00"  # entry time (UTC)
    assert row[2] == "100.00"
    assert row[3] == "1970-01-01 00:01"  # exit time (UTC)
    assert row[4] == "110.00"
    assert row[5] == "2.0000"
    assert row[6] == "+20.00"  # PnL keeps an explicit sign
    assert row[7] == "0.50"


def test_negative_pnl_keeps_sign():
    t = Trade(entry_price=100, exit_price=90, size=1, pnl=-10.0)
    assert trade_rows([t])[0][6] == "-10.00"


def test_empty_trades_is_empty():
    assert trade_rows([]) == []

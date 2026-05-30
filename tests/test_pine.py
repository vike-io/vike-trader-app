"""TradingView Pine export tests."""

from vike_trader_app.analysis.pine import to_pine
from vike_trader_app.core.model import Trade


def test_pine_export_contains_markers_and_timestamps():
    trades = [
        Trade(entry_price=100, exit_price=110, size=1, pnl=10, fees=0, entry_ts=60_000, exit_ts=180_000),
    ]
    src = to_pine(trades, title="BTC test")
    assert "//@version=5" in src
    assert 'indicator("BTC test"' in src
    assert "60000" in src and "180000" in src   # entry/exit epoch-ms
    assert "plotshape" in src


def test_pine_export_handles_no_trades():
    src = to_pine([], title="empty")
    assert "//@version=5" in src
    assert "array.from()" not in src  # must stay valid Pine with zero markers

"""Chart-data helper tests (Qt-free prep for plotting)."""

import pytest

from vike_trader_app.core.model import Trade
from vike_trader_app.ui.chartdata import Marker, equity_points, trade_markers


def test_trade_markers_emit_entry_then_exit():
    t = Trade(entry_price=100, exit_price=110, size=1, pnl=10, entry_ts=1, exit_ts=2)
    assert trade_markers([t]) == [
        Marker(ts=1, price=100, kind="entry"),
        Marker(ts=2, price=110, kind="exit"),
    ]


def test_trade_markers_empty():
    assert trade_markers([]) == []


def test_equity_points_zips_ts_and_equity():
    xs, ys = equity_points([10, 20, 30], [100.0, 101.0, 102.0])
    assert xs == [10, 20, 30]
    assert ys == [100.0, 101.0, 102.0]


def test_equity_points_length_mismatch_raises():
    with pytest.raises(ValueError):
        equity_points([1, 2], [100.0])

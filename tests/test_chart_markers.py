"""Offscreen tests for TradeStation-style trade markers on the price chart.

Long  -> buy  (up-arrow, below the bar, blue) + exit (down-arrow, above, white)
Short -> sell (down-arrow, above the bar, red) + exit (up-arrow, below, white)
plus a dotted entry->exit connector per trade.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.core.model import Bar, Trade  # noqa: E402
from vike_trader_app.ui import theme  # noqa: E402
from vike_trader_app.ui.chart import PriceChart  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _bars(n=10):
    return [Bar(ts=i * 60_000, open=100, high=101, low=99, close=100) for i in range(n)]


def test_long_trade_markers(app):
    t = Trade(entry_price=100, exit_price=105, size=1, pnl=5, entry_ts=60_000, exit_ts=180_000)
    pc = PriceChart()
    pc.set_data(_bars(), [t])
    assert [(m["symbol"], m["below"]) for m in pc._markers] == [
        ("arrow_up", True),     # buy: up-arrow below the bar
        ("arrow_down", False),  # long exit: down-arrow above
    ]
    assert pc._markers[0]["color"] == theme.BLUE
    assert pc._markers[1]["color"] == "#ffffff"
    assert pc._conn == [(1, 100, 3, 105)]


def test_short_trade_markers(app):
    t = Trade(entry_price=104, exit_price=102, size=-1, pnl=2, entry_ts=300_000, exit_ts=420_000)
    pc = PriceChart()
    pc.set_data(_bars(), [t])
    assert [(m["symbol"], m["below"]) for m in pc._markers] == [
        ("arrow_down", False),  # sell: down-arrow above the bar
        ("arrow_up", True),     # short exit: up-arrow below
    ]
    assert pc._markers[0]["color"] == theme.DOWN
    assert pc._markers[1]["color"] == "#ffffff"


def test_markers_reveal_with_show_upto(app):
    t = Trade(entry_price=100, exit_price=105, size=1, pnl=5, entry_ts=60_000, exit_ts=180_000)
    pc = PriceChart()
    pc.set_data(_bars(), [t])
    pc.show_upto(2)  # entry at index 1 revealed; exit at index 3 not yet
    assert len(pc._marker_scatter.data) == 1
    pc.show_upto(5)  # both revealed
    assert len(pc._marker_scatter.data) == 2

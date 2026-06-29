"""Engine fidelity: gap-open fill normalization + intrabar SL/TP both-hit pessimism.

Two spec'd-but-previously-unimplemented behaviours (core/__init__ "Planned"):
  * a bar that OPENS past a stop/limit trigger fills at the gapped open (adverse for stops,
    favourable for limits) — not optimistically at the trigger price that never traded;
  * when a stop-loss AND a take-profit (both reducing the position) trigger in one bar, OHLC can't
    say which hit first, so the engine resolves pessimistically (stop first) and flags the bar.
"""

import pytest

from vike_trader_app.core.engine import SingleSymbolEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.orders import Order, order_fill_price
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy


def _bar(ts, o, h, l, c):
    return Bar(ts=ts, open=o, high=h, low=l, close=c, volume=1.0)


# --- gap-open normalization (pure order_fill_price) ---------------------------------------------

def test_sell_stop_gap_down_fills_at_open_not_trigger():
    # protective sell-stop @95; bar gaps DOWN and opens at 90 -> fill at 90 (worse), not 95
    o = Order("stop", side=-1, size=1.0, price=95.0)
    assert order_fill_price(o, _bar(0, 90, 92, 88, 91)) == pytest.approx(90.0)
    # no gap (opens above the stop): fills exactly at the trigger
    o2 = Order("stop", side=-1, size=1.0, price=95.0)
    assert order_fill_price(o2, _bar(0, 100, 100, 94, 96)) == pytest.approx(95.0)


def test_buy_stop_gap_up_fills_at_open_not_trigger():
    o = Order("stop", side=1, size=1.0, price=105.0)
    assert order_fill_price(o, _bar(0, 110, 112, 108, 111)) == pytest.approx(110.0)  # gap up -> worse


def test_buy_limit_gap_down_gives_price_improvement():
    # buy-limit @95; bar gaps DOWN to open 90 -> filled BETTER at 90, not 95
    o = Order("limit", side=1, size=1.0, price=95.0)
    assert order_fill_price(o, _bar(0, 90, 92, 88, 91)) == pytest.approx(90.0)
    # no gap (opens above the limit, dips to it): fills at the limit
    o2 = Order("limit", side=1, size=1.0, price=95.0)
    assert order_fill_price(o2, _bar(0, 100, 101, 94, 96)) == pytest.approx(95.0)


def test_sell_limit_gap_up_gives_price_improvement():
    o = Order("limit", side=-1, size=1.0, price=105.0)
    assert order_fill_price(o, _bar(0, 110, 112, 108, 109)) == pytest.approx(110.0)  # gap up -> better


def test_trailing_stop_gap_down_fills_at_open():
    o = Order("trailing", side=-1, size=1.0, trail=5.0, extreme=110.0)  # trigger = 105
    assert order_fill_price(o, _bar(0, 100, 101, 95, 96)) == pytest.approx(100.0)  # gapped below 105


# --- intrabar SL/TP both-hit (engine, pessimistic) ---------------------------------------------

class _BracketLong(Strategy):
    """Buy, then rest a protective sell-stop (loss) AND a sell-limit (profit) bracketing the entry."""

    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)                       # market -> fills @ bar1 open (100)
        elif self.index == 1:
            self.stop_sell(1.0, 95.0)           # stop-loss @95
            self.limit_sell(1.0, 110.0)         # take-profit @110


def test_intrabar_both_hit_resolves_pessimistically_stop_first():
    bars = [
        _bar(0, 100, 101, 99, 100),         # buy submitted
        _bar(60_000, 100, 100, 100, 100),   # buy fills @100; bracket submitted
        _bar(120_000, 100, 112, 94, 100),   # BOTH hit: high 112>=110 (TP) and low 94<=95 (SL)
        _bar(180_000, 100, 101, 99, 100),
    ]
    eng = SingleSymbolEngine(bars, _BracketLong())
    result = eng.run()
    # exactly ONE closing trade (no over-close / flip), taken at the STOP, and the bar is flagged
    assert eng.position.size == pytest.approx(0.0)
    assert len(result.trades) == 1
    assert result.trades[0].exit_price == pytest.approx(95.0)   # pessimistic: the loss, not the 110 profit
    assert result.trades[0].pnl == pytest.approx(-5.0)
    assert result.intrabar_both_hit == 1


class _BracketTPOnly(_BracketLong):
    pass


def test_only_tp_hit_is_not_flagged_and_takes_profit():
    bars = [
        _bar(0, 100, 101, 99, 100),
        _bar(60_000, 100, 100, 100, 100),    # buy @100; bracket submitted
        _bar(120_000, 100, 112, 99, 108),    # only TP hit (low 99 > 95 stop)
        _bar(180_000, 100, 101, 99, 100),
    ]
    eng = SingleSymbolEngine(bars, _BracketTPOnly())
    result = eng.run()
    assert len(result.trades) == 1
    assert result.trades[0].exit_price == pytest.approx(110.0)  # profit target
    assert result.intrabar_both_hit == 0

"""Backtest-engine behavior tests (Phase 1, step 1)."""

import pytest

from vike_trader_app.core.engine import SingleSymbolEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy


def _bar(ts, o, c):
    return Bar(ts=ts, open=o, high=max(o, c) + 1, low=min(o, c) - 1, close=c, volume=1.0)


def _bars():
    # opens: 100, 110, 120, 130
    return [
        _bar(0, 100, 100),
        _bar(60_000, 110, 110),
        _bar(120_000, 120, 120),
        _bar(180_000, 130, 130),
    ]


class _BuyThenClose(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)  # market -> fills at NEXT bar's open (110)
        elif self.index == 2:
            self.close()  # market -> fills at NEXT bar's open (130)


def test_buy_then_close_realizes_pnl_at_next_open_no_fees():
    result = SingleSymbolEngine(_bars(), _BuyThenClose(), fee_rate=0.0, cash=10_000.0).run()
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.entry_price == 110.0  # next-open after the index-0 buy (no look-ahead)
    assert t.exit_price == 130.0  # next-open after the index-2 close
    assert t.pnl == 20.0
    assert result.final_equity == 10_020.0


def test_fees_reduce_equity():
    result = SingleSymbolEngine(_bars(), _BuyThenClose(), fee_rate=0.001, cash=10_000.0).run()
    t = result.trades[0]
    assert t.pnl == 20.0  # gross price PnL
    assert t.fees == pytest.approx(0.11 + 0.13)  # entry 110*.001 + exit 130*.001
    assert result.final_equity == pytest.approx(10_000 + 20 - 0.24)


class _RecordHigherTF(Strategy):
    """Records, at each base bar, the closes of the completed 2m bars it can see."""

    def __init__(self):
        super().__init__()
        self.seen = []  # list of (base_index, [completed 2m closes])

    def on_bar(self, bar):
        self.seen.append((self.index, [b.close for b in self.bars("2m")]))


def test_engine_exposes_completed_higher_tf_bars_without_lookahead():
    # base = 1m bars at ts 0,60k,120k,180k with closes 100,110,120,130
    bars = [
        _bar(0, 100, 100),
        _bar(60_000, 110, 110),
        _bar(120_000, 120, 120),
        _bar(180_000, 130, 130),
    ]
    strat = _RecordHigherTF()
    SingleSymbolEngine(bars, strat, timeframes=["2m"]).run()

    # 2m windows: [0,120k) -> closes from bars at 0 & 60k -> coarse close 110
    #             [120k,240k) -> bars at 120k & 180k -> coarse close 130 (forms after run)
    # Visibility (deliver-on-complete):
    #   base idx0 (ts0,   window0): no completed coarse yet               -> []
    #   base idx1 (ts60k, window0): still inside window0                  -> []
    #   base idx2 (ts120k,window1): window0 closed -> see its coarse(110) -> [110]
    #   base idx3 (ts180k,window1): still window1                         -> [110]
    assert strat.seen == [(0, []), (1, []), (2, [110.0]), (3, [110.0])]


class _RecordForming(Strategy):
    def __init__(self):
        super().__init__()
        self.forming_seen = []  # (base_index, forming-2m-bar-or-None as (high, close))

    def on_bar(self, bar):
        f = self.forming("2m")
        self.forming_seen.append((self.index, None if f is None else (f.high, f.close)))


def test_engine_exposes_forming_higher_tf_bar():
    bars = [
        _bar(0, 100, 100),     # high 101, low 99
        _bar(60_000, 110, 110),
        _bar(120_000, 120, 120),
        _bar(180_000, 130, 130),
    ]
    strat = _RecordForming()
    SingleSymbolEngine(bars, strat, timeframes=["2m"]).run()

    # window0 = [0,120k): after bar0 forming=(high101, close100); after bar1 forming=(high111, close110)
    # window1 = [120k,240k): after bar2 forming=(high121, close120); after bar3 forming=(high131, close130)
    assert strat.forming_seen == [
        (0, (101.0, 100.0)),
        (1, (111.0, 110.0)),
        (2, (121.0, 120.0)),
        (3, (131.0, 130.0)),
    ]


class _HTFFilterStrategy(Strategy):
    """Go long on the base bar only when the last completed 4m close is rising."""

    def on_bar(self, bar):
        closes = [b.close for b in self.bars("4m")]
        if len(closes) >= 2 and closes[-1] > closes[-2] and self.position.size == 0:
            self.buy(1.0)


def test_mtf_filter_strategy_runs_and_only_uses_completed_htf():
    # 8 one-minute bars, rising closes; 4m windows: [0,240k) and [240k,480k)
    bars = [_bar(i * 60_000, 100 + i, 100 + i) for i in range(8)]
    strat = _HTFFilterStrategy()
    result = SingleSymbolEngine(bars, strat, timeframes=["4m"]).run()
    # Only 2 completed 4m windows ever exist, and the 2nd completes after the run,
    # so the strategy never sees two completed rising 4m bars -> no trade.
    # Asserts no look-ahead false-positive.
    assert result.trades == []


def test_mtf_filter_strategy_trades_with_three_htf_windows():
    # 12 one-minute bars -> 4m windows [0,240k),[240k,480k),[480k,720k)
    bars = [_bar(i * 60_000, 100 + i, 100 + i) for i in range(12)]
    strat = _HTFFilterStrategy()
    SingleSymbolEngine(bars, strat, timeframes=["4m"]).run()
    # By the third window two completed rising 4m bars are visible -> opens a position.
    assert strat.position.size > 0


def test_slippage_worsens_fill_prices():
    # buy fills at next open *(1+slip); sell(close) at next open *(1-slip)
    result = SingleSymbolEngine(_bars(), _BuyThenClose(), slippage=0.01).run()
    t = result.trades[0]
    assert t.entry_price == pytest.approx(110.0 * 1.01)  # 111.1
    assert t.exit_price == pytest.approx(130.0 * 0.99)  # 128.7
    assert t.pnl == pytest.approx((130.0 * 0.99) - (110.0 * 1.01))


class _BuyHold(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)


def test_funding_charges_held_position():
    # flat prices isolate funding; bar[1] charges 1% funding on a 1-unit long.
    bars = [
        Bar(ts=0, open=100, high=101, low=99, close=100, volume=1.0),
        Bar(ts=60_000, open=100, high=101, low=99, close=100, volume=1.0, funding=0.01),
        Bar(ts=120_000, open=100, high=101, low=99, close=100, volume=1.0),
    ]
    result = SingleSymbolEngine(bars, _BuyHold(), cash=10_000.0).run()
    # buy 1 @100 -> cash 9900; funding 1*100*0.01 = 1 -> cash 9899; equity 9899 + 100 = 9999
    assert result.final_equity == pytest.approx(9_999.0)


# --- partial-reduce / cross-zero-flip fills (regression: the close branch used to ALWAYS full-close) ---

class _ReduceToFour(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.order_target_shares(10)   # buy 10 -> fills bar 1 @110
        elif self.index == 1:
            self.order_target_shares(4)    # reduce to 4 (sell 6) -> fills bar 2 @120


def test_partial_reduce_keeps_remainder_and_books_only_closed_units():
    eng = SingleSymbolEngine(_bars(), _ReduceToFour(), fee_rate=0.0, cash=10_000.0)
    eng.run()
    assert eng.position.size == 4.0                              # remainder kept (was wrongly zeroed)
    assert len(eng.trades) == 1
    assert eng.trades[-1].size == 6.0                            # only the 6 closed units (was 10)
    assert eng.trades[-1].pnl == pytest.approx((120 - 110) * 6)  # 60 on the closed portion (was 100)


class _FlipLongToShort(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.order_target_shares(5)    # long 5 -> fills bar 1 @110
        elif self.index == 1:
            self.order_target_shares(-3)   # flip to short 3 (sell 8) -> fills bar 2 @120


def test_cross_zero_flip_closes_then_opens_opposite_side():
    eng = SingleSymbolEngine(_bars(), _FlipLongToShort(), fee_rate=0.0, cash=10_000.0)
    eng.run()
    assert eng.position.size == -3.0                             # opposite side opened (was left flat)
    assert eng.position.avg_price == pytest.approx(120.0)        # at the flip fill price
    assert len(eng.trades) == 1
    assert eng.trades[-1].size == 5.0                            # closed the original 5 long
    assert eng.trades[-1].pnl == pytest.approx((120 - 110) * 5)  # 50 on the closed long

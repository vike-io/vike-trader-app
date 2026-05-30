"""Resting limit/stop order tests + risk-based sizing."""

import pytest

from vike_trader_app.core.engine import BacktestEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.strategy import Strategy


def _bar(ts, o, h, l, c):
    return Bar(ts=ts, open=o, high=h, low=l, close=c, volume=1.0)


class _LimitBuy(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.limit_buy(1.0, 95.0)  # rest until price dips to 95


def test_limit_buy_fills_only_when_price_reaches_limit():
    bars = [
        _bar(0, 100, 101, 99, 100),       # submit limit @95
        _bar(60_000, 100, 102, 98, 101),  # low 98 > 95 -> no fill (rests)
        _bar(120_000, 100, 101, 94, 96),  # low 94 <= 95 -> fills at 95
    ]
    eng = BacktestEngine(bars, _LimitBuy())
    eng.run()
    assert eng.position.size == pytest.approx(1.0)
    assert eng.position.avg_price == pytest.approx(95.0)


class _StopBuy(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.stop_buy(1.0, 105.0)  # breakout entry


def test_stop_buy_fills_on_breakout():
    bars = [
        _bar(0, 100, 101, 99, 100),       # submit stop @105
        _bar(60_000, 100, 104, 99, 102),  # high 104 < 105 -> no fill
        _bar(120_000, 103, 106, 102, 105),  # high 106 >= 105 -> fills at 105
    ]
    eng = BacktestEngine(bars, _StopBuy())
    eng.run()
    assert eng.position.size == pytest.approx(1.0)
    assert eng.position.avg_price == pytest.approx(105.0)


class _LimitNeverFills(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.limit_buy(1.0, 1.0)  # absurdly low -> never triggers


def test_resting_order_that_never_triggers_makes_no_trade():
    bars = [_bar(i * 60_000, 100, 101, 99, 100) for i in range(4)]
    eng = BacktestEngine(bars, _LimitNeverFills())
    result = eng.run()
    assert result.trades == []
    assert eng.position.size == 0.0


class _CancelAfterSubmit(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.limit_buy(1.0, 95.0)
        elif self.index == 1:
            self.cancel_all()  # cancel before it can fill


def test_cancel_all_removes_resting_orders():
    bars = [
        _bar(0, 100, 101, 99, 100),
        _bar(60_000, 100, 101, 99, 100),
        _bar(120_000, 100, 101, 90, 95),  # would have filled @95 if not cancelled
    ]
    eng = BacktestEngine(bars, _CancelAfterSubmit())
    result = eng.run()
    assert result.trades == []
    assert eng.position.size == 0.0


def test_risk_to_qty_sizes_by_stop_distance():
    s = Strategy()
    # risk 100 with a 5-wide stop -> 20 units
    assert s.risk_to_qty(100.0, entry=100.0, stop=95.0) == pytest.approx(20.0)
    assert s.risk_to_qty(100.0, entry=100.0, stop=100.0) == 0.0  # no distance -> 0


class _TrailingExit(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)                       # market -> fills at bar 1 open (100)
        elif self.index == 1:
            self.trailing_stop(1.0, trail=5.0)  # protective trailing sell-stop


def test_trailing_stop_exits_after_pullback_from_peak():
    bars = [
        _bar(0, 100, 101, 99, 100),       # buy submitted
        _bar(60_000, 100, 100, 100, 100), # buy fills @100; trailing submitted, extreme=100
        _bar(120_000, 100, 110, 100, 110),# trigger 95, low 100 > 95 -> no fill; extreme -> 110
        _bar(180_000, 108, 108, 104, 104),# trigger 110-5=105, low 104 <= 105 -> sell @105
    ]
    eng = BacktestEngine(bars, _TrailingExit())
    result = eng.run()
    assert len(result.trades) == 1
    assert result.trades[0].exit_price == pytest.approx(105.0)
    assert result.trades[0].pnl == pytest.approx(5.0)  # 105 - 100 entry


class _DCA(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)   # fills @ bar1 open
        elif self.index == 1:
            self.buy(1.0)   # add (DCA) -> fills @ bar2 open


def test_dca_adds_to_position_with_weighted_avg():
    bars = [
        _bar(0, 100, 101, 99, 100),
        _bar(60_000, 100, 101, 99, 100),   # first fill @100
        _bar(120_000, 120, 121, 119, 120), # add fill @120
        _bar(180_000, 130, 131, 129, 130),
    ]
    eng = BacktestEngine(bars, _DCA())
    eng.run()
    assert eng.position.size == pytest.approx(2.0)
    assert eng.position.avg_price == pytest.approx(110.0)  # (100 + 120) / 2


class _Hold(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)


def test_drawdown_tracks_equity_drop_from_peak():
    bars = [
        _bar(0, 100, 101, 99, 100),
        _bar(60_000, 100, 101, 99, 100),   # buy fills @100, equity flat
        _bar(120_000, 150, 151, 149, 150), # peak (equity up)
        _bar(180_000, 120, 121, 119, 120), # pullback -> drawdown from peak
    ]

    seen = {}

    class _Probe(_Hold):
        def on_bar(self, bar):
            super().on_bar(bar)
            seen[self.index] = self.drawdown

    eng = BacktestEngine(bars, _Probe(), cash=10_000.0)
    eng.run()
    assert seen[2] == pytest.approx(0.0)        # at the peak, no drawdown
    assert seen[3] > 0.0                          # after pullback, positive drawdown

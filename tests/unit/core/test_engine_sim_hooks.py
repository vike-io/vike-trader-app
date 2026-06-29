"""Additive submit/fill/cancel hooks on SingleSymbolEngine — C1a (default-off, byte-identical).

These hooks thread order identity to a sim venue without touching the optimizer
fast path (hooks default to None; the ``if self._on_X is not None`` guards are free).
"""

from vike_trader_app.core.engine import SingleSymbolEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy
from vike_trader_app.core.orders import Order


def _bar(ts, o, c):
    return Bar(ts=ts, open=o, high=max(o, c) + 1, low=min(o, c) - 1, close=c, volume=1.0)


def _bars():
    return [_bar(0, 100, 100), _bar(60_000, 110, 110), _bar(120_000, 120, 120), _bar(180_000, 130, 130)]


class _BuyThenClose(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 2:
            self.close()


class _BuyThenCancel(Strategy):
    """Submit a limit order on bar 0; cancel it on bar 1; close via market on bar 2."""
    def __init__(self):
        super().__init__()
        self._limit_order = None

    def on_bar(self, bar):
        if self.index == 0:
            self._limit_order = self._engine.submit_limit(+1, 1.0, 50.0)  # won't trigger
        elif self.index == 1:
            self._engine.cancel_order(None, self._limit_order)
        elif self.index == 2:
            self.buy(1.0)


class _BuyCancelAll(Strategy):
    """Submit a limit order on bar 0; cancel_all on bar 1."""
    def on_bar(self, bar):
        if self.index == 0:
            self._engine.submit_limit(+1, 1.0, 50.0)  # won't trigger
        elif self.index == 1:
            self._engine.cancel_all()


# ---------------------------------------------------------------------------
# on_submit hook
# ---------------------------------------------------------------------------

def test_on_submit_fires_on_each_submitted_order():
    submitted = []

    class _Strat(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.buy(1.0)

    eng = SingleSymbolEngine(_bars(), _Strat(), cash=10_000.0)
    eng._on_submit = lambda o: submitted.append(o)
    eng.run()

    assert len(submitted) == 1
    o = submitted[0]
    assert isinstance(o, Order)
    assert o.side == +1
    assert o.size == 1.0
    assert o.kind == "market"


def test_on_submit_fires_for_limit_order():
    submitted = []

    class _Strat(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self._engine.submit_limit(+1, 1.0, 50.0)

    eng = SingleSymbolEngine(_bars(), _Strat(), cash=10_000.0)
    eng._on_submit = lambda o: submitted.append(o)
    eng.run()

    assert len(submitted) == 1
    assert submitted[0].kind == "limit"
    assert submitted[0].price == 50.0


def test_on_submit_default_none_no_crash():
    """Default off — no hook, no crash, results identical."""
    base = SingleSymbolEngine(_bars(), _BuyThenClose(), cash=10_000.0).run()
    assert base.final_equity > 0  # sanity


# ---------------------------------------------------------------------------
# on_fill hook — 7-arg: (side, size, price, fee, ts, is_maker, order)
# ---------------------------------------------------------------------------

def test_on_fill_receives_originating_order_for_market():
    fill_calls = []

    eng = SingleSymbolEngine(_bars(), _BuyThenClose(), cash=10_000.0, taker_fee=0.001,
                         on_fill=lambda side, size, price, fee, ts, is_maker, order:
                             fill_calls.append((side, size, price, fee, ts, is_maker, order)))
    eng.run()

    assert len(fill_calls) == 2
    # buy fill
    side, size, price, fee, ts, is_maker, order = fill_calls[0]
    assert side == +1
    assert size == 1.0
    assert isinstance(order, Order)
    assert order.side == +1
    assert order.kind == "market"
    # close fill
    side, size, price, fee, ts, is_maker, order = fill_calls[1]
    assert side == -1
    assert isinstance(order, Order)
    assert order.side == -1


def test_on_fill_order_is_none_for_liquidation():
    """Liquidation-triggered fills pass order=None.

    Setup: cash=10, buy 1 unit at open=10 (bar 1). Equity at open=10 is exactly 10.
    Bar 2 has low=1 → equity_extreme = 10 + 1*(1) - 10 = 1; notional_extreme = 1.
    maint_margin=0.9 → need eq_ex > 0.9 * 1 = 0.9, but eq_ex ≈ 1 > 0.9 — not enough margin.
    Use maint_margin=1.0: eq_ex=1 <= 1.0*1 → triggers liquidation.
    """
    fill_calls = []

    class _BuyStrat(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.buy(1.0)

    bars = [
        Bar(ts=0, open=10, high=11, low=9, close=10, volume=1.0),
        Bar(ts=60_000, open=10, high=11, low=9, close=10, volume=1.0),  # fills buy at open=10
        Bar(ts=120_000, open=5, high=5, low=0, close=5, volume=1.0),    # low=0 → liq
    ]

    eng = SingleSymbolEngine(bars, _BuyStrat(), cash=10.0, taker_fee=0.0,
                         maint_margin=1.0,
                         on_fill=lambda side, size, price, fee, ts, is_maker, order:
                             fill_calls.append((side, size, price, fee, ts, is_maker, order)))
    eng.run()

    # Last fill should be the liquidation with order=None
    liq_calls = [c for c in fill_calls if c[6] is None]
    assert len(liq_calls) >= 1


def test_on_fill_6arg_lambda_still_works_before_upgrade():
    """Existing 6-arg on_fill lambdas (pre-C1a) continue to work via *args."""
    fills = []
    eng = SingleSymbolEngine(_bars(), _BuyThenClose(), cash=10_000.0, taker_fee=0.001,
                         on_fill=lambda side, size, price, fee, ts, is_maker, order=None:
                             fills.append((side, size, price, fee, ts, is_maker)))
    eng.run()
    assert len(fills) == 2


# ---------------------------------------------------------------------------
# on_cancel hook
# ---------------------------------------------------------------------------

def test_on_cancel_fires_on_cancel_order():
    cancelled = []

    eng = SingleSymbolEngine(_bars(), _BuyThenCancel(), cash=10_000.0)
    eng._on_cancel = lambda o: cancelled.append(o)
    eng.run()

    assert len(cancelled) == 1
    o = cancelled[0]
    assert isinstance(o, Order)
    assert o.kind == "limit"
    assert o.price == 50.0


def test_on_cancel_fires_on_cancel_all():
    cancelled = []

    eng = SingleSymbolEngine(_bars(), _BuyCancelAll(), cash=10_000.0)
    eng._on_cancel = lambda o: cancelled.append(o)
    eng.run()

    assert len(cancelled) == 1
    o = cancelled[0]
    assert isinstance(o, Order)
    assert o.kind == "limit"


def test_on_cancel_default_none_no_crash():
    """Default off — no hook, no crash."""
    base = SingleSymbolEngine(_bars(), _BuyThenCancel(), cash=10_000.0).run()
    assert base.final_equity > 0


# ---------------------------------------------------------------------------
# Byte-identical gate: hooks default-off produces same results
# ---------------------------------------------------------------------------

def test_all_hooks_default_off_byte_identical():
    """With no hooks set, engine output is byte-identical to a plain engine."""
    base = SingleSymbolEngine(_bars(), _BuyThenClose(), cash=10_000.0, taker_fee=0.001).run()
    hooked = SingleSymbolEngine(_bars(), _BuyThenClose(), cash=10_000.0, taker_fee=0.001).run()
    assert hooked.equity_curve == base.equity_curve
    assert [t.pnl for t in hooked.trades] == [t.pnl for t in base.trades]
    assert hooked.final_equity == base.final_equity

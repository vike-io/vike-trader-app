"""Tests for LivePump — unified pump covering all three strategy types.

Verifies:
- (a) N=1 unified Strategy calling self.buy(bar.symbol, 1.0) routes the order.
- (b) Per-hub StrategyEventAdapter constructed for N>1 (len(pump._adapters) == N).
- (c) _dispatch_step fires the right on_bar variant for unified Strategy (per-symbol 1-arg)
  AND PortfolioStrategy (bundle 2-arg via _on_step).
- (d) N=1 SingleSymbolStrategy (old buy(1.0)) runs via the LiveSymbolShim.
- Alias: LivePump is LivePump.
"""

from __future__ import annotations

import warnings

import pytest

from vike_trader_app.core.model import Bar
from vike_trader_app.core.multi_symbol_engine import PortfolioStrategy
from vike_trader_app.core.strategy import Strategy, SingleSymbolStrategy
from vike_trader_app.exec.live_portfolio_pump import LivePump, LivePump


# ---------------------------------------------------------------------------
# Stubs (reuse same shape as test_live_portfolio_pump.py)
# ---------------------------------------------------------------------------

class _Hub:
    def __init__(self, venue: str, symbol: str):
        self.venue = venue
        self.symbol = symbol
        self.account = _Acct()
        self.submitted: list = []
        self.registry: dict = {}
        # Minimal EventBus stub so StrategyEventAdapter can subscribe
        self.bus = _Bus()

    def submit_ticket(self, req) -> None:
        self.submitted.append(req)

    def cancel_ticket(self, coid: str) -> None:
        pass


class _Bus:
    """Minimal EventBus stub (subscribe/unsubscribe/publish)."""

    def __init__(self):
        self._subs: list = []

    def subscribe(self, cb) -> None:
        self._subs.append(cb)

    def unsubscribe(self, cb) -> None:
        self._subs.remove(cb)

    def publish(self, event) -> None:
        for cb in list(self._subs):
            cb(event)


class _Acct:
    def __init__(self, bal: float = 10_000.0):
        self.balance = bal
        self.positions: dict = {}
        self.marks: dict = {}

    def set_mark(self, venue: str, symbol: str, px: float) -> None:
        self.marks[(venue, symbol)] = px

    def unrealized_pnl(self, venue: str, symbol: str, position_side: str = "BOTH") -> float:
        return 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BTC = "BTCUSDT"
_ETH = "ETHUSDT"


def _bar(ts: int, symbol: str = _BTC, close: float = 100.0) -> Bar:
    return Bar(ts=ts, open=close, high=close, low=close, close=close, symbol=symbol)


def _make_pump(strategy, symbols=(_BTC,)):
    """Build a pump with stub hubs that have buses (for adapter wiring)."""
    acct = _Acct()
    hubs = {sym: _Hub(venue="binance", symbol=sym) for sym in symbols}
    pump = LivePump(strategy, hubs, acct, now_ms=lambda: 999)
    return pump, hubs, acct


# ---------------------------------------------------------------------------
# Alias test
# ---------------------------------------------------------------------------

def test_alias_live_portfolio_pump_is_live_pump():
    """LivePump = LivePump (alias for backward compat)."""
    assert LivePump is LivePump


# ---------------------------------------------------------------------------
# (a) N=1 unified Strategy — buy(bar.symbol, 1.0) routes the order
# ---------------------------------------------------------------------------

class _UnifiedBuyStrategy(Strategy):
    """Unified Strategy: calls self.buy(bar.symbol, 1.0) on each bar."""

    WARMUP = 0

    def __init__(self):
        super().__init__()
        self.bar_calls: list[Bar] = []

    def on_bar(self, bar: Bar) -> None:
        self.bar_calls.append(bar)
        self.buy(bar.symbol, 1.0)


def test_n1_unified_strategy_buy_routes_order():
    """N=1: unified Strategy.buy(bar.symbol, 1.0) → order arrives in the hub.submitted list."""
    strat = _UnifiedBuyStrategy()
    pump, hubs, _ = _make_pump(strat, symbols=[_BTC])
    pump.start()

    b = _bar(ts=1000, symbol=_BTC)
    pump.feed_bar(_BTC, b)

    # on_bar was called once with the bar
    assert len(strat.bar_calls) == 1
    # Order was submitted to the BTC hub
    assert len(hubs[_BTC].submitted) == 1
    req = hubs[_BTC].submitted[0]
    assert req.side == +1
    assert req.qty == 1.0


def test_n1_unified_strategy_engine_is_live_engine():
    """N=1 unified Strategy: _engine is LiveEngine (not a shim)."""
    from vike_trader_app.exec.live_portfolio_engine import LiveEngine
    strat = _UnifiedBuyStrategy()
    pump, _, _ = _make_pump(strat, symbols=[_BTC])
    assert isinstance(strat._engine, LiveEngine)


# ---------------------------------------------------------------------------
# (b) Per-hub StrategyEventAdapter constructed for N>1
# ---------------------------------------------------------------------------

def test_adapters_count_equals_n_hubs():
    """len(pump._adapters) == N (one adapter per hub)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)

        class _PS(PortfolioStrategy):
            def on_bar(self, ts, bars):
                pass

    strat = _PS()
    acct = _Acct()
    symbols = [_BTC, _ETH]
    hubs = {sym: _Hub(venue="binance", symbol=sym) for sym in symbols}
    pump = LivePump(strat, hubs, acct, now_ms=lambda: 999)

    assert len(pump._adapters) == 2


def test_adapters_count_n1():
    """N=1: still one adapter per hub (1 adapter for the single hub)."""
    strat = _UnifiedBuyStrategy()
    pump, _, _ = _make_pump(strat, symbols=[_BTC])
    assert len(pump._adapters) == 1


def test_stop_unsubscribes_all_adapters():
    """stop() calls unsubscribe() on all adapters (subscriber list drains to 0)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)

        class _PS(PortfolioStrategy):
            def on_bar(self, ts, bars):
                pass

    strat = _PS()
    acct = _Acct()
    symbols = [_BTC, _ETH]
    hubs = {sym: _Hub(venue="binance", symbol=sym) for sym in symbols}
    pump = LivePump(strat, hubs, acct, now_ms=lambda: 999)

    # Both hubs' buses have 1 subscriber (the adapter)
    assert len(hubs[_BTC].bus._subs) == 1
    assert len(hubs[_ETH].bus._subs) == 1

    pump.start()
    pump.stop()

    # After stop(), adapters unsubscribed
    assert len(hubs[_BTC].bus._subs) == 0
    assert len(hubs[_ETH].bus._subs) == 0


# ---------------------------------------------------------------------------
# (c) _dispatch_step — unified Strategy (per-symbol 1-arg) AND PortfolioStrategy (bundle)
# ---------------------------------------------------------------------------

class _UnifiedMultiStrategy(Strategy):
    """Records each on_bar(bar) call."""

    WARMUP = 0

    def __init__(self):
        super().__init__()
        self.bar_calls: list[Bar] = []

    def on_bar(self, bar: Bar) -> None:
        self.bar_calls.append(bar)


def test_dispatch_unified_strategy_n1_calls_on_bar_per_symbol():
    """Unified Strategy: _dispatch_step fans once per symbol (N=1 → 1 call)."""
    strat = _UnifiedMultiStrategy()
    pump, _, _ = _make_pump(strat, symbols=[_BTC])
    pump.start()
    b = _bar(ts=1000, symbol=_BTC)
    pump.feed_bar(_BTC, b)
    assert len(strat.bar_calls) == 1
    assert strat.bar_calls[0] is b


def test_dispatch_unified_strategy_n2_calls_on_bar_per_symbol():
    """Unified Strategy N=2: _dispatch_step fans once per symbol (2 calls per step)."""
    strat = _UnifiedMultiStrategy()
    acct = _Acct()
    hubs = {sym: _Hub(venue="binance", symbol=sym) for sym in [_BTC, _ETH]}
    pump = LivePump(strat, hubs, acct, now_ms=lambda: 999)
    pump.start()

    b_btc = _bar(ts=1000, symbol=_BTC, close=50_000.0)
    b_eth = _bar(ts=1000, symbol=_ETH, close=3_000.0)
    pump.feed_bar(_BTC, b_btc)
    pump.feed_bar(_ETH, b_eth)

    # 2 per-symbol on_bar calls for the single aligned step
    assert len(strat.bar_calls) == 2
    symbols_seen = {b.symbol for b in strat.bar_calls}
    assert symbols_seen == {_BTC, _ETH}


def test_dispatch_portfolio_strategy_calls_bundle_on_bar():
    """PortfolioStrategy: _dispatch_step calls on_bar(ts, bars) (bundle form)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)

        class _PS(PortfolioStrategy):
            def __init__(self):
                super().__init__()
                self.bundle_calls: list[tuple] = []

            def on_bar(self, ts: int, bars: dict) -> None:
                self.bundle_calls.append((ts, dict(bars)))

    strat = _PS()
    acct = _Acct()
    hubs = {sym: _Hub(venue="binance", symbol=sym) for sym in [_BTC, _ETH]}
    pump = LivePump(strat, hubs, acct, now_ms=lambda: 999)
    pump.start()

    pump.feed_bar(_BTC, _bar(ts=1000, symbol=_BTC))
    pump.feed_bar(_ETH, _bar(ts=1000, symbol=_ETH))

    assert len(strat.bundle_calls) == 1
    ts_got, bars_got = strat.bundle_calls[0]
    assert ts_got == 1000
    assert set(bars_got.keys()) == {_BTC, _ETH}


# ---------------------------------------------------------------------------
# (d) N=1 SingleSymbolStrategy — runs via LiveSymbolShim
# ---------------------------------------------------------------------------

class _SingleBuyStrategy(SingleSymbolStrategy):
    """Old-style single-symbol: calls self.buy(1.0) (unkeyed)."""

    WARMUP = 0

    def __init__(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            super().__init__()
        self.bar_calls: list[Bar] = []

    def on_bar(self, bar: Bar) -> None:
        self.bar_calls.append(bar)
        self.buy(1.0)  # old 1-arg API (unkeyed)

    def on_start(self) -> None:
        pass

    def on_stop(self) -> None:
        pass


def test_n1_single_symbol_strategy_shim_routes_order():
    """N=1 SingleSymbolStrategy: buy(1.0) routes through LiveSymbolShim → hub."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        strat = _SingleBuyStrategy()

    pump, hubs, _ = _make_pump(strat, symbols=[_BTC])
    pump.start()

    b = _bar(ts=1000, symbol=_BTC)
    pump.feed_bar(_BTC, b)

    # on_bar called with the 1-arg bar
    assert len(strat.bar_calls) == 1
    assert strat.bar_calls[0] is b
    # Order routed through the shim → hub
    assert len(hubs[_BTC].submitted) == 1
    req = hubs[_BTC].submitted[0]
    assert req.side == +1
    assert req.qty == 1.0


def test_n1_single_symbol_strategy_engine_is_shim():
    """N=1 SingleSymbolStrategy: strategy._engine is a LiveSymbolShim."""
    from vike_trader_app.exec.live_symbol_shim import LiveSymbolShim
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        strat = _SingleBuyStrategy()

    pump, _, _ = _make_pump(strat, symbols=[_BTC])
    assert isinstance(strat._engine, LiveSymbolShim)


def test_n1_single_symbol_strategy_shim_symbol_bound():
    """The LiveSymbolShim is bound to the correct symbol."""
    from vike_trader_app.exec.live_symbol_shim import LiveSymbolShim
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        strat = _SingleBuyStrategy()

    pump, _, _ = _make_pump(strat, symbols=[_BTC])
    assert isinstance(strat._engine, LiveSymbolShim)
    assert strat._engine._symbol == _BTC


def test_n2_single_symbol_strategy_does_not_use_shim():
    """N>1 SingleSymbolStrategy: strategy._engine is the LiveEngine (no shim for multi-hub)."""
    from vike_trader_app.exec.live_portfolio_engine import LiveEngine
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        strat = _SingleBuyStrategy()

    acct = _Acct()
    hubs = {sym: _Hub(venue="binance", symbol=sym) for sym in [_BTC, _ETH]}
    pump = LivePump(strat, hubs, acct, now_ms=lambda: 999)
    # N>1 → engine is the raw LiveEngine, not the shim
    assert isinstance(strat._engine, LiveEngine)


# ---------------------------------------------------------------------------
# (e) Ported pump-behavior tests — adapter delivery, set_mark, warmup, before-start
# ---------------------------------------------------------------------------


class _EventRecordingStrategy(Strategy):
    """Records on_order_accepted and on_bar calls."""

    WARMUP = 0

    def __init__(self):
        super().__init__()
        self.accepted_events: list = []
        self.bar_calls: list[Bar] = []

    def on_order_accepted(self, e) -> None:
        self.accepted_events.append(e)

    def on_bar(self, bar: Bar) -> None:
        self.bar_calls.append(bar)


def test_adapter_delivers_events_to_strategy():
    """Per-hub StrategyEventAdapter delivers venue events to the strategy's handler.

    After start(), publishing an OrderAccepted on the hub's bus must call
    strategy.on_order_accepted exactly once with the same event object.
    This proves the adapter wired by LivePump actually routes events end-to-end.
    """
    from vike_trader_app.exec.events import OrderAccepted

    strat = _EventRecordingStrategy()
    pump, hubs, _ = _make_pump(strat, symbols=[_BTC])
    pump.start()

    evt = OrderAccepted(client_order_id="coid-1", venue_order_id="void-1", ts=1000)
    hubs[_BTC].bus.publish(evt)

    assert len(strat.accepted_events) == 1
    assert strat.accepted_events[0] is evt


def test_feed_bar_sets_mark_on_account():
    """feed_bar → engine.add_live_bar → account.set_mark sets the mark for (venue, symbol).

    After start() and one feed_bar call, account.marks[(venue, symbol)] must equal
    the bar's close price.
    """
    strat = _EventRecordingStrategy()
    pump, hubs, acct = _make_pump(strat, symbols=[_BTC])
    pump.start()

    close_px = 42_000.0
    b = _bar(ts=1000, symbol=_BTC, close=close_px)
    pump.feed_bar(_BTC, b)

    venue = hubs[_BTC].venue  # "binance"
    assert acct.marks[(venue, _BTC)] == close_px


class _WarmupRecordingStrategy(Strategy):
    """WARMUP=3; records index value and on_bar call count."""

    WARMUP = 3

    def __init__(self):
        super().__init__()
        self.bar_calls: list[Bar] = []
        self.index_on_bar: list[int] = []

    def on_bar(self, bar: Bar) -> None:
        self.bar_calls.append(bar)
        self.index_on_bar.append(self.index)


def test_index_advances_through_warmup():
    """strategy.index advances on every aligned step; on_bar fires only after WARMUP.

    With WARMUP=3, feeding 5 bars means:
    - strategy.index should equal 4 (0-based, incremented on every fire, including warmup steps).
    - on_bar should only have been called for steps with index >= WARMUP (i.e., steps 3 and 4).
    """
    strat = _WarmupRecordingStrategy()
    pump, _, _ = _make_pump(strat, symbols=[_BTC])
    pump.start()

    for i in range(5):
        pump.feed_bar(_BTC, _bar(ts=1000 + i, symbol=_BTC))

    # _i starts at -1 and advances by 1 per fired step → after 5 steps: _i == 4
    assert strat.index == 4

    # on_bar fires for steps where _i >= WARMUP (3): steps 3 and 4 → 2 calls
    assert len(strat.bar_calls) == 2
    # The index values seen inside on_bar should all be >= WARMUP
    assert all(idx >= strat.WARMUP for idx in strat.index_on_bar)


class _NullStrategy(Strategy):
    """Minimal strategy that records on_bar calls."""

    WARMUP = 0

    def __init__(self):
        super().__init__()
        self.bar_calls: list[Bar] = []

    def on_bar(self, bar: Bar) -> None:
        self.bar_calls.append(bar)


def test_feed_bar_noop_before_start():
    """feed_bar before start() must not fire on_bar and must not route any order.

    The not-started guard in feed_bar (``if not self._started: return``) must prevent
    the pump from dispatching or routing orders when start() has not been called.
    """
    strat = _NullStrategy()
    pump, hubs, _ = _make_pump(strat, symbols=[_BTC])
    # Do NOT call pump.start()

    pump.feed_bar(_BTC, _bar(ts=1000, symbol=_BTC))

    assert len(strat.bar_calls) == 0
    assert len(hubs[_BTC].submitted) == 0

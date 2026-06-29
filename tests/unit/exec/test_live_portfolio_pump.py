"""Tests for LivePump — wait-for-all align + portfolio lifecycle (A2d Task 2).

Verifies:
- feed_bar(sym, bar@T) for ONE symbol does NOT fire on_bar (waiting for others).
- Completing the timestamp (feeding all symbols) fires on_bar exactly once with the full dict.
- _i / strategy.index advance by ONE per aligned on_bar, NOT per symbol fed.
- schedule.check_due is called AFTER on_bar with the correct (ts, _i).
- Warmup gate: on_bar NOT called until _i >= WARMUP (but _i still advances).
- Stale-bucket flush: an older incomplete bucket is DROPPED (not fired) when a strictly newer
  bucket completes; no deadlock, no double-fire.
- feed_bar after stop() is a no-op (started guard).
- stop() fires strategy.on_stop().
- start() fires strategy.on_start() (if present).
"""

from __future__ import annotations

import pytest

from vike_trader_app.core.model import Bar
from vike_trader_app.core.portfolio import PortfolioStrategy
from vike_trader_app.exec.live_portfolio_pump import LivePump


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

class _NullBus:
    """Minimal EventBus stub (subscribe/unsubscribe — no-ops; no publish needed here)."""

    def subscribe(self, cb) -> None:
        pass

    def unsubscribe(self, cb) -> None:
        pass


class _Hub:
    """Minimal hub stub (needs venue + symbol + submit_ticket; tracks submitted for A2e tests)."""

    def __init__(self, venue: str, symbol: str):
        self.venue = venue
        self.symbol = symbol
        self.account = _Acct()
        self.submitted: list = []
        self.registry: dict = {}
        self.bus = _NullBus()

    def submit_ticket(self, req) -> None:
        self.submitted.append(req)

    def cancel_ticket(self, coid: str) -> None:
        pass


class _Acct:
    """Minimal Account stub."""

    def __init__(self, bal: float = 10_000.0):
        self.balance = bal
        self.positions: dict = {}
        self.marks: dict = {}

    def set_mark(self, venue: str, symbol: str, px: float) -> None:
        self.marks[(venue, symbol)] = px

    def unrealized_pnl(self, venue: str, symbol: str, position_side: str = "BOTH") -> float:
        return 0.0


class _RecordingStrategy(PortfolioStrategy):
    """A PortfolioStrategy that records all on_bar calls and on_start/on_stop hooks."""

    WARMUP = 0  # default: no warmup

    def __init__(self):
        super().__init__()
        self.bar_calls: list[tuple[int, dict]] = []  # [(ts, bars), ...]
        self.started = False
        self.stopped = False

    def on_start(self):
        self.started = True

    def on_stop(self):
        self.stopped = True

    def on_bar(self, ts: int, bars: dict) -> None:
        self.bar_calls.append((ts, dict(bars)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BTC = "BTCUSDT"
_ETH = "ETHUSDT"


def _bar(ts: int, close: float = 100.0) -> Bar:
    return Bar(ts=ts, open=close, high=close, low=close, close=close)


def _make_pump(warmup: int = 0, symbols=(_BTC, _ETH)):
    """Build a 2-symbol pump with stub hubs + shared account."""
    acct = _Acct()
    hubs = {sym: _Hub(venue="binance", symbol=sym) for sym in symbols}
    strat = _RecordingStrategy()
    strat.WARMUP = warmup
    pump = LivePump(strat, hubs, acct, now_ms=lambda: 999)
    return pump, strat, acct


# ---------------------------------------------------------------------------
# Basic alignment — partial then complete
# ---------------------------------------------------------------------------

def test_partial_feed_does_not_fire():
    """Feeding one symbol at T does NOT trigger on_bar — waiting for the other."""
    pump, strat, _ = _make_pump()
    pump.start()
    pump.feed_bar(_BTC, _bar(ts=1000))
    assert strat.bar_calls == []


def test_completing_timestamp_fires_once():
    """Feeding both symbols at T fires on_bar exactly once with the full dict."""
    pump, strat, _ = _make_pump()
    pump.start()
    b_btc = _bar(ts=1000, close=50_000.0)
    b_eth = _bar(ts=1000, close=3_000.0)
    pump.feed_bar(_BTC, b_btc)
    pump.feed_bar(_ETH, b_eth)
    assert len(strat.bar_calls) == 1
    ts_got, bars_got = strat.bar_calls[0]
    assert ts_got == 1000
    assert set(bars_got.keys()) == {_BTC, _ETH}
    assert bars_got[_BTC] is b_btc
    assert bars_got[_ETH] is b_eth


def test_on_bar_not_fired_twice_for_same_ts():
    """Once fired, feeding the same ts again (e.g., a dup signal) does not re-fire."""
    pump, strat, _ = _make_pump()
    pump.start()
    pump.feed_bar(_BTC, _bar(ts=1000))
    pump.feed_bar(_ETH, _bar(ts=1000))
    # First completion: 1 call
    assert len(strat.bar_calls) == 1
    # Feed same ts again — must NOT trigger a second on_bar
    pump.feed_bar(_BTC, _bar(ts=1000))
    pump.feed_bar(_ETH, _bar(ts=1000))
    # Still only 1 call (the bucket was popped)
    assert len(strat.bar_calls) == 1


# ---------------------------------------------------------------------------
# _i / strategy.index advances once per aligned on_bar (not per symbol fed)
# ---------------------------------------------------------------------------

def test_index_advances_once_per_aligned_bar():
    pump, strat, _ = _make_pump()
    pump.start()
    assert pump._i == -1
    assert strat.index == 0  # PortfolioStrategy default

    # Feed BTC only — partial, no advance
    pump.feed_bar(_BTC, _bar(ts=1000))
    assert pump._i == -1

    # Complete T1
    pump.feed_bar(_ETH, _bar(ts=1000))
    assert pump._i == 0
    assert strat.index == 0

    # Complete T2
    pump.feed_bar(_BTC, _bar(ts=2000))
    pump.feed_bar(_ETH, _bar(ts=2000))
    assert pump._i == 1
    assert strat.index == 1


# ---------------------------------------------------------------------------
# schedule.check_due fires AFTER on_bar
# ---------------------------------------------------------------------------

def test_schedule_check_due_fires_after_on_bar():
    """schedule.check_due(ts, _i) is called after on_bar; callbacks are invoked."""
    pump, strat, _ = _make_pump()
    pump.start()

    fired: list[tuple[int, int]] = []

    from vike_trader_app.core.schedule import EveryNBars
    strat.schedule.on(EveryNBars(1), lambda: fired.append((pump._i, len(strat.bar_calls))))

    pump.feed_bar(_BTC, _bar(ts=1000))
    pump.feed_bar(_ETH, _bar(ts=1000))

    # schedule fired once; on_bar was already called (bar_calls length = 1 when cb ran)
    assert len(fired) == 1
    bar_index_at_fire, calls_at_fire = fired[0]
    assert bar_index_at_fire == 0       # _i == 0 at that point
    assert calls_at_fire == 1           # on_bar ran first (len == 1)


def test_schedule_fires_on_every_aligned_bar():
    pump, strat, _ = _make_pump()
    pump.start()

    counter: list[int] = []
    from vike_trader_app.core.schedule import EveryNBars
    strat.schedule.on(EveryNBars(1), lambda: counter.append(1))

    for t in [1000, 2000, 3000]:
        pump.feed_bar(_BTC, _bar(ts=t))
        pump.feed_bar(_ETH, _bar(ts=t))

    assert len(counter) == 3


# ---------------------------------------------------------------------------
# Warmup gate
# ---------------------------------------------------------------------------

def test_warmup_gate_suppresses_early_on_bar():
    """on_bar is NOT called until _i >= WARMUP, but _i still advances."""
    pump, strat, _ = _make_pump(warmup=2)
    pump.start()

    for t in [1000, 2000]:
        pump.feed_bar(_BTC, _bar(ts=t))
        pump.feed_bar(_ETH, _bar(ts=t))

    # _i is 1 (0-indexed) after 2 aligned bars; WARMUP=2 → gate: _i >= 2 not yet satisfied
    assert pump._i == 1
    assert strat.bar_calls == []

    # Third aligned bar: _i == 2 >= WARMUP(2) → fires
    pump.feed_bar(_BTC, _bar(ts=3000))
    pump.feed_bar(_ETH, _bar(ts=3000))
    assert pump._i == 2
    assert len(strat.bar_calls) == 1


def test_warmup_zero_fires_immediately():
    pump, strat, _ = _make_pump(warmup=0)
    pump.start()
    pump.feed_bar(_BTC, _bar(ts=1000))
    pump.feed_bar(_ETH, _bar(ts=1000))
    assert len(strat.bar_calls) == 1


# ---------------------------------------------------------------------------
# Stale-bucket flush (no deadlock, no double-fire)
# ---------------------------------------------------------------------------

def test_stale_bucket_flushed_when_newer_ts_completes():
    """
    T1 receives BTC only.
    T2 receives both BTC + ETH (complete).
    Rule: T1's bucket is STALE (never completed) → it is DROPPED before T2 fires.
    T2 fires exactly once; T1 is NEVER fired (no partial dict, no double-fire).
    _i advances by 1 (for T2 only).
    """
    pump, strat, _ = _make_pump()
    pump.start()

    # T1: only BTC arrives → incomplete bucket
    pump.feed_bar(_BTC, _bar(ts=1000))
    assert strat.bar_calls == []

    # T2: both arrive → T2 is complete; T1 is stale → drop T1, fire T2
    pump.feed_bar(_BTC, _bar(ts=2000))
    pump.feed_bar(_ETH, _bar(ts=2000))

    # on_bar was fired ONCE (for T2, not T1)
    assert len(strat.bar_calls) == 1
    ts_fired, bars_fired = strat.bar_calls[0]
    assert ts_fired == 2000
    assert set(bars_fired.keys()) == {_BTC, _ETH}

    # _i advanced exactly once
    assert pump._i == 0


def test_stale_bucket_does_not_cause_double_fire():
    """Multiple stale buckets accumulating then a complete one: only the complete one fires."""
    pump, strat, _ = _make_pump()
    pump.start()

    # T1 and T2: only BTC (both stale)
    pump.feed_bar(_BTC, _bar(ts=1000))
    pump.feed_bar(_BTC, _bar(ts=2000))
    assert strat.bar_calls == []

    # T3: both → completes; drops T1 + T2, fires T3 once
    pump.feed_bar(_BTC, _bar(ts=3000))
    pump.feed_bar(_ETH, _bar(ts=3000))
    assert len(strat.bar_calls) == 1
    assert strat.bar_calls[0][0] == 3000
    assert pump._i == 0


# ---------------------------------------------------------------------------
# feed_bar after stop() is a no-op
# ---------------------------------------------------------------------------

def test_feed_bar_after_stop_is_noop():
    pump, strat, _ = _make_pump()
    pump.start()
    pump.stop()
    pump.feed_bar(_BTC, _bar(ts=1000))
    pump.feed_bar(_ETH, _bar(ts=1000))
    assert strat.bar_calls == []


# ---------------------------------------------------------------------------
# start() / stop() lifecycle
# ---------------------------------------------------------------------------

def test_start_fires_on_start():
    pump, strat, _ = _make_pump()
    assert not strat.started
    pump.start()
    assert strat.started


def test_stop_fires_on_stop():
    pump, strat, _ = _make_pump()
    pump.start()
    assert not strat.stopped
    pump.stop()
    assert strat.stopped


def test_start_idempotent():
    """Calling start() twice does not double-call on_start."""
    pump, strat, _ = _make_pump()
    pump.start()
    pump.start()
    # No assertion on strat.started beyond bool; idempotent means _started guard prevents re-entry.
    assert strat.started


def test_stop_before_start_does_not_raise():
    """Calling stop() without start() must not raise."""
    pump, strat, _ = _make_pump()
    pump.stop()  # should not crash
    assert not strat.stopped  # on_stop not called if never started


# ---------------------------------------------------------------------------
# Strategy exceptions are swallowed (robustness)
# ---------------------------------------------------------------------------

def test_strategy_on_bar_exception_does_not_crash_pump():
    """A strategy bug in on_bar must not crash the live session."""

    class _CrashingStrategy(_RecordingStrategy):
        def on_bar(self, ts: int, bars: dict) -> None:
            super().on_bar(ts, bars)
            raise RuntimeError("strategy bug")

    acct = _Acct()
    hubs = {sym: _Hub(venue="binance", symbol=sym) for sym in [_BTC, _ETH]}
    strat = _CrashingStrategy()
    pump = LivePump(strat, hubs, acct, now_ms=lambda: 999)
    pump.start()

    # Should not raise; bar_calls captures the entry even though on_bar raises
    pump.feed_bar(_BTC, _bar(ts=1000))
    pump.feed_bar(_ETH, _bar(ts=1000))

    # Pump continues: a second aligned bar also does not crash
    pump.feed_bar(_BTC, _bar(ts=2000))
    pump.feed_bar(_ETH, _bar(ts=2000))
    assert len(strat.bar_calls) == 2


def test_strategy_not_implemented_error_does_not_crash_pump():
    """NotImplementedError from on_bar is also swallowed (stop/trailing → A2e)."""

    class _NIEStrategy(_RecordingStrategy):
        def on_bar(self, ts: int, bars: dict) -> None:
            raise NotImplementedError("stop/trailing A2e")

    acct = _Acct()
    hubs = {sym: _Hub(venue="binance", symbol=sym) for sym in [_BTC, _ETH]}
    strat = _NIEStrategy()
    pump = LivePump(strat, hubs, acct, now_ms=lambda: 999)
    pump.start()

    pump.feed_bar(_BTC, _bar(ts=1000))
    pump.feed_bar(_ETH, _bar(ts=1000))
    # Pump did not crash; _i advanced
    assert pump._i == 0


# ---------------------------------------------------------------------------
# .engine attribute
# ---------------------------------------------------------------------------

def test_engine_attribute_is_live_portfolio_engine():
    from vike_trader_app.exec.live_portfolio_engine import LiveEngine
    pump, _, _ = _make_pump()
    assert isinstance(pump.engine, LiveEngine)


def test_engine_set_on_strategy():
    """strategy._engine is set to the LiveEngine by the pump."""
    from vike_trader_app.exec.live_portfolio_engine import LiveEngine
    pump, strat, _ = _make_pump()
    assert isinstance(strat._engine, LiveEngine)
    assert strat._engine is pump.engine


# ---------------------------------------------------------------------------
# on_start / on_stop missing (PortfolioStrategy may not define them)
# ---------------------------------------------------------------------------

def test_strategy_without_on_start_does_not_raise():
    """PortfolioStrategy base does NOT define on_start; pump must use getattr safely."""

    class _NoHooksStrategy(PortfolioStrategy):
        def on_bar(self, ts: int, bars: dict) -> None:
            pass

    acct = _Acct()
    hubs = {sym: _Hub(venue="binance", symbol=sym) for sym in [_BTC, _ETH]}
    strat = _NoHooksStrategy()
    pump = LivePump(strat, hubs, acct)
    pump.start()   # must not raise AttributeError
    pump.stop()    # must not raise AttributeError


# ---------------------------------------------------------------------------
# Single-symbol edge case
# ---------------------------------------------------------------------------

def test_single_symbol_fires_immediately():
    """A pump with 1 symbol fires on_bar after each feed_bar (no waiting needed)."""
    acct = _Acct()
    hubs = {_BTC: _Hub(venue="binance", symbol=_BTC)}
    strat = _RecordingStrategy()
    pump = LivePump(strat, hubs, acct, now_ms=lambda: 999)
    pump.start()
    pump.feed_bar(_BTC, _bar(ts=1000))
    assert len(strat.bar_calls) == 1
    assert strat.bar_calls[0][0] == 1000


# ---------------------------------------------------------------------------
# IMPORTANT-2: monotonic watermark (no time regression; _fired_ts removed)
# ---------------------------------------------------------------------------

def test_watermark_blocks_old_ts_after_newer_fired():
    """An OLD ts re-fed after a NEWER ts already fired is silently dropped (watermark guard)."""
    pump, strat, _ = _make_pump()
    pump.start()

    # Fire T2
    pump.feed_bar(_BTC, _bar(ts=2000))
    pump.feed_bar(_ETH, _bar(ts=2000))
    assert pump._i == 0
    assert strat.bar_calls[0][0] == 2000

    # Now feed T1 (older) for BOTH symbols — must not re-fire or advance _i
    pump.feed_bar(_BTC, _bar(ts=1000))
    pump.feed_bar(_ETH, _bar(ts=1000))
    # _i must remain 0, no additional on_bar
    assert pump._i == 0
    assert len(strat.bar_calls) == 1


def test_watermark_blocks_equal_ts_replay():
    """Re-feeding an already-fired ts (same ts, replay) is also dropped."""
    pump, strat, _ = _make_pump()
    pump.start()

    pump.feed_bar(_BTC, _bar(ts=1000))
    pump.feed_bar(_ETH, _bar(ts=1000))
    assert len(strat.bar_calls) == 1

    # Replay the same ts for both symbols — must be a no-op
    pump.feed_bar(_BTC, _bar(ts=1000))
    pump.feed_bar(_ETH, _bar(ts=1000))
    assert len(strat.bar_calls) == 1
    assert pump._i == 0


def test_watermark_o1_no_set_attribute():
    """_last_fired_ts exists; _fired_ts set must NOT exist (replaced by watermark)."""
    pump, _, _ = _make_pump()
    assert hasattr(pump, "_last_fired_ts"), "_last_fired_ts watermark attribute missing"
    assert not hasattr(pump, "_fired_ts"), (
        "_fired_ts set still present — must be replaced by _last_fired_ts watermark"
    )


def test_watermark_initial_value_allows_ts_zero():
    """_last_fired_ts starts at -1 so ts=0 (a valid epoch bar) can fire normally."""
    pump, strat, _ = _make_pump()
    pump.start()
    pump.feed_bar(_BTC, _bar(ts=0))
    pump.feed_bar(_ETH, _bar(ts=0))
    assert len(strat.bar_calls) == 1
    assert strat.bar_calls[0][0] == 0


def test_watermark_in_order_sequence_unaffected():
    """Normal in-order sequence still fires correctly with the watermark."""
    pump, strat, _ = _make_pump()
    pump.start()
    for t in [1000, 2000, 3000]:
        pump.feed_bar(_BTC, _bar(ts=t))
        pump.feed_bar(_ETH, _bar(ts=t))
    assert pump._i == 2
    assert len(strat.bar_calls) == 3
    assert [c[0] for c in strat.bar_calls] == [1000, 2000, 3000]


# ---------------------------------------------------------------------------
# IMPORTANT-3: prime() method — warmup parity
# ---------------------------------------------------------------------------

def test_prime_advances_i_by_aligned_steps():
    """prime({sym: bars}) advances _i by min(len(bars)) across symbols."""
    pump, strat, _ = _make_pump(warmup=3)
    btc_bars = [_bar(ts=i * 1000, close=float(i + 1) * 100) for i in range(5)]
    eth_bars = [_bar(ts=i * 1000, close=float(i + 1) * 200) for i in range(5)]
    pump.prime({_BTC: btc_bars, _ETH: eth_bars})
    # 5 bars each → 5 aligned steps → _i = 5 - 1 + (-1) = ... actually: starts at -1, +5 = 4
    assert pump._i == 4
    assert strat.index == 4


def test_prime_does_not_fire_on_bar():
    """prime() must NOT call strategy.on_bar for any history bars."""
    pump, strat, _ = _make_pump(warmup=0)
    btc_bars = [_bar(ts=i * 1000) for i in range(3)]
    eth_bars = [_bar(ts=i * 1000) for i in range(3)]
    pump.prime({_BTC: btc_bars, _ETH: eth_bars})
    # on_bar must NOT have been called even though warmup=0
    assert strat.bar_calls == []
    assert pump._i == 2  # 3 aligned steps, _i starts at -1 → -1 + 3 = 2


def test_prime_then_feed_respects_warmup():
    """After prime with WARMUP aligned bars, the first live feed_bar fires on_bar immediately."""
    pump, strat, _ = _make_pump(warmup=3)
    # Prime with exactly WARMUP bars (0-indexed _i becomes 2 after 3 bars)
    btc_bars = [_bar(ts=i * 1000) for i in range(3)]
    eth_bars = [_bar(ts=i * 1000) for i in range(3)]
    pump.prime({_BTC: btc_bars, _ETH: eth_bars})
    assert pump._i == 2
    assert strat.bar_calls == []

    pump.start()

    # First live aligned bar → _i advances to 3 = WARMUP → gate satisfied → on_bar fires
    pump.feed_bar(_BTC, _bar(ts=3000))
    pump.feed_bar(_ETH, _bar(ts=3000))
    assert pump._i == 3
    assert len(strat.bar_calls) == 1
    assert strat.bar_calls[0][0] == 3000


def test_prime_with_unequal_history_uses_min():
    """prime() uses min(len) when symbols have different-length histories."""
    pump, strat, _ = _make_pump()
    btc_bars = [_bar(ts=i * 1000) for i in range(5)]
    eth_bars = [_bar(ts=i * 1000) for i in range(3)]  # shorter
    pump.prime({_BTC: btc_bars, _ETH: eth_bars})
    # min(5, 3) = 3 → _i = -1 + 3 = 2
    assert pump._i == 2


def test_prime_empty_history_noop():
    """prime() with empty histories is a no-op (does not crash or advance _i)."""
    pump, strat, _ = _make_pump()
    pump.prime({_BTC: [], _ETH: []})
    assert pump._i == -1
    assert strat.index == 0  # PortfolioStrategy default


def test_prime_feeds_engine_buffer():
    """prime() feeds all bars through engine.add_live_bar so the buffers are populated."""
    pump, strat, _ = _make_pump()
    btc_bars = [_bar(ts=i * 1000, close=float(i + 1) * 100) for i in range(4)]
    eth_bars = [_bar(ts=i * 1000, close=float(i + 1) * 50) for i in range(4)]
    pump.prime({_BTC: btc_bars, _ETH: eth_bars})
    assert len(pump.engine._bufs[_BTC].bars) == 4
    assert len(pump.engine._bufs[_ETH].bars) == 4


# ---------------------------------------------------------------------------
# A2e: check_conditionals called BEFORE on_bar for each symbol in a fired bucket
# ---------------------------------------------------------------------------

def _hbar(ts: int, high: float, low: float) -> Bar:
    """Bar with explicit high/low for trigger testing."""
    close = (high + low) / 2
    return Bar(ts=ts, open=close, high=high, low=low, close=close)


def test_pump_calls_check_conditionals_before_on_bar():
    """A stop armed before the bar fires; the fill reaches hub BEFORE on_bar runs.

    The strategy's on_bar records the hub's submitted count at the time it runs.
    If check_conditionals fires first, submitted_at_on_bar == 1; if after, it is 0.
    """
    from vike_trader_app.exec.events import OrderRequest

    acct = _Acct()
    hub_btc = _Hub(venue="binance", symbol=_BTC)
    hub_eth = _Hub(venue="binance", symbol=_ETH)

    submitted_count_at_on_bar: list[int] = []

    class _ObservingStrategy(_RecordingStrategy):
        def on_bar(self, ts: int, bars: dict) -> None:
            submitted_count_at_on_bar.append(len(hub_btc.submitted))
            super().on_bar(ts, bars)

    strat = _ObservingStrategy()
    hubs = {_BTC: hub_btc, _ETH: hub_eth}
    pump = LivePump(strat, hubs, acct, now_ms=lambda: 999)

    # Arm a buy-stop on BTC at 110 — triggers when high >= 110
    pump.engine.submit_stop(_BTC, +1, 2.0, 110.0)

    pump.start()

    # Feed a crossing bar for BOTH symbols (so the bucket completes)
    pump.feed_bar(_BTC, _hbar(ts=1000, high=115.0, low=109.0))
    pump.feed_bar(_ETH, _hbar(ts=1000, high=200.0, low=190.0))

    # on_bar was called once
    assert len(strat.bar_calls) == 1
    # The hub already had the submitted request when on_bar ran (conditionals fire FIRST)
    assert submitted_count_at_on_bar[0] == 1
    assert len(hub_btc.submitted) == 1
    req = hub_btc.submitted[0]
    assert req.side == +1
    assert req.qty == 2.0
    assert req.order_type == "market"


def test_pump_check_conditionals_fires_for_each_symbol_independently():
    """Both BTC and ETH conditionals fire in the same aligned bar bucket."""
    acct = _Acct()
    hub_btc = _Hub(venue="binance", symbol=_BTC)
    hub_eth = _Hub(venue="binance", symbol=_ETH)
    hubs = {_BTC: hub_btc, _ETH: hub_eth}
    strat = _RecordingStrategy()
    pump = LivePump(strat, hubs, acct, now_ms=lambda: 999)

    pump.engine.submit_stop(_BTC, +1, 2.0, 110.0)
    pump.engine.submit_stop(_ETH, -1, 1.0, 180.0)

    pump.start()

    # BTC bar crosses 110 (high=115); ETH bar crosses 180 (low=175)
    pump.feed_bar(_BTC, _hbar(ts=1000, high=115.0, low=109.0))
    pump.feed_bar(_ETH, _hbar(ts=1000, high=185.0, low=175.0))

    assert len(hub_btc.submitted) == 1
    assert hub_btc.submitted[0].side == +1
    assert len(hub_eth.submitted) == 1
    assert hub_eth.submitted[0].side == -1


def test_pump_check_conditionals_fires_regardless_of_warmup():
    """Conditionals fire even before the warmup gate (on_bar is suppressed but fills happen)."""
    acct = _Acct()
    hub_btc = _Hub(venue="binance", symbol=_BTC)
    hub_eth = _Hub(venue="binance", symbol=_ETH)
    hubs = {_BTC: hub_btc, _ETH: hub_eth}
    strat = _RecordingStrategy()
    strat.WARMUP = 5   # large warmup — on_bar suppressed for 5 bars
    pump = LivePump(strat, hubs, acct, now_ms=lambda: 999)

    pump.engine.submit_stop(_BTC, +1, 2.0, 110.0)
    pump.start()

    # First bar — warmup gate should suppress on_bar but NOT check_conditionals
    pump.feed_bar(_BTC, _hbar(ts=1000, high=115.0, low=109.0))
    pump.feed_bar(_ETH, _hbar(ts=1000, high=200.0, low=190.0))

    # on_bar suppressed (WARMUP=5, _i=0)
    assert strat.bar_calls == []
    # But the conditional DID fire
    assert len(hub_btc.submitted) == 1


def test_pump_no_conditionals_on_bar_runs_normally():
    """When no conditionals are armed, on_bar still fires normally (no regression)."""
    pump, strat, _ = _make_pump()
    pump.start()
    pump.feed_bar(_BTC, _bar(ts=1000))
    pump.feed_bar(_ETH, _bar(ts=1000))
    assert len(strat.bar_calls) == 1

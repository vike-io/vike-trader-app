"""Tests for StrategyLiveEngine — market/target order verbs -> LiveOmsHub + Account reads.

Handler firing (on_order_submitted, on_order_rejected, …) is A2b's responsibility, driven by
the real EventBus events.  StrategyLiveEngine does NOT fire strategy callbacks — tests here
verify only that orders reach the hub and that account reads are correct.
"""
from vike_trader_app.exec.strategy_live_engine import StrategyLiveEngine
from vike_trader_app.exec.events import OrderRequest


class _Hub:
    def __init__(self): self.submitted = []; self.registry = {}; self.canceled = []
    def submit_ticket(self, req): self.submitted.append(req)
    def cancel_ticket(self, coid): self.canceled.append(coid)


class _Acct:
    def __init__(self, size=0.0, avg=0.0, bal=10_000.0):
        self.positions = {("binance", "BTCUSDT", "BOTH"): {"size": size, "avg_px": avg}}
        self.balance = bal
        self._u = 0.0
        self.marks = {}
    def unrealized_pnl(self, venue, symbol, position_side="BOTH"): return self._u


def _eng(acct=None, hub=None):
    return StrategyLiveEngine(hub or _Hub(), acct or _Acct(),
                              venue="binance", symbol="BTCUSDT", now_ms=lambda: 123)


def test_submit_builds_orderrequest_and_routes_to_hub():
    hub = _Hub(); e = _eng(hub=hub)
    e.submit(+1, 2.0)
    assert len(hub.submitted) == 1
    req = hub.submitted[0]
    assert isinstance(req, OrderRequest)
    assert (req.venue, req.symbol, req.side, req.qty, req.order_type, req.ts) == \
           ("binance", "BTCUSDT", 1, 2.0, "market", 123)
    assert req.client_order_id  # unique id present


def test_position_and_equity_from_account():
    acct = _Acct(size=3.0, avg=100.0, bal=5_000.0); acct._u = 250.0
    e = _eng(acct=acct)
    assert e.position.size == 3.0 and e.position.avg_price == 100.0
    assert e.equity_now() == 5_250.0     # balance + unrealized


def test_submit_close_flattens():
    acct = _Acct(size=4.0); hub = _Hub(); e = _eng(acct=acct, hub=hub)
    e.submit_close()
    assert hub.submitted[0].side == -1 and hub.submitted[0].qty == 4.0   # sell 4 to flatten long


def test_order_target_percent_uses_live_equity():
    acct = _Acct(size=0.0, bal=10_000.0); hub = _Hub()
    e = _eng(acct=acct, hub=hub)
    # inject mark via account.marks (the real source — mirrors BacktestEngine._price)
    acct.marks[("binance", "BTCUSDT")] = 100.0
    e.order_target_percent(0.5)   # target 50% -> 50 notional / 100 = 50 units
    assert hub.submitted and hub.submitted[0].side == +1 and abs(hub.submitted[0].qty - 50.0) < 1e-9


def test_cancel_all_cancels_open_registry_orders():
    hub = _Hub(); hub.registry = {"c1": object(), "c2": object()}
    e = _eng(hub=hub); e.cancel_all()
    assert set(hub.canceled) == {"c1", "c2"}


def test_client_order_id_is_unique_per_submit():
    hub = _Hub(); e = _eng(hub=hub)
    e.submit(+1, 1.0)
    e.submit(+1, 1.0)
    ids = [r.client_order_id for r in hub.submitted]
    assert ids[0] != ids[1]


def test_order_target_sells_delta_to_reach_target():
    # currently long 5, target 2 -> sell 3
    acct = _Acct(size=5.0, avg=50.0, bal=10_000.0); hub = _Hub()
    e = _eng(acct=acct, hub=hub)
    acct.marks[("binance", "BTCUSDT")] = 50.0
    e.order_target(2.0)
    assert hub.submitted[0].side == -1 and abs(hub.submitted[0].qty - 3.0) < 1e-9


def test_order_target_value_converts_notional_to_units():
    # notional = 500 / (price=50 * mult=1) = 10 units target; currently flat -> buy 10
    acct = _Acct(size=0.0, bal=10_000.0); hub = _Hub()
    e = _eng(acct=acct, hub=hub)
    acct.marks[("binance", "BTCUSDT")] = 50.0
    e.order_target_value(500.0)
    assert hub.submitted[0].side == +1 and abs(hub.submitted[0].qty - 10.0) < 1e-9


def test_submit_close_short():
    # flat a short: currently -3, should submit buy 3
    acct = _Acct(size=-3.0); hub = _Hub(); e = _eng(acct=acct, hub=hub)
    e.submit_close()
    assert hub.submitted[0].side == +1 and hub.submitted[0].qty == 3.0


def test_submit_close_flat_noop():
    hub = _Hub(); e = _eng(hub=hub)  # default size=0
    e.submit_close()
    assert len(hub.submitted) == 0


def test_drawdown_now_zero_when_no_peak_drawdown():
    acct = _Acct(bal=10_000.0)
    e = _eng(acct=acct)
    # with no unrealized PnL, equity == balance, no drawdown vs peak
    dd = e.drawdown_now()
    assert dd == 0.0


def test_now_returns_injected_clock():
    e = _eng()
    assert e.now == 123


# ---------------------------------------------------------------------------
# Task 2: resting-order verbs
# ---------------------------------------------------------------------------

def test_submit_limit_builds_limit_request():
    hub = _Hub(); e = _eng(hub=hub)
    e.submit_limit(+1, 1.0, price=95.0)
    req = hub.submitted[0]
    assert req.order_type == "limit" and req.price == 95.0 and req.side == +1


def test_submit_limit_weight_accepted():
    hub = _Hub(); e = _eng(hub=hub)
    e.submit_limit(-1, 2.0, price=105.0, weight=0.5)
    req = hub.submitted[0]
    assert req.order_type == "limit" and req.price == 105.0 and req.side == -1


def test_submit_stop_raises_not_implemented():
    """submit_stop must raise NotImplementedError — no venue client honors native stops yet.

    Stops are deferred to A2e (client-side emulated conditionals).  Submitting as-is would
    silently route a stop as a plain MARKET (every build_order_params branch only checks
    is_limit), which fires immediately with the trigger price dropped — a real-money mis-order.
    """
    import pytest
    e = _eng()
    with pytest.raises(NotImplementedError, match="A2e"):
        e.submit_stop(-1, 1.0, price=90.0)


def test_submit_market_close_builds_market_request():
    hub = _Hub(); e = _eng(hub=hub)
    e.submit_market_close(-1, 1.5)
    req = hub.submitted[0]
    assert req.order_type == "market" and req.side == -1 and req.qty == 1.5


def test_submit_limit_close_builds_limit_request():
    hub = _Hub(); e = _eng(hub=hub)
    e.submit_limit_close(-1, 2.0, price=98.0)
    req = hub.submitted[0]
    assert req.order_type == "limit" and req.price == 98.0 and req.side == -1


def test_submit_trailing_raises_not_implemented():
    import pytest
    e = _eng()
    with pytest.raises(NotImplementedError):
        e.submit_trailing(+1, 1.0, trail=5.0)


# ---------------------------------------------------------------------------
# Task 2: MTF buffer
# ---------------------------------------------------------------------------

def test_mtf_buffer_bars_for():
    from vike_trader_app.core.model import Bar
    e = StrategyLiveEngine(_Hub(), _Acct(), venue="binance", symbol="BTCUSDT",
                           now_ms=lambda: 0, timeframes=["1h"])
    for t in range(120):  # 1-min bars (60_000 ms each); feed 2h worth
        e.add_live_bar(Bar(ts=t * 60_000, open=1, high=1, low=1, close=1))
    assert isinstance(e.bars_for("1h"), list)   # completed 1h bars visible (no look-ahead)


def test_mtf_buffer_bars_for_returns_completed_bars_only():
    from vike_trader_app.core.model import Bar
    e = StrategyLiveEngine(_Hub(), _Acct(), venue="binance", symbol="BTCUSDT",
                           now_ms=lambda: 0, timeframes=["1h"])
    # Feed 61 1-min bars: bars 0..59 cover the first hour (ts=0..3_540_000),
    # bar 60 (ts=3_600_000) falls in the second hour — so the first hour is now completed
    # and bars_for("1h") must return at least 1 completed bar.
    for t in range(61):
        e.add_live_bar(Bar(ts=t * 60_000, open=1, high=1, low=1, close=1))
    completed = e.bars_for("1h")
    assert len(completed) >= 1


def test_mtf_buffer_forming_for():
    from vike_trader_app.core.model import Bar
    e = StrategyLiveEngine(_Hub(), _Acct(), venue="binance", symbol="BTCUSDT",
                           now_ms=lambda: 0, timeframes=["1h"])
    # Feed 30 1-min bars — halfway through the first hour
    for t in range(30):
        e.add_live_bar(Bar(ts=t * 60_000, open=1, high=1, low=1, close=1))
    forming = e.forming_for("1h")
    # Should return a forming bar (not None) since we have data in the current window
    assert forming is not None


def test_mtf_buffer_empty_without_timeframes():
    """Engine without timeframes= still has bars list."""
    from vike_trader_app.core.model import Bar
    e = StrategyLiveEngine(_Hub(), _Acct(), venue="binance", symbol="BTCUSDT",
                           now_ms=lambda: 0)
    e.add_live_bar(Bar(ts=0, open=1, high=1, low=1, close=1))
    assert len(e.bars) == 1


# ---------------------------------------------------------------------------
# Task 2: StrategyEngine protocol conformance
# ---------------------------------------------------------------------------

def test_conforms_to_strategy_engine_protocol():
    from vike_trader_app.core.strategy_engine import StrategyEngine
    assert isinstance(_eng(), StrategyEngine)

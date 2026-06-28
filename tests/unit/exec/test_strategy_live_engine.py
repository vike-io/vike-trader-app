"""Tests for StrategyLiveEngine — market/target order verbs -> LiveOmsHub + Account reads."""
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


class _Strat:
    def __init__(self): self.submitted_events = []
    def on_order_submitted(self, order): self.submitted_events.append(order)


def _eng(acct=None, hub=None, strat=None):
    return StrategyLiveEngine(strat or _Strat(), hub or _Hub(), acct or _Acct(),
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


def test_submit_fires_on_order_submitted_sync():
    strat = _Strat(); e = _eng(strat=strat)
    e.submit(-1, 1.0)
    assert len(strat.submitted_events) == 1


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

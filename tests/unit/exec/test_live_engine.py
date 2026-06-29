"""Tests for the unified LiveEngine (P0 Task 1 — generalize LiveEngine).

At N=1 (one hub/account) validates:
- LiveEngine is LiveEngine  (alias kept)
- order_target_percent(sym, pct) → correct units submitted
- order_target_value(sym, value) → correct units submitted
- order_target(sym, target) → delta market order
- submit_close(sym) reduce_only via per-hub flag
- drawdown_now() == 0.0
- add_live_bar(sym, bar) calls account.set_mark
- multipliers kwarg feeds _mult_of(sym)
- timeframes kwarg accepted without error (ctor guard only — buffer seam tested in LPE suite)
"""

import pytest

from vike_trader_app.exec.live_portfolio_engine import LiveEngine, LiveEngine
from vike_trader_app.exec.events import OrderRequest
from vike_trader_app.core.model import Bar


# ---------------------------------------------------------------------------
# Stubs (mirror test_live_portfolio_engine.py patterns)
# ---------------------------------------------------------------------------

class _Hub:
    def __init__(self, venue: str, symbol: str):
        self.venue = venue
        self.symbol = symbol
        self.submitted: list[OrderRequest] = []
        self.canceled: list[str] = []
        self.registry: dict = {}

    def submit_ticket(self, req: OrderRequest) -> None:
        self.submitted.append(req)

    def cancel_ticket(self, coid: str) -> None:
        self.canceled.append(coid)


class _Acct:
    def __init__(self, bal: float = 10_000.0):
        self.balance = bal
        self.positions: dict = {}
        self.marks: dict = {}
        self._set_mark_calls: list = []
        self._unrealized_by_sym: dict[str, float] = {}

    def set_mark(self, venue: str, symbol: str, px: float) -> None:
        self.marks[(venue, symbol)] = px
        self._set_mark_calls.append((venue, symbol, px))

    def unrealized_pnl(self, venue: str, symbol: str, position_side: str = "BOTH") -> float:
        return self._unrealized_by_sym.get(symbol, 0.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SYM = "BTCUSDT"
_VENUE = "binance"


def _make_engine(
    bal: float = 10_000.0,
    multipliers: dict | None = None,
    timeframes: list | None = None,
):
    acct = _Acct(bal=bal)
    hub = _Hub(venue=_VENUE, symbol=_SYM)
    eng = LiveEngine(
        {_SYM: hub}, acct,
        multipliers=multipliers,
        timeframes=timeframes,
        now_ms=lambda: 999,
    )
    return eng, hub, acct


def _bar(close: float = 100.0, ts: int = 0) -> Bar:
    return Bar(ts=ts, open=close, high=close, low=close, close=close)


# ---------------------------------------------------------------------------
# Alias check
# ---------------------------------------------------------------------------

def test_live_portfolio_engine_is_live_engine():
    """The alias must be the same class object (not a subclass)."""
    assert LiveEngine is LiveEngine


# ---------------------------------------------------------------------------
# drawdown_now stub
# ---------------------------------------------------------------------------

def test_drawdown_now_returns_zero():
    eng, _, _ = _make_engine()
    assert eng.drawdown_now() == 0.0


# ---------------------------------------------------------------------------
# add_live_bar calls account.set_mark
# ---------------------------------------------------------------------------

def test_add_live_bar_calls_set_mark():
    eng, hub, acct = _make_engine()
    eng.add_live_bar(_SYM, _bar(close=50_000.0, ts=1_000))
    assert (_VENUE, _SYM, 50_000.0) in acct._set_mark_calls


# ---------------------------------------------------------------------------
# submit_close reduce_only via hub flag (port from StrategyLiveEngine)
# ---------------------------------------------------------------------------

def test_submit_close_spot_hub_not_reduce_only():
    """Spot hub (no reduce_only_on_close attr) → reduce_only=False."""
    eng, hub, acct = _make_engine()
    acct.positions[(_VENUE, _SYM, "BOTH")] = {"size": 1.0, "avg_px": 100.0}
    eng.submit_close(_SYM)
    assert len(hub.submitted) == 1
    assert hub.submitted[0].reduce_only is False


def test_submit_close_perp_hub_sets_reduce_only():
    """Perp hub (reduce_only_on_close=True) → reduce_only=True on close."""
    eng, hub, acct = _make_engine()
    hub.reduce_only_on_close = True
    acct.positions[(_VENUE, _SYM, "BOTH")] = {"size": 2.0, "avg_px": 100.0}
    eng.submit_close(_SYM)
    assert hub.submitted[0].reduce_only is True
    assert hub.submitted[0].side == -1   # sell to flatten long


# ---------------------------------------------------------------------------
# multipliers kwarg → _mult_of
# ---------------------------------------------------------------------------

def test_mult_of_defaults_to_one():
    eng, _, _ = _make_engine()
    assert eng._mult_of(_SYM) == 1.0


def test_mult_of_uses_supplied_multiplier():
    eng, _, _ = _make_engine(multipliers={_SYM: 10.0})
    assert eng._mult_of(_SYM) == 10.0


def test_mult_of_unknown_sym_defaults_to_one():
    eng, _, _ = _make_engine(multipliers={_SYM: 10.0})
    assert eng._mult_of("XYZUSDT") == 1.0


# ---------------------------------------------------------------------------
# timeframes kwarg accepted (ctor guard — full buffer test in LPE suite)
# ---------------------------------------------------------------------------

def test_timeframes_kwarg_does_not_raise():
    eng, _, _ = _make_engine(timeframes=["1h", "4h"])
    # The engine should have constructed buffers for the sym
    assert _SYM in eng._bufs


# ---------------------------------------------------------------------------
# order_target (sym, target_size) — delta market order
# ---------------------------------------------------------------------------

def test_order_target_buys_delta_when_flat():
    """Flat → target=3 → buy 3."""
    eng, hub, acct = _make_engine()
    eng.order_target(_SYM, 3.0)
    assert len(hub.submitted) == 1
    req = hub.submitted[0]
    assert req.side == +1
    assert req.qty == 3.0
    assert req.order_type == "market"


def test_order_target_sells_delta_when_long():
    """Long 5 → target=2 → sell 3."""
    eng, hub, acct = _make_engine()
    acct.positions[(_VENUE, _SYM, "BOTH")] = {"size": 5.0, "avg_px": 100.0}
    eng.order_target(_SYM, 2.0)
    assert len(hub.submitted) == 1
    req = hub.submitted[0]
    assert req.side == -1
    assert req.qty == 3.0


def test_order_target_noop_when_at_target():
    """Already at target → no order submitted."""
    eng, hub, acct = _make_engine()
    acct.positions[(_VENUE, _SYM, "BOTH")] = {"size": 2.0, "avg_px": 100.0}
    eng.order_target(_SYM, 2.0)
    assert len(hub.submitted) == 0


# ---------------------------------------------------------------------------
# order_target_value(sym, value)
# ---------------------------------------------------------------------------

def test_order_target_value_sizes_correctly():
    """value=5000, price=50000, mult=1 → target=5000/(50000*1)=0.1 units."""
    eng, hub, acct = _make_engine()
    acct.marks[(_VENUE, _SYM)] = 50_000.0
    eng.order_target_value(_SYM, 5_000.0)
    assert len(hub.submitted) == 1
    req = hub.submitted[0]
    assert abs(req.qty - 0.1) < 1e-9
    assert req.side == +1


def test_order_target_value_uses_multiplier():
    """value=5000, price=50000, mult=10 → target=5000/(50000*10)=0.01 units."""
    eng, hub, acct = _make_engine(multipliers={_SYM: 10.0})
    acct.marks[(_VENUE, _SYM)] = 50_000.0
    eng.order_target_value(_SYM, 5_000.0)
    assert len(hub.submitted) == 1
    assert abs(hub.submitted[0].qty - 0.01) < 1e-9


def test_order_target_value_noop_when_no_mark():
    """No mark set → 0.0 price guard → no order."""
    eng, hub, acct = _make_engine()
    eng.order_target_value(_SYM, 5_000.0)
    assert len(hub.submitted) == 0


# ---------------------------------------------------------------------------
# order_target_percent(sym, pct)
# ---------------------------------------------------------------------------

def test_order_target_percent_sizes_correctly():
    """equity=10000, pct=0.5, price=100, mult=1 → target=50 units."""
    eng, hub, acct = _make_engine(bal=10_000.0)
    acct.marks[(_VENUE, _SYM)] = 100.0
    eng.order_target_percent(_SYM, 0.5)
    assert len(hub.submitted) == 1
    req = hub.submitted[0]
    # target = 0.5 * 10000 / (100 * 1) = 50.0
    assert abs(req.qty - 50.0) < 1e-9
    assert req.side == +1


def test_order_target_percent_uses_multiplier():
    """equity=10000, pct=0.5, price=100, mult=5 → target=10 units."""
    eng, hub, acct = _make_engine(bal=10_000.0, multipliers={_SYM: 5.0})
    acct.marks[(_VENUE, _SYM)] = 100.0
    eng.order_target_percent(_SYM, 0.5)
    assert len(hub.submitted) == 1
    # target = 0.5 * 10000 / (100 * 5) = 10.0
    assert abs(hub.submitted[0].qty - 10.0) < 1e-9


def test_order_target_percent_noop_when_no_mark():
    """No mark → price=0.0 guard → no order."""
    eng, hub, acct = _make_engine()
    eng.order_target_percent(_SYM, 0.5)
    assert len(hub.submitted) == 0


def test_order_target_percent_accounts_for_existing_position():
    """Long 20 → pct=0.5 with equity 10k/price 100 → target 50, delta=30 → buy 30."""
    eng, hub, acct = _make_engine(bal=10_000.0)
    acct.marks[(_VENUE, _SYM)] = 100.0
    acct.positions[(_VENUE, _SYM, "BOTH")] = {"size": 20.0, "avg_px": 100.0}
    eng.order_target_percent(_SYM, 0.5)
    assert len(hub.submitted) == 1
    # target=50, delta=50-20=30 → buy 30
    assert abs(hub.submitted[0].qty - 30.0) < 1e-9
    assert hub.submitted[0].side == +1


# ---------------------------------------------------------------------------
# _route method — calls hub.submit_ticket (not handler callbacks)
# ---------------------------------------------------------------------------

def test_route_calls_hub_submit_ticket():
    """_route(req) must delegate directly to hub.submit_ticket (no handlers)."""
    from vike_trader_app.exec.events import OrderRequest
    eng, hub, acct = _make_engine()
    req = OrderRequest(
        client_order_id="x-1",
        venue=_VENUE,
        symbol=_SYM,
        side=+1,
        qty=1.0,
        order_type="market",
        price=None,
        ts=999,
    )
    eng._route(req)
    assert hub.submitted == [req]


# ---------------------------------------------------------------------------
# _route no-fire contract (ported from StrategyLiveEngine era; P0 must-preserve)
# ---------------------------------------------------------------------------

def test_route_does_not_synchronously_fire_on_order_submitted():
    """Submitting an order via the engine must NOT synchronously call on_order_submitted
    on the strategy.  Only the StrategyEventAdapter, driven by a real OrderSubmitted
    EventBus event, fires that callback.  This test guards the contract so no future
    refactor accidentally adds a direct handler call inside _route.
    """
    from vike_trader_app.core.strategy import Strategy

    received: list = []

    class _RecordingStrategy(Strategy):
        WARMUP = 0

        def on_order_submitted(self, event) -> None:  # type: ignore[override]
            received.append(event)

        def on_bar(self, bar) -> None:
            pass

    strat = _RecordingStrategy()
    eng, hub, acct = _make_engine()
    strat._engine = eng

    # Submit directly through the engine — on_order_submitted must NOT fire.
    eng.submit(_SYM, +1, 1.0)

    assert received == [], (
        "_route fired on_order_submitted synchronously — only the StrategyEventAdapter "
        "(driven by the EventBus) should fire strategy callbacks"
    )
    # The hub DID receive the ticket (order was routed)
    assert len(hub.submitted) == 1


# ---------------------------------------------------------------------------
# cancel_all (ported from StrategyLiveEngine — per-sym API)
# ---------------------------------------------------------------------------

def test_cancel_all_cancels_open_registry_orders():
    """cancel_all(sym) must cancel every key in hub.registry and clear the book."""
    eng, hub, acct = _make_engine()
    hub.registry = {"c1": object(), "c2": object()}
    eng.cancel_all(_SYM)
    assert set(hub.canceled) == {"c1", "c2"}


# ---------------------------------------------------------------------------
# client_order_id uniqueness (per-sym)
# ---------------------------------------------------------------------------

def test_client_order_id_is_unique_per_submit():
    """Two consecutive submits for the same symbol must produce distinct client_order_ids."""
    eng, hub, acct = _make_engine()
    eng.submit(_SYM, +1, 1.0)
    eng.submit(_SYM, +1, 1.0)
    ids = [r.client_order_id for r in hub.submitted]
    assert ids[0] != ids[1]


# ---------------------------------------------------------------------------
# now property
# ---------------------------------------------------------------------------

def test_now_returns_injected_clock():
    """engine.now must return the value from the injected now_ms callable."""
    eng, _, _ = _make_engine()
    assert eng.now == 999


# ---------------------------------------------------------------------------
# submit_limit (per-sym)
# ---------------------------------------------------------------------------

def test_submit_limit_builds_limit_request():
    """submit_limit(sym, +1, 1.0, price=95.0) → limit order in hub."""
    eng, hub, acct = _make_engine()
    eng.submit_limit(_SYM, +1, 1.0, price=95.0)
    req = hub.submitted[0]
    assert req.order_type == "limit" and req.price == 95.0 and req.side == +1


def test_submit_limit_weight_accepted():
    """weight= kwarg is accepted (signature-parity) and silently ignored."""
    eng, hub, acct = _make_engine()
    eng.submit_limit(_SYM, -1, 2.0, price=105.0, weight=0.5)
    req = hub.submitted[0]
    assert req.order_type == "limit" and req.price == 105.0 and req.side == -1


# ---------------------------------------------------------------------------
# submit_market_close / submit_limit_close (per-sym)
# ---------------------------------------------------------------------------

def test_submit_market_close_builds_market_request():
    """submit_market_close(sym, -1, 1.5) → market order in hub."""
    eng, hub, acct = _make_engine()
    eng.submit_market_close(_SYM, -1, 1.5)
    req = hub.submitted[0]
    assert req.order_type == "market" and req.side == -1 and req.qty == 1.5


def test_submit_limit_close_builds_limit_request():
    """submit_limit_close(sym, -1, 2.0, price=98.0) → limit order in hub."""
    eng, hub, acct = _make_engine()
    eng.submit_limit_close(_SYM, -1, 2.0, price=98.0)
    req = hub.submitted[0]
    assert req.order_type == "limit" and req.price == 98.0 and req.side == -1


# ---------------------------------------------------------------------------
# submit_stop / submit_trailing — register, don't raise (per-sym)
# ---------------------------------------------------------------------------

def test_submit_stop_registers_not_raises():
    """submit_stop must register a conditional in the book (not raise)."""
    eng, hub, acct = _make_engine()
    eng.submit_stop(_SYM, -1, 1.0, price=90.0)
    assert len(eng._books.get(_SYM, [])) == 1


def test_submit_trailing_registers_not_raises():
    """submit_trailing with a mark must register a conditional (not raise)."""
    eng, hub, acct = _make_engine()
    acct.marks[(_VENUE, _SYM)] = 100.0
    eng.submit_trailing(_SYM, +1, 1.0, trail=5.0)
    assert len(eng._books.get(_SYM, [])) == 1


# ---------------------------------------------------------------------------
# Multi-TF buffer (per-sym bars_for / forming_for)
# ---------------------------------------------------------------------------

def test_mtf_bars_for_returns_list():
    """bars_for(sym, '1h') returns a list after feeding 2h of 1-min bars."""
    from vike_trader_app.core.model import Bar
    acct = _Acct()
    hub = _Hub(venue=_VENUE, symbol=_SYM)
    eng = LiveEngine({_SYM: hub}, acct, timeframes=["1h"], now_ms=lambda: 0)
    for t in range(120):
        eng.add_live_bar(_SYM, Bar(ts=t * 60_000, open=1, high=1, low=1, close=1))
    assert isinstance(eng.bars_for(_SYM, "1h"), list)


def test_mtf_bars_for_returns_completed_bars_only():
    """bars_for(sym, '1h') yields at least 1 completed bar after 61 1-min bars."""
    from vike_trader_app.core.model import Bar
    acct = _Acct()
    hub = _Hub(venue=_VENUE, symbol=_SYM)
    eng = LiveEngine({_SYM: hub}, acct, timeframes=["1h"], now_ms=lambda: 0)
    for t in range(61):
        eng.add_live_bar(_SYM, Bar(ts=t * 60_000, open=1, high=1, low=1, close=1))
    assert len(eng.bars_for(_SYM, "1h")) >= 1


def test_mtf_forming_for_returns_forming_bar():
    """forming_for(sym, '1h') is not None halfway through the first hour."""
    from vike_trader_app.core.model import Bar
    acct = _Acct()
    hub = _Hub(venue=_VENUE, symbol=_SYM)
    eng = LiveEngine({_SYM: hub}, acct, timeframes=["1h"], now_ms=lambda: 0)
    for t in range(30):
        eng.add_live_bar(_SYM, Bar(ts=t * 60_000, open=1, high=1, low=1, close=1))
    assert eng.forming_for(_SYM, "1h") is not None


# ---------------------------------------------------------------------------
# Conditional stop/trailing — per-sym check_conditionals (ported from StrategyLiveEngine)
# ---------------------------------------------------------------------------

def _hbar(ts: int, o: float, h: float, l: float, c: float) -> Bar:
    return Bar(ts=ts, open=o, high=h, low=l, close=c, volume=1.0)


def test_submit_stop_buy_check_conditionals_fires_market_buy():
    """A buy-stop fires a market buy when bar.high >= price."""
    eng, hub, acct = _make_engine()
    eng.submit_stop(_SYM, +1, 2.0, price=110.0)
    fired = eng.check_conditionals(_SYM, _hbar(1, 100, 105, 99, 102))
    assert fired == [] and len(hub.submitted) == 0
    fired = eng.check_conditionals(_SYM, _hbar(2, 106, 111, 105, 109))
    assert len(fired) == 1
    req = hub.submitted[0]
    assert req.side == +1 and req.qty == 2.0 and req.order_type == "market"
    assert len(eng._books.get(_SYM, [])) == 0  # fire-once: book empty


def test_submit_stop_sell_check_conditionals_fires_market_sell():
    """A sell-stop fires a market sell when bar.low <= price."""
    eng, hub, acct = _make_engine()
    eng.submit_stop(_SYM, -1, 1.0, price=90.0)
    fired = eng.check_conditionals(_SYM, _hbar(1, 100, 101, 95, 98))
    assert fired == [] and len(hub.submitted) == 0
    fired = eng.check_conditionals(_SYM, _hbar(2, 96, 97, 89, 91))
    assert len(fired) == 1
    assert hub.submitted[0].side == -1 and hub.submitted[0].qty == 1.0


def test_submit_trailing_inits_extreme_from_mark_then_fires():
    """submit_trailing initialises extreme from account.marks; ratchets; fires on retrace."""
    eng, hub, acct = _make_engine()
    acct.marks[(_VENUE, _SYM)] = 100.0
    eng.submit_trailing(_SYM, -1, 1.0, trail=5.0)
    fired = eng.check_conditionals(_SYM, _hbar(1, 100, 108, 99, 107))
    assert fired == [] and len(hub.submitted) == 0
    fired = eng.check_conditionals(_SYM, _hbar(2, 106, 107, 102, 104))
    assert len(fired) == 1
    assert hub.submitted[0].side == -1 and hub.submitted[0].qty == 1.0


def test_cancel_all_clears_conditional_book():
    """cancel_all(sym) must clear client-side conditionals."""
    eng, hub, acct = _make_engine()
    eng.submit_stop(_SYM, +1, 2.0, price=110.0)
    assert len(eng._books[_SYM]) == 1
    eng.cancel_all(_SYM)
    assert _SYM not in eng._books or len(eng._books[_SYM]) == 0
    fired = eng.check_conditionals(_SYM, _hbar(1, 100, 200, 50, 150))
    assert fired == [] and len(hub.submitted) == 0


# ---------------------------------------------------------------------------
# No-mark guard on submit_trailing (ported from StrategyLiveEngine review wave 1)
# ---------------------------------------------------------------------------

def test_buy_trailing_no_mark_does_not_register():
    """A BUY trailing armed when mark=0.0 must NOT register in the book."""
    eng, hub, acct = _make_engine()
    eng.submit_trailing(_SYM, +1, 1.0, trail=5.0)
    assert _SYM not in eng._books or len(eng._books[_SYM]) == 0


def test_buy_trailing_no_mark_does_not_fire_on_next_bar():
    """A BUY trailing armed with no mark must not route any order on the next bar."""
    eng, hub, acct = _make_engine()
    eng.submit_trailing(_SYM, +1, 1.0, trail=5.0)
    fired = eng.check_conditionals(_SYM, _hbar(1, 1000.0, 1010.0, 990.0, 1000.0))
    assert fired == [] and len(hub.submitted) == 0


def test_buy_trailing_with_mark_registers_and_fires():
    """Positive regression: a BUY trailing WITH a mark registers and fires on retrace."""
    eng, hub, acct = _make_engine()
    acct.marks[(_VENUE, _SYM)] = 100.0
    eng.submit_trailing(_SYM, +1, 1.0, trail=5.0)
    assert len(eng._books[_SYM]) == 1
    fired = eng.check_conditionals(_SYM, _hbar(1, 100.0, 106.0, 99.0, 104.0))
    assert len(fired) == 1
    assert hub.submitted[0].side == +1 and hub.submitted[0].qty == 1.0

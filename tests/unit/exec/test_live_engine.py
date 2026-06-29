"""Tests for the unified LiveEngine (P0 Task 1 — generalize LivePortfolioEngine).

At N=1 (one hub/account) validates:
- LivePortfolioEngine is LiveEngine  (alias kept)
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

from vike_trader_app.exec.live_portfolio_engine import LiveEngine, LivePortfolioEngine
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
    assert LivePortfolioEngine is LiveEngine


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

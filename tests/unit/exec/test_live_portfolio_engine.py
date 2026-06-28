"""Tests for LivePortfolioEngine — multi-symbol live engine interface (A2d Task 1).

Mirrors test_strategy_live_engine.py, extended for per-symbol routing isolation and
shared-Account equity aggregation across N symbols.

Verifies:
- symbols list
- submit(sym, side, size) → correct hub ONLY (per-symbol routing isolation)
- submit_close(sym) flattens the held position via the shared account
- position_of / price_of read from the shared Account
- equity_now() == account.balance + sum(unrealized_pnl per symbol)
- add_live_bar(sym, bar) → buffer update + account.set_mark
- unique client_order_id per submit
- submit_stop / submit_trailing raise NotImplementedError (A2e deferred)
"""
import pytest

from vike_trader_app.exec.live_portfolio_engine import LivePortfolioEngine
from vike_trader_app.exec.events import OrderRequest
from vike_trader_app.core.model import Bar, Position


# ---------------------------------------------------------------------------
# Stubs
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
    """Minimal Account stub with per-symbol positions, marks, and balance."""

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

_BTC = "BTCUSDT"
_ETH = "ETHUSDT"


def _make_engine(bal: float = 10_000.0):
    acct = _Acct(bal=bal)
    hub_btc = _Hub(venue="binance", symbol=_BTC)
    hub_eth = _Hub(venue="binance", symbol=_ETH)
    hubs = {_BTC: hub_btc, _ETH: hub_eth}
    eng = LivePortfolioEngine(hubs, acct, now_ms=lambda: 111)
    return eng, hub_btc, hub_eth, acct


def _bar(close: float = 100.0, ts: int = 0) -> Bar:
    return Bar(ts=ts, open=close, high=close, low=close, close=close)


# ---------------------------------------------------------------------------
# symbols
# ---------------------------------------------------------------------------

def test_symbols_lists_both():
    eng, *_ = _make_engine()
    assert set(eng.symbols) == {_BTC, _ETH}
    assert len(eng.symbols) == 2


# ---------------------------------------------------------------------------
# Per-symbol routing isolation
# ---------------------------------------------------------------------------

def test_submit_btc_routes_to_btc_hub_only():
    eng, hub_btc, hub_eth, _ = _make_engine()
    eng.submit(_BTC, +1, 2.0)
    assert len(hub_btc.submitted) == 1
    assert len(hub_eth.submitted) == 0
    req = hub_btc.submitted[0]
    assert isinstance(req, OrderRequest)
    assert req.venue == "binance"
    assert req.symbol == _BTC
    assert req.side == +1
    assert req.qty == 2.0
    assert req.order_type == "market"
    assert req.ts == 111


def test_submit_eth_routes_to_eth_hub_only():
    eng, hub_btc, hub_eth, _ = _make_engine()
    eng.submit(_ETH, -1, 3.0)
    assert len(hub_eth.submitted) == 1
    assert len(hub_btc.submitted) == 0
    req = hub_eth.submitted[0]
    assert req.symbol == _ETH
    assert req.side == -1
    assert req.qty == 3.0
    assert req.order_type == "market"


def test_submit_weight_and_raw_accepted_for_parity():
    """weight/raw/stop are signature-parity params; the explicit size is routed as-is."""
    eng, hub_btc, hub_eth, _ = _make_engine()
    eng.submit(_BTC, +1, 1.5, weight=0.5, raw=True, stop=None)
    assert len(hub_btc.submitted) == 1
    assert hub_btc.submitted[0].qty == 1.5


# ---------------------------------------------------------------------------
# submit_close
# ---------------------------------------------------------------------------

def test_submit_close_flattens_long():
    eng, hub_btc, hub_eth, acct = _make_engine()
    acct.positions[("binance", _BTC, "BOTH")] = {"size": 4.0, "avg_px": 50.0}
    eng.submit_close(_BTC)
    assert len(hub_btc.submitted) == 1
    req = hub_btc.submitted[0]
    assert req.side == -1    # sell to flatten long
    assert req.qty == 4.0
    assert len(hub_eth.submitted) == 0


def test_submit_close_flattens_short():
    eng, hub_btc, hub_eth, acct = _make_engine()
    acct.positions[("binance", _BTC, "BOTH")] = {"size": -3.0, "avg_px": 80.0}
    eng.submit_close(_BTC)
    req = hub_btc.submitted[0]
    assert req.side == +1    # buy to flatten short
    assert req.qty == 3.0


def test_submit_close_noop_if_flat():
    eng, hub_btc, hub_eth, acct = _make_engine()
    acct.positions[("binance", _BTC, "BOTH")] = {"size": 0.0, "avg_px": 0.0}
    eng.submit_close(_BTC)
    assert len(hub_btc.submitted) == 0


def test_submit_close_eth_routes_to_eth_hub_only():
    eng, hub_btc, hub_eth, acct = _make_engine()
    acct.positions[("binance", _ETH, "BOTH")] = {"size": 10.0, "avg_px": 200.0}
    eng.submit_close(_ETH)
    assert len(hub_eth.submitted) == 1
    assert len(hub_btc.submitted) == 0


# ---------------------------------------------------------------------------
# position_of / price_of
# ---------------------------------------------------------------------------

def test_position_of_reads_shared_account():
    eng, _, _, acct = _make_engine()
    acct.positions[("binance", _BTC, "BOTH")] = {"size": 7.0, "avg_px": 30_000.0}
    pos = eng.position_of(_BTC)
    assert isinstance(pos, Position)
    assert pos.size == 7.0
    assert pos.avg_price == 30_000.0


def test_position_of_returns_flat_when_absent():
    eng, _, _, _ = _make_engine()
    pos = eng.position_of(_ETH)
    assert pos.size == 0.0
    assert pos.avg_price == 0.0


def test_price_of_reads_account_marks():
    eng, _, _, acct = _make_engine()
    acct.marks[("binance", _BTC)] = 45_000.0
    assert eng.price_of(_BTC) == 45_000.0


def test_price_of_returns_zero_if_no_mark():
    eng, _, _, _ = _make_engine()
    assert eng.price_of(_ETH) == 0.0


# ---------------------------------------------------------------------------
# equity_now
# ---------------------------------------------------------------------------

def test_equity_now_balance_plus_unrealized_both_symbols():
    eng, _, _, acct = _make_engine(bal=5_000.0)
    acct._unrealized_by_sym[_BTC] = 300.0
    acct._unrealized_by_sym[_ETH] = 200.0
    # equity = 5000 + 300 + 200 = 5500
    assert eng.equity_now() == 5_500.0


def test_equity_now_zero_unrealized():
    eng, _, _, acct = _make_engine(bal=10_000.0)
    assert eng.equity_now() == 10_000.0


# ---------------------------------------------------------------------------
# add_live_bar
# ---------------------------------------------------------------------------

def test_add_live_bar_calls_set_mark():
    eng, _, _, acct = _make_engine()
    bar = _bar(close=50_000.0, ts=1_000)
    eng.add_live_bar(_BTC, bar)
    assert ("binance", _BTC, 50_000.0) in acct._set_mark_calls


def test_add_live_bar_eth_calls_correct_set_mark():
    eng, _, _, acct = _make_engine()
    bar = _bar(close=2_000.0, ts=2_000)
    eng.add_live_bar(_ETH, bar)
    assert ("binance", _ETH, 2_000.0) in acct._set_mark_calls
    # BTC set_mark NOT called
    assert not any(c[1] == _BTC for c in acct._set_mark_calls)


def test_add_live_bar_appends_to_symbol_buffer():
    """add_live_bar feeds the per-symbol BarSeriesBuffer (not shared)."""
    eng, _, _, _ = _make_engine()
    b1 = _bar(close=100.0, ts=0)
    b2 = _bar(close=200.0, ts=1)
    eng.add_live_bar(_BTC, b1)
    eng.add_live_bar(_BTC, b2)
    # BTC buffer should have 2 bars; ETH should have 0
    assert len(eng._bufs[_BTC].bars) == 2
    assert len(eng._bufs[_ETH].bars) == 0


# ---------------------------------------------------------------------------
# unique client_order_id
# ---------------------------------------------------------------------------

def test_unique_client_order_ids_per_submit():
    eng, hub_btc, hub_eth, _ = _make_engine()
    eng.submit(_BTC, +1, 1.0)
    eng.submit(_ETH, +1, 1.0)
    eng.submit(_BTC, -1, 1.0)
    ids = [r.client_order_id for r in hub_btc.submitted + hub_eth.submitted]
    assert len(set(ids)) == len(ids), "All client_order_ids must be unique"


# ---------------------------------------------------------------------------
# now property
# ---------------------------------------------------------------------------

def test_now_returns_injected_clock():
    eng, _, _, _ = _make_engine()
    assert eng.now == 111


# ---------------------------------------------------------------------------
# submit_stop / submit_trailing raise NotImplementedError (A2e deferred)
# ---------------------------------------------------------------------------

def test_submit_stop_raises():
    eng, _, _, _ = _make_engine()
    with pytest.raises(NotImplementedError, match="A2e"):
        eng.submit_stop(_BTC, -1, 1.0, 90.0)


def test_submit_trailing_raises():
    eng, _, _, _ = _make_engine()
    with pytest.raises(NotImplementedError):
        eng.submit_trailing(_BTC, +1, 1.0, 5.0)


# ---------------------------------------------------------------------------
# IMPORTANT-1: missing verbs (submit_limit, submit_market_close,
#              submit_limit_close, cancel_all, bars_for, forming_for)
# ---------------------------------------------------------------------------

def test_submit_limit_routes_to_correct_hub():
    """submit_limit(sym, side, size, price) → limit OrderRequest to the right hub only."""
    eng, hub_btc, hub_eth, _ = _make_engine()
    eng.submit_limit(_BTC, +1, 2.0, 50_000.0)
    assert len(hub_btc.submitted) == 1
    assert len(hub_eth.submitted) == 0
    req = hub_btc.submitted[0]
    assert req.order_type == "limit"
    assert req.price == 50_000.0
    assert req.side == +1
    assert req.qty == 2.0


def test_submit_limit_eth_routes_to_eth_hub():
    eng, hub_btc, hub_eth, _ = _make_engine()
    eng.submit_limit(_ETH, -1, 3.0, 2_000.0)
    assert len(hub_eth.submitted) == 1
    assert len(hub_btc.submitted) == 0
    req = hub_eth.submitted[0]
    assert req.order_type == "limit"
    assert req.price == 2_000.0


def test_submit_limit_weight_raw_stop_accepted():
    """weight/raw/stop are parity params and must not raise."""
    eng, hub_btc, _, _ = _make_engine()
    eng.submit_limit(_BTC, +1, 1.0, 40_000.0, weight=0.5, raw=True, stop=None)
    assert len(hub_btc.submitted) == 1


def test_submit_market_close_routes_to_correct_hub():
    """submit_market_close(sym, side, size) → market OrderRequest to the right hub."""
    eng, hub_btc, hub_eth, _ = _make_engine()
    eng.submit_market_close(_BTC, -1, 2.0)
    assert len(hub_btc.submitted) == 1
    assert len(hub_eth.submitted) == 0
    req = hub_btc.submitted[0]
    assert req.order_type == "market"
    assert req.side == -1
    assert req.qty == 2.0


def test_submit_market_close_noop_on_zero_size():
    eng, hub_btc, _, _ = _make_engine()
    eng.submit_market_close(_BTC, -1, 0.0)
    assert len(hub_btc.submitted) == 0


def test_submit_limit_close_routes_to_correct_hub():
    """submit_limit_close(sym, side, size, price) → limit OrderRequest to the right hub."""
    eng, hub_btc, hub_eth, _ = _make_engine()
    eng.submit_limit_close(_BTC, -1, 1.5, 48_000.0)
    assert len(hub_btc.submitted) == 1
    assert len(hub_eth.submitted) == 0
    req = hub_btc.submitted[0]
    assert req.order_type == "limit"
    assert req.price == 48_000.0
    assert req.qty == 1.5


def test_cancel_all_calls_cancel_ticket_for_each_registry_entry():
    """cancel_all(sym) cancels every coid in hub.registry for that symbol."""
    eng, hub_btc, hub_eth, _ = _make_engine()
    hub_btc.registry["coid-1"] = object()
    hub_btc.registry["coid-2"] = object()
    eng.cancel_all(_BTC)
    assert set(hub_btc.canceled) == {"coid-1", "coid-2"}
    # ETH hub must be untouched
    assert hub_eth.canceled == []


def test_cancel_all_noop_when_registry_empty():
    eng, hub_btc, _, _ = _make_engine()
    eng.cancel_all(_BTC)  # must not raise
    assert hub_btc.canceled == []


def _make_engine_with_tf(bal: float = 10_000.0, timeframes=("1h",)):
    """Engine where the per-symbol buffers have a higher timeframe pre-registered."""
    from vike_trader_app.core.bar_buffer import BarSeriesBuffer
    acct = _Acct(bal=bal)
    hub_btc = _Hub(venue="binance", symbol=_BTC)
    hub_eth = _Hub(venue="binance", symbol=_ETH)
    hubs = {_BTC: hub_btc, _ETH: hub_eth}
    eng = LivePortfolioEngine(hubs, acct, now_ms=lambda: 111)
    # Re-initialise the per-symbol buffers WITH a timeframe so bars_for/forming_for don't KeyError.
    for sym in eng.symbols:
        eng._bufs[sym] = BarSeriesBuffer([], timeframes=list(timeframes))
    return eng, hub_btc, hub_eth, acct


def test_bars_for_delegates_to_symbol_buffer():
    """bars_for(sym, tf) returns results from the correct per-symbol BarSeriesBuffer."""
    from vike_trader_app.core.model import Bar
    eng, _, _, _ = _make_engine_with_tf()
    # No bars fed yet → empty list
    result = eng.bars_for(_BTC, "1h")
    assert isinstance(result, list)
    assert result == []


def test_bars_for_btc_does_not_include_eth_bars():
    """bars_for for BTC only returns BTC's buffer, not ETH's."""
    from vike_trader_app.core.model import Bar
    eng, _, _, _ = _make_engine_with_tf()
    # Feed bars to ETH only; BTC should still be empty.
    for i in range(3):
        eng.add_live_bar(_ETH, Bar(ts=i * 3_600_000, open=2000.0, high=2100.0, low=1900.0, close=2050.0))
    assert eng.bars_for(_BTC, "1h") == []
    # ETH also empty at ts=0 (still forming); at a later ts it should have bars
    # The point is just that BTC's buffer is untouched.


def test_forming_for_delegates_to_correct_symbol_buffer():
    """forming_for(sym, tf) delegates to the per-symbol BarSeriesBuffer (None when no bars)."""
    eng, _, _, _ = _make_engine_with_tf()
    result = eng.forming_for(_ETH, "1h")
    assert result is None  # no bars fed yet → forming bar is None


def test_forming_for_eth_different_from_btc():
    """forming_for reads the correct per-symbol buffer (isolation)."""
    from vike_trader_app.core.model import Bar
    eng, _, _, _ = _make_engine_with_tf()
    # Feed a bar to ETH only; BTC should still return None for forming_for.
    eng.add_live_bar(_ETH, Bar(ts=0, open=2000.0, high=2100.0, low=1900.0, close=2050.0))
    assert eng.forming_for(_ETH, "1h") is not None  # ETH has a bar in the window
    assert eng.forming_for(_BTC, "1h") is None      # BTC has no bars


def test_unknown_symbol_raises_value_error():
    """_hub(sym) with an unknown symbol raises ValueError with a descriptive message."""
    eng, _, _, _ = _make_engine()
    with pytest.raises(ValueError, match="armed basket"):
        eng.submit(_BTC[:-3] + "XYZ", +1, 1.0)  # "BTCXYZ" is not in the basket


def test_unknown_symbol_cancel_all_raises_value_error():
    eng, _, _, _ = _make_engine()
    with pytest.raises(ValueError, match="armed basket"):
        eng.cancel_all("SOLANA")

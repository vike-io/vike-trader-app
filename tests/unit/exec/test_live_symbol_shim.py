"""Tests for LiveSymbolShim — SingleSymbolStrategy compat over the unified LiveEngine.

Verifies that every unkeyed call on a SingleSymbolStrategy is forwarded through
LiveSymbolShim to the correct symbol-keyed verb on LiveEngine.

Key assertions:
- buy(size)               → engine.submit("BTCUSDT", +1, size)
- sell(size)              → engine.submit("BTCUSDT", -1, size)
- close()                 → engine.submit_close("BTCUSDT")
- self.position           → engine.position_of("BTCUSDT")
- order_target_percent()  → engine.order_target_percent("BTCUSDT", pct)
                            which internally calls order_target(sym, ...) with raw=True
- equity_now()            → engine.equity_now()
- drawdown_now()          → engine.drawdown_now()
- bars_for(tf)            → engine.bars_for("BTCUSDT", tf)
- forming_for(tf)         → engine.forming_for("BTCUSDT", tf)
- add_live_bar(bar)       → engine.add_live_bar("BTCUSDT", bar)
- check_conditionals(bar) → engine.check_conditionals("BTCUSDT", bar)
- cancel_all()            → engine.cancel_all("BTCUSDT")
- submit_limit()          → engine.submit_limit("BTCUSDT", ...)
- submit_stop()           → engine.submit_stop("BTCUSDT", ...)
- submit_trailing()       → engine.submit_trailing("BTCUSDT", ...)
- submit_market_close()   → engine.submit_market_close("BTCUSDT", ...)
- submit_limit_close()    → engine.submit_limit_close("BTCUSDT", ...)
- price property          → engine.price_of("BTCUSDT")
"""
import warnings
import pytest
from unittest.mock import MagicMock, call

from vike_trader_app.exec.live_portfolio_engine import LiveEngine
from vike_trader_app.exec.live_symbol_shim import LiveSymbolShim
from vike_trader_app.core.model import Bar, Position
from vike_trader_app.exec.events import OrderRequest


# ---------------------------------------------------------------------------
# Stubs mirroring test_live_portfolio_engine.py
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


_BTC = "BTCUSDT"


def _make(bal: float = 10_000.0):
    acct = _Acct(bal=bal)
    hub = _Hub(venue="binance", symbol=_BTC)
    hubs = {_BTC: hub}
    eng = LiveEngine(hubs, acct, now_ms=lambda: 111)
    shim = LiveSymbolShim(eng, _BTC)
    return eng, hub, acct, shim


def _bar(close: float = 100.0, ts: int = 1) -> Bar:
    return Bar(ts=ts, open=close, high=close + 1, low=close - 1, close=close, volume=1.0)


# ---------------------------------------------------------------------------
# Helpers — a concrete SingleSymbolStrategy subclass for integration path
# ---------------------------------------------------------------------------

def _make_concrete_strategy():
    """Return a SingleSymbolStrategy subclass that exercises buy/position/order_target_percent."""
    from vike_trader_app.core.compat_strategy import SingleSymbolStrategy

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)

        class _Strat(SingleSymbolStrategy):
            def __init__(self):
                super().__init__()
                self._calls = []

            def on_bar(self, bar):
                self.buy(1.0)
                _ = self.position  # read position
                self.order_target_percent(0.5)

        strat = _Strat()
    return strat


# ---------------------------------------------------------------------------
# Unit tests: LiveSymbolShim forwards each unkeyed verb to the keyed engine
# ---------------------------------------------------------------------------

class TestLiveSymbolShimForwarding:
    """Each unkeyed verb on the shim should forward to the keyed LiveEngine verb."""

    def test_submit_buy_forwarded(self):
        eng, hub, _, shim = _make()
        shim.submit(+1, 2.0)
        assert len(hub.submitted) == 1
        req = hub.submitted[0]
        assert req.symbol == _BTC
        assert req.side == +1
        assert req.qty == 2.0

    def test_submit_sell_forwarded(self):
        eng, hub, _, shim = _make()
        shim.submit(-1, 3.0)
        assert len(hub.submitted) == 1
        assert hub.submitted[0].side == -1
        assert hub.submitted[0].qty == 3.0

    def test_submit_close_forwarded(self):
        eng, hub, acct, shim = _make()
        # seed a position so submit_close actually routes
        acct.positions[("binance", _BTC, "BOTH")] = {"size": 1.0, "avg_px": 100.0}
        shim.submit_close()
        assert len(hub.submitted) == 1
        assert hub.submitted[0].symbol == _BTC

    def test_position_property(self):
        eng, hub, acct, shim = _make()
        acct.positions[("binance", _BTC, "BOTH")] = {"size": 2.5, "avg_px": 50_000.0}
        pos = shim.position
        assert isinstance(pos, Position)
        assert pos.size == 2.5
        assert pos.avg_price == 50_000.0

    def test_equity_now(self):
        eng, hub, acct, shim = _make(bal=5_000.0)
        assert shim.equity_now() == eng.equity_now()

    def test_drawdown_now(self):
        eng, hub, _, shim = _make()
        assert shim.drawdown_now() == eng.drawdown_now()

    def test_cancel_all_forwarded(self):
        eng, hub, _, shim = _make()
        # register a fake resting order in the hub registry
        hub.registry["fake-coid"] = True
        shim.cancel_all()
        assert "fake-coid" in hub.canceled

    def test_submit_limit_forwarded(self):
        eng, hub, _, shim = _make()
        shim.submit_limit(+1, 1.0, 95_000.0)
        assert len(hub.submitted) == 1
        req = hub.submitted[0]
        assert req.order_type == "limit"
        assert req.price == 95_000.0
        assert req.symbol == _BTC

    def test_submit_stop_registered(self):
        """submit_stop registers a client-side conditional (no hub call yet)."""
        eng, hub, acct, shim = _make()
        # seed mark so submit_stop doesn't do anything unexpected
        acct.marks[("binance", _BTC)] = 100_000.0
        shim.submit_stop(-1, 0.5, 98_000.0)
        # no hub call on register
        assert len(hub.submitted) == 0
        # check_conditionals fires it when triggered
        bar = Bar(ts=1, open=97_000.0, high=97_500.0, low=96_000.0, close=97_000.0, volume=1.0)
        fired = eng.check_conditionals(_BTC, bar)
        assert len(fired) == 1
        assert len(hub.submitted) == 1

    def test_submit_trailing_no_hub_call_when_no_mark(self):
        """submit_trailing with no mark is a no-op (logs warning, no crash)."""
        eng, hub, _, shim = _make()
        # no mark set → price_of returns 0.0 → no-op
        shim.submit_trailing(-1, 0.5, 500.0)
        assert len(hub.submitted) == 0

    def test_submit_market_close_forwarded(self):
        eng, hub, _, shim = _make()
        shim.submit_market_close(-1, 1.0)
        assert len(hub.submitted) == 1
        req = hub.submitted[0]
        assert req.order_type == "market"
        assert req.side == -1

    def test_submit_limit_close_forwarded(self):
        eng, hub, _, shim = _make()
        shim.submit_limit_close(-1, 1.0, 97_000.0)
        assert len(hub.submitted) == 1
        req = hub.submitted[0]
        assert req.order_type == "limit"
        assert req.price == 97_000.0

    def test_bars_for_forwarded(self):
        acct = _Acct()
        hub = _Hub(venue="binance", symbol=_BTC)
        eng = LiveEngine({_BTC: hub}, acct, timeframes=["1h"], now_ms=lambda: 111)
        shim = LiveSymbolShim(eng, _BTC)
        bar = _bar(100.0, ts=1)
        eng.add_live_bar(_BTC, bar)
        # returns completed higher-TF bars (empty with only one base bar)
        result = shim.bars_for("1h")
        assert isinstance(result, list)  # forwarding succeeded; empty is fine

    def test_forming_for_forwarded(self):
        acct = _Acct()
        hub = _Hub(venue="binance", symbol=_BTC)
        eng = LiveEngine({_BTC: hub}, acct, timeframes=["1h"], now_ms=lambda: 111)
        shim = LiveSymbolShim(eng, _BTC)
        bar = _bar(100.0, ts=1)
        eng.add_live_bar(_BTC, bar)
        result = shim.forming_for("1h")
        # may be None or a Bar depending on the buffer state — just ensure no error
        assert result is None or hasattr(result, "close")

    def test_add_live_bar_forwarded(self):
        eng, hub, acct, shim = _make()
        bar = _bar(50_000.0, ts=5)
        shim.add_live_bar(bar)
        # mark should now be set
        assert acct.marks.get(("binance", _BTC)) == 50_000.0

    def test_check_conditionals_forwarded(self):
        eng, hub, acct, shim = _make()
        acct.marks[("binance", _BTC)] = 100_000.0
        shim.submit_stop(-1, 0.5, 98_000.0)
        bar = Bar(ts=1, open=97_000.0, high=97_500.0, low=96_000.0, close=97_000.0, volume=1.0)
        fired = shim.check_conditionals(bar)
        assert len(fired) == 1

    def test_price_property(self):
        eng, hub, acct, shim = _make()
        acct.marks[("binance", _BTC)] = 42_000.0
        assert shim.price == 42_000.0

    def test_now_property(self):
        eng, hub, _, shim = _make()
        assert shim.now == 111  # injected clock returns 111


# ---------------------------------------------------------------------------
# Integration: SingleSymbolStrategy.on_bar via LiveSymbolShim
# ---------------------------------------------------------------------------

class TestSingleSymbolStrategyIntegration:
    """Bind a real SingleSymbolStrategy subclass's _engine to a LiveSymbolShim and drive on_bar."""

    def test_buy_and_position_and_order_target_percent(self):
        eng, hub, acct, shim = _make()
        # Seed mark so order_target_percent can compute
        acct.marks[("binance", _BTC)] = 50_000.0

        strat = _make_concrete_strategy()
        strat._engine = shim
        strat.index = 0

        bar = _bar(50_000.0)
        strat.on_bar(bar)

        # buy(1.0) → submit(+1, 1.0) on BTCUSDT
        submitted = hub.submitted
        assert len(submitted) >= 1, "expected at least one submitted order from buy(1.0)"
        first = submitted[0]
        assert first.symbol == _BTC
        assert first.side == +1
        assert first.qty == 1.0

        # position read: should not raise (returns Position from Account)
        pos = strat.position
        assert isinstance(pos, Position)

    def test_order_target_percent_symbol_forwarded(self):
        """order_target_percent goes through order_target with raw=True via the keyed engine."""
        eng, hub, acct, shim = _make()
        # Set equity + mark so the sizing produces a known target
        acct.marks[("binance", _BTC)] = 50_000.0
        # balance=10_000, equity≈10_000, 50% → $5000 / 50_000 = 0.1 BTC target
        # position is 0, so delta = 0.1 → buy 0.1
        shim.order_target_percent(0.5)
        # verify a buy order was placed for symbol BTCUSDT
        assert len(hub.submitted) == 1
        req = hub.submitted[0]
        assert req.symbol == _BTC
        assert req.side == +1
        assert abs(req.qty - 0.1) < 1e-9

    def test_symbol_isolation(self):
        """The shim never routes to any other symbol's hub."""
        acct = _Acct(bal=10_000.0)
        hub_btc = _Hub(venue="binance", symbol="BTCUSDT")
        hub_eth = _Hub(venue="binance", symbol="ETHUSDT")
        hubs = {"BTCUSDT": hub_btc, "ETHUSDT": hub_eth}
        eng = LiveEngine(hubs, acct, now_ms=lambda: 1)
        shim = LiveSymbolShim(eng, "BTCUSDT")

        shim.submit(+1, 1.0)
        assert len(hub_btc.submitted) == 1
        assert len(hub_eth.submitted) == 0  # isolation

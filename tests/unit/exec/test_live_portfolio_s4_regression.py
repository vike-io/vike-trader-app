"""S4: LiveEngine/LivePump wired to a Portfolio (per-venue Account aggregator).

Pins the regression gate: single-venue armed basket -> one Account -> equity bit-identical to the
old `balance + sum(unrealized)` read; a fill followed by an authoritative AccountState frame must
not double-count; a 2-venue basket gets two distinct Accounts (no clobber) with per-symbol mults.
"""
import pytest

from vike_trader_app.exec.accounting import Account
from vike_trader_app.exec.portfolio import Portfolio
from vike_trader_app.exec.live_portfolio_engine import LiveEngine
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.events import AccountState, FillEvent
from vike_trader_app.core.model import Bar, Position


_BTC = "BTCUSDT"
_ETH = "ETHUSDT"


class _Hub:
    def __init__(self, venue: str, symbol: str):
        self.venue = venue
        self.symbol = symbol
        self.bus = EventBus()    # LivePump subscribes a StrategyEventAdapter per hub bus
        self.submitted: list = []
        self.canceled: list = []
        self.registry: dict = {}

    def submit_ticket(self, req) -> None:
        self.submitted.append(req)

    def cancel_ticket(self, coid: str) -> None:
        self.canceled.append(coid)


def _bar(close: float, ts: int = 0) -> Bar:
    return Bar(ts=ts, open=close, high=close, low=close, close=close)


def test_equity_now_single_venue_equals_portfolio_equity():
    pf = Portfolio()
    acct = pf.account("binance", multipliers={_BTC: 1.0, _ETH: 1.0}, seed=0.0)
    acct.balance = 5_000.0
    acct.positions[("binance", _BTC, "BOTH")] = {"size": 2.0, "avg_px": 100.0}
    acct.positions[("binance", _ETH, "BOTH")] = {"size": 3.0, "avg_px": 50.0}
    acct.set_mark("binance", _BTC, 110.0)   # +20
    acct.set_mark("binance", _ETH, 60.0)    # +30
    hubs = {_BTC: _Hub("binance", _BTC), _ETH: _Hub("binance", _ETH)}
    eng = LiveEngine(hubs, pf, now_ms=lambda: 0)
    old = acct.balance + sum(acct.unrealized_pnl("binance", s) for s in (_BTC, _ETH))
    assert eng.equity_now() == pytest.approx(pf.equity(), abs=1e-12)
    assert eng.equity_now() == pytest.approx(old, abs=1e-12)


def test_position_of_and_price_of_deref_portfolio_account():
    pf = Portfolio()
    acct = pf.account("binance")
    acct.positions[("binance", _BTC, "BOTH")] = {"size": 7.0, "avg_px": 30_000.0}
    acct.set_mark("binance", _BTC, 31_000.0)
    hubs = {_BTC: _Hub("binance", _BTC)}
    eng = LiveEngine(hubs, pf, now_ms=lambda: 0)
    pos = eng.position_of(_BTC)
    assert isinstance(pos, Position)
    assert pos.size == 7.0 and pos.avg_price == 30_000.0
    assert eng.price_of(_BTC) == 31_000.0


def test_add_live_bar_sets_mark_on_portfolio_account():
    pf = Portfolio()
    acct = pf.account("binance")
    hubs = {_BTC: _Hub("binance", _BTC)}
    eng = LiveEngine(hubs, pf, now_ms=lambda: 0)
    eng.add_live_bar(_BTC, _bar(close=42_000.0, ts=1_000))
    assert acct.marks[("binance", _BTC)] == 42_000.0


def test_livepump_drives_real_portfolio_equity():
    from vike_trader_app.exec.live_portfolio_pump import LivePump
    from vike_trader_app.core.strategy import Strategy

    class _NoopStrat(Strategy):
        WARMUP = 0
        def on_bar(self, bar):  # pragma: no cover
            pass

    pf = Portfolio()
    acct = pf.account("binance", multipliers={_BTC: 1.0})
    hubs = {_BTC: _Hub("binance", _BTC)}
    pump = LivePump(_NoopStrat(), hubs, pf, now_ms=lambda: 0)
    acct.apply_fill(FillEvent(trade_id="t1", client_order_id="c1", venue="binance", symbol=_BTC,
                              side=+1, last_qty=1.0, last_px=100.0, commission=0.0,
                              liquidity_side="taker", ts=0))
    acct.set_mark("binance", _BTC, 120.0)
    assert pump.engine.equity_now() == pytest.approx(20.0, abs=1e-12)


def test_two_venue_basket_gets_two_accounts_no_clobber():
    pf = Portfolio()
    a_binance = pf.account("binance", multipliers={_BTC: 1.0})
    a_bybit = pf.account("bybit", multipliers={_ETH: 10.0})
    assert a_binance is not a_bybit
    assert pf.account("binance") is a_binance
    assert pf.account("bybit") is a_bybit
    assert a_binance.multiplier_of(_BTC) == 1.0
    assert a_bybit.multiplier_of(_ETH) == 10.0


def test_fill_then_authoritative_frame_not_double_counted():
    """The design doc's named double-count catch: a fill with NON-ZERO realized then an authoritative
    AccountState frame -> equity unchanged (seed/realized drop, venue balance already embeds them)."""
    pf = Portfolio()
    acct = pf.account("binance", multipliers={_BTC: 1.0}, seed=1_000.0)
    hubs = {_BTC: _Hub("binance", _BTC)}
    eng = LiveEngine(hubs, pf, now_ms=lambda: 0)
    acct.apply_fill(FillEvent(trade_id="t1", client_order_id="c1", venue="binance", symbol=_BTC,
                              side=+1, last_qty=2.0, last_px=100.0, commission=0.0,
                              liquidity_side="taker", ts=0))
    acct.apply_fill(FillEvent(trade_id="t2", client_order_id="c2", venue="binance", symbol=_BTC,
                              side=-1, last_qty=1.0, last_px=130.0, commission=0.0,
                              liquidity_side="taker", ts=1))
    acct.set_mark("binance", _BTC, 130.0)
    assert acct.realized_pnl == pytest.approx(30.0, abs=1e-12)
    assert acct.balance_mode == "delta"
    pre = eng.equity_now()
    assert pre == pytest.approx(1_060.0, abs=1e-12)   # seed 1000 + realized 30 + unrealized 30
    acct.apply_account_state(AccountState(venue="binance", balances=(("USDT", 1_030.0),), ts=2))
    assert acct.balance_mode == "authoritative"
    post = eng.equity_now()
    assert post == pytest.approx(pre, abs=1e-12)        # balance 1030 + unrealized 30 = 1060
    assert post == pytest.approx(1_060.0, abs=1e-12)

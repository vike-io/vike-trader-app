"""S3 parity harness: Portfolio (per-venue Accounts fed only by the FillEvent mirror) reproduces
MultiSymbolEngine equity bar-by-bar, AND the SAME synthetic fill stream reproduces a LiveEngine-
shaped authoritative read.

Anti-vacuity: a pure backtest-formula Portfolio would pass the first assertion via the same
notional-cancels algebra. The SECOND assertion drives an `authoritative` Account (balance set
absolutely by the venue, the LIVE mode) off the IDENTICAL fill stream and pins it to the same
number — so the LIVE aggregator, not just the backtest algebra, is proven.

TOL = 1e-10 (absolute).
"""

from __future__ import annotations

import itertools

import pytest

from vike_trader_app.core.model import Bar
from vike_trader_app.core.multi_symbol_engine import MultiSymbolEngine, PortfolioStrategy
from vike_trader_app.exec.accounting import Account
from vike_trader_app.exec.events import FillEvent, FundingEvent
from vike_trader_app.exec.portfolio import Portfolio
from vike_trader_app.tester.config import TesterConfig

TOL = 1e-10


def _bar(ts, o, c):
    return Bar(ts=ts, open=o, high=max(o, c) + 1, low=min(o, c) - 1, close=c, volume=1.0)


def _series(opens):
    return [_bar(i * 60_000, o, o) for i, o in enumerate(opens)]


class _TradeBoth(PortfolioStrategy):
    def on_bar(self, ts, bars):
        if self.index == 0:
            self.buy("AAA", 2.0)
            self.sell("BBB", 1.0)
        elif self.index == 2:
            self.sell("AAA", 1.0)     # reduce the long
        elif self.index == 3:
            self.buy("BBB", 3.0)      # close short (1) + flip long (2)


VENUE = "binance"
MULTS = {"AAA": 1.0, "BBB": 10.0}


def _build_mirror():
    pf = Portfolio()
    acct = pf.account(VENUE, multipliers=MULTS, seed=10_000.0)
    counter = itertools.count(1)

    def on_fill(symbol, side_sign, size, price, fee, ts, is_maker):
        n = next(counter)
        acct.apply_fill(FillEvent(
            trade_id=f"t{n}", client_order_id=f"c{n}", venue=VENUE, symbol=symbol,
            side=side_sign, last_qty=size, last_px=price, commission=fee,
            liquidity_side="maker" if is_maker else "taker", ts=ts,
        ))

    return pf, acct, on_fill


def test_portfolio_equity_matches_mse_after_run():
    bars = {"AAA": _series([100, 110, 120, 130, 125]), "BBB": _series([10, 12, 14, 16, 15])}
    pf, acct, on_fill = _build_mirror()
    eng = MultiSymbolEngine(bars, _TradeBoth(), cash=10_000.0, fee_rate=0.001,
                            multipliers=MULTS, on_fill=on_fill)
    result = eng.run()
    for s in eng.symbols:
        acct.set_mark(VENUE, s, eng._sym[s].price)
    for s in eng.symbols:
        pos = eng._sym[s].pos
        mirror = acct.positions.get((VENUE, s, "BOTH"), {"size": 0.0, "avg_px": 0.0})
        assert mirror["size"] == pytest.approx(pos.size, abs=TOL), s
        if pos.size != 0.0:
            assert mirror["avg_px"] == pytest.approx(pos.avg_price, abs=TOL), s
    assert pf.equity() == pytest.approx(eng.equity_now(), abs=TOL)
    assert pf.equity() == pytest.approx(result.final_equity, abs=TOL)


def test_per_bar_equity_parity():
    """Bar-by-bar parity via prefix replay: rerun a fresh engine+mirror for each prefix length and
    compare Portfolio.equity() to engine.equity_now() at EVERY prefix (pins parity per bar)."""
    bars = {"AAA": _series([100, 110, 120, 130, 125]), "BBB": _series([10, 12, 14, 16, 15])}
    for k in range(1, len(bars["AAA"]) + 1):
        prefix = {s: bars[s][:k] for s in bars}
        pf_k, acct_k, on_fill_k = _build_mirror()
        eng_k = MultiSymbolEngine(prefix, _TradeBoth(), cash=10_000.0, fee_rate=0.001,
                                  multipliers=MULTS, on_fill=on_fill_k)
        res_k = eng_k.run()
        for s in eng_k.symbols:
            acct_k.set_mark(VENUE, s, eng_k._sym[s].price)
        assert pf_k.equity() == pytest.approx(eng_k.equity_now(), abs=TOL), f"prefix {k}"
        assert eng_k.equity_now() == pytest.approx(res_k.equity_curve[-1], abs=TOL), f"prefix {k}"


def test_authoritative_live_read_matches_same_fill_stream():
    """Anti-vacuity: an AUTHORITATIVE Account (LIVE mode) fed the SAME stream pins (balance + Σ
    unrealized) to the delta-mode equity — proving the LIVE aggregator path, not just the algebra.

    The authoritative model is the LiveEngine read (``LiveEngine.equity_now`` / ``Account.equity_all``
    authoritative branch): equity = venue wallet balance + Σ unrealized. A real venue reports the
    wallet balance as ``seed + Σ realized_pnl - Σ commission`` — the open position's mark-to-market
    lives ENTIRELY in ``unrealized``, NOT in the wallet (you don't pay the full notional out of the
    wallet on a perp/margin fill, and on spot the asset you bought is carried at mark via unrealized).
    We mirror that exactly: ``apply_fill`` nets ``-commission`` into ``balance`` and folds realized PnL
    into ``realized_pnl``; we then push the SAME realized increment into the wallet ``balance`` so the
    authoritative ``balance + unrealized`` read reproduces the delta-mode ``seed - fees + realized +
    unrealized`` total off the IDENTICAL fill stream."""
    bars = {"AAA": _series([100, 110, 120, 130, 125]), "BBB": _series([10, 12, 14, 16, 15])}
    pf_delta, acct_delta, on_fill_delta = _build_mirror()
    eng = MultiSymbolEngine(bars, _TradeBoth(), cash=10_000.0, fee_rate=0.001,
                            multipliers=MULTS, on_fill=on_fill_delta)
    eng.run()
    for s in eng.symbols:
        acct_delta.set_mark(VENUE, s, eng._sym[s].price)
    delta_equity = pf_delta.equity()

    auth = Account(venue=VENUE, multipliers=MULTS, balance_mode="authoritative")
    auth.balance = 10_000.0   # the venue-reported starting wallet balance (authoritative)
    counter = itertools.count(1)

    def on_fill_auth(symbol, side_sign, size, price, fee, ts, is_maker):
        n = next(counter)
        prior_realized = auth.realized_pnl
        auth.apply_fill(FillEvent(   # nets -commission into balance; folds realized into realized_pnl
            trade_id=f"a{n}", client_order_id=f"ca{n}", venue=VENUE, symbol=symbol,
            side=side_sign, last_qty=size, last_px=price, commission=fee,
            liquidity_side="maker" if is_maker else "taker", ts=ts,
        ))
        # The venue wallet accrues the realized PnL of any closed portion (commission already netted
        # by apply_fill). The open position's notional is NOT debited from the wallet — it rides in
        # `unrealized`. This is exactly how Binance/Bybit report walletBalance.
        auth.balance += (auth.realized_pnl - prior_realized)

    eng2 = MultiSymbolEngine(bars, _TradeBoth(), cash=10_000.0, fee_rate=0.001,
                             multipliers=MULTS, on_fill=on_fill_auth)
    eng2.run()
    for s in eng2.symbols:
        auth.set_mark(VENUE, s, eng2._sym[s].price)
    assert auth.balance_mode == "authoritative"
    assert auth.equity_all() == pytest.approx(delta_equity, abs=TOL)


def test_two_venue_portfolio_equity_parity():
    """Synthetic 2-venue scenario: AAA trades on binance, BBB on bybit. The Portfolio aggregates
    two per-venue Accounts and must still equal the single MSE equity (cross-venue LIVE shape)."""
    bars = {"AAA": _series([100, 110, 120, 130, 125]), "BBB": _series([10, 12, 14, 16, 15])}
    venue_of = {"AAA": "binance", "BBB": "bybit"}
    pf = Portfolio()
    acc_bn = pf.account("binance", multipliers=MULTS, seed=10_000.0)
    acc_by = pf.account("bybit", multipliers=MULTS, seed=0.0)
    accts = {"binance": acc_bn, "bybit": acc_by}
    counter = itertools.count(1)

    def on_fill(symbol, side_sign, size, price, fee, ts, is_maker):
        n = next(counter)
        v = venue_of[symbol]
        accts[v].apply_fill(FillEvent(
            trade_id=f"t{n}", client_order_id=f"c{n}", venue=v, symbol=symbol,
            side=side_sign, last_qty=size, last_px=price, commission=fee,
            liquidity_side="maker" if is_maker else "taker", ts=ts,
        ))

    eng = MultiSymbolEngine(bars, _TradeBoth(), cash=10_000.0, fee_rate=0.001,
                            multipliers=MULTS, on_fill=on_fill)
    eng.run()
    for s in eng.symbols:
        accts[venue_of[s]].set_mark(venue_of[s], s, eng._sym[s].price)
    # seed sum is 10_000 (binance) + 0 (bybit) = 10_000, matching MSE starting cash.
    assert pf.equity() == pytest.approx(eng.equity_now(), abs=TOL)
    # net_position is multiplier-independent raw size; verify both legs land in the right Account.
    assert acc_bn.positions.get(("binance", "AAA", "BOTH"), {}).get("size", 0.0) != 0.0
    assert acc_by.positions.get(("bybit", "BBB", "BOTH"), {}).get("size", 0.0) != 0.0


def test_portfolio_engine_kwargs_never_passes_on_fill():
    """DETERMINISTIC perf invariant: config-built MSE kwargs carry no on_fill -> sweep engine has no mirror."""
    cfg = TesterConfig()
    kw = cfg.portfolio_engine_kwargs()
    assert "on_fill" not in kw
    assert "sim_account" not in kw
    assert "on_funding" not in kw
    bars = {"AAA": _series([1, 2, 3])}
    eng = MultiSymbolEngine(bars, _TradeBoth(), **kw)
    assert eng._on_fill is None
    assert eng._on_submit is None
    assert eng._on_funding is None


# --- funding emitter -----------------------------------------------------------------------------


def _bar_f(ts, o, c, funding):
    return Bar(ts=ts, open=o, high=max(o, c) + 1, low=min(o, c) - 1, close=c, volume=1.0, funding=funding)


class _BuyHoldFunding(PortfolioStrategy):
    def on_bar(self, ts, bars):
        if self.index == 0:
            self.buy("AAA", 2.0)


def test_funding_emitter_keeps_balance_in_step_with_engine_cash():
    bars = {"AAA": [_bar_f(i * 60_000, p, p, 0.0001) for i, p in enumerate([100, 110, 120, 130])]}
    pf = Portfolio()
    acct = pf.account(VENUE, multipliers={"AAA": 1.0}, seed=10_000.0)
    fcounter = itertools.count(1)

    def on_fill(symbol, side_sign, size, price, fee, ts, is_maker):
        n = next(fcounter)
        acct.apply_fill(FillEvent(
            trade_id=f"ft{n}", client_order_id=f"fc{n}", venue=VENUE, symbol=symbol,
            side=side_sign, last_qty=size, last_px=price, commission=fee,
            liquidity_side="maker" if is_maker else "taker", ts=ts,
        ))

    def on_funding(symbol, amount_signed, ts):
        acct.apply_funding(FundingEvent(
            venue=VENUE, symbol=symbol, position_side="BOTH",
            funding_rate=0.0, amount=amount_signed, ts=ts,
        ))

    eng = MultiSymbolEngine(bars, _BuyHoldFunding(), cash=10_000.0, multipliers={"AAA": 1.0}, on_fill=on_fill)
    eng._on_funding = on_funding   # mirror the SimulatedExchange wiring (S5 wires this on the bus)
    eng.run()
    for s in eng.symbols:
        acct.set_mark(VENUE, s, eng._sym[s].price)
    assert acct.funding_paid != 0.0
    assert pf.equity() == pytest.approx(eng.equity_now(), abs=TOL)

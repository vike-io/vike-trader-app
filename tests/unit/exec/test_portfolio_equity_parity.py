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

    Independence guarantee: realized_indep and fees_indep are computed by THIS TEST using
    weighted-average-cost arithmetic written inline — they do NOT call Account.apply_fill,
    compute_fill, or read Account.realized_pnl / Account.fees_paid.  A bug in compute_fill that
    corrupts Account.realized_pnl would move MSE.equity_now() and the delta-Account oracle
    together but leave realized_indep / fees_indep unchanged, making the assertion below fail.
    """
    _EPS = 1e-12

    bars = {"AAA": _series([100, 110, 120, 130, 125]), "BBB": _series([10, 12, 14, 16, 15])}

    # --- primary delta-Portfolio oracle (unchanged) -----------------------------------
    pf_delta, acct_delta, on_fill_delta = _build_mirror()
    eng = MultiSymbolEngine(bars, _TradeBoth(), cash=10_000.0, fee_rate=0.001,
                            multipliers=MULTS, on_fill=on_fill_delta)
    eng.run()
    for s in eng.symbols:
        acct_delta.set_mark(VENUE, s, eng._sym[s].price)
    delta_equity = pf_delta.equity()

    # --- independent shadow ledger (test-owned WAC, no compute_fill / Account) -------
    seed = 10_000.0
    fees_indep: float = 0.0          # cumulative commissions paid (positive = cost)
    realized_indep: float = 0.0      # gross WAC realized PnL on closed portions
    # per-symbol open position: size (signed) + average entry price
    pos_size: dict[str, float] = {}
    pos_avg:  dict[str, float] = {}

    def _wac_apply(symbol: str, side_sign: int, qty: float, price: float, fee: float) -> None:
        """Weighted-average-cost position update + realized PnL — no Account/compute_fill call."""
        nonlocal fees_indep, realized_indep
        fees_indep += fee
        mult = MULTS[symbol]
        cur_size = pos_size.get(symbol, 0.0)
        cur_avg  = pos_avg.get(symbol, 0.0)
        delta = side_sign * qty
        if cur_size == 0.0:                               # open from flat
            pos_size[symbol] = delta
            pos_avg[symbol]  = price
        elif (cur_size > 0.0) == (delta > 0.0):          # add in same direction (WAC)
            new_size = cur_size + delta
            pos_avg[symbol]  = (cur_avg * abs(cur_size) + price * abs(delta)) / abs(new_size)
            pos_size[symbol] = new_size
        else:                                             # reduce / close / flip (opposite side)
            sign = 1.0 if cur_size > 0.0 else -1.0
            closing = min(abs(delta), abs(cur_size))
            realized_indep += (price - cur_avg) * (sign * closing) * mult
            remaining = abs(cur_size) - closing
            leftover  = abs(delta) - closing
            if remaining > _EPS:                          # partial reduce
                pos_size[symbol] = sign * remaining
                # avg_px stays the same (cost basis of the remaining lot is unchanged)
            elif leftover > _EPS:                         # close-and-flip
                pos_size[symbol] = (1.0 if delta > 0.0 else -1.0) * leftover
                pos_avg[symbol]  = price
            else:                                         # full close to flat
                pos_size[symbol] = 0.0
                pos_avg[symbol]  = 0.0

    counter_indep = itertools.count(1)

    def on_fill_indep(symbol, side_sign, size, price, fee, ts, is_maker):
        _wac_apply(symbol, side_sign, size, price, fee)

    eng2 = MultiSymbolEngine(bars, _TradeBoth(), cash=10_000.0, fee_rate=0.001,
                             multipliers=MULTS, on_fill=on_fill_indep)
    eng2.run()

    # independently-computed wallet balance (venue model: seed – fees + realized; unrealized is open)
    indep_balance = seed - fees_indep + realized_indep

    # independently-computed unrealized PnL using the shadow ledger's positions and engine mark prices
    unrealized_indep: float = sum(
        (eng2._sym[s].price - pos_avg.get(s, 0.0)) * pos_size.get(s, 0.0) * MULTS[s]
        for s in eng2.symbols
    )
    indep_equity = indep_balance + unrealized_indep

    # --- authoritative Account set from independently-computed balance ----------------
    auth = Account(venue=VENUE, multipliers=MULTS, balance_mode="authoritative")
    # Seed the authoritative Account with the INDEPENDENTLY derived wallet balance so its equity_all()
    # uses (indep_balance + Σ unrealized from marks) — neither realized_pnl nor fees_paid from
    # apply_fill routes into this number.
    auth.balance = indep_balance
    # Apply fills to the auth Account ONLY to populate positions/marks for unrealized computation.
    # Note: auth.realized_pnl and auth.balance changes from apply_fill are NOT used in the assertions
    # (auth.balance was set externally; we only need auth.positions to be populated for set_mark/unrealized_pnl).
    counter_auth = itertools.count(1)

    def on_fill_auth(symbol, side_sign, size, price, fee, ts, is_maker):
        n = next(counter_auth)
        auth.apply_fill(FillEvent(
            trade_id=f"a{n}", client_order_id=f"ca{n}", venue=VENUE, symbol=symbol,
            side=side_sign, last_qty=size, last_px=price, commission=fee,
            liquidity_side="maker" if is_maker else "taker", ts=ts,
        ))

    eng3 = MultiSymbolEngine(bars, _TradeBoth(), cash=10_000.0, fee_rate=0.001,
                             multipliers=MULTS, on_fill=on_fill_auth)
    eng3.run()
    for s in eng3.symbols:
        auth.set_mark(VENUE, s, eng3._sym[s].price)

    # Re-override balance AFTER fill replay so apply_fill's balance mutations don't contaminate;
    # the authoritative balance must come from the independent shadow ledger.
    auth.balance = indep_balance
    assert auth.balance_mode == "authoritative"

    # Three-way pin: all three routes must agree to within TOL.
    # (1) independent arithmetic pins to MSE
    assert indep_equity == pytest.approx(eng2.equity_now(), abs=TOL), \
        "indep_equity vs MSE"
    # (2) authoritative Account (position data from fills, balance from independent ledger) pins to MSE
    assert auth.equity_all() == pytest.approx(eng.equity_now(), abs=TOL), \
        "auth.equity_all() vs MSE"
    # (3) keep the primary delta-Portfolio assertion
    assert delta_equity == pytest.approx(eng.equity_now(), abs=TOL), \
        "Portfolio delta equity vs MSE"


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

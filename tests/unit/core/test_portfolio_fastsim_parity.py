"""BYTE-PARITY gate: the time×symbol portfolio kernel vs the event ``MultiSymbolEngine``.

ONE momentum top-k rotation strategy is implemented BOTH ways:

  * Event:  a :class:`CrossSectionalStrategy` subclass run on :class:`MultiSymbolEngine`
            (the source of truth — next-open fills, column-order shared cash, taker fee,
            slippage, cost-basis averaging).
  * Kernel: the SAME momentum logic vectorized into a ``(T, S)`` ``target_weights`` matrix,
            then :func:`fast_portfolio_backtest` (the compiled fast path).

The kernel runs as plain Python in-env (numba no-ops via the shim), so equality is EXACT:
equity curve (per bar), final equity, and trade PnLs must match to ``abs < 1e-9``.

The synthetic universe has divergent trends so top-k membership actually rotates (winners
change → drop-outs get fully closed), exercising open / add / reduce / close / flip branches.
"""

from __future__ import annotations

import numpy as np
import pytest

from vike_trader_app.core.model import Bar
from vike_trader_app.core.multi_symbol_engine import CrossSectionalStrategy, MultiSymbolEngine
from vike_trader_app.core.portfolio_fastsim import (
    CrossSectionalSignalStrategy,
    fast_portfolio_backtest,
)

_TOL = 1e-9


# --------------------------------------------------------------------------- #
# Synthetic universe: 3 symbols whose RANKING flips part-way so top-k rotates. #
# --------------------------------------------------------------------------- #
# A starts strongest then stalls; C starts weakest then rips; B is steady middle.
# With a trailing-return score and k=2, the held set rotates A,B -> B,C (A drops out
# -> full close; C opens), exercising open/close/reduce branches.
_PRICES = {
    "A": [100, 108, 117, 127, 138, 140, 141, 141, 140, 139, 138, 137],
    "B": [100, 102, 104, 106, 109, 112, 115, 118, 121, 124, 127, 130],
    "C": [100,  99,  98,  97,  98, 102, 110, 122, 137, 150, 162, 175],
}
_SYMBOLS = ["A", "B", "C"]
_N = len(_PRICES["A"])


def _series(prices):
    # open != close so next-open fills are distinguishable from the close mark.
    return [
        Bar(ts=i * 60_000, open=p - 0.5, high=p + 1.0, low=p - 1.0, close=float(p), volume=1.0)
        for i, p in enumerate(prices)
    ]


def _bars_by_symbol():
    return {s: _series(_PRICES[s]) for s in _SYMBOLS}


def _matrices():
    """Column-aligned ``(T, S)`` matrices (column order == ``_SYMBOLS``)."""
    opens = np.array([[p - 0.5 for p in _PRICES[s]] for s in _SYMBOLS], np.float64).T
    highs = np.array([[p + 1.0 for p in _PRICES[s]] for s in _SYMBOLS], np.float64).T
    lows = np.array([[p - 1.0 for p in _PRICES[s]] for s in _SYMBOLS], np.float64).T
    closes = np.array([[float(p) for p in _PRICES[s]] for s in _SYMBOLS], np.float64).T
    funding = np.zeros((_N, len(_SYMBOLS)), np.float64)
    ts = np.array([i * 60_000 for i in range(_N)], np.int64)
    return opens, highs, lows, closes, funding, ts


# --------------------------------------------------------------------------- #
# Event-side strategy.                                                        #
# --------------------------------------------------------------------------- #
class _MomentumEvent(CrossSectionalStrategy):
    lookback = 3

    def __init__(self, *, k, rebalance_every):
        # Instance attrs (set BEFORE super().__init__, which reads self.rebalance_every via the
        # schedule ctor) — avoids mutating the class dict, which would race under xdist.
        self.k = k
        self.rebalance_every = rebalance_every
        super().__init__()

    def score(self, symbol, history):
        if len(history) <= self.lookback:
            return None
        return history[-1] / history[-1 - self.lookback] - 1.0  # trailing return


# --------------------------------------------------------------------------- #
# Kernel-side: build the SAME momentum logic into a (T, S) target-weight matrix. #
# --------------------------------------------------------------------------- #
def _build_target_weights(closes, *, k, rebalance_every, lookback):
    """Vectorized mirror of ``CrossSectionalStrategy._rebalance`` for the momentum score.

    Non-rebalance bars -> all-NaN row (queue nothing). Rebalance bars -> ``1/k`` to the top-k
    winners (descending trailing return, ties broken by column order = stable), ``0.0`` to
    everyone else (a held drop-out -> full close; a non-held non-winner -> delta 0, no-op,
    exactly the union the event engine produces with held drop-outs + winners).
    """
    T, S = closes.shape
    weights = np.full((T, S), np.nan, np.float64)
    for i in range(T):
        if i % rebalance_every != 0:
            continue  # schedule did not fire -> NaN row (no rebalance)
        # scores: only symbols with len(history) > lookback, i.e. i >= lookback
        if i < lookback:
            valid = []
        else:
            valid = list(range(S))  # every symbol has a score (no None from history-length)
        if len(valid) < k:
            continue  # engine's `len(scores) < k` guard -> no orders -> NaN row
        scores = closes[i, :] / closes[i - lookback, :] - 1.0
        # Stable descending sort by score; ties -> lower column index first (Python sorted is
        # stable and the scores dict preserves self.symbols == column insertion order).
        order = sorted(valid, key=lambda s: (-scores[s], s))
        winners = order[:k]
        row = np.zeros(S, np.float64)
        for w in winners:
            row[w] = 1.0 / k
        weights[i, :] = row
    return weights


class _MomentumKernel(CrossSectionalSignalStrategy):
    def __init__(self, *, k, rebalance_every, lookback=3):
        self.k = k
        self.rebalance_every = rebalance_every
        self.lookback = lookback

    def target_weights(self, data):
        return _build_target_weights(
            np.ascontiguousarray(data["close"], np.float64),
            k=self.k, rebalance_every=self.rebalance_every, lookback=self.lookback)


# --------------------------------------------------------------------------- #
# The parity assertions.                                                      #
# --------------------------------------------------------------------------- #
def _assert_parity(*, k, rebalance_every, taker_fee, slippage, cash):
    bars = _bars_by_symbol()
    strat = _MomentumEvent(k=k, rebalance_every=rebalance_every)
    eng = MultiSymbolEngine(bars, strat, taker_fee=taker_fee, slippage=slippage, cash=cash)
    event_res = eng.run()

    opens, highs, lows, closes, funding, ts = _matrices()
    kstrat = _MomentumKernel(k=k, rebalance_every=rebalance_every, lookback=_MomentumEvent.lookback)
    kernel_res = kstrat.run(
        {"open": opens, "high": highs, "low": lows, "close": closes,
         "funding": funding, "ts": ts, "symbols": _SYMBOLS},
        taker_fee=taker_fee, slippage=slippage, init_cash=cash,
    )

    # 1) Equity curve, per bar.
    assert len(kernel_res["equity_curve"]) == len(event_res.equity_curve) == _N
    for i, (ek, ee) in enumerate(zip(kernel_res["equity_curve"], event_res.equity_curve)):
        assert ek == pytest.approx(ee, abs=_TOL), f"equity bar {i}: kernel {ek} != event {ee}"

    # 2) Final equity.
    assert kernel_res["final_equity"] == pytest.approx(event_res.final_equity, abs=_TOL)

    # 3) Trade count + PnLs (sorted by (exit_ts, symbol, entry_price) so ordering is canonical).
    assert kernel_res["n_trades"] == len(event_res.trades)

    def _key(tr):
        return (tr.exit_ts, tr.symbol, round(tr.entry_price, 9), round(tr.size, 9))

    ev_trades = sorted(event_res.trades, key=_key)
    k_trades = sorted(kernel_res["trades"], key=_key)
    for et, kt in zip(ev_trades, k_trades):
        assert kt.pnl == pytest.approx(et.pnl, abs=_TOL), f"trade pnl: kernel {kt} != event {et}"
        assert kt.entry_price == pytest.approx(et.entry_price, abs=_TOL)
        assert kt.exit_price == pytest.approx(et.exit_price, abs=_TOL)
        assert kt.size == pytest.approx(et.size, abs=_TOL)
        assert kt.fees == pytest.approx(et.fees, abs=_TOL)
        assert kt.symbol == et.symbol


def test_parity_k2_every1_with_costs():
    """Primary scenario: k=2, rebalance every bar, taker fee + slippage on -> rotations fire."""
    _assert_parity(k=2, rebalance_every=1, taker_fee=0.001, slippage=0.0005, cash=100_000.0)


def test_parity_k1_every2_no_costs():
    """k=1, rebalance every 2 bars, frictionless -> a single held name flips A->B/C->… (flip branch)."""
    _assert_parity(k=1, rebalance_every=2, taker_fee=0.0, slippage=0.0, cash=50_000.0)


def test_parity_k2_every3_with_costs():
    """k=2, rebalance every 3 bars -> non-rebalance bars must queue nothing (NaN rows)."""
    _assert_parity(k=2, rebalance_every=3, taker_fee=0.0008, slippage=0.0002, cash=250_000.0)


def test_rotation_actually_happens():
    """Guard: the universe must trigger a drop-out -> the kernel records a closing trade
    (otherwise the parity test could pass trivially on a never-rotating book)."""
    opens, highs, lows, closes, funding, ts = _matrices()
    weights = _build_target_weights(closes, k=2, rebalance_every=1, lookback=3)
    res = fast_portfolio_backtest(
        opens, highs, lows, closes, funding, ts, weights,
        taker_fee=0.001, slippage=0.0005, init_cash=100_000.0, symbols=_SYMBOLS)
    assert res["n_trades"] >= 1, "synthetic universe never rotated — fixture is not exercising exits"


def test_naive_carryforward_would_diverge():
    """Sanity: a NON-NaN carry-forward weight matrix (re-trading every bar) must NOT match the
    event engine on a cadence>1 schedule — proving the NaN 'no rebalance' sentinel is load-bearing."""
    bars = _bars_by_symbol()
    strat = _MomentumEvent(k=2, rebalance_every=3)
    event_res = MultiSymbolEngine(bars, strat, taker_fee=0.001, slippage=0.0005, cash=100_000.0).run()

    opens, highs, lows, closes, funding, ts = _matrices()
    w = _build_target_weights(closes, k=2, rebalance_every=3, lookback=3)
    # Forward-fill NaN rows with the last real weights (the WRONG model: re-trades every bar).
    last = None
    for i in range(w.shape[0]):
        if not np.isnan(w[i, 0]):
            last = w[i, :].copy()
        elif last is not None:
            w[i, :] = last
    bad = fast_portfolio_backtest(
        opens, highs, lows, closes, funding, ts, w,
        taker_fee=0.001, slippage=0.0005, init_cash=100_000.0, symbols=_SYMBOLS)
    assert bad["final_equity"] != pytest.approx(event_res.final_equity, abs=_TOL)

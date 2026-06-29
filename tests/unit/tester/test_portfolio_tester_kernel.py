"""Parity gate: MultiSymbolStrategyTester routes CrossSectionalSignalStrategy to the vectorized
kernel and produces results IDENTICAL to running the kernel directly on the same data.

The reference (event-engine) parity is already proven in tests/unit/core/test_portfolio_fastsim_parity.py
(kernel == MultiSymbolEngine to abs < 1e-9).  Here we pin the TESTER WIRING:

  Tester kernel path: _MomentumKernel (CrossSectionalSignalStrategy) → MultiSymbolStrategyTester.run()
                      → _run_one() detects isinstance → data_from_bars → strat.run() → kernel_result_to_obj
                      → TesterReport.from_result()

  Reference: the SAME _MomentumKernel.run() called directly (no tester wrapper), results wrapped into
             the same TesterReport shape.

Because the two paths ultimately call the same CrossSectionalSignalStrategy.run() on the same data, the
TesterReports must be numerically IDENTICAL.

Additionally we guard that regular CrossSectionalStrategy subclasses (run through MultiSymbolStrategyRunner)
still work correctly through the tester (event path not accidentally broken).
"""

from __future__ import annotations

import numpy as np
import pytest

from vike_trader_app.core.model import Bar
from vike_trader_app.core.multi_symbol_engine import CrossSectionalStrategy
from vike_trader_app.core.portfolio_adapter import MultiSymbolStrategyRunner, align_bars
from vike_trader_app.core.portfolio_fastsim import (
    CrossSectionalSignalStrategy,
    data_from_bars,
    kernel_result_to_obj,
)
from vike_trader_app.tester.config import TesterConfig
from vike_trader_app.tester.portfolio_tester import MultiSymbolStrategyTester
from vike_trader_app.tester.report import TesterReport

_TOL = 1e-7

# ---------------------------------------------------------------------------
# Synthetic universe — same as test_portfolio_fastsim_parity.py (divergent trends, rotating winners)
# ---------------------------------------------------------------------------
_PRICES = {
    "A": [100, 108, 117, 127, 138, 140, 141, 141, 140, 139, 138, 137],
    "B": [100, 102, 104, 106, 109, 112, 115, 118, 121, 124, 127, 130],
    "C": [100,  99,  98,  97,  98, 102, 110, 122, 137, 150, 162, 175],
}
_SYMBOLS = ["A", "B", "C"]


def _series(prices):
    return [
        Bar(ts=i * 60_000, open=p - 0.5, high=p + 1.0, low=p - 1.0, close=float(p), volume=1.0)
        for i, p in enumerate(prices)
    ]


def _bars_by_symbol():
    return {s: _series(_PRICES[s]) for s in _SYMBOLS}


# ---------------------------------------------------------------------------
# Kernel fast path: CrossSectionalSignalStrategy
# ---------------------------------------------------------------------------
def _build_target_weights(closes, *, k, rebalance_every, lookback):
    """Vectorized mirror of CrossSectionalStrategy._rebalance for the same momentum score."""
    T, S = closes.shape
    weights = np.full((T, S), np.nan, np.float64)
    for i in range(T):
        if i % rebalance_every != 0:
            continue
        if i < lookback:
            valid = []
        else:
            valid = list(range(S))
        if len(valid) < k:
            continue
        scores = closes[i, :] / closes[i - lookback, :] - 1.0
        order = sorted(valid, key=lambda s: (-scores[s], s))
        winners = order[:k]
        row = np.zeros(S, np.float64)
        for w in winners:
            row[w] = 1.0 / k
        weights[i, :] = row
    return weights


class _MomentumKernel(CrossSectionalSignalStrategy):
    """Trailing-return momentum, top-k equal weight — runs through the vectorized kernel."""

    def __init__(self, *, k=2, rebalance_every=1, lookback=3):
        self.k = k
        self.rebalance_every = rebalance_every
        self.lookback = lookback

    def target_weights(self, data):
        closes = np.ascontiguousarray(data["close"], np.float64)
        return _build_target_weights(
            closes, k=self.k, rebalance_every=self.rebalance_every, lookback=self.lookback,
        )


# ---------------------------------------------------------------------------
# Reference: run the same kernel strategy directly (bypass tester) and wrap into TesterReport
# ---------------------------------------------------------------------------
def _kernel_reference_report(*, k, rebalance_every, config):
    """Ground truth: call CrossSectionalSignalStrategy.run() directly, wrap into TesterReport."""
    strat = _MomentumKernel(k=k, rebalance_every=rebalance_every)
    bbs = _bars_by_symbol()
    taker_fee = config.taker_fee if config.taker_fee is not None else config.fee_rate
    data = data_from_bars(bbs)
    kernel_dict = strat.run(
        data,
        taker_fee=taker_fee,
        slippage=config.slippage,
        init_cash=config.cash,
        multiplier=config.multiplier,
    )
    result = kernel_result_to_obj(kernel_dict, data["ts"])
    return TesterReport.from_result(result, periods_per_year=config.periods_per_year)


def _tester_kernel_report(*, k, rebalance_every, config):
    """Tester-routed kernel path: CrossSectionalSignalStrategy through MultiSymbolStrategyTester."""
    tester = MultiSymbolStrategyTester(_bars_by_symbol(), config)
    return tester.run(lambda: _MomentumKernel(k=k, rebalance_every=rebalance_every))


# ---------------------------------------------------------------------------
# Parity tests: tester-routed kernel == direct kernel call
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("k,rev,taker_fee,slippage,cash", [
    (2, 1, 0.001, 0.0005, 100_000.0),
    (1, 2, 0.0,   0.0,    50_000.0),
    (2, 3, 0.0008, 0.0002, 250_000.0),
])
def test_tester_kernel_parity(k, rev, taker_fee, slippage, cash):
    """TesterReport from the tester-routed kernel path must match the direct kernel call to abs<1e-7."""
    cfg = TesterConfig(cash=cash, taker_fee=taker_fee, slippage=slippage)
    ref = _kernel_reference_report(k=k, rebalance_every=rev, config=cfg)
    kn  = _tester_kernel_report(k=k, rebalance_every=rev, config=cfg)

    assert kn.final_equity == pytest.approx(ref.final_equity, abs=_TOL), (
        f"final_equity: tester={kn.final_equity} direct={ref.final_equity}"
    )
    assert kn.total_return == pytest.approx(ref.total_return, abs=_TOL), (
        f"total_return: tester={kn.total_return} direct={ref.total_return}"
    )
    assert kn.sharpe == pytest.approx(ref.sharpe, abs=_TOL), (
        f"sharpe: tester={kn.sharpe} direct={ref.sharpe}"
    )
    assert len(kn.equity_curve) == len(ref.equity_curve), (
        f"equity_curve length: tester={len(kn.equity_curve)} direct={len(ref.equity_curve)}"
    )
    for i, (vk, vr) in enumerate(zip(kn.equity_curve, ref.equity_curve)):
        assert vk == pytest.approx(vr, abs=_TOL), f"equity_curve[{i}]: tester={vk} direct={vr}"


def test_tester_kernel_equity_ts_populated():
    """The kernel-routed TesterReport must have equity_ts populated (list of int timestamps)."""
    cfg = TesterConfig(cash=100_000.0, taker_fee=0.001)
    kn = _tester_kernel_report(k=2, rebalance_every=1, config=cfg)
    assert kn.equity_ts is not None, "equity_ts should be set by the kernel path"
    assert len(kn.equity_ts) == len(kn.equity_curve)
    assert all(isinstance(t, int) for t in kn.equity_ts)


def test_tester_fee_rate_fallback_matches_direct():
    """When taker_fee is None, config.fee_rate must be used — matching the direct kernel call."""
    # Engine internally: taker_fee = taker_fee if taker_fee is not None else fee_rate
    # _run_one() must mirror this exactly.
    cfg = TesterConfig(cash=100_000.0, fee_rate=0.001, taker_fee=None, slippage=0.0005)
    ref = _kernel_reference_report(k=2, rebalance_every=1, config=cfg)
    kn  = _tester_kernel_report(k=2, rebalance_every=1, config=cfg)
    assert kn.final_equity == pytest.approx(ref.final_equity, abs=_TOL)
    assert kn.sharpe == pytest.approx(ref.sharpe, abs=_TOL)


# ---------------------------------------------------------------------------
# Guard: regular CrossSectionalStrategy still routes to event engine (no regression)
# ---------------------------------------------------------------------------
class _SimpleSingleSymbolStrategy:
    """Trivial single-symbol buy-and-hold strategy (always fully invested) — goes through MultiSymbolRunner."""
    WARMUP = 0

    def __init__(self):
        self._entered = False

    def on_bar(self, bar):
        if not self._entered and self._engine.position.size == 0:
            equity = self._engine.equity_now()
            price = bar.close
            if price > 0:
                self._engine.submit(1, equity / price, raw=True)
                self._entered = True


def test_regular_strategy_uses_event_path():
    """A non-CrossSectionalSignalStrategy must still use the event engine (no regression).

    We check this by verifying MultiSymbolStrategyTester.run() completes without error
    and returns a TesterReport with non-trivial results for a simple single-symbol strategy.
    """
    from vike_trader_app.core.compat_strategy import SingleSymbolStrategy

    # Minimal always-in strategy using the standard API
    class _AlwaysIn(SingleSymbolStrategy):
        _entered = False

        def on_bar(self, bar):
            if not self._entered and self.position.size == 0:
                price = bar.close
                if price > 0:
                    self.order_target_percent(1.0)
                    self._entered = True

    cfg = TesterConfig(cash=10_000.0)
    tester = MultiSymbolStrategyTester(_bars_by_symbol(), cfg)
    report = tester.run(lambda: _AlwaysIn())
    assert report.final_equity > 0
    assert len(report.equity_curve) > 0
    # The equity_ts should NOT be set by the event path (it comes from MultiSymbolResult which
    # may or may not have it — we don't require it; we only require the path works at all).


def test_kernel_route_confirmed_by_isinstance():
    """Direct check that _run_one selects the kernel path for CrossSectionalSignalStrategy
    and the event path for everything else.

    We test this structurally: the kernel route must produce a non-default final_equity
    (the strategy trades something), and the equity_ts attribute must be a list of timestamps
    (only set by kernel_result_to_obj, not by MultiSymbolResult by default).
    """
    cfg = TesterConfig(cash=100_000.0, taker_fee=0.001)
    kn = _tester_kernel_report(k=2, rebalance_every=1, config=cfg)
    # equity_ts is only populated by the kernel wrapper — not by MultiSymbolRunner by default
    assert kn.equity_ts is not None and len(kn.equity_ts) > 0
    # Kernel must actually trade (not just sit in cash)
    assert kn.final_equity != pytest.approx(100_000.0, abs=1.0)


def test_event_cross_sectional_strategy_uses_event_path():
    """A CrossSectionalStrategy (EVENT — has score/weights, NOT target_weights) must route to the
    event engine, NOT the kernel. It is the closest-named class to CrossSectionalSignalStrategy, so
    it is the likeliest isinstance false-positive — and if it were mis-routed to the kernel, .run()
    would raise (CrossSectionalStrategy has no target_weights / signal-style .run)."""
    class _MomEvent(CrossSectionalStrategy):
        k = 2
        rebalance_every = 1
        lookback = 3

        def score(self, symbol, history):
            if len(history) <= self.lookback:
                return None
            return history[-1] / history[-1 - self.lookback] - 1.0

    report = MultiSymbolStrategyTester(_bars_by_symbol(), TesterConfig(cash=100_000.0)).run(lambda: _MomEvent())
    assert isinstance(report, TesterReport) and report.final_equity > 0

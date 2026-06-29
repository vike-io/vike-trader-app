"""Hand-run sweep wall-clock benchmark + scalar-vs-Account equity microbench (S6).

NOT a pytest gate — timing is environment-dependent and would bloat CI.
Run manually:
    PYTHONPATH=src python -m tests.perf.bench_sweep
or on Windows:
    $env:PYTHONPATH="src"; python -m tests.perf.bench_sweep

Prints:
  sweep median      <N>s  (median of 5 timed runs of a ~196-combo MA-cross grid over 50k bars)
  scalar equity_now  <N>ns/call
  Account.equity_all <N>ns/call
  ratio              <N>x  (how much more expensive the Account dict-walk is)

Design:
- MaCross is a GENERAL (non-Signal, non-vectorized) strategy that uses a rolling window of closes,
  so it exercises the real SingleSymbolEngine.run() event loop (not the fastsim njit detour).
- _synthetic_bars(50_000): deterministic price series (no RNG, no I/O) that trends enough to
  produce MA-crossover trades across most parameter combos.
- _grid(): {"fast": range(2,16), "slow": range(16,30)} = 14*14 = 196 combos (~200 per spec).
- bench_sweep(): 1 warmup + 5 timed calls via the real StrategyTester.optimize() path; prints median.
- bench_equity_read(): 2_000_000 iterations of each; prints ns/call and ratio.

The microbench quantifies the per-call cost that justifies the S6 STOP decision:
routing the per-bar backtest ledger through Account.equity_all() would impose the measured Nx
overhead vs the scalar cash+pos*price*mult read — for zero benefit (parity proven by C2 + S3).
"""

from __future__ import annotations

import math
import statistics
import time

from vike_trader_app.core.model import Bar
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy
from vike_trader_app.exec.accounting import Account
from vike_trader_app.exec.events import FillEvent
from vike_trader_app.tester.config import TesterConfig
from vike_trader_app.tester.strategy_tester import StrategyTester


# ---------------------------------------------------------------------------
# Deterministic synthetic price series — no RNG, no I/O
# ---------------------------------------------------------------------------

def _synthetic_bars(n: int = 50_000) -> list[Bar]:
    """50k bars: slow sine trend + micro-oscillation, so MA-cross strategies actually trade."""
    closes = [100.0 + 20.0 * math.sin(i / 250.0) + 0.001 * i for i in range(n)]
    bars: list[Bar] = []
    for i, c in enumerate(closes):
        prev = closes[i - 1] if i > 0 else c
        lo = min(prev, c) * 0.999
        hi = max(prev, c) * 1.001
        bars.append(Bar(ts=i * 60_000, open=prev, high=hi, low=lo, close=c, volume=1_000.0))
    return bars


# ---------------------------------------------------------------------------
# MA-cross strategy (GENERAL — NOT Signal/vectorized; exercises real event loop)
# ---------------------------------------------------------------------------

class MaCross(Strategy):
    """Simple moving-average crossover. Parameters injected via ctor kwargs by the optimizer."""

    def __init__(self, fast: int = 5, slow: int = 20):
        self.fast = fast
        self.slow = slow
        self._closes: list[float] = []

    def on_bar(self, bar: Bar) -> None:
        self._closes.append(bar.close)
        if len(self._closes) < self.slow:
            return
        fast_ma = sum(self._closes[-self.fast:]) / self.fast
        slow_ma = sum(self._closes[-self.slow:]) / self.slow
        pos = self.position.size
        if fast_ma > slow_ma and pos <= 0.0:
            if pos < 0.0:
                self.close()
            self.buy(1.0)
        elif fast_ma < slow_ma and pos >= 0.0:
            if pos > 0.0:
                self.close()


# ---------------------------------------------------------------------------
# Parameter grid: ~196 combos
# ---------------------------------------------------------------------------

def _grid() -> dict:
    return {"fast": list(range(2, 16)), "slow": list(range(16, 30))}


# ---------------------------------------------------------------------------
# Sweep wall-clock benchmark
# ---------------------------------------------------------------------------

def bench_sweep(bars: list[Bar] | None = None) -> float:
    """Time the optimizer sweep 5 times (+ 1 warmup); return the median wall-clock in seconds."""
    if bars is None:
        bars = _synthetic_bars(50_000)

    cfg = TesterConfig(taker_fee=0.001, cash=10_000.0)
    grid = _grid()

    def make(**kw: int) -> MaCross:
        return MaCross(**kw)

    def _run() -> None:
        st = StrategyTester(make(), bars, cfg)
        st.optimize(make, grid, criterion="total_return", method="grid")

    # warmup
    _run()

    timings: list[float] = []
    for _ in range(5):
        t0 = time.perf_counter()
        _run()
        timings.append(time.perf_counter() - t0)

    return statistics.median(timings)


# ---------------------------------------------------------------------------
# Equity-read microbench: scalar vs Account.equity_all()
# ---------------------------------------------------------------------------

def bench_equity_read(iters: int = 2_000_000) -> tuple[float, float]:
    """Return (scalar_ns_per_call, account_ns_per_call).

    Scalar: mimics SingleSymbolEngine.equity_now() — cash + size*price*mult (one expression).
    Account: a 1-position Account after one fill + set_mark, calling equity_all(seed).
    """
    # --- scalar setup ---
    cash = 10_000.0
    size = 1.0
    price = 105.0
    mult = 2.0

    t0 = time.perf_counter()
    for _ in range(iters):
        _ = cash + size * price * mult  # noqa: F841
    scalar_ns = (time.perf_counter() - t0) * 1e9 / iters

    # --- Account setup: one BUY fill, then a mark ---
    acc = Account(multiplier=mult, venue="sim")
    fill = FillEvent(
        trade_id="t1",
        client_order_id="c1",
        venue="sim",
        symbol="X",
        side=1,
        last_qty=size,
        last_px=100.0,
        commission=0.001 * size * 100.0,
        liquidity_side="taker",
        ts=0,
    )
    acc.apply_fill(fill)
    acc.set_mark("sim", "X", price)

    t0 = time.perf_counter()
    for _ in range(iters):
        _ = acc.equity_all(seed=cash)  # noqa: F841
    account_ns = (time.perf_counter() - t0) * 1e9 / iters

    return scalar_ns, account_ns


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("bench_sweep.py — S6 wall-clock + microbench")
    print(f"  generating 50k synthetic bars …", flush=True)
    bars = _synthetic_bars(50_000)
    n_combos = len(_grid()["fast"]) * len(_grid()["slow"])
    print(f"  grid: {n_combos} combos  (fast=range(2,16) × slow=range(16,30))")

    print("  running sweep bench (1 warmup + 5 timed) …", flush=True)
    median_s = bench_sweep(bars)
    print(f"\nsweep median        {median_s:.3f}s  ({n_combos} combos × 50k bars, median of 5)")

    print("  running equity-read microbench (2M iters each) …", flush=True)
    scalar_ns, account_ns = bench_equity_read(iters=2_000_000)
    ratio = account_ns / scalar_ns
    print(f"scalar equity_now   {scalar_ns:.1f} ns/call")
    print(f"Account.equity_all  {account_ns:.1f} ns/call")
    print(f"ratio               {ratio:.1f}x  (Account is {ratio:.1f}x more expensive per call)")
    print()
    print("S6 VERDICT: STOP at S5.")
    print("  The sweep engine carries no Account (deterministic guard: test_perf_invariant.py).")
    print(f"  Routing through Account would add ~{ratio:.0f}x overhead per equity read at ~2 reads/bar")
    print(f"  over {50_000} bars x {n_combos} combos — zero benefit (parity proven by C2 + S3).")

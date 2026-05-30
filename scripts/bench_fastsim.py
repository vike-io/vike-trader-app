"""Benchmark the compiled fast_backtest vs the Python BacktestEngine.

Reported, not asserted (timings are machine-dependent). Run:
    uv run python scripts/bench_fastsim.py
"""

import time

import numpy as np

from vike_trader_app.core.engine import BacktestEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.strategy import Strategy


class _Alt(Strategy):
    """Alternate in/out every bar -> ~N order fills, the worst case for the Python loop."""

    def on_bar(self, bar):  # noqa: ARG002
        if self.position.size == 0.0:
            self.buy(1.0)
        else:
            self.close()


def main(n=1_000_000):
    rng = np.random.default_rng(0)
    closes = 100 + np.cumsum(rng.normal(0, 1, n))
    opens = np.concatenate([[closes[0]], closes[:-1]])
    ts = np.arange(n, dtype=np.int64) * 60_000

    bars = [Bar(ts=int(ts[i]), open=float(opens[i]), high=float(closes[i]) + 1,
                low=float(closes[i]) - 1, close=float(closes[i])) for i in range(n)]
    t0 = time.perf_counter()
    BacktestEngine(bars, _Alt()).run()
    py = time.perf_counter() - t0

    from vike_trader_app.core.fastsim import fast_backtest

    entries = np.zeros(n, np.bool_)
    exits = np.zeros(n, np.bool_)
    entries[::2] = True
    exits[1::2] = True
    size = np.ones(n)
    side = np.ones(n, np.int64)
    funding = np.zeros(n)
    fast_backtest(opens, closes + 1, closes - 1, closes, funding, ts,
                  entries, exits, size, side)  # warm up JIT (compile)
    t0 = time.perf_counter()
    fast_backtest(opens, closes + 1, closes - 1, closes, funding, ts,
                  entries, exits, size, side)
    fast = time.perf_counter() - t0

    print(f"n={n:,}  python={py:.3f}s  fast={fast:.4f}s  speedup={py / fast:.0f}x")


if __name__ == "__main__":
    main()

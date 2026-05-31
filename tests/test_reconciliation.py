import numpy as np
import pytest

from vike_trader_app.core.engine import BacktestEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.strategy import Strategy
from vike_trader_app.core.fastsim import fast_backtest


class _WarmupArrayStrategy(Strategy):
    """Signal-array oracle WITH a warm-up gate (mirrors the kernel's warm_up)."""

    WARMUP = 5

    def __init__(self, entries, exits, size, side):
        super().__init__()
        self.entries, self.exits, self.size, self.side = entries, exits, size, side

    def on_bar(self, bar):  # noqa: ARG002
        i = self.index
        pos = self.position.size
        did_exit = False
        if self.exits[i] and pos != 0.0:
            self.close()
            did_exit = True
        if self.entries[i] and (pos == 0.0 or did_exit):
            (self.buy if self.side[i] > 0 else self.sell)(self.size[i])


def test_kernel_warm_up_matches_engine_WARMUP():
    n = 40
    rng = np.random.default_rng(99)
    closes = (100 + np.cumsum(rng.normal(0, 1, n))).tolist()
    opens = [closes[0]] + closes[:-1]
    highs = [max(o, c) + 0.5 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 0.5 for o, c in zip(opens, closes)]
    ts = list(range(0, n * 60_000, 60_000))
    entries = [i % 4 == 0 for i in range(n)]   # would fire at i=0 (before warm-up) without the gate
    exits = [i % 4 == 2 for i in range(n)]
    size = [1.0] * n
    side = [1] * n

    bars = [Bar(ts=ts[i], open=opens[i], high=highs[i], low=lows[i], close=closes[i]) for i in range(n)]
    eng = BacktestEngine(bars, _WarmupArrayStrategy(entries, exits, size, side), fee_rate=0.001)
    expected = eng.run()  # engine skips on_bar for i < 5

    got = fast_backtest(
        np.asarray(opens, float), np.asarray(highs, float), np.asarray(lows, float),
        np.asarray(closes, float), np.zeros(n), np.asarray(ts, np.int64),
        np.asarray(entries, np.bool_), np.asarray(exits, np.bool_),
        np.asarray(size, float), np.asarray(side, np.int64),
        taker_fee=0.001, warm_up=5,
    )
    assert got["equity_curve"] == pytest.approx(expected.equity_curve, rel=1e-9, abs=1e-9)
    assert got["n_trades"] == len(expected.trades)

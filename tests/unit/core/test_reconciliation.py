import numpy as np
import pytest

from vike_trader_app.core.engine import SingleSymbolEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy
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
    eng = SingleSymbolEngine(bars, _WarmupArrayStrategy(entries, exits, size, side), fee_rate=0.001)
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


def test_engine_equals_fastsim_canonical_guarantee():
    """§2.1: the event engine and the compiled kernel are ONE cost model. Long/flat + short + funding."""
    n = 60
    rng = np.random.default_rng(2024)
    closes = (100 + np.cumsum(rng.normal(0, 1, n))).tolist()
    opens = [closes[0]] + closes[:-1]
    highs = [max(o, c) + 0.5 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 0.5 for o, c in zip(opens, closes)]
    ts = list(range(0, n * 60_000, 60_000))
    funding = [0.0001 if i % 8 == 0 else 0.0 for i in range(n)]
    entries = [i % 6 == 0 for i in range(n)]
    exits = [i % 6 == 3 for i in range(n)]
    size = [2.0] * n
    side = [1 if (i // 6) % 2 == 0 else -1 for i in range(n)]   # alternate long/short blocks

    bars = [Bar(ts=ts[i], open=opens[i], high=highs[i], low=lows[i], close=closes[i], funding=funding[i])
            for i in range(n)]

    class _Oracle(Strategy):
        def on_bar(self, bar):  # noqa: ARG002
            i = self.index
            pos = self.position.size
            did_exit = False
            if exits[i] and pos != 0.0:
                self.close(); did_exit = True
            if entries[i] and (pos == 0.0 or did_exit):
                (self.buy if side[i] > 0 else self.sell)(size[i])

    expected = SingleSymbolEngine(bars, _Oracle(), taker_fee=0.001, slippage=0.0005).run()
    got = fast_backtest(
        np.asarray(opens, float), np.asarray(highs, float), np.asarray(lows, float),
        np.asarray(closes, float), np.asarray(funding, float), np.asarray(ts, np.int64),
        np.asarray(entries, np.bool_), np.asarray(exits, np.bool_),
        np.asarray(size, float), np.asarray(side, np.int64),
        taker_fee=0.001, slippage=0.0005,
    )
    assert got["equity_curve"] == pytest.approx(expected.equity_curve, rel=1e-9, abs=1e-9)
    assert got["final_equity"] == pytest.approx(expected.final_equity, rel=1e-9, abs=1e-9)
    assert got["n_trades"] == len(expected.trades)


def test_broker_sim_primitives_match_canonical_formulas():
    """Pin broker_sim's primitives to their literal formulas (engine<->kernel drift is covered by the parity tests above)."""
    from vike_trader_app.core.broker_sim import adverse_fill_price, fee, funding_charge
    # verify broker_sim returns the canonical formulas (the same arithmetic the engine + kernel use).
    raw, side_sign, slip, sz, rate, mult, pos, close, frate = 100.0, 1, 0.0005, 2.0, 0.001, 5.0, 3.0, 101.0, 0.0001
    fill = raw * (1.0 + side_sign * slip)
    assert fill == adverse_fill_price(raw, side_sign, slip)
    assert sz * fill * rate * mult == fee(sz, fill, rate, mult)
    assert pos * close * frate * mult == funding_charge(pos, close, frate, mult)

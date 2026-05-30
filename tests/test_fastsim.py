"""Compiled fast-path kernel: parity with the Python BacktestEngine + numba/numpy equivalence."""

import numpy as np
import pytest

from vike_trader_app.core.engine import BacktestEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.strategy import Strategy
from vike_trader_app.core.fastsim import fast_backtest


class _ArrayStrategy(Strategy):
    """on_bar strategy driven by precomputed signal arrays — the parity oracle.

    Mirrors the fast kernel's decision rule exactly: exit goes flat, entry opens
    when flat (or immediately after an exit on the same bar = a flip). No pyramiding.
    """

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


def _bars(opens, highs, lows, closes, ts, funding=None):
    n = len(closes)
    fund = funding if funding is not None else [None] * n
    return [Bar(ts=ts[i], open=opens[i], high=highs[i], low=lows[i],
                close=closes[i], funding=fund[i]) for i in range(n)]


def _engine_result(opens, highs, lows, closes, ts, entries, exits, size, side,
                   *, taker_fee=0.0, slippage=0.0, init_cash=10_000.0):
    bars = _bars(opens, highs, lows, closes, ts)
    strat = _ArrayStrategy(entries, exits, size, side)
    eng = BacktestEngine(bars, strat, fee_rate=taker_fee, slippage=slippage, cash=init_cash)
    return eng.run()


def _arrays(opens, highs, lows, closes, ts, entries, exits, size, side, funding=None):
    n = len(closes)
    return dict(
        opens=np.asarray(opens, float), highs=np.asarray(highs, float),
        lows=np.asarray(lows, float), closes=np.asarray(closes, float),
        ts=np.asarray(ts, np.int64),
        funding=np.asarray(funding if funding is not None else [0.0] * n, float),
        entries=np.asarray(entries, np.bool_), exits=np.asarray(exits, np.bool_),
        size=np.asarray(size, float), side=np.asarray(side, np.int64),
    )


def test_long_only_matches_engine():
    n = 50
    rng = np.random.default_rng(0)
    closes = (100 + np.cumsum(rng.normal(0, 1, n))).tolist()
    opens = ([closes[0]] + closes[:-1])           # open[i] = prior close (simple, deterministic)
    highs = [max(o, c) + 0.5 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 0.5 for o, c in zip(opens, closes)]
    ts = list(range(0, n * 60_000, 60_000))
    entries = [i % 10 == 0 for i in range(n)]     # enter every 10 bars when flat
    exits = [i % 10 == 5 for i in range(n)]       # exit 5 bars later
    size = [1.0] * n
    side = [1] * n

    expected = _engine_result(opens, highs, lows, closes, ts, entries, exits, size, side,
                              taker_fee=0.001, slippage=0.0005)
    got = fast_backtest(**_arrays(opens, highs, lows, closes, ts, entries, exits, size, side),
                        taker_fee=0.001, slippage=0.0005)

    assert got["equity_curve"] == pytest.approx(expected.equity_curve, rel=1e-9, abs=1e-9)
    assert got["final_equity"] == pytest.approx(expected.final_equity, rel=1e-9, abs=1e-9)
    assert got["n_trades"] == len(expected.trades)
    for g, e in zip(got["trades"], expected.trades):
        assert g.entry_price == pytest.approx(e.entry_price, rel=1e-9)
        assert g.exit_price == pytest.approx(e.exit_price, rel=1e-9)
        assert g.pnl == pytest.approx(e.pnl, rel=1e-9)
        assert g.fees == pytest.approx(e.fees, rel=1e-9)


def test_short_side_matches_engine():
    n = 40
    rng = np.random.default_rng(7)
    closes = (100 + np.cumsum(rng.normal(0, 1, n))).tolist()
    opens = [closes[0]] + closes[:-1]
    highs = [max(o, c) + 0.5 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 0.5 for o, c in zip(opens, closes)]
    ts = list(range(0, n * 60_000, 60_000))
    entries = [i % 8 == 0 for i in range(n)]
    exits = [i % 8 == 4 for i in range(n)]
    size = [2.0] * n
    side = [-1] * n                                # short entries

    expected = _engine_result(opens, highs, lows, closes, ts, entries, exits, size, side,
                              taker_fee=0.001)
    got = fast_backtest(**_arrays(opens, highs, lows, closes, ts, entries, exits, size, side),
                        taker_fee=0.001)
    assert got["equity_curve"] == pytest.approx(expected.equity_curve, rel=1e-9, abs=1e-9)
    assert got["n_trades"] == len(expected.trades)
    for g, e in zip(got["trades"], expected.trades):
        assert g.pnl == pytest.approx(e.pnl, rel=1e-9)   # shorts profit when price falls


def test_flip_long_to_short_matches_engine():
    n = 12
    closes = [100, 102, 104, 103, 101, 99, 100, 101, 102, 103, 104, 105]
    opens = [closes[0]] + closes[:-1]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    ts = list(range(0, n * 60_000, 60_000))
    # bar 1: go long. bar 5: flip (exit long + enter short). bar 9: flip back to long.
    entries = [False, True, False, False, False, True, False, False, False, True, False, False]
    exits = [False, False, False, False, False, True, False, False, False, True, False, False]
    size = [1.0] * n
    side = [1, 1, 1, 1, 1, -1, -1, -1, -1, 1, 1, 1]

    expected = _engine_result(opens, highs, lows, closes, ts, entries, exits, size, side,
                              taker_fee=0.002, slippage=0.001)
    got = fast_backtest(**_arrays(opens, highs, lows, closes, ts, entries, exits, size, side),
                        taker_fee=0.002, slippage=0.001)
    assert got["equity_curve"] == pytest.approx(expected.equity_curve, rel=1e-9, abs=1e-9)
    assert got["n_trades"] == len(expected.trades)

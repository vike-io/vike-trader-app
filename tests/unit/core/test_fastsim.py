"""Compiled fast-path kernel: parity with the Python SingleSymbolEngine + numba/numpy equivalence."""

import numpy as np
import pytest

from vike_trader_app.core.engine import SingleSymbolEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy
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
    eng = SingleSymbolEngine(bars, strat, fee_rate=taker_fee, slippage=slippage, cash=init_cash)
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
    for gtr, etr in zip(got["trades"], expected.trades):
        assert gtr.pnl == pytest.approx(etr.pnl, rel=1e-9)
        assert gtr.fees == pytest.approx(etr.fees, rel=1e-9)


def test_funding_matches_engine():
    n = 30
    rng = np.random.default_rng(3)
    closes = (100 + np.cumsum(rng.normal(0, 0.8, n))).tolist()
    opens = [closes[0]] + closes[:-1]
    highs = [max(o, c) + 0.5 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 0.5 for o, c in zip(opens, closes)]
    ts = list(range(0, n * 60_000, 60_000))
    funding = [0.0001 if i % 3 == 0 else 0.0 for i in range(n)]
    entries = [i == 2 for i in range(n)]
    exits = [i == 25 for i in range(n)]
    size = [3.0] * n
    side = [1] * n

    # funding-aware engine oracle (Bars carry the funding rate)
    bars = _bars(opens, highs, lows, closes, ts, funding=funding)
    eng = SingleSymbolEngine(bars, _ArrayStrategy(entries, exits, size, side), fee_rate=0.0)
    expected = eng.run()

    got = fast_backtest(**_arrays(opens, highs, lows, closes, ts, entries, exits, size, side,
                                  funding=funding))
    assert got["equity_curve"] == pytest.approx(expected.equity_curve, rel=1e-9, abs=1e-9)
    assert got["final_equity"] == pytest.approx(expected.final_equity, rel=1e-9, abs=1e-9)


def test_numba_and_numpy_paths_agree():
    pytest.importorskip("numba")
    import vike_trader_app.core.fastsim as fs

    n = 200
    rng = np.random.default_rng(11)
    closes = (100 + np.cumsum(rng.normal(0, 1, n))).tolist()
    opens = [closes[0]] + closes[:-1]
    highs = [max(o, c) + 0.5 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 0.5 for o, c in zip(opens, closes)]
    ts = list(range(0, n * 60_000, 60_000))
    entries = [i % 9 == 0 for i in range(n)]
    exits = [i % 9 == 4 for i in range(n)]
    size = [1.5] * n
    side = [1 if (i // 9) % 2 == 0 else -1 for i in range(n)]
    kw = _arrays(opens, highs, lows, closes, ts, entries, exits, size, side)

    compiled = fast_backtest(**kw, taker_fee=0.001, slippage=0.0005)

    # force the pure-python path by calling the kernel's undecorated __wrapped__
    py_kernel = fs._sim_kernel.py_func if hasattr(fs._sim_kernel, "py_func") else fs._sim_kernel
    n_bars = kw["closes"].shape[0]
    cashflow_zeros = np.zeros(n_bars, np.float64)
    res = py_kernel(kw["opens"], kw["highs"], kw["lows"], kw["closes"], kw["funding"],
                    cashflow_zeros, kw["ts"],
                    kw["entries"], kw["exits"], kw["size"], kw["side"],
                    0.001, 0.0005, 10_000.0,
                    1.0, 0.0, 0.0, 0, 0)
    assert res[0].tolist() == pytest.approx(compiled["equity_curve"], rel=1e-9, abs=1e-9)
    assert int(res[1]) == compiled["n_trades"]


def test_build_trades_false_skips_trade_objects():
    n = 30
    rng = np.random.default_rng(5)
    closes = (100 + np.cumsum(rng.normal(0, 1, n))).tolist()
    opens = [closes[0]] + closes[:-1]
    highs = [max(o, c) + 0.5 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 0.5 for o, c in zip(opens, closes)]
    ts = list(range(0, n * 60_000, 60_000))
    entries = [i % 6 == 0 for i in range(n)]
    exits = [i % 6 == 3 for i in range(n)]
    size = [1.0] * n
    side = [1] * n
    kw = _arrays(opens, highs, lows, closes, ts, entries, exits, size, side)
    full = fast_backtest(**kw, taker_fee=0.001)
    lean = fast_backtest(**kw, taker_fee=0.001, build_trades=False)
    assert lean["equity_curve"] == pytest.approx(full["equity_curve"], rel=1e-12, abs=1e-12)
    assert lean["final_equity"] == pytest.approx(full["final_equity"])
    assert lean["n_trades"] == full["n_trades"]
    assert lean["trades"] == []
    assert len(full["trades"]) == full["n_trades"]


def test_noop_njit_shim_supports_both_decorator_forms():
    # Guards the numba-absent fallback path: bare @njit and @njit(cache=True) must both work.
    from vike_trader_app.core.fastsim import _noop_njit

    @_noop_njit
    def f(x):
        return x + 1

    @_noop_njit(cache=True)
    def g(x):
        return x * 2

    assert f(1) == 2
    assert g(3) == 6


def test_multiplier_matches_engine():
    n = 30
    rng = np.random.default_rng(21)
    closes = (100 + np.cumsum(rng.normal(0, 1, n))).tolist()
    opens = [closes[0]] + closes[:-1]
    highs = [max(o, c) + 0.5 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 0.5 for o, c in zip(opens, closes)]
    ts = list(range(0, n * 60_000, 60_000))
    entries = [i % 7 == 0 for i in range(n)]
    exits = [i % 7 == 3 for i in range(n)]
    size = [2.0] * n
    side = [1] * n
    mult = 5.0

    bars = _bars(opens, highs, lows, closes, ts)
    eng = SingleSymbolEngine(bars, _ArrayStrategy(entries, exits, size, side),
                         fee_rate=0.001, slippage=0.0005, multiplier=mult)
    expected = eng.run()
    got = fast_backtest(**_arrays(opens, highs, lows, closes, ts, entries, exits, size, side),
                        taker_fee=0.001, slippage=0.0005, multiplier=mult)
    assert got["equity_curve"] == pytest.approx(expected.equity_curve, rel=1e-9, abs=1e-9)
    assert got["final_equity"] == pytest.approx(expected.final_equity, rel=1e-9, abs=1e-9)
    assert got["n_trades"] == len(expected.trades)
    for g, e in zip(got["trades"], expected.trades):
        assert g.pnl == pytest.approx(e.pnl, rel=1e-9)
        assert g.fees == pytest.approx(e.fees, rel=1e-9)


def test_cashflow_matches_engine():
    n = 25
    rng = np.random.default_rng(31)
    closes = (100 + np.cumsum(rng.normal(0, 1, n))).tolist()
    opens = [closes[0]] + closes[:-1]
    highs = [max(o, c) + 0.5 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 0.5 for o, c in zip(opens, closes)]
    ts = list(range(0, n * 60_000, 60_000))
    entries = [i == 1 for i in range(n)]
    exits = [i == 20 for i in range(n)]
    size = [1.0] * n
    side = [1] * n
    cashflow = [500.0 if i == 5 else (-200.0 if i == 12 else 0.0) for i in range(n)]

    bars = _bars(opens, highs, lows, closes, ts)
    eng = SingleSymbolEngine(bars, _ArrayStrategy(entries, exits, size, side), cashflows=cashflow)
    expected = eng.run()
    got = fast_backtest(**_arrays(opens, highs, lows, closes, ts, entries, exits, size, side),
                        cashflow=cashflow)
    assert got["equity_curve"] == pytest.approx(expected.equity_curve, rel=1e-9, abs=1e-9)
    assert got["final_equity"] == pytest.approx(expected.final_equity, rel=1e-9, abs=1e-9)


class _PercentEntryStrategy(Strategy):
    """Oracle for size_type='percent': on entry, target `pct*equity` notional at decision close."""

    def __init__(self, entries, exits, pct, side, mult):
        super().__init__()
        self.entries, self.exits, self.pct, self.side, self.mult = entries, exits, pct, side, mult

    def on_bar(self, bar):  # noqa: ARG002
        i = self.index
        pos = self.position.size
        did_exit = False
        if self.exits[i] and pos != 0.0:
            self.close()
            did_exit = True
        if self.entries[i] and (pos == 0.0 or did_exit):
            shares = self.pct[i] * self.equity / (bar.close * self.mult)
            (self.buy if self.side[i] > 0 else self.sell)(shares)


def test_size_type_percent_matches_engine():
    n = 24
    rng = np.random.default_rng(41)
    closes = (100 + np.cumsum(rng.normal(0, 1, n))).tolist()
    opens = [closes[0]] + closes[:-1]
    highs = [max(o, c) + 0.5 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 0.5 for o, c in zip(opens, closes)]
    ts = list(range(0, n * 60_000, 60_000))
    entries = [i % 6 == 0 for i in range(n)]
    exits = [i % 6 == 3 for i in range(n)]
    pct = [0.5] * n          # target 50% of equity notional per entry
    side = [1] * n

    bars = _bars(opens, highs, lows, closes, ts)
    eng = SingleSymbolEngine(bars, _PercentEntryStrategy(entries, exits, pct, side, 1.0), taker_fee=0.001)
    expected = eng.run()
    got = fast_backtest(**_arrays(opens, highs, lows, closes, ts, entries, exits, pct, side),
                        taker_fee=0.001, size_type="percent")
    assert got["equity_curve"] == pytest.approx(expected.equity_curve, rel=1e-9, abs=1e-9)
    assert got["n_trades"] == len(expected.trades)


class _ValueEntryStrategy(Strategy):
    """Oracle for size_type='value': on entry, target a fixed cash notional at decision close."""

    def __init__(self, entries, exits, value, side, mult):
        super().__init__()
        self.entries, self.exits, self.value, self.side, self.mult = entries, exits, value, side, mult

    def on_bar(self, bar):  # noqa: ARG002
        i = self.index
        pos = self.position.size
        did_exit = False
        if self.exits[i] and pos != 0.0:
            self.close()
            did_exit = True
        if self.entries[i] and (pos == 0.0 or did_exit):
            shares = self.value[i] / (bar.close * self.mult)
            (self.buy if self.side[i] > 0 else self.sell)(shares)


def test_size_type_value_matches_engine():
    n = 24
    rng = np.random.default_rng(42)
    closes = (100 + np.cumsum(rng.normal(0, 1, n))).tolist()
    opens = [closes[0]] + closes[:-1]
    highs = [max(o, c) + 0.5 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 0.5 for o, c in zip(opens, closes)]
    ts = list(range(0, n * 60_000, 60_000))
    entries = [i % 6 == 0 for i in range(n)]
    exits = [i % 6 == 3 for i in range(n)]
    value = [3000.0] * n      # target $3000 notional per entry
    side = [1] * n

    bars = _bars(opens, highs, lows, closes, ts)
    eng = SingleSymbolEngine(bars, _ValueEntryStrategy(entries, exits, value, side, 1.0), taker_fee=0.001)
    expected = eng.run()
    got = fast_backtest(**_arrays(opens, highs, lows, closes, ts, entries, exits, value, side),
                        taker_fee=0.001, size_type="value")
    assert got["equity_curve"] == pytest.approx(expected.equity_curve, rel=1e-9, abs=1e-9)
    assert got["n_trades"] == len(expected.trades)


def test_leverage_cap_matches_engine():
    n = 20
    rng = np.random.default_rng(51)
    closes = (100 + np.cumsum(rng.normal(0, 1, n))).tolist()
    opens = [closes[0]] + closes[:-1]
    highs = [max(o, c) + 0.5 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 0.5 for o, c in zip(opens, closes)]
    ts = list(range(0, n * 60_000, 60_000))
    entries = [i == 1 for i in range(n)]
    exits = [i == 18 for i in range(n)]
    size = [1000.0] * n   # huge target; must be capped by leverage
    side = [1] * n

    bars = _bars(opens, highs, lows, closes, ts)
    eng = SingleSymbolEngine(bars, _ArrayStrategy(entries, exits, size, side), leverage=2.0)
    expected = eng.run()
    got = fast_backtest(**_arrays(opens, highs, lows, closes, ts, entries, exits, size, side),
                        leverage=2.0)
    assert got["equity_curve"] == pytest.approx(expected.equity_curve, rel=1e-9, abs=1e-9)
    assert got["n_trades"] == len(expected.trades)
    for g, e in zip(got["trades"], expected.trades):
        assert g.size == pytest.approx(e.size, rel=1e-9)  # both capped to the same share count

    # leverage 2.0 on ~10_000 equity at price ~closes[1] -> capped notional 2*equity
    entry_price = opens[2]  # the bar-1 entry fills at bar-2 open
    assert expected.trades[0].size == pytest.approx(2.0 * 10_000.0 / entry_price, rel=0.05)


def test_leverage_flip_matches_engine():
    # long, flip to a short that exceeds the cap, price drops, then exit the short.
    # Without the pending-aware leverage cap, the engine fails to open the short -> diverges
    # in both the realized short trade and the equity curve.
    n = 9
    closes = [100.0, 100.0, 100.0, 100.0, 100.0, 90.0, 90.0, 90.0, 90.0]
    opens =  [100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 90.0, 90.0, 90.0]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    ts = list(range(0, n * 60_000, 60_000))
    entries = [i in (1, 4) for i in range(n)]   # long @1, flip @4
    exits = [i in (4, 7) for i in range(n)]      # bar 4: close long + open short; bar 7: exit short
    size = [1000.0] * n                           # huge target -> capped both times
    side = [1, 1, 1, 1, -1, -1, -1, -1, -1]       # short from bar 4

    bars = _bars(opens, highs, lows, closes, ts)
    eng = SingleSymbolEngine(bars, _ArrayStrategy(entries, exits, size, side), leverage=2.0)
    expected = eng.run()
    got = fast_backtest(**_arrays(opens, highs, lows, closes, ts, entries, exits, size, side),
                        leverage=2.0)
    assert got["equity_curve"] == pytest.approx(expected.equity_curve, rel=1e-9, abs=1e-9)
    assert got["n_trades"] == len(expected.trades)
    assert got["n_trades"] == 2   # long round-trip + short round-trip
    for g, e in zip(got["trades"], expected.trades):
        assert g.size == pytest.approx(e.size, rel=1e-9)
        assert g.pnl == pytest.approx(e.pnl, rel=1e-9)


def test_liquidation_matches_engine():
    # rising open then a deep crash low on bar 4 to trigger a long liquidation
    closes = [100.0, 100.0, 100.0, 100.0, 60.0, 60.0, 60.0]
    opens = [100.0, 100.0, 100.0, 100.0, 95.0, 60.0, 60.0]
    highs = [c + 1 for c in closes]
    lows = [100.0, 100.0, 100.0, 100.0, 55.0, 59.0, 59.0]   # bar 4 low=55 forces liquidation
    n = len(closes)
    ts = list(range(0, n * 60_000, 60_000))
    entries = [i == 1 for i in range(n)]
    exits = [False] * n
    size = [50.0] * n   # 50 shares @100 = 5000 notional on 1000 cash -> 5x
    side = [1] * n

    bars = _bars(opens, highs, lows, closes, ts)
    eng = SingleSymbolEngine(bars, _ArrayStrategy(entries, exits, size, side),
                         cash=1_000.0, leverage=10.0, maint_margin=0.05)
    expected = eng.run()
    got = fast_backtest(**_arrays(opens, highs, lows, closes, ts, entries, exits, size, side),
                        init_cash=1_000.0, leverage=10.0, maint_margin=0.05)
    assert got["equity_curve"] == pytest.approx(expected.equity_curve, rel=1e-9, abs=1e-9)
    assert got["n_trades"] == len(expected.trades)
    assert got["n_trades"] == 1   # the forced liquidation is the only round-trip
    for g, e in zip(got["trades"], expected.trades):
        assert g.exit_price == pytest.approx(e.exit_price, rel=1e-9)
        assert g.pnl == pytest.approx(e.pnl, rel=1e-9)

"""Vectorized fast-path: numpy cumprod backtest + polars-accelerated SMA sweep."""

import pytest

from vike_trader_app.core.vectorized import sweep_sma_cross, vector_backtest


def test_vector_backtest_long_hold_return():
    # enter at bar 0 (held from bar 1), hold to the end on a +10%/+10% path
    closes = [100.0, 110.0, 121.0]
    out = vector_backtest(closes, entries=[True, False, False], exits=[False, False, False])
    assert out["total_return"] == pytest.approx(0.21)   # 1.1 * 1.1 - 1
    assert out["n_trades"] == 1
    assert len(out["equity_curve"]) == 3


def test_vector_backtest_no_signals_is_flat():
    out = vector_backtest([100, 101, 102, 103], entries=[False] * 4, exits=[False] * 4)
    assert out["total_return"] == pytest.approx(0.0)
    assert out["n_trades"] == 0


def test_vector_backtest_fees_reduce_return():
    closes = [100.0, 110.0, 121.0, 121.0]
    base = vector_backtest(closes, [True, False, False, False], [False, False, True, False], fee_rate=0.0)
    with_fee = vector_backtest(closes, [True, False, False, False], [False, False, True, False], fee_rate=0.01)
    assert with_fee["total_return"] < base["total_return"]
    assert base["n_trades"] == 1  # one round trip opened


def test_vector_backtest_no_lookahead_entry_acts_next_bar():
    # entry signal on bar 0 must NOT capture bar 0's own return (acts from bar 1)
    closes = [100.0, 200.0]  # +100% on bar 1
    out = vector_backtest(closes, entries=[True, False], exits=[False, False])
    # held from bar 1, so it DOES capture the 0->1 move -> +100%
    assert out["total_return"] == pytest.approx(1.0)
    # but a signal on the LAST bar captures nothing (no future bar)
    out2 = vector_backtest(closes, entries=[False, True], exits=[False, False])
    assert out2["total_return"] == pytest.approx(0.0)


def test_sweep_sma_cross_covers_grid_and_ranks():
    # deterministic rising series -> trend strategies profit
    closes = [100 + i + (i % 7) for i in range(300)]
    fasts, slows = [5, 10, 20], [50, 80]
    res = sweep_sma_cross(closes, fasts, slows, fee_rate=0.0)
    assert len(res) == len(fasts) * len(slows)          # one result per combo
    assert all("params" in r and "total_return" in r for r in res)
    assert res == sorted(res, key=lambda r: r["total_return"], reverse=True)  # ranked best-first


def test_numba_engine_matches_numpy_engine_exactly():
    pytest.importorskip("numba")
    import numpy as np

    closes = [100 + 9 * np.sin(i / 11) + (i % 6) for i in range(500)]
    fasts, slows = [5, 12, 25], [40, 70, 120]
    npy = sweep_sma_cross(closes, fasts, slows, fee_rate=0.001, engine="numpy")
    nmb = sweep_sma_cross(closes, fasts, slows, fee_rate=0.001, engine="numba")
    npy_by = {(r["params"]["fast"], r["params"]["slow"]): r for r in npy}
    nmb_by = {(r["params"]["fast"], r["params"]["slow"]): r for r in nmb}
    assert set(npy_by) == set(nmb_by)
    for key, a in npy_by.items():
        b = nmb_by[key]
        assert b["total_return"] == pytest.approx(a["total_return"], rel=1e-9, abs=1e-9)
        assert b["n_trades"] == a["n_trades"]

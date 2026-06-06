"""SignalStrategy: vectorized signal generation executed through the fast kernel."""

import numpy as np
import pytest

from vike_trader_app.core.signal_strategy import SignalStrategy


class _Buy10Sell20(SignalStrategy):
    """Enter long at bar 1, exit at bar 3 — trivial deterministic strategy."""

    def signals(self, data):
        n = len(data["close"])
        entries = np.array([i == 1 for i in range(n)], dtype=np.bool_)
        exits = np.array([i == 3 for i in range(n)], dtype=np.bool_)
        size = np.ones(n)
        side = np.ones(n, dtype=np.int64)
        return entries, exits, size, side


def test_signal_strategy_runs_and_trades():
    closes = [100.0, 100.0, 110.0, 121.0, 121.0]
    data = {
        "open": closes, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes],
        "close": closes, "ts": list(range(0, 5 * 60_000, 60_000)), "funding": [0.0] * 5,
    }
    out = _Buy10Sell20().run(data, taker_fee=0.0, init_cash=10_000.0)
    assert out["n_trades"] == 1
    # entry signal at bar 1 fills at open[2]=110.0; exit signal at bar 3 fills at open[4]=121.0
    assert out["trades"][0].pnl == pytest.approx(11.0)
    assert len(out["equity_curve"]) == 5

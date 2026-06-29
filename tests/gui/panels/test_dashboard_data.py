"""Qt-free data prep for the optimizer dashboard (4 charts + heatmap)."""

import pytest

from vike_trader_app.ui.dashboard_data import drawdown_curve, per_bar_pnl, return_histogram, sharpe_heatmap


def test_drawdown_curve_is_zero_until_pullback():
    dd = drawdown_curve([100.0, 110.0, 105.0, 120.0, 90.0])
    assert dd[0] == pytest.approx(0.0)
    assert dd[1] == pytest.approx(0.0)        # new high
    assert dd[2] == pytest.approx((105 - 110) / 110)
    assert dd[4] == pytest.approx((90 - 120) / 120)  # deepest


def test_per_bar_pnl_diffs_equity():
    assert per_bar_pnl([100.0, 102.0, 101.0]) == pytest.approx([2.0, -1.0])


def test_return_histogram_bins_and_counts():
    centers, counts = return_histogram([100.0, 110.0, 99.0, 108.9], bins=4)
    assert len(centers) == 4 and len(counts) == 4
    assert sum(counts) == 3  # n-1 per-bar returns


def test_sharpe_heatmap_shapes_to_grid():
    from vike_trader_app.core.model import Bar
    from vike_trader_app.core.strategy import SingleSymbolStrategy as Strategy

    bars = [Bar(ts=i * 60_000, open=100 + i, high=101 + i, low=99 + i, close=100 + i, volume=1.0) for i in range(12)]

    def make(fast, slow):  # noqa: ARG001
        class _S(Strategy):
            def on_bar(self, bar):
                if self.index == 0:
                    self.buy(fast)

        return _S()

    xs, ys, scores = sharpe_heatmap(bars, make, "fast", [0.1, 1.0], "slow", [10, 20, 30])
    assert xs == [0.1, 1.0]
    assert ys == [10, 20, 30]
    assert len(scores) == 3 and all(len(row) == 2 for row in scores)

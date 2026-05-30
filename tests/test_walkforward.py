"""Walk-forward optimization tests."""

from vike_trader_app.analysis.optimizer import grid_search
from vike_trader_app.analysis.walkforward import walk_forward_optimize
from vike_trader_app.core.model import Bar
from vike_trader_app.core.strategy import Strategy


def _rising(n=20):
    return [Bar(ts=i * 60_000, open=100 + i, high=101 + i, low=99 + i, close=100 + i, volume=1.0) for i in range(n)]


def _make(size):
    class _S(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.buy(size)

    return _S()


def test_walk_forward_optimize_picks_best_is_param_per_window():
    bars = _rising(20)
    grid = {"size": [0.1, 0.5, 1.0]}
    rep = walk_forward_optimize(bars, _make, grid, n_splits=4, score_fn=lambda r: r.final_equity)
    assert len(rep.windows) == 4
    # On a rising market the largest size maximizes IS final equity every window.
    assert all(w.best_params["size"] == 1.0 for w in rep.windows)
    # Each window's chosen params match grid_search on that exact train slice.
    for w in rep.windows:
        tr_s, tr_e = w.train_range
        top = grid_search(bars[tr_s:tr_e], _make, grid, score_fn=lambda r: r.final_equity)[0]
        assert w.best_params == top.params
    # Each window carries an OOS result.
    assert all(w.oos_result is not None for w in rep.windows)


def test_walk_forward_stitches_oos_curve_and_reports_positive_return():
    bars = _rising(20)
    rep = walk_forward_optimize(bars, _make, {"size": [1.0]}, n_splits=4, score_fn=lambda r: r.final_equity)
    # n_splits=4 over 20 bars -> 4 test windows of 4 bars each = 16 stitched points.
    assert len(rep.oos_equity_curve) == 16
    # Rising market, long position -> positive stitched OOS return.
    assert rep.oos_return > 0
    # Curve is monotonically non-decreasing in equity terms here (rising prices, held long).
    assert rep.oos_equity_curve[-1] >= rep.oos_equity_curve[0]

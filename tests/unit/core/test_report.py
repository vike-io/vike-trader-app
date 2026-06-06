"""End-to-end anti-overfit report over a small synthetic backtest."""

import math

from vike_trader_app.analysis.report import build_overfit_report
from vike_trader_app.core.model import Bar
from vike_trader_app.core.strategy import Strategy


def _wave_bars(n=240):
    bars = []
    price = 100.0
    for i in range(n):
        o = price
        price = 100 + 10 * math.sin(i / 12) + (i % 5) - 2
        c = price
        bars.append(
            Bar(ts=i * 60_000, open=o, high=max(o, c) + 1, low=min(o, c) - 1, close=c, volume=1.0)
        )
    return bars


def _make(threshold):
    class _S(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.buy(0.01)
            elif bar.close > 100 + threshold and self.position.size > 0:
                self.close()

    return _S()


def test_report_runs_and_has_verdict():
    report = build_overfit_report(_wave_bars(), _make, {"threshold": [2, 4, 6, 8]}, n_splits=4)
    assert report.n_trials == 4
    assert 0.0 <= report.pbo <= 1.0
    assert 0.0 <= report.deflated_sharpe <= 1.0
    assert report.verdict.level in {"Low", "Medium", "High"}
    assert "threshold" in report.best_params

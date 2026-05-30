"""Experiment recorder — log every backtest/optimization trial to SQLite.

Wires the engine's ``Result`` into the ``data.store`` run ledger so the count of
configurations tried is *recorded*, not guessed. That count is exactly the input
the deflated Sharpe ratio needs to correct for multiple testing — turning a
reporting feature into a moat-strengthener.
"""

import math

from ..data.store import RunRecord, Store
from . import metrics


def _finite(x: float, cap: float = 1e9) -> float:
    """Clamp non-finite values (e.g. inf profit factor) so SQLite stores a real number."""
    if math.isinf(x) or math.isnan(x):
        return cap if x > 0 else (-cap if x < 0 else 0.0)
    return x


class ExperimentRecorder:
    """Records each run's params + headline metrics; exposes the trial count for DSR."""

    def __init__(self, store: Store):
        self.store = store

    def record(self, *, symbol, interval, strategy, params, result, ts, start_ts=0, end_ts=0, n_bars=0) -> int:
        """Persist one run and return its id."""
        eq = result.equity_curve
        rec = RunRecord(
            ts=ts,
            symbol=symbol,
            interval=interval,
            strategy=strategy,
            start_ts=start_ts,
            end_ts=end_ts,
            n_bars=n_bars or len(eq),
            net_return=metrics.total_return(eq),
            final_equity=result.final_equity,
            trades=len(result.trades),
            win_rate=metrics.win_rate(result.trades),
            profit_factor=_finite(metrics.profit_factor(result.trades)),
            max_drawdown=metrics.max_drawdown(eq),
            sharpe=metrics.sharpe(eq),
            params=params,
        )
        return self.store.save_run(rec)

    def n_trials(self, strategy: str | None = None) -> int:
        """Number of recorded runs (optionally for one strategy) — DSR's trial count."""
        runs = self.store.list_runs(limit=10**9)
        return len([r for r in runs if strategy is None or r.strategy == strategy])

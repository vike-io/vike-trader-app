"""Forward (paper) testing — drive the backtest engine live, one closed bar at a time.

A ``ForwardTester`` wraps a ``BacktestEngine`` and feeds it each newly-closed live bar via
``engine.step`` — the *same* fill path (next-open, slippage, maker/taker, funding) the
backtest uses. So a ``Strategy`` runs **unchanged** backtest↔forward; the only difference is
where the bars come from (a live feed instead of a historical list).

**Paper only** — no broker, no real orders (locked design decision). Each received bar is
streamed to the SQLite ``Store`` so a run survives closing the app and can be ``resume``-d
(re-seed warm-up history, then re-apply the stored bars to reach the same state).

Single-timeframe in this version; multi-timeframe forward (``self.bars(tf)`` live) is a
follow-up — see docs/handoff.md.
"""

from .engine import BacktestEngine, Result


def pump(feed, tester) -> list:
    """Pull this round's newly-closed bars from ``feed`` into ``tester``; return them.

    The Qt-free seam a GUI timer/thread calls each tick: ``feed`` exposes ``poll_once()``
    (PollingBarFeed), ``tester`` is a ``ForwardTester``. Returns the bars processed so the
    caller can repaint only when something changed.
    """
    processed = []
    for bar in feed.poll_once():
        tester.on_bar_live(bar)
        processed.append(bar)
    return processed


class ForwardTester:
    """Paper forward-test loop. Feed closed bars with ``on_bar_live``; read ``result()``."""

    def __init__(
        self,
        *,
        symbol: str,
        interval: str,
        strategy,
        cash: float = 10_000.0,
        fee_rate: float = 0.0,
        slippage: float = 0.0,
        maker_fee: float | None = None,
        taker_fee: float | None = None,
        seed_bars=None,
        timeframes=None,
        store=None,
        on_step=None,
        created_ts: int = 0,
        _persist: bool = True,
    ) -> None:
        self.symbol = symbol
        self.interval = interval
        self.on_step = on_step
        self.store = store
        self._persist = _persist
        self.run_id: int | None = None

        seed = list(seed_bars or [])
        self.engine = BacktestEngine(
            seed, strategy, fee_rate=fee_rate, cash=cash, timeframes=timeframes,
            slippage=slippage, maker_fee=maker_fee, taker_fee=taker_fee,
        )
        # Warm up strategy/indicator state on the seed without recording a live curve.
        self._i = 0
        for bar in seed:
            self.engine.step(bar, self._i)
            self._i += 1

        self.equity_curve: list[float] = []

        if store is not None and _persist:
            self.run_id = store.create_forward_run(
                symbol=symbol, interval=interval, strategy=type(strategy).__name__,
                cash=cash, fee_rate=fee_rate, params={}, created_ts=created_ts,
            )

    def on_bar_live(self, bar) -> float:
        """Process one newly-closed live bar; return equity after it."""
        self.engine.add_live_bar(bar)  # history + higher-TF refresh, before stepping
        eq = self.engine.step(bar, self._i)
        self._i += 1
        self.equity_curve.append(eq)
        if self.store is not None and self._persist and self.run_id is not None:
            self.store.append_forward_bar(self.run_id, bar)
        if self.on_step is not None:
            self.on_step(bar, eq)
        return eq

    def result(self) -> Result:
        """A ``Result`` over the live portion — same shape as a backtest, so GUI/tearsheets reuse."""
        return Result(self.engine.trades, self.equity_curve, self.engine.equity_now())

    def stop(self) -> None:
        if self.store is not None and self.run_id is not None:
            self.store.set_forward_status(self.run_id, "stopped")

    @classmethod
    def resume(cls, store, run_id: int, strategy, seed_bars=None) -> "ForwardTester":
        """Rebuild a forward run from the store: re-seed warm-up, replay stored bars.

        ``strategy`` must be a fresh instance of the same class; pass the same ``seed_bars``
        the original run warmed up on for an exact reconstruction of indicator state.
        """
        rec = next(r for r in store.list_forward_runs(limit=10**9) if r.id == run_id)
        ft = cls(
            symbol=rec.symbol, interval=rec.interval, strategy=strategy,
            cash=rec.cash, fee_rate=rec.fee_rate, seed_bars=seed_bars,
            store=store, _persist=False,  # reuse the existing run; don't re-write bars
        )
        ft.run_id = run_id
        for bar in store.forward_bars(run_id):
            ft.on_bar_live(bar)
        ft._persist = True  # new live bars from here on are persisted again
        return ft

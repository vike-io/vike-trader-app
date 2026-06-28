"""The Strategy API — the stable contract users write against.

Subclass ``Strategy`` and implement ``on_bar``. Place orders with ``buy``/``sell``/
``close``; read state via ``position`` / ``equity`` / ``index``. The engine fills
market orders at the next bar's open (no look-ahead).
"""

import concurrent.futures
from typing import TYPE_CHECKING

from .model import Bar, Fill, Position

if TYPE_CHECKING:
    from .strategy_engine import StrategyEngine

_HISTORY_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None


def _history_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Lazy shared pool for Strategy.history_async (daemon workers; reads are thread-safe, #259)."""
    global _HISTORY_EXECUTOR
    if _HISTORY_EXECUTOR is None:
        _HISTORY_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="history")
    return _HISTORY_EXECUTOR


class Strategy:
    """Base strategy. The engine injects itself and updates ``index`` each bar.

    Multi-timeframe: pass ``timeframes=["1h", "4h"]`` to ``BacktestEngine``; then read
    completed higher-TF candles with ``self.bars("1h")`` (look-ahead-safe) and the
    still-forming candle with ``self.forming("1h")`` (for replay-style logic).
    """

    #: Optional optimizable parameters, e.g. ``{"fast": [5, 10], "slow": [20, 30]}``.
    PARAM_GRID: dict = {}

    #: Bars to skip before ``on_bar`` fires — set to your longest indicator lookback
    #: so it never acts on NaN.
    WARMUP: int = 0

    _engine: "StrategyEngine | None"

    def __init__(self) -> None:
        self._engine = None  # set by the engine in run()
        self.index = 0  # current bar index

    @classmethod
    def make(cls, **params) -> "Strategy":
        """Build an instance, overriding the named parameters as attributes."""
        inst = cls()
        for key, value in params.items():
            setattr(inst, key, value)
        return inst

    # --- read-only state (delegated to the engine) ---
    @property
    def position(self) -> Position:
        return self._engine.position

    @property
    def equity(self) -> float:
        return self._engine.equity_now()

    @property
    def now(self) -> int:
        """Current simulation time (epoch ms): the ts of the bar/tick being processed."""
        return self._engine.now

    def history(self, symbol, interval, count=None, *, period=None, start=None, end=None):
        """Look-ahead-safe historical bars up to the current sim time, as a polars DataFrame.

        Pass EXACTLY ONE of: ``count`` (int, last N bars), ``period`` (timedelta, trailing window),
        or ``start``+``end`` (epoch-ms range). For the start/end form, either bound is optional:
        ``start`` defaults to the beginning of the series and ``end`` defaults to ``self.now``; both
        are look-ahead-clamped so the result never exceeds the current sim time. ``symbol`` may be a
        str (flat DataFrame) or a list of symbols (adds a ``symbol`` column; read in parallel). Reads
        the LOCAL cache only — download uncached symbols first (Data Manager). Columns: ts, open,
        high, low, close, volume.
        """
        return self._history_at(self.now, symbol, interval, count, period, start, end)

    def _history_at(self, now, symbol, interval, count=None, period=None, start=None, end=None):
        import polars as pl
        from ..data.parquet_source import bars_to_dataframe
        forms = (count is not None) + (period is not None) + (start is not None or end is not None)
        if forms != 1:
            raise ValueError("history(): pass exactly one of count, period, or start/end")
        eff_end = min(end, now) if end is not None else now      # look-ahead clamp
        if period is not None:
            lo = eff_end - int(period.total_seconds() * 1000)
        elif start is not None:
            lo = start
        else:
            lo = None                                            # count: read up to eff_end, tail later
        cat = getattr(self._engine, "catalog", None)
        if cat is None:
            from ..data.catalog import Catalog
            cat = Catalog()
        symbols = [symbol] if isinstance(symbol, str) else list(symbol)
        if len(symbols) == 1:
            bars = cat.query(symbols[0], interval, lo, eff_end)
            if count is not None:
                bars = bars[-count:]
            return bars_to_dataframe(bars)
        from ..data.parallel_read import read_series_many
        per = read_series_many(cat, symbols, interval, start=lo, end=eff_end)
        frames = []
        for s in symbols:
            b = per.get(s, [])
            if count is not None:
                b = b[-count:]
            frames.append(bars_to_dataframe(b).with_columns(pl.lit(s).alias("symbol")))
        return pl.concat(frames) if frames else bars_to_dataframe([])

    def history_async(self, symbol, interval, count=None, *, period=None, start=None, end=None):
        """Off-thread ``history()`` returning a ``concurrent.futures.Future[pl.DataFrame]``.

        The look-ahead clamp uses ``self.now`` captured AT CALL TIME, so the result is correct even
        though sim time advances before you consume it. Fire on one bar, read ``fut.result()`` /
        check ``fut.done()`` on a later bar — non-blocking. Safe because reads are thread-safe (#259).
        """
        now = self.now  # capture on the calling thread
        return _history_executor().submit(
            self._history_at, now, symbol, interval, count, period, start, end)

    def bars(self, tf: str):
        """Completed bars of higher timeframe ``tf`` visible now (no look-ahead)."""
        return self._engine.bars_for(tf)

    def forming(self, tf: str):
        """The in-progress (still-building) bar of higher timeframe ``tf``, or None."""
        return self._engine.forming_for(tf)

    # --- actions (resolved by the engine) ---
    def buy(self, size: float, weight: float = 0.0, stop: float | None = None) -> None:
        """Open/add a long. ``stop`` declares a protective stop price: it feeds the risk sizer and
        auto-arms a protective sell-stop that closes the position when breached (portfolio mode only;
        the single-symbol engine accepts and ignores ``stop`` to keep kernel parity)."""
        self._engine.submit(+1, size, weight=weight, stop=stop)

    def sell(self, size: float, weight: float = 0.0, stop: float | None = None) -> None:
        """Open/add a short. ``stop`` is the protective stop price (above entry) — see ``buy``."""
        self._engine.submit(-1, size, weight=weight, stop=stop)

    def close(self) -> None:
        self._engine.submit_close()

    # --- resting orders (fill on a future bar when the trigger is hit) ---
    def limit_buy(self, size: float, price: float, weight: float = 0.0) -> None:
        self._engine.submit_limit(+1, size, price, weight=weight)

    def limit_sell(self, size: float, price: float, weight: float = 0.0) -> None:
        self._engine.submit_limit(-1, size, price, weight=weight)

    def stop_buy(self, size: float, price: float, weight: float = 0.0) -> None:
        self._engine.submit_stop(+1, size, price, weight=weight)

    def stop_sell(self, size: float, price: float, weight: float = 0.0) -> None:
        self._engine.submit_stop(-1, size, price, weight=weight)

    def trailing_stop(self, size: float, trail: float, weight: float = 0.0) -> None:
        """Protective trailing sell-stop for a long: exits ``trail`` below the peak."""
        self._engine.submit_trailing(-1, size, trail, weight=weight)

    def trailing_stop_cover(self, size: float, trail: float, weight: float = 0.0) -> None:
        """Protective trailing buy-stop for a short: covers ``trail`` above the trough."""
        self._engine.submit_trailing(+1, size, trail, weight=weight)

    def buy_on_close(self, size: float) -> None:
        """Market-on-Close buy: fills at the next bar's close (MOC semantics)."""
        self._engine.submit_market_close(+1, size)

    def sell_on_close(self, size: float) -> None:
        """Market-on-Close sell: fills at the next bar's close (MOC semantics)."""
        self._engine.submit_market_close(-1, size)

    def limit_buy_on_close(self, size: float, price: float) -> None:
        """Limit-on-Close buy: fills at close only if close <= price."""
        self._engine.submit_limit_close(+1, size, price)

    def limit_sell_on_close(self, size: float, price: float) -> None:
        """Limit-on-Close sell: fills at close only if close >= price."""
        self._engine.submit_limit_close(-1, size, price)

    def cancel_all(self) -> None:
        """Cancel all resting (and not-yet-filled) orders."""
        self._engine.cancel_all()

    def order_target_shares(self, target: float) -> None:
        """Submit a market order to reach a signed target position of ``target`` shares."""
        self._engine.order_target(target)

    def order_target_value(self, value: float) -> None:
        """Target a position worth ``value`` in cash terms (signed)."""
        self._engine.order_target_value(value)

    def order_target_percent(self, pct: float) -> None:
        """Target a position worth ``pct`` of current equity (signed)."""
        self._engine.order_target_percent(pct)

    @property
    def drawdown(self) -> float:
        """Current drawdown from the equity peak (0.2 == 20% below peak) — for protections."""
        return self._engine.drawdown_now()

    @staticmethod
    def risk_to_qty(risk_amount: float, entry: float, stop: float) -> float:
        """Position size such that hitting ``stop`` from ``entry`` loses ``risk_amount``."""
        dist = abs(entry - stop)
        return risk_amount / dist if dist > 0 else 0.0

    # --- override this ---
    def on_bar(self, bar: Bar) -> None:  # noqa: ARG002 - overridden by users
        """Called once per bar, after pending orders for this bar have filled."""

    def on_quote_tick(self, tick) -> None:  # noqa: ARG002 - overridden by the per-tick engine (Slice 2)
        """Per-quote-tick hook. No-op in the bar/consolidator path; used by the Slice-2 per-tick engine."""

    def on_trade_tick(self, tick) -> None:  # noqa: ARG002 - overridden by the per-tick engine (Slice 2)
        """Per-trade-tick hook. No-op in the bar/consolidator path; used by the Slice-2 per-tick engine."""

    # --- lifecycle ---
    def on_start(self) -> None:
        """Called once before the run begins (engine wired). Override for setup."""

    def on_stop(self) -> None:
        """Called once after the run completes. Override for teardown/finalization."""

    # --- order lifecycle (granular, Nautilus-style). filled/submitted fire in backtest+live;
    #     accepted/rejected/canceled/expired/updated are LIVE-only (wired in A2). ---
    def on_order_submitted(self, order) -> None:  # noqa: ARG002
        """An order was placed (added to the engine's pending book)."""

    def on_order_accepted(self, event) -> None:  # noqa: ARG002
        """LIVE: the venue accepted the order (no-op in backtest)."""

    def on_order_rejected(self, event) -> None:  # noqa: ARG002
        """LIVE: the venue (or risk gate) rejected the order (no-op in backtest)."""

    def on_order_filled(self, fill: Fill) -> None:  # noqa: ARG002
        """An order filled (partially or fully), AFTER the position updates."""

    def on_order_canceled(self, event) -> None:  # noqa: ARG002
        """LIVE: the order was canceled (no-op in backtest)."""

    def on_order_expired(self, event) -> None:  # noqa: ARG002
        """LIVE: the order expired per its time-in-force (no-op in backtest)."""

    def on_order_updated(self, event) -> None:  # noqa: ARG002
        """LIVE: an amend/replace was applied (no-op in backtest)."""

    # --- position lifecycle ---
    def on_position_opened(self, position) -> None:  # noqa: ARG002
        """A flat position became non-flat."""

    def on_position_changed(self, position) -> None:  # noqa: ARG002
        """An existing position's size/avg-price changed (add or partial reduce)."""

    def on_position_closed(self, position) -> None:  # noqa: ARG002
        """A position returned to flat (``position.size == 0``)."""

    # --- catch-all + forced close ---
    def on_event(self, event) -> None:  # noqa: ARG002
        """Catch-all: receives every order/position/fill event (isinstance-dispatch)."""

    def on_liquidation(self, fill: Fill) -> None:  # noqa: ARG002
        """The engine force-closed at maintenance margin (the close also fires on_order_filled/on_position_closed)."""

    # --- optional: declare indicator lines to overlay on the price chart ---
    def chart_overlays(self, closes: list[float]) -> dict[str, list]:  # noqa: ARG002
        """Return ``{label: series}`` (each series aligned to ``closes``) to plot.

        Default: no overlays. Override to draw indicators on the chart.
        """
        return {}

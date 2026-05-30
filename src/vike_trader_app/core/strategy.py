"""The Strategy API — the stable contract users write against.

Subclass ``Strategy`` and implement ``on_bar``. Place orders with ``buy``/``sell``/
``close``; read state via ``position`` / ``equity`` / ``index``. The engine fills
market orders at the next bar's open (no look-ahead).
"""

from .model import Bar, Position


class Strategy:
    """Base strategy. The engine injects itself and updates ``index`` each bar.

    Multi-timeframe: pass ``timeframes=["1h", "4h"]`` to ``BacktestEngine``; then read
    completed higher-TF candles with ``self.bars("1h")`` (look-ahead-safe) and the
    still-forming candle with ``self.forming("1h")`` (for replay-style logic).
    """

    #: Optional optimizable parameters, e.g. ``{"fast": [5, 10], "slow": [20, 30]}``.
    PARAM_GRID: dict = {}

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

    def bars(self, tf: str):
        """Completed bars of higher timeframe ``tf`` visible now (no look-ahead)."""
        return self._engine.bars_for(tf)

    def forming(self, tf: str):
        """The in-progress (still-building) bar of higher timeframe ``tf``, or None."""
        return self._engine.forming_for(tf)

    # --- actions (resolved by the engine) ---
    def buy(self, size: float) -> None:
        self._engine.submit(+1, size)

    def sell(self, size: float) -> None:
        self._engine.submit(-1, size)

    def close(self) -> None:
        self._engine.submit_close()

    # --- resting orders (fill on a future bar when the trigger is hit) ---
    def limit_buy(self, size: float, price: float) -> None:
        self._engine.submit_limit(+1, size, price)

    def limit_sell(self, size: float, price: float) -> None:
        self._engine.submit_limit(-1, size, price)

    def stop_buy(self, size: float, price: float) -> None:
        self._engine.submit_stop(+1, size, price)

    def stop_sell(self, size: float, price: float) -> None:
        self._engine.submit_stop(-1, size, price)

    def trailing_stop(self, size: float, trail: float) -> None:
        """Protective trailing sell-stop for a long: exits ``trail`` below the peak."""
        self._engine.submit_trailing(-1, size, trail)

    def trailing_stop_cover(self, size: float, trail: float) -> None:
        """Protective trailing buy-stop for a short: covers ``trail`` above the trough."""
        self._engine.submit_trailing(+1, size, trail)

    def cancel_all(self) -> None:
        """Cancel all resting (and not-yet-filled) orders."""
        self._engine.cancel_all()

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

    # --- optional: declare indicator lines to overlay on the price chart ---
    def chart_overlays(self, closes: list[float]) -> dict[str, list]:  # noqa: ARG002
        """Return ``{label: series}`` (each series aligned to ``closes``) to plot.

        Default: no overlays. Override to draw indicators on the chart.
        """
        return {}

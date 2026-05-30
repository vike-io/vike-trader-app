"""The vectorized strategy front door: produce signal arrays, run them on the fast kernel.

Subclass and implement ``signals(data)`` returning aligned ``(entries, exits, size, side)``
arrays. ``run(data, **cfg)`` executes them via ``fast_backtest`` (compiled fast path).
For path-dependent logic (resting orders, trailing stops) use the event-driven
``Strategy``/``BacktestEngine`` instead.
"""

from .fastsim import fast_backtest


class SignalStrategy:
    """Base class for array/signal strategies executed on the compiled kernel."""

    def signals(self, data):
        """Override: return ``(entries, exits, size, side)`` arrays aligned to ``data['close']``.

        ``entries``/``exits`` are boolean; ``size`` is float shares; ``side`` is +1 long / -1 short (any value <= 0 is treated as short).
        """
        raise NotImplementedError

    def run(self, data, *, maker_fee=0.0, taker_fee=0.0, slippage=0.0, init_cash=10_000.0, build_trades=True):
        """Generate signals from ``data`` and backtest them. ``data`` maps the keys
        ``open, high, low, close, ts, funding`` to aligned sequences."""
        entries, exits, size, side = self.signals(data)
        return fast_backtest(
            data["open"], data["high"], data["low"], data["close"],
            data["funding"], data["ts"], entries, exits, size, side,
            maker_fee=maker_fee, taker_fee=taker_fee, slippage=slippage, init_cash=init_cash,
            build_trades=build_trades,
        )

"""BarSeriesBuffer — shared MTF (multi-timeframe) bar buffer.

Extracted from ``SingleSymbolEngine`` so that ``StrategyLiveEngine`` can reuse the
exact same logic without copying it.  Both engines hold the buffer as
``self._buf``; the base-bar list (``self.bars``) is shared by reference between
the engine and the buffer so every consumer reads the same list object.

Public API
----------
``add_live_bar(bar)``
    Append a live base bar and re-resample every registered higher timeframe.
``bars_for(tf, now)``
    Completed higher-TF bars visible at ``now`` (deliver-on-complete, no
    look-ahead). Mirrors ``SingleSymbolEngine.bars_for`` exactly.
``forming_for(tf, now)``
    The still-forming coarse bar for ``tf`` built from base bars seen up to
    ``now``, or ``None`` if none have started yet.  Mirrors
    ``SingleSymbolEngine.forming_for`` exactly.
"""

import bisect
from operator import attrgetter

from .model import Bar
from .timeframe import parse_timeframe, resample

_BAR_TS = attrgetter("ts")  # bisect key: ts-ascending list -> O(log n) slicing


class BarSeriesBuffer:
    """Shared multi-timeframe bar buffer for SingleSymbolEngine and StrategyLiveEngine.

    Parameters
    ----------
    bars:
        The engine's base-bar list.  **Must be the SAME list object the caller
        holds** — the buffer appends to it and reads from it; it is NOT copied.
    timeframes:
        Sequence of higher-timeframe strings (e.g. ``["1h", "4h"]``).
        Each is parsed once and pre-populated by resampling ``bars``.
    """

    def __init__(self, bars: list, timeframes=None) -> None:
        self.bars = bars  # SHARED reference — same list the engine exposes as self.bars
        self._tf: dict[str, tuple[int, list]] = {}
        for tf in timeframes or []:
            ms = parse_timeframe(tf)
            self._tf[tf] = (ms, resample(bars, ms))

    def add_live_bar(self, bar: Bar) -> None:
        """Append a live base bar and refresh higher-TF aggregates (forward mode).

        Appends ``bar`` to the shared ``self.bars`` list and re-resamples each
        registered higher timeframe.  Re-resampling is O(n) per bar; forward
        cadence is one bar per interval so cost is negligible.
        """
        self.bars.append(bar)
        for tf, (ms, _) in list(self._tf.items()):
            self._tf[tf] = (ms, resample(self.bars, ms))

    def bars_for(self, tf: str, now: int) -> list:
        """Completed higher-TF bars visible at ``now`` (deliver-on-complete).

        Slices the coarse list up to (but not including) the window that
        contains ``now`` — so the bar currently forming is NOT returned.

        Mirrors ``SingleSymbolEngine.bars_for`` exactly: the coarse list is
        ts-ascending; ``bisect_left`` finds the boundary in O(log n) instead of
        rescanning the whole list per call (the dominant MTF O(n²) hot path).
        """
        ms, coarse = self._tf[tf]
        window_start = now - now % ms
        return coarse[:bisect.bisect_left(coarse, window_start, key=_BAR_TS)]

    def forming_for(self, tf: str, now: int):
        """The still-forming coarse bar for ``tf`` up to ``now``, or ``None``.

        Aggregates the base bars in the current higher-TF window into a
        synthetic ``Bar``.  Returns ``None`` if no base bars have started the
        current window yet.

        Mirrors ``SingleSymbolEngine.forming_for`` exactly: ``self.bars`` is
        ts-ascending; ``bisect`` slices the ``[window_start, now]`` window in
        O(log n) instead of scanning the whole base series.
        """
        ms, _ = self._tf[tf]
        window_start = now - now % ms
        lo = bisect.bisect_left(self.bars, window_start, key=_BAR_TS)
        hi = bisect.bisect_right(self.bars, now, key=_BAR_TS)
        window = self.bars[lo:hi]
        if not window:
            return None
        return Bar(
            ts=window_start,
            open=window[0].open,
            high=max(b.high for b in window),
            low=min(b.low for b in window),
            close=window[-1].close,
            volume=sum(b.volume for b in window),
        )

"""Polling bar feed — Option 0 real-time source (no websocket, no new server work).

Calls a REST "latest bars" fetcher once per poll, emits each newly-**closed** bar to a
callback, and de-dupes by ``ts``. A bar is closed once ``now >= ts + interval_ms`` — so the
still-forming current candle is never emitted (look-ahead-safe). Latency is up to one
interval; that's the trade-off for needing zero work on the vike.io side.

Exposes the same shape a websocket feed would (``poll_once`` / ``run(on_bar)``), so
``ForwardTester`` is identical whichever backend drives it. The clock and sleep are injected,
so the loop is fully deterministic in tests; only ``make_vike_fetch_latest`` does network I/O.
"""

import time

from ..core.model import Bar
from .binance_source import interval_ms


class PollingBarFeed:
    """Polls ``fetch_latest`` and yields newly-closed bars in ``ts`` order."""

    def __init__(
        self,
        symbol: str,
        interval: str,
        *,
        fetch_latest,
        now=None,
        sleep=None,
        poll_seconds: float | None = None,
    ) -> None:
        self.symbol = symbol
        self.interval = interval
        self.interval_ms = interval_ms(interval)
        self._fetch_latest = fetch_latest  # () -> list[Bar] (may include the forming bar)
        self._now = now or (lambda: int(time.time() * 1000))
        self._sleep = sleep or time.sleep
        # Poll a few times per interval so a close is picked up promptly (capped at 15s).
        secs = self.interval_ms / 1000
        self.poll_seconds = poll_seconds if poll_seconds is not None else max(1.0, min(secs / 4, 15.0))
        self._last_ts: int | None = None

    def poll_once(self) -> list[Bar]:
        """Fetch once; return closed bars not seen before, ascending by ts."""
        now = self._now()
        closed = sorted(
            (b for b in self._fetch_latest() if b.ts + self.interval_ms <= now),
            key=lambda b: b.ts,
        )
        new = [b for b in closed if self._last_ts is None or b.ts > self._last_ts]
        if new:
            self._last_ts = new[-1].ts
        return new

    def run(self, on_bar, *, max_polls: int | None = None, stop=None) -> None:
        """Poll forever (or ``max_polls`` times), calling ``on_bar(bar)`` per new bar.

        Stops when ``stop()`` returns True (checked before each poll) or ``max_polls`` is
        reached. Sleeps ``poll_seconds`` *between* polls, not after the last one.
        """
        polls = 0
        while True:
            if stop is not None and stop():
                break
            for bar in self.poll_once():
                on_bar(bar)
            polls += 1
            if max_polls is not None and polls >= max_polls:
                break
            self._sleep(self.poll_seconds)


def make_vike_fetch_latest(symbol: str, interval: str, lookback: int = 5, caller=None):
    """Build a ``fetch_latest`` that pulls the last ``lookback`` intervals via vike.io REST.

    Network-backed (uses ``vike_source.fetch_bars_range``); kept out of the unit-tested
    core. Returns a zero-arg callable suitable for ``PollingBarFeed(fetch_latest=...)``.
    """
    from .vike_source import fetch_bars_range

    step = interval_ms(interval)

    def fetch_latest() -> list[Bar]:
        now = int(time.time() * 1000)
        start = now - lookback * step
        return fetch_bars_range(symbol, interval, start, now, caller=caller)

    return fetch_latest

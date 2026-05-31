"""LiveBarFeed — push consumer of the vike.io ``wss://vike.io/v1/stream`` bar stream.

Lower-latency alternative to ``PollingBarFeed``: instead of polling REST, it holds a
websocket and reacts to pushed ``bar`` frames. It emits the same **closed** ``Bar`` objects
to the same ``on_bar`` callback, so it drops into ``PaperTester`` unchanged — only the
transport differs.

The pure frame-handling core (``bar_from_frame`` / ``handle_frame``) is fully unit-tested
with scripted frames; the async websocket transport (``run`` / ``run_forever``) is the thin
network shell — ``websockets`` is imported lazily there, so the core needs no extra dep.

Recovery: each ``bar`` frame carries a monotonic ``seq``. A jump in ``seq`` means frames were
missed, so the gap is filled from the existing REST source (``vike_source.fetch_bars_range``)
before the new bar is emitted — keeping the bar stream contiguous and look-ahead-safe
(forming ``closed:false`` frames are stored for chart painting only, never emitted/traded).
"""

import asyncio
import json

from ..core.model import Bar
from .binance_source import interval_ms

STREAM_URL = "wss://vike.io/v1/stream"


class LiveFeedError(RuntimeError):
    """Raised when the server sends an ``error`` frame."""

    def __init__(self, code: str | None, message: str | None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def bar_from_frame(frame: dict) -> Bar:
    """Map a ``bar`` frame to a ``Bar`` (funding ``None`` preserved)."""
    funding = frame.get("funding")
    return Bar(
        ts=int(frame["ts"]),
        open=float(frame["open"]),
        high=float(frame["high"]),
        low=float(frame["low"]),
        close=float(frame["close"]),
        volume=float(frame.get("volume", 0.0)),
        funding=None if funding is None else float(funding),
    )


class LiveBarFeed:
    """Consume stream frames; emit closed bars to ``on_bar`` (same shape as PollingBarFeed)."""

    def __init__(
        self,
        symbol: str,
        interval: str,
        *,
        token: str | None = None,
        url: str = STREAM_URL,
        backfill=None,
        connect=None,
    ) -> None:
        self.symbol = symbol
        self.interval = interval
        self.interval_ms = interval_ms(interval)
        self.token = token
        self.url = url
        self._backfill = backfill  # (symbol, interval, start_ms, end_ms) -> list[Bar]
        self._connect = connect    # injected async connect (tests / custom transport)
        self.forming: Bar | None = None  # latest in-progress bar, for live chart paint only
        self._last_ts: int | None = None
        self._last_seq: int | None = None

    # --- pure core (unit-tested) ---
    def _emit(self, bar: Bar, on_bar) -> None:
        """Emit a closed bar at most once, in ts order (de-dupe by ts)."""
        if self._last_ts is None or bar.ts > self._last_ts:
            self._last_ts = bar.ts
            on_bar(bar)

    def handle_frame(self, frame: dict, on_bar) -> str | None:
        """Process one decoded server frame. Returns ``"pong"`` when the caller must reply."""
        kind = frame.get("type")
        if kind == "ping":
            return "pong"
        if kind == "error":
            raise LiveFeedError(frame.get("code"), frame.get("message"))
        if kind != "bar":
            return None

        seq = frame.get("seq")
        # Gap = seq jumped past the next expected value (computed before we advance the tracker).
        gap = self._last_seq is not None and seq is not None and seq > self._last_seq + 1
        if seq is not None:
            self._last_seq = seq  # advance on every seq-bearing frame (incl. forming)

        if not frame.get("closed", False):
            self.forming = bar_from_frame(frame)  # paint only; never traded
            return None

        bar = bar_from_frame(frame)
        if gap and self._backfill is not None and self._last_ts is not None:
            for b in self._backfill(self.symbol, self.interval, self._last_ts + 1, bar.ts):
                self._emit(b, on_bar)
        self._emit(bar, on_bar)
        return None

    # --- async network shell (lazy websockets import; not unit-tested) ---
    async def _open(self):
        if self._connect is not None:
            return await self._connect()
        import websockets  # optional [live] dependency

        try:
            return await websockets.connect(self.url, additional_headers={"X-API-KEY": self.token})
        except TypeError:  # older websockets used extra_headers
            return await websockets.connect(self.url, extra_headers={"X-API-KEY": self.token})

    async def run(self, on_bar, *, stop=None) -> None:
        """Connect, subscribe, and pump frames until ``stop()`` is true or the socket closes."""
        ws = await self._open()
        try:
            await ws.send(json.dumps(
                {"action": "subscribe", "channel": "bars", "symbol": self.symbol, "interval": self.interval}
            ))
            while not (stop is not None and stop()):
                raw = await ws.recv()
                if self.handle_frame(json.loads(raw), on_bar) == "pong":
                    await ws.send(json.dumps({"action": "pong"}))
        finally:
            await ws.close()

    async def run_forever(self, on_bar, *, stop=None, max_backoff: float = 30.0) -> None:
        """``run`` with reconnect + exponential backoff; the seq-gap logic backfills on resume."""
        backoff = 1.0
        while not (stop is not None and stop()):
            try:
                await self.run(on_bar, stop=stop)
                backoff = 1.0  # clean close
            except LiveFeedError:
                raise  # auth/protocol error — don't loop on it
            except Exception:  # noqa: BLE001 - transport hiccup -> reconnect
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)


def make_live_feed(symbol: str, interval: str, token: str | None = None) -> LiveBarFeed:
    """A LiveBarFeed wired to the live stream + REST backfill from the existing vike source."""
    import os

    from .vike_source import fetch_bars_range

    return LiveBarFeed(
        symbol,
        interval,
        token=token or os.environ.get("vikeio_full_token"),
        backfill=lambda s, i, a, b: fetch_bars_range(s, i, a, b),
    )

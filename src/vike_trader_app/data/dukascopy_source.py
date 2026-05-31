"""Dukascopy forex source — keyless deep tick history (bid/ask, back to ~2003).

Downloads the public per-hour ``.bi5`` tick archives (no key, no signup), decodes them,
and aggregates ticks to ``Bar`` OHLC at any interval. Covers every FX pair, ~23 years deep,
with a publish lag of roughly T+1 — so it is **history only, not realtime** (pair with
``yahoo_source`` for the live edge). ``fetch_bars_range`` matches the binance_source
signature, so it drops into ``cache.get_bars(..., fetcher=fetch_bars_range)``.

File format (one UTC hour per file): LZMA-compressed records of 20 bytes, big-endian
``>3i2f`` = (ms-offset-into-hour, ask_points, bid_points, ask_volume, bid_volume). Prices are
integers in "points"; divide by 10**digits (5 for most pairs, 3 for JPY-quoted).

The pure parts (`point_divisor`, `decompress`, `decode_ticks`, `ticks_to_bars`, `_retry`)
are unit-tested; only `_fetch_hour` performs network I/O. ``_fetch_hour`` retries transient
CDN failures (429/5xx, connection resets, timeouts) so a long deep-history pull survives the
occasional hiccup instead of aborting the whole run.
"""

import lzma
import struct
import time
import urllib.error
import urllib.request
from collections import namedtuple
from datetime import datetime, timezone

from ..core.model import Bar
from .binance_source import interval_ms

DATAFEED = "https://datafeed.dukascopy.com/datafeed"
HOUR_MS = 3_600_000
_REC = struct.Struct(">3i2f")  # 20 bytes: ms, ask, bid, askVol, bidVol
_UA = "Mozilla/5.0 (vike-trader-app forex source)"
# Transient HTTP statuses worth retrying (Dukascopy's CDN occasionally 503s mid-pull).
_TRANSIENT_CODES = frozenset({429, 500, 502, 503, 504})

#: One decoded tick. ``mid`` is the bid/ask midpoint used for OHLC aggregation.
Tick = namedtuple("Tick", "ts bid ask bid_vol ask_vol")


def point_divisor(symbol: str) -> float:
    """Price scale for a pair: 1e3 for JPY-quoted (3 digits), else 1e5 (5 digits)."""
    return 1e3 if symbol.upper().endswith("JPY") else 1e5


def decompress(blob: bytes) -> bytes:
    """LZMA-decompress a ``.bi5`` payload (tries the standard then the legacy 'alone' header)."""
    for fmt in (lzma.FORMAT_AUTO, lzma.FORMAT_ALONE):
        try:
            return lzma.LZMADecompressor(format=fmt).decompress(blob)
        except lzma.LZMAError:
            continue
    raise lzma.LZMAError("could not decode .bi5 (unrecognised LZMA stream)")


def decode_ticks(raw: bytes, hour_start_ms: int, divisor: float) -> list[Tick]:
    """Decode raw (decompressed) bytes into Ticks, with timestamps anchored to the hour."""
    ticks = []
    for ms, ask_pts, bid_pts, ask_vol, bid_vol in _REC.iter_unpack(raw):
        ticks.append(Tick(
            ts=hour_start_ms + ms,
            bid=bid_pts / divisor,
            ask=ask_pts / divisor,
            bid_vol=bid_vol,
            ask_vol=ask_vol,
        ))
    return ticks


def ticks_to_bars(ticks: list[Tick], step_ms: int) -> list[Bar]:
    """Aggregate Ticks into OHLC bars of ``step_ms``, using the bid/ask mid as the price.

    ``volume`` is the **tick count** in the bucket (FX has no true volume — tick volume is
    the standard proxy). Assumes ticks are ascending by ``ts``; emits bars ascending.
    """
    buckets: dict[int, list[float]] = {}
    counts: dict[int, int] = {}
    for t in ticks:
        bucket = (t.ts // step_ms) * step_ms
        mid = (t.bid + t.ask) / 2
        buckets.setdefault(bucket, []).append(mid)
        counts[bucket] = counts.get(bucket, 0) + 1
    bars = []
    for bucket in sorted(buckets):
        mids = buckets[bucket]
        bars.append(Bar(ts=bucket, open=mids[0], high=max(mids), low=min(mids),
                        close=mids[-1], volume=float(counts[bucket])))
    return bars


def hour_url(symbol: str, hour_start_ms: int) -> str:
    """Build the ``.bi5`` URL for a UTC hour. Month is 0-indexed (January = 00).

    ``hour_start_ms`` is interpreted strictly as UTC epoch ms — all archive paths and tick
    timestamps are UTC, matching ``Bar.ts``.
    """
    dt = datetime.fromtimestamp(hour_start_ms / 1000, timezone.utc)
    return (f"{DATAFEED}/{symbol.upper()}/{dt.year}/{dt.month - 1:02d}/"
            f"{dt.day:02d}/{dt.hour:02d}h_ticks.bi5")


def _is_transient(err: BaseException) -> bool:
    """Whether a network error is worth retrying. 404/other-4xx are permanent."""
    if isinstance(err, urllib.error.HTTPError):
        return err.code in _TRANSIENT_CODES
    return True  # non-HTTP URLError (DNS / connection reset) or timeout -> retry


def _retry(call, *, tries: int = 4, sleep=time.sleep, base: float = 0.5):
    """Run ``call()``, retrying transient failures with exponential backoff.

    Retries 429/5xx, connection errors, and timeouts; re-raises 404/other-4xx immediately so
    the caller can map them (a 404 hour = no file). The final attempt's error propagates.
    ``sleep``/``base`` are injectable so tests run without real delay.
    """
    for attempt in range(tries):
        try:
            return call()
        except (urllib.error.URLError, TimeoutError) as e:  # HTTPError subclasses URLError
            if attempt == tries - 1 or not _is_transient(e):
                raise
            sleep(base * (2 ** attempt))


def _fetch_hour(symbol: str, hour_start_ms: int, timeout: int = 30) -> bytes | None:
    """Download one hour's ``.bi5`` (None if absent), retrying transient CDN errors."""
    req = urllib.request.Request(hour_url(symbol, hour_start_ms),  # noqa: S310 - fixed https host
                                 headers={"User-Agent": _UA})

    def _open() -> bytes:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read()

    try:
        return _retry(_open)
    except urllib.error.HTTPError as e:
        if e.code == 404:  # no data for this hour (weekend / pre-listing / not-yet-published)
            return None
        raise


def fetch_ticks_range(symbol: str, start_ms: int, end_ms: int, fetch_hour=None, progress=None) -> list[Tick]:
    """Download and decode every tick in ``[start_ms, end_ms]`` (hour by hour)."""
    fetch = fetch_hour if fetch_hour is not None else _fetch_hour
    divisor = point_divisor(symbol)
    out: list[Tick] = []
    hour = (start_ms // HOUR_MS) * HOUR_MS
    while hour <= end_ms:
        blob = fetch(symbol, hour)
        if blob:  # skip empty (0 ticks) and missing (None) hours
            for t in decode_ticks(decompress(blob), hour, divisor):
                if start_ms <= t.ts <= end_ms:
                    out.append(t)
        if progress:
            progress(min(hour + HOUR_MS, end_ms), start_ms, end_ms)
        hour += HOUR_MS
    return out


def fetch_bars_range(symbol: str, interval: str, start_ms: int, end_ms: int,
                     fetch_hour=None, progress=None) -> list[Bar]:
    """Fetch tick history and aggregate to ``interval`` bars. Matches binance_source's signature."""
    ticks = fetch_ticks_range(symbol, start_ms, end_ms, fetch_hour=fetch_hour, progress=progress)
    return ticks_to_bars(ticks, interval_ms(interval))

"""Kraken OHLC source.

Pure transforms (``to_bars``, ``parse_response``, ``market_symbol``) are unit-tested; the REST
wrapper hits the public ``/0/public/OHLC`` endpoint (no key). Kraken keys the result by the
(sometimes renamed) pair, returns candles **ascending** with volume in column 6, uses minute
intervals, calls Bitcoin ``XBT``, and paginates forward via ``since``.
"""

import time

from ..core.model import Bar
from .rest import get_json
from .rows import rows_to_bars

KRAKEN_API = "https://api.kraken.com"

# our interval -> Kraken interval in minutes (only this set is supported)
INTERVALS = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}


def market_symbol(symbol: str) -> str:
    """Kraken uses ``XBT`` for Bitcoin: ``BTCUSDT`` -> ``XBTUSDT`` (others pass through)."""
    s = symbol.upper()
    return "XBT" + s[3:] if s.startswith("BTC") else s


def to_bars(rows: list[list]) -> list[Bar]:
    """``[time_s, o, h, l, c, vwap, vol, count]`` rows -> ascending ``Bar``s (volume = col 6)."""
    return rows_to_bars(rows, {"ts": 0, "open": 1, "high": 2, "low": 3, "close": 4, "volume": 6},
                        ts_scale=1000)


def parse_response(resp: dict) -> list[Bar]:
    """Pull the candle list out of Kraken's ``result`` (keyed by pair; skip the ``last`` cursor)."""
    for key, val in resp.get("result", {}).items():
        if key != "last":
            return to_bars(val)
    return []


def _fetch(symbol: str, interval: str, since_s: int, base_url: str = KRAKEN_API) -> dict:
    url = (f"{base_url}/0/public/OHLC?pair={market_symbol(symbol)}"
           f"&interval={INTERVALS[interval]}&since={since_s}")
    return get_json(url)


def fetch_bars_range(symbol: str, interval: str, start_ms: int, end_ms: int,
                     base_url: str = KRAKEN_API, pause: float = 0.0, progress=None,
                     max_pages: int = 10_000) -> list[Bar]:
    """All bars in ``[start_ms, end_ms]`` walked forward via the ``since`` cursor."""
    by_ts: dict[int, Bar] = {}
    since = start_ms // 1000
    last = None
    for _ in range(max_pages):
        bars = parse_response(_fetch(symbol, interval, since, base_url=base_url))
        if not bars:
            break
        for b in bars:
            by_ts[b.ts] = b
        newest = bars[-1].ts
        if last is not None and newest <= last:  # no forward progress
            break
        last = newest
        since = newest // 1000 + 1
        if progress:
            progress(min(newest, end_ms), start_ms, end_ms)
        if newest >= end_ms:
            break
        if pause:
            time.sleep(pause)
    return [by_ts[t] for t in sorted(by_ts) if start_ms <= t <= end_ms]

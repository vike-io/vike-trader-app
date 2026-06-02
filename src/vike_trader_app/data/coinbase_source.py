"""Coinbase Exchange candles source.

Pure transforms (``to_bars``, ``market_symbol``) are unit-tested; the REST wrapper hits the
public ``/products/<id>/candles`` endpoint (no key). Granularity is in **seconds** from a fixed
set, candles are **newest-first**, and the column order is unusual: ``[time, low, high, open,
close, volume]``. History is walked forward in 300-candle windows (Coinbase's per-call cap).
"""

import time

from ..core.model import Bar
from .rest import get_json

COINBASE_API = "https://api.exchange.coinbase.com"

# our interval -> Coinbase granularity in seconds (only this fixed set is supported)
INTERVALS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "1d": 86400}
_QUOTES = ("USDT", "USDC", "USD", "EUR", "GBP", "BTC")
_MAX_CANDLES = 300


def market_symbol(symbol: str) -> str:
    """``BTCUSD`` -> Coinbase product ``BTC-USD`` (dash before the quote currency)."""
    s = symbol.upper()
    for q in _QUOTES:
        if s.endswith(q) and len(s) > len(q):
            return f"{s[:-len(q)]}-{q}"
    return s


def to_bars(rows: list[list]) -> list[Bar]:
    """``[time_s, low, high, open, close, vol]`` rows -> ascending ``Bar``s (note column order)."""
    bars = [Bar(ts=int(r[0]) * 1000, open=float(r[3]), high=float(r[2]), low=float(r[1]),
                close=float(r[4]), volume=float(r[5])) for r in rows]
    bars.sort(key=lambda b: b.ts)
    return bars


def _fetch(symbol: str, interval: str, start_s: int, end_s: int,
           base_url: str = COINBASE_API) -> list[list]:
    url = (f"{base_url}/products/{market_symbol(symbol)}/candles"
           f"?granularity={INTERVALS[interval]}&start={start_s}&end={end_s}")
    return get_json(url)


def fetch_bars_range(symbol: str, interval: str, start_ms: int, end_ms: int,
                     base_url: str = COINBASE_API, pause: float = 0.0, progress=None) -> list[Bar]:
    """All bars in ``[start_ms, end_ms]`` walked forward in 300-candle windows."""
    gran = INTERVALS[interval]
    window = _MAX_CANDLES * gran  # seconds per request
    out: list[list] = []
    cur, end_s = start_ms // 1000, end_ms // 1000
    while cur <= end_s:
        chunk_end = min(cur + window, end_s)
        out.extend(_fetch(symbol, interval, cur, chunk_end, base_url=base_url))
        cur = chunk_end + gran
        if progress:
            progress(min(cur * 1000, end_ms), start_ms, end_ms)
        if pause:
            time.sleep(pause)
    return [b for b in to_bars(out) if start_ms <= b.ts <= end_ms]

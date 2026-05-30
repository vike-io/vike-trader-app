"""Binance klines data source.

Pure transforms (`klines_to_bars`, `paginate`) are unit-tested; the functions that
hit the public REST API (no key required) are exercised by the live seed/scripts.
Pagination assembles long history from Binance's per-call cap (1000 klines).
"""

import json
import time
import urllib.request

from ..core.model import Bar

BINANCE_API = "https://api.binance.com"

INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
}


def interval_ms(interval: str) -> int:
    """Milliseconds per bar for a Binance interval. Raises KeyError if unknown."""
    return INTERVAL_MS[interval]


def klines_to_bars(raw: list[list]) -> list[Bar]:
    """Convert raw Binance klines to Bars. Kline = [openTime, o, h, l, c, v, ...]."""
    return [
        Bar(
            ts=int(k[0]),
            open=float(k[1]),
            high=float(k[2]),
            low=float(k[3]),
            close=float(k[4]),
            volume=float(k[5]),
        )
        for k in raw
    ]


def fetch_klines(
    symbol: str = "BTCUSDT",
    interval: str = "1m",
    limit: int = 1000,
    base_url: str = BINANCE_API,
) -> list[list]:
    """Fetch the most recent ``limit`` raw klines (no time range)."""
    url = f"{base_url}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 - fixed https host
        return json.loads(resp.read())


def fetch_klines_page(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    limit: int = 1000,
    base_url: str = BINANCE_API,
) -> list[list]:
    """Fetch one page of raw klines within ``[start_ms, end_ms]`` (ascending)."""
    url = (
        f"{base_url}/api/v3/klines?symbol={symbol}&interval={interval}"
        f"&startTime={start_ms}&endTime={end_ms}&limit={limit}"
    )
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 - fixed https host
        return json.loads(resp.read())


def paginate(
    start_ms: int,
    end_ms: int,
    step_ms: int,
    page_fn,
    pause: float = 0.0,
    max_pages: int = 100_000,
    progress=None,
) -> list[list]:
    """Assemble raw klines across ``[start_ms, end_ms]`` by repeatedly calling ``page_fn``.

    ``page_fn(start, end)`` returns one ascending page of raw klines. Advances past the
    last bar's openTime each round; stops on an empty page, no forward progress, or once
    the cursor passes ``end_ms``. ``progress(done_ms, start_ms, end_ms)`` is optional.
    """
    out: list[list] = []
    cursor = start_ms
    pages = 0
    last_open = None  # highest openTime seen so far
    while cursor <= end_ms and pages < max_pages:
        page = page_fn(cursor, end_ms)
        if not page:
            break
        page_last = int(page[-1][0])
        # safety: a page that doesn't advance past what we've seen would loop forever.
        if last_open is not None and page_last <= last_open:
            break
        out.extend(page)
        pages += 1
        last_open = page_last
        cursor = page_last + step_ms
        if progress:
            progress(min(cursor, end_ms), start_ms, end_ms)
        if pause:
            time.sleep(pause)
    return out


def fetch_bars(
    symbol: str = "BTCUSDT",
    interval: str = "1m",
    limit: int = 1000,
    base_url: str = BINANCE_API,
) -> list[Bar]:
    """Fetch the most recent ``limit`` bars (no time range)."""
    return klines_to_bars(fetch_klines(symbol, interval, limit, base_url))


def fetch_bars_range(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    base_url: str = BINANCE_API,
    pause: float = 0.0,
    progress=None,
) -> list[Bar]:
    """Fetch ALL bars in ``[start_ms, end_ms]`` via pagination (years of history)."""
    step = interval_ms(interval)
    raw = paginate(
        start_ms,
        end_ms,
        step,
        lambda s, e: fetch_klines_page(symbol, interval, s, e, base_url=base_url),
        pause=pause,
        progress=progress,
    )
    return klines_to_bars(raw)

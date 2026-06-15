"""OKX candles source.

Pure transforms (``to_bars``, ``market_symbol``) are unit-tested; the REST wrapper hits the
public ``/api/v5/market/history-candles`` endpoint (no key). OKX paginates by a backward
``after`` cursor (records older than a timestamp, newest-first), so history is walked from the
end of the range toward the start.
"""

import time

from ..core.model import Bar
from .rest import get_json
from .rows import rows_to_bars

OKX_API = "https://www.okx.com"

# our interval -> OKX's (note upper-case H/D/W for hours/day/week)
INTERVALS = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1H", "2h": "2H", "4h": "4H", "6h": "6H", "12h": "12H", "1d": "1D", "1w": "1W",
}
_QUOTES = ("USDT", "USDC", "USD", "DAI", "EUR", "BTC", "ETH")


def market_symbol(symbol: str) -> str:
    """``BTCUSDT`` -> OKX instId ``BTC-USDT`` (dash before the quote currency)."""
    s = symbol.upper()
    for q in _QUOTES:
        if s.endswith(q) and len(s) > len(q):
            return f"{s[:-len(q)]}-{q}"
    return s


def to_bars(rows: list[list]) -> list[Bar]:
    """``[ts, o, h, l, c, vol, ...]`` rows (newest-first) -> ascending ``Bar``s."""
    return rows_to_bars(rows, {"ts": 0, "open": 1, "high": 2, "low": 3, "close": 4, "volume": 5})


def _fetch(after_ms: int, symbol: str, interval: str, limit: int = 100,
           base_url: str = OKX_API) -> list[list]:
    """Up to ``limit`` candles older than ``after_ms`` (newest-first)."""
    url = (f"{base_url}/api/v5/market/history-candles?instId={market_symbol(symbol)}"
           f"&bar={INTERVALS[interval]}&after={after_ms}&limit={limit}")
    return get_json(url).get("data", [])


def fetch_bars_range(symbol: str, interval: str, start_ms: int, end_ms: int,
                     base_url: str = OKX_API, pause: float = 0.0, progress=None,
                     max_pages: int = 10_000) -> list[Bar]:
    """All bars in ``[start_ms, end_ms]`` via the backward ``after`` cursor."""
    out: list[list] = []
    cursor = end_ms + 1
    seen_min = None
    for _ in range(max_pages):
        rows = _fetch(cursor, symbol, interval, base_url=base_url)
        if not rows:
            break
        out.extend(rows)
        page_min = min(int(r[0]) for r in rows)
        if seen_min is not None and page_min >= seen_min:  # no backward progress
            break
        seen_min = cursor = page_min
        if progress:
            progress(max(start_ms, page_min), start_ms, end_ms)
        if page_min <= start_ms:
            break
        if pause:
            time.sleep(pause)
    return [b for b in to_bars(out) if start_ms <= b.ts <= end_ms]

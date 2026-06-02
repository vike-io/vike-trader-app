"""Bybit v5 spot klines source.

Pure transforms (``to_bars``, ``market_symbol``) are unit-tested; the REST wrapper hits the
public ``/v5/market/kline`` endpoint (no key) and reuses Binance's ``paginate``. Bybit returns
candles newest-first, so each page is reversed to ascending before assembly.
"""

from ..core.model import Bar
from .binance_source import INTERVAL_MS, paginate
from .rest import get_json

BYBIT_API = "https://api.bybit.com"

# our interval -> Bybit's (minutes as digits; D/W/M for day/week/month)
INTERVALS = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720", "1d": "D", "1w": "W",
}


def market_symbol(symbol: str) -> str:
    """Bybit spot uses the joined ticker as-is (``BTCUSDT``)."""
    return symbol.upper()


def to_bars(rows: list[list]) -> list[Bar]:
    """``[startMs, o, h, l, c, vol, turnover]`` rows (any order) -> ascending ``Bar``s."""
    bars = [Bar(ts=int(r[0]), open=float(r[1]), high=float(r[2]), low=float(r[3]),
                close=float(r[4]), volume=float(r[5])) for r in rows]
    bars.sort(key=lambda b: b.ts)
    return bars


def _fetch_page(symbol: str, interval: str, start_ms: int, end_ms: int,
                limit: int = 1000, base_url: str = BYBIT_API) -> list[list]:
    """One ascending page of raw klines in ``[start_ms, end_ms]`` (Bybit returns descending)."""
    url = (f"{base_url}/v5/market/kline?category=spot&symbol={market_symbol(symbol)}"
           f"&interval={INTERVALS[interval]}&start={start_ms}&end={end_ms}&limit={limit}")
    rows = list(get_json(url).get("result", {}).get("list", []))
    rows.reverse()  # newest-first -> ascending for paginate
    return rows


def fetch_bars_range(symbol: str, interval: str, start_ms: int, end_ms: int,
                     base_url: str = BYBIT_API, pause: float = 0.0, progress=None) -> list[Bar]:
    """All bars in ``[start_ms, end_ms]`` via pagination."""
    raw = paginate(start_ms, end_ms, INTERVAL_MS[interval],
                   lambda s, e: _fetch_page(symbol, interval, s, e, base_url=base_url),
                   pause=pause, progress=progress)
    return to_bars(raw)

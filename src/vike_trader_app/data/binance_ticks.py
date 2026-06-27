"""Binance historical trade ticks (aggTrades) -> TradeTick, persisted to the tick store.

The pure ``rows_to_trade_ticks`` is unit-tested; ``_fetch_agg_trades`` performs the REST
paging and is injected in tests. Binance has deep aggTrades history (data.binance.vision /
REST); Bybit/OKX trade history is shallower and handled in their own modules later.
"""

import json
import urllib.request

from ..core.ticks import TradeTick
from . import tick_store

_REST = "https://api.binance.com/api/v3/aggTrades"
_UA = "Mozilla/5.0 (vike-trader-app binance ticks)"


def rows_to_trade_ticks(rows: list[dict]) -> list[TradeTick]:
    """Map Binance aggTrades rows to TradeTicks (T=time ms, p=price, q=qty, m=isBuyerMaker)."""
    return [TradeTick(ts=int(r["T"]), price=float(r["p"]), size=float(r["q"]),
                      is_buyer_maker=bool(r["m"])) for r in rows]


def _fetch_agg_trades(symbol: str, start_ms: int, end_ms: int) -> list[dict]:  # pragma: no cover - network
    out: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        url = f"{_REST}?symbol={symbol}&startTime={cursor}&endTime={end_ms}&limit=1000"
        req = urllib.request.Request(url, headers={"User-Agent": _UA})  # noqa: S310 - fixed https host
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            batch = json.loads(resp.read())
        if not batch:
            break
        out.extend(batch)
        last = int(batch[-1]["T"])
        if last <= cursor:
            break
        cursor = last + 1
    return out


def cache_trades_range(symbol: str, start_ms: int, end_ms: int, root: str, fetch=None) -> int:
    """Fetch Binance trade ticks for ``[start_ms, end_ms]`` and persist them. Returns count."""
    fetch = fetch if fetch is not None else _fetch_agg_trades
    ticks = rows_to_trade_ticks(fetch(symbol, start_ms, end_ms))
    tick_store.write_trades(ticks, root, symbol)
    return len(ticks)

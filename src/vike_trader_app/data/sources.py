"""Source selector — picks the data backend for a symbol (crypto vs forex).

One ``Source`` bundles the three things the app needs from a backend:
  * ``fetch_bars_range`` — history fetcher (matches ``cache.get_bars``'s ``fetcher=``)
  * ``make_fetch_latest`` — builds the zero-arg ``fetch_latest`` for ``PollingBarFeed``
  * ``supports_live_ws`` — whether to attempt the push WebSocket before polling

``select_source`` routes by symbol (or an explicit provider override):
  * **crypto** (e.g. ``BTCUSDT``) — unchanged: Binance history + vike live + WebSocket.
  * **forex** (e.g. ``EURUSD``) — keyless: Yahoo for the recent edge stitched with
    Dukascopy for deeper history, Yahoo polling for live, no WebSocket.

The stitch (``forex_fetch_bars_range``) and routing (``is_forex_symbol``, ``split_range``)
are pure/unit-tested; only the underlying source modules do network I/O.
"""

import time
from dataclasses import dataclass
from typing import Callable

from ..core.model import Bar
from . import binance_source, dukascopy_source, yahoo_source
from .polling_feed import make_vike_fetch_latest
from .vike_source import fetch_bars_range as vike_fetch_bars_range

# ISO-4217 codes we treat as forex legs (majors + common crosses/exotics we verified).
CURRENCIES = {
    "USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD",
    "SGD", "HKD", "MXN", "ZAR", "TRY", "SEK", "NOK", "PLN", "DKK", "CZK", "HUF", "CNH",
}

DAY_MS = 86_400_000
# Yahoo retains 1m for ~a month; newer than this -> Yahoo, older -> Dukascopy.
YAHOO_MAX_AGE_MS = 28 * DAY_MS


@dataclass(frozen=True)
class Source:
    """A data backend: history + live-poll factory + whether a push feed exists."""

    name: str
    fetch_bars_range: Callable  # (symbol, interval, start_ms, end_ms, progress=None) -> list[Bar]
    make_fetch_latest: Callable  # (symbol, interval) -> (() -> list[Bar])
    supports_live_ws: bool


def is_forex_symbol(symbol: str) -> bool:
    """True for a 6-letter pair whose halves are both currency codes (``EURUSD``).

    Distinguishes forex from crypto stablecoin pairs by length: ``EURUSD`` (6) is forex,
    ``EURUSDT`` (7) is crypto.
    """
    s = symbol.upper()
    return len(s) == 6 and s.isalpha() and s[:3] in CURRENCIES and s[3:] in CURRENCIES


def split_range(start_ms: int, end_ms: int, now_ms: int, max_age_ms: int = YAHOO_MAX_AGE_MS):
    """Split ``[start, end]`` into ``(old, recent)`` sub-ranges at the ``now - max_age`` cutoff.

    ``old`` goes to Dukascopy (deep archive), ``recent`` to Yahoo (live edge). Either may be
    None when the whole window falls on one side. Returns ``(old|None, recent|None)``.
    """
    cutoff = now_ms - max_age_ms
    if end_ms < cutoff:
        return (start_ms, end_ms), None
    if start_ms >= cutoff:
        return None, (start_ms, end_ms)
    return (start_ms, cutoff - 1), (cutoff, end_ms)


def forex_fetch_bars_range(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    progress=None,
    now_ms: int | None = None,
    yahoo_fetch=None,
    duka_fetch=None,
) -> list[Bar]:
    """Stitched forex history: Dukascopy for the old part, Yahoo for the recent part.

    Signature matches ``cache.get_bars``'s fetcher. ``now_ms`` and the two sub-fetchers are
    injectable for deterministic tests; they default to wall-clock + the real source modules.
    """
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    yh = yahoo_fetch if yahoo_fetch is not None else yahoo_source.fetch_bars_range
    dk = duka_fetch if duka_fetch is not None else dukascopy_source.fetch_bars_range
    old, recent = split_range(start_ms, end_ms, now)
    by_ts: dict[int, Bar] = {}
    if old is not None:
        for b in dk(symbol, interval, old[0], old[1], progress=progress):
            by_ts[b.ts] = b
    if recent is not None:
        for b in yh(symbol, interval, recent[0], recent[1], progress=progress):
            by_ts[b.ts] = b
    return [by_ts[t] for t in sorted(by_ts)]


# Crypto keeps today's exact behavior: Binance history, vike live-poll, WebSocket push.
CRYPTO = Source(
    name="crypto",
    fetch_bars_range=binance_source.fetch_bars_range,
    make_fetch_latest=make_vike_fetch_latest,
    supports_live_ws=True,
)

# Forex: keyless Yahoo (recent) + Dukascopy (deep) for history, Yahoo polling for live.
FOREX = Source(
    name="forex",
    fetch_bars_range=forex_fetch_bars_range,
    make_fetch_latest=yahoo_source.make_yahoo_fetch_latest,
    supports_live_ws=False,
)

SOURCES = {
    "crypto": CRYPTO,
    "forex": FOREX,
    # explicit single-backend handles, for callers that want to force one:
    "binance": Source("binance", binance_source.fetch_bars_range, make_vike_fetch_latest, True),
    "vike": Source("vike", vike_fetch_bars_range, make_vike_fetch_latest, True),
    "yahoo": Source("yahoo", yahoo_source.fetch_bars_range, yahoo_source.make_yahoo_fetch_latest, False),
    "dukascopy": Source("dukascopy", dukascopy_source.fetch_bars_range, yahoo_source.make_yahoo_fetch_latest, False),
}


def select_source(symbol: str, provider: str | None = None) -> Source:
    """Resolve the Source for ``symbol``. Explicit ``provider`` wins; else infer crypto/forex."""
    if provider is not None:
        try:
            return SOURCES[provider]
        except KeyError:
            raise ValueError(f"unknown provider {provider!r}; expected one of {sorted(SOURCES)}") from None
    return FOREX if is_forex_symbol(symbol) else CRYPTO

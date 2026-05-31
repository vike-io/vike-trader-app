"""Yahoo Finance forex source — keyless realtime quote + recent OHLC history.

Hits the public, no-key chart endpoint ``query1.finance.yahoo.com/v8/finance/chart``
and maps each candle to ``Bar``. Covers every FX pair (majors, crosses, exotics) via the
``EURUSD=X`` symbol convention. ``fetch_bars_range`` matches the binance_source signature,
so it drops into ``cache.get_bars(..., fetcher=fetch_bars_range)`` and feeds
``PollingBarFeed`` via ``make_yahoo_fetch_latest``.

The pure parts (`yahoo_symbol`, `chart_to_bars`, `quote_from_payload`) are unit-tested with
scripted payloads; only `_fetch_chart` performs network I/O.

Limits (measured, not realtime — Yahoo follows the 24/5 FX schedule):
  * 1m history caps at ~7 days per the API; longer windows are fetched in <=7d chunks.
  * Closed-market minutes come back as nulls and are skipped.
  * Unofficial endpoint: no SLA / commercial guarantee. Poll modestly (ideally client-side).
"""

import json
import urllib.error
import urllib.request

from ..core.model import Bar
from .binance_source import interval_ms

CHART_API = "https://query1.finance.yahoo.com/v8/finance/chart"

# Yahoo's per-interval history cap (ms). 1m is the tight one; the rest are generous.
DAY_MS = 86_400_000
MAX_SPAN_MS = {
    "1m": 7 * DAY_MS,
    "2m": 60 * DAY_MS,
    "5m": 60 * DAY_MS,
    "15m": 60 * DAY_MS,
    "30m": 60 * DAY_MS,
    "1h": 730 * DAY_MS,
    "1d": 100 * 365 * DAY_MS,
}
YAHOO_INTERVALS = set(MAX_SPAN_MS)

# Yahoo 403/429s the default Python-urllib UA; a browser-ish UA is enough.
_UA = "Mozilla/5.0 (vike-trader-app forex source)"


def yahoo_symbol(pair: str) -> str:
    """Map a 6-letter FX pair to Yahoo's symbol: ``EURUSD`` -> ``EURUSD=X`` (idempotent)."""
    p = pair.upper()
    return p if p.endswith("=X") else f"{p}=X"


def chart_to_bars(payload: dict) -> list[Bar]:
    """Map a decoded chart payload to Bars, skipping null (closed-market) rows.

    Yahoo nests one result under ``chart.result[0]`` with parallel arrays: ``timestamp``
    (epoch SECONDS, bar-open) and ``indicators.quote[0].{open,high,low,close,volume}``.
    Raises RuntimeError on an ``error`` envelope.
    """
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise RuntimeError(f"yahoo chart error: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        return []
    res = results[0]
    ts = res.get("timestamp") or []
    quotes = (res.get("indicators") or {}).get("quote") or [{}]
    q = quotes[0]
    o, h, lo, c = (q.get(k) or [] for k in ("open", "high", "low", "close"))
    v = q.get("volume") or []
    bars: list[Bar] = []
    for i, t in enumerate(ts):
        oi, hi, li, ci = o[i], h[i], lo[i], c[i]
        if None in (oi, hi, li, ci):  # closed-market minute -> no candle
            continue
        vol = v[i] if i < len(v) and v[i] is not None else 0.0
        bars.append(Bar(ts=int(t) * 1000, open=float(oi), high=float(hi),
                        low=float(li), close=float(ci), volume=float(vol)))
    return bars


def quote_from_payload(payload: dict) -> float | None:
    """The live quote from a chart payload's ``meta.regularMarketPrice`` (None if absent)."""
    results = ((payload.get("chart") or {}).get("result")) or []
    if not results:
        return None
    price = (results[0].get("meta") or {}).get("regularMarketPrice")
    return None if price is None else float(price)


def _fetch_chart(symbol: str, interval: str, period1_s: int, period2_s: int, timeout: int = 30) -> dict:
    """Fetch one chart window ``[period1_s, period2_s]`` (epoch seconds) as decoded JSON."""
    url = (f"{CHART_API}/{symbol}?interval={interval}"
           f"&period1={period1_s}&period2={period2_s}&includePrePost=false")
    req = urllib.request.Request(url, headers={"User-Agent": _UA})  # noqa: S310 - fixed https host
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read())


def fetch_bars_range(
    pair: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    fetch_chart=None,
    progress=None,
) -> list[Bar]:
    """Fetch all bars in ``[start_ms, end_ms]``, chunked to Yahoo's per-interval span cap.

    Signature matches binance_source so it plugs into ``cache.get_bars``. ``fetch_chart`` is
    the injected window fetcher (defaults to the live HTTP call), enabling network-free tests.
    """
    if interval not in YAHOO_INTERVALS:
        raise ValueError(f"unsupported interval {interval!r}; expected one of {sorted(YAHOO_INTERVALS)}")
    fetch = fetch_chart if fetch_chart is not None else _fetch_chart
    symbol = yahoo_symbol(pair)
    span = MAX_SPAN_MS[interval]
    by_ts: dict[int, Bar] = {}
    cursor = start_ms
    while cursor <= end_ms:
        win_end = min(cursor + span, end_ms)
        try:
            payload = fetch(symbol, interval, cursor // 1000, win_end // 1000 + 1)
        except urllib.error.HTTPError as e:
            if e.code != 422:  # 422 = interval unsupported for this (old) window
                raise
            # Yahoo only retains intraday history for a limited window; deeper ranges
            # are Dukascopy's job. Treat as "no data here" so cache can fall back.
            payload = {"chart": {"result": []}}
        for b in chart_to_bars(payload):
            if start_ms <= b.ts <= end_ms:
                by_ts[b.ts] = b
        if progress:
            progress(min(win_end, end_ms), start_ms, end_ms)
        cursor = win_end + 1
    return [by_ts[t] for t in sorted(by_ts)]


def fetch_quote(pair: str, fetch_chart=None) -> float | None:
    """Current quote for ``pair`` -> ``meta.regularMarketPrice`` (present even on weekends).

    The window is only there to make a valid request; ask for the last day (UTC epoch s).
    """
    import time

    fetch = fetch_chart if fetch_chart is not None else _fetch_chart
    now = int(time.time())
    payload = fetch(yahoo_symbol(pair), "1m", now - 86_400, now)
    return quote_from_payload(payload)


def make_yahoo_fetch_latest(pair: str, interval: str, lookback: int = 5, fetch_chart=None):
    """Build a zero-arg ``fetch_latest`` for ``PollingBarFeed`` (last ``lookback`` intervals).

    Mirrors ``polling_feed.make_vike_fetch_latest`` so the forex feed swaps in cleanly.
    """
    import time

    step = interval_ms(interval)

    def fetch_latest() -> list[Bar]:
        now = int(time.time() * 1000)
        start = now - lookback * step
        return fetch_bars_range(pair, interval, start, now, fetch_chart=fetch_chart)

    return fetch_latest

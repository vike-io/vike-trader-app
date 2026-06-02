"""Deribit crypto-options provider (public REST, no API key).

One `get_book_summary_by_currency` call returns the whole chain for a currency
(bid/ask/mark/mark_iv/OI/volume/underlying_price). Pure parse helpers are unit-
tested with captured dicts; the HTTP method is a thin shell with a short cache so
`list_expiries` + `fetch_chain` share a single request.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request

from .greeks import enrich_quote, years_to_expiry
from .model import Expiry, OptionChain, OptionQuote, StrikeRow, limit_strikes, make_expiry

_BASE = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"
_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}
_NAME_RE = re.compile(r"^([A-Z]+)-(\d{1,2})([A-Z]{3})(\d{2})-(\d+(?:\.\d+)?)-([CP])$")
_CACHE_TTL_MS = 5_000


def _now_ms() -> int:
    return int(time.time() * 1000)


def parse_instrument_name(name: str) -> tuple[str, str, float, str] | None:
    """`'BTC-27JUN26-100000-C'` -> `('BTC', '2026-06-27', 100000.0, 'C')`; None if not an option."""
    m = _NAME_RE.match(name or "")
    if not m:
        return None
    cur, day, mon, yr, strike, typ = m.groups()
    if mon not in _MONTHS:
        return None
    date_iso = f"20{yr}-{_MONTHS[mon]:02d}-{int(day):02d}"
    return cur, date_iso, float(strike), typ


def list_expiries_from_summary(rows: list[dict], now_ms: int) -> list[Expiry]:
    """Distinct option expiries present in a book-summary payload, ascending."""
    dates: set[str] = set()
    for r in rows:
        parsed = parse_instrument_name(r.get("instrument_name", ""))
        if parsed:
            dates.add(parsed[1])
    return [make_expiry(d, now_ms) for d in sorted(dates)]


def build_chain_from_summary(
    currency: str, rows: list[dict], expiry_iso: str, now_ms: int,
) -> OptionChain:
    """Group a book-summary payload into an `OptionChain` for one expiry, greeks enriched."""
    t = years_to_expiry(expiry_iso, now_ms)
    spot: float | None = None
    by_strike: dict[float, dict[str, OptionQuote]] = {}
    for r in rows:
        parsed = parse_instrument_name(r.get("instrument_name", ""))
        if not parsed:
            continue
        _cur, e_iso, strike, typ = parsed
        if e_iso != expiry_iso:
            continue
        if spot is None and r.get("underlying_price") is not None:
            spot = float(r["underlying_price"])
        iv = r.get("mark_iv")
        q = OptionQuote(
            strike=strike, type=typ,
            bid=r.get("bid_price"), ask=r.get("ask_price"), last=None,
            mark=r.get("mark_price"),
            iv=(iv / 100.0) if iv is not None else None,
            open_interest=r.get("open_interest"), volume=r.get("volume"),
        )
        q = enrich_quote(q, spot, t)
        by_strike.setdefault(strike, {})[typ] = q
    chain_rows = tuple(
        StrikeRow(strike=s, call=qs.get("C"), put=qs.get("P"))
        for s, qs in sorted(by_strike.items())
    )
    return OptionChain(
        underlying=currency, asset_class="crypto", underlying_price=spot,
        expiry=make_expiry(expiry_iso, now_ms), asof_ms=now_ms, source="deribit",
        rows=chain_rows,
    )


def _http_get_json(url: str, timeout: float = 10.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - fixed https host
        return json.loads(resp.read().decode("utf-8"))


class DeribitOptionsProvider:
    """OptionsProvider for Deribit BTC/ETH/SOL options."""

    name = "deribit"
    asset_class = "crypto"

    def __init__(self) -> None:
        self._cache: dict[str, tuple[int, list[dict]]] = {}

    def list_underlyings(self) -> list[str]:
        return ["BTC", "ETH", "SOL"]

    def _summary(self, currency: str, now_ms: int) -> list[dict]:
        cached = self._cache.get(currency)
        if cached and now_ms - cached[0] < _CACHE_TTL_MS:
            return cached[1]
        data = _http_get_json(f"{_BASE}?currency={currency}&kind=option")
        rows = data.get("result", []) or []
        self._cache[currency] = (now_ms, rows)
        return rows

    def list_expiries(self, underlying: str) -> list[Expiry]:
        now = _now_ms()
        return list_expiries_from_summary(self._summary(underlying, now), now)

    def fetch_chain(self, underlying: str, expiry: Expiry, strikes: int | None = None) -> OptionChain:
        now = _now_ms()
        chain = build_chain_from_summary(underlying, self._summary(underlying, now), expiry.date, now)
        return limit_strikes(chain, strikes)

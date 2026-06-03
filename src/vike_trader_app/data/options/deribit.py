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
# Two instrument-name shapes: legacy coin-settled "BTC-27JUN26-100000-C" and the newer USDC-margined
# altcoin form "SOL_USDC-26JUN26-90-P" (the base carries a _USDC/_USDT settlement suffix). The suffix
# is captured and discarded so the base coin normalizes to "SOL"/"BTC".
_NAME_RE = re.compile(
    r"^([A-Z]+)(?:_USD[CT])?-(\d{1,2})([A-Z]{3})(\d{2})-(\d+(?:\.\d+)?)-([CP])$")
# Currency to query Deribit's book-summary with: BTC/ETH have their own coin-settled books; the
# altcoins (SOL, and others) are listed only under the shared USDC book, so we query USDC and then
# keep just the rows for the requested base coin.
_BOOK_CURRENCY = {"BTC": "BTC", "ETH": "ETH", "SOL": "USDC"}
_CACHE_TTL_MS = 5_000


def _now_ms() -> int:
    return int(time.time() * 1000)


def _usd(v: float | None, scale: float) -> float | None:
    """Deribit quotes option premiums in coin units (fractions of BTC/ETH/SOL); scale to USD by
    the underlying price so they line up with the USD Theor/Strike/Distance columns."""
    return float(v) * scale if (v is not None and scale) else None


def parse_instrument_name(name: str) -> tuple[str, str, float, str] | None:
    """`'BTC-27JUN26-100000-C'` -> `('BTC', '2026-06-27', 100000.0, 'C')`; None if not an option."""
    m = _NAME_RE.match(name or "")
    if not m:
        return None
    cur, day, mon, yr, strike, typ = m.groups()
    if mon not in _MONTHS:  # regex allows any [A-Z]{3}; reject non-month tokens
        return None
    date_iso = f"20{yr}-{_MONTHS[mon]:02d}-{int(day):02d}"
    return cur, date_iso, float(strike), typ


def list_expiries_from_summary(
    rows: list[dict], now_ms: int, currency: str | None = None,
) -> list[Expiry]:
    """Distinct option expiries present in a book-summary payload, ascending. When `currency` is
    given (e.g. "SOL"), only that base coin's instruments count — the shared USDC book mixes
    several coins, so without this filter SOL's expiries would pick up BTC/ETH/XRP dates."""
    dates: set[str] = set()
    for r in rows:
        parsed = parse_instrument_name(r.get("instrument_name", ""))
        if parsed and (currency is None or parsed[0] == currency):
            dates.add(parsed[1])
    return [make_expiry(d, now_ms) for d in sorted(dates)]


def build_chain_from_summary(
    currency: str, rows: list[dict], expiry_iso: str, now_ms: int, usd_quoted: bool = False,
) -> OptionChain:
    """Group a book-summary payload into an `OptionChain` for one expiry, greeks enriched.

    `usd_quoted` selects the premium unit convention: the coin-settled BTC/ETH books quote premiums
    in COIN units (scaled to USD by the underlying price), while the USDC-margined altcoin book
    (SOL etc.) already quotes premiums in USD — those pass through unscaled.
    """
    t = years_to_expiry(expiry_iso, now_ms)
    spot: float | None = None
    by_strike: dict[float, dict[str, OptionQuote]] = {}
    for r in rows:
        parsed = parse_instrument_name(r.get("instrument_name", ""))
        if not parsed:
            continue
        base, e_iso, strike, typ = parsed
        # The USDC book mixes coins (BTC_USDC/SOL_USDC/XRP_USDC); keep only the requested coin.
        if base != currency or e_iso != expiry_iso:
            continue
        if spot is None and r.get("underlying_price") is not None:
            spot = float(r["underlying_price"])
        iv = r.get("mark_iv")
        # Coin-settled premiums arrive in coin units -> scale to USD by this row's underlying price
        # (fall back to the chain spot). USDC-margined premiums are already USD -> scale 1.0.
        # Greeks/Theor come from IV+spot, so this only affects the dollar columns.
        px = r.get("underlying_price")
        scale = 1.0 if usd_quoted else (float(px) if px is not None else (spot or 0.0))
        q = OptionQuote(
            strike=strike, type=typ,
            bid=_usd(r.get("bid_price"), scale), ask=_usd(r.get("ask_price"), scale), last=None,
            mark=_usd(r.get("mark_price"), scale),
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
        return json.loads(resp.read())


class DeribitOptionsProvider:
    """OptionsProvider for Deribit BTC/ETH/SOL options."""

    name = "deribit"
    asset_class = "crypto"

    def __init__(self) -> None:
        self._cache: dict[str, tuple[int, list[dict]]] = {}

    def list_underlyings(self) -> list[str]:
        return ["BTC", "ETH", "SOL"]

    def _summary(self, underlying: str, now_ms: int) -> list[dict]:
        # SOL (and other altcoins) live in the shared USDC book, not a same-named book; BTC/ETH
        # keep their own coin-settled books. _BOOK_CURRENCY maps the coin to the book to query.
        book = _BOOK_CURRENCY.get(underlying, underlying)
        cached = self._cache.get(book)
        if cached and now_ms - cached[0] < _CACHE_TTL_MS:
            return cached[1]
        data = _http_get_json(f"{_BASE}?currency={book}&kind=option")
        rows = data.get("result", []) or []
        self._cache[book] = (now_ms, rows)
        return rows

    def list_expiries(self, underlying: str) -> list[Expiry]:
        now = _now_ms()
        return list_expiries_from_summary(self._summary(underlying, now), now, underlying)

    def fetch_chain(self, underlying: str, expiry: Expiry, strikes: int | None = None) -> OptionChain:
        now = _now_ms()
        usd_quoted = _BOOK_CURRENCY.get(underlying, underlying) == "USDC"
        chain = build_chain_from_summary(
            underlying, self._summary(underlying, now), expiry.date, now, usd_quoted)
        return limit_strikes(chain, strikes)

"""Polygon.io stock/index-options provider.

Uses the options-chain **snapshot** (`/v3/snapshot/options/{underlying}`), which returns
per-contract bid/ask, implied volatility, greeks (Δ/Γ/Θ/V) and open interest in one call —
so greeks come straight from Polygon, no local Black–Scholes needed. Expiries come from the
reference-contracts endpoint.

Note on tiers: the snapshot/quotes endpoints require a paid Polygon "Options" entitlement;
the free tier returns 403 for them (only reference + EOD aggregates are free). The pure
parser is unit-tested with a captured snapshot payload, so it needs no live access.

Auth: `polygon_api_key` in the environment (.env). Routed to only when opted in — see
`provider.select_provider`.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

from .model import Expiry, OptionChain, OptionQuote, StrikeRow, limit_strikes, make_expiry

_BASE = "https://api.polygon.io"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _num(value: object) -> float | None:
    """Coerce a JSON number to float, or None if absent/not numeric."""
    return float(value) if isinstance(value, (int, float)) else None


def _quote_from_snapshot(item: dict) -> OptionQuote:
    details = item.get("details", {})
    greeks = item.get("greeks") or {}
    strike = float(details["strike_price"])
    typ = "C" if details.get("contract_type") == "call" else "P"
    bid, ask = _num((item.get("last_quote") or {}).get("bid")), _num((item.get("last_quote") or {}).get("ask"))
    mark = _num((item.get("last_quote") or {}).get("midpoint"))
    if mark is None and bid and ask:
        mark = (bid + ask) / 2.0
    spot = _num((item.get("underlying_asset") or {}).get("price"))
    itm = None if spot is None else (spot > strike if typ == "C" else spot < strike)
    return OptionQuote(
        strike=strike, type=typ,
        bid=bid, ask=ask, last=_num((item.get("last_trade") or {}).get("price")), mark=mark,
        iv=_num(item.get("implied_volatility")),
        open_interest=_num(item.get("open_interest")),
        volume=_num((item.get("day") or {}).get("volume")),
        delta=_num(greeks.get("delta")), gamma=_num(greeks.get("gamma")),
        theta=_num(greeks.get("theta")), vega=_num(greeks.get("vega")),
        in_the_money=itm,
    )


def build_chain_from_snapshot(
    underlying: str, results: list[dict], expiry_iso: str, now_ms: int,
) -> OptionChain:
    """Group a Polygon options-snapshot payload into an `OptionChain` for one expiry."""
    spot: float | None = None
    by_strike: dict[float, dict[str, OptionQuote]] = {}
    for item in results:
        details = item.get("details", {})
        if details.get("expiration_date") != expiry_iso or "strike_price" not in details:
            continue
        if spot is None:
            spot = _num((item.get("underlying_asset") or {}).get("price"))
        q = _quote_from_snapshot(item)
        by_strike.setdefault(q.strike, {})[q.type] = q
    rows = tuple(
        StrikeRow(strike=s, call=qs.get("C"), put=qs.get("P"))
        for s, qs in sorted(by_strike.items())
    )
    return OptionChain(
        underlying=underlying, asset_class="equity", underlying_price=spot,
        expiry=make_expiry(expiry_iso, now_ms), asof_ms=now_ms, source="polygon", rows=rows,
    )


class PolygonOptionsProvider:
    """OptionsProvider for equity/index options via Polygon.io (paid options entitlement)."""

    name = "polygon"
    asset_class = "equity"
    DEFAULT_UNDERLYINGS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA"]

    def __init__(self, api_key: str | None = None) -> None:
        self._key = api_key or os.environ.get("polygon_api_key", "")

    def list_underlyings(self) -> list[str]:
        return list(self.DEFAULT_UNDERLYINGS)

    def _get(self, path: str) -> dict:
        req = urllib.request.Request(f"{_BASE}{path}", headers={"Authorization": f"Bearer {self._key}"})
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - fixed https host
            return json.loads(resp.read())

    def list_expiries(self, underlying: str) -> list[Expiry]:
        path = (f"/v3/reference/options/contracts?underlying_ticker={underlying}"
                f"&expired=false&sort=expiration_date&order=asc&limit=1000")
        data = self._get(path)
        dates = {c["expiration_date"] for c in data.get("results", []) if c.get("expiration_date")}
        now = _now_ms()
        return [make_expiry(d, now) for d in sorted(dates)]

    def fetch_chain(self, underlying: str, expiry: Expiry, strikes: int | None = None) -> OptionChain:
        path = (f"/v3/snapshot/options/{underlying}"
                f"?expiration_date={expiry.date}&limit=250")
        data = self._get(path)
        chain = build_chain_from_snapshot(underlying, data.get("results", []), expiry.date, _now_ms())
        return limit_strikes(chain, strikes)

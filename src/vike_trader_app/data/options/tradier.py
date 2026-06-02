"""Tradier stock/index-options provider (free sandbox).

Tradier's developer **sandbox** is free with no funding/credit card and serves delayed option
chains **with greeks + IV** (via ORATS; greeks refresh ~hourly). The chain response carries no
underlying price, so we fetch the spot from the quotes endpoint for ATM-centering/Distance.
Auth: `tradier_token` in the env (a sandbox token). Routed to only when opted in via
`options_stock_provider=tradier` — see `provider.select_provider`.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

from .model import Expiry, OptionChain, OptionQuote, StrikeRow, limit_strikes, make_expiry

_BASE = "https://sandbox.tradier.com/v1/markets"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _num(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _mid(bid: float | None, ask: float | None) -> float | None:
    return (bid + ask) / 2.0 if (bid and ask and bid > 0 and ask > 0) else None


def build_chain_from_options(underlying: str, options: list[dict], expiry_iso: str,
                             now_ms: int, spot: float | None) -> OptionChain:
    """Group Tradier's `options.option[]` list into an `OptionChain` (greeks straight from feed)."""
    by_strike: dict[float, dict[str, OptionQuote]] = {}
    for o in options:
        strike = float(o["strike"])
        typ = "C" if o.get("option_type") == "call" else "P"
        greeks = o.get("greeks") or {}
        bid, ask = _num(o.get("bid")), _num(o.get("ask"))
        itm = None if spot is None else (spot > strike if typ == "C" else spot < strike)
        by_strike.setdefault(strike, {})[typ] = OptionQuote(
            strike=strike, type=typ, bid=bid, ask=ask, last=_num(o.get("last")),
            mark=_mid(bid, ask), iv=_num(greeks.get("mid_iv")),
            open_interest=_num(o.get("open_interest")), volume=_num(o.get("volume")),
            delta=_num(greeks.get("delta")), gamma=_num(greeks.get("gamma")),
            theta=_num(greeks.get("theta")), vega=_num(greeks.get("vega")),
            in_the_money=itm,
        )
    rows = tuple(
        StrikeRow(strike=s, call=qs.get("C"), put=qs.get("P"))
        for s, qs in sorted(by_strike.items())
    )
    return OptionChain(
        underlying=underlying, asset_class="equity", underlying_price=spot,
        expiry=make_expiry(expiry_iso, now_ms), asof_ms=now_ms, source="tradier", rows=rows,
    )


class TradierOptionsProvider:
    """OptionsProvider for equity/index options via the free Tradier sandbox."""

    name = "tradier"
    asset_class = "equity"
    DEFAULT_UNDERLYINGS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA"]

    def __init__(self, token: str | None = None) -> None:
        self._token = token or os.environ.get("tradier_token", "")

    def list_underlyings(self) -> list[str]:
        return list(self.DEFAULT_UNDERLYINGS)

    def _get(self, path: str) -> dict:
        req = urllib.request.Request(
            f"{_BASE}{path}",
            headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - fixed https host
            return json.loads(resp.read())

    def _spot(self, underlying: str) -> float | None:
        try:
            quote = ((self._get(f"/quotes?symbols={underlying}").get("quotes") or {}).get("quote"))
        except Exception:  # noqa: BLE001 - spot is optional; chain still renders without it
            return None
        if isinstance(quote, list):
            quote = quote[0] if quote else None
        return _num((quote or {}).get("last"))

    def list_expiries(self, underlying: str) -> list[Expiry]:
        data = self._get(f"/options/expirations?symbol={underlying}")
        dates = (data.get("expirations") or {}).get("date") or []
        if isinstance(dates, str):
            dates = [dates]
        now = _now_ms()
        return [make_expiry(d, now) for d in dates]

    def fetch_chain(self, underlying: str, expiry: Expiry, strikes: int | None = None) -> OptionChain:
        spot = self._spot(underlying)
        data = self._get(f"/options/chains?symbol={underlying}&expiration={expiry.date}&greeks=true")
        options = (data.get("options") or {}).get("option") or []
        if isinstance(options, dict):
            options = [options]
        chain = build_chain_from_options(underlying, options, expiry.date, _now_ms(), spot)
        return limit_strikes(chain, strikes)

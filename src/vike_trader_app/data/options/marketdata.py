"""marketdata.app stock/index-options provider.

Its options-chain endpoint returns real bid/ask, implied volatility, greeks (Δ/Γ/Θ/V) and
open interest — and the **free tier includes delayed data** (unlike Polygon's free tier, which
403s on the snapshot). Response is columnar (parallel arrays), so the pure parser zips them
into `OptionChain`; greeks come straight from the feed. Auth: `marketdata_api_key` in the env.

Routed to only when opted in via `options_stock_provider=marketdata` — see `provider.select_provider`.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

from .model import Expiry, OptionChain, OptionQuote, StrikeRow, limit_strikes, make_expiry

_BASE = "https://api.marketdata.app/v1/options"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _num(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _at(arr: list, i: int):
    return arr[i] if i < len(arr) else None


def build_chain_from_payload(underlying: str, data: dict, expiry_iso: str, now_ms: int) -> OptionChain:
    """Zip marketdata.app's columnar chain payload into an `OptionChain` (greeks straight from feed)."""
    def col(name: str) -> list:
        return data.get(name) or []

    strikes = col("strike")
    sides, bids, asks, mids, lasts = (col(k) for k in ("side", "bid", "ask", "mid", "last"))
    ivs, ois, vols = (col(k) for k in ("iv", "openInterest", "volume"))
    deltas, gammas, thetas, vegas, itms = (col(k) for k in ("delta", "gamma", "theta", "vega", "inTheMoney"))
    underlying_px = col("underlyingPrice")

    spot: float | None = None
    by_strike: dict[float, dict[str, OptionQuote]] = {}
    for i in range(len(strikes)):
        strike = float(strikes[i])
        typ = "C" if _at(sides, i) == "call" else "P"
        if spot is None:
            spot = _num(_at(underlying_px, i))
        itm = _at(itms, i)
        by_strike.setdefault(strike, {})[typ] = OptionQuote(
            strike=strike, type=typ,
            bid=_num(_at(bids, i)), ask=_num(_at(asks, i)), last=_num(_at(lasts, i)),
            mark=_num(_at(mids, i)), iv=_num(_at(ivs, i)),
            open_interest=_num(_at(ois, i)), volume=_num(_at(vols, i)),
            delta=_num(_at(deltas, i)), gamma=_num(_at(gammas, i)),
            theta=_num(_at(thetas, i)), vega=_num(_at(vegas, i)),
            in_the_money=bool(itm) if itm is not None else None,
        )
    rows = tuple(
        StrikeRow(strike=s, call=qs.get("C"), put=qs.get("P"))
        for s, qs in sorted(by_strike.items())
    )
    return OptionChain(
        underlying=underlying, asset_class="equity", underlying_price=spot,
        expiry=make_expiry(expiry_iso, now_ms), asof_ms=now_ms, source="marketdata", rows=rows,
    )


class MarketDataOptionsProvider:
    """OptionsProvider for equity/index options via marketdata.app (free delayed tier works)."""

    name = "marketdata"
    asset_class = "equity"
    DEFAULT_UNDERLYINGS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA"]

    def __init__(self, api_key: str | None = None) -> None:
        self._key = api_key or os.environ.get("marketdata_api_key", "")

    def list_underlyings(self) -> list[str]:
        return list(self.DEFAULT_UNDERLYINGS)

    def _get(self, path: str) -> dict:
        req = urllib.request.Request(f"{_BASE}{path}", headers={"Authorization": f"Bearer {self._key}"})
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - fixed https host
            return json.loads(resp.read())

    def list_expiries(self, underlying: str) -> list[Expiry]:
        data = self._get(f"/expirations/{underlying}/")
        now = _now_ms()
        return [make_expiry(d, now) for d in (data.get("expirations") or [])]

    def fetch_chain(self, underlying: str, expiry: Expiry, strikes: int | None = None) -> OptionChain:
        data = self._get(f"/chain/{underlying}/?expiration={expiry.date}")
        chain = build_chain_from_payload(underlying, data, expiry.date, _now_ms())
        return limit_strikes(chain, strikes)

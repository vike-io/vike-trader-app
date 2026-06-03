"""yfinance stock/index-options provider (free, ~15-min delayed).

`import yfinance` is deferred into `_ticker` so the module (and `provider.py`,
`select_provider`) import fine without the `options` extra installed; only a live
`fetch_chain`/`list_expiries` needs the dependency. Pure record parsing is unit-
tested without yfinance or pandas.
"""

from __future__ import annotations

import math
import time
from dataclasses import replace

from .greeks import enrich_quote, implied_vol, years_to_expiry
from .model import (
    Expiry, OptionChain, OptionQuote, StrikeRow, _expiry_ms, limit_strikes, make_expiry,
)

# Yahoo's free feed often returns 0 bid/ask (market closed) and a junk IV — observed values
# range from ~1e-5 up to ~0.02. Real annualized option IV is effectively never this low, so
# below this floor we treat Yahoo's IV as missing and infer one from the mark/last price.
_DEGENERATE_IV = 0.05


def _now_ms() -> int:
    return int(time.time() * 1000)


def _mark_from(bid: float | None, ask: float | None, last: float | None) -> float | None:
    """Bid/ask midpoint when both are live, else the last trade (Yahoo zeroes bid/ask off-hours)."""
    if bid and ask and bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return last


def _quote_from_record(rec: dict, typ: str) -> OptionQuote:
    bid, ask, last = rec.get("bid"), rec.get("ask"), rec.get("lastPrice")
    return OptionQuote(
        strike=float(rec["strike"]), type=typ,
        bid=bid, ask=ask, last=last, mark=_mark_from(bid, ask, last),
        iv=rec.get("impliedVolatility"),
        open_interest=rec.get("openInterest"), volume=rec.get("volume"),
        in_the_money=rec.get("inTheMoney"),
    )


def _enrich(q: OptionQuote, S: float | None, t: float) -> OptionQuote:
    """Enrich greeks, inferring IV from the mark price when Yahoo's IV is degenerate."""
    if (q.iv is None or q.iv < _DEGENERATE_IV) and q.mark and S and t > 0:
        inferred = implied_vol(q.mark, S, q.strike, t, q.type)
        if inferred is not None:
            q = replace(q, iv=inferred)
    return enrich_quote(q, S, t)


def build_chain_from_records(
    underlying: str, expiry_iso: str, calls: list[dict], puts: list[dict],
    underlying_price: float | None, now_ms: int,
) -> OptionChain:
    """Group yfinance calls/puts records into an `OptionChain`, greeks enriched."""
    t = years_to_expiry(expiry_iso, now_ms)
    by_strike: dict[float, dict[str, OptionQuote]] = {}
    for rec in calls:
        by_strike.setdefault(float(rec["strike"]), {})["C"] = _enrich(
            _quote_from_record(rec, "C"), underlying_price, t)
    for rec in puts:
        by_strike.setdefault(float(rec["strike"]), {})["P"] = _enrich(
            _quote_from_record(rec, "P"), underlying_price, t)
    rows = tuple(
        StrikeRow(strike=s, call=qs.get("C"), put=qs.get("P"))
        for s, qs in sorted(by_strike.items())
    )
    return OptionChain(
        underlying=underlying, asset_class="equity", underlying_price=underlying_price,
        expiry=make_expiry(expiry_iso, now_ms), asof_ms=now_ms, source="yfinance", rows=rows,
    )


class YFinanceOptionsProvider:
    """OptionsProvider for equity/index options via yfinance."""

    name = "yfinance"
    asset_class = "equity"
    DEFAULT_UNDERLYINGS = ["^VIX", "SPY", "QQQ", "AAPL"]

    def list_underlyings(self) -> list[str]:
        return list(self.DEFAULT_UNDERLYINGS)

    def _ticker(self, underlying: str):
        import yfinance as yf  # lazy: keeps the package importable without the extra
        return yf.Ticker(underlying)

    def list_expiries(self, underlying: str) -> list[Expiry]:
        now = _now_ms()
        # Yahoo's `.options` can carry a just-passed expiry alongside today's; both clamp to DTE 0
        # and render a SECOND "0DTE" pill. Drop already-expired dates and de-dupe so each expiry
        # (and the single 0DTE) appears once. `_expiry_ms` settles at 08:00 UTC, matching make_expiry.
        seen: set[str] = set()
        out: list[Expiry] = []
        for e in self._ticker(underlying).options:
            if e in seen or _expiry_ms(e) + 86_400_000 <= now:  # past its settle day -> expired
                continue
            seen.add(e)
            out.append(make_expiry(e, now))
        return out

    def fetch_chain(self, underlying: str, expiry: Expiry, strikes: int | None = None) -> OptionChain:
        now = _now_ms()
        tk = self._ticker(underlying)
        oc = tk.option_chain(expiry.date)
        try:
            _v = float(tk.fast_info["lastPrice"])
            spot = _v if math.isfinite(_v) else None  # after-hours can yield NaN -> drop it
        except Exception:  # noqa: BLE001 - fast_info shape varies; spot is optional
            spot = None
        chain = build_chain_from_records(
            underlying, expiry.date, oc.calls.to_dict("records"), oc.puts.to_dict("records"),
            spot, now)
        return limit_strikes(chain, strikes)

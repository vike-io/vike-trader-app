"""yfinance stock/index-options provider (free, ~15-min delayed).

`import yfinance` is deferred into `_ticker` so the module (and `provider.py`,
`select_provider`) import fine without the `options` extra installed; only a live
`fetch_chain`/`list_expiries` needs the dependency. Pure record parsing is unit-
tested without yfinance or pandas.
"""

from __future__ import annotations

import time

from .greeks import enrich_quote, years_to_expiry
from .model import Expiry, OptionChain, OptionQuote, StrikeRow, limit_strikes, make_expiry


def _now_ms() -> int:
    return int(time.time() * 1000)


def _quote_from_record(rec: dict, typ: str) -> OptionQuote:
    return OptionQuote(
        strike=float(rec["strike"]), type=typ,
        bid=rec.get("bid"), ask=rec.get("ask"), last=rec.get("lastPrice"), mark=None,
        iv=rec.get("impliedVolatility"),
        open_interest=rec.get("openInterest"), volume=rec.get("volume"),
        in_the_money=rec.get("inTheMoney"),
    )


def build_chain_from_records(
    underlying: str, expiry_iso: str, calls: list[dict], puts: list[dict],
    underlying_price: float | None, now_ms: int,
) -> OptionChain:
    """Group yfinance calls/puts records into an `OptionChain`, greeks enriched."""
    t = years_to_expiry(expiry_iso, now_ms)
    by_strike: dict[float, dict[str, OptionQuote]] = {}
    for rec in calls:
        by_strike.setdefault(float(rec["strike"]), {})["C"] = enrich_quote(
            _quote_from_record(rec, "C"), underlying_price, t)
    for rec in puts:
        by_strike.setdefault(float(rec["strike"]), {})["P"] = enrich_quote(
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
        return [make_expiry(e, now) for e in self._ticker(underlying).options]

    def fetch_chain(self, underlying: str, expiry: Expiry, strikes: int | None = None) -> OptionChain:
        now = _now_ms()
        tk = self._ticker(underlying)
        oc = tk.option_chain(expiry.date)
        try:
            spot = float(tk.fast_info["lastPrice"])
        except Exception:  # noqa: BLE001 - fast_info shape varies; spot is optional
            spot = None
        chain = build_chain_from_records(
            underlying, expiry.date, oc.calls.to_dict("records"), oc.puts.to_dict("records"),
            spot, now)
        return limit_strikes(chain, strikes)

"""Normalized options-chain data model + small pure helpers.

Providers translate their native payloads into these types; the UI only ever
consumes `OptionChain` / `Expiry`, so it never knows which feed produced them.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Literal

_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@dataclass(frozen=True)
class OptionQuote:
    strike: float
    type: Literal["C", "P"]
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    mark: float | None = None
    iv: float | None = None         # decimal (0.62 == 62%)
    open_interest: float | None = None
    volume: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None      # per calendar day
    vega: float | None = None       # per 1pp change in IV (0.01 decimal)
    in_the_money: bool | None = None


@dataclass(frozen=True)
class Expiry:
    date: str                       # ISO "YYYY-MM-DD"
    dte: int                        # days to expiry (>= 0)
    label: str                      # display, e.g. "02 Jul" / "0DTE"


@dataclass(frozen=True)
class StrikeRow:
    strike: float
    call: OptionQuote | None = None
    put: OptionQuote | None = None


@dataclass(frozen=True)
class OptionChain:
    underlying: str                 # "BTC", "^VIX"
    asset_class: Literal["crypto", "equity"]
    underlying_price: float | None
    expiry: Expiry
    asof_ms: int                    # snapshot epoch ms (UTC)
    source: str                     # "deribit" | "yfinance"
    rows: tuple[StrikeRow, ...]     # ascending by strike


def _expiry_ms(date_iso: str, hour_utc: int = 8) -> int:
    """Epoch ms of an option expiry (Deribit/most US options settle ~08:00 UTC)."""
    y, m, d = (int(p) for p in date_iso.split("-"))
    return int(datetime(y, m, d, hour_utc, tzinfo=timezone.utc).timestamp() * 1000)


def make_expiry(date_iso: str, now_ms: int) -> Expiry:
    """Build an `Expiry` (DTE + human label) for an ISO date relative to `now_ms`."""
    dte = max(int((_expiry_ms(date_iso) - now_ms) // (86_400 * 1000)), 0)
    _, m, d = (int(p) for p in date_iso.split("-"))
    label = "0DTE" if dte == 0 else f"{d:02d} {_MONTH_ABBR[m - 1]}"
    return Expiry(date=date_iso, dte=dte, label=label)


def limit_strikes(chain: OptionChain, n: int | None) -> OptionChain:
    """Window the chain to ``n`` strikes ABOVE and ``n`` strikes BELOW the at-the-money strike
    (the "±n strikes" toolbar selection): the ATM strike (first strike >= spot) plus its n nearest
    neighbours on each side, so ±3 shows 3 rows above + 3 below, ±6 shows 6 each side, etc.

    Returns the chain unchanged for n=None / n<=0 / no spot. Rows stay ascending by strike.
    """
    if n is None or n <= 0 or chain.underlying_price is None:
        return chain
    spot = chain.underlying_price
    rows = chain.rows  # already ascending by strike
    # ATM anchor: the first strike at/above spot (clamp to the last strike if spot tops the ladder).
    atm = next((i for i, r in enumerate(rows) if r.strike >= spot), len(rows) - 1)
    lo, hi = max(atm - n, 0), min(atm + n + 1, len(rows))
    if lo == 0 and hi == len(rows):
        return chain
    return replace(chain, rows=tuple(rows[lo:hi]))

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
    instrument_name: str | None = None   # venue contract id (Deribit); None for yfinance/equity


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
    """Build an `Expiry` (DTE + human label) for an ISO date relative to `now_ms`.

    DTE is a CALENDAR-day difference (expiry's UTC date minus today's UTC date), not a floored
    elapsed-ms count: late in the UTC day the ms-floor rounds BOTH today and tomorrow to 0, so the
    expiry strip rendered two "0DTE" pills. With the calendar diff only the contract that actually
    settles today reads 0DTE; tomorrow is 1 (and shows its date label).
    """
    y, m, d = (int(p) for p in date_iso.split("-"))
    exp_date = datetime(y, m, d, tzinfo=timezone.utc).date()
    today = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).date()
    dte = max((exp_date - today).days, 0)
    label = "0DTE" if dte == 0 else f"{d:02d} {_MONTH_ABBR[m - 1]}"
    return Expiry(date=date_iso, dte=dte, label=label)


def limit_strikes(chain: OptionChain, n: int | None) -> OptionChain:
    """Window the chain to ``n`` strikes BELOW the spot price and ``n`` strikes AT/ABOVE it — a
    symmetric "±n strikes" window of 2n rows centred on the spot marker, so ±3 shows exactly 3 rows
    above the spot band and 3 below, ±6 shows 6 each side, etc. (The spot price falls between the
    two middle strikes, so the marker band lands dead-centre with n rows on each side.)

    Returns the chain unchanged for n=None / n<=0 / no spot. Rows stay ascending by strike.
    """
    if n is None or n <= 0 or chain.underlying_price is None:
        return chain
    spot = chain.underlying_price
    rows = chain.rows  # already ascending by strike
    # Split point: the first strike at/above spot. Take n strikes below it and n at/above it, so the
    # spot marker (inserted at this split) gets exactly n rows on each side — no lopsided +1 strike.
    split = next((i for i, r in enumerate(rows) if r.strike >= spot), len(rows))
    lo, hi = max(split - n, 0), min(split + n, len(rows))
    if lo == 0 and hi == len(rows):
        return chain
    return replace(chain, rows=tuple(rows[lo:hi]))

"""Equity calendars (Earnings / Dividends / IPO) for the Calendar space.

Different data than the macro Economic calendar — company-level events from Finnhub
(earnings; free tier), Financial Modeling Prep (dividends; FMP free tier) and Nasdaq's
public IPO calendar (IPOs; keyless, dense, forward-looking). Each provider takes an
injectable `http` (tests pass a fake) and reads its key from the environment; a missing
key or a flaky source yields an empty list, never an exception.

IPO uses Nasdaq as the primary source with Finnhub as the fallback: Finnhub's free-tier IPO
calendar is sparse and forward-thin (verified live 2026-06: 9 rows this week, then 1/0/0 for
the next 3 weeks), whereas Nasdaq's keyless month feed is far denser and forward-looking
(12 rows this week, with priced/filed history) — FMP's IPO endpoint is paywalled (HTTP 402)
on the free tier, so it is not used for IPOs.

Verified live (2026-06): Finnhub earnings ~234/2wk; FMP dividends with yield; Nasdaq IPO.
Neither vendor exposes options data on these free tiers — options stay with the existing
Deribit/yfinance feature.
"""
from __future__ import annotations

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .http import http_get_json
from .store import DB_DEFAULT, open_db as _open_calendar_db

FINNHUB_EARNINGS = "https://finnhub.io/api/v1/calendar/earnings?from={frm}&to={to}&token={key}"
FINNHUB_IPO = "https://finnhub.io/api/v1/calendar/ipo?from={frm}&to={to}&token={key}"
FINNHUB_PROFILE = "https://finnhub.io/api/v1/stock/profile2?symbol={sym}&token={key}"
FMP_DIVIDENDS = ("https://financialmodelingprep.com/stable/dividends-calendar"
                 "?from={frm}&to={to}&apikey={key}")
# Nasdaq's public IPO calendar is month-based (?date=YYYY-MM) and needs no API key.
NASDAQ_IPO = "https://api.nasdaq.com/api/ipo/calendar?date={month}"
# api.nasdaq.com rejects the default urllib agent; mimic a browser like the dividend feed does not need to.
_NASDAQ_HEADERS = {"User-Agent": "Mozilla/5.0 (vike-trader-app)", "Accept": "application/json"}
# LEGACY profile-cache file — read only by the calendar store's one-time sweep into the DB.
_PROFILE_CACHE = "storage/calendar/profiles.json"
# Where the cache lives now: the ``calendar_profiles`` table in the app DB (state-in-DB rule).
# Module-level so tests can repoint it at a tmp file (alongside _PROFILE_CACHE).
_PROFILE_DB = DB_DEFAULT


def _num(v):
    try:
        return float(v) if v not in (None, "", "None") else None
    except (TypeError, ValueError):
        return None


def _num_str(v):
    """Numeric from a display string like '7,900,000' or '$181,700,000' (Nasdaq fields)."""
    if v in (None, "", "None"):
        return None
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def _norm_mdy(s: str) -> str:
    """Nasdaq dates are 'M/D/YYYY'; return ISO 'YYYY-MM-DD' ('' if unparseable)."""
    try:
        return datetime.strptime(s, "%m/%d/%Y").strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""


def _months_between(frm: str, to: str) -> list[str]:
    """The 'YYYY-MM' month tags spanned by an inclusive [frm, to] ISO-date range (1–2 for a
    week, occasionally 2 when the week straddles a month boundary)."""
    a, b = date.fromisoformat(frm), date.fromisoformat(to)
    out, y, m = [], a.year, a.month
    while (y, m) <= (b.year, b.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


@dataclass
class EarningsEvent:
    date: str            # YYYY-MM-DD
    symbol: str
    hour: str            # bmo (before open) / amc (after close) / dmh / ""
    eps_estimate: float | None
    eps_actual: float | None
    rev_estimate: float | None
    rev_actual: float | None
    name: str = ""               # company name (from profile2 enrichment)
    market_cap: float | None = None   # USD millions (from profile2 enrichment)

    @property
    def surprise(self) -> float | None:
        """EPS surprise %, when both estimate and actual are known."""
        if self.eps_actual is None or not self.eps_estimate:
            return None
        return (self.eps_actual - self.eps_estimate) / abs(self.eps_estimate) * 100


@dataclass
class DividendEvent:
    symbol: str
    ex_date: str         # YYYY-MM-DD
    pay_date: str
    amount: float | None
    yield_pct: float | None
    frequency: str
    name: str = ""               # company name (from profile2 enrichment; FMP carries none)


@dataclass
class IpoEvent:
    date: str
    symbol: str
    name: str
    exchange: str
    price: str           # range or value, kept as the provider's display string
    shares: float | None
    status: str


class FinnhubEarnings:
    name = "Finnhub"

    def __init__(self, key: str | None = None, http=http_get_json):
        self._key = key if key is not None else os.environ.get("FINNHUB_API_KEY")
        self._http = http

    def fetch(self, frm: str, to: str) -> list[EarningsEvent]:
        if not self._key:
            return []
        try:
            rows = self._http(FINNHUB_EARNINGS.format(frm=frm, to=to, key=self._key))
            return [EarningsEvent(
                date=r.get("date", ""), symbol=r.get("symbol", ""), hour=r.get("hour", "") or "",
                eps_estimate=_num(r.get("epsEstimate")), eps_actual=_num(r.get("epsActual")),
                rev_estimate=_num(r.get("revenueEstimate")), rev_actual=_num(r.get("revenueActual")),
            ) for r in (rows or {}).get("earningsCalendar", [])]
        except Exception:  # noqa: BLE001 - a flaky source must never break the calendar
            return []


class FinnhubIpo:
    name = "Finnhub"

    def __init__(self, key: str | None = None, http=http_get_json):
        self._key = key if key is not None else os.environ.get("FINNHUB_API_KEY")
        self._http = http

    def fetch(self, frm: str, to: str) -> list[IpoEvent]:
        if not self._key:
            return []
        try:
            rows = self._http(FINNHUB_IPO.format(frm=frm, to=to, key=self._key))
            # Finnhub returns explicit JSON null for exchange/name/symbol on many rows, so a
            # `.get(key, default)` keeps the None (the key IS present) — guard each with `or ""`.
            return [IpoEvent(
                date=r.get("date", "") or "", symbol=r.get("symbol", "") or "", name=r.get("name", "") or "",
                exchange=r.get("exchange", "") or "", price=str(r.get("price", "") or ""),
                shares=_num(r.get("numberOfShares")), status=r.get("status", "") or "",
            ) for r in (rows or {}).get("ipoCalendar", [])]
        except Exception:  # noqa: BLE001
            return []


class NasdaqIpo:
    """Nasdaq's public IPO calendar — keyless, dense and forward-looking, far richer than the
    free Finnhub feed. The feed is per-month (?date=YYYY-MM); we fetch every month the requested
    [frm, to] week spans, flatten the `upcoming`/`priced`/`filed` sections into the shared
    IpoEvent shape and keep only the rows whose date falls inside the window."""

    name = "Nasdaq"
    # (section, date field, status label). `upcoming` nests its rows under `upcomingTable`.
    _SECTIONS = (("upcoming", "expectedPriceDate"), ("priced", "pricedDate"), ("filed", "filedDate"))

    def __init__(self, http=http_get_json):
        self._http = http

    def fetch(self, frm: str, to: str) -> list[IpoEvent]:
        try:
            out: list[IpoEvent] = []
            for month in _months_between(frm, to):
                data = (self._http(NASDAQ_IPO.format(month=month), headers=_NASDAQ_HEADERS) or {}).get("data") or {}
                for section, datefield in self._SECTIONS:
                    block = data.get(section) or {}
                    rows = block.get("rows") or (block.get("upcomingTable") or {}).get("rows") or []
                    for r in rows:
                        if not isinstance(r, dict):
                            continue
                        iso = _norm_mdy(r.get(datefield, "") or "")
                        if not iso or not (frm <= iso <= to):   # keep only the requested week
                            continue
                        out.append(IpoEvent(
                            date=iso, symbol=r.get("proposedTickerSymbol", "") or "",
                            name=r.get("companyName", "") or "", exchange=r.get("proposedExchange", "") or "",
                            price=str(r.get("proposedSharePrice", "") or ""),
                            shares=_num_str(r.get("sharesOffered")), status=section,
                        ))
            return out
        except Exception:  # noqa: BLE001 - a flaky source must never break the calendar
            return []


class Ipo:
    """IPO calendar with Nasdaq as the primary (dense, forward-looking, keyless) source and
    Finnhub as the fallback. Falls back when Nasdaq is unavailable/errors (returns []) so the tab
    still shows the sparse-but-present Finnhub rows rather than going empty."""

    name = "Nasdaq"

    def __init__(self, key: str | None = None, http=http_get_json):
        self._nasdaq = NasdaqIpo(http=http)
        self._finnhub = FinnhubIpo(key=key, http=http)

    def fetch(self, frm: str, to: str) -> list[IpoEvent]:
        rows = self._nasdaq.fetch(frm, to)
        return rows if rows else self._finnhub.fetch(frm, to)


class FmpDividends:
    name = "FMP"

    def __init__(self, key: str | None = None, http=http_get_json):
        self._key = key if key is not None else os.environ.get("FMP_API_KEY")
        self._http = http

    def fetch(self, frm: str, to: str) -> list[DividendEvent]:
        if not self._key:
            return []
        try:
            rows = self._http(FMP_DIVIDENDS.format(frm=frm, to=to, key=self._key))
            out = []
            for r in rows or []:
                if not isinstance(r, dict):
                    continue
                out.append(DividendEvent(
                    symbol=r.get("symbol", ""), ex_date=r.get("date", ""),
                    pay_date=r.get("paymentDate", "") or "",
                    amount=_num(r.get("dividend") if r.get("dividend") is not None else r.get("adjDividend")),
                    yield_pct=_num(r.get("yield")), frequency=r.get("frequency", "") or "",
                ))
            return out
        except Exception:  # noqa: BLE001
            return []


# ---- company profiles (name + market cap) for earnings enrichment -------------------
def _profiles_db() -> sqlite3.Connection:
    """Per-call connection to ``calendar_profiles`` in the app DB.

    Opening through the calendar store sweeps the legacy ``storage/calendar/`` JSON dir
    (including ``profiles.json``) into the DB first, so a pre-DB cache is honored on first
    use. This runs on the equity tab's fetch worker thread — safe because every call opens
    and closes its own connection (no connection object ever crosses a thread) and the
    busy timeout covers a concurrent writer.
    """
    return _open_calendar_db(_PROFILE_DB, Path(_PROFILE_CACHE).parent)


def _load_profiles() -> dict:
    try:
        with closing(_profiles_db()) as conn:
            rows = conn.execute("SELECT symbol, payload FROM calendar_profiles").fetchall()
        return {sym: json.loads(payload) for sym, payload in rows}
    except (sqlite3.Error, json.JSONDecodeError, TypeError, OSError):
        return {}


def _save_profiles(data: dict) -> None:
    try:
        rows = [(sym, json.dumps(prof)) for sym, prof in data.items()]
        with closing(_profiles_db()) as conn, conn:
            conn.executemany(
                "INSERT OR REPLACE INTO calendar_profiles (symbol, payload) VALUES (?, ?)",
                rows)
    except (sqlite3.Error, OSError):
        pass  # cache write is best-effort, like the JSON store this replaces


def profiles(symbols, *, key: str | None = None, http=http_get_json,
             max_workers: int = 4, limit: int = 12) -> dict:
    """{symbol: {'name', 'cap'}} from Finnhub profile2 — persistent-cached so each symbol is
    fetched once, fetched concurrently (bounded) the first time. Finnhub's free tier is
    ~60 req/min, so `limit` keeps each cold load's burst small; only SUCCESSFUL lookups are
    cached, so rate-limited misses retry on the next load rather than sticking as blanks."""
    key = key if key is not None else os.environ.get("FINNHUB_API_KEY")
    cache = _load_profiles()
    want = [s for s in dict.fromkeys(symbols) if s and s not in cache][:limit]
    if key and want:
        def one(sym):
            try:
                d = http(FINNHUB_PROFILE.format(sym=sym, key=key)) or {}
                cap = d.get("marketCapitalization")
                if cap:                                 # success only
                    return sym, {"name": d.get("name", ""), "cap": cap}
            except Exception:  # noqa: BLE001
                pass
            return sym, None
        got = False
        with ThreadPoolExecutor(max_workers=min(max_workers, len(want))) as ex:
            for sym, prof in ex.map(one, want):
                if prof:
                    cache[sym] = prof
                    got = True
        if got:
            _save_profiles(cache)
    return {s: cache.get(s, {}) for s in symbols}


def fetch_dividends_enriched(frm: str, to: str, *, key: str | None = None,
                             http=http_get_json) -> list[DividendEvent]:
    """Dividends for the week with the company name filled in (FMP's dividends feed carries no
    name), so the Dividends tab can show the same Symbol · Company columns as Earnings/IPO. Names
    come from the shared Finnhub profile cache; a missing key / rate-limited symbol just leaves the
    name blank (one empty cell, like an uncovered earnings row) — never an error."""
    events = FmpDividends(key=key, http=http).fetch(frm, to)
    profs = profiles([e.symbol for e in events], key=key, http=http)
    for e in events:
        e.name = (profs.get(e.symbol) or {}).get("name", "") or ""
    return events


def fetch_earnings_enriched(frm: str, to: str, *, key: str | None = None,
                            http=http_get_json) -> list[EarningsEvent]:
    """Earnings for the week, with company name + market cap filled in for the covered
    symbols — fetched biggest-first (by revenue) so the names a trader cares about get a
    market cap even when the per-load profile budget is small."""
    events = FinnhubEarnings(key=key, http=http).fetch(frm, to)
    covered = sorted((e for e in events if e.eps_estimate is not None),
                     key=lambda e: -(e.rev_estimate or 0))
    profs = profiles([e.symbol for e in covered], key=key, http=http)
    for e in events:
        p = profs.get(e.symbol) or {}
        e.name = p.get("name", "") or ""
        e.market_cap = p.get("cap")
    return events

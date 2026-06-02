"""Equity calendars (Earnings / Dividends / IPO) for the Calendar space.

Different data than the macro Economic calendar — company-level events from Finnhub
(earnings, IPOs; free tier) and Financial Modeling Prep (dividends; FMP free tier).
Each provider takes an injectable `http` (tests pass a fake) and reads its key from the
environment; a missing key or a flaky source yields an empty list, never an exception.

Verified live (2026-06): Finnhub earnings ~234/2wk, Finnhub IPO; FMP dividends with yield.
Neither vendor exposes options data on these free tiers — options stay with the existing
Deribit/yfinance feature.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .http import http_get_json

FINNHUB_EARNINGS = "https://finnhub.io/api/v1/calendar/earnings?from={frm}&to={to}&token={key}"
FINNHUB_IPO = "https://finnhub.io/api/v1/calendar/ipo?from={frm}&to={to}&token={key}"
FMP_DIVIDENDS = ("https://financialmodelingprep.com/stable/dividends-calendar"
                 "?from={frm}&to={to}&apikey={key}")


def _num(v):
    try:
        return float(v) if v not in (None, "", "None") else None
    except (TypeError, ValueError):
        return None


@dataclass
class EarningsEvent:
    date: str            # YYYY-MM-DD
    symbol: str
    hour: str            # bmo (before open) / amc (after close) / dmh / ""
    eps_estimate: float | None
    eps_actual: float | None
    rev_estimate: float | None
    rev_actual: float | None


@dataclass
class DividendEvent:
    symbol: str
    ex_date: str         # YYYY-MM-DD
    pay_date: str
    amount: float | None
    yield_pct: float | None
    frequency: str


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
            return [IpoEvent(
                date=r.get("date", ""), symbol=r.get("symbol", "") or "", name=r.get("name", ""),
                exchange=r.get("exchange", ""), price=str(r.get("price", "") or ""),
                shares=_num(r.get("numberOfShares")), status=r.get("status", ""),
            ) for r in (rows or {}).get("ipoCalendar", [])]
        except Exception:  # noqa: BLE001
            return []


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

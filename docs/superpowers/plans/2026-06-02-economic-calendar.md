# Economic Calendar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a TradingView-style Economic Calendar panel to vike-trader-app: a week-navigable, date→country-grouped table of macro events with importance, forecast/previous, live-backfilled actuals, filters, a live countdown, and expandable detail.

**Architecture:** A pluggable data layer in `src/vike_trader_app/data/calendar/` — a `ScheduleProvider` (ForexFactory weekly JSON) feeds events; a priority list of `ActualsProvider`s (FRED, BLS, BEA, Census, ECB) backfill `actual` after release; a `CalendarRepository` merges + caches them as JSON-per-ISO-week. The UI (`src/vike_trader_app/ui/economic_calendar.py`) is a `QTreeWidget` grouped Date→Country→Event with a custom delegate (importance bars, colored actuals, countdown), driven off the main thread by a `QThread` worker and a 1 s `QTimer`.

**Tech Stack:** Python 3.10+, PySide6 (Qt), stdlib `urllib.request`/`json` for HTTP (matching `data/binance_source.py`), `python-dotenv` for API keys, pytest (+ pytest-timeout) with `QT_QPA_PLATFORM=offscreen` for GUI tests.

---

## Conventions (apply to every task)

- **Branch/worktree:** all work on `worktree-economic-calendar` (already created).
- **Run a test:** `python -m pytest <path>::<name> -v`. GUI test modules set `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")` **before** importing PySide6, and `pytest.importorskip("PySide6")`.
- **No modal dialogs** anywhere in non-interactive paths (CI headless hang rule). Surface errors to a status label, never `QMessageBox.exec()`.
- **HTTP is injectable.** Providers take an `http` callable (default = the real urllib getter) so tests pass a fake — never hit the network in tests.
- **Commit** after each green task with the shown message.

---

## File Structure

**Create:**
- `src/vike_trader_app/data/calendar/__init__.py` — package exports
- `src/vike_trader_app/data/calendar/model.py` — `CalendarEvent`, `ActualValue`, value/impact/time parsing, week helpers
- `src/vike_trader_app/data/calendar/http.py` — `http_get_json()` (urllib) + `http_get_text()`
- `src/vike_trader_app/data/calendar/taxonomy.py` — `normalize_title`, `categorize`, `CURRENCY_COUNTRY` (currency→(country, iso2))
- `src/vike_trader_app/data/calendar/providers/__init__.py`
- `src/vike_trader_app/data/calendar/providers/base.py` — `ScheduleProvider`, `ActualsProvider` protocols
- `src/vike_trader_app/data/calendar/providers/forexfactory.py` — `ForexFactoryProvider`
- `src/vike_trader_app/data/calendar/providers/fred.py` — `FredProvider`
- `src/vike_trader_app/data/calendar/providers/bls.py` — `BlsProvider`
- `src/vike_trader_app/data/calendar/providers/bea.py` — `BeaProvider`
- `src/vike_trader_app/data/calendar/providers/census.py` — `CensusProvider`
- `src/vike_trader_app/data/calendar/providers/ecb.py` — `EcbProvider`
- `src/vike_trader_app/data/calendar/store.py` — `CalendarStore`
- `src/vike_trader_app/data/calendar/repository.py` — `CalendarRepository`, `default_repository()`
- `src/vike_trader_app/ui/economic_calendar.py` — `EconomicCalendarTab`, `_CalendarFetchWorker`
- `src/vike_trader_app/ui/calendar_delegate.py` — `CalendarDelegate` (importance bars, colored values, country chip/flag)
- `tests/test_calendar_model.py`, `tests/test_calendar_forexfactory.py`, `tests/test_calendar_store.py`, `tests/test_calendar_fred.py`, `tests/test_calendar_actuals_more.py`, `tests/test_calendar_repository.py`, `tests/test_economic_calendar_gui.py`
- `tests/fixtures/ff_calendar_thisweek.json` — captured ForexFactory sample

**Modify:**
- `src/vike_trader_app/ui/app.py` — add `EconomicCalendarTab` tab + rail item

---

## Phase 1 — Data model & parsing

### Task 1: `CalendarEvent` model + `ActualValue`

**Files:**
- Create: `src/vike_trader_app/data/calendar/__init__.py` (empty for now)
- Create: `src/vike_trader_app/data/calendar/model.py`
- Test: `tests/test_calendar_model.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_calendar_model.py
from vike_trader_app.data.calendar.model import CalendarEvent, ActualValue


def test_make_id_is_stable_and_distinct():
    a = CalendarEvent.make_id(1_700_000_000_000, "USD", "Non-Farm Payrolls")
    b = CalendarEvent.make_id(1_700_000_000_000, "USD", "Non-Farm Payrolls")
    c = CalendarEvent.make_id(1_700_000_000_000, "USD", "CPI")
    assert a == b and a != c and isinstance(a, str)


def test_event_roundtrips_through_dict():
    ev = CalendarEvent(
        id="x", ts_utc=1_700_000_000_000, all_day=False, country="US",
        currency="USD", title="CPI", category="inflation", importance=2,
        actual=3.2, forecast=3.2, previous=3.0, unit="%",
        actual_display="3.2%", forecast_display="3.2%", previous_display="3%",
        actual_source="BLS",
    )
    assert CalendarEvent.from_dict(ev.to_dict()) == ev


def test_actual_value_holds_number_unit_source():
    av = ActualValue(value=6.82, unit="M", source="FRED")
    assert (av.value, av.unit, av.source) == (6.82, "M", "FRED")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_calendar_model.py -v`
Expected: FAIL — `ModuleNotFoundError: ... calendar.model`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/vike_trader_app/data/calendar/model.py
"""Economic-calendar event model + a small value holder.

A CalendarEvent is the normalized unit shared across providers, store, repository
and UI. Display strings (e.g. "−27.1 B A$") are authoritative for rendering; the
parsed (value, unit) drives beat/miss coloring and any future charting.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ActualValue:
    """One backfilled actual: parsed number + unit + which provider supplied it."""
    value: float | None
    unit: str
    source: str


@dataclass
class CalendarEvent:
    id: str
    ts_utc: int                 # epoch ms, UTC
    all_day: bool
    country: str                # normalized country name, e.g. "United States"
    currency: str               # ForexFactory code, e.g. "USD"
    title: str
    category: str               # rates|inflation|employment|gdp|trade|housing|other
    importance: int             # 0 low, 1 med, 2 high
    actual: float | None
    forecast: float | None
    previous: float | None
    unit: str
    actual_display: str
    forecast_display: str
    previous_display: str
    actual_source: str | None = None

    @staticmethod
    def make_id(ts_utc: int, currency: str, title: str) -> str:
        key = f"{ts_utc}|{currency}|{title}".encode("utf-8")
        return hashlib.sha1(key).hexdigest()[:16]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CalendarEvent":
        return cls(**d)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_calendar_model.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/vike_trader_app/data/calendar/__init__.py src/vike_trader_app/data/calendar/model.py tests/test_calendar_model.py
git commit -m "feat(calendar): CalendarEvent model + ActualValue"
```

---

### Task 2: value / impact / time parsing

**Files:**
- Modify: `src/vike_trader_app/data/calendar/model.py` (append functions)
- Test: `tests/test_calendar_model.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_calendar_model.py  (append)
import pytest
from vike_trader_app.data.calendar.model import (
    parse_value, impact_to_importance, iso_to_ts_utc, week_start_utc,
)


@pytest.mark.parametrize("raw, value, unit", [
    ("3.2%", 3.2, "%"),
    ("−27.1 B A$", -27.1, "B A$"),   # unicode minus
    ("-27.1B A$", -27.1, "B A$"),    # ascii minus, no space
    ("65.94 K", 65.94, "K"),
    ("6.82 M", 6.82, "M"),
    ("103.15", 103.15, ""),
    ("", None, ""),
    ("—", None, ""),                 # em dash = no value
])
def test_parse_value(raw, value, unit):
    assert parse_value(raw) == (value, unit)


def test_impact_to_importance():
    assert impact_to_importance("High") == 2
    assert impact_to_importance("Medium") == 1
    assert impact_to_importance("Low") == 0
    assert impact_to_importance("Holiday") == 0
    assert impact_to_importance("anything else") == 0


def test_iso_to_ts_utc_handles_offset():
    # 2026-06-02T12:30:00+03:00 == 09:30:00Z
    assert iso_to_ts_utc("2026-06-02T12:30:00+03:00") == 1_780_392_600_000


def test_week_start_utc_is_monday_midnight():
    # a Tuesday → Monday 00:00:00Z of that ISO week
    tue = iso_to_ts_utc("2026-06-02T12:30:00+00:00")
    mon = iso_to_ts_utc("2026-06-01T00:00:00+00:00")
    assert week_start_utc(tue) == mon
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_calendar_model.py -k "parse_value or impact or iso_to or week_start" -v`
Expected: FAIL — `ImportError: cannot import name 'parse_value'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/vike_trader_app/data/calendar/model.py  (append)
import re
from datetime import datetime, timezone, timedelta

_NUM_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def parse_value(raw: str) -> tuple[float | None, str]:
    """Split a ForexFactory display string into (number, unit).

    Handles unicode minus, magnitude letters (K/M/B), and currency/percent units.
    Returns (None, "") for blanks and em/en dashes.
    """
    if raw is None:
        return None, ""
    s = raw.strip().replace("−", "-")  # unicode minus → ascii
    if s in ("", "—", "–", "-"):
        return None, ""
    m = _NUM_RE.search(s)
    if not m:
        return None, ""
    value = float(m.group(0).replace(",", ""))
    unit = (s[: m.start()] + s[m.end():]).strip()
    return value, unit


def impact_to_importance(impact: str) -> int:
    return {"high": 2, "medium": 1, "low": 0}.get((impact or "").strip().lower(), 0)


def iso_to_ts_utc(iso: str) -> int:
    """ISO-8601 (with offset, or trailing 'Z') → epoch ms UTC."""
    s = iso.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def week_start_utc(ts_utc: int) -> int:
    """Monday 00:00:00 UTC of the ISO week containing ts_utc (epoch ms)."""
    dt = datetime.fromtimestamp(ts_utc / 1000, tz=timezone.utc)
    monday = (dt - timedelta(days=dt.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return int(monday.timestamp() * 1000)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_calendar_model.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/vike_trader_app/data/calendar/model.py tests/test_calendar_model.py
git commit -m "feat(calendar): value/impact/time parsing helpers"
```

---

### Task 3: taxonomy — title normalization, category, currency→country

**Files:**
- Create: `src/vike_trader_app/data/calendar/taxonomy.py`
- Test: `tests/test_calendar_model.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_calendar_model.py  (append)
from vike_trader_app.data.calendar.taxonomy import (
    normalize_title, categorize, currency_country,
)


def test_normalize_title_strips_qualifiers_and_case():
    assert normalize_title("Inflation Rate YoY Flash") == "inflation rate"
    assert normalize_title("Core Inflation Rate MoM Prel") == "core inflation rate"
    assert normalize_title("GDP Growth Rate QoQ Final") == "gdp growth rate"


def test_categorize_buckets_known_events():
    assert categorize("Non-Farm Payrolls") == "employment"
    assert categorize("Inflation Rate YoY") == "inflation"
    assert categorize("Fed Interest Rate Decision") == "rates"
    assert categorize("GDP Growth Rate QoQ") == "gdp"
    assert categorize("Balance of Trade") == "trade"
    assert categorize("Some Random Auction") == "other"


def test_currency_country_maps_and_falls_back():
    assert currency_country("USD") == ("United States", "us")
    assert currency_country("EUR") == ("European Union", "eu")
    assert currency_country("ZZZ") == ("ZZZ", "")   # unknown → echo code, no iso
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_calendar_model.py -k "normalize or categorize or currency_country" -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write minimal implementation**

```python
# src/vike_trader_app/data/calendar/taxonomy.py
"""Title normalization, category bucketing, and currency→country/ISO mapping.

Kept deliberately small and data-driven so it's cheap to extend as coverage grows.
"""
from __future__ import annotations

import re

# qualifiers that distinguish releases but not the underlying indicator identity
_QUALIFIERS = re.compile(
    r"\b(yoy|mom|qoq|wow|flash|prel|prelim|preliminary|final|adv|advance|"
    r"2nd est|3rd est|s\.a\.|n\.s\.a\.)\b",
    re.IGNORECASE,
)


def normalize_title(title: str) -> str:
    t = _QUALIFIERS.sub("", title or "")
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


# ordered (keyword, category) — first match wins
_CATEGORY_RULES: list[tuple[str, str]] = [
    ("payroll", "employment"), ("unemployment", "employment"),
    ("jobless", "employment"), ("employment", "employment"), ("jolts", "employment"),
    ("inflation", "inflation"), ("cpi", "inflation"), ("ppi", "inflation"),
    ("pce price", "inflation"),
    ("interest rate", "rates"), ("rate decision", "rates"), ("fed ", "rates"),
    ("ecb ", "rates"), ("boe ", "rates"), ("fomc", "rates"),
    ("gdp", "gdp"),
    ("balance of trade", "trade"), ("exports", "trade"), ("imports", "trade"),
    ("current account", "trade"),
    ("housing", "housing"), ("home sales", "housing"), ("building permits", "housing"),
    ("mortgage", "housing"),
]


def categorize(title: str) -> str:
    t = (title or "").lower()
    for kw, cat in _CATEGORY_RULES:
        if kw in t:
            return cat
    return "other"


# ForexFactory currency code → (display country, ISO-3166 alpha-2 lowercase for flags)
CURRENCY_COUNTRY: dict[str, tuple[str, str]] = {
    "USD": ("United States", "us"), "EUR": ("European Union", "eu"),
    "GBP": ("United Kingdom", "gb"), "JPY": ("Japan", "jp"),
    "AUD": ("Australia", "au"), "NZD": ("New Zealand", "nz"),
    "CAD": ("Canada", "ca"), "CHF": ("Switzerland", "ch"),
    "CNY": ("Mainland China", "cn"), "INR": ("India", "in"),
    "BRL": ("Brazil", "br"), "ZAR": ("South Africa", "za"),
    "KRW": ("South Korea", "kr"), "MXN": ("Mexico", "mx"),
    "RUB": ("Russia", "ru"), "TRY": ("Turkey", "tr"),
    "IDR": ("Indonesia", "id"), "SAR": ("Saudi Arabia", "sa"),
    "SGD": ("Singapore", "sg"), "HKD": ("Hong Kong", "hk"),
    "SEK": ("Sweden", "se"), "NOK": ("Norway", "no"),
}


def currency_country(currency: str) -> tuple[str, str]:
    return CURRENCY_COUNTRY.get((currency or "").upper(), (currency, ""))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_calendar_model.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/vike_trader_app/data/calendar/taxonomy.py tests/test_calendar_model.py
git commit -m "feat(calendar): taxonomy (normalize/categorize/currency-country)"
```

---

## Phase 2 — Schedule provider (ForexFactory)

### Task 4: HTTP helpers (urllib) + provider protocols

**Files:**
- Create: `src/vike_trader_app/data/calendar/http.py`
- Create: `src/vike_trader_app/data/calendar/providers/__init__.py` (empty)
- Create: `src/vike_trader_app/data/calendar/providers/base.py`
- Test: `tests/test_calendar_forexfactory.py` (import-only smoke for base)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_calendar_forexfactory.py
from vike_trader_app.data.calendar.providers.base import ScheduleProvider, ActualsProvider
from vike_trader_app.data.calendar import http


def test_base_protocols_importable():
    assert hasattr(ScheduleProvider, "fetch_week")
    assert hasattr(ActualsProvider, "backfill")


def test_http_module_exposes_getters():
    assert callable(http.http_get_json)
    assert callable(http.http_get_text)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_calendar_forexfactory.py -v`
Expected: FAIL — modules missing.

- [ ] **Step 3: Write minimal implementation**

```python
# src/vike_trader_app/data/calendar/http.py
"""Tiny urllib JSON/text getters (matches data/binance_source.py's stdlib approach).

Injected into providers as the default `http` so tests can pass a fake and never
touch the network.
"""
from __future__ import annotations

import json
import urllib.request


def http_get_text(url: str, *, timeout: int = 30, headers: dict | None = None) -> str:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "vike-trader-app"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https hosts
        return resp.read().decode("utf-8")


def http_get_json(url: str, *, timeout: int = 30, headers: dict | None = None):
    return json.loads(http_get_text(url, timeout=timeout, headers=headers))
```

```python
# src/vike_trader_app/data/calendar/providers/base.py
"""Provider roles: a ScheduleProvider yields the event list; ActualsProviders fill
`actual` after release. Both are Protocols so any object with the right method fits.
"""
from __future__ import annotations

from typing import Protocol

from ..model import ActualValue, CalendarEvent


class ScheduleProvider(Protocol):
    def fetch_week(self, week_start_utc: int) -> list[CalendarEvent]: ...


class ActualsProvider(Protocol):
    name: str

    def backfill(self, events: list[CalendarEvent]) -> dict[str, ActualValue]:
        """Return {event_id: ActualValue} for the events this provider can fill."""
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_calendar_forexfactory.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/vike_trader_app/data/calendar/http.py src/vike_trader_app/data/calendar/providers/
git add tests/test_calendar_forexfactory.py
git commit -m "feat(calendar): http getters + provider protocols"
```

---

### Task 5: capture a ForexFactory fixture

**Files:**
- Create: `tests/fixtures/ff_calendar_thisweek.json`

- [ ] **Step 1: Write the fixture** (a hand-built, schema-accurate sample — 4 records covering the cases the parser must handle: percent unit, magnitude+currency unit, blank forecast, Holiday impact)

```json
[
  {"title": "Non-Farm Payrolls", "country": "USD", "date": "2026-06-05T15:30:00+03:00",
   "impact": "High", "forecast": "185K", "previous": "177K"},
  {"title": "Inflation Rate YoY Flash", "country": "EUR", "date": "2026-06-02T12:00:00+03:00",
   "impact": "Medium", "forecast": "3.2%", "previous": "3%"},
  {"title": "Current Account", "country": "AUD", "date": "2026-06-02T04:30:00+03:00",
   "impact": "Low", "forecast": "", "previous": "−23 B A$"},
  {"title": "Bank Holiday", "country": "GBP", "date": "2026-06-01T00:00:00+03:00",
   "impact": "Holiday", "forecast": "", "previous": ""}
]
```

- [ ] **Step 2: Commit**

```bash
git add tests/fixtures/ff_calendar_thisweek.json
git commit -m "test(calendar): ForexFactory JSON fixture"
```

---

### Task 6: `ForexFactoryProvider`

**Files:**
- Create: `src/vike_trader_app/data/calendar/providers/forexfactory.py`
- Test: `tests/test_calendar_forexfactory.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_calendar_forexfactory.py  (append)
import json
from pathlib import Path

from vike_trader_app.data.calendar.providers.forexfactory import ForexFactoryProvider
from vike_trader_app.data.calendar.model import week_start_utc, iso_to_ts_utc

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "ff_calendar_thisweek.json").read_text("utf-8"))


def _provider():
    # inject http that ignores the URL and returns the fixture
    return ForexFactoryProvider(http=lambda url, **kw: FIXTURE)


def test_parses_all_records_into_events():
    evs = _provider().fetch_week(week_start_utc(iso_to_ts_utc("2026-06-02T00:00:00+00:00")))
    assert len(evs) == 4


def test_maps_fields_units_and_importance():
    evs = {e.title: e for e in _provider().fetch_week(0)}
    nfp = evs["Non-Farm Payrolls"]
    assert nfp.currency == "USD" and nfp.country == "United States"
    assert nfp.importance == 2 and nfp.category == "employment"
    assert nfp.forecast == 185.0 and nfp.unit == "K"
    ca = evs["Current Account"]
    assert ca.previous == -23.0 and ca.unit == "B A$"
    assert ca.forecast is None and ca.forecast_display == ""


def test_actual_is_blank_from_schedule_only():
    evs = _provider().fetch_week(0)
    assert all(e.actual is None and e.actual_display == "" for e in evs)


def test_id_is_deterministic_across_fetches():
    a = {e.id for e in _provider().fetch_week(0)}
    b = {e.id for e in _provider().fetch_week(0)}
    assert a == b and len(a) == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_calendar_forexfactory.py -v`
Expected: FAIL — `ForexFactoryProvider` missing.

- [ ] **Step 3: Write minimal implementation**

```python
# src/vike_trader_app/data/calendar/providers/forexfactory.py
"""ForexFactory weekly JSON schedule provider.

Fetches the publisher's own static weekly files (no API key). Gives every country's
time/importance/forecast/previous; `actual` is NOT in the feed (backfill layer fills it).
"""
from __future__ import annotations

from ..http import http_get_json
from ..model import (
    CalendarEvent, impact_to_importance, iso_to_ts_utc, parse_value,
)
from ..taxonomy import categorize, currency_country

THIS_WEEK = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEXT_WEEK = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"


class ForexFactoryProvider:
    name = "ForexFactory"

    def __init__(self, http=http_get_json, *, url: str = THIS_WEEK):
        self._http = http
        self._url = url

    def fetch_week(self, week_start_utc: int) -> list[CalendarEvent]:
        records = self._http(self._url)
        return [self._to_event(r) for r in records]

    @staticmethod
    def _to_event(r: dict) -> CalendarEvent:
        currency = r.get("country", "")            # ForexFactory puts the code in `country`
        country, _iso = currency_country(currency)
        ts = iso_to_ts_utc(r["date"])
        all_day = r["date"].endswith("00:00:00+03:00") and r.get("impact") == "Holiday"
        fval, funit = parse_value(r.get("forecast", ""))
        pval, punit = parse_value(r.get("previous", ""))
        title = r.get("title", "")
        return CalendarEvent(
            id=CalendarEvent.make_id(ts, currency, title),
            ts_utc=ts, all_day=all_day, country=country, currency=currency,
            title=title, category=categorize(title),
            importance=impact_to_importance(r.get("impact", "")),
            actual=None, forecast=fval, previous=pval,
            unit=funit or punit,
            actual_display="",
            forecast_display=(r.get("forecast") or "").replace("−", "-"),
            previous_display=(r.get("previous") or "").replace("−", "-"),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_calendar_forexfactory.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/vike_trader_app/data/calendar/providers/forexfactory.py tests/test_calendar_forexfactory.py
git commit -m "feat(calendar): ForexFactory schedule provider"
```

---

## Phase 3 — Cache / store

### Task 7: `CalendarStore` (JSON per ISO week)

**Files:**
- Create: `src/vike_trader_app/data/calendar/store.py`
- Test: `tests/test_calendar_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_calendar_store.py
from vike_trader_app.data.calendar.store import CalendarStore
from vike_trader_app.data.calendar.model import CalendarEvent, iso_to_ts_utc


def _ev(ts, title):
    return CalendarEvent(
        id=CalendarEvent.make_id(ts, "USD", title), ts_utc=ts, all_day=False,
        country="United States", currency="USD", title=title, category="other",
        importance=1, actual=None, forecast=None, previous=None, unit="",
        actual_display="", forecast_display="", previous_display="")


def test_iso_week_key_format():
    ts = iso_to_ts_utc("2026-06-02T00:00:00+00:00")
    assert CalendarStore.iso_week_key(ts) == "2026-W23"


def test_save_and_load_roundtrip(tmp_path):
    store = CalendarStore(str(tmp_path))
    ts = iso_to_ts_utc("2026-06-02T12:00:00+00:00")
    store.save_week("2026-W23", [_ev(ts, "CPI")])
    again = store.load_week("2026-W23")
    assert [e.title for e in again] == ["CPI"]


def test_load_missing_week_returns_empty(tmp_path):
    assert CalendarStore(str(tmp_path)).load_week("1999-W01") == []


def test_meta_tracks_last_fetch(tmp_path):
    store = CalendarStore(str(tmp_path))
    assert store.last_fetch("2026-W23") == 0
    store.mark_fetched("2026-W23", 1_700_000_000_000)
    assert CalendarStore(str(tmp_path)).last_fetch("2026-W23") == 1_700_000_000_000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_calendar_store.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write minimal implementation**

```python
# src/vike_trader_app/data/calendar/store.py
"""JSON-per-ISO-week cache for calendar events + a small fetch-time meta file.

Mirrors analysis/journal.py: all I/O through a base dir; corrupt files start clean.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .model import CalendarEvent

DEFAULT_ROOT = "storage/calendar"


class CalendarStore:
    def __init__(self, root: str = DEFAULT_ROOT):
        self.root = Path(root)
        self._meta_path = self.root / "meta.json"

    @staticmethod
    def iso_week_key(ts_utc: int) -> str:
        dt = datetime.fromtimestamp(ts_utc / 1000, tz=timezone.utc)
        y, w, _ = dt.isocalendar()
        return f"{y}-W{w:02d}"

    def _week_path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def load_week(self, key: str) -> list[CalendarEvent]:
        p = self._week_path(key)
        if not p.exists():
            return []
        try:
            return [CalendarEvent.from_dict(d) for d in json.loads(p.read_text("utf-8"))]
        except (json.JSONDecodeError, TypeError, OSError):
            return []

    def save_week(self, key: str, events: list[CalendarEvent]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._week_path(key).write_text(
            json.dumps([e.to_dict() for e in events], indent=2), encoding="utf-8")

    def _meta(self) -> dict:
        if not self._meta_path.exists():
            return {}
        try:
            return json.loads(self._meta_path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def last_fetch(self, key: str) -> int:
        return int(self._meta().get(key, 0))

    def mark_fetched(self, key: str, ts_ms: int) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        meta = self._meta()
        meta[key] = ts_ms
        self._meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_calendar_store.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/vike_trader_app/data/calendar/store.py tests/test_calendar_store.py
git commit -m "feat(calendar): JSON-per-ISO-week store + fetch meta"
```

---

## Phase 4 — Actuals backfill providers

### Task 8: `FredProvider` (US macro, free key)

**Files:**
- Create: `src/vike_trader_app/data/calendar/providers/fred.py`
- Test: `tests/test_calendar_fred.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_calendar_fred.py
from vike_trader_app.data.calendar.providers.fred import FredProvider
from vike_trader_app.data.calendar.model import CalendarEvent, iso_to_ts_utc


def _ev(title, currency="USD"):
    ts = iso_to_ts_utc("2026-06-05T12:30:00+00:00")
    return CalendarEvent(
        id=CalendarEvent.make_id(ts, currency, title), ts_utc=ts, all_day=False,
        country="United States", currency=currency, title=title, category="employment",
        importance=2, actual=None, forecast=185.0, previous=177.0, unit="K",
        actual_display="", forecast_display="185K", previous_display="177K")


def test_no_key_returns_empty():
    p = FredProvider(api_key=None, http=lambda url, **kw: {})
    assert p.backfill([_ev("Non-Farm Payrolls")]) == {}


def test_backfills_mapped_us_event():
    # FRED series/observations JSON shape
    fake = {"observations": [{"date": "2026-06-01", "value": "272.4"}]}
    p = FredProvider(api_key="k", http=lambda url, **kw: fake)
    out = p.backfill([_ev("Non-Farm Payrolls")])
    ev_id = _ev("Non-Farm Payrolls").id
    assert ev_id in out and out[ev_id].value == 272.4 and out[ev_id].source == "FRED"


def test_ignores_unmapped_or_nonus_events():
    p = FredProvider(api_key="k", http=lambda url, **kw: {"observations": []})
    assert p.backfill([_ev("Mystery Indicator")]) == {}
    assert p.backfill([_ev("Non-Farm Payrolls", currency="EUR")]) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_calendar_fred.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write minimal implementation**

```python
# src/vike_trader_app/data/calendar/providers/fred.py
"""FRED (St. Louis Fed) actuals backfill for US events.

Maps a curated set of high/medium-impact US event titles to FRED series, fetches the
latest observation, and returns it as an ActualValue. Needs a free FRED_API_KEY; with
no key the provider is a no-op (graceful degradation).
"""
from __future__ import annotations

import os

from ..http import http_get_json
from ..model import ActualValue, CalendarEvent
from ..taxonomy import normalize_title

OBS_URL = ("https://api.stlouisfed.org/fred/series/observations"
           "?series_id={series}&api_key={key}&file_type=json"
           "&sort_order=desc&limit=1")

# normalized US event title → (FRED series id, unit)
SERIES: dict[str, tuple[str, str]] = {
    "non-farm payrolls": ("PAYEMS", "K"),
    "unemployment rate": ("UNRATE", "%"),
    "inflation rate": ("CPIAUCSL", "%"),
    "core inflation rate": ("CPILFESL", "%"),
    "gdp growth rate": ("A191RL1Q225SBEA", "%"),
    "fed funds rate": ("FEDFUNDS", "%"),
    "retail sales": ("RSAFS", "%"),
}


class FredProvider:
    name = "FRED"

    def __init__(self, api_key: str | None = None, http=http_get_json):
        self._key = api_key if api_key is not None else os.environ.get("FRED_API_KEY")
        self._http = http

    def backfill(self, events: list[CalendarEvent]) -> dict[str, ActualValue]:
        if not self._key:
            return {}
        out: dict[str, ActualValue] = {}
        for ev in events:
            if ev.currency != "USD" or ev.actual is not None:
                continue
            mapped = SERIES.get(normalize_title(ev.title))
            if not mapped:
                continue
            series, unit = mapped
            try:
                data = self._http(OBS_URL.format(series=series, key=self._key))
                obs = data.get("observations") or []
                if not obs or obs[0].get("value") in (None, ".", ""):
                    continue
                out[ev.id] = ActualValue(float(obs[0]["value"]), unit, self.name)
            except Exception:  # noqa: BLE001 - a flaky source must never break the calendar
                continue
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_calendar_fred.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/vike_trader_app/data/calendar/providers/fred.py tests/test_calendar_fred.py
git commit -m "feat(calendar): FRED actuals backfill provider"
```

---

### Task 9: BLS, BEA, Census, ECB providers (same shape, real endpoints)

Each follows the FRED pattern: a normalized-title→series map, key from env (except ECB), graceful no-op on missing key/unmapped/error. One test module covers all four with mocked `http`.

**Files:**
- Create: `src/vike_trader_app/data/calendar/providers/bls.py`
- Create: `src/vike_trader_app/data/calendar/providers/bea.py`
- Create: `src/vike_trader_app/data/calendar/providers/census.py`
- Create: `src/vike_trader_app/data/calendar/providers/ecb.py`
- Test: `tests/test_calendar_actuals_more.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_calendar_actuals_more.py
from vike_trader_app.data.calendar.providers.bls import BlsProvider
from vike_trader_app.data.calendar.providers.bea import BeaProvider
from vike_trader_app.data.calendar.providers.census import CensusProvider
from vike_trader_app.data.calendar.providers.ecb import EcbProvider
from vike_trader_app.data.calendar.model import CalendarEvent, iso_to_ts_utc


def _ev(title, currency):
    ts = iso_to_ts_utc("2026-06-05T12:30:00+00:00")
    return CalendarEvent(
        id=CalendarEvent.make_id(ts, currency, title), ts_utc=ts, all_day=False,
        country="x", currency=currency, title=title, category="inflation",
        importance=2, actual=None, forecast=None, previous=None, unit="",
        actual_display="", forecast_display="", previous_display="")


def test_bls_backfills_cpi():
    fake = {"Results": {"series": [{"data": [{"value": "317.6"}]}]}}
    p = BlsProvider(api_key="k", http=lambda url, **kw: fake)
    ev = _ev("Inflation Rate YoY", "USD")
    out = p.backfill([ev])
    assert out[ev.id].value == 317.6 and out[ev.id].source == "BLS"


def test_bea_backfills_gdp():
    fake = {"BEAAPI": {"Results": {"Data": [{"DataValue": "2.7"}]}}}
    p = BeaProvider(api_key="k", http=lambda url, **kw: fake)
    ev = _ev("GDP Growth Rate QoQ", "USD")
    assert p.backfill([ev])[ev.id].value == 2.7


def test_census_backfills_retail():
    fake = [["cell_value", "time"], ["712345", "2026-05"]]
    p = CensusProvider(api_key="k", http=lambda url, **kw: fake)
    ev = _ev("Retail Sales MoM", "USD")
    assert p.backfill([ev])[ev.id].source == "Census"


def test_ecb_needs_no_key_and_backfills_eu():
    fake = {"dataSets": [{"series": {"0:0:0": {"observations": {"0": [2.5]}}}}]}
    p = EcbProvider(http=lambda url, **kw: fake)
    ev = _ev("Inflation Rate YoY Flash", "EUR")
    out = p.backfill([ev])
    assert out[ev.id].value == 2.5 and out[ev.id].source == "ECB"


def test_all_skip_unmapped():
    for P in (BlsProvider(api_key="k", http=lambda u, **k: {}),
              BeaProvider(api_key="k", http=lambda u, **k: {}),
              CensusProvider(api_key="k", http=lambda u, **k: []),
              EcbProvider(http=lambda u, **k: {})):
        assert P.backfill([_ev("Totally Unknown Event", "USD")]) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_calendar_actuals_more.py -v`
Expected: FAIL — modules missing.

- [ ] **Step 3: Write minimal implementation** (four small files)

```python
# src/vike_trader_app/data/calendar/providers/bls.py
"""BLS (US Bureau of Labor Statistics) v2 API actuals: CPI, NFP, unemployment.

Optional BLS_API_KEY (higher limits); works keyless for light use. No-op on miss/error.
"""
from __future__ import annotations

import json
import os

from ..http import http_get_text
from ..model import ActualValue, CalendarEvent
from ..taxonomy import normalize_title

URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
SERIES: dict[str, tuple[str, str]] = {
    "inflation rate": ("CUUR0000SA0", "%"),
    "core inflation rate": ("CUUR0000SA0L1E", "%"),
    "unemployment rate": ("LNS14000000", "%"),
    "non-farm payrolls": ("CES0000000001", "K"),
}


class BlsProvider:
    name = "BLS"

    def __init__(self, api_key: str | None = None, http=None):
        self._key = api_key if api_key is not None else os.environ.get("BLS_API_KEY")
        # BLS uses POST; wrap a poster, but allow a fake http(url, data=...) in tests
        self._http = http

    def _post(self, series_id: str):
        if self._http is not None:
            return self._http(URL, data=series_id)
        import urllib.request
        body = json.dumps({"seriesid": [series_id],
                           **({"registrationkey": self._key} if self._key else {})}).encode()
        req = urllib.request.Request(URL, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))

    def backfill(self, events: list[CalendarEvent]) -> dict[str, ActualValue]:
        out: dict[str, ActualValue] = {}
        for ev in events:
            if ev.currency != "USD" or ev.actual is not None:
                continue
            mapped = SERIES.get(normalize_title(ev.title))
            if not mapped:
                continue
            series, unit = mapped
            try:
                data = self._post(series)
                rows = data["Results"]["series"][0]["data"]
                if rows:
                    out[ev.id] = ActualValue(float(rows[0]["value"]), unit, self.name)
            except Exception:  # noqa: BLE001
                continue
        return out
```

```python
# src/vike_trader_app/data/calendar/providers/bea.py
"""BEA (US Bureau of Economic Analysis) actuals: GDP, PCE. Needs free BEA_API_KEY."""
from __future__ import annotations

import os

from ..http import http_get_json
from ..model import ActualValue, CalendarEvent
from ..taxonomy import normalize_title

URL = ("https://apps.bea.gov/api/data?UserID={key}&method=GetData&ResultFormat=JSON"
       "&datasetname=NIPA&TableName={table}&Frequency=Q&Year=LAST5")
# normalized title → (BEA NIPA table, unit)
TABLES: dict[str, tuple[str, str]] = {
    "gdp growth rate": ("T10101", "%"),
    "pce price index": ("T20804", "%"),
}


class BeaProvider:
    name = "BEA"

    def __init__(self, api_key: str | None = None, http=http_get_json):
        self._key = api_key if api_key is not None else os.environ.get("BEA_API_KEY")
        self._http = http

    def backfill(self, events: list[CalendarEvent]) -> dict[str, ActualValue]:
        if not self._key:
            return {}
        out: dict[str, ActualValue] = {}
        for ev in events:
            if ev.currency != "USD" or ev.actual is not None:
                continue
            mapped = TABLES.get(normalize_title(ev.title))
            if not mapped:
                continue
            table, unit = mapped
            try:
                data = self._http(URL.format(key=self._key, table=table))
                rows = data["BEAAPI"]["Results"]["Data"]
                if rows:
                    out[ev.id] = ActualValue(float(rows[-1]["DataValue"].replace(",", "")),
                                             unit, self.name)
            except Exception:  # noqa: BLE001
                continue
        return out
```

```python
# src/vike_trader_app/data/calendar/providers/census.py
"""US Census actuals: retail sales, housing starts/permits. Needs free CENSUS_API_KEY."""
from __future__ import annotations

import os

from ..http import http_get_json
from ..model import ActualValue, CalendarEvent
from ..taxonomy import normalize_title

URL = ("https://api.census.gov/data/timeseries/eits/{program}"
       "?get=cell_value,time_slot_id&key={key}&category_code={cat}")
# normalized title → (program, category_code, unit)
SERIES: dict[str, tuple[str, str, str]] = {
    "retail sales": ("marts", "44000", "%"),
    "building permits": ("ressales", "PERMIT", "K"),
}


class CensusProvider:
    name = "Census"

    def __init__(self, api_key: str | None = None, http=http_get_json):
        self._key = api_key if api_key is not None else os.environ.get("CENSUS_API_KEY")
        self._http = http

    def backfill(self, events: list[CalendarEvent]) -> dict[str, ActualValue]:
        if not self._key:
            return {}
        out: dict[str, ActualValue] = {}
        for ev in events:
            if ev.currency != "USD" or ev.actual is not None:
                continue
            mapped = SERIES.get(normalize_title(ev.title))
            if not mapped:
                continue
            program, cat, unit = mapped
            try:
                rows = self._http(URL.format(program=program, key=self._key, cat=cat))
                # rows[0] is the header; rows[-1] the latest data row, col 0 = cell_value
                if len(rows) > 1:
                    out[ev.id] = ActualValue(float(rows[-1][0]), unit, self.name)
            except Exception:  # noqa: BLE001
                continue
        return out
```

```python
# src/vike_trader_app/data/calendar/providers/ecb.py
"""ECB Statistical Data Warehouse actuals: EU rates/inflation. No API key required."""
from __future__ import annotations

from ..http import http_get_json
from ..model import ActualValue, CalendarEvent
from ..taxonomy import normalize_title

URL = "https://data-api.ecb.europa.eu/service/data/{flow}/{key}?lastNObservations=1&format=jsondata"
# normalized title → (dataflow, series key, unit)
SERIES: dict[str, tuple[str, str, str]] = {
    "inflation rate": ("ICP", "M.U2.N.000000.4.ANR", "%"),
    "core inflation rate": ("ICP", "M.U2.N.XEF000.4.ANR", "%"),
}


class EcbProvider:
    name = "ECB"

    def __init__(self, http=http_get_json):
        self._http = http

    def backfill(self, events: list[CalendarEvent]) -> dict[str, ActualValue]:
        out: dict[str, ActualValue] = {}
        for ev in events:
            if ev.currency != "EUR" or ev.actual is not None:
                continue
            mapped = SERIES.get(normalize_title(ev.title))
            if not mapped:
                continue
            flow, key, unit = mapped
            try:
                data = self._http(URL.format(flow=flow, key=key))
                series = next(iter(data["dataSets"][0]["series"].values()))
                obs = series["observations"]
                first = next(iter(obs.values()))
                out[ev.id] = ActualValue(float(first[0]), unit, self.name)
            except Exception:  # noqa: BLE001
                continue
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_calendar_actuals_more.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/vike_trader_app/data/calendar/providers/bls.py src/vike_trader_app/data/calendar/providers/bea.py src/vike_trader_app/data/calendar/providers/census.py src/vike_trader_app/data/calendar/providers/ecb.py tests/test_calendar_actuals_more.py
git commit -m "feat(calendar): BLS/BEA/Census/ECB actuals providers"
```

---

## Phase 5 — Repository (merge + backfill + cache)

### Task 10: `CalendarRepository`

**Files:**
- Create: `src/vike_trader_app/data/calendar/repository.py`
- Test: `tests/test_calendar_repository.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_calendar_repository.py
from vike_trader_app.data.calendar.repository import CalendarRepository
from vike_trader_app.data.calendar.store import CalendarStore
from vike_trader_app.data.calendar.model import (
    CalendarEvent, ActualValue, iso_to_ts_utc, week_start_utc,
)

TS = iso_to_ts_utc("2026-06-05T12:30:00+00:00")
WK = week_start_utc(TS)


def _sched_ev(title, actual=None):
    return CalendarEvent(
        id=CalendarEvent.make_id(TS, "USD", title), ts_utc=TS, all_day=False,
        country="United States", currency="USD", title=title, category="employment",
        importance=2, actual=actual, forecast=185.0, previous=177.0, unit="K",
        actual_display=("%sK" % actual if actual else ""),
        forecast_display="185K", previous_display="177K")


class _Sched:
    def __init__(self, evs): self._evs = evs
    def fetch_week(self, ws): return list(self._evs)


class _Actuals:
    name = "FAKE"
    def __init__(self, mapping): self._m = mapping
    def backfill(self, events):
        return {e.id: self._m[e.title] for e in events if e.title in self._m}


def test_fetches_and_caches(tmp_path):
    store = CalendarStore(str(tmp_path))
    repo = CalendarRepository(_Sched([_sched_ev("Non-Farm Payrolls")]), [], store,
                              now_ms=lambda: TS + 60_000)
    evs = repo.get_week(WK)
    assert [e.title for e in evs] == ["Non-Farm Payrolls"]
    # second call served from cache even if the schedule now errors
    repo2 = CalendarRepository(_Sched([]), [], store, now_ms=lambda: TS + 120_000)
    assert [e.title for e in repo2.get_week(WK)] == ["Non-Farm Payrolls"]


def test_backfills_actual_for_past_events(tmp_path):
    store = CalendarStore(str(tmp_path))
    actuals = _Actuals({"Non-Farm Payrolls": ActualValue(272.4, "K", "FAKE")})
    repo = CalendarRepository(_Sched([_sched_ev("Non-Farm Payrolls")]), [actuals], store,
                              now_ms=lambda: TS + 60_000)   # event is in the past
    ev = repo.get_week(WK)[0]
    assert ev.actual == 272.4 and ev.actual_source == "FAKE" and ev.actual_display == "272.4K"


def test_does_not_backfill_future_events(tmp_path):
    store = CalendarStore(str(tmp_path))
    actuals = _Actuals({"Non-Farm Payrolls": ActualValue(272.4, "K", "FAKE")})
    repo = CalendarRepository(_Sched([_sched_ev("Non-Farm Payrolls")]), [actuals], store,
                              now_ms=lambda: TS - 60_000)   # event still in the future
    assert repo.get_week(WK)[0].actual is None


def test_rate_limit_skips_refetch_when_fresh(tmp_path):
    store = CalendarStore(str(tmp_path))
    calls = {"n": 0}
    class Counting(_Sched):
        def fetch_week(self, ws):
            calls["n"] += 1
            return super().fetch_week(ws)
    sched = Counting([_sched_ev("CPI")])
    repo = CalendarRepository(sched, [], store, now_ms=lambda: TS,
                              min_refetch_ms=10 * 60_000)
    repo.get_week(WK)
    repo.get_week(WK)            # within the 10-min window → no second fetch
    assert calls["n"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_calendar_repository.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write minimal implementation**

```python
# src/vike_trader_app/data/calendar/repository.py
"""Aggregator: schedule + actuals + cache.

get_week() loads the cached ISO week, refetches the schedule when stale (respecting a
min-refetch window), merges by id, backfills `actual` for past events via the actuals
providers in priority order, persists, and returns events sorted by time.
"""
from __future__ import annotations

import os
import time

from .model import CalendarEvent
from .store import CalendarStore

_MIN_REFETCH_MS = 10 * 60_000  # ForexFactory: ~2 downloads / 5 min — stay well under


class CalendarRepository:
    def __init__(self, schedule, actuals_providers, store: CalendarStore, *,
                 now_ms=lambda: int(time.time() * 1000), min_refetch_ms: int = _MIN_REFETCH_MS):
        self._schedule = schedule
        self._actuals = list(actuals_providers)
        self._store = store
        self._now = now_ms
        self._min_refetch = min_refetch_ms

    def get_week(self, week_start_utc: int, *, force: bool = False) -> list[CalendarEvent]:
        key = self._store.iso_week_key(week_start_utc)
        cached = {e.id: e for e in self._store.load_week(key)}

        if force or self._is_stale(key):
            try:
                fetched = self._schedule.fetch_week(week_start_utc)
                cached = self._merge(cached, fetched)
                self._store.mark_fetched(key, self._now())
            except Exception:  # noqa: BLE001 - keep serving cache if the source is down
                pass

        self._backfill(cached)
        events = sorted(cached.values(), key=lambda e: (e.ts_utc, e.country, e.title))
        self._store.save_week(key, events)
        return events

    def _is_stale(self, key: str) -> bool:
        return (self._now() - self._store.last_fetch(key)) >= self._min_refetch

    @staticmethod
    def _merge(cached: dict, fetched: list) -> dict:
        for ev in fetched:
            old = cached.get(ev.id)
            if old is not None and old.actual is not None:
                # preserve an already-backfilled actual; refresh schedule fields
                ev.actual, ev.actual_display, ev.actual_source = (
                    old.actual, old.actual_display, old.actual_source)
            cached[ev.id] = ev
        return cached

    def _backfill(self, cached: dict) -> None:
        now = self._now()
        pending = [e for e in cached.values() if e.actual is None and e.ts_utc <= now]
        if not pending:
            return
        for provider in self._actuals:
            if not pending:
                break
            try:
                filled = provider.backfill(pending)
            except Exception:  # noqa: BLE001
                filled = {}
            for ev_id, av in filled.items():
                ev = cached[ev_id]
                if ev.actual is None and av.value is not None:
                    ev.actual = av.value
                    ev.unit = ev.unit or av.unit
                    ev.actual_display = f"{_fmt(av.value)}{av.unit}"
                    ev.actual_source = av.source
            pending = [e for e in pending if e.actual is None]


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def default_repository(root: str = "storage/calendar") -> "CalendarRepository":
    """Wire the real providers from env keys. Missing keys disable a provider silently."""
    from .providers.forexfactory import ForexFactoryProvider
    from .providers.fred import FredProvider
    from .providers.bls import BlsProvider
    from .providers.bea import BeaProvider
    from .providers.census import CensusProvider
    from .providers.ecb import EcbProvider
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:  # noqa: BLE001
        pass
    actuals = [FredProvider(), BlsProvider(), BeaProvider(), CensusProvider(), EcbProvider()]
    return CalendarRepository(ForexFactoryProvider(), actuals, CalendarStore(root))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_calendar_repository.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/vike_trader_app/data/calendar/repository.py tests/test_calendar_repository.py
git commit -m "feat(calendar): repository (merge + cache + actuals backfill)"
```

---

## Phase 6 — UI

### Task 11: `CalendarDelegate` (importance bars, colored value, country chip)

**Files:**
- Create: `src/vike_trader_app/ui/calendar_delegate.py`
- Test: `tests/test_economic_calendar_gui.py` (delegate-only pieces first)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_economic_calendar_gui.py
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PySide6")

from PySide6 import QtWidgets, QtGui
from vike_trader_app.ui.calendar_delegate import importance_bar_pixmap, value_color


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_importance_pixmap_sizes(app):
    pm = importance_bar_pixmap(2)
    assert isinstance(pm, QtGui.QPixmap) and not pm.isNull()


def test_value_color_beat_miss(app):
    from vike_trader_app.ui import theme
    assert value_color(actual=3.5, forecast=3.2) == theme.UP     # beat
    assert value_color(actual=3.0, forecast=3.2) == theme.DOWN   # miss
    assert value_color(actual=3.2, forecast=3.2) == theme.TEXT   # inline
    assert value_color(actual=None, forecast=3.2) == theme.TEXT  # unreleased
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_economic_calendar_gui.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write minimal implementation**

```python
# src/vike_trader_app/ui/calendar_delegate.py
"""Custom painting helpers for the calendar tree: the 1–3 bar importance glyph and
beat/miss value coloring. Kept as free functions so they're unit-testable without a view.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui

from . import theme

_BAR_COLORS = {0: theme.TEXT3, 1: theme.WARN, 2: theme.DOWN}


def importance_bar_pixmap(importance: int) -> QtGui.QPixmap:
    """Three ascending bars; `importance`+1 are lit in the level color, rest dim."""
    w, h = 18, 14
    pm = QtGui.QPixmap(w, h)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    lit = _BAR_COLORS.get(importance, theme.TEXT3)
    heights = [5, 9, 13]
    for i, bh in enumerate(heights):
        on = i <= importance
        p.fillRect(QtCore.QRect(1 + i * 6, h - bh, 4, bh),
                   QtGui.QColor(lit if on else theme.BORDER2))
    p.end()
    return pm


def value_color(actual: float | None, forecast: float | None) -> str:
    if actual is None or forecast is None:
        return theme.TEXT
    if actual > forecast:
        return theme.UP
    if actual < forecast:
        return theme.DOWN
    return theme.TEXT
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_economic_calendar_gui.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/vike_trader_app/ui/calendar_delegate.py tests/test_economic_calendar_gui.py
git commit -m "feat(calendar): importance-bar + value-color delegate helpers"
```

---

### Task 12: `EconomicCalendarTab` — grouped tree from a repository

**Files:**
- Create: `src/vike_trader_app/ui/economic_calendar.py`
- Test: `tests/test_economic_calendar_gui.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_economic_calendar_gui.py  (append)
from vike_trader_app.ui.economic_calendar import EconomicCalendarTab
from vike_trader_app.data.calendar.model import CalendarEvent, iso_to_ts_utc, week_start_utc

TS_TUE = iso_to_ts_utc("2026-06-02T12:30:00+00:00")
TS_WED = iso_to_ts_utc("2026-06-03T08:00:00+00:00")
WK = week_start_utc(TS_TUE)


def _ev(ts, currency, title, importance, actual=None, forecast=None):
    return CalendarEvent(
        id=CalendarEvent.make_id(ts, currency, title), ts_utc=ts, all_day=False,
        country={"USD": "United States", "EUR": "European Union"}[currency],
        currency=currency, title=title, category="other", importance=importance,
        actual=actual, forecast=forecast, previous=None, unit="%",
        actual_display=("" if actual is None else f"{actual}%"),
        forecast_display=("" if forecast is None else f"{forecast}%"),
        previous_display="")


class _FakeRepo:
    def __init__(self, evs): self._evs = evs
    def get_week(self, ws, *, force=False): return list(self._evs)


def _tab(app):
    repo = _FakeRepo([
        _ev(TS_TUE, "USD", "JOLTs Job Openings", 2, actual=6.82, forecast=6.9),
        _ev(TS_TUE, "EUR", "Inflation Rate YoY", 1, actual=3.2, forecast=3.2),
        _ev(TS_WED, "USD", "GDP Growth Rate QoQ", 2, forecast=0.5),
    ])
    t = EconomicCalendarTab(repository=repo)
    t.load_week(WK)
    return t


def test_tree_groups_by_date_then_event(app):
    t = _tab(app)
    # two date-header top-level rows (Tue, Wed)
    roots = [t._tree.topLevelItem(i).text(0) for i in range(t._tree.topLevelItemCount())]
    assert any("June 2" in r for r in roots) and any("June 3" in r for r in roots)


def test_importance_filter_high_only_reduces_rows(app):
    t = _tab(app)
    assert t.visible_event_count() == 3
    t.set_high_only(True)
    assert t.visible_event_count() == 2     # the medium EUR row is hidden


def test_country_filter(app):
    t = _tab(app)
    t.set_countries({"USD"})
    assert t.visible_event_count() == 2     # only US events


def test_countdown_text_for_future_event(app):
    t = _tab(app)
    # pin "now" 90 minutes before the Wednesday GDP event
    t.set_now_ms(TS_WED - 90 * 60_000)
    assert t.countdown_text(TS_WED) == "Coming in 1:30:00"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_economic_calendar_gui.py -v`
Expected: FAIL — `EconomicCalendarTab` missing.

- [ ] **Step 3: Write minimal implementation**

```python
# src/vike_trader_app/ui/economic_calendar.py
"""TradingView-style Economic Calendar tab.

A grouped QTreeWidget (Date header → event rows) fed by a CalendarRepository. Pure-Qt,
dependency-injected repository (tests pass a fake; no network, no modals). Filters,
live countdown and the now-line are computed against an injectable `now_ms`.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme
from .calendar_delegate import importance_bar_pixmap, value_color
from ..data.calendar.model import week_start_utc

_COLS = ["Time", "Country", "", "Event", "Actual", "Forecast", "Prior"]


class EconomicCalendarTab(QtWidgets.QWidget):
    def __init__(self, repository=None, parent=None):
        super().__init__(parent)
        if repository is None:
            from ..data.calendar.repository import default_repository
            repository = default_repository()
        self._repo = repository
        self._events: list = []
        self._high_only = False
        self._countries: set[str] | None = None      # None = all
        self._now = lambda: int(time.time() * 1000)
        self._week_start = week_start_utc(self._now())

        root = QtWidgets.QVBoxLayout(self)
        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet(f"color:{theme.TEXT2};font-size:11px;")
        self._tree = QtWidgets.QTreeWidget()
        self._tree.setColumnCount(len(_COLS))
        self._tree.setHeaderLabels(_COLS)
        self._tree.setRootIsDecorated(False)
        self._tree.setIndentation(0)
        self._tree.setAlternatingRowColors(False)
        self._tree.header().setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        root.addWidget(self._status)
        root.addWidget(self._tree, 1)

    # ---- data ----
    def load_week(self, week_start_ms: int) -> None:
        self._week_start = week_start_ms
        self._events = self._repo.get_week(week_start_ms)
        self._rebuild()

    def _passes(self, ev) -> bool:
        if self._high_only and ev.importance < 2:
            return False
        if self._countries is not None and ev.currency not in self._countries:
            return False
        return True

    def _rebuild(self) -> None:
        self._tree.clear()
        groups: dict[str, QtWidgets.QTreeWidgetItem] = {}
        for ev in sorted(self._events, key=lambda e: (e.ts_utc, e.country, e.title)):
            if not self._passes(ev):
                continue
            day = self._date_header(ev.ts_utc)
            parent = groups.get(day)
            if parent is None:
                parent = QtWidgets.QTreeWidgetItem([day])
                parent.setFirstColumnSpanned(True)
                f = parent.font(0); f.setBold(True); parent.setFont(0, f)
                self._tree.addTopLevelItem(parent)
                parent.setExpanded(True)
                groups[day] = parent
            parent.addChild(self._row(ev))

    def _row(self, ev) -> QtWidgets.QTreeWidgetItem:
        t = "" if ev.all_day else self._hhmm(ev.ts_utc)
        actual = self.countdown_text(ev.ts_utc) if ev.actual is None and ev.ts_utc > self._now() \
            else ev.actual_display or "—"
        it = QtWidgets.QTreeWidgetItem([t, ev.country, "", ev.title, actual,
                                        ev.forecast_display or "—", ev.previous_display or "—"])
        it.setData(0, QtCore.Qt.UserRole, ev.id)
        it.setIcon(2, QtGui.QIcon(importance_bar_pixmap(ev.importance)))
        it.setForeground(4, QtGui.QColor(value_color(ev.actual, ev.forecast)))
        if ev.actual is None and ev.ts_utc > self._now():
            it.setForeground(4, QtGui.QColor(theme.DOWN))   # red "Coming in …"
        return it

    # ---- filters (return nothing; trigger a rebuild) ----
    def set_high_only(self, on: bool) -> None:
        self._high_only = on; self._rebuild()

    def set_countries(self, currencies: set[str] | None) -> None:
        self._countries = currencies; self._rebuild()

    def visible_event_count(self) -> int:
        n = 0
        for i in range(self._tree.topLevelItemCount()):
            n += self._tree.topLevelItem(i).childCount()
        return n

    # ---- time helpers ----
    def set_now_ms(self, ms: int) -> None:
        self._now = lambda: ms; self._rebuild()

    def countdown_text(self, ts_utc: int) -> str:
        secs = max(0, (ts_utc - self._now()) // 1000)
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        return f"Coming in {h}:{m:02d}:{s:02d}"

    @staticmethod
    def _date_header(ts_utc: int) -> str:
        # NB: %-d / %#d are not portable across OSes — build the day number manually.
        dt = datetime.fromtimestamp(ts_utc / 1000, tz=timezone.utc)
        return f"{dt.strftime('%A, %B')} {dt.day}"

    @staticmethod
    def _hhmm(ts_utc: int) -> str:
        return datetime.fromtimestamp(ts_utc / 1000, tz=timezone.utc).strftime("%H:%M")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_economic_calendar_gui.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/vike_trader_app/ui/economic_calendar.py tests/test_economic_calendar_gui.py
git commit -m "feat(calendar): EconomicCalendarTab grouped tree + filters + countdown"
```

---

### Task 13: toolbar (week nav, Today, filters, timezone) + week strip

**Files:**
- Modify: `src/vike_trader_app/ui/economic_calendar.py`
- Test: `tests/test_economic_calendar_gui.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_economic_calendar_gui.py  (append)
def test_week_nav_changes_week_and_reloads(app):
    t = _tab(app)
    start = t.current_week_start()
    t.go_next_week()
    assert t.current_week_start() == start + 7 * 24 * 3600 * 1000
    t.go_today()
    assert t.current_week_start() == week_start_utc(t._now())


def test_week_strip_has_seven_day_cards(app):
    t = _tab(app)
    assert t.day_card_count() == 7


def test_category_filter(app):
    t = _tab(app)                  # GDP event has category "other" in the fixture builder
    t.set_category("inflation")
    # only events categorized inflation remain; fixture builder uses "other", so expect 0
    assert t.visible_event_count() == 0
    t.set_category("All")
    assert t.visible_event_count() == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_economic_calendar_gui.py -k "week_nav or week_strip or category" -v`
Expected: FAIL — methods missing.

- [ ] **Step 3: Write minimal implementation** (add to `EconomicCalendarTab`)

```python
# economic_calendar.py — add a _category filter field in __init__: self._category = "All"
# Build a toolbar + week strip above the tree in __init__ (insert before addWidget(self._tree)).

    def _build_toolbar(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(bar); h.setContentsMargins(0, 0, 0, 0)
        self._btn_today = QtWidgets.QPushButton("Today"); self._btn_today.clicked.connect(self.go_today)
        prev = QtWidgets.QPushButton("‹"); prev.clicked.connect(self.go_prev_week)
        nxt = QtWidgets.QPushButton("›"); nxt.clicked.connect(self.go_next_week)
        self._lbl_range = QtWidgets.QLabel("")
        self._chk_high = QtWidgets.QCheckBox("High only")
        self._chk_high.toggled.connect(self.set_high_only)
        self._cmb_cat = QtWidgets.QComboBox()
        self._cmb_cat.addItems(["All", "rates", "inflation", "employment", "gdp", "trade", "housing", "other"])
        self._cmb_cat.currentTextChanged.connect(self.set_category)
        for w in (self._btn_today, prev, nxt, self._lbl_range):
            h.addWidget(w)
        h.addStretch(1)
        h.addWidget(self._chk_high); h.addWidget(self._cmb_cat)
        return bar

    def _build_week_strip(self) -> QtWidgets.QWidget:
        self._strip = QtWidgets.QWidget()
        self._strip_layout = QtWidgets.QHBoxLayout(self._strip)
        self._strip_layout.setContentsMargins(0, 0, 0, 0)
        self._day_cards = []          # list[tuple[QFrame, QLabel title, QLabel count]]
        for _ in range(7):
            card = QtWidgets.QFrame(); card.setProperty("class", "Panel")
            v = QtWidgets.QVBoxLayout(card)
            title = QtWidgets.QLabel(""); title.setStyleSheet(f"color:{theme.TEXT};font-weight:600;")
            count = QtWidgets.QLabel(""); count.setStyleSheet(f"color:{theme.TEXT2};font-size:11px;")
            v.addWidget(title); v.addWidget(count)
            self._day_cards.append((card, title, count))
            self._strip_layout.addWidget(card)
        return self._strip

    def day_card_count(self) -> int:
        return len(self._day_cards)

    def _refresh_strip(self) -> None:
        """Fill the 7 day-cards with weekday+date and that day's event count."""
        from datetime import datetime, timezone, timedelta
        day_ms = 24 * 3600 * 1000
        for i, (_card, title, count) in enumerate(self._day_cards):
            start = self._week_start + i * day_ms
            dt = datetime.fromtimestamp(start / 1000, tz=timezone.utc)
            n = sum(1 for e in self._events if start <= e.ts_utc < start + day_ms)
            title.setText(f"{dt.strftime('%a')} {dt.day}")
            count.setText(f"Economic {n}")

    def _refresh_range_label(self) -> None:
        from datetime import datetime, timezone
        a = datetime.fromtimestamp(self._week_start / 1000, tz=timezone.utc)
        b = datetime.fromtimestamp((self._week_start + 6 * 24 * 3600 * 1000) / 1000, tz=timezone.utc)
        self._lbl_range.setText(f"{a.strftime('%b')} {a.day} — {b.strftime('%b')} {b.day}, {b.year}")

    def current_week_start(self) -> int:
        return self._week_start

    def go_today(self) -> None:
        self.load_week(week_start_utc(self._now()))

    def go_prev_week(self) -> None:
        self.load_week(self._week_start - 7 * 24 * 3600 * 1000)

    def go_next_week(self) -> None:
        self.load_week(self._week_start + 7 * 24 * 3600 * 1000)

    def set_category(self, cat: str) -> None:
        self._category = cat; self._rebuild()
```

Update `_passes()` to honor category:

```python
    def _passes(self, ev) -> bool:
        if self._high_only and ev.importance < 2:
            return False
        if self._countries is not None and ev.currency not in self._countries:
            return False
        if self._category != "All" and ev.category != self._category:
            return False
        return True
```

At the **end** of `_rebuild()`, refresh the header widgets (guarded — `_rebuild` first runs in
Task 12 before the strip/label exist):

```python
        if self._day_cards:
            self._refresh_strip()
        if hasattr(self, "_lbl_range"):
            self._refresh_range_label()
```

In `__init__`, add the defaults **before** the first `_rebuild` can run, then wire the new
widgets in above the tree:
```python
        # defaults (place near the other state defaults, before any _rebuild)
        self._category = "All"
        self._day_cards = []
        ...
        root.addWidget(self._build_toolbar())
        root.addWidget(self._build_week_strip())
        root.addWidget(self._status)
        root.addWidget(self._tree, 1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_economic_calendar_gui.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/vike_trader_app/ui/economic_calendar.py tests/test_economic_calendar_gui.py
git commit -m "feat(calendar): toolbar (week nav/filters/category) + week strip"
```

---

### Task 14: background fetch worker + live countdown timer

**Files:**
- Modify: `src/vike_trader_app/ui/economic_calendar.py`
- Test: `tests/test_economic_calendar_gui.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_economic_calendar_gui.py  (append)
from vike_trader_app.ui.economic_calendar import _CalendarFetchWorker


def test_fetch_worker_emits_events(app, qtbot=None):
    repo = _FakeRepo([_ev(TS_TUE, "USD", "CPI", 2, actual=3.2, forecast=3.1)])
    worker = _CalendarFetchWorker(repo, WK)
    got = {}
    worker.eventsReady.connect(lambda evs: got.setdefault("evs", evs))
    worker.run()                      # call run() directly (no thread) for a deterministic test
    assert got["evs"][0].title == "CPI"


def test_tick_refreshes_only_future_countdowns(app):
    t = _tab(app)
    t.set_now_ms(TS_WED - 2 * 60_000)         # 2 minutes before GDP
    assert t.countdown_text(TS_WED) == "Coming in 0:02:00"
    t.set_now_ms(TS_WED - 60_000)
    t._tick()                                  # advance; should not raise, recomputes labels
    assert t.countdown_text(TS_WED) == "Coming in 0:01:00"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_economic_calendar_gui.py -k "fetch_worker or tick" -v`
Expected: FAIL — `_CalendarFetchWorker` / `_tick` missing.

- [ ] **Step 3: Write minimal implementation**

```python
# economic_calendar.py — add at module level
class _CalendarFetchWorker(QtCore.QThread):
    """Off-thread week fetch (network + JSON only — safe off the UI thread, like
    app._LiveFetchWorker). Results marshal back via signals; the UI never blocks."""
    eventsReady = QtCore.Signal(object)   # list[CalendarEvent]
    failed = QtCore.Signal(str)

    def __init__(self, repo, week_start_ms: int, *, force: bool = False):
        super().__init__()
        self._repo, self._ws, self._force = repo, week_start_ms, force

    def run(self):
        try:
            self.eventsReady.emit(self._repo.get_week(self._ws, force=self._force))
        except Exception as exc:  # noqa: BLE001 - surfaced to a status label, never a modal
            self.failed.emit(str(exc))
```

```python
# economic_calendar.py — in __init__, set up a 1s timer and start it
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._worker = None
```

```python
# economic_calendar.py — async load used by the live app (tests still call load_week directly)
    def refresh_async(self, *, force: bool = False) -> None:
        self._status.setText("Loading…")
        self._worker = _CalendarFetchWorker(self._repo, self._week_start, force=force)
        self._worker.eventsReady.connect(self._on_events)
        self._worker.failed.connect(lambda msg: self._status.setText(f"Calendar error: {msg}"))
        self._worker.start()

    def _on_events(self, events) -> None:
        self._events = events
        self._status.setText("")
        self._rebuild()

    def _tick(self) -> None:
        # cheap: only touch rows that show a countdown (future, no actual)
        now = self._now()
        for i in range(self._tree.topLevelItemCount()):
            top = self._tree.topLevelItem(i)
            for j in range(top.childCount()):
                row = top.child(j)
                ev_id = row.data(0, QtCore.Qt.UserRole)
                ev = next((e for e in self._events if e.id == ev_id), None)
                if ev and ev.actual is None and ev.ts_utc > now:
                    row.setText(4, self.countdown_text(ev.ts_utc))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_economic_calendar_gui.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/vike_trader_app/ui/economic_calendar.py tests/test_economic_calendar_gui.py
git commit -m "feat(calendar): off-thread fetch worker + live countdown timer"
```

---

### Task 15: expandable per-event detail row

**Files:**
- Modify: `src/vike_trader_app/ui/economic_calendar.py`
- Test: `tests/test_economic_calendar_gui.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_economic_calendar_gui.py  (append)
def test_clicking_event_toggles_detail_child(app):
    t = _tab(app)
    top = t._tree.topLevelItem(0)
    row = top.child(0)
    assert row.childCount() == 0
    t._toggle_detail(row)
    assert row.childCount() == 1            # detail node added
    assert "Forecast" in row.child(0).text(0) or row.child(0).text(0) != ""
    t._toggle_detail(row)
    assert row.childCount() == 0            # collapses again
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_economic_calendar_gui.py -k "detail" -v`
Expected: FAIL — `_toggle_detail` missing.

- [ ] **Step 3: Write minimal implementation**

```python
# economic_calendar.py — connect in __init__ after building the tree:
        self._tree.itemClicked.connect(lambda it, _c: self._toggle_detail(it))

    def _toggle_detail(self, row) -> None:
        ev_id = row.data(0, QtCore.Qt.UserRole)
        if ev_id is None:                      # date header or a detail node itself
            return
        if row.childCount():
            row.takeChildren()
            return
        ev = next((e for e in self._events if e.id == ev_id), None)
        if ev is None:
            return
        text = (f"{ev.title} · {ev.country}  |  "
                f"Actual {ev.actual_display or '—'} · "
                f"Forecast {ev.forecast_display or '—'} · "
                f"Prior {ev.previous_display or '—'}"
                + (f"  ·  actual via {ev.actual_source}" if ev.actual_source else ""))
        detail = QtWidgets.QTreeWidgetItem([text])
        detail.setFirstColumnSpanned(True)
        detail.setForeground(0, QtGui.QColor(theme.TEXT2))
        row.addChild(detail)
        row.setExpanded(True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_economic_calendar_gui.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/vike_trader_app/ui/economic_calendar.py tests/test_economic_calendar_gui.py
git commit -m "feat(calendar): expandable per-event detail row"
```

---

## Phase 7 — App integration & assets

### Task 16: wire the Calendar tab into `MainWindow`

**Files:**
- Modify: `src/vike_trader_app/ui/app.py` (import; `_build_central` ~line 364; `_RAIL_ITEMS` ~line 393)
- Test: `tests/test_economic_calendar_gui.py` (append a smoke test that builds MainWindow)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_economic_calendar_gui.py  (append)
def test_mainwindow_registers_calendar_rail_item():
    # Class-attribute check — no MainWindow construction (which loads symbols and can be
    # flaky offscreen). Verifies the rail wiring; the actual addTab is checked manually
    # (Task 18) and guarded by the rail-count == tab-count invariant in the app.
    from vike_trader_app.ui.app import MainWindow
    assert ("▦", "Calendar") in MainWindow._RAIL_ITEMS
```

Rationale: constructing `MainWindow` triggers background symbol loading and other heavy
setup that's noisy in headless tests. The rail list is the single source of truth for the
spaces, so asserting the calendar entry exists there confirms the wiring cheaply and
deterministically. (`EconomicCalendarTab()` itself does **not** touch the network at
construction — providers are lazy and `get_week` runs only on demand — so the live tab is
safe to instantiate in `_build_central`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_economic_calendar_gui.py -k "rail_item" -v`
Expected: FAIL — `("▦", "Calendar")` not in `_RAIL_ITEMS`.

- [ ] **Step 3: Write minimal implementation**

In `app.py`, add the import near the other tab imports:
```python
from .economic_calendar import EconomicCalendarTab
```
In `_build_central()`, after the Data tab block (~line 365), add:
```python
        self.economic_calendar = EconomicCalendarTab()
        self.tabs.addTab(self.economic_calendar, "Calendar")
```
Extend `_RAIL_ITEMS` (~line 393) with a calendar glyph:
```python
    _RAIL_ITEMS = [
        ("▤", "Chart"), ("✦", "Studio"), ("⚙", "Tools"),
        ("⊞", "Screener"), ("☰", "Journal"), ("◉", "Alerts"), ("◈", "Data"),
        ("▦", "Calendar"),
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_economic_calendar_gui.py -v`
Expected: PASS (all). Also run the whole calendar suite:
`python -m pytest tests/ -k calendar -v` → all green.

- [ ] **Step 5: Commit**

```bash
git add src/vike_trader_app/ui/app.py tests/test_economic_calendar_gui.py
git commit -m "feat(calendar): wire Calendar tab + rail item into MainWindow"
```

---

### Task 17: flag chips + initial async load

**Files:**
- Modify: `src/vike_trader_app/ui/economic_calendar.py`
- Test: `tests/test_economic_calendar_gui.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_economic_calendar_gui.py  (append)
def test_country_cell_shows_iso_chip_when_no_flag_asset(app):
    from vike_trader_app.ui.economic_calendar import country_chip_pixmap
    pm = country_chip_pixmap("us")
    assert not pm.isNull()
    pm2 = country_chip_pixmap("")          # unknown → still returns a (blank) pixmap, no crash
    assert pm2 is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_economic_calendar_gui.py -k "iso_chip" -v`
Expected: FAIL — `country_chip_pixmap` missing.

- [ ] **Step 3: Write minimal implementation**

```python
# economic_calendar.py — flag/chip helper. Loads resources/flags/{iso}.png if present;
# otherwise paints a small rounded chip with the ISO code so it works with no assets yet.
import os

_FLAG_DIR = os.path.join(os.path.dirname(__file__), "resources", "flags")


def country_chip_pixmap(iso2: str) -> QtGui.QPixmap:
    if iso2:
        path = os.path.join(_FLAG_DIR, f"{iso2}.png")
        if os.path.exists(path):
            pm = QtGui.QPixmap(path)
            if not pm.isNull():
                return pm.scaledToHeight(14, QtCore.Qt.SmoothTransformation)
    pm = QtGui.QPixmap(20, 14)
    pm.fill(QtCore.Qt.transparent)
    if iso2:
        p = QtGui.QPainter(pm)
        p.setPen(QtGui.QColor(theme.TEXT3))
        p.drawRoundedRect(0, 0, 19, 13, 3, 3)
        f = p.font(); f.setPointSize(6); p.setFont(f)
        p.drawText(pm.rect(), QtCore.Qt.AlignCenter, iso2.upper())
        p.end()
    return pm
```

Use it in `_row()` (set the country column icon) by mapping currency→iso:
```python
# at top of _row(): from ..data.calendar.taxonomy import currency_country
        _country, iso = currency_country(ev.currency)
        it.setIcon(1, QtGui.QIcon(country_chip_pixmap(iso)))
```

Also create the resources dir so the path exists:
```bash
mkdir -p src/vike_trader_app/ui/resources/flags
```
(Real flag PNGs are an optional later drop-in; the ISO chip is the graceful fallback.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_economic_calendar_gui.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/vike_trader_app/ui/economic_calendar.py tests/test_economic_calendar_gui.py
git commit -m "feat(calendar): flag/ISO country chips with asset fallback"
```

---

## Phase 8 — Verification

### Task 18: full suite + manual screenshot (dark theme)

- [ ] **Step 1: Run the full calendar suite + a lint pass**

Run: `python -m pytest tests/ -k calendar -v`
Expected: all green.
Run: `python -m ruff check src/vike_trader_app/data/calendar src/vike_trader_app/ui/economic_calendar.py src/vike_trader_app/ui/calendar_delegate.py`
Expected: no errors (fix any).

- [ ] **Step 2: Run the whole test suite to confirm no regressions**

Run: `python -m pytest tests/ -q`
Expected: pre-existing pass count + the new calendar tests, 0 failures. (Investigate any new failure before proceeding.)

- [ ] **Step 3: Manual visual check (dark theme)**

Use the `run-app` skill to launch the GUI, click the new Calendar rail item, and screenshot.
Verify against the spec: date→country grouping, 1–3 importance bars, green/red actuals,
"Coming in …" countdown on a future event, week nav, High-only + category filters,
all rendered in the dark palette. Capture a screenshot for the PR.

- [ ] **Step 4: Optional live smoke (network)**

With a `FRED_API_KEY` in `.env`, launch and confirm at least one US event shows a
backfilled actual with `actual via FRED` in its detail row. Without keys, confirm the
calendar still loads schedule + forecast/previous (graceful degradation).

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "test(calendar): full-suite green + lint clean"
```

---

## Self-Review notes (coverage map)

- Spec §2 data sources → Tasks 6 (ForexFactory), 8 (FRED), 9 (BLS/BEA/Census/ECB).
- Spec §3 model/providers/aggregator → Tasks 1–3, 4, 6, 8–10.
- Spec §4 caching → Task 7; meta/rate-limit → Tasks 7, 10.
- Spec §5 threading → Task 14 (`_CalendarFetchWorker` + `QTimer`).
- Spec §6 UI (tree, delegate, toolbar, week strip, countdown, detail, flags) →
  Tasks 11–15, 17. The literal red **now-line** between rows is approximated in v1 by the
  red "Coming in …" countdown + red time styling on imminent events (drawn separator line
  is a follow-up — see deferrals).
- Spec §7 app integration → Task 16.
- Spec §8 testing → tests in every task; GUI tests offscreen + DI + no modals.
- Spec §2.3 limitations → honored in code (graceful no-key degradation; cache-served weeks;
  unmapped events leave `actual` blank, never wrong).

**Known deferrals (call out in PR, not silently dropped):** real flag PNG assets (ISO chip
fallback ships), the prior-history sparkline in the detail panel (text detail ships),
timezone selector UI (events stored UTC, rendered UTC in v1 — selector is a small follow-up),
the drawn red now-line separator (approximated by red countdown/time styling in v1), and the
non-Economic tabs (Earnings/Revenue/Dividends/IPO) which the spec scopes out of v1.

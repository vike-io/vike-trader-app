# Economic Calendar (TradingView-style) — Design Spec

**Date:** 2026-06-02
**Branch / worktree:** `worktree-economic-calendar`
**Status:** Approved design, pending spec review

---

## 1. Goal

Add an **Economic Calendar** panel to vike-trader-app that replicates TradingView's
economic calendar (https://www.tradingview.com/economic-calendar/) as a **full clone**:
week navigation with per-day category counts, date→country grouping, 1–3 bar importance,
country / importance / category / timezone filters, a live "Coming in HH:MM" countdown,
a red current-time line, and expandable per-event detail.

The panel renders natively in the app's existing dark palette (`theme.BG = #0d1117`),
matching TradingView's dark theme.

### Non-goals (v1)
- Earnings / Revenue / Dividends / IPO tabs (different data sources) — rendered as
  tabs but show a "coming soon" empty state.
- Deep historical / far-future schedule beyond what the free schedule source publishes
  plus what we have locally cached.
- Plotting events onto the price chart (possible future enhancement).

---

## 2. Data sources

TradingView does not expose a usable public feed (its endpoint is Origin-locked and
ToS-restricted). We assemble an equivalent from **free, legal** sources via a pluggable
provider interface, so sources can be added/swapped without touching the UI.

### 2.1 Schedule provider (the event list)
**ForexFactory weekly JSON** — their own published static files (sanctioned; used by their
MT4/MT5 indicators), **no API key**:
- `https://nfs.faireconomy.media/ff_calendar_thisweek.json`
- `https://nfs.faireconomy.media/ff_calendar_nextweek.json`

Each record: `title`, `country` (currency/ISO-ish code: `USD`/`EUR`/`GBP`/…), `date`
(ISO-8601 with timezone), `impact` (`High`/`Medium`/`Low`/`Holiday`), `forecast`,
`previous`. **No `actual` field** (actuals are published on-site after release, not in the
feed) — this is what the backfill layer (2.2) is for.

**Rate limit:** ~2 downloads / 5 minutes. We fetch at most once per refresh interval and
cache aggressively (§4).

### 2.2 Actuals backfill providers (fill `actual` after release)
Priority-ordered. Each owns a curated **event-title → data-series** mapping table covering
the **high/medium-impact events first**, expandable over time. All optional; a missing
API key disables only that provider.

| Provider | Source | Key | Covers |
|---|---|---|---|
| `FredProvider` | FRED (St. Louis Fed) `series/observations`, `releases/dates` | free key | Broad US macro |
| `BlsProvider` | BLS API v2 | optional key | NFP / CPI / unemployment |
| `BeaProvider` | BEA API | free key | GDP / PCE |
| `CensusProvider` | US Census | free key | Retail sales / housing |
| `EcbProvider` | ECB SDW | no key | EU rates / inflation |

### 2.3 Honest coverage limitations (must be communicated in-app)
- **Schedule horizon:** ForexFactory only publishes *this week* + *next week*. Past weeks
  come from local cache; we cannot show arbitrary deep history/future like TradingView.
- **Actuals:** comprehensive for US, partial for EU/UK, sparse elsewhere in v1; grows as
  mapping tables expand. Events with no mapped actual render `—` until/unless ForexFactory
  itself later carries the value.
- **Forecast/Previous:** only where ForexFactory provides them.

---

## 3. Data layer — `src/vike_trader_app/data/calendar/`

### 3.1 Model — `CalendarEvent` (dataclass)
| Field | Type | Notes |
|---|---|---|
| `id` | `str` | stable hash of `date` + `country` + `title`; dedup/merge + expand-state key |
| `ts_utc` | `int` | event time, epoch ms UTC (ForexFactory ISO → UTC) |
| `all_day` | `bool` | "All Day" / tentative events |
| `country` | `str` | normalized country code |
| `currency` | `str` | as given by ForexFactory (`USD`, `EUR`, …) |
| `title` | `str` | event name (e.g. "Non-Farm Payrolls") |
| `category` | `str` | derived bucket (rates / inflation / employment / GDP / trade / housing / other) |
| `importance` | `int` | `0` low / `1` med / `2` high (ForexFactory `Holiday` → low/flag) |
| `actual` / `forecast` / `previous` | `float \| None` | parsed numeric for beat/miss coloring |
| `unit` | `str` | `%`, `$`, `€`, `£`, `A$`, `K`, `M`, `B`, … parsed from the display string |
| `actual_display` / `forecast_display` / `previous_display` | `str` | raw strings (`−27.1 B A$`) — source of truth for rendering |
| `actual_source` | `str \| None` | which provider filled `actual` (e.g. `"FRED"`) |

Display strings are authoritative for what's shown; parsed `float` + `unit` drive
comparison (beat/miss) and any future charting.

### 3.2 Provider interfaces
```python
class ScheduleProvider(Protocol):
    def fetch_week(self, week_start_utc: int) -> list[CalendarEvent]: ...

class ActualsProvider(Protocol):
    name: str
    def backfill(self, events: list[CalendarEvent]) -> dict[str, ActualValue]:
        """Return {event_id: ActualValue(value, unit)} for events it can fill."""
```
Concrete: `ForexFactoryProvider`, `FredProvider`, `BlsProvider`, `BeaProvider`,
`CensusProvider`, `EcbProvider`. Each provider module is independently unit-testable with
mocked HTTP (fixtures), and owns its own mapping table.

### 3.3 Aggregator — `CalendarRepository`
```python
class CalendarRepository:
    def __init__(self, schedule, actuals_providers, store, *, http=requests): ...
    def get_week(self, week_start_utc: int, *, force: bool = False) -> list[CalendarEvent]:
        # 1. load cached week from store
        # 2. if missing/stale (and within rate budget) → schedule.fetch_week()
        # 3. merge into cache, dedup by id (new schedule wins for fcst/prev/time)
        # 4. for past events still missing `actual`, run actuals_providers in priority order
        # 5. persist, return sorted events
```
Backfill matches events by `(country, normalized_title, reference_period)`. Title
normalization: lowercase, strip qualifiers (`Flash`, `Prel`, `Final`, `MoM`, `YoY` handled
per mapping). Unmapped events are simply left without `actual`.

### 3.4 Config / keys
API keys read from environment / a config file (`FRED_API_KEY`, `BEA_API_KEY`,
`BLS_API_KEY`, `CENSUS_API_KEY`). No key → provider skipped silently. ForexFactory needs
none, so the calendar is always functional (forecast/previous always present).

---

## 4. Caching & persistence

- `CalendarStore` — JSON file per ISO week under `storage/calendar/YYYY-Www.json`
  (mirrors `analysis/journal.py`'s load/save pattern). Stores the merged event list.
- `storage/calendar/meta.json` — last-fetch timestamp per source to respect ForexFactory's
  2 dl / 5 min cap and drive staleness checks.
- `calendar_settings.json` — selected countries, importance filter, category, timezone,
  and expanded-row ids; persisted on change, restored on open.

---

## 5. Threading

- A `QThread` worker (`_CalendarFetchWorker`, mirroring `_LiveFeedWorker`) performs all
  network I/O (ForexFactory + actuals, plain `requests`/JSON) and emits
  `eventsReady(list[CalendarEvent])` back to the main thread. **All widget mutations happen
  on the main thread.**
  - Note: the repo's "no Parquet reads off the main thread" rule does **not** apply here —
    we do HTTP+JSON, not Parquet/Catalog reads.
- A `QTimer` (1 s tick) updates the **"Coming in HH:MM" countdown** cells and the **red
  now-line** position without refetching.
- Schedule auto-refresh: every N minutes for the visible week, within the rate budget;
  actuals re-checked for just-passed events.

---

## 6. UI — `src/vike_trader_app/ui/economic_calendar.py`

`EconomicCalendarTab(QtWidgets.QWidget)`, styled with existing `theme` tokens.
Constructor takes an injectable `repository` (tests pass a fake — no network, no modals).
**Render approach B: grouped `QTreeWidget` + custom delegate.**

Top → bottom:

1. **Toolbar:** `Today` · week `‹ ›` · week-range label · stretch · **Countries** filter
   (popup checkable list + "Top 20" / "Entire world" presets) · **Importance** toggle
   (all / high-only) with low/med/high checkboxes · **Category** dropdown (All categories +
   rates/inflation/employment/GDP/trade/housing) · **Timezone** selector (default = local) ·
   **Refresh**.
2. **Week strip:** 7 day-cards (`QFrame`, like `ReportPanel` cards) showing weekday + date
   and per-category counts; current day highlighted; clicking a card scrolls to that day.
3. **Calendar tabs:** Economic / Earnings / Revenue / Dividends / IPO. **Economic fully
   built**; the others are present but show a "coming soon" empty state.
4. **Grouped `QTreeWidget`:** top-level **date-header** rows ("Tuesday, June 2") spanning
   full width; child **event rows** with inline flag on the first event of each country run.
   Columns: **Time | Country | Importance | Event | Actual | Forecast | Prior**.
   - A `QStyledItemDelegate` paints the **1–3 importance bars**, the **green/red Actual**
     (beat = green, miss = red, vs forecast/prior), value **units**, and the red
     "Coming in …" countdown.
   - Expanding an event row reveals a **detail panel**: description, source link, and a
     prior-history **sparkline** (sparkline is a stretch goal; degrade to text if cut).
5. **Now-line & countdown:** red line under the current date group; imminent event's time
   badge turns red; driven by the `QTimer`.

### 6.1 Flag assets
Windows does not render emoji flags. Bundle a small ISO-coded flag set under
`src/vike_trader_app/ui/resources/flags/` (SVG or PNG), keyed by a currency→country→ISO map.
Only the covered countries are needed initially.

---

## 7. App integration — `src/vike_trader_app/ui/app.py`

- Instantiate `EconomicCalendarTab` in `_build_central()`; `self.tabs.addTab(tab, "Calendar")`.
- Add a rail item (calendar glyph) to `_RAIL_ITEMS` and wire its button to the tab index,
  exactly like the existing Alerts/Screener rail entries.

---

## 8. Testing

Offscreen Qt (`QT_QPA_PLATFORM=offscreen`), dependency injection, **no modal dialogs**
(honors the CI-headless-hang rule).

- `tests/test_calendar_providers.py`
  - ForexFactory JSON fixture → `CalendarEvent` list; unit parsing (`%`, `B`, `K`, `A$`, …);
    `impact`→`importance` mapping; ISO date → `ts_utc`.
  - FRED / BLS / BEA mapping with mocked HTTP responses.
  - `CalendarRepository` merge + dedup-by-id + backfill priority order; staleness/rate logic.
- `tests/test_economic_calendar_gui.py`
  - Inject a **fake repository** returning canned events; assert date/country grouping,
    importance-delegate data, countdown formatting, beat/miss coloring, and that each filter
    (country / importance / category) reduces rows as expected.

---

## 9. Build sequence

1. `CalendarEvent` model + value/unit parsing.
2. `ForexFactoryProvider` (+ JSON fixture & tests).
3. `CalendarStore` (JSON-per-ISO-week) + `meta.json`.
4. Actuals providers — `FredProvider`, then `Bls`/`Bea`/`Census`/`Ecb` — each with mapping
   table + mocked tests.
5. `CalendarRepository` aggregator (merge + backfill + staleness/rate budget).
6. `EconomicCalendarTab` UI: tree + delegate + toolbar + week strip (against a fake repo).
7. `_CalendarFetchWorker` threading + `QTimer` live countdown / now-line.
8. App wiring (rail item + tab) + flag assets.
9. GUI + provider tests green; manual screenshot verification (dark theme) via run-app.

All work on branch `worktree-economic-calendar` in the dedicated git worktree.

---

## 10. Open risks

- **Title↔series mapping is manual** and the main ongoing maintenance cost; start small
  (high/med-impact US events) and grow. Mapping misses simply leave `actual` blank — never
  wrong data.
- **ForexFactory feed stability:** it's a published file, but format could change; isolate
  parsing in `ForexFactoryProvider` behind tests with a captured fixture.
- **Country/currency normalization:** ForexFactory uses currency-ish codes; the
  currency→country→flag/ISO map must be explicit and tested.
- **Timezone correctness:** store UTC, convert at render; cover DST in tests.

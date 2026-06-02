"""Import third-party OHLCV CSV exports into ``Bar``s — the QDM/Wealth-Lab "Import" flow.

Real exports vary wildly, so the parser is deliberately lenient:

* **delimiter** — comma, semicolon, or tab (auto-detected);
* **header** — recognised by name (``open``/``high``/…); falls back to positional order;
* **timestamp** — a single datetime column *or* split ``Date`` + ``Time`` columns; epoch
  seconds/ms; ISO ``2024-01-02 15:04:00``, MT4 ``2024.01.02``, Dukascopy ``02.01.2024 …``;
* **timezone** — source stamps are shifted to **UTC** (the one storage timezone) via
  ``tz_offset_minutes`` = the source's offset from UTC;
* **aggregate** — :func:`aggregate` resamples the parsed bars to a higher timeframe on import.

Bad rows are skipped (not fatal); output is sorted and de-duplicated by timestamp (last wins).
"""

import csv
import io
from datetime import datetime, timezone

from ..core.model import Bar
from ..core.timeframe import parse_timeframe, resample

# Header aliases (lower-cased, stripped) -> canonical field.
_OHLCV = {
    "open": {"open", "o"}, "high": {"high", "h"}, "low": {"low", "l"},
    "close": {"close", "c", "last", "price"}, "volume": {"volume", "vol", "v", "tickvol"},
}
_DATE_KEYS = {"date", "day", "<date>"}
_TIME_KEYS = {"time", "<time>"}
_DATETIME_KEYS = {"datetime", "timestamp", "date_time", "date time", "gmt time", "gmttime",
                  "local time", "localtime", "time"}

_DT_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M",
    "%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y.%m.%d",
    "%d.%m.%Y %H:%M:%S.%f", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
    "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d",
)


def _detect_delimiter(sample: str) -> str:
    line = sample.splitlines()[0] if sample.splitlines() else ""
    for d in (",", ";", "\t", "|"):
        if d in line:
            return d
    return ","


def _parse_datetime(token: str, tz_offset_minutes: int) -> int | None:
    """One datetime/epoch token -> epoch ms in UTC, or None if unparseable."""
    s = token.strip().strip('"').strip()
    if not s:
        return None
    digits = s.replace(".", "", 1) if s.count(".") == 1 else s
    if digits.isdigit():  # epoch seconds (>=9 digits) or milliseconds (>=12 digits)
        n = int(float(s))
        ms = n if len(digits) >= 12 else n * 1000
        return ms - tz_offset_minutes * 60_000
    for fmt in _DT_FORMATS:
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        return int(dt.timestamp() * 1000) - tz_offset_minutes * 60_000
    return None


def _header_map(header: list[str]) -> dict | None:
    """Map a recognised header row to column indices, or None if it isn't a header."""
    idx = {h.strip().lower(): i for i, h in enumerate(header)}
    cols: dict[str, int] = {}
    for field, aliases in _OHLCV.items():
        hit = next((idx[a] for a in aliases if a in idx), None)
        if hit is not None:
            cols[field] = hit
    if not {"open", "high", "low", "close"} <= cols.keys():
        return None  # no recognisable OHLC header -> treat as headerless
    date_i = next((idx[k] for k in _DATE_KEYS if k in idx), None)
    time_i = idx.get("time") if "time" in idx else next((idx[k] for k in _TIME_KEYS if k in idx), None)
    if date_i is not None and time_i is not None and time_i != date_i:
        cols["date"], cols["time"] = date_i, time_i
    else:
        dt_i = next((idx[k] for k in _DATETIME_KEYS if k in idx), None)
        if dt_i is None:
            return None
        cols["datetime"] = dt_i
    return cols


def _positional_map(ncols: int) -> dict:
    """Column map for a headerless row by width: 7 = date,time,OHLCV; 6 = datetime,OHLCV; 5 = OHLC."""
    if ncols >= 7:
        return {"date": 0, "time": 1, "open": 2, "high": 3, "low": 4, "close": 5, "volume": 6}
    if ncols == 6:
        return {"datetime": 0, "open": 1, "high": 2, "low": 3, "close": 4, "volume": 5}
    return {"datetime": 0, "open": 1, "high": 2, "low": 3, "close": 4}


def _row_to_bar(row: list[str], cols: dict, tz_offset_minutes: int) -> Bar | None:
    try:
        if "datetime" in cols:
            ts = _parse_datetime(row[cols["datetime"]], tz_offset_minutes)
        else:
            ts = _parse_datetime(f"{row[cols['date']]} {row[cols['time']]}", tz_offset_minutes)
        if ts is None:
            return None
        vol = float(row[cols["volume"]]) if "volume" in cols and cols["volume"] < len(row) else 0.0
        return Bar(ts=ts, open=float(row[cols["open"]]), high=float(row[cols["high"]]),
                   low=float(row[cols["low"]]), close=float(row[cols["close"]]), volume=vol)
    except (ValueError, IndexError, KeyError):
        return None


def parse_csv(text: str, *, tz_offset_minutes: int = 0) -> list[Bar]:
    """Parse OHLCV ``text`` into UTC ``Bar``s — sorted, de-duplicated by ts (last wins).

    ``tz_offset_minutes`` is the source data's offset from UTC (e.g. ``120`` for UTC+2); stamps
    are shifted back to UTC. Unparseable rows are skipped.
    """
    if not text or not text.strip():
        return []
    delim = _detect_delimiter(text)
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if r and any(c.strip() for c in r)]
    if not rows:
        return []
    cols = _header_map(rows[0])
    data_rows = rows[1:] if cols is not None else rows
    if cols is None:
        cols = _positional_map(len(rows[0]))
    by_ts: dict[int, Bar] = {}
    for row in data_rows:
        bar = _row_to_bar(row, cols, tz_offset_minutes)
        if bar is not None:
            by_ts[bar.ts] = bar  # last duplicate wins
    return [by_ts[t] for t in sorted(by_ts)]


def aggregate(bars: list[Bar], interval: str) -> list[Bar]:
    """Resample parsed ``bars`` up to ``interval`` (e.g. ``"5m"``) using the canonical rule."""
    return resample(bars, parse_timeframe(interval))


def infer_interval_ms(bars: list[Bar]) -> int | None:
    """The most common gap between consecutive bars — the series' native interval (None if <2)."""
    if len(bars) < 2:
        return None
    gaps: dict[int, int] = {}
    for a, b in zip(bars, bars[1:]):
        g = b.ts - a.ts
        if g > 0:
            gaps[g] = gaps.get(g, 0) + 1
    return max(gaps, key=gaps.get) if gaps else None


_INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "1d": 86_400_000, "1w": 604_800_000,
}


def ms_to_interval(ms: int | None) -> str | None:
    """Label a millisecond gap as a known interval string (``60000 -> "1m"``), else None."""
    if ms is None:
        return None
    return next((k for k, v in _INTERVAL_MS.items() if v == ms), None)

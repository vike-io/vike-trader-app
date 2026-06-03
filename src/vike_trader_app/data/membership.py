"""Membership import utilities for dynamic DataSets.

Parses CSV text describing per-symbol membership windows (``symbol,start,end[,fate]``) into
``{symbol: [DateRange, ...]}`` dictionaries, and decodes WealthLab ``SYM.YYYYMMDD.Z`` delisting
tags into structured form.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from .datasets import DateRange

# ---------------------------------------------------------------------------
# WealthLab fate codes
# ---------------------------------------------------------------------------

_FATE_LABELS: dict[str, str] = {
    "A": "acquired/merged",
    "C": "chapter 11",
    "P": "taken private",
    "R": "recap",
}


def _date_str_to_ms(value: str) -> int:
    """Parse ``YYYY-MM-DD`` to UTC-midnight epoch ms."""
    dt = datetime.strptime(value.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _parse_ts(value: str) -> int | None:
    """Return epoch ms from a ``YYYY-MM-DD`` string or a bare integer string.

    Returns ``None`` for blank/empty values (open-ended window end).
    """
    v = value.strip()
    if not v:
        return None
    # Bare integer → epoch ms
    if v.lstrip("-").isdigit():
        return int(v)
    # Date string
    return _date_str_to_ms(v)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_membership_csv(text: str) -> dict[str, list[DateRange]]:
    """Parse membership rows ``symbol,start,end[,fate]`` into per-symbol DateRange windows.

    start/end accept ``YYYY-MM-DD`` (UTC midnight) or a bare epoch-ms integer.  An empty/blank
    end = open-ended (None).  Blank lines and a header row (first cell == 'symbol',
    case-insensitive) are skipped.  Multiple rows for one symbol accumulate as multiple windows.
    """
    result: dict[str, list[DateRange]] = {}
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        # Skip empty rows
        if not row or all(cell.strip() == "" for cell in row):
            continue
        first = row[0].strip()
        # Skip header row (case-insensitive match on 'symbol')
        if first.lower() == "symbol":
            continue
        if len(row) < 3:
            continue
        symbol = first.upper()
        start_ts = _parse_ts(row[1])
        end_ts = _parse_ts(row[2]) if len(row) > 2 else None
        if start_ts is None:
            # start is mandatory; skip malformed row
            continue
        dr = DateRange(start_ts=start_ts, end_ts=end_ts)
        result.setdefault(symbol, []).append(dr)
    return result


def parse_delisting_symbol(symbol: str) -> tuple[str, int | None, str]:
    """Parse a WealthLab ``SYM.YYYYMMDD.Z`` delisting tag.

    Returns ``(base_symbol, end_ts_ms, fate_label)`` where:
    - ``end_ts_ms`` is the last-trade date (UTC midnight epoch ms), or ``None``
    - ``fate_label`` is a human label for the fate code, or ``""`` for a plain symbol

    A plain symbol with no such suffix returns ``(symbol, None, "")``.
    """
    parts = symbol.split(".")
    # Expect exactly SYM.YYYYMMDD.Z  (3 parts; middle part is 8 digits)
    if len(parts) == 3 and len(parts[1]) == 8 and parts[1].isdigit() and len(parts[2]) == 1:
        base = parts[0]
        date_str = f"{parts[1][:4]}-{parts[1][4:6]}-{parts[1][6:8]}"
        try:
            end_ts_ms = _date_str_to_ms(date_str)
        except ValueError:
            return (symbol, None, "")
        fate_code = parts[2].upper()
        fate_label = _FATE_LABELS.get(fate_code, fate_code)
        return (base, end_ts_ms, fate_label)
    return (symbol, None, "")

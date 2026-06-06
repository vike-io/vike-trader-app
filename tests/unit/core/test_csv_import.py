"""CSV import: parse third-party OHLCV exports → UTC ``Bar``s, with optional TZ-shift + aggregate.

Covers the formats real exports use — ISO datetimes, MT4 ``Date,Time`` split columns, Dukascopy
``dd.mm.yyyy``, epoch seconds/ms, and ``;`` / tab delimiters — plus the source→UTC offset and
resample-on-import. All bars land in UTC (the project's single storage timezone).
"""

from vike_trader_app.core.model import Bar
from vike_trader_app.data import csv_import as ci

_MIN = 60_000


def _ts(y, mo, d, h=0, mi=0, s=0):
    from datetime import datetime, timezone
    return int(datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp() * 1000)


# --- datetime parsing --------------------------------------------------------------------

def test_parse_iso_datetime_header_comma():
    text = "time,open,high,low,close,volume\n2024-01-02 15:04:00,1.10,1.20,1.00,1.15,100\n"
    bars = ci.parse_csv(text)
    assert bars == [Bar(_ts(2024, 1, 2, 15, 4), 1.10, 1.20, 1.00, 1.15, 100.0)]


def test_parse_mt4_date_time_split_columns():
    text = "Date,Time,Open,High,Low,Close,Volume\n2024.01.02,15:04,1.10,1.20,1.00,1.15,100\n"
    bars = ci.parse_csv(text)
    assert bars[0].ts == _ts(2024, 1, 2, 15, 4)
    assert bars[0].close == 1.15


def test_parse_dukascopy_ddmmyyyy_semicolon():
    text = "Gmt time;Open;High;Low;Close;Volume\n02.01.2024 15:04:00;1.1;1.2;1.0;1.15;100\n"
    bars = ci.parse_csv(text)
    assert bars[0].ts == _ts(2024, 1, 2, 15, 4)


def test_parse_epoch_ms_and_seconds():
    ms = "timestamp,open,high,low,close,volume\n1704207840000,1,2,0.5,1.5,9\n"
    assert ci.parse_csv(ms)[0].ts == 1704207840000
    secs = "timestamp,open,high,low,close,volume\n1704207840,1,2,0.5,1.5,9\n"
    assert ci.parse_csv(secs)[0].ts == 1704207840000  # seconds promoted to ms


def test_parse_headerless_positional_six_columns():
    text = "2024-01-02 15:04:00,1.10,1.20,1.00,1.15,100\n2024-01-02 15:05:00,1.15,1.25,1.10,1.20,80\n"
    bars = ci.parse_csv(text)
    assert len(bars) == 2 and bars[1].open == 1.15


def test_missing_volume_defaults_zero():
    text = "time,open,high,low,close\n2024-01-02 15:04:00,1.1,1.2,1.0,1.15\n"
    assert ci.parse_csv(text)[0].volume == 0.0


# --- robustness --------------------------------------------------------------------------

def test_rows_are_sorted_and_deduped_by_ts():
    text = ("time,open,high,low,close,volume\n"
            "2024-01-02 15:05:00,2,2,2,2,1\n"
            "2024-01-02 15:04:00,1,1,1,1,1\n"
            "2024-01-02 15:05:00,9,9,9,9,9\n")  # dup ts -> last wins
    bars = ci.parse_csv(text)
    assert [b.ts for b in bars] == [_ts(2024, 1, 2, 15, 4), _ts(2024, 1, 2, 15, 5)]
    assert bars[1].open == 9


def test_malformed_rows_are_skipped():
    text = ("time,open,high,low,close,volume\n"
            "2024-01-02 15:04:00,1,2,0.5,1.5,9\n"
            "garbage,row,that,is,not,valid\n"
            "2024-01-02 15:05:00,1,2,0.5,1.5,9\n")
    assert len(ci.parse_csv(text)) == 2


def test_blank_input_returns_empty():
    assert ci.parse_csv("") == []
    assert ci.parse_csv("   \n  \n") == []


# --- timezone shift (source TZ -> UTC) ---------------------------------------------------

def test_tz_offset_shifts_source_local_to_utc():
    # source stamps are UTC+2; subtract 120 min to get UTC
    text = "time,open,high,low,close,volume\n2024-01-02 15:04:00,1,2,0.5,1.5,9\n"
    bars = ci.parse_csv(text, tz_offset_minutes=120)
    assert bars[0].ts == _ts(2024, 1, 2, 13, 4)


# --- aggregate on import -----------------------------------------------------------------

def test_aggregate_resamples_to_higher_timeframe():
    rows = ["time,open,high,low,close,volume"]
    for i in range(5):  # five 1m bars -> one 5m bar
        rows.append(f"2024-01-02 15:0{i}:00,{i},{i + 1},{i - 1},{i},1")
    bars = ci.parse_csv("\n".join(rows))
    agg = ci.aggregate(bars, "5m")
    assert len(agg) == 1
    assert agg[0].open == 0 and agg[0].high == 5 and agg[0].close == 4 and agg[0].volume == 5


# --- interval inference ------------------------------------------------------------------

def test_infer_interval_ms_uses_modal_gap():
    bars = [Bar(0, 1, 1, 1, 1), Bar(_MIN, 1, 1, 1, 1), Bar(2 * _MIN, 1, 1, 1, 1)]
    assert ci.infer_interval_ms(bars) == _MIN


def test_ms_to_interval_labels():
    assert ci.ms_to_interval(_MIN) == "1m"
    assert ci.ms_to_interval(5 * _MIN) == "5m"
    assert ci.ms_to_interval(3_600_000) == "1h"
    assert ci.ms_to_interval(86_400_000) == "1d"

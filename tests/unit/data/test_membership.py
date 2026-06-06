"""Tests for membership CSV/delisting parser (data/membership.py)."""

from datetime import datetime, timezone

import pytest

from vike_trader_app.data.datasets import DateRange
from vike_trader_app.data.membership import parse_delisting_symbol, parse_membership_csv


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_ms(year: int, month: int, day: int) -> int:
    """Compute UTC-midnight epoch ms for a given date."""
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp() * 1000)


# ---------------------------------------------------------------------------
# parse_membership_csv
# ---------------------------------------------------------------------------


def test_basic_two_symbols():
    text = "AAA,2020-01-01,2020-12-31\nBBB,2021-06-01,\n"
    result = parse_membership_csv(text)
    assert set(result.keys()) == {"AAA", "BBB"}
    assert result["AAA"] == [DateRange(_utc_ms(2020, 1, 1), _utc_ms(2020, 12, 31))]
    assert result["BBB"] == [DateRange(_utc_ms(2021, 6, 1), None)]


def test_open_ended_window_end_is_none():
    text = "XYZ,2022-03-15,\n"
    result = parse_membership_csv(text)
    assert result["XYZ"][0].end_ts is None


def test_header_row_skipped_case_insensitive():
    text = "symbol,start,end\nAAA,2020-01-01,2020-12-31\n"
    result = parse_membership_csv(text)
    assert "SYMBOL" not in result
    assert "AAA" in result


def test_header_row_mixed_case_skipped():
    text = "Symbol,Start,End\nBBB,2021-01-01,\n"
    result = parse_membership_csv(text)
    assert "SYMBOL" not in result
    assert "BBB" in result


def test_blank_lines_skipped():
    text = "\n\nAAA,2020-01-01,2020-12-31\n\n"
    result = parse_membership_csv(text)
    assert list(result.keys()) == ["AAA"]


def test_epoch_ms_integers_accepted():
    start_ms = _utc_ms(2020, 1, 1)
    end_ms = _utc_ms(2020, 12, 31)
    text = f"AAA,{start_ms},{end_ms}\n"
    result = parse_membership_csv(text)
    assert result["AAA"] == [DateRange(start_ms, end_ms)]


def test_epoch_ms_open_ended():
    start_ms = _utc_ms(2021, 6, 1)
    text = f"BBB,{start_ms},\n"
    result = parse_membership_csv(text)
    assert result["BBB"][0].end_ts is None


def test_two_rows_same_symbol_accumulate_windows():
    text = (
        "AAA,2020-01-01,2020-06-30\n"
        "AAA,2021-01-01,2021-06-30\n"
    )
    result = parse_membership_csv(text)
    assert len(result["AAA"]) == 2
    assert result["AAA"][0] == DateRange(_utc_ms(2020, 1, 1), _utc_ms(2020, 6, 30))
    assert result["AAA"][1] == DateRange(_utc_ms(2021, 1, 1), _utc_ms(2021, 6, 30))


def test_symbol_uppercased():
    text = "aapl,2020-01-01,2020-12-31\n"
    result = parse_membership_csv(text)
    assert "AAPL" in result


def test_empty_text_returns_empty():
    assert parse_membership_csv("") == {}
    assert parse_membership_csv("\n\n") == {}


def test_fate_column_is_ignored_in_csv():
    """A 4-column row (symbol,start,end,fate) must still parse correctly."""
    text = "AAA,2020-01-01,2020-12-31,acquired\n"
    result = parse_membership_csv(text)
    assert "AAA" in result
    assert result["AAA"][0].end_ts == _utc_ms(2020, 12, 31)


def test_utc_date_conversion_correct():
    """Spot-check that 2020-01-01 maps to exactly 1577836800000 ms."""
    expected = 1577836800000  # 2020-01-01 00:00:00 UTC
    text = "AAA,2020-01-01,\n"
    result = parse_membership_csv(text)
    assert result["AAA"][0].start_ts == expected


# ---------------------------------------------------------------------------
# parse_delisting_symbol
# ---------------------------------------------------------------------------


def test_chapter_11_lehman():
    base, end_ts, fate = parse_delisting_symbol("LEH.20080915.C")
    assert base == "LEH"
    assert end_ts == _utc_ms(2008, 9, 15)
    assert fate == "chapter 11"


def test_acquired_fate():
    base, end_ts, fate = parse_delisting_symbol("TWX.20180614.A")
    assert base == "TWX"
    assert end_ts == _utc_ms(2018, 6, 14)
    assert fate == "acquired/merged"


def test_taken_private_fate():
    base, end_ts, fate = parse_delisting_symbol("DELL.20131029.P")
    assert base == "DELL"
    assert fate == "taken private"


def test_recap_fate():
    base, end_ts, fate = parse_delisting_symbol("XYZ.20200101.R")
    assert fate == "recap"


def test_unknown_fate_code_returns_raw_letter():
    """An unrecognised fate code is returned as-is (no crash)."""
    base, end_ts, fate = parse_delisting_symbol("XYZ.20200101.X")
    assert fate == "X"


def test_plain_symbol_no_suffix():
    base, end_ts, fate = parse_delisting_symbol("AAPL")
    assert base == "AAPL"
    assert end_ts is None
    assert fate == ""


def test_plain_symbol_with_dots_not_wl_format():
    """BRK.B style (not a delisting tag) should pass through unchanged."""
    base, end_ts, fate = parse_delisting_symbol("BRK.B")
    assert base == "BRK.B"
    assert end_ts is None
    assert fate == ""


def test_delisting_utc_midnight_ms():
    """2008-09-15 UTC midnight = 1221436800000 ms."""
    _, end_ts, _ = parse_delisting_symbol("LEH.20080915.C")
    assert end_ts == 1221436800000

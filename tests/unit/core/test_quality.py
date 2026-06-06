"""Bar-series quality checks: interior gap detection and OHLCV validation."""

from vike_trader_app.core.model import Bar
from vike_trader_app.data import quality

STEP = 60_000


def _bar(ts, o=100.0, h=101.0, low=99.0, c=100.0, v=1.0):
    return Bar(ts=ts, open=o, high=h, low=low, close=c, volume=v)


# --- find_gaps ---


def test_find_gaps_contiguous_is_empty():
    bars = [_bar(i * STEP) for i in range(5)]
    assert quality.find_gaps(bars, STEP) == []


def test_find_gaps_too_few_bars_is_empty():
    assert quality.find_gaps([], STEP) == []
    assert quality.find_gaps([_bar(0)], STEP) == []


def test_find_gaps_interior_hole_returns_exact_missing_range():
    # bars at 0, STEP, then jump to 5*STEP -> interior hole covering 2..4 * STEP.
    bars = [_bar(0), _bar(STEP), _bar(5 * STEP), _bar(6 * STEP)]
    gaps = quality.find_gaps(bars, STEP)
    assert gaps == [(2 * STEP, 4 * STEP)]


def test_find_gaps_multiple_holes():
    bars = [_bar(0), _bar(2 * STEP), _bar(3 * STEP), _bar(6 * STEP)]
    gaps = quality.find_gaps(bars, STEP)
    assert gaps == [(STEP, STEP), (4 * STEP, 5 * STEP)]


# --- validate_bars ---


def test_validate_clean_series_is_empty():
    bars = [_bar(i * STEP) for i in range(5)]
    assert quality.validate_bars(bars, STEP) == []


def test_validate_flags_high_below_low():
    bars = [_bar(0), _bar(STEP, h=98.0, low=99.0)]  # high < low
    problems = quality.validate_bars(bars, STEP)
    assert any("high" in p.lower() for p in problems)


def test_validate_flags_high_below_open_close():
    bars = [_bar(0), _bar(STEP, o=105.0, c=104.0, h=103.0, low=99.0)]  # high < max(open, close)
    problems = quality.validate_bars(bars, STEP)
    assert problems  # at least one problem flagged


def test_validate_flags_low_above_open_close():
    bars = [_bar(0), _bar(STEP, o=100.0, c=101.0, h=102.0, low=100.5)]  # low > min(open, close)
    problems = quality.validate_bars(bars, STEP)
    assert problems


def test_validate_flags_negative_price_and_volume():
    bars = [_bar(0), _bar(STEP, o=-1.0, h=101.0, low=-2.0, c=100.0, v=-5.0)]
    problems = quality.validate_bars(bars, STEP)
    assert any("negative" in p.lower() for p in problems)


def test_validate_flags_duplicate_timestamp():
    bars = [_bar(0), _bar(0)]
    problems = quality.validate_bars(bars, STEP)
    assert any("increasing" in p.lower() or "duplicat" in p.lower() for p in problems)


def test_validate_flags_out_of_order_timestamp():
    bars = [_bar(2 * STEP), _bar(STEP)]
    problems = quality.validate_bars(bars, STEP)
    assert any("increasing" in p.lower() or "order" in p.lower() for p in problems)


def test_validate_flags_interior_gap():
    bars = [_bar(0), _bar(STEP), _bar(5 * STEP)]  # gap between STEP and 5*STEP
    problems = quality.validate_bars(bars, STEP)
    assert any("gap" in p.lower() or "spacing" in p.lower() for p in problems)


# --- repair_bars ---


def test_repair_clean_series_unchanged_empty_log():
    bars = [_bar(i * STEP) for i in range(5)]
    repaired, audit = quality.repair_bars(bars, STEP)
    assert repaired == bars
    assert audit == []


def test_repair_zero_close_replaced_with_prior_close():
    prior = _bar(0, c=101.5)
    bad = _bar(STEP, o=101.5, h=102.0, low=100.0, c=0.0)
    repaired, audit = quality.repair_bars([prior, bad], STEP)
    assert repaired[1].close == 101.5
    assert any("zero close" in a and "101.5" in a for a in audit)


def test_repair_nan_open_replaced_with_prior_close():
    import math
    prior = _bar(0, c=55.0)
    bad = Bar(ts=STEP, open=float("nan"), high=60.0, low=54.0, close=56.0, volume=1.0)
    repaired, audit = quality.repair_bars([prior, bad], STEP)
    assert not math.isnan(repaired[1].open)
    assert repaired[1].open == 55.0
    assert any("NaN open" in a for a in audit)


def test_repair_high_below_max_ohlc_is_clamped_up():
    # open=105, close=104, high=103 (too low) — should be clamped to 105
    bad = _bar(0, o=105.0, h=103.0, low=99.0, c=104.0)
    repaired, audit = quality.repair_bars([bad], STEP)
    assert repaired[0].high == 105.0
    assert any("high" in a and "103" in a for a in audit)


def test_repair_low_above_min_ohlc_is_clamped_down():
    # open=100, close=101, low=100.5 (too high) — should be clamped to 100
    bad = _bar(0, o=100.0, h=102.0, low=100.5, c=101.0)
    repaired, audit = quality.repair_bars([bad], STEP)
    assert repaired[0].low == 100.0
    assert any("low" in a and "100.5" in a for a in audit)


def test_repair_duplicate_ts_keeps_last():
    first = _bar(0, c=10.0)
    second = _bar(0, c=20.0)  # same ts, should win
    repaired, audit = quality.repair_bars([first, second], STEP)
    assert len(repaired) == 1
    assert repaired[0].close == 20.0
    assert any("duplicate" in a and "ts=0" in a for a in audit)


def test_repair_out_of_order_ts_dropped():
    b1 = _bar(2 * STEP)
    b2 = _bar(STEP)  # earlier — out of order
    repaired, audit = quality.repair_bars([b1, b2], STEP)
    assert len(repaired) == 1
    assert repaired[0].ts == 2 * STEP
    assert any("out-of-order" in a for a in audit)


def test_repair_negative_volume_clamped_to_zero():
    bad = Bar(ts=0, open=100.0, high=101.0, low=99.0, close=100.0, volume=-5.0)
    repaired, audit = quality.repair_bars([bad], STEP)
    assert repaired[0].volume == 0.0
    assert any("negative volume" in a for a in audit)


def test_repair_first_bar_zero_price_uses_median_of_others():
    # open=0 on the very first bar (no prev_close); h/l/c are valid
    bad = Bar(ts=0, open=0.0, high=102.0, low=98.0, close=100.0, volume=1.0)
    repaired, audit = quality.repair_bars([bad], STEP)
    # median of [102, 98, 100] = 100
    assert repaired[0].open == 100.0
    assert any("zero open" in a for a in audit)


def test_repair_returns_new_bar_objects_not_mutated():
    """repair_bars must not return the original Bar objects for changed bars."""
    import math
    prior = _bar(0, c=50.0)
    bad = Bar(ts=STEP, open=float("nan"), high=55.0, low=49.0, close=51.0, volume=1.0)
    original_bars = [prior, bad]
    repaired, audit = quality.repair_bars(original_bars, STEP)
    # original bad bar is unchanged (frozen dataclass, but make sure we got a different value)
    assert math.isnan(original_bars[1].open)   # original untouched
    assert not math.isnan(repaired[1].open)    # repaired is a new object

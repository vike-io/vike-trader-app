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

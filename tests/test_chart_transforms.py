"""Pure chart-style transforms: Heikin-Ashi, Renko, Range, Line-break, Kagi, Point & Figure."""

from vike_trader_app.core.chart_transforms import (
    auto_box,
    heikin_ashi,
    kagi,
    line_break,
    point_and_figure,
    range_bars,
    renko,
)
from vike_trader_app.core.model import Bar


def _bars(closes):
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        out.append(Bar(ts=i * 60_000, open=prev, high=max(prev, c) + 0.0,
                       low=min(prev, c), close=c, volume=10.0))
        prev = c
    return out


_RISE = _bars([100.0 + i for i in range(11)])  # 100 -> 110, monotonic


# --- auto_box ---

def test_auto_box_positive():
    assert auto_box(_RISE) > 0
    assert auto_box([]) == 0.0


# --- Heikin-Ashi (same length, 1:1) ---

def test_heikin_ashi_same_length_and_smoothing():
    ha = heikin_ashi(_RISE)
    assert len(ha) == len(_RISE)                       # 1:1 — keeps time/index alignment
    b0 = _RISE[0]
    assert ha[0].open == (b0.open + b0.close) / 2.0    # seed
    assert ha[0].close == (b0.open + b0.high + b0.low + b0.close) / 4.0
    for i, h in enumerate(ha):
        assert h.high >= max(h.open, h.close)          # body contained
        assert h.low <= min(h.open, h.close)
        assert h.ts == _RISE[i].ts                     # timestamps preserved


def test_heikin_ashi_deterministic():
    assert heikin_ashi(_RISE) == heikin_ashi(_RISE)


# --- Renko ---

def test_renko_rising_series_all_up_bricks():
    bricks = renko(_RISE, box_size=1.0)
    assert len(bricks) == 10                            # 100->110 in 1.0 boxes
    assert all(b.close > b.open for b in bricks)        # all up
    assert bricks[0].open == 100.0 and bricks[0].close == 101.0
    assert bricks[-1].close == 110.0
    for b in bricks:                                    # bricks have no wick
        assert b.high == max(b.open, b.close)
        assert b.low == min(b.open, b.close)


def test_renko_reverses_on_down_move():
    bricks = renko(_bars([100, 101, 102, 103, 100, 99]), box_size=1.0)
    assert any(b.close < b.open for b in bricks)        # produced at least one down brick


def test_renko_empty_and_deterministic():
    assert renko([], box_size=1.0) == []
    assert renko(_RISE, box_size=1.0) == renko(_RISE, box_size=1.0)


# --- Range bars ---

def test_range_bars_rising_series():
    rb = range_bars(_RISE, range_size=1.0)
    assert len(rb) >= 8
    assert all(b.close >= b.open for b in rb)           # rising -> up bars


def test_range_bars_empty():
    assert range_bars([], range_size=1.0) == []


# --- Line break ---

def test_line_break_monotonic_all_up():
    blocks = line_break(_RISE, n=3)
    assert len(blocks) >= 3
    assert all(b.close > b.open for b in blocks)        # every line is an up line


def test_line_break_needs_two_bars():
    assert line_break([_RISE[0]], n=3) == []


# --- Kagi ---

def test_kagi_monotonic_single_segment():
    res = kagi(_RISE, reversal=1.0)
    assert res.prices[0] == 100.0
    assert res.prices[-1] == 110.0
    assert len(res.prices) == 2                          # no reversal -> one rising segment
    assert len(res.thick) == len(res.prices) - 1


def test_kagi_reversal_adds_vertices():
    res = kagi(_bars([100, 105, 104, 103, 102, 101, 100, 99]), reversal=2.0)
    assert len(res.prices) >= 3                          # at least one reversal vertex
    assert len(res.bars) == len(res.prices)


# --- Point & Figure ---

def test_pnf_rising_single_up_column():
    res = point_and_figure(_RISE, box_size=1.0, reversal=3)
    assert len(res.columns) == 1
    assert res.columns[0].up
    assert res.columns[0].top >= 109.0
    assert len(res.bars) == len(res.columns)
    assert res.box == 1.0


def test_pnf_reversal_adds_column():
    res = point_and_figure(_bars([100, 105, 110, 109, 108, 107, 106, 105]), box_size=1.0, reversal=3)
    assert len(res.columns) >= 2
    assert res.columns[0].up and not res.columns[-1].up  # X column then O column


def test_pnf_empty():
    res = point_and_figure([], box_size=1.0)
    assert res.columns == [] and res.bars == []


# --- robustness: malformed / degenerate data must not hang or crash --------------------------

def _bad_single():
    # a single malformed bar with high < low (auto_box would have been negative -> infinite loop)
    return [Bar(ts=0, open=100.0, high=99.0, low=101.0, close=100.0)]


def test_malformed_single_bar_does_not_hang():
    # The regression: a high<low single bar made auto_box NEGATIVE -> renko/range looped forever.
    # abs() in auto_box fixes it; these now all return promptly and bounded.
    bad = _bad_single()
    assert renko(bad) == []
    assert range_bars(bad) == []
    assert len(kagi(bad).prices) <= 1
    assert len(point_and_figure(bad).columns) <= 1


def test_negative_explicit_box_falls_back_to_auto_no_hang():
    from vike_trader_app.core.chart_transforms import _MAX_UNITS
    bricks = renko(_RISE, box_size=-1.0)            # invalid explicit -> positive auto box, bounded
    assert 0 < len(bricks) <= _MAX_UNITS
    assert renko([], box_size=-1.0) == []
    assert range_bars(_RISE, range_size=0.0) != []  # 0 also falls back to auto (positive)


def test_nan_close_does_not_crash_pnf():
    nan = float("nan")
    bars = [Bar(ts=i * 60_000, open=100, high=101, low=99, close=(nan if i == 0 else 100 + i))
            for i in range(8)]
    res = point_and_figure(bars, box_size=1.0)   # math.floor(nan) would raise without the finite filter
    assert isinstance(res.columns, list)


def test_runaway_box_is_capped():
    from vike_trader_app.core.chart_transforms import _MAX_UNITS
    # a tiny box vs a large move would build millions of bricks without the cap
    bricks = renko(_bars([100.0, 100_000.0]), box_size=0.001)
    assert len(bricks) <= _MAX_UNITS

"""Tests for the 16 two-bar candlestick patterns (Task 3).

Each test constructs a synthetic bar sequence whose last two bars match the
target pattern's geometry, and asserts the signal fires (+100 or -100).
A no-pattern series is tested for each to confirm it returns 0.
"""

from vike_trader_app.core.indicators.patterns import (
    harami,
    harami_cross,
    piercing,
    dark_cloud_cover,
    counterattack,
    meeting_lines,
    separating_lines,
    matching_low,
    on_neck,
    in_neck,
    thrusting,
    kicking,
    kicking_by_length,
    homing_pigeon,
    gap_side_side_white,
    tasuki_gap,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _series(bars):
    """bars = list of (o,h,l,c); return parallel o/h/l/c lists."""
    o = [b[0] for b in bars]
    h = [b[1] for b in bars]
    l = [b[2] for b in bars]
    c = [b[3] for b in bars]
    return o, h, l, c


# 11 neutral context bars with moderate bodies to prime avg_body
_CTX = [(10.0, 11.0, 9.0, 10.5)] * 11  # body=0.5 each


def _flat_series(n=20):
    """All-flat bars: open=close=high=low=10."""
    return _series([(10.0, 10.0, 10.0, 10.0)] * n)


# ---------------------------------------------------------------------------
# harami — current body inside prev body, opposite colour
#          prev black/curr white → +100; prev white/curr black → -100
# ---------------------------------------------------------------------------

def test_harami_bullish_fires():
    # prev: black, open=12, close=9 (body 3)
    # curr: white, open=9.5, close=11.0 — inside prev body
    prev = (12.0, 12.2, 8.8, 9.0)
    curr = (9.5, 11.2, 9.4, 11.0)
    o, h, l, c = _series(_CTX + [prev, curr])
    out = harami(o, h, l, c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


def test_harami_bearish_fires():
    # prev: white, open=9, close=12 (body 3)
    # curr: black, open=11.5, close=10.0 — inside prev body
    prev = (9.0, 12.2, 8.8, 12.0)
    curr = (11.5, 11.6, 9.9, 10.0)
    o, h, l, c = _series(_CTX + [prev, curr])
    out = harami(o, h, l, c)
    assert out[-1] == -100, f"expected -100, got {out[-1]}"


def test_harami_no_signal_flat():
    o, h, l, c = _flat_series()
    out = harami(o, h, l, c)
    assert all(v == 0 for v in out)


# ---------------------------------------------------------------------------
# harami_cross — harami where the current bar is a doji → ±100 by prior colour
# ---------------------------------------------------------------------------

def test_harami_cross_bullish_fires():
    # prev: big black (open=12, close=9)
    # curr: doji inside prev body (open=close=10.5, tiny body)
    ctx = [(10.0, 11.0, 9.0, 10.5)] * 11  # body=0.5 avg → doji needs body≤0.05
    prev = (12.0, 12.2, 8.8, 9.0)   # black, body=3
    curr = (10.5, 10.8, 10.3, 10.5)  # body=0 (doji), inside prev body
    o, h, l, c = _series(ctx + [prev, curr])
    out = harami_cross(o, h, l, c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


def test_harami_cross_bearish_fires():
    ctx = [(10.0, 11.0, 9.0, 10.5)] * 11
    prev = (9.0, 12.2, 8.8, 12.0)   # white, body=3
    curr = (10.5, 10.8, 10.3, 10.5)  # doji inside prev body
    o, h, l, c = _series(ctx + [prev, curr])
    out = harami_cross(o, h, l, c)
    assert out[-1] == -100, f"expected -100, got {out[-1]}"


def test_harami_cross_no_signal_flat():
    o, h, l, c = _flat_series()
    out = harami_cross(o, h, l, c)
    assert all(v == 0 for v in out)


# ---------------------------------------------------------------------------
# piercing — prev black, curr white opens below prev low, closes above prev midpoint
#            (but below prev open) → +100
# ---------------------------------------------------------------------------

def test_piercing_fires():
    # prev: black, open=12, close=9; midpoint=10.5
    # curr: white, opens at 8.5 (below prev low 8.8), closes at 11.0 (above midpoint 10.5, below prev open 12)
    prev = (12.0, 12.1, 8.8, 9.0)
    curr = (8.5, 11.1, 8.4, 11.0)
    o, h, l, c = _series(_CTX + [prev, curr])
    out = piercing(o, h, l, c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


def test_piercing_no_signal_flat():
    o, h, l, c = _flat_series()
    out = piercing(o, h, l, c)
    assert all(v == 0 for v in out)


# ---------------------------------------------------------------------------
# dark_cloud_cover — prev white, curr black opens above prev high, closes below
#                   prev midpoint (above prev open) → -100
# ---------------------------------------------------------------------------

def test_dark_cloud_cover_fires():
    # prev: white, open=9, close=12; midpoint=10.5
    # curr: black, opens at 12.5 (above prev high 12.1), closes at 10.0 (below midpoint 10.5, above prev open 9)
    prev = (9.0, 12.1, 8.9, 12.0)
    curr = (12.5, 12.6, 9.9, 10.0)
    o, h, l, c = _series(_CTX + [prev, curr])
    out = dark_cloud_cover(o, h, l, c)
    assert out[-1] == -100, f"expected -100, got {out[-1]}"


def test_dark_cloud_cover_no_signal_flat():
    o, h, l, c = _flat_series()
    out = dark_cloud_cover(o, h, l, c)
    assert all(v == 0 for v in out)


# ---------------------------------------------------------------------------
# counterattack — opposite-colour bodies that close at ≈ same price → ±100 by curr colour
# ---------------------------------------------------------------------------

def test_counterattack_bullish_fires():
    # prev: black, close=9.0; curr: white, closes ≈ 9.0
    # Context body avg ≈ 0.5, tolerance = 3% of avg = 0.015; use close within 0.01
    ctx = [(10.0, 11.0, 9.0, 10.5)] * 11   # body=0.5 → avg≈0.5
    prev = (12.0, 12.2, 8.8, 9.0)
    curr = (7.0, 9.06, 6.9, 9.01)  # white, close=9.01 ≈ prev close 9.0 (diff=0.01 ≤ 0.015)
    o, h, l, c = _series(ctx + [prev, curr])
    out = counterattack(o, h, l, c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


def test_counterattack_bearish_fires():
    # prev: white, close=12.0; curr: black, closes ≈ 12.0
    ctx = [(10.0, 11.0, 9.0, 10.5)] * 11
    prev = (9.0, 12.2, 8.8, 12.0)
    curr = (14.0, 14.1, 11.85, 12.01)  # black, close=12.01 ≈ prev close 12.0 (diff=0.01)
    o, h, l, c = _series(ctx + [prev, curr])
    out = counterattack(o, h, l, c)
    assert out[-1] == -100, f"expected -100, got {out[-1]}"


def test_counterattack_no_signal_flat():
    o, h, l, c = _flat_series()
    out = counterattack(o, h, l, c)
    assert all(v == 0 for v in out)


# ---------------------------------------------------------------------------
# meeting_lines — like counterattack (closes meet) → ±100
# ---------------------------------------------------------------------------

def test_meeting_lines_bullish_fires():
    # prev: black, close=9.0; curr: white, closes exactly at 9.0
    prev = (12.0, 12.2, 8.8, 9.0)
    curr = (7.0, 9.05, 6.9, 9.0)
    o, h, l, c = _series(_CTX + [prev, curr])
    out = meeting_lines(o, h, l, c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


def test_meeting_lines_bearish_fires():
    prev = (9.0, 12.2, 8.8, 12.0)
    curr = (14.0, 14.1, 11.85, 12.0)
    o, h, l, c = _series(_CTX + [prev, curr])
    out = meeting_lines(o, h, l, c)
    assert out[-1] == -100, f"expected -100, got {out[-1]}"


def test_meeting_lines_no_signal_flat():
    o, h, l, c = _flat_series()
    out = meeting_lines(o, h, l, c)
    assert all(v == 0 for v in out)


# ---------------------------------------------------------------------------
# separating_lines — same colour, curr opens at prev open → ±100 by colour
# ---------------------------------------------------------------------------

def test_separating_lines_white_fires():
    # both white, curr opens at same as prev open
    prev = (9.0, 11.2, 8.8, 11.0)   # white, open=9.0
    curr = (9.0, 12.5, 8.9, 12.0)   # white, open=9.0 (same)
    o, h, l, c = _series(_CTX + [prev, curr])
    out = separating_lines(o, h, l, c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


def test_separating_lines_black_fires():
    prev = (12.0, 12.2, 8.8, 9.0)   # black, open=12.0
    curr = (12.0, 12.1, 7.5, 8.0)   # black, open=12.0 (same)
    o, h, l, c = _series(_CTX + [prev, curr])
    out = separating_lines(o, h, l, c)
    assert out[-1] == -100, f"expected -100, got {out[-1]}"


def test_separating_lines_no_signal_flat():
    o, h, l, c = _flat_series()
    out = separating_lines(o, h, l, c)
    assert all(v == 0 for v in out)


# ---------------------------------------------------------------------------
# matching_low — two black candles with equal closes → +100 (bullish reversal)
# ---------------------------------------------------------------------------

def test_matching_low_fires():
    prev = (12.0, 12.2, 8.8, 9.0)   # black, close=9.0
    curr = (11.0, 11.1, 8.9, 9.0)   # black, close=9.0 (same)
    o, h, l, c = _series(_CTX + [prev, curr])
    out = matching_low(o, h, l, c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


def test_matching_low_no_signal_flat():
    o, h, l, c = _flat_series()
    out = matching_low(o, h, l, c)
    assert all(v == 0 for v in out)


# ---------------------------------------------------------------------------
# on_neck — prev black (downtrend), curr white closes ≈ prev low → -100 (continuation)
# ---------------------------------------------------------------------------

def test_on_neck_fires():
    # prev: black, open=12, close=9, low=8.8
    # curr: white, opens below prev low (8.5), closes AT prev low (≈8.8)
    prev = (12.0, 12.1, 8.8, 9.0)
    curr = (8.5, 8.85, 8.4, 8.8)   # close≈prev low=8.8
    o, h, l, c = _series(_CTX + [prev, curr])
    out = on_neck(o, h, l, c)
    assert out[-1] == -100, f"expected -100, got {out[-1]}"


def test_on_neck_no_signal_flat():
    o, h, l, c = _flat_series()
    out = on_neck(o, h, l, c)
    assert all(v == 0 for v in out)


# ---------------------------------------------------------------------------
# in_neck — prev black, curr white closes slightly into prev body (≈ prev close) → -100
# ---------------------------------------------------------------------------

def test_in_neck_fires():
    # prev: black, open=12, close=9, range 9-12
    # curr: white, opens below prev low, closes ≈ prev close+tiny (9.1, just inside prev body)
    prev = (12.0, 12.1, 8.8, 9.0)
    curr = (8.5, 9.15, 8.4, 9.1)   # close just above prev close (9.0), small intrusion
    o, h, l, c = _series(_CTX + [prev, curr])
    out = in_neck(o, h, l, c)
    assert out[-1] == -100, f"expected -100, got {out[-1]}"


def test_in_neck_no_signal_flat():
    o, h, l, c = _flat_series()
    out = in_neck(o, h, l, c)
    assert all(v == 0 for v in out)


# ---------------------------------------------------------------------------
# thrusting — prev black, curr white closes into prev body but below midpoint → -100
# ---------------------------------------------------------------------------

def test_thrusting_fires():
    # prev: black, open=12, close=9, midpoint=10.5
    # curr: white, opens below prev low, closes between prev close and midpoint (e.g. 9.8)
    prev = (12.0, 12.1, 8.8, 9.0)
    curr = (8.5, 9.85, 8.4, 9.8)   # close=9.8; prev close=9.0, midpoint=10.5 → 9<9.8<10.5
    o, h, l, c = _series(_CTX + [prev, curr])
    out = thrusting(o, h, l, c)
    assert out[-1] == -100, f"expected -100, got {out[-1]}"


def test_thrusting_no_signal_flat():
    o, h, l, c = _flat_series()
    out = thrusting(o, h, l, c)
    assert all(v == 0 for v in out)


# ---------------------------------------------------------------------------
# kicking — two marubozu of opposite colour with a gap between them
#           white after black with gap up → +100; black after white with gap down → -100
# ---------------------------------------------------------------------------

def test_kicking_bullish_fires():
    # prev: black marubozu, open=12, close=9 (no shadows)
    # curr: white marubozu, open=10 (gap up from prev close 9), close=13 (no shadows)
    prev = (12.0, 12.0, 9.0, 9.0)   # black marubozu
    curr = (10.0, 13.0, 10.0, 13.0)  # white marubozu, gap up (curr open > prev close)
    o, h, l, c = _series(_CTX + [prev, curr])
    out = kicking(o, h, l, c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


def test_kicking_bearish_fires():
    # prev: white marubozu, open=9, close=12
    # curr: black marubozu, open=11 (gap down from prev close 12), close=8
    prev = (9.0, 12.0, 9.0, 12.0)   # white marubozu
    curr = (11.0, 11.0, 8.0, 8.0)   # black marubozu, gap down (curr open < prev close)
    o, h, l, c = _series(_CTX + [prev, curr])
    out = kicking(o, h, l, c)
    assert out[-1] == -100, f"expected -100, got {out[-1]}"


def test_kicking_no_signal_flat():
    o, h, l, c = _flat_series()
    out = kicking(o, h, l, c)
    assert all(v == 0 for v in out)


# ---------------------------------------------------------------------------
# kicking_by_length — kicking, signal by the longer marubozu's colour
# ---------------------------------------------------------------------------

def test_kicking_by_length_bullish_fires():
    # same setup as kicking bullish — longer white marubozu wins (+100)
    prev = (12.0, 12.0, 9.0, 9.0)    # black marubozu, body=3
    curr = (10.0, 14.0, 10.0, 14.0)   # white marubozu, body=4 (longer)
    o, h, l, c = _series(_CTX + [prev, curr])
    out = kicking_by_length(o, h, l, c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


def test_kicking_by_length_bearish_fires():
    prev = (9.0, 12.0, 9.0, 12.0)    # white marubozu, body=3
    curr = (11.0, 11.0, 7.0, 7.0)    # black marubozu, body=4 (longer)
    o, h, l, c = _series(_CTX + [prev, curr])
    out = kicking_by_length(o, h, l, c)
    assert out[-1] == -100, f"expected -100, got {out[-1]}"


def test_kicking_by_length_no_signal_flat():
    o, h, l, c = _flat_series()
    out = kicking_by_length(o, h, l, c)
    assert all(v == 0 for v in out)


# ---------------------------------------------------------------------------
# homing_pigeon — two black candles, second harami-inside the first → +100
# ---------------------------------------------------------------------------

def test_homing_pigeon_fires():
    # prev: black, open=12, close=9 (body 3)
    # curr: black, open=11.5, close=9.5 — inside prev body
    prev = (12.0, 12.2, 8.8, 9.0)
    curr = (11.5, 11.6, 9.4, 9.5)  # black, inside prev body
    o, h, l, c = _series(_CTX + [prev, curr])
    out = homing_pigeon(o, h, l, c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


def test_homing_pigeon_no_signal_flat():
    o, h, l, c = _flat_series()
    out = homing_pigeon(o, h, l, c)
    assert all(v == 0 for v in out)


# ---------------------------------------------------------------------------
# gap_side_side_white — two white candles of similar size gapping the same way → +100
# ---------------------------------------------------------------------------

def test_gap_side_side_white_fires():
    # prev: white, open=9, close=11 (body 2)
    # curr: white, gaps up — open > prev close, similar body size
    prev = (9.0, 11.1, 8.9, 11.0)
    curr = (11.5, 13.6, 11.4, 13.5)  # white, open 11.5 > prev close 11.0 (gap up), body≈2
    o, h, l, c = _series(_CTX + [prev, curr])
    out = gap_side_side_white(o, h, l, c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


def test_gap_side_side_white_no_signal_flat():
    o, h, l, c = _flat_series()
    out = gap_side_side_white(o, h, l, c)
    assert all(v == 0 for v in out)


# ---------------------------------------------------------------------------
# tasuki_gap — gap then an opposite candle staying within the gap → continuation ±100
# ---------------------------------------------------------------------------

def test_tasuki_gap_bullish_fires():
    # Upside tasuki gap: first white candle, then white gaps up, then black candle
    # fills part of gap but doesn't close below first candle's close
    # We need a 3-bar pattern here: bar[i-2] white, bar[i-1] white gapped up, bar[i] black within gap
    # per plan: "gap then an opposite candle that stays within the gap → continuation ±100"
    # Implement as 2-bar: prev=white candle, curr=black candle that opened in a gap area
    # Simpler 2-bar version: prev white gapped up from prior context, curr black stays in gap
    # Actually tasuki gap reads 3 bars (bar before gap, gapped bar, filling bar)
    # But since the plan says "2-bar", we'll treat it as: prev=gapped white, curr=opposite filling partially
    # prev: white (the gapped bar), curr: black but open < prev open, close > prev-of-prev close
    # For the test: prev=white (10→12), curr=black opens inside prev body (11.5), closes above gap bottom
    prev = (10.0, 12.1, 9.9, 12.0)   # white candle (gapped bar)
    curr = (11.5, 11.6, 9.0, 10.2)   # black, closes above prev open (10.0) → stays in gap
    o, h, l, c = _series(_CTX + [prev, curr])
    out = tasuki_gap(o, h, l, c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


def test_tasuki_gap_bearish_fires():
    # Downside tasuki gap: prev black gapped down, curr white fills partially within gap
    prev = (12.0, 12.1, 9.9, 10.0)   # black candle (gapped down)
    curr = (10.5, 11.2, 9.9, 11.0)   # white, opens inside prev body (10.5), closes below prev open (12.0)
    o, h, l, c = _series(_CTX + [prev, curr])
    out = tasuki_gap(o, h, l, c)
    assert out[-1] == -100, f"expected -100, got {out[-1]}"


def test_tasuki_gap_no_signal_flat():
    o, h, l, c = _flat_series()
    out = tasuki_gap(o, h, l, c)
    assert all(v == 0 for v in out)

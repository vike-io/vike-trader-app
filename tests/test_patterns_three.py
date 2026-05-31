"""Tests for three-bar (and longer) candlestick patterns (Task 4).

Each test constructs a synthetic bar sequence whose last 3-5 bars match the
target pattern's geometry, with ~11 neutral context bars to prime avg_body.
Asserts the signal fires (+100 or -100) at the last bar; also asserts a
flat/no-pattern series returns 0.
"""

from vike_trader_app.core.indicators.patterns import (
    morning_star,
    evening_star,
    morning_doji_star,
    evening_doji_star,
    three_white_soldiers,
    three_black_crows,
    identical_three_crows,
    three_inside,
    three_outside,
    three_line_strike,
    three_stars_in_south,
    abandoned_baby,
    advance_block,
    stalled_pattern,
    two_crows,
    upside_gap_two_crows,
    tristar,
    unique_three_river,
    stick_sandwich,
    ladder_bottom,
    concealing_baby_swallow,
    rise_fall_three_methods,
    mat_hold,
    hikkake,
    hikkake_mod,
    xside_gap_three_methods,
    breakaway,
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


# 11 neutral context bars with moderate bodies to prime avg_body (body=1.0 each)
_CTX = [(10.0, 11.5, 8.5, 11.0)] * 11  # body=1.0, range=3.0


def _flat(n=20):
    """All-flat bars: open=close=high=low=10."""
    return _series([(10.0, 10.0, 10.0, 10.0)] * n)


# ---------------------------------------------------------------------------
# morning_star (+100)
# 3-bar: long black, small-body star gapping down, long white closing into first
# ---------------------------------------------------------------------------

def test_morning_star_fires():
    # bar1: long black  open=12, close=9
    # bar2: small body star gapping down below bar1 close
    # bar3: long white closing above midpoint of bar1 body (mid=10.5)
    b1 = (12.0, 12.2, 8.8, 9.0)    # long black, body=3
    b2 = (8.4,  8.6,  7.9, 8.3)    # small black star gapping below bar1 close (9.0)
    b3 = (8.5,  12.0, 8.4, 11.0)   # long white, close > mid of (12,9) = 10.5
    o, h, l, c = _series(_CTX + [b1, b2, b3])
    out = morning_star(o, h, l, c)
    assert len(out) == len(c)
    assert out[-1] == 100, f"morning_star should be +100, got {out[-1]}"


def test_morning_star_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in morning_star(o, h, l, c))


# ---------------------------------------------------------------------------
# evening_star (-100)
# 3-bar: long white, small-body star gapping up, long black closing into first
# ---------------------------------------------------------------------------

def test_evening_star_fires():
    b1 = (9.0,  12.2, 8.8, 12.0)   # long white, body=3
    b2 = (12.4, 13.0, 12.2, 12.6)  # small star gapping above bar1 close (12)
    b3 = (12.5, 12.6, 8.5,  9.5)   # long black, close < mid of (9,12)=10.5
    o, h, l, c = _series(_CTX + [b1, b2, b3])
    out = evening_star(o, h, l, c)
    assert out[-1] == -100, f"evening_star should be -100, got {out[-1]}"


def test_evening_star_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in evening_star(o, h, l, c))


# ---------------------------------------------------------------------------
# morning_doji_star (+100) — morning star where the star is a doji
# ---------------------------------------------------------------------------

def test_morning_doji_star_fires():
    b1 = (12.0, 12.2, 8.8, 9.0)    # long black, body=3
    b2 = (8.3,  8.7,  7.8, 8.31)   # doji star: body~0.01 << avg~1, gaps below 9.0
    b3 = (8.5,  12.0, 8.4, 11.0)   # long white closing above midpoint (10.5)
    o, h, l, c = _series(_CTX + [b1, b2, b3])
    out = morning_doji_star(o, h, l, c)
    assert out[-1] == 100, f"morning_doji_star should be +100, got {out[-1]}"


def test_morning_doji_star_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in morning_doji_star(o, h, l, c))


# ---------------------------------------------------------------------------
# evening_doji_star (-100) — evening star where the star is a doji
# ---------------------------------------------------------------------------

def test_evening_doji_star_fires():
    b1 = (9.0,  12.2, 8.8, 12.0)   # long white, body=3
    b2 = (12.4, 13.0, 12.2, 12.41) # doji star gapping above bar1 close (12)
    b3 = (12.5, 12.6, 8.5,  9.5)   # long black closing below midpoint (10.5)
    o, h, l, c = _series(_CTX + [b1, b2, b3])
    out = evening_doji_star(o, h, l, c)
    assert out[-1] == -100, f"evening_doji_star should be -100, got {out[-1]}"


def test_evening_doji_star_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in evening_doji_star(o, h, l, c))


# ---------------------------------------------------------------------------
# three_white_soldiers (+100)
# 3 consecutive long white candles, each opens within prior body, closes near high
# ---------------------------------------------------------------------------

def test_three_white_soldiers_fires():
    b1 = (9.0,  10.5, 8.9, 10.4)   # long white, body=1.4
    b2 = (9.8,  11.5, 9.7, 11.4)   # opens within b1 body, long white
    b3 = (10.8, 12.5, 10.7, 12.4)  # opens within b2 body, long white
    o, h, l, c = _series(_CTX + [b1, b2, b3])
    out = three_white_soldiers(o, h, l, c)
    assert out[-1] == 100, f"three_white_soldiers should be +100, got {out[-1]}"


def test_three_white_soldiers_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in three_white_soldiers(o, h, l, c))


# ---------------------------------------------------------------------------
# three_black_crows (-100)
# 3 consecutive long black candles, each opens within prior body, closes near low
# ---------------------------------------------------------------------------

def test_three_black_crows_fires():
    b1 = (12.0, 12.1, 10.5, 10.6)  # long black, body=1.4
    b2 = (11.2, 11.3, 9.7,  9.8)   # opens within b1 body, long black
    b3 = (10.2, 10.3, 8.7,  8.8)   # opens within b2 body, long black
    o, h, l, c = _series(_CTX + [b1, b2, b3])
    out = three_black_crows(o, h, l, c)
    assert out[-1] == -100, f"three_black_crows should be -100, got {out[-1]}"


def test_three_black_crows_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in three_black_crows(o, h, l, c))


# ---------------------------------------------------------------------------
# identical_three_crows (-100) — three black crows where each opens ≈ prior close
# ---------------------------------------------------------------------------

def test_identical_three_crows_fires():
    b1 = (12.0, 12.1, 10.5, 10.6)  # long black, closes=10.6
    b2 = (10.6, 10.7,  9.1,  9.2)  # opens at prior close (~10.6), long black
    b3 = (9.2,  9.3,  7.7,  7.8)   # opens at prior close (~9.2), long black
    o, h, l, c = _series(_CTX + [b1, b2, b3])
    out = identical_three_crows(o, h, l, c)
    assert out[-1] == -100, f"identical_three_crows should be -100, got {out[-1]}"


def test_identical_three_crows_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in identical_three_crows(o, h, l, c))


# ---------------------------------------------------------------------------
# three_inside (+100 up, -100 down)
# harami then a confirming third bar
# ---------------------------------------------------------------------------

def test_three_inside_up_fires():
    # prev black (b1), harami white (b2 body inside b1), confirming white (b3 close > b1 open)
    b1 = (12.0, 12.2, 8.8, 9.0)    # long black, body open=12, close=9
    b2 = (9.5,  11.0, 9.4, 10.8)   # white body inside b1 body range (9..12)
    b3 = (10.9, 12.5, 10.8, 12.3)  # confirming white, close > b1 open(12) to confirm up
    o, h, l, c = _series(_CTX + [b1, b2, b3])
    out = three_inside(o, h, l, c)
    assert out[-1] == 100, f"three_inside up should be +100, got {out[-1]}"


def test_three_inside_down_fires():
    # prev white (b1), harami black (b2 body inside b1), confirming black (b3 close < b1 open)
    b1 = (9.0,  12.2, 8.8, 12.0)   # long white, body open=9, close=12
    b2 = (11.5, 12.1, 11.0, 11.2)  # black body inside b1 body range (9..12)
    b3 = (11.1, 11.2,  8.5,  8.7)  # confirming black, close < b1 open(9)
    o, h, l, c = _series(_CTX + [b1, b2, b3])
    out = three_inside(o, h, l, c)
    assert out[-1] == -100, f"three_inside down should be -100, got {out[-1]}"


def test_three_inside_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in three_inside(o, h, l, c))


# ---------------------------------------------------------------------------
# three_outside (+100 up, -100 down)
# engulfing then a confirming third bar
# ---------------------------------------------------------------------------

def test_three_outside_up_fires():
    # b1: black; b2: white engulfs b1; b3: white confirming (close > b2 close)
    b1 = (11.0, 11.2, 9.8, 10.0)   # black
    b2 = (9.8,  12.2, 9.7, 12.0)   # white engulfs b1 (c>=b1.o, o<=b1.c)
    b3 = (12.1, 13.0, 12.0, 12.8)  # confirming white
    o, h, l, c = _series(_CTX + [b1, b2, b3])
    out = three_outside(o, h, l, c)
    assert out[-1] == 100, f"three_outside up should be +100, got {out[-1]}"


def test_three_outside_down_fires():
    # b1: white; b2: black engulfs b1; b3: black confirming
    b1 = (10.0, 12.2, 9.8, 12.0)   # white
    b2 = (12.0, 12.2, 9.7, 9.8)    # black engulfs b1 (o>=b1.c, c<=b1.o)
    b3 = (9.7,  9.8,  8.5,  8.7)   # confirming black
    o, h, l, c = _series(_CTX + [b1, b2, b3])
    out = three_outside(o, h, l, c)
    assert out[-1] == -100, f"three_outside down should be -100, got {out[-1]}"


def test_three_outside_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in three_outside(o, h, l, c))


# ---------------------------------------------------------------------------
# three_line_strike (4 bars) ±100
# 3 same-colour candles then a 4th that engulfs all three
# ---------------------------------------------------------------------------

def test_three_line_strike_bullish_fires():
    # 3 black crows then long white engulfing all 3 → +100 (bullish reversal)
    b1 = (12.0, 12.1, 10.5, 10.6)
    b2 = (11.2, 11.3,  9.7,  9.8)
    b3 = (10.2, 10.3,  8.7,  8.8)
    b4 = (8.7,  8.8,  12.2, 12.0)  # white, opens below b3 close, closes above b1 open
    o, h, l, c = _series(_CTX + [b1, b2, b3, b4])
    out = three_line_strike(o, h, l, c)
    assert out[-1] == 100, f"three_line_strike bull should be +100 (bullish reversal), got {out[-1]}"


def test_three_line_strike_bearish_fires():
    # 3 white soldiers then long black engulfing all 3 → -100 (bearish reversal)
    b1 = (9.0,  10.5, 8.9, 10.4)
    b2 = (9.8,  11.5, 9.7, 11.4)
    b3 = (10.8, 12.5, 10.7, 12.4)
    b4 = (12.5, 12.6, 8.8,  9.0)   # black, opens above b3 close, closes below b1 open
    o, h, l, c = _series(_CTX + [b1, b2, b3, b4])
    out = three_line_strike(o, h, l, c)
    assert out[-1] == -100, f"three_line_strike bear should be -100 (bearish reversal), got {out[-1]}"


def test_three_line_strike_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in three_line_strike(o, h, l, c))


# ---------------------------------------------------------------------------
# three_stars_in_south (+100)
# 3 black candles of diminishing size/range in downtrend
# ---------------------------------------------------------------------------

def test_three_stars_in_south_fires():
    b1 = (12.0, 12.1,  8.0,  9.0)  # large black, body=3, range=4.1
    b2 = (10.0, 10.1,  8.5,  9.2)  # smaller black, body=0.8, range=1.6, low > b1.low
    b3 = (9.4,  9.8,  9.0,  9.3)   # small black star, body=0.1, high<b2.high, low>b2.low
    o, h, l, c = _series(_CTX + [b1, b2, b3])
    out = three_stars_in_south(o, h, l, c)
    assert out[-1] == 100, f"three_stars_in_south should be +100, got {out[-1]}"


def test_three_stars_in_south_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in three_stars_in_south(o, h, l, c))


# ---------------------------------------------------------------------------
# abandoned_baby (3 bars: long candle, doji with GAPS on both sides, confirming)
# Bullish: long black, gap-down doji, gap-up white → +100
# Bearish: long white, gap-up doji, gap-down black → -100
# ---------------------------------------------------------------------------

def test_abandoned_baby_bullish_fires():
    b1 = (12.0, 12.2, 8.8, 9.0)    # long black
    b2 = (8.0,  8.3,  7.5, 8.01)   # doji, entire bar below b1 low (gaps down) and above nothing
    b3 = (8.4,  12.0, 8.3, 11.5)   # white, entire bar above b2 high (gaps up)
    o, h, l, c = _series(_CTX + [b1, b2, b3])
    out = abandoned_baby(o, h, l, c)
    assert out[-1] == 100, f"abandoned_baby bull should be +100, got {out[-1]}"


def test_abandoned_baby_bearish_fires():
    b1 = (9.0,  12.2, 8.8, 12.0)   # long white
    b2 = (12.5, 13.0, 12.4, 12.51) # doji, entire bar above b1 high (gaps up)
    b3 = (12.0, 12.1,  8.5,  9.0)  # black, entire bar below b2 low (gaps down)
    o, h, l, c = _series(_CTX + [b1, b2, b3])
    out = abandoned_baby(o, h, l, c)
    assert out[-1] == -100, f"abandoned_baby bear should be -100, got {out[-1]}"


def test_abandoned_baby_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in abandoned_baby(o, h, l, c))


# ---------------------------------------------------------------------------
# advance_block (-100)
# 3 white candles with weakening bodies / growing upper shadows → bearish warning
# ---------------------------------------------------------------------------

def test_advance_block_fires():
    b1 = (9.0,  10.8, 8.9, 10.6)   # white, body=1.6, small upper shadow=0.2
    b2 = (10.0, 11.8, 9.9, 11.3)   # white, body=1.3 < b1, upper shadow=0.5 > b1
    b3 = (10.8, 12.5, 10.7, 11.5)  # white, body=0.7 < b2, upper shadow=1.0 > b2
    o, h, l, c = _series(_CTX + [b1, b2, b3])
    out = advance_block(o, h, l, c)
    assert out[-1] == -100, f"advance_block should be -100, got {out[-1]}"


def test_advance_block_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in advance_block(o, h, l, c))


# ---------------------------------------------------------------------------
# stalled_pattern (-100)
# Two long whites then a small white body near the top → bearish warning
# ---------------------------------------------------------------------------

def test_stalled_pattern_fires():
    b1 = (9.0,  11.5, 8.9, 11.3)   # long white, body=2.3
    b2 = (10.8, 13.0, 10.7, 12.8)  # long white, body=2.0
    b3 = (12.7, 13.2, 12.6, 12.85) # small white near top, body=0.15 << avg
    o, h, l, c = _series(_CTX + [b1, b2, b3])
    out = stalled_pattern(o, h, l, c)
    assert out[-1] == -100, f"stalled_pattern should be -100, got {out[-1]}"


def test_stalled_pattern_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in stalled_pattern(o, h, l, c))


# ---------------------------------------------------------------------------
# two_crows (-100)
# Long white, gap-up black, then black closing into first body
# ---------------------------------------------------------------------------

def test_two_crows_fires():
    b1 = (9.0,  12.2, 8.8, 12.0)   # long white, body=3
    b2 = (13.0, 13.5, 12.5, 12.6)  # black (open>close), gap up (open 13.0 > b1 close 12.0)
    b3 = (12.8, 13.1, 9.5,  10.0)  # black, closes inside b1 body (9 < 10 < 12)
    o, h, l, c = _series(_CTX + [b1, b2, b3])
    out = two_crows(o, h, l, c)
    assert out[-1] == -100, f"two_crows should be -100, got {out[-1]}"


def test_two_crows_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in two_crows(o, h, l, c))


# ---------------------------------------------------------------------------
# upside_gap_two_crows (-100)
# Like two_crows with a strict upside gap maintained
# ---------------------------------------------------------------------------

def test_upside_gap_two_crows_fires():
    b1 = (9.0,  12.2, 8.8, 12.0)   # long white
    b2 = (13.0, 13.5, 12.5, 12.6)  # black (open>close), gaps up (open 13.0 > b1 close 12.0)
    b3 = (13.2, 13.5, 12.1, 12.3)  # black, engulfs b2 (o3>=o2, c3<=c2) stays above b1 close (12)
    o, h, l, c = _series(_CTX + [b1, b2, b3])
    out = upside_gap_two_crows(o, h, l, c)
    assert out[-1] == -100, f"upside_gap_two_crows should be -100, got {out[-1]}"


def test_upside_gap_two_crows_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in upside_gap_two_crows(o, h, l, c))


# ---------------------------------------------------------------------------
# tristar (3 dojis, middle gapped away) ±100
# ---------------------------------------------------------------------------

def test_tristar_bullish_fires():
    # Middle doji gaps DOWN from first, third gaps back up
    b1 = (10.0, 11.0, 9.0, 10.01)  # doji near 10
    b2 = (8.5,  9.2,  7.8,  8.51)  # doji gapping down (high < b1 low=9)
    b3 = (9.5,  10.5, 9.4,  9.51)  # doji gapping up (low > b2 high=9.2)
    o, h, l, c = _series(_CTX + [b1, b2, b3])
    out = tristar(o, h, l, c)
    assert out[-1] == 100, f"tristar bull should be +100, got {out[-1]}"


def test_tristar_bearish_fires():
    # Middle doji gaps UP from first, third gaps back down
    b1 = (10.0, 11.0, 9.0, 10.01)  # doji near 10
    b2 = (11.5, 12.5, 11.3, 11.51) # doji gapping up (low > b1 high=11)
    b3 = (10.3, 11.2, 10.2, 10.31) # doji gapping down (high < b2 low=11.3)
    o, h, l, c = _series(_CTX + [b1, b2, b3])
    out = tristar(o, h, l, c)
    assert out[-1] == -100, f"tristar bear should be -100, got {out[-1]}"


def test_tristar_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in tristar(o, h, l, c))


# ---------------------------------------------------------------------------
# unique_three_river (+100)
# long black, second black with lower low (harami-like), small white
# ---------------------------------------------------------------------------

def test_unique_three_river_fires():
    b1 = (12.0, 12.2, 8.8, 9.0)    # long black
    b2 = (10.5, 10.8, 7.5,  9.5)   # black, lower low than b1, body inside b1
    b3 = (9.5,  9.8,  9.3,  9.6)   # small white, body < b2 body
    o, h, l, c = _series(_CTX + [b1, b2, b3])
    out = unique_three_river(o, h, l, c)
    assert out[-1] == 100, f"unique_three_river should be +100, got {out[-1]}"


def test_unique_three_river_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in unique_three_river(o, h, l, c))


# ---------------------------------------------------------------------------
# stick_sandwich (+100)
# black, white, black — two blacks with equal closes
# ---------------------------------------------------------------------------

def test_stick_sandwich_fires():
    b1 = (12.0, 12.2, 9.8, 10.0)   # black, close=10.0
    b2 = (10.2, 12.5, 10.1, 12.3)  # white, higher
    b3 = (12.5, 12.6, 9.8, 10.0)   # black, close=10.0 ≈ b1 close
    o, h, l, c = _series(_CTX + [b1, b2, b3])
    out = stick_sandwich(o, h, l, c)
    assert out[-1] == 100, f"stick_sandwich should be +100, got {out[-1]}"


def test_stick_sandwich_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in stick_sandwich(o, h, l, c))


# ---------------------------------------------------------------------------
# ladder_bottom (+100) — 4 black candles then a white reversal (5 bars total)
# ---------------------------------------------------------------------------

def test_ladder_bottom_fires():
    b1 = (12.0, 12.1, 10.5, 10.6)  # black
    b2 = (11.0, 11.1,  9.5,  9.6)  # black, lower
    b3 = (10.0, 10.1,  8.5,  8.6)  # black, lower
    b4 = (9.0,  9.5,   7.5,  8.0)  # black, lower, with upper shadow
    b5 = (8.5,  12.0,  8.4, 11.5)  # white reversal gapping up
    o, h, l, c = _series(_CTX + [b1, b2, b3, b4, b5])
    out = ladder_bottom(o, h, l, c)
    assert out[-1] == 100, f"ladder_bottom should be +100, got {out[-1]}"


def test_ladder_bottom_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in ladder_bottom(o, h, l, c))


# ---------------------------------------------------------------------------
# concealing_baby_swallow (+100) — 4 black marubozu sequence
# ---------------------------------------------------------------------------

def test_concealing_baby_swallow_fires():
    # 4 black candles, first two are marubozu, third gaps down with upper shadow, fourth engulfs
    b1 = (12.0, 12.0, 10.0, 10.0)  # black marubozu (no shadows)
    b2 = (10.0, 10.0,  8.0,  8.0)  # black marubozu
    b3 = (7.8,  8.2,  6.5,  6.8)   # black with upper shadow (gap down from b2)
    b4 = (7.5,  8.5,  5.5,  5.8)   # black engulfs b3 range
    o, h, l, c = _series(_CTX + [b1, b2, b3, b4])
    out = concealing_baby_swallow(o, h, l, c)
    assert out[-1] == 100, f"concealing_baby_swallow should be +100, got {out[-1]}"


def test_concealing_baby_swallow_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in concealing_baby_swallow(o, h, l, c))


# ---------------------------------------------------------------------------
# rise_fall_three_methods (5 bars) ±100
# long candle, 3 small opposite-colour inside bars, long candle continuing
# ---------------------------------------------------------------------------

def test_rise_three_methods_fires():
    b1 = (9.0,  12.5, 8.9, 12.3)   # long white, body=3.3
    b2 = (11.8, 12.0, 11.5, 11.7)  # small black inside b1 range
    b3 = (11.5, 11.8, 11.2, 11.4)  # small black inside b1 range
    b4 = (11.3, 11.6, 11.0, 11.2)  # small black inside b1 range
    b5 = (11.5, 14.0, 11.4, 13.8)  # long white continuation, close > b1 close
    o, h, l, c = _series(_CTX + [b1, b2, b3, b4, b5])
    out = rise_fall_three_methods(o, h, l, c)
    assert out[-1] == 100, f"rise_three_methods should be +100, got {out[-1]}"


def test_fall_three_methods_fires():
    b1 = (12.0, 12.1,  8.7,  9.0)  # long black, body=3
    b2 = (9.5,   9.8,  9.2,  9.6)  # small white inside b1 range
    b3 = (9.7,  10.0,  9.4,  9.8)  # small white inside b1 range
    b4 = (9.9,  10.2,  9.6, 10.0)  # small white inside b1 range
    b5 = (9.5,  9.6,   6.5,  6.8)  # long black continuation, close < b1 close
    o, h, l, c = _series(_CTX + [b1, b2, b3, b4, b5])
    out = rise_fall_three_methods(o, h, l, c)
    assert out[-1] == -100, f"fall_three_methods should be -100, got {out[-1]}"


def test_rise_fall_three_methods_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in rise_fall_three_methods(o, h, l, c))


# ---------------------------------------------------------------------------
# mat_hold (+100) — bullish gap variant of rising three methods
# ---------------------------------------------------------------------------

def test_mat_hold_fires():
    b1 = (9.0,  12.5, 8.9, 12.3)   # long white
    b2 = (12.5, 12.8, 12.0, 12.2)  # small black, gaps up from b1 (open > b1 close)
    b3 = (12.0, 12.3, 11.7, 11.9)  # small black, still in b1 range (but above b1 open=9)
    b4 = (11.8, 12.1, 11.5, 11.7)  # small black, still above b1 open
    b5 = (12.0, 14.0, 11.9, 13.8)  # long white continuation, close > b1 close
    o, h, l, c = _series(_CTX + [b1, b2, b3, b4, b5])
    out = mat_hold(o, h, l, c)
    assert out[-1] == 100, f"mat_hold should be +100, got {out[-1]}"


def test_mat_hold_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in mat_hold(o, h, l, c))


# ---------------------------------------------------------------------------
# hikkake (inside bar then false breakout then reversal) ±100
# ---------------------------------------------------------------------------

def test_hikkake_bullish_fires():
    # b1: normal; b2: inside bar (range inside b1); b3: breaks below b2 low (false bear); b4: reverses up above b2 high
    b1 = (9.0,  12.0, 8.5, 11.5)   # reference bar
    b2 = (10.0, 11.0, 9.5, 10.5)   # inside bar (h<b1.h, l>b1.l)
    b3 = (10.0, 10.5, 9.0,  9.5)   # breaks below b2 low (false bearish breakout)
    b4 = (9.8,  12.0, 9.7, 11.8)   # reverses above b2 high (11.0) → bullish hikkake
    o, h, l, c = _series(_CTX + [b1, b2, b3, b4])
    out = hikkake(o, h, l, c)
    assert out[-1] == 100, f"hikkake bull should be +100, got {out[-1]}"


def test_hikkake_bearish_fires():
    b1 = (9.0,  12.0, 8.5, 11.5)
    b2 = (10.0, 11.0, 9.5, 10.5)   # inside bar
    b3 = (10.5, 11.5, 10.0, 11.2)  # breaks above b2 high (false bull breakout)
    b4 = (10.8, 11.0,  8.5,  8.8)  # reverses below b2 low (9.5) → bearish hikkake
    o, h, l, c = _series(_CTX + [b1, b2, b3, b4])
    out = hikkake(o, h, l, c)
    assert out[-1] == -100, f"hikkake bear should be -100, got {out[-1]}"


def test_hikkake_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in hikkake(o, h, l, c))


# ---------------------------------------------------------------------------
# hikkake_mod (modified — 5 bars, confirming bar added) ±100
# ---------------------------------------------------------------------------

def test_hikkake_mod_bullish_fires():
    b1 = (9.0,  12.0, 8.5, 11.5)
    b2 = (10.0, 11.0, 9.5, 10.5)   # inside bar
    b3 = (10.0, 10.5, 9.0,  9.5)   # false bear breakout (low < b2.low)
    b4 = (9.8,  11.5, 9.7, 11.3)   # above b2.high (11.0) — hikkake reversal bar
    b5 = (11.4, 12.5, 11.3, 12.3)  # confirming white
    o, h, l, c = _series(_CTX + [b1, b2, b3, b4, b5])
    out = hikkake_mod(o, h, l, c)
    assert out[-1] == 100, f"hikkake_mod bull should be +100, got {out[-1]}"


def test_hikkake_mod_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in hikkake_mod(o, h, l, c))


# ---------------------------------------------------------------------------
# xside_gap_three_methods ±100
# gap then a filling candle → continuation
# Bullish: two whites with gap between, then black that stays within the gap
# ---------------------------------------------------------------------------

def test_xside_gap_three_methods_bullish_fires():
    # upside gap three methods: b1 white, b2 white gaps up, b3 black fills back but stays above b1 close
    b1 = (9.0,  11.0, 8.9, 10.8)   # white, close=10.8
    b2 = (11.0, 12.5, 10.9, 12.3)  # white, gaps up (open > b1.close)
    b3 = (12.0, 12.1, 11.0, 11.2)  # black, closes above b1 close (10.8) — stays in gap
    o, h, l, c = _series(_CTX + [b1, b2, b3])
    out = xside_gap_three_methods(o, h, l, c)
    assert out[-1] == 100, f"xside_gap_three_methods bull should be +100, got {out[-1]}"


def test_xside_gap_three_methods_bearish_fires():
    # downside gap three methods: b1 black, b2 black gaps down, b3 white fills but stays below b1 close
    b1 = (12.0, 12.1, 10.0, 10.2)  # black, close=10.2
    b2 = (10.0, 10.1,  8.5,  8.8)  # black, gaps down (open < b1.close)
    b3 = (8.9,  10.0,  8.8,  9.8)  # white, closes below b1 close (10.2) — stays in gap
    o, h, l, c = _series(_CTX + [b1, b2, b3])
    out = xside_gap_three_methods(o, h, l, c)
    assert out[-1] == -100, f"xside_gap_three_methods bear should be -100, got {out[-1]}"


def test_xside_gap_three_methods_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in xside_gap_three_methods(o, h, l, c))


# ---------------------------------------------------------------------------
# breakaway (5-bar pattern) ±100
# gap then run then reversal closing into the gap
# ---------------------------------------------------------------------------

def test_breakaway_bullish_fires():
    # 5-bar bearish breakaway: first long black, gap down, 2 black trending, then white reversing
    b1 = (12.0, 12.2, 10.8, 11.0)  # long black, close=11.0
    b2 = (10.5, 10.6,  9.5,  9.7)  # black, gaps down (open<b1.close=11 no, but high<b1.low is strict)
    b3 = (9.6,  9.7,   8.6,  8.8)  # black trending down
    b4 = (8.7,  8.8,   7.7,  7.9)  # black trending down
    b5 = (8.0,  11.5,  7.9, 10.5)  # white reversal, closes into gap area (above b2.open)
    o, h, l, c = _series(_CTX + [b1, b2, b3, b4, b5])
    out = breakaway(o, h, l, c)
    assert out[-1] == 100, f"breakaway bull should be +100, got {out[-1]}"


def test_breakaway_no_pattern():
    o, h, l, c = _flat()
    assert all(v == 0 for v in breakaway(o, h, l, c))

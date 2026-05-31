"""Tests for the 18 single-bar candlestick patterns (Task 2).

Each test constructs a synthetic bar sequence whose last bar matches the target
pattern's geometry, and asserts the signal fires (+100 or -100). A plain flat
series is also tested for a few patterns to assert they all return 0 cleanly.
"""

import pytest

from vike_trader_app.core.indicators.patterns import (
    inverted_hammer,
    hanging_man,
    shooting_star,
    dragonfly_doji,
    gravestone_doji,
    longlegged_doji,
    rickshaw_man,
    takuri,
    marubozu,
    closing_marubozu,
    opening_marubozu,
    spinning_top,
    high_wave,
    long_line,
    short_line,
    belt_hold,
    doji_star,
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


# 11 neutral context bars used to prime avg_body
_CTX = [(10.0, 10.5, 9.5, 10.0)] * 11   # body=0, avg_body context — kept small but non-zero
# Better context: bars with a moderate body
_CTX_BODY = [(10.0, 11.0, 9.0, 10.5)] * 11   # body=0.5 each


def _flat_series(n=20):
    """All-flat bars: open=close=high=low → body=0, range=0."""
    return _series([(10.0, 10.0, 10.0, 10.0)] * n)


# ---------------------------------------------------------------------------
# inverted_hammer — small body near the low, long upper shadow (≥2×body),
#                   little lower shadow; non-directional → +100
# ---------------------------------------------------------------------------

def test_inverted_hammer_fires():
    # body = 0.2 (10.0 → 10.2, white), upper shadow = 1.8 (top of body 10.2 → 12.0),
    # lower shadow = 0.05 (lows 9.95)
    # body/range = 0.2/2.05 ≈ 9.8% (small), upper ≥ 2*body ✓, lower ≤ body ✓
    target = (10.0, 12.0, 9.95, 10.2)
    o, h, l, c = _series(_CTX_BODY + [target])
    out = inverted_hammer(o, h, l, c)
    assert len(out) == len(c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


def test_inverted_hammer_no_signal_flat():
    o, h, l, c = _flat_series()
    out = inverted_hammer(o, h, l, c)
    assert all(v == 0 for v in out)


# ---------------------------------------------------------------------------
# hanging_man — hammer geometry (long lower shadow) but treated as bearish → -100
# ---------------------------------------------------------------------------

def test_hanging_man_fires():
    # body = 0.2 (10.2 → 10.0, black), lower shadow = 2.0 (min 10.0 → 8.0),
    # upper shadow = 0.05; small body vs range
    target = (10.2, 10.25, 8.0, 10.0)
    o, h, l, c = _series(_CTX_BODY + [target])
    out = hanging_man(o, h, l, c)
    assert out[-1] == -100, f"expected -100, got {out[-1]}"


def test_hanging_man_no_signal_flat():
    o, h, l, c = _flat_series()
    out = hanging_man(o, h, l, c)
    assert all(v == 0 for v in out)


# ---------------------------------------------------------------------------
# shooting_star — inverted_hammer geometry (long upper shadow) → -100
# ---------------------------------------------------------------------------

def test_shooting_star_fires():
    # body = 0.2 (10.4 → 10.2, black), upper shadow = 1.8 (max 10.4 → 12.2),
    # lower shadow = 0.05
    target = (10.4, 12.2, 10.15, 10.2)
    o, h, l, c = _series(_CTX_BODY + [target])
    out = shooting_star(o, h, l, c)
    assert out[-1] == -100, f"expected -100, got {out[-1]}"


# ---------------------------------------------------------------------------
# dragonfly_doji — doji, open≈close≈high, very long lower shadow → +100
# ---------------------------------------------------------------------------

def test_dragonfly_doji_fires():
    # open=close=high=10.0, low=7.0 → body=0, lower=3.0 (≥50% of range=3), range=3
    # To make it a doji: avg_body must be non-zero from context
    ctx = [(10.0, 11.0, 9.0, 10.5)] * 11
    target = (10.0, 10.0, 7.0, 10.0)
    o, h, l, c = _series(ctx + [target])
    out = dragonfly_doji(o, h, l, c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


# ---------------------------------------------------------------------------
# gravestone_doji — doji, open≈close≈low, very long upper shadow → -100
# ---------------------------------------------------------------------------

def test_gravestone_doji_fires():
    ctx = [(10.0, 11.0, 9.0, 10.5)] * 11
    target = (10.0, 13.0, 10.0, 10.0)
    o, h, l, c = _series(ctx + [target])
    out = gravestone_doji(o, h, l, c)
    assert out[-1] == -100, f"expected -100, got {out[-1]}"


# ---------------------------------------------------------------------------
# longlegged_doji — doji with long upper AND lower shadows → +100
# ---------------------------------------------------------------------------

def test_longlegged_doji_fires():
    ctx = [(10.0, 11.0, 9.0, 10.5)] * 11
    # open=close=10, high=13, low=7 → upper=3, lower=3, body=0
    target = (10.0, 13.0, 7.0, 10.0)
    o, h, l, c = _series(ctx + [target])
    out = longlegged_doji(o, h, l, c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


# ---------------------------------------------------------------------------
# rickshaw_man — long-legged doji body near the middle of the range → +100
# ---------------------------------------------------------------------------

def test_rickshaw_man_fires():
    ctx = [(10.0, 11.0, 9.0, 10.5)] * 11
    # range 7–13=6, mid=10; open=close=9.95 (near middle), body≈0
    target = (9.95, 13.0, 7.0, 9.95)
    o, h, l, c = _series(ctx + [target])
    out = rickshaw_man(o, h, l, c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


# ---------------------------------------------------------------------------
# takuri — dragonfly doji with exceptionally long lower shadow (≥3×range/4) → +100
# ---------------------------------------------------------------------------

def test_takuri_fires():
    ctx = [(10.0, 11.0, 9.0, 10.5)] * 11
    # open=close=10.0, high=10.1, low=6.0 → body=0, range=4.1, lower=4.0 (≥3×any body)
    target = (10.0, 10.1, 6.0, 10.0)
    o, h, l, c = _series(ctx + [target])
    out = takuri(o, h, l, c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


# ---------------------------------------------------------------------------
# marubozu — body ≈ full range (shadows ≤ 5% of range);
#            white → +100, black → -100
# ---------------------------------------------------------------------------

def test_marubozu_white_fires():
    ctx = [(10.0, 11.0, 9.0, 10.5)] * 11
    # open=9.0, close=12.0, high=12.0, low=9.0 → no shadows at all
    target = (9.0, 12.0, 9.0, 12.0)
    o, h, l, c = _series(ctx + [target])
    out = marubozu(o, h, l, c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


def test_marubozu_black_fires():
    ctx = [(10.0, 11.0, 9.0, 10.5)] * 11
    target = (12.0, 12.0, 9.0, 9.0)
    o, h, l, c = _series(ctx + [target])
    out = marubozu(o, h, l, c)
    assert out[-1] == -100, f"expected -100, got {out[-1]}"


def test_marubozu_no_signal_flat():
    o, h, l, c = _flat_series()
    out = marubozu(o, h, l, c)
    assert all(v == 0 for v in out)


# ---------------------------------------------------------------------------
# closing_marubozu — no shadow on the CLOSE side;
#                    white: no upper shadow; black: no lower shadow → ±100
# ---------------------------------------------------------------------------

def test_closing_marubozu_white_fires():
    ctx = [(10.0, 11.0, 9.0, 10.5)] * 11
    # white: open=9.0, high=12.0, low=8.5, close=12.0 → upper=0, lower=0.5 (ok)
    target = (9.0, 12.0, 8.5, 12.0)
    o, h, l, c = _series(ctx + [target])
    out = closing_marubozu(o, h, l, c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


def test_closing_marubozu_black_fires():
    ctx = [(10.0, 11.0, 9.0, 10.5)] * 11
    # black: open=12.0, high=12.5, low=9.0, close=9.0 → lower=0, upper=0.5 (ok)
    target = (12.0, 12.5, 9.0, 9.0)
    o, h, l, c = _series(ctx + [target])
    out = closing_marubozu(o, h, l, c)
    assert out[-1] == -100, f"expected -100, got {out[-1]}"


# ---------------------------------------------------------------------------
# opening_marubozu — no shadow on the OPEN side;
#                    white: no lower shadow; black: no upper shadow → ±100
# ---------------------------------------------------------------------------

def test_opening_marubozu_white_fires():
    ctx = [(10.0, 11.0, 9.0, 10.5)] * 11
    # white: open=low=9.0, close=12.0, high=12.5 → lower=0, upper=0.5 (ok)
    target = (9.0, 12.5, 9.0, 12.0)
    o, h, l, c = _series(ctx + [target])
    out = opening_marubozu(o, h, l, c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


def test_opening_marubozu_black_fires():
    ctx = [(10.0, 11.0, 9.0, 10.5)] * 11
    # black: open=high=12.0, close=9.0, low=8.5 → upper=0, lower=0.5 (ok)
    target = (12.0, 12.0, 8.5, 9.0)
    o, h, l, c = _series(ctx + [target])
    out = opening_marubozu(o, h, l, c)
    assert out[-1] == -100, f"expected -100, got {out[-1]}"


# ---------------------------------------------------------------------------
# spinning_top — small body, upper and lower shadows each > body → +100
# ---------------------------------------------------------------------------

def test_spinning_top_fires():
    ctx = [(10.0, 11.0, 9.0, 10.5)] * 11
    # body=0.2 (10.0→10.2), upper=1.8 (10.2→12.0), lower=1.8 (10.0→8.2)
    target = (10.0, 12.0, 8.2, 10.2)
    o, h, l, c = _series(ctx + [target])
    out = spinning_top(o, h, l, c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


# ---------------------------------------------------------------------------
# high_wave — very small body with very long upper AND lower shadows (≥3×body) → +100
# ---------------------------------------------------------------------------

def test_high_wave_fires():
    ctx = [(10.0, 11.0, 9.0, 10.5)] * 11
    # body=0.1 (10.0→10.1), upper=3.9 (10.1→14.0), lower=3.0 (10.0→7.0)
    target = (10.0, 14.0, 7.0, 10.1)
    o, h, l, c = _series(ctx + [target])
    out = high_wave(o, h, l, c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


# ---------------------------------------------------------------------------
# long_line — body ≥ 1.3× avg body; white → +100, black → -100
# ---------------------------------------------------------------------------

def test_long_line_white_fires():
    # context bars body=0.5 each; avg≈0.5; need body≥0.65
    ctx = [(10.0, 11.0, 9.5, 10.5)] * 11  # body=0.5
    target = (9.0, 12.0, 8.8, 12.0)  # body=3.0 >> 1.3×0.5
    o, h, l, c = _series(ctx + [target])
    out = long_line(o, h, l, c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


def test_long_line_black_fires():
    ctx = [(10.0, 11.0, 9.5, 10.5)] * 11
    target = (12.0, 12.2, 8.8, 9.0)  # body=3.0
    o, h, l, c = _series(ctx + [target])
    out = long_line(o, h, l, c)
    assert out[-1] == -100, f"expected -100, got {out[-1]}"


# ---------------------------------------------------------------------------
# short_line — body ≤ 0.5× avg body, small range → +100 (presence)
# ---------------------------------------------------------------------------

def test_short_line_fires():
    ctx = [(10.0, 11.0, 9.0, 10.5)] * 11  # body=0.5
    # body=0.1 (≤0.5*0.5=0.25), range=0.2 (small)
    target = (10.0, 10.2, 9.9, 10.1)
    o, h, l, c = _series(ctx + [target])
    out = short_line(o, h, l, c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


# ---------------------------------------------------------------------------
# belt_hold — white opens at its low (no lower shadow), long body → +100;
#             black opens at its high (no upper shadow), long body → -100
# ---------------------------------------------------------------------------

def test_belt_hold_white_fires():
    ctx = [(10.0, 11.0, 9.0, 10.5)] * 11
    # white: open=low=8.0, close=11.0, high=11.2 → no lower shadow, body=3.0 (long)
    target = (8.0, 11.2, 8.0, 11.0)
    o, h, l, c = _series(ctx + [target])
    out = belt_hold(o, h, l, c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


def test_belt_hold_black_fires():
    ctx = [(10.0, 11.0, 9.0, 10.5)] * 11
    # black: open=high=12.0, close=9.0, low=8.8 → no upper shadow, body=3.0 (long)
    target = (12.0, 12.0, 8.8, 9.0)
    o, h, l, c = _series(ctx + [target])
    out = belt_hold(o, h, l, c)
    assert out[-1] == -100, f"expected -100, got {out[-1]}"


# ---------------------------------------------------------------------------
# doji_star — doji that gaps away from a prior long body:
#             gap down after black → +100 (bullish morning-doji-star setup)
#             gap up after white → -100 (bearish evening-doji-star setup)
# ---------------------------------------------------------------------------

def test_doji_star_bullish_fires():
    # Prior bar: long black (body 2.0), then doji gaps DOWN below its close → +100
    ctx = [(10.0, 11.0, 9.0, 10.5)] * 10
    prev = (11.0, 11.1, 8.5, 9.0)   # long black body: open=11, close=9, body=2
    # doji: open=close=8.0, high=8.4, low=7.6; entire bar below prev close(9) → gap down
    star = (8.0, 8.4, 7.6, 8.0)
    o, h, l, c = _series(ctx + [prev, star])
    out = doji_star(o, h, l, c)
    assert out[-1] == 100, f"expected +100, got {out[-1]}"


def test_doji_star_bearish_fires():
    # Prior bar: long white (body 2.0), then doji gaps UP above its close → -100
    ctx = [(10.0, 11.0, 9.0, 10.5)] * 10
    prev = (9.0, 12.5, 8.9, 11.0)   # long white body: open=9, close=11, body=2
    # doji: open=close=12.0, high=12.5, low=11.5; entire bar above prev close(11) → gap up
    star = (12.0, 12.5, 11.5, 12.0)
    o, h, l, c = _series(ctx + [prev, star])
    out = doji_star(o, h, l, c)
    assert out[-1] == -100, f"expected -100, got {out[-1]}"


def test_doji_star_no_signal_flat():
    o, h, l, c = _flat_series()
    out = doji_star(o, h, l, c)
    assert all(v == 0 for v in out)

"""Candlestick patterns: each returns an int signal series (+100 bull / -100 bear / 0 none)."""

import pytest

from vike_trader_app.core.indicators import base
from vike_trader_app.core.indicators.patterns import doji, engulfing, hammer


def _series(bars):
    """bars = list of (o,h,l,c); return parallel o/h/l/c lists."""
    o = [b[0] for b in bars]; h = [b[1] for b in bars]
    l = [b[2] for b in bars]; c = [b[3] for b in bars]
    return o, h, l, c


def test_doji_fires_on_doji_bar():
    # tiny body, real range -> doji at the last bar
    o, h, l, c = _series([(10, 11, 9, 10.5)] * 12 + [(10, 12, 8, 10.02)])
    out = doji(o, h, l, c)
    assert len(out) == len(c)
    assert out[-1] == 100  # doji is non-directional -> +100 "present"


def test_engulfing_bullish():
    # down candle then a bigger up candle that engulfs it
    o, h, l, c = _series([(10, 10.2, 8.8, 9.0)] * 11 + [(9.5, 9.6, 9.0, 9.1), (8.9, 11.0, 8.8, 10.8)])
    out = engulfing(o, h, l, c)
    assert out[-1] == 100  # bullish engulfing


def test_hammer_fires():
    # small body near the top, long lower shadow, after a context of bars
    o, h, l, c = _series([(10, 10.5, 9.5, 10)] * 11 + [(10, 10.2, 8.0, 10.1)])
    out = hammer(o, h, l, c)
    assert out[-1] == 100


def test_patterns_registered():
    names = {s.name for s in base.list_indicators(category="pattern")}
    assert {"doji", "engulfing", "hammer"} <= names

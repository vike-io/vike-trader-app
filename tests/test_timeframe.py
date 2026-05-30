"""Multi-timeframe aggregation tests."""

import pytest

from vike_trader_app.core.model import Bar
from vike_trader_app.core.timeframe import parse_timeframe, resample


def _bar(ts, o, h, l, c, v=1.0):
    return Bar(ts=ts, open=o, high=h, low=l, close=c, volume=v)


def test_parse_timeframe_minutes_hours_days():
    assert parse_timeframe("1m") == 60_000
    assert parse_timeframe("5m") == 300_000
    assert parse_timeframe("1h") == 3_600_000
    assert parse_timeframe("4h") == 14_400_000
    assert parse_timeframe("1d") == 86_400_000


def test_parse_timeframe_rejects_garbage():
    with pytest.raises(ValueError):
        parse_timeframe("banana")
    with pytest.raises(ValueError):
        parse_timeframe("10x")


def test_resample_three_1m_bars_into_one_3m_bar():
    base = [
        _bar(0, 100, 105, 99, 101, v=1.0),
        _bar(60_000, 101, 110, 100, 108, v=2.0),
        _bar(120_000, 108, 112, 95, 96, v=3.0),
    ]
    coarse = resample(base, 180_000)  # 3 minutes
    assert len(coarse) == 1
    c = coarse[0]
    assert c.ts == 0
    assert c.open == 100  # first
    assert c.high == 112  # max
    assert c.low == 95  # min
    assert c.close == 96  # last
    assert c.volume == 6.0  # sum


def test_resample_aligns_to_epoch_windows():
    # two windows of 2m each: [0,120k) and [120k,240k)
    base = [
        _bar(0, 10, 10, 10, 11),
        _bar(60_000, 11, 12, 9, 12),
        _bar(120_000, 12, 13, 12, 13),
        _bar(180_000, 13, 14, 8, 9),
    ]
    coarse = resample(base, 120_000)
    assert [c.ts for c in coarse] == [0, 120_000]
    assert coarse[0].open == 10 and coarse[0].close == 12 and coarse[0].high == 12 and coarse[0].low == 9
    assert coarse[1].open == 12 and coarse[1].close == 9 and coarse[1].high == 14 and coarse[1].low == 8


def test_resample_empty():
    assert resample([], 60_000) == []

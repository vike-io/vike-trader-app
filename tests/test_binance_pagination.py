"""Paginated Binance fetch (assemble years of bars from capped pages)."""

import pytest

from vike_trader_app.core.model import Bar
from vike_trader_app.data import binance_source as bs


def test_interval_ms_known_values():
    assert bs.interval_ms("1m") == 60_000
    assert bs.interval_ms("1h") == 3_600_000
    assert bs.interval_ms("1d") == 86_400_000


def test_interval_ms_unknown_raises():
    with pytest.raises(KeyError):
        bs.interval_ms("7s")


def _pager(all_ts, page_limit):
    """A fake page fetcher over a synthetic dataset; records the calls it received."""
    calls = []

    def pager(start, end):
        calls.append((start, end))
        sel = [t for t in all_ts if start <= t <= end][:page_limit]
        return [[t, "1", "2", "0.5", "1.5", "3"] for t in sel]

    return pager, calls


def test_paginate_assembles_all_pages_in_order():
    step = 60_000
    all_ts = [i * step for i in range(10)]
    pager, calls = _pager(all_ts, page_limit=3)
    raw = bs.paginate(0, 9 * step, step, pager)
    assert [int(r[0]) for r in raw] == all_ts  # all 10, ascending, no gaps
    assert len(calls) == 4  # 3 + 3 + 3 + 1


def test_paginate_no_duplicate_timestamps():
    step = 60_000
    all_ts = [i * step for i in range(7)]
    pager, _ = _pager(all_ts, page_limit=3)
    raw = bs.paginate(0, 6 * step, step, pager)
    seen = [int(r[0]) for r in raw]
    assert len(seen) == len(set(seen))


def test_paginate_empty_page_terminates():
    pager, calls = _pager([], page_limit=3)
    raw = bs.paginate(0, 10_000, 60_000, pager)
    assert raw == []
    assert len(calls) == 1  # one call, empty -> stop


def test_paginate_stops_without_forward_progress():
    # pathological pager that always returns the same single bar -> must not loop forever
    def stuck(start, end):  # noqa: ARG001
        return [[0, "1", "1", "1", "1", "1"]]

    raw = bs.paginate(0, 10 * 60_000, 60_000, stuck)
    assert len(raw) == 1


def test_fetch_bars_range_uses_pagination(monkeypatch):
    step = 60_000
    all_ts = [i * step for i in range(5)]

    def fake_page(symbol, interval, start_ms, end_ms, limit=1000, base_url=bs.BINANCE_API):  # noqa: ARG001
        return [[t, "10", "12", "9", "11", "100"] for t in all_ts if start_ms <= t <= end_ms][:3]

    monkeypatch.setattr(bs, "fetch_klines_page", fake_page)
    bars = bs.fetch_bars_range("BTCUSDT", "1m", 0, 4 * step)
    assert [b.ts for b in bars] == all_ts
    assert all(isinstance(b, Bar) for b in bars)
    assert bars[0].open == 10.0 and bars[0].close == 11.0

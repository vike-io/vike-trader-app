"""Qt-free watchlist/load helpers: freshness gate + quote derivation."""

import pytest

from vike_trader_app.core.model import Bar
from vike_trader_app.ui import watchlist_data as wd

STEP = 60_000
_DAY_MS = 86_400_000


def _bar(ts, close=100.0):
    return Bar(ts=ts, open=close, high=close + 1, low=close - 1, close=close, volume=1.0)


# --- is_stale (freshness gate for cache-first display) ---


def test_is_stale_none_or_empty_is_stale():
    # no cached tail -> must fetch before showing
    assert wd.is_stale(None, now_ms=10 * STEP, fresh_ms=5 * STEP) is True


def test_is_stale_recent_tail_is_fresh():
    # last bar within the freshness window -> serve cache instantly, no network
    assert wd.is_stale(8 * STEP, now_ms=10 * STEP, fresh_ms=5 * STEP) is False


def test_is_stale_old_tail_even_with_deep_history():
    # the bug being fixed: a deep cache whose newest bar is hours old is STILL stale.
    # depth must not mask a stale right edge (old `covers` check did exactly that).
    assert wd.is_stale(2 * STEP, now_ms=100 * STEP, fresh_ms=5 * STEP) is True


def test_is_stale_boundary_is_inclusive_fresh():
    # exactly at the window edge counts as fresh (not stale)
    assert wd.is_stale(5 * STEP, now_ms=10 * STEP, fresh_ms=5 * STEP) is False


# --- quote_from_bars (last close + 24h change) ---


def test_quote_from_bars_empty_is_none():
    assert wd.quote_from_bars([]) is None


def test_quote_from_bars_last_close_and_24h_change():
    # ref is the first bar at/after (last.ts - 24h); change is last/ref - 1
    bars = [_bar(0, close=100.0), _bar(_DAY_MS, close=110.0)]
    close, chg = wd.quote_from_bars(bars)
    assert close == 110.0
    assert chg == pytest.approx(0.10)  # 110/100 - 1


def test_quote_from_bars_zero_ref_close_is_safe():
    # a zero reference close must not divide-by-zero
    bars = [_bar(0, close=0.0), _bar(_DAY_MS, close=50.0)]
    close, chg = wd.quote_from_bars(bars)
    assert close == 50.0
    assert chg == 0.0

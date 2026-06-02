"""Parquet cache layer: merge, missing-range math, and cache-aware get_bars."""

from vike_trader_app.core.model import Bar
from vike_trader_app.data import cache
from vike_trader_app.data import parquet_source as ps
from vike_trader_app.data.binance_source import interval_ms

STEP = 60_000


def test_interval_ms_supports_8h_and_1w():
    # cache.get_bars steps by interval_ms; the vike source offers 8h and 1w, so the
    # step map must cover them for those timeframes to be cacheable.
    assert interval_ms("8h") == 8 * 3_600_000
    assert interval_ms("1w") == 7 * 86_400_000


def _bar(ts, close=100.0):
    return Bar(ts=ts, open=close, high=close + 1, low=close - 1, close=close, volume=1.0)


# --- merge_bars ---


def test_merge_dedups_by_ts_and_sorts():
    existing = [_bar(0), _bar(2 * STEP)]
    new = [_bar(STEP), _bar(2 * STEP, close=999.0)]  # 2*STEP overlaps
    merged = cache.merge_bars(existing, new)
    assert [b.ts for b in merged] == [0, STEP, 2 * STEP]
    assert merged[-1].close == 999.0  # new wins on conflict


def test_merge_empty_sides():
    assert cache.merge_bars([], [_bar(0)]) == [_bar(0)]
    assert cache.merge_bars([_bar(0)], []) == [_bar(0)]


# --- covered_range / slice ---


def test_covered_range():
    assert cache.covered_range([]) is None
    assert cache.covered_range([_bar(STEP), _bar(3 * STEP)]) == (STEP, 3 * STEP)


def test_slice_bars_inclusive():
    bars = [_bar(i * STEP) for i in range(5)]
    out = cache.slice_bars(bars, STEP, 3 * STEP)
    assert [b.ts for b in out] == [STEP, 2 * STEP, 3 * STEP]


# --- missing_ranges ---


def test_missing_no_cache_fetches_full():
    assert cache.missing_ranges(None, (0, 100), 10) == [(0, 100)]


def test_missing_fully_covered_is_empty():
    assert cache.missing_ranges((0, 100), (20, 80), 10) == []


def test_missing_before_only():
    assert cache.missing_ranges((50, 100), (0, 100), 10) == [(0, 40)]


def test_missing_after_only():
    assert cache.missing_ranges((0, 50), (0, 100), 10) == [(60, 100)]


def test_missing_both_ends():
    assert cache.missing_ranges((40, 60), (0, 100), 10) == [(0, 30), (70, 100)]


# --- get_bars (fake fetcher + tmp cache dir) ---


def _fake_fetcher():
    """Generates contiguous bars across [s, e] at STEP; records calls."""
    calls = []

    def fetcher(symbol, interval, s, e, progress=None):  # noqa: ARG001
        calls.append((s, e))
        first = (s // STEP) * STEP
        return [_bar(t) for t in range(first if first >= s else first + STEP, e + 1, STEP)]

    return fetcher, calls


def test_get_bars_fetches_then_caches(tmp_path):
    fetcher, calls = _fake_fetcher()
    root = str(tmp_path)
    bars = cache.get_bars("BTCUSDT", "1m", 0, 5 * STEP, root=root, fetcher=fetcher)
    assert [b.ts for b in bars] == [i * STEP for i in range(6)]
    assert len(calls) == 1  # fetched once
    # Phase 2b: append-only month partitions (ts 0..5*STEP all fall in 1970-01), not a single file.
    assert (tmp_path / "BTCUSDT" / "1m" / "1970-01.parquet").exists()
    assert not (tmp_path / "BTCUSDT" / "1m.parquet").exists()


def test_get_bars_second_call_hits_cache(tmp_path):
    fetcher, calls = _fake_fetcher()
    root = str(tmp_path)
    cache.get_bars("BTCUSDT", "1m", 0, 5 * STEP, root=root, fetcher=fetcher)
    calls.clear()
    bars = cache.get_bars("BTCUSDT", "1m", 0, 5 * STEP, root=root, fetcher=fetcher)
    assert [b.ts for b in bars] == [i * STEP for i in range(6)]
    assert calls == []  # served entirely from cache, no fetch


def test_get_bars_extends_only_the_gap(tmp_path):
    fetcher, calls = _fake_fetcher()
    root = str(tmp_path)
    cache.get_bars("BTCUSDT", "1m", 0, 5 * STEP, root=root, fetcher=fetcher)
    calls.clear()
    bars = cache.get_bars("BTCUSDT", "1m", 0, 10 * STEP, root=root, fetcher=fetcher)
    assert [b.ts for b in bars] == [i * STEP for i in range(11)]
    assert calls == [(6 * STEP, 10 * STEP)]  # only the missing tail was fetched


def test_get_bars_slices_subrange_from_cache(tmp_path):
    fetcher, calls = _fake_fetcher()
    root = str(tmp_path)
    cache.get_bars("BTCUSDT", "1m", 0, 10 * STEP, root=root, fetcher=fetcher)
    calls.clear()
    bars = cache.get_bars("BTCUSDT", "1m", 3 * STEP, 5 * STEP, root=root, fetcher=fetcher)
    assert [b.ts for b in bars] == [3 * STEP, 4 * STEP, 5 * STEP]
    assert calls == []


# --- repair_gaps (user-triggered interior gap-fill, once, with backoff) ---


def _noslp(_s):
    return None


def test_repair_gaps_fills_an_interior_hole(tmp_path):
    root = str(tmp_path)
    # 0,1, [hole 2,3], 4,5
    ps.append_series([_bar(0), _bar(STEP), _bar(4 * STEP), _bar(5 * STEP)], root, "BTCUSDT", "1m")

    def fetcher(symbol, interval, s, e, progress=None):  # noqa: ARG001
        return [_bar(t) for t in range(s, e + 1, STEP)]

    n = cache.repair_gaps("BTCUSDT", "1m", 0, 5 * STEP, root=root, fetcher=fetcher, sleep=_noslp)
    assert n == 2  # 2*STEP and 3*STEP
    assert [b.ts for b in ps.read_series(root, "BTCUSDT", "1m")] == [i * STEP for i in range(6)]


def test_repair_gaps_no_fetch_when_contiguous(tmp_path):
    root = str(tmp_path)
    ps.append_series([_bar(i * STEP) for i in range(4)], root, "X", "1m")
    calls = []
    cache.repair_gaps("X", "1m", 0, 3 * STEP, root=root,
                      fetcher=lambda *a, **k: (calls.append(1), [])[1], sleep=_noslp)
    assert calls == []  # no interior gaps -> no fetch


def test_repair_gaps_tolerates_empty_gap_fetch(tmp_path):
    # A legitimate closed-market gap: the source returns nothing; the hole stays, no crash.
    root = str(tmp_path)
    ps.append_series([_bar(0), _bar(STEP), _bar(4 * STEP), _bar(5 * STEP)], root, "X", "1m")
    n = cache.repair_gaps("X", "1m", 0, 5 * STEP, root=root, fetcher=lambda *a, **k: [], sleep=_noslp)
    assert n == 0


def test_repair_gaps_retries_a_transient_failure(tmp_path):
    root = str(tmp_path)
    ps.append_series([_bar(0), _bar(STEP), _bar(4 * STEP), _bar(5 * STEP)], root, "X", "1m")
    state = {"i": 0}

    def flaky(symbol, interval, s, e, progress=None):  # noqa: ARG001
        state["i"] += 1
        if state["i"] == 1:
            raise ConnectionError("transient")
        return [_bar(t) for t in range(s, e + 1, STEP)]

    n = cache.repair_gaps("X", "1m", 0, 5 * STEP, root=root, fetcher=flaky, sleep=_noslp)
    assert n == 2 and state["i"] == 2  # retried once, then succeeded

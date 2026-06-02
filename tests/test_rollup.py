"""Pin-to-precompute rollups (Phase 3): incremental, watermarked, idempotent materialization.

A "pinned" higher timeframe is materialised from the 1m base into its own partitioned series
so reads don't re-resample the base each time. ``refresh_rollup`` is incremental (recomputes
only from the watermark — the last materialised bucket, reopened in case it was partial) and
idempotent (re-running with no new base data leaves the rollup unchanged). ``rollup_refresh_start``
is the pure watermark→bucket-floor helper.
"""

from vike_trader_app.core.model import Bar
from vike_trader_app.data import parquet_source as ps
from vike_trader_app.data.rollup import refresh_rollup, rollup_refresh_start

HOUR = 3_600_000
MIN = 60_000


def _m1(n, base_ts=0):
    return [Bar(ts=base_ts + i * MIN, open=100, high=101, low=99, close=100 + i, volume=1.0)
            for i in range(n)]


# --- rollup_refresh_start (pure) -------------------------------------------

def test_refresh_start_none_recomputes_from_zero():
    assert rollup_refresh_start(None, HOUR) == 0


def test_refresh_start_floors_to_the_bucket_containing_the_watermark():
    assert rollup_refresh_start(HOUR + 5 * MIN, HOUR) == HOUR  # 01:05 -> reopen the 01:00 bucket


def test_refresh_start_on_boundary_reopens_that_bucket():
    assert rollup_refresh_start(2 * HOUR, HOUR) == 2 * HOUR


# --- refresh_rollup --------------------------------------------------------

def test_refresh_rollup_builds_higher_tf_from_base(tmp_path):
    root = str(tmp_path)
    ps.append_series(_m1(90), root, "X", "1m")  # 1.5h of 1m bars
    assert refresh_rollup(root, "X", "1h") == 2
    out = ps.read_series(root, "X", "1h")
    assert [b.ts for b in out] == [0, HOUR]          # one full hour + one partial
    assert (out[0].volume, out[1].volume) == (60.0, 30.0)


def test_refresh_rollup_reopens_and_completes_the_partial_last_bucket(tmp_path):
    root = str(tmp_path)
    ps.append_series(_m1(90), root, "X", "1m")
    refresh_rollup(root, "X", "1h")                  # 2nd hour partial (30 bars)
    ps.append_series(_m1(30, base_ts=90 * MIN), root, "X", "1m")  # fill the 2nd hour to 60
    refresh_rollup(root, "X", "1h")
    out = ps.read_series(root, "X", "1h")
    assert [b.ts for b in out] == [0, HOUR]
    assert out[1].volume == 60.0                     # reopened partial bucket, now complete
    assert out[0].volume == 60.0                     # earlier bucket preserved


def test_refresh_rollup_only_resamples_from_the_watermark(tmp_path, monkeypatch):
    import vike_trader_app.data.rollup as rollup

    root = str(tmp_path)
    ps.append_series(_m1(90), root, "X", "1m")
    refresh_rollup(root, "X", "1h")                  # watermark now at the 2nd-hour bucket
    ps.append_series(_m1(30, base_ts=90 * MIN), root, "X", "1m")
    sizes: list[int] = []
    orig = rollup.resample
    monkeypatch.setattr(rollup, "resample",
                        lambda bars, ms: (sizes.append(len(bars)), orig(bars, ms))[1])
    refresh_rollup(root, "X", "1h")
    assert sizes == [60]  # only the 2nd hour's base bars re-read, NOT all 120


def test_refresh_rollup_is_idempotent(tmp_path):
    root = str(tmp_path)
    ps.append_series(_m1(120), root, "X", "1m")      # exactly 2 full hours
    refresh_rollup(root, "X", "1h")
    before = ps.read_series(root, "X", "1h")
    refresh_rollup(root, "X", "1h")                  # no new base -> unchanged
    assert ps.read_series(root, "X", "1h") == before


def test_refresh_rollup_no_base_returns_zero(tmp_path):
    assert refresh_rollup(str(tmp_path), "X", "1h") == 0


def test_refresh_rollup_base_interval_is_noop(tmp_path):
    root = str(tmp_path)
    ps.append_series(_m1(10), root, "X", "1m")
    assert refresh_rollup(root, "X", "1m") == 0      # rolling base -> base does nothing

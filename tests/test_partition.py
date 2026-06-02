"""Pure month-partition helpers for the append-only Parquet base (Phase 2b).

``month_key`` maps an epoch-ms timestamp to its ``YYYY-MM`` partition (UTC); ``partition_by_month``
groups bars into per-month buckets (insertion order preserved) so a write only has to touch the
months that actually changed, instead of rewriting the whole series.
"""

from vike_trader_app.core.model import Bar
from vike_trader_app.data.partition import month_key, partition_by_month


def _bar(ts):
    return Bar(ts=ts, open=1, high=1, low=1, close=1, volume=1.0)


def test_month_key_epoch_is_1970_01():
    assert month_key(0) == "1970-01"


def test_month_key_uses_utc_calendar_month():
    # 1_700_000_000_000 ms = 2023-11-14T22:13:20Z
    assert month_key(1_700_000_000_000) == "2023-11"


def test_month_key_end_of_month_boundary():
    # 2024-01-31T23:59:59.999Z stays in 2024-01; +1ms rolls to 2024-02.
    jan_end = 1_706_745_599_999
    assert month_key(jan_end) == "2024-01"
    assert month_key(jan_end + 1) == "2024-02"


def test_partition_by_month_groups_and_preserves_order():
    nov = 1_700_000_000_000  # 2023-11
    dec = 1_701_500_000_000  # 2023-12
    bars = [_bar(nov), _bar(nov + 60_000), _bar(dec)]
    parts = partition_by_month(bars)
    assert set(parts) == {"2023-11", "2023-12"}
    assert [b.ts for b in parts["2023-11"]] == [nov, nov + 60_000]
    assert [b.ts for b in parts["2023-12"]] == [dec]


def test_partition_by_month_empty():
    assert partition_by_month([]) == {}

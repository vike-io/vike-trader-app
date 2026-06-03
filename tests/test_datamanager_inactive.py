"""inactive_candidates: which cached series are dead (0 bars) or stale (last data before a date)."""

from vike_trader_app.ui.datamanager_data import inactive_candidates


class _Info:
    def __init__(self, symbol, n_bars, end_ts, interval="1m"):
        self.symbol, self.interval, self.n_bars, self.end_ts, self.start_ts = symbol, interval, n_bars, end_ts, 0


def test_zero_bar_series_are_candidates():
    infos = [_Info("A", 0, 0), _Info("B", 100, 5_000)]
    assert inactive_candidates(infos) == [("A", "1m")]


def test_stale_series_included_only_when_date_given():
    infos = [_Info("A", 0, 0), _Info("B", 100, 1_000), _Info("C", 100, 9_000)]
    # no date -> only the 0-bar one
    assert inactive_candidates(infos) == [("A", "1m")]
    # with cutoff 5000 -> 0-bar A plus stale B (end_ts 1000 < 5000); C (9000) kept
    assert set(inactive_candidates(infos, last_before_ms=5_000)) == {("A", "1m"), ("B", "1m")}


def test_zero_bars_flag_can_be_disabled():
    infos = [_Info("A", 0, 0), _Info("B", 100, 1_000)]
    # only stale, ignore 0-bar
    assert inactive_candidates(infos, zero_bars=False, last_before_ms=5_000) == [("B", "1m")]

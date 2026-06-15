"""Characterization tests for the crypto REST sources' row->Bar transforms (bybit / coinbase /
kraken / okx). These pin the per-source column maps (which genuinely differ) BEFORE consolidating
them onto the shared data.rows.rows_to_bars helper, so the refactor can't silently swap a column."""

from vike_trader_app.data import bybit_source, coinbase_source, kraken_source, okx_source


def test_bybit_to_bars_ms_ts_and_columns():
    # [startMs, o, h, l, c, vol, turnover]
    rows = [["2000", "10", "12", "9", "11", "100", "x"],
            ["1000", "5", "6", "4", "5.5", "50", "x"]]
    bars = bybit_source.to_bars(rows)
    assert [b.ts for b in bars] == [1000, 2000]                      # ascending, ms unchanged
    b = bars[1]
    assert (b.open, b.high, b.low, b.close, b.volume) == (10, 12, 9, 11, 100)


def test_coinbase_to_bars_seconds_and_swapped_low_high_open():
    # [time_s, low, high, open, close, vol]  (note: low/high/open order)
    rows = [["2", "9", "12", "10", "11", "100"]]
    b = coinbase_source.to_bars(rows)[0]
    assert b.ts == 2000                                              # seconds -> ms
    assert (b.open, b.high, b.low, b.close, b.volume) == (10, 12, 9, 11, 100)


def test_kraken_to_bars_seconds_and_volume_at_col6():
    # [time_s, o, h, l, c, vwap, vol, count]
    rows = [["3", "10", "12", "9", "11", "10.5", "100", 5]]
    b = kraken_source.to_bars(rows)[0]
    assert b.ts == 3000
    assert (b.open, b.high, b.low, b.close, b.volume) == (10, 12, 9, 11, 100)


def test_okx_to_bars_ms_and_newest_first_sorted_ascending():
    # [ts, o, h, l, c, vol, ...] newest-first; to_bars must sort ascending
    rows = [["4000", "10", "12", "9", "11", "100", "x"],
            ["3000", "5", "6", "4", "5.5", "50", "x"]]
    bars = okx_source.to_bars(rows)
    assert [b.ts for b in bars] == [3000, 4000]
    b = bars[1]
    assert (b.open, b.high, b.low, b.close, b.volume) == (10, 12, 9, 11, 100)

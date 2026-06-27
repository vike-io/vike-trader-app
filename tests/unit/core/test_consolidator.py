from vike_trader_app.core.ticks import QuoteTick, TradeTick
from vike_trader_app.core.consolidator import consolidate_quotes, consolidate_trades


def test_consolidate_quotes_opening_bidask_and_mid_ohlc():
    ticks = [
        QuoteTick(ts=0, bid=1.0, ask=1.2),     # mid 1.1  (opening quote of bucket)
        QuoteTick(ts=10, bid=1.2, ask=1.4),    # mid 1.3
        QuoteTick(ts=20, bid=0.8, ask=1.0),    # mid 0.9
    ]
    bars = consolidate_quotes(ticks, step_ms=60)
    assert len(bars) == 1
    b = bars[0]
    assert b.ts == 0
    assert abs(b.open - 1.1) < 1e-9
    assert abs(b.high - 1.3) < 1e-9
    assert abs(b.low - 0.9) < 1e-9
    assert abs(b.close - 0.9) < 1e-9
    assert b.bid == 1.0 and b.ask == 1.2  # opening quote, not last
    assert b.volume == 3.0  # tick count


def test_consolidate_quotes_buckets_by_step():
    ticks = [QuoteTick(ts=10, bid=1, ask=1), QuoteTick(ts=70, bid=2, ask=2)]
    bars = consolidate_quotes(ticks, step_ms=60)
    assert [b.ts for b in bars] == [0, 60]


def test_consolidate_trades_volume_is_size_sum_no_quote():
    ticks = [TradeTick(ts=0, price=50.0, size=0.1), TradeTick(ts=5, price=51.0, size=0.4)]
    bars = consolidate_trades(ticks, step_ms=60)
    assert len(bars) == 1
    b = bars[0]
    assert (b.open, b.high, b.low, b.close) == (50.0, 51.0, 50.0, 51.0)
    assert b.volume == 0.5
    assert b.bid is None and b.ask is None

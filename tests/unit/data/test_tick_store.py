from vike_trader_app.core.ticks import QuoteTick, TradeTick
from vike_trader_app.data import tick_store


def test_quote_roundtrip_and_range(tmp_path):
    root = str(tmp_path)
    ticks = [QuoteTick(ts=t, bid=1.0, ask=1.1) for t in (1_000, 2_000, 90_000_000)]
    tick_store.write_quotes(ticks, root, "EURUSD")
    got = tick_store.read_quotes(root, "EURUSD", 0, 3_000)
    assert [q.ts for q in got] == [1_000, 2_000]  # 90_000_000 (next day) excluded by range


def test_trade_roundtrip_keeps_same_ts_duplicates(tmp_path):
    root = str(tmp_path)
    ticks = [TradeTick(ts=1_000, price=50.0, size=0.1),
             TradeTick(ts=1_000, price=50.5, size=0.2)]
    tick_store.write_trades(ticks, root, "BTCUSDT")
    got = tick_store.read_trades(root, "BTCUSDT", 0, 2_000)
    assert len(got) == 2  # distinct trades at the same ms are not deduped


def test_rewrite_is_idempotent(tmp_path):
    root = str(tmp_path)
    ticks = [QuoteTick(ts=1_000, bid=1.0, ask=1.1)]
    tick_store.write_quotes(ticks, root, "EURUSD")
    tick_store.write_quotes(ticks, root, "EURUSD")  # re-fetch same day
    assert len(tick_store.read_quotes(root, "EURUSD", 0, 2_000)) == 1

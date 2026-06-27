from vike_trader_app.data import binance_ticks
from vike_trader_app.data import tick_store


def test_rows_to_trade_ticks_parses_aggtrades():
    rows = [{"a": 1, "p": "50000.5", "q": "0.2", "T": 1_000, "m": True},
            {"a": 2, "p": "50001.0", "q": "0.1", "T": 1_500, "m": False}]
    ticks = binance_ticks.rows_to_trade_ticks(rows)
    assert [(t.ts, t.price, t.size, t.is_buyer_maker) for t in ticks] == [
        (1_000, 50000.5, 0.2, True),
        (1_500, 50001.0, 0.1, False),
    ]


def test_cache_trades_range_persists(tmp_path):
    rows = [{"a": 1, "p": "50000.0", "q": "0.3", "T": 1_000, "m": False}]

    def fake_fetch(symbol, start_ms, end_ms):
        assert symbol == "BTCUSDT"
        return rows

    n = binance_ticks.cache_trades_range("BTCUSDT", 0, 2_000, str(tmp_path), fetch=fake_fetch)
    assert n == 1
    got = tick_store.read_trades(str(tmp_path), "BTCUSDT", 0, 2_000)
    assert got[0].price == 50000.0 and got[0].size == 0.3

from vike_trader_app.core.ticks import QuoteTick, TradeTick


def test_quote_tick_mid_and_fields():
    q = QuoteTick(ts=1000, bid=1.0, ask=1.2, bid_size=5.0, ask_size=3.0)
    assert q.ts == 1000 and q.bid == 1.0 and q.ask == 1.2
    assert q.mid == 1.1


def test_trade_tick_fields_and_defaults():
    t = TradeTick(ts=2000, price=50.0, size=0.5)
    assert t.ts == 2000 and t.price == 50.0 and t.size == 0.5
    assert t.is_buyer_maker is False

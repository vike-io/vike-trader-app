from datetime import datetime, timezone

from vike_trader_app.data.options.polygon import build_chain_from_snapshot


def _ms(y, m, d, h=8):
    return int(datetime(y, m, d, h, tzinfo=timezone.utc).timestamp() * 1000)


def _snapshot():
    # Polygon /v3/snapshot/options/{underlying} result shape: one expiry (02 Jul), two strikes;
    # 460 has call+put, 470 has only a call. Greeks/IV come straight from Polygon.
    ua = {"price": 460.52, "ticker": "MSFT"}
    return [
        {"details": {"contract_type": "call", "expiration_date": "2026-07-02", "strike_price": 460,
                     "ticker": "O:MSFT260702C00460000"},
         "last_quote": {"bid": 6.1, "ask": 6.3, "midpoint": 6.2}, "last_trade": {"price": 6.25},
         "implied_volatility": 0.234,
         "greeks": {"delta": 0.52, "gamma": 0.01, "theta": -0.05, "vega": 0.3},
         "open_interest": 610, "day": {"volume": 8963}, "underlying_asset": ua},
        {"details": {"contract_type": "put", "expiration_date": "2026-07-02", "strike_price": 460,
                     "ticker": "O:MSFT260702P00460000"},
         "last_quote": {"bid": 5.4, "ask": 5.6, "midpoint": 5.5}, "last_trade": {"price": 5.5},
         "implied_volatility": 0.241,
         "greeks": {"delta": -0.48, "gamma": 0.01, "theta": -0.04, "vega": 0.3},
         "open_interest": 488, "day": {"volume": 3201}, "underlying_asset": ua},
        {"details": {"contract_type": "call", "expiration_date": "2026-07-02", "strike_price": 470,
                     "ticker": "O:MSFT260702C00470000"},
         "last_quote": {"bid": 1.2, "ask": 1.4, "midpoint": 1.3}, "implied_volatility": 0.255,
         "greeks": {"delta": 0.21}, "open_interest": 73, "day": {"volume": 410},
         "underlying_asset": ua},
        # a different expiry must be excluded
        {"details": {"contract_type": "call", "expiration_date": "2026-09-18", "strike_price": 460,
                     "ticker": "O:MSFT260918C00460000"},
         "last_quote": {"bid": 12.0, "ask": 12.4, "midpoint": 12.2}, "implied_volatility": 0.28,
         "greeks": {"delta": 0.55}, "open_interest": 5, "underlying_asset": ua},
    ]


def test_build_chain_from_snapshot():
    chain = build_chain_from_snapshot("MSFT", _snapshot(), "2026-07-02", _ms(2026, 6, 2))
    assert chain.source == "polygon" and chain.asset_class == "equity"
    assert chain.underlying_price == 460.52
    assert [r.strike for r in chain.rows] == [460.0, 470.0]  # 18 Sep row excluded
    c = chain.rows[0].call
    assert c.bid == 6.1 and c.ask == 6.3 and c.mark == 6.2 and c.last == 6.25
    assert c.iv == 0.234 and c.delta == 0.52 and c.gamma == 0.01     # greeks straight from Polygon
    assert c.open_interest == 610 and c.volume == 8963
    assert c.in_the_money is True                                     # spot 460.52 > 460 call
    assert chain.rows[0].put.delta == -0.48 and chain.rows[0].put.in_the_money is False
    assert chain.rows[1].put is None                                  # only a call at 470
    # missing greeks coerce to None, not a crash
    assert chain.rows[1].call.theta is None and chain.rows[1].call.delta == 0.21

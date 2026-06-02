from datetime import datetime, timezone

from vike_trader_app.data.options.marketdata import build_chain_from_payload


def _ms(y, m, d, h=8):
    return int(datetime(y, m, d, h, tzinfo=timezone.utc).timestamp() * 1000)


def _payload():
    # marketdata.app columnar chain shape: parallel arrays. One expiry, two strikes;
    # 460 has call+put, 470 has only a call.
    return {
        "s": "ok",
        "optionSymbol": ["MSFT260702C00460000", "MSFT260702P00460000", "MSFT260702C00470000"],
        "side": ["call", "put", "call"],
        "strike": [460, 460, 470],
        "bid": [6.1, 5.4, 1.2],
        "ask": [6.3, 5.6, 1.4],
        "mid": [6.2, 5.5, 1.3],
        "last": [6.25, 5.5, 1.25],
        "iv": [0.234, 0.241, 0.255],
        "delta": [0.52, -0.48, 0.21],
        "gamma": [0.01, 0.01, 0.008],
        "theta": [-0.05, -0.04, -0.03],
        "vega": [0.3, 0.3, 0.2],
        "openInterest": [610, 488, 73],
        "volume": [8963, 3201, 410],
        "inTheMoney": [True, False, False],
        "underlyingPrice": [460.52, 460.52, 460.52],
    }


def test_build_chain_from_payload():
    chain = build_chain_from_payload("MSFT", _payload(), "2026-07-02", _ms(2026, 6, 2))
    assert chain.source == "marketdata" and chain.asset_class == "equity"
    assert chain.underlying_price == 460.52
    assert [r.strike for r in chain.rows] == [460.0, 470.0]
    c = chain.rows[0].call
    assert c.bid == 6.1 and c.ask == 6.3 and c.mark == 6.2 and c.last == 6.25
    assert c.iv == 0.234 and c.delta == 0.52 and c.gamma == 0.01      # greeks straight from feed
    assert c.open_interest == 610 and c.volume == 8963 and c.in_the_money is True
    assert chain.rows[0].put.delta == -0.48 and chain.rows[0].put.in_the_money is False
    assert chain.rows[1].put is None                                  # only a call at 470


def test_build_chain_handles_empty_payload():
    chain = build_chain_from_payload("MSFT", {"s": "no_data"}, "2026-07-02", _ms(2026, 6, 2))
    assert chain.rows == () and chain.underlying_price is None

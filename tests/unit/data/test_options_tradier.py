from datetime import datetime, timezone

import pytest

from vike_trader_app.data.options.tradier import build_chain_from_options


def _ms(y, m, d, h=8):
    return int(datetime(y, m, d, h, tzinfo=timezone.utc).timestamp() * 1000)


def _options():
    # Tradier options.option[] shape: one expiry, two strikes; 460 call+put, 470 call only.
    return [
        {"option_type": "call", "strike": 460, "bid": 6.1, "ask": 6.3, "last": 6.25,
         "volume": 8963, "open_interest": 610,
         "greeks": {"mid_iv": 0.234, "delta": 0.52, "gamma": 0.01, "theta": -0.05, "vega": 0.3}},
        {"option_type": "put", "strike": 460, "bid": 5.4, "ask": 5.6, "last": 5.5,
         "volume": 3201, "open_interest": 488,
         "greeks": {"mid_iv": 0.241, "delta": -0.48, "gamma": 0.01, "theta": -0.04, "vega": 0.3}},
        {"option_type": "call", "strike": 470, "bid": 1.2, "ask": 1.4, "last": 1.25,
         "volume": 410, "open_interest": 73, "greeks": {"mid_iv": 0.255, "delta": 0.21}},
    ]


def test_build_chain_from_options():
    chain = build_chain_from_options("MSFT", _options(), "2026-07-02", _ms(2026, 6, 2), spot=460.52)
    assert chain.source == "tradier" and chain.asset_class == "equity"
    assert chain.underlying_price == 460.52
    assert [r.strike for r in chain.rows] == [460.0, 470.0]
    c = chain.rows[0].call
    assert c.bid == 6.1 and c.ask == 6.3 and c.last == 6.25
    assert c.mark == pytest.approx(6.2)   # computed mid-price (fp)
    assert c.iv == 0.234 and c.delta == 0.52 and c.gamma == 0.01   # greeks straight from Tradier
    assert c.open_interest == 610 and c.volume == 8963 and c.in_the_money is True
    assert chain.rows[0].put.delta == -0.48 and chain.rows[0].put.in_the_money is False
    assert chain.rows[1].put is None                                # only a call at 470
    assert chain.rows[1].call.theta is None and chain.rows[1].call.delta == 0.21  # partial greeks ok


def test_build_chain_without_spot():
    chain = build_chain_from_options("MSFT", _options(), "2026-07-02", _ms(2026, 6, 2), spot=None)
    assert chain.underlying_price is None
    assert chain.rows[0].call.in_the_money is None  # ITM unknown without spot

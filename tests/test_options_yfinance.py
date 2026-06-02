from datetime import datetime, timezone

import pytest

from vike_trader_app.data.options.yfinance import build_chain_from_records


def _ms(y, m, d, h=8):
    return int(datetime(y, m, d, h, tzinfo=timezone.utc).timestamp() * 1000)


def test_build_chain_from_records():
    calls = [
        {"strike": 20.0, "bid": 1.5, "ask": 1.7, "lastPrice": 1.6,
         "impliedVolatility": 0.55, "openInterest": 200, "volume": 50, "inTheMoney": True},
        {"strike": 25.0, "bid": 0.4, "ask": 0.5, "lastPrice": 0.45,
         "impliedVolatility": 0.62, "openInterest": 80, "volume": 10, "inTheMoney": False},
    ]
    puts = [
        {"strike": 20.0, "bid": 0.3, "ask": 0.4, "lastPrice": 0.35,
         "impliedVolatility": 0.58, "openInterest": 150, "volume": 20, "inTheMoney": False},
    ]
    chain = build_chain_from_records("^VIX", "2026-07-02", calls, puts, 22.5, _ms(2026, 6, 2))
    assert chain.source == "yfinance" and chain.asset_class == "equity"
    assert chain.underlying_price == 22.5
    assert [r.strike for r in chain.rows] == [20.0, 25.0]
    r0 = chain.rows[0]
    assert r0.call.iv == 0.55 and r0.call.in_the_money is True
    assert r0.call.delta is not None              # greeks enriched
    assert r0.put.bid == 0.3
    assert chain.rows[1].put is None              # no 25 put


def test_build_chain_without_spot_leaves_greeks_none():
    # v1 degradation contract: no underlying price -> greeks can't be computed, chain still builds
    calls = [{"strike": 100.0, "bid": 1.0, "ask": 1.1, "lastPrice": 1.05,
              "impliedVolatility": 0.30, "openInterest": 10, "volume": 5, "inTheMoney": False}]
    chain = build_chain_from_records("SPY", "2026-07-02", calls, [], None, _ms(2026, 6, 2))
    assert chain.underlying_price is None
    assert chain.rows[0].call.iv == 0.30          # raw fields still populated
    assert chain.rows[0].call.delta is None       # but greeks stay None


def test_degenerate_iv_inferred_from_price_and_mark_uses_last():
    # Yahoo after-hours: bid/ask 0 + stale ~0 IV, but a real lastPrice. We use last as the
    # mark and infer IV from it so greeks are meaningful (ATM, ~1y -> price 7.97 => iv ~0.20).
    calls = [{"strike": 100.0, "bid": 0.0, "ask": 0.0, "lastPrice": 7.97,
              "impliedVolatility": 0.02, "openInterest": 10, "volume": 5, "inTheMoney": False}]
    chain = build_chain_from_records("MSFT", "2027-06-02", calls, [], 100.0, _ms(2026, 6, 2))
    q = chain.rows[0].call
    assert q.mark == 7.97                          # bid/ask are 0 -> fall back to last trade
    assert q.iv == pytest.approx(0.20, abs=0.02)   # inferred from the price, not Yahoo's 1e-5
    assert q.delta is not None                     # greeks now meaningful

from datetime import datetime, timezone

from vike_trader_app.data.options.deribit import (
    build_chain_from_summary, list_expiries_from_summary, parse_instrument_name,
)


def _ms(y, m, d, h=8):
    return int(datetime(y, m, d, h, tzinfo=timezone.utc).timestamp() * 1000)


def _summary():
    # two expiries (27 Jun + 25 Sep 2026); within 27 Jun: two strikes, call+put on the first
    return [
        {"instrument_name": "BTC-27JUN26-100000-C", "bid_price": 0.05, "ask_price": 0.06,
         "mark_price": 0.055, "mark_iv": 62.5, "open_interest": 120.0, "volume": 8.0,
         "underlying_price": 104000.0},
        {"instrument_name": "BTC-27JUN26-100000-P", "bid_price": 0.04, "ask_price": 0.05,
         "mark_price": 0.045, "mark_iv": 61.0, "open_interest": 90.0, "volume": 3.0,
         "underlying_price": 104000.0},
        {"instrument_name": "BTC-27JUN26-110000-C", "bid_price": 0.02, "ask_price": 0.03,
         "mark_price": 0.025, "mark_iv": 64.0, "open_interest": 50.0, "volume": 1.0,
         "underlying_price": 104000.0},
        # different expiry + distinct strike: must be excluded when building the 27 Jun chain
        {"instrument_name": "BTC-25SEP26-120000-C", "bid_price": 0.01, "ask_price": 0.02,
         "mark_price": 0.015, "mark_iv": 70.0, "open_interest": 10.0, "volume": 1.0,
         "underlying_price": 104000.0},
        {"instrument_name": "BTC-PERPETUAL", "mark_price": 104000.0},  # non-option, ignored
    ]


def test_parse_instrument_name():
    assert parse_instrument_name("BTC-27JUN26-100000-C") == ("BTC", "2026-06-27", 100000.0, "C")
    assert parse_instrument_name("BTC-PERPETUAL") is None
    assert parse_instrument_name("BTC-27JUN26-100000-Z") is None


def test_list_expiries_from_summary():
    exps = list_expiries_from_summary(_summary(), _ms(2026, 6, 2))
    assert [e.date for e in exps] == ["2026-06-27", "2026-09-25"]  # distinct, ascending
    assert exps[0].dte == 25


def test_build_chain_from_summary_groups_and_enriches():
    chain = build_chain_from_summary("BTC", _summary(), "2026-06-27", _ms(2026, 6, 2))
    assert chain.source == "deribit" and chain.asset_class == "crypto"
    assert chain.underlying_price == 104000.0
    assert len(chain.rows) == 2  # the 25 Sep / 120000 row is filtered out
    assert [r.strike for r in chain.rows] == [100000.0, 110000.0]
    row = chain.rows[0]
    assert row.call.iv == 0.625 and row.put.iv == 0.61        # mark_iv % -> decimal
    # Deribit premiums are coin units, scaled to USD by underlying_price (0.05 * 104000)
    assert row.call.bid == 0.05 * 104000.0 and row.call.mark == 0.055 * 104000.0
    assert row.call.delta is not None                          # greeks enriched from IV
    assert chain.rows[1].put is None                           # only a call at 110000

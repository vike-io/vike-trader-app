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
    # USDC-margined altcoin form: base carries a _USDC suffix, dropped so base normalizes to SOL
    assert parse_instrument_name("SOL_USDC-26JUN26-90-P") == ("SOL", "2026-06-26", 90.0, "P")
    assert parse_instrument_name("SOL_USDC-5JUN26-80-C") == ("SOL", "2026-06-05", 80.0, "C")


def _usdc_summary():
    # The shared USDC book mixes coins; SOL's chain must pick out only SOL_USDC rows.
    return [
        {"instrument_name": "SOL_USDC-26JUN26-90-P", "bid_price": 17.5, "ask_price": 18.0,
         "mark_price": 17.75, "mark_iv": 60.0, "open_interest": 40.0, "volume": 5.0,
         "underlying_price": 74.5},
        {"instrument_name": "SOL_USDC-26JUN26-90-C", "bid_price": 1.0, "ask_price": 1.2,
         "mark_price": 1.1, "mark_iv": 61.0, "open_interest": 30.0, "volume": 2.0,
         "underlying_price": 74.5},
        # other coins in the same book — excluded from SOL's expiries + chain
        {"instrument_name": "BTC_USDC-31JUL26-115000-P", "bid_price": 5000.0, "ask_price": 5100.0,
         "mark_price": 5050.0, "mark_iv": 55.0, "underlying_price": 104000.0},
        {"instrument_name": "XRP_USDC-26JUN26-3-C", "bid_price": 0.1, "ask_price": 0.12,
         "mark_price": 0.11, "mark_iv": 70.0, "underlying_price": 2.4},
    ]


def test_list_expiries_filters_to_requested_coin_in_shared_book():
    exps = list_expiries_from_summary(_usdc_summary(), _ms(2026, 6, 2), "SOL")
    assert [e.date for e in exps] == ["2026-06-26"]  # only SOL, not the BTC 31 Jul row


def test_build_sol_chain_from_usdc_book_unscaled_premiums():
    # usd_quoted=True: USDC premiums are already USD -> NOT scaled by the ~74.5 underlying
    chain = build_chain_from_summary(
        "SOL", _usdc_summary(), "2026-06-26", _ms(2026, 6, 2), usd_quoted=True)
    assert chain.underlying == "SOL" and chain.underlying_price == 74.5
    assert [r.strike for r in chain.rows] == [90.0]   # BTC/XRP rows excluded
    assert chain.rows[0].put.bid == 17.5              # passed through, not 17.5 * 74.5
    assert chain.rows[0].call.mark == 1.1


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

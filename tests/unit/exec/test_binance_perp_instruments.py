"""parse_binance_perp_instruments: fapi /fapi/v1/exchangeInfo -> RiskLimits-shaped filters + base_asset.

Binance USDⓈ-M (perpetual) instruments: qty is in BASE asset (NOT contracts).
Uses MARKET_LOT_SIZE filter for market-order quantity caps (prefer over LOT_SIZE.maxQty).
"""

from __future__ import annotations

from vike_trader_app.exec.binance.perp_instruments import parse_binance_perp_instruments


_PAYLOAD = {"symbols": [{
    "symbol": "BTCUSDT", "contractType": "PERPETUAL",
    "baseAsset": "BTC", "quoteAsset": "USDT",
    "pricePrecision": 2, "quantityPrecision": 3,
    "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.10", "minPrice": "0", "maxPrice": "0"},
        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000"},
        {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "120"},
        {"filterType": "MIN_NOTIONAL", "notional": "100"},
    ],
}]}


def test_parses_market_lot_cap_and_base_qty():
    """MARKET_LOT_SIZE.maxQty for market orders; base qty (no ct_val)."""
    out = parse_binance_perp_instruments(_PAYLOAD)
    f = out["BTCUSDT"]
    assert f == {
        "tick_size": 0.10, "step_size": 0.001, "min_qty": 0.001,
        "max_qty": 120.0,            # MARKET_LOT_SIZE.maxQty, NOT 1000 (LOT_SIZE)
        "min_notional": 100.0, "base_asset": "BTC",
    }
    assert "ct_val" not in f          # base qty — no contracts


def test_missing_filters_default_zero():
    """Missing filters coerce to 0.0."""
    out = parse_binance_perp_instruments({"symbols": [{"symbol": "ETHUSDT", "baseAsset": "ETH", "filters": []}]})
    f = out["ETHUSDT"]
    assert f["tick_size"] == 0.0 and f["step_size"] == 0.0 and f["max_qty"] == 0.0
    assert f["base_asset"] == "ETH"


def test_empty_symbol_skipped():
    """Empty symbol string skipped."""
    assert parse_binance_perp_instruments({"symbols": [{"symbol": "", "filters": []}]}) == {}


def test_all_filters_present():
    """Parse all filters when present."""
    payload = {"symbols": [{
        "symbol": "ETHUSDT", "baseAsset": "ETH",
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            {"filterType": "LOT_SIZE", "stepSize": "0.01", "minQty": "0.01", "maxQty": "100000"},
            {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.01", "minQty": "0.01", "maxQty": "50000"},
            {"filterType": "MIN_NOTIONAL", "notional": "10"},
        ],
    }]}
    out = parse_binance_perp_instruments(payload)["ETHUSDT"]
    assert out["tick_size"] == 0.01
    assert out["step_size"] == 0.01
    assert out["min_qty"] == 0.01
    assert out["max_qty"] == 50000.0
    assert out["min_notional"] == 10.0
    assert out["base_asset"] == "ETH"


def test_empty_payload():
    """Empty payload returns empty dict."""
    assert parse_binance_perp_instruments({"symbols": []}) == {}
    assert parse_binance_perp_instruments({}) == {}


def test_no_market_lot_size_falls_back_to_zero():
    """Without MARKET_LOT_SIZE filter, max_qty defaults to 0.0."""
    payload = {"symbols": [{
        "symbol": "SOLUSDT", "baseAsset": "SOL",
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            {"filterType": "LOT_SIZE", "stepSize": "0.1", "minQty": "0.1", "maxQty": "10000"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
        ],
    }]}
    out = parse_binance_perp_instruments(payload)["SOLUSDT"]
    assert out["max_qty"] == 0.0     # no MARKET_LOT_SIZE


def test_coerces_string_numbers():
    """String numbers coerce to float; invalid/None -> 0.0."""
    payload = {"symbols": [{
        "symbol": "BTCUSDT", "baseAsset": "BTC",
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "123.456"},
            {"filterType": "LOT_SIZE", "stepSize": "0", "minQty": None, "maxQty": "1000"},
            {"filterType": "MARKET_LOT_SIZE", "maxQty": "invalid"},
            {"filterType": "MIN_NOTIONAL", "notional": ""},
        ],
    }]}
    out = parse_binance_perp_instruments(payload)["BTCUSDT"]
    assert out["tick_size"] == 123.456
    assert out["step_size"] == 0.0
    assert out["min_qty"] == 0.0
    assert out["max_qty"] == 0.0     # 'invalid' -> 0.0
    assert out["min_notional"] == 0.0  # '' -> 0.0


def test_uppercase_symbol():
    """Symbols are uppercased."""
    payload = {"symbols": [{
        "symbol": "btcusdt", "baseAsset": "BTC",
        "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.1"}],
    }]}
    out = parse_binance_perp_instruments(payload)
    assert "BTCUSDT" in out
    assert "btcusdt" not in out

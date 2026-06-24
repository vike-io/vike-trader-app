"""parse_bybit_perp_instruments: result.list[] -> RiskLimits-shaped filters + base_asset.

Linear perps use qtyStep (not basePrecision) and minNotionalValue (not minOrderAmt).
"""

from __future__ import annotations

from vike_trader_app.exec.bybit.perp_instruments import parse_bybit_perp_instruments


def test_parse_uses_qtystep_and_min_notional_value():
    payload = {"result": {"list": [{
        "symbol": "BTCUSDT", "baseCoin": "BTC",
        "priceFilter": {"tickSize": "0.1"},
        "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001",
                          "maxOrderQty": "1190", "minNotionalValue": "5"}}]}}
    out = parse_bybit_perp_instruments(payload)["BTCUSDT"]
    assert out["tick_size"] == 0.1
    assert out["step_size"] == 0.001        # from qtyStep
    assert out["min_notional"] == 5.0       # from minNotionalValue
    assert out["base_asset"] == "BTC"


def test_parse_all_fields():
    payload = {"result": {"list": [{
        "symbol": "ETHUSDT", "baseCoin": "ETH",
        "priceFilter": {"tickSize": "0.01"},
        "lotSizeFilter": {"qtyStep": "0.01", "minOrderQty": "0.01",
                          "maxOrderQty": "5000", "minNotionalValue": "10"}}]}}
    out = parse_bybit_perp_instruments(payload)["ETHUSDT"]
    assert out["tick_size"] == 0.01
    assert out["step_size"] == 0.01
    assert out["min_qty"] == 0.01
    assert out["max_qty"] == 5000.0
    assert out["min_notional"] == 10.0
    assert out["base_asset"] == "ETH"


def test_missing_fields_default_to_zero():
    payload = {"result": {"list": [{"symbol": "SOLUSDT", "baseCoin": "SOL",
                                    "priceFilter": {}, "lotSizeFilter": {}}]}}
    out = parse_bybit_perp_instruments(payload)["SOLUSDT"]
    assert out["tick_size"] == 0.0
    assert out["step_size"] == 0.0
    assert out["min_notional"] == 0.0
    assert out["base_asset"] == "SOL"


def test_empty_payload_returns_empty_dict():
    assert parse_bybit_perp_instruments({"result": {"list": []}}) == {}
    assert parse_bybit_perp_instruments({}) == {}


def test_does_not_use_spot_keys():
    """Ensure basePrecision and minOrderAmt (spot keys) are NOT read — only qtyStep and minNotionalValue."""
    payload = {"result": {"list": [{
        "symbol": "BTCUSDT", "baseCoin": "BTC",
        "priceFilter": {"tickSize": "0.1"},
        "lotSizeFilter": {
            # spot keys present but should be ignored
            "basePrecision": "9999",
            "minOrderAmt": "9999",
            # perp keys
            "qtyStep": "0.001",
            "minNotionalValue": "5",
        }}]}}
    out = parse_bybit_perp_instruments(payload)["BTCUSDT"]
    assert out["step_size"] == 0.001   # reads qtyStep, NOT basePrecision=9999
    assert out["min_notional"] == 5.0  # reads minNotionalValue, NOT minOrderAmt=9999

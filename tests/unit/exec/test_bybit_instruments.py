"""parse_bybit_instruments_info: result.list[] -> RiskLimits-shaped filters + base asset."""

from vike_trader_app.exec.bybit.instruments import parse_bybit_instruments_info

_PAYLOAD = {
    "retCode": 0,
    "result": {
        "category": "spot",
        "list": [
            {
                "symbol": "BTCUSDT",
                "baseCoin": "BTC",
                "quoteCoin": "USDT",
                "priceFilter": {"tickSize": "0.01"},
                "lotSizeFilter": {
                    "basePrecision": "0.000001",
                    "minOrderQty": "0.000048",
                    "maxOrderQty": "71.73956243",
                    "minOrderAmt": "1",
                    "maxOrderAmt": "4000000",
                },
            }
        ],
    },
}


def test_parses_btcusdt_filters():
    out = parse_bybit_instruments_info(_PAYLOAD)
    assert "BTCUSDT" in out
    f = out["BTCUSDT"]
    assert f["tick_size"] == 0.01
    assert f["step_size"] == 0.000001
    assert f["min_qty"] == 0.000048
    assert f["max_qty"] == 71.73956243
    assert f["min_notional"] == 1.0
    assert f["base_asset"] == "BTC"


def test_missing_fields_default_to_zero():
    payload = {"result": {"list": [{"symbol": "ETHUSDT", "baseCoin": "ETH",
                                    "priceFilter": {}, "lotSizeFilter": {}}]}}
    f = parse_bybit_instruments_info(payload)["ETHUSDT"]
    assert f["tick_size"] == 0.0
    assert f["step_size"] == 0.0
    assert f["min_notional"] == 0.0
    assert f["base_asset"] == "ETH"


def test_empty_payload_returns_empty_dict():
    assert parse_bybit_instruments_info({"result": {"list": []}}) == {}
    assert parse_bybit_instruments_info({}) == {}

"""parse_symbol_filters keeps NOTIONAL + LOT_SIZE bounds without regressing parse_exchange_info."""

from vike_trader_app.data.instrument_db import parse_exchange_info, parse_symbol_filters

_PAYLOAD = {
    "symbols": [
        {
            "symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT",
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                {"filterType": "LOT_SIZE", "stepSize": "0.00001", "minQty": "0.00001", "maxQty": "9000.0"},
                {"filterType": "NOTIONAL", "minNotional": "5.0"},
            ],
        }
    ]
}


def test_parse_symbol_filters_keeps_bounds():
    filters = parse_symbol_filters(_PAYLOAD)
    f = filters["BTCUSDT"]
    assert f["tick_size"] == 0.01
    assert f["step_size"] == 0.00001
    assert f["min_qty"] == 0.00001
    assert f["max_qty"] == 9000.0
    assert f["min_notional"] == 5.0


def test_min_notional_filter_fallback_name():
    payload = {"symbols": [{"symbol": "ETHUSDT", "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000"},
        {"filterType": "MIN_NOTIONAL", "minNotional": "10.0"},  # older filter name
    ]}]}
    assert parse_symbol_filters(payload)["ETHUSDT"]["min_notional"] == 10.0


def test_parse_exchange_info_unchanged():
    specs = parse_exchange_info(_PAYLOAD)
    assert len(specs) == 1
    assert specs[0].symbol == "BTCUSDT"
    assert specs[0].tick_size == 0.01
    assert specs[0].volume_step == 0.00001

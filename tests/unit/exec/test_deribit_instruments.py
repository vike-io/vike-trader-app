"""parse_deribit_option_instruments: public/get_instruments option rows -> RiskLimits filters dict.
Reuses data/options/deribit.py parse_instrument_name (one shared regex, no forked parser)."""
from vike_trader_app.exec.deribit.instruments import parse_deribit_option_instruments

_PAYLOAD = {
    "jsonrpc": "2.0",
    "result": [
        {"instrument_name": "BTC-27JUN25-60000-C", "kind": "option", "contract_size": 1.0,
         "tick_size": 0.0001, "min_trade_amount": 0.1},
        {"instrument_name": "SOL_USDC-26JUN26-90-P", "kind": "option", "contract_size": 10.0,
         "tick_size": 0.1, "min_trade_amount": 10.0},
        {"instrument_name": "BTC-PERPETUAL", "kind": "future", "contract_size": 10.0,
         "tick_size": 0.5, "min_trade_amount": 1.0},   # not an option -> skipped
    ],
}


def test_parses_option_rows_into_filters():
    out = parse_deribit_option_instruments(_PAYLOAD)
    assert set(out) == {"BTC-27JUN25-60000-C", "SOL_USDC-26JUN26-90-P"}  # future skipped
    btc = out["BTC-27JUN25-60000-C"]
    assert btc["tick_size"] == 0.0001
    assert btc["step_size"] == 0.1            # min_trade_amount = qty granularity
    assert btc["min_qty"] == 0.1
    assert btc["min_notional"] == 0.0         # options: no notional floor
    assert btc["contract_size"] == 1.0
    assert btc["base_asset"] == "BTC"


def test_usdc_margined_contract_size_and_base_asset():
    out = parse_deribit_option_instruments(_PAYLOAD)
    sol = out["SOL_USDC-26JUN26-90-P"]
    assert sol["contract_size"] == 10.0
    assert sol["step_size"] == 10.0
    assert sol["base_asset"] == "SOL"         # _USDC suffix stripped by the shared parser


def test_empty_payload_is_empty_dict():
    assert parse_deribit_option_instruments({"result": []}) == {}
    assert parse_deribit_option_instruments({}) == {}

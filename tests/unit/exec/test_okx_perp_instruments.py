"""parse_okx_perp_instruments: instType=SWAP data[] -> RiskLimits-shaped filters + base_asset + ct_val/ct_mult.

OKX SWAP differs from SPOT in TWO filter keys:
  - base_asset <- ctValCcy (SWAP has no baseCcy)
  - PLUS perp-only ct_val (contract value in base coin) and ct_mult (contract multiplier)
"""

from __future__ import annotations

from vike_trader_app.exec.okx.perp_instruments import parse_okx_perp_instruments


def test_parses_ctval_and_filters():
    payload = {"data": [{"instId": "BTC-USDT-SWAP", "instType": "SWAP", "tickSz": "0.1",
                         "lotSz": "0.1", "minSz": "0.1", "maxMktSz": "10000",
                         "ctVal": "0.01", "ctMult": "1", "ctValCcy": "BTC"}]}
    out = parse_okx_perp_instruments(payload)["BTC-USDT-SWAP"]
    assert out["tick_size"] == 0.1
    assert out["step_size"] == 0.1
    assert out["min_qty"] == 0.1
    assert out["max_qty"] == 10000.0
    assert out["min_notional"] == 0.0
    assert out["base_asset"] == "BTC"
    assert out["ct_val"] == 0.01
    assert out["ct_mult"] == 1.0


def test_ctval_missing_defaults_zero():
    payload = {"data": [{"instId": "ETH-USDT-SWAP", "ctValCcy": "ETH",
                         "tickSz": "0.1", "lotSz": "0.1", "minSz": "0.1", "maxMktSz": "5000"}]}
    out = parse_okx_perp_instruments(payload)["ETH-USDT-SWAP"]
    assert out["ct_val"] == 0.0
    assert out["ct_mult"] == 0.0


def test_empty_payload_returns_empty_dict():
    assert parse_okx_perp_instruments({}) == {}
    assert parse_okx_perp_instruments({"data": []}) == {}


def test_uppercases_inst_id():
    payload = {"data": [{"instId": "btc-usdt-swap", "ctValCcy": "BTC",
                         "tickSz": "0.1", "lotSz": "0.1", "minSz": "0.1", "maxMktSz": "10000",
                         "ctVal": "0.01", "ctMult": "1"}]}
    out = parse_okx_perp_instruments(payload)
    assert "BTC-USDT-SWAP" in out
    assert "btc-usdt-swap" not in out


def test_parse_all_fields():
    payload = {"data": [{"instId": "SOL-USDT-SWAP", "ctValCcy": "SOL",
                         "tickSz": "0.01", "lotSz": "0.01", "minSz": "0.01", "maxMktSz": "1000000",
                         "ctVal": "1", "ctMult": "1"}]}
    out = parse_okx_perp_instruments(payload)["SOL-USDT-SWAP"]
    assert out["tick_size"] == 0.01
    assert out["step_size"] == 0.01
    assert out["min_qty"] == 0.01
    assert out["max_qty"] == 1000000.0
    assert out["min_notional"] == 0.0
    assert out["base_asset"] == "SOL"
    assert out["ct_val"] == 1.0
    assert out["ct_mult"] == 1.0

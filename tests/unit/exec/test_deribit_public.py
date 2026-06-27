"""Unit tests for exec/deribit/public.py fetch_option_instruments."""
from __future__ import annotations
import pytest
from vike_trader_app.exec.deribit.public import fetch_option_instruments

_FAKE_RESULT = [
    {"instrument_name": "BTC-27JUN26-100000-C", "kind": "option",
     "tick_size": 0.0001, "min_trade_amount": 0.1, "contract_size": 1.0},
    {"instrument_name": "BTC-27JUN26-100000-P", "kind": "option",
     "tick_size": 0.0001, "min_trade_amount": 0.1, "contract_size": 1.0},
    {"instrument_name": "ETH-27JUN26-2000-C", "kind": "option",
     "tick_size": 0.0001, "min_trade_amount": 1.0, "contract_size": 1.0},
]


def _make_transport(result):
    def _t(url):
        return {"jsonrpc": "2.0", "result": result}
    return _t


def test_fetch_returns_filters_dict():
    result = fetch_option_instruments(
        "BTC", base_url="https://www.deribit.com",
        transport=_make_transport(_FAKE_RESULT))
    assert "BTC-27JUN26-100000-C" in result
    assert "BTC-27JUN26-100000-P" in result
    # ETH instrument also appears — parse_deribit_option_instruments doesn't filter by currency
    assert "ETH-27JUN26-2000-C" in result
    # Check filters shape
    f = result["BTC-27JUN26-100000-C"]
    assert f["tick_size"] == pytest.approx(0.0001)
    assert f["step_size"] == pytest.approx(0.1)
    assert f["min_qty"] == pytest.approx(0.1)
    assert f["max_qty"] == 0.0
    assert f["min_notional"] == 0.0


def test_fetch_empty_result():
    result = fetch_option_instruments(
        "BTC", base_url="https://www.deribit.com",
        transport=_make_transport([]))
    assert result == {}


def test_fetch_url_includes_currency_and_kind():
    seen_urls = []
    def _t(url):
        seen_urls.append(url)
        return {"result": []}
    fetch_option_instruments("ETH", base_url="https://test.deribit.com", transport=_t)
    assert len(seen_urls) == 1
    assert "currency=ETH" in seen_urls[0]
    assert "kind=option" in seen_urls[0]
    assert "test.deribit.com" in seen_urls[0]

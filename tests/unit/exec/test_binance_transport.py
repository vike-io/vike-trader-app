"""Signed transport: POST builds a signed Request with the auth header; error body raises typed."""

import io
import json
import urllib.error

import pytest

from vike_trader_app.exec.binance.transport import BinanceApiError, signed_request


class _FakeSigner:
    def prepare(self, params):
        from urllib.parse import urlencode
        return urlencode({**params, "timestamp": 1, "signature": "deadbeef"}), {"X-MBX-APIKEY": "KEY"}


def _ok_urlopen(captured):
    def _open(req, timeout=30):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = dict(req.header_items())
        return io.BytesIO(json.dumps({"orderId": 99, "status": "NEW"}).encode())
    return _open


def test_signed_request_builds_post_with_header_and_signature():
    captured = {}
    out = signed_request("https://demo-api.binance.com", "/api/v3/order", "POST",
                         {"symbol": "BTCUSDT", "side": "BUY"},
                         _FakeSigner(), urlopen=_ok_urlopen(captured))
    assert out == {"orderId": 99, "status": "NEW"}
    assert captured["method"] == "POST"
    assert "signature=deadbeef" in captured["url"]
    assert captured["url"].startswith("https://demo-api.binance.com/api/v3/order?")
    # header keys are title-cased by urllib; match case-insensitively
    assert any(k.lower() == "x-mbx-apikey" for k in captured["headers"])


def test_error_body_raises_typed_error():
    def _err_open(req, timeout=30):
        body = json.dumps({"code": -2010, "msg": "Filter failure: MIN_NOTIONAL"}).encode()
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(body))
    with pytest.raises(BinanceApiError) as ei:
        signed_request("https://demo-api.binance.com", "/api/v3/order", "POST",
                       {"symbol": "BTCUSDT"}, _FakeSigner(), urlopen=_err_open)
    assert ei.value.code == -2010
    assert "MIN_NOTIONAL" in ei.value.msg


def test_network_error_is_wrapped_without_leaking_the_signed_url():
    def _boom(req, timeout=0):
        raise urllib.error.URLError("getaddrinfo failed")
    with pytest.raises(BinanceApiError) as ei:
        signed_request("https://x", "/api/v3/order", "POST", {"a": 1}, _FakeSigner(), urlopen=_boom)
    assert "signature" not in str(ei.value)

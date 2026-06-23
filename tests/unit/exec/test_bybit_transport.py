"""Bybit transport: GET query in URL; POST sends the signer's EXACT body bytes with JSON content-type
and never re-serializes; the 200 body is returned verbatim (retCode unwrapping is the client's job)."""

import io
import json

from vike_trader_app.exec.bybit.transport import BybitApiError, bybit_signed_request
from vike_trader_app.exec.crypto_client import VenueApiError
from vike_trader_app.exec.signer import PreparedRequest


class _GetSigner:
    def prepare(self, params, *, method="GET", path=""):
        assert method == "GET"
        return PreparedRequest(query="category=spot&symbol=BTCUSDT",
                               headers={"X-BAPI-API-KEY": "K"})


class _PostSigner:
    BODY = b'{"category":"spot","symbol":"BTCUSDT"}'

    def prepare(self, params, *, method="GET", path=""):
        assert method == "POST"
        return PreparedRequest(body=self.BODY, headers={"X-BAPI-SIGN": "sig"})


def _capture_open(captured, payload):
    def _open(req, timeout=30):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["data"] = req.data
        return io.BytesIO(json.dumps(payload).encode())
    return _open


def test_get_puts_query_in_url():
    captured = {}
    out = bybit_signed_request("https://api-demo.bybit.com", "/v5/order/realtime", "GET",
                               {"category": "spot", "symbol": "BTCUSDT"}, _GetSigner(),
                               urlopen=_capture_open(captured, {"retCode": 0, "result": {}}))
    assert captured["method"] == "GET"
    assert captured["url"] == ("https://api-demo.bybit.com/v5/order/realtime"
                               "?category=spot&symbol=BTCUSDT")
    assert captured["data"] is None
    assert out == {"retCode": 0, "result": {}}


def test_post_sends_exact_signed_bytes_with_json_content_type():
    captured = {}
    out = bybit_signed_request("https://api-demo.bybit.com", "/v5/order/create", "POST",
                               {"category": "spot", "symbol": "BTCUSDT"}, _PostSigner(),
                               urlopen=_capture_open(captured, {"retCode": 0, "result": {"orderId": "1"}}))
    assert captured["method"] == "POST"
    assert captured["url"] == "https://api-demo.bybit.com/v5/order/create"
    # the EXACT bytes from the signer, not a re-dump
    assert captured["data"] == _PostSigner.BODY
    assert captured["headers"]["content-type"] == "application/json"
    # 200 body returned verbatim — transport does NOT raise on retCode
    assert out == {"retCode": 0, "result": {"orderId": "1"}}


def test_business_error_body_is_returned_not_raised():
    """retCode!=0 is a normal 200 body — the transport returns it; the client's unwrap() raises."""
    captured = {}
    out = bybit_signed_request("https://api-demo.bybit.com", "/v5/order/create", "POST",
                               {"x": 1}, _PostSigner(),
                               urlopen=_capture_open(captured, {"retCode": 110001, "retMsg": "gone"}))
    assert out == {"retCode": 110001, "retMsg": "gone"}


def test_bybit_api_error_is_a_venue_api_error():
    assert issubclass(BybitApiError, VenueApiError)
    err = BybitApiError(110001, "order not exists")
    assert isinstance(err, VenueApiError)
    assert err.code == 110001

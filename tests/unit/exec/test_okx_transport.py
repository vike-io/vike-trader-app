"""OKX transport: browser-UA on every request; x-simulated-trading on demo; 200-body verbatim.

GET appends the signed query verbatim; POST sends the signer's EXACT body bytes.
Business-error {code!="0"} bodies are returned verbatim — unwrap() in the client raises.
HTTP errors (e.g. Cloudflare 1010 on 403) become OKXApiError.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from vike_trader_app.exec.crypto_client import VenueApiError
from vike_trader_app.exec.okx.transport import OKXApiError, okx_public_get, okx_signed_request
from vike_trader_app.exec.signer import PreparedRequest


class _GetSigner:
    def prepare(self, params, *, method="GET", path=""):
        assert method == "GET"
        return PreparedRequest(query="instType=SPOT&instId=BTC-USDT",
                               headers={"OK-ACCESS-KEY": "K"})


class _PostSigner:
    BODY = b'{"instId":"BTC-USDT","tdMode":"cash"}'

    def prepare(self, params, *, method="GET", path=""):
        assert method == "POST"
        return PreparedRequest(body=self.BODY, headers={"OK-ACCESS-SIGN": "sig"})


def _capture_open(captured, payload):
    def _open(req, timeout=30):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["data"] = req.data
        return io.BytesIO(json.dumps(payload).encode())
    return _open


def test_get_puts_query_in_url_with_browser_ua():
    captured = {}
    out = okx_signed_request(
        "https://www.okx.com", "/api/v5/trade/orders-pending", "GET",
        {"instType": "SPOT", "instId": "BTC-USDT"}, _GetSigner(),
        simulated=True,
        urlopen=_capture_open(captured, {"code": "0", "data": []}),
    )
    assert captured["method"] == "GET"
    assert captured["url"] == (
        "https://www.okx.com/api/v5/trade/orders-pending?instType=SPOT&instId=BTC-USDT"
    )
    assert captured["data"] is None
    assert "mozilla/5.0" in captured["headers"]["user-agent"].lower()
    assert captured["headers"]["accept-language"].startswith("en-US")
    assert captured["headers"]["x-simulated-trading"] == "1"
    assert out == {"code": "0", "data": []}


def test_post_sends_exact_signed_bytes_json_ct():
    captured = {}
    out = okx_signed_request(
        "https://www.okx.com", "/api/v5/trade/order", "POST",
        {"instId": "BTC-USDT", "tdMode": "cash"}, _PostSigner(),
        simulated=True,
        urlopen=_capture_open(captured, {"code": "0", "data": [{"ordId": "1"}]}),
    )
    assert captured["method"] == "POST"
    assert captured["data"] == _PostSigner.BODY
    assert captured["headers"]["content-type"] == "application/json"
    assert "mozilla/5.0" in captured["headers"]["user-agent"].lower()
    assert captured["headers"]["x-simulated-trading"] == "1"
    assert out == {"code": "0", "data": [{"ordId": "1"}]}


def test_mainnet_omits_simulated_header():
    captured = {}
    okx_signed_request(
        "https://www.okx.com", "/api/v5/trade/orders-pending", "GET",
        {"instType": "SPOT"}, _GetSigner(),
        simulated=False,
        urlopen=_capture_open(captured, {"code": "0", "data": []}),
    )
    assert "x-simulated-trading" not in captured["headers"]
    assert "mozilla/5.0" in captured["headers"]["user-agent"].lower()


def test_business_error_body_is_returned_not_raised():
    """code!="0" is still a 200 body — the transport returns it verbatim; client unwrap() raises."""
    captured = {}
    out = okx_signed_request(
        "https://www.okx.com", "/api/v5/trade/order", "POST",
        {"instId": "BTC-USDT"}, _PostSigner(),
        simulated=True,
        urlopen=_capture_open(
            captured,
            {"code": "1", "data": [{"sCode": "51400", "sMsg": "gone"}]},
        ),
    )
    assert out == {"code": "1", "data": [{"sCode": "51400", "sMsg": "gone"}]}


def test_public_get_has_browser_ua_and_query():
    captured = {}
    out = okx_public_get(
        "https://www.okx.com", "/api/v5/public/instruments",
        {"instType": "SPOT", "instId": "BTC-USDT"},
        simulated=True,
        urlopen=_capture_open(captured, {"code": "0", "data": []}),
    )
    assert captured["url"] == (
        "https://www.okx.com/api/v5/public/instruments?instType=SPOT&instId=BTC-USDT"
    )
    assert "mozilla/5.0" in captured["headers"]["user-agent"].lower()
    assert captured["headers"]["x-simulated-trading"] == "1"
    assert out == {"code": "0", "data": []}


def test_http_403_cloudflare_html_becomes_okx_api_error():
    def _open_403(req, timeout=30):
        raise urllib.error.HTTPError(
            req.full_url, 403, "Forbidden", {},
            io.BytesIO(b"<html>error code: 1010</html>"),
        )

    with pytest.raises(OKXApiError) as ei:
        okx_signed_request(
            "https://www.okx.com", "/api/v5/trade/orders-pending", "GET",
            {"instType": "SPOT"}, _GetSigner(),
            simulated=True,
            urlopen=_open_403,
        )
    assert ei.value.code == 403


def test_okx_api_error_is_a_venue_api_error():
    assert issubclass(OKXApiError, VenueApiError)
    err = OKXApiError(51400, "invalid instrument")
    assert isinstance(err, VenueApiError)
    assert err.code == 51400

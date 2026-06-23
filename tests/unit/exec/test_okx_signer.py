"""OKXV5Signer: ISO8601-ms-Z timestamp, base64 HMAC over ts+method+requestPath+body, OK-ACCESS-* headers."""

import base64
import hashlib
import hmac
import json
import re
import urllib.parse
from datetime import datetime, timezone

from vike_trader_app.exec.credentials import Credentials
from vike_trader_app.exec.signer import OKXV5Signer, PreparedRequest


_KEY = "OKXKEY"
_SECRET = "OKXSECRET"
_PASS = "OKXPASS"
_TS = 1_700_000_000_000


def _signer(offset_ms=0):
    creds = Credentials(api_key=_KEY, api_secret=_SECRET, passphrase=_PASS)
    return OKXV5Signer(creds, now_ms=lambda: _TS, offset_ms=offset_ms)


def _iso(ms):
    """ISO8601 with millis + Z (e.g. 2026-06-24T12:34:56.789Z)."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _expected_sign(prehash):
    """base64(HMAC-SHA256(prehash, secret))."""
    return base64.b64encode(
        hmac.new(_SECRET.encode(), prehash.encode(), hashlib.sha256).digest()
    ).decode()


def test_get_signs_requestpath_with_query():
    params = {"instType": "SPOT", "instId": "BTC-USDT"}
    pr = _signer().prepare(params, method="GET", path="/api/v5/trade/orders-pending")

    assert isinstance(pr, PreparedRequest)
    q = urllib.parse.urlencode(params)
    assert pr.query == q
    assert pr.body is None
    assert pr.headers["OK-ACCESS-KEY"] == _KEY
    assert pr.headers["OK-ACCESS-PASSPHRASE"] == _PASS
    assert pr.headers["OK-ACCESS-TIMESTAMP"] == _iso(_TS)
    assert pr.headers["OK-ACCESS-SIGN"] == _expected_sign(_iso(_TS) + "GET" + "/api/v5/trade/orders-pending?" + q + "")


def test_post_signs_exact_body_bytes():
    params = {
        "instId": "BTC-USDT",
        "tdMode": "cash",
        "side": "buy",
        "ordType": "limit",
        "sz": "0.001",
        "px": "65000",
        "clOrdId": "sess-0",
    }
    pr = _signer().prepare(params, method="POST", path="/api/v5/trade/order")

    body = json.dumps(params, separators=(",", ":"))
    assert pr.body == body.encode()
    assert pr.query == ""
    assert pr.headers["OK-ACCESS-SIGN"] == _expected_sign(_iso(_TS) + "POST" + "/api/v5/trade/order" + body)


def test_timestamp_is_iso8601_ms_z():
    pr = _signer().prepare({}, method="GET", path="/p")
    ts = pr.headers["OK-ACCESS-TIMESTAMP"]
    assert ts.endswith("Z")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", ts)


def test_offset_is_applied_to_timestamp():
    pr = _signer(offset_ms=250).prepare({"a": "1"}, method="GET", path="/p")
    assert pr.headers["OK-ACCESS-TIMESTAMP"] == _iso(_TS + 250)


def test_set_offset_updates_skew():
    s = _signer()
    s.set_offset_ms(-1000)
    pr = s.prepare({"a": "1"}, method="GET", path="/p")
    assert pr.headers["OK-ACCESS-TIMESTAMP"] == _iso(_TS - 1000)


def test_secret_sign_passphrase_never_in_repr_or_str():
    s = _signer()
    pr = s.prepare({}, method="GET", path="/p")

    assert _SECRET not in repr(s)
    assert _SECRET not in str(s)
    assert _PASS not in repr(s)
    assert pr.headers["OK-ACCESS-SIGN"] not in repr(s)

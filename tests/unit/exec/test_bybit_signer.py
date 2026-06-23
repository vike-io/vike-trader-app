"""BybitV5Signer: HMAC over ts+key+recv+(query|body); sign-then-send the EXACT body bytes; secret
and signature never leak into repr/str."""

import hashlib
import hmac
import json
import urllib.parse

from vike_trader_app.exec.credentials import Credentials
from vike_trader_app.exec.signer import BybitV5Signer, PreparedRequest

_KEY = "BYBITKEY"
_SECRET = "BYBITSECRET"
_TS = 1_700_000_000_000


def _signer(offset_ms=0):
    creds = Credentials(api_key=_KEY, api_secret=_SECRET)
    return BybitV5Signer(creds, now_ms=lambda: _TS, recv_window=5000, offset_ms=offset_ms)


def _expected_sign(payload: str, ts: int = _TS, recv: str = "5000") -> str:
    msg = f"{ts}{_KEY}{recv}{payload}"
    return hmac.new(_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()


def test_get_signs_urlencoded_query():
    params = {"category": "spot", "symbol": "BTCUSDT"}
    pr = _signer().prepare(params, method="GET", path="/v5/order/realtime")
    assert isinstance(pr, PreparedRequest)
    expected_query = urllib.parse.urlencode(params)
    assert pr.query == expected_query
    assert pr.body is None
    assert pr.headers["X-BAPI-API-KEY"] == _KEY
    assert pr.headers["X-BAPI-TIMESTAMP"] == str(_TS)
    assert pr.headers["X-BAPI-RECV-WINDOW"] == "5000"
    assert pr.headers["X-BAPI-SIGN"] == _expected_sign(expected_query)


def test_post_signs_exact_body_bytes():
    params = {"category": "spot", "symbol": "BTCUSDT", "side": "Buy",
              "orderType": "Limit", "qty": "0.001", "orderLinkId": "sess-0"}
    pr = _signer().prepare(params, method="POST", path="/v5/order/create")
    body_str = json.dumps(params, separators=(",", ":"))
    # the EXACT bytes that were signed are the EXACT bytes returned for the wire
    assert pr.body == body_str.encode()
    assert pr.query == ""
    assert pr.headers["X-BAPI-SIGN"] == _expected_sign(body_str)


def test_offset_is_applied_to_timestamp():
    pr = _signer(offset_ms=250).prepare({"a": "1"}, method="GET")
    assert pr.headers["X-BAPI-TIMESTAMP"] == str(_TS + 250)


def test_set_offset_updates_skew():
    s = _signer()
    s.set_offset_ms(-1000)
    pr = s.prepare({"a": "1"}, method="GET")
    assert pr.headers["X-BAPI-TIMESTAMP"] == str(_TS - 1000)


def test_secret_and_sign_never_in_repr_or_str():
    s = _signer()
    assert _SECRET not in repr(s)
    assert _SECRET not in str(s)
    pr = s.prepare({"a": "1"}, method="GET")
    assert pr.headers["X-BAPI-SIGN"] not in repr(s)

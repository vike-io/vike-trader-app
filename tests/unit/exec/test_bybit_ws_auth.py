"""Bybit V5 private-WS auth: GET/realtime{expires} signing, distinct from REST."""

from __future__ import annotations

import hashlib
import hmac

from vike_trader_app.exec.bybit.ws_auth import (
    PING_FRAME,
    build_auth_frame,
    build_subscribe_frame,
    bybit_ws_sign,
)
from vike_trader_app.exec.credentials import Credentials
from vike_trader_app.exec.signer import BybitV5Signer

_KEY = "BYBITKEY"
_SECRET = "BYBITSECRET"


def test_sign_matches_reference_hmac():
    """WS sign = hex HMAC-SHA256(secret, 'GET/realtime{expires_ms}')."""
    expires_ms = 1_700_000_000_000
    expected = hmac.new(
        _SECRET.encode(), f"GET/realtime{expires_ms}".encode(), hashlib.sha256
    ).hexdigest()
    assert bybit_ws_sign(_SECRET, expires_ms) == expected


def test_auth_frame_shape_and_expires():
    """{'op':'auth','args':[api_key, expires, sign]} with expires = now_ms() + expires_skew_ms."""
    now_ms = 1000
    frame = build_auth_frame(_KEY, _SECRET, now_ms=lambda: now_ms)

    assert frame["op"] == "auth"
    assert frame["args"][0] == _KEY
    assert frame["args"][1] == 6000  # 1000 + 5000 (default skew)
    assert frame["args"][2] == bybit_ws_sign(_SECRET, 6000)


def test_subscribe_frame_default_topics():
    """Default topics are ('execution', 'order')."""
    frame = build_subscribe_frame()
    assert frame == {"op": "subscribe", "args": ["execution", "order"]}


def test_subscribe_frame_custom_topics():
    """Custom topics override the default."""
    frame = build_subscribe_frame(("fills", "orders", "positions"))
    assert frame == {"op": "subscribe", "args": ["fills", "orders", "positions"]}


def test_ping_frame():
    """PING_FRAME is the keepalive."""
    assert PING_FRAME == {"op": "ping"}


def test_sign_is_not_rest_signature():
    """WS sign is distinct from REST X-BAPI-SIGN (guards against accidental reuse)."""
    expires_ms = 1_700_000_000_000
    ws_sign = bybit_ws_sign(_SECRET, expires_ms)

    # REST signature is over ts+key+recv+payload, with different prehash
    creds = Credentials(_KEY, _SECRET)
    rest_signer = BybitV5Signer(creds, now_ms=lambda: 0)
    rest_request = rest_signer.prepare({}, method="POST")
    rest_sign = rest_request.headers["X-BAPI-SIGN"]

    # They must be different (WS and REST sign different strings)
    assert ws_sign != rest_sign

"""Tests for OKX V5 WS-login auth helper."""
from __future__ import annotations
import base64
import hashlib
import hmac

import pytest

from vike_trader_app.exec.okx.ws_auth import okx_ws_sign, build_login_frame, build_subscribe_frame, PING_TEXT
from vike_trader_app.exec.signer import OKXV5Signer
from vike_trader_app.exec.credentials import Credentials


_SECRET = "OKXSECRET"


def test_sign_matches_reference_hmac():
    """Test that okx_ws_sign matches stdlib HMAC-SHA256 directly."""
    ts = "1700000000"
    prehash = f"{ts}GET/users/self/verify".encode()
    expected = base64.b64encode(hmac.new(_SECRET.encode(), prehash, hashlib.sha256).digest()).decode()
    assert okx_ws_sign(_SECRET, ts) == expected


def test_login_frame_shape():
    """Test login frame structure: op, args list with correct keys."""
    frame = build_login_frame("K", "S", "P", now_s=lambda: 1700000000)
    assert frame["op"] == "login"
    assert isinstance(frame["args"], list)
    assert len(frame["args"]) == 1

    login_arg = frame["args"][0]
    assert set(login_arg.keys()) == {"apiKey", "passphrase", "timestamp", "sign"}
    assert login_arg["apiKey"] == "K"
    assert login_arg["passphrase"] == "P"
    assert login_arg["timestamp"] == "1700000000"
    assert login_arg["sign"] == okx_ws_sign("S", "1700000000")


def test_login_timestamp_is_seconds_not_ms():
    """Test that timestamp is seconds string (10 digits), not milliseconds."""
    frame = build_login_frame("K", "S", "P", now_s=lambda: 1700000000)
    timestamp = frame["args"][0]["timestamp"]
    assert timestamp == "1700000000"
    assert len(timestamp) == 10  # Guard: seconds are 10 digits; ms would be 13


def test_subscribe_frame_default():
    """Test default subscribe frame with SPOT inst_type."""
    frame = build_subscribe_frame()
    assert frame == {"op": "subscribe", "args": [{"channel": "orders", "instType": "SPOT"}]}


def test_subscribe_frame_custom_inst_type():
    """Test subscribe frame with custom inst_type."""
    frame = build_subscribe_frame("ANY")
    assert frame["args"][0]["instType"] == "ANY"


def test_sign_is_not_rest_signature():
    """Test that WS sign is DISTINCT from REST OKXV5Signer signature."""
    # Build REST signer and get its signature
    rest_signer = OKXV5Signer(Credentials("K", "S", "P"), now_ms=lambda: 0)
    rest_sig = rest_signer.prepare({}, method="POST", path="/api/v5/trade/order")
    rest_access_sign = rest_sig.headers["OK-ACCESS-SIGN"]

    # Build WS signature
    ws_sign = okx_ws_sign("S", "1700000000")

    # They must NOT be the same
    assert ws_sign != rest_access_sign


def test_ping_text_is_raw_text():
    """Test that PING_TEXT is the raw text 'ping' for keepalive."""
    assert PING_TEXT == "ping"

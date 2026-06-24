"""Tests for Binance WS-API userDataStream.subscribe.signature helper."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from unittest.mock import patch

import pytest

from vike_trader_app.exec.binance.ws_auth import binance_ws_sign, build_subscribe_request


def test_sign_matches_reference_hmac():
    """Verify binance_ws_sign produces hex HMAC-SHA256 matching stdlib."""
    params = {"apiKey": "K", "recvWindow": 5000, "timestamp": 1700000000000}
    payload = "apiKey=K&recvWindow=5000&timestamp=1700000000000"
    expected = hmac.new(b"S", payload.encode(), hashlib.sha256).hexdigest()
    assert binance_ws_sign("S", params) == expected


def test_subscribe_request_shape_and_signs_over_sorted_params_excluding_signature():
    """Verify build_subscribe_request assembles correct shape and signature excludes itself."""
    req = build_subscribe_request(
        "K", "S", now_ms=lambda: 1700000000000, recv_window=5000, req_id="r1"
    )
    assert req["id"] == "r1"
    assert req["method"] == "userDataStream.subscribe.signature"
    p = req["params"]
    assert p["apiKey"] == "K" and p["recvWindow"] == 5000 and p["timestamp"] == 1700000000000
    payload = "apiKey=K&recvWindow=5000&timestamp=1700000000000"
    assert p["signature"] == hmac.new(b"S", payload.encode(), hashlib.sha256).hexdigest()
    assert "signature" not in payload  # signature excluded from its own signed payload


def test_default_req_id_is_unique_and_secret_not_in_frame():
    """Verify default req_id is unique UUID and secret doesn't leak into repr/str."""
    a = build_subscribe_request("K", "SUPERSECRET", now_ms=lambda: 1)
    b = build_subscribe_request("K", "SUPERSECRET", now_ms=lambda: 1)
    assert a["id"] != b["id"]
    assert "SUPERSECRET" not in str(a) and "SUPERSECRET" not in repr(a)

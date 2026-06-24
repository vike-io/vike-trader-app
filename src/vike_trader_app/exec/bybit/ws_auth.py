"""Bybit V5 private WebSocket auth helper: GET/realtime{expires} signing (distinct from REST)."""

from __future__ import annotations

import hashlib
import hmac
from typing import Callable


def bybit_ws_sign(api_secret: str, expires_ms: int) -> str:
    """Return hex HMAC-SHA256(secret, f'GET/realtime{expires_ms}').

    This is the WS auth signature prehash — different from REST (ts+key+recv+payload).
    """
    prehash = f"GET/realtime{expires_ms}".encode()
    return hmac.new(api_secret.encode(), prehash, hashlib.sha256).hexdigest()


def build_auth_frame(
    api_key: str, api_secret: str, *, now_ms: Callable[[], int], expires_skew_ms: int = 5000
) -> dict:
    """Build a WS auth frame: {'op':'auth','args':[api_key, expires, sign]}.

    expires = now_ms() + expires_skew_ms (ms timestamp).
    """
    expires = now_ms() + expires_skew_ms
    sign = bybit_ws_sign(api_secret, expires)
    return {"op": "auth", "args": [api_key, expires, sign]}


def build_subscribe_frame(topics: tuple[str, ...] = ("execution", "order")) -> dict:
    """Build a WS subscribe frame: {'op':'subscribe','args':[*topics]}."""
    return {"op": "subscribe", "args": list(topics)}


PING_FRAME = {"op": "ping"}

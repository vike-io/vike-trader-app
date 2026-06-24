"""OKX V5 private-WS login auth: ts+'GET'+'/users/self/verify' signing (distinct from REST)."""
from __future__ import annotations
import base64
import hashlib
import hmac
from typing import Callable

PING_TEXT = "ping"   # OKX keepalive is a raw text frame; server replies 'pong'


def okx_ws_sign(api_secret: str, ts_seconds: str) -> str:
    """base64(HMAC-SHA256(secret, f'{ts_seconds}GET/users/self/verify')).

    ts_seconds is epoch SECONDS as a string (not milliseconds, not ISO8601).
    This is DISTINCT from the REST OKXV5Signer.prepare() signing.
    """
    prehash = f"{ts_seconds}GET/users/self/verify".encode()
    return base64.b64encode(hmac.new(api_secret.encode(), prehash, hashlib.sha256).digest()).decode()


def build_login_frame(
    api_key: str, api_secret: str, passphrase: str, *,
    now_s: Callable[[], int]
) -> dict:
    """Build OKX WS login frame.

    {"op":"login","args":[{"apiKey","passphrase","timestamp":str(now_s()),"sign"}]}

    NEVER log this frame — it carries apiKey + passphrase + sign.
    """
    ts = str(now_s())
    return {
        "op": "login",
        "args": [{
            "apiKey": api_key,
            "passphrase": passphrase,
            "timestamp": ts,
            "sign": okx_ws_sign(api_secret, ts)
        }]
    }


def build_subscribe_frame(inst_type: str = "SPOT") -> dict:
    """Build OKX WS subscribe frame for orders.

    {"op":"subscribe","args":[{"channel":"orders","instType":inst_type}]}
    """
    return {
        "op": "subscribe",
        "args": [{
            "channel": "orders",
            "instType": inst_type
        }]
    }

"""Binance WS-API userDataStream.subscribe.signature helper.

Signs HMAC-SHA256 hex over the sorted (alphabetical) params joined as k=v&... (EXCLUDING
the signature itself). The signature is placed in the JSON params dict, not a query string
or header. HARD: the secret/signature must never leak into __repr__/__str__/any log line.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from typing import Callable


def binance_ws_sign(api_secret: str, params: dict) -> str:
    """Compute hex HMAC-SHA256 over sorted params (excluding 'signature' key).

    The payload is constructed by sorting params alphabetically, joining as 'k=v&...',
    and signing with the api_secret.

    Args:
        api_secret: The Binance API secret key.
        params: The parameters dict (should exclude 'signature' key).

    Returns:
        Hex-encoded HMAC-SHA256 digest.
    """
    # Sort params alphabetically, join as k=v&...
    payload = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    # Compute HMAC-SHA256
    return hmac.new(
        api_secret.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()


def build_subscribe_request(
    api_key: str,
    api_secret: str,
    *,
    now_ms: Callable[[], int],
    recv_window: int = 5000,
    req_id: str | None = None,
) -> dict:
    """Build a userDataStream.subscribe.signature JSON request.

    Constructs the params dict (apiKey, recvWindow, timestamp), computes the signature
    over these three sorted params (excluding signature itself), then assembles the
    complete request.

    Args:
        api_key: The Binance API key.
        api_secret: The Binance API secret key.
        now_ms: Callable that returns current time in milliseconds.
        recv_window: Receive window in milliseconds (default 5000).
        req_id: Optional request ID (default: UUID4 hex).

    Returns:
        A dict with shape:
        {
            'id': <req_id or uuid4 hex>,
            'method': 'userDataStream.subscribe.signature',
            'params': {
                'apiKey': api_key,
                'recvWindow': recv_window,
                'timestamp': now_ms(),
                'signature': <hex>
            }
        }

    WARNING: This request carries apiKey + signature — NEVER log it.
    """
    req_id = req_id or uuid.uuid4().hex

    # Build params for signing (excluding signature)
    params_to_sign = {
        "apiKey": api_key,
        "recvWindow": recv_window,
        "timestamp": now_ms(),
    }

    # Sign over the three params
    signature = binance_ws_sign(api_secret, params_to_sign)

    # Build final request
    return {
        "id": req_id,
        "method": "userDataStream.subscribe.signature",
        "params": {
            **params_to_sign,
            "signature": signature,
        },
    }

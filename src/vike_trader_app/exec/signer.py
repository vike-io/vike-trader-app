"""Request signing seam. `Signer.prepare(params)` stamps a timestamp + recvWindow, signs the query
string, and returns (signed_query_string, headers). BinanceHmacSigner is the first impl (stdlib
hmac/hashlib). A clock-offset hook (set via set_offset_ms after a GET /api/v3/time) keeps skew a
Signer concern. HARD: the secret/signature never reach __repr__/__str__/any log line.
"""

from __future__ import annotations

import hashlib
import hmac
import urllib.parse
from typing import Callable, Protocol

from vike_trader_app.exec.credentials import Credentials


class Signer(Protocol):
    def prepare(self, params: dict) -> tuple[str, dict[str, str]]:
        ...


class BinanceHmacSigner:
    """HMAC-SHA256 hex over the query string, X-MBX-APIKEY header, ms timestamp + recvWindow."""

    def __init__(self, credentials: Credentials, *, now_ms: Callable[[], int],
                 recv_window: int = 5000, offset_ms: int = 0) -> None:
        self._key = credentials.api_key
        self._secret = credentials.api_secret.encode()
        self._now_ms = now_ms
        self._recv_window = recv_window
        self._offset_ms = offset_ms

    def set_offset_ms(self, offset_ms: int) -> None:
        """Apply a server-time skew correction (from GET /api/v3/time)."""
        self._offset_ms = offset_ms

    def prepare(self, params: dict) -> tuple[str, dict[str, str]]:
        signed = dict(params)
        signed["timestamp"] = self._now_ms() + self._offset_ms
        signed["recvWindow"] = self._recv_window
        body = urllib.parse.urlencode(signed)
        signature = hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()
        return f"{body}&signature={signature}", {"X-MBX-APIKEY": self._key}

    def __repr__(self) -> str:  # secret/signature must never leak into logs/exceptions
        return f"BinanceHmacSigner(key=***{self._key[-4:] if self._key else ''}, recv_window={self._recv_window})"

    __str__ = __repr__

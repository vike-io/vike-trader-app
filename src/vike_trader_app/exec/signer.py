"""Request signing seam. `Signer.prepare(params)` stamps a timestamp + recvWindow, signs the query
string, and returns (signed_query_string, headers). BinanceHmacSigner is the first impl (stdlib
hmac/hashlib). A clock-offset hook (set via set_offset_ms after a GET /api/v3/time) keeps skew a
Signer concern. HARD: the secret/signature never reach __repr__/__str__/any log line.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable, Protocol

from vike_trader_app.exec.credentials import Credentials


@dataclass(frozen=True)
class PreparedRequest:
    """A signed request the transport can send: GET appends `query` to the URL; POST sends the exact
    signed `body` bytes (Bybit). `headers` carries the venue auth headers. Binance keeps using the
    legacy 2-tuple return; only BybitV5Signer returns a PreparedRequest."""

    query: str = ""
    body: bytes | None = None
    headers: dict[str, str] = field(default_factory=dict)


class Signer(Protocol):
    def prepare(self, params: dict, *, method: str = "GET", path: str = ""): ...


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

    def prepare(self, params: dict, *, method: str = "GET", path: str = "") -> tuple[str, dict[str, str]]:
        signed = dict(params)
        signed["timestamp"] = self._now_ms() + self._offset_ms
        signed["recvWindow"] = self._recv_window
        body = urllib.parse.urlencode(signed)
        signature = hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()
        return f"{body}&signature={signature}", {"X-MBX-APIKEY": self._key}

    def __repr__(self) -> str:  # secret/signature must never leak into logs/exceptions
        return f"BinanceHmacSigner(key=***{self._key[-4:] if self._key else ''}, recv_window={self._recv_window})"

    __str__ = __repr__


class BybitV5Signer:
    """Bybit V5: HMAC-SHA256 hex over ts+api_key+recv_window+(queryString|rawJsonBody), carried in
    X-BAPI-* headers. GET signs the urlencoded query; POST signs the EXACT json body bytes that are
    then sent verbatim (sign-then-send — never re-serialize, or X-BAPI-SIGN won't match)."""

    def __init__(self, credentials: Credentials, *, now_ms: Callable[[], int],
                 recv_window: int = 5000, offset_ms: int = 0) -> None:
        self._key = credentials.api_key
        self._secret = credentials.api_secret.encode()
        self._now_ms = now_ms
        self._recv_window = recv_window
        self._offset_ms = offset_ms

    def set_offset_ms(self, offset_ms: int) -> None:
        """Apply a server-time skew correction (from GET /v5/market/time)."""
        self._offset_ms = offset_ms

    def prepare(self, params: dict, *, method: str = "GET", path: str = "") -> PreparedRequest:
        ts = str(self._now_ms() + self._offset_ms)
        recv = str(self._recv_window)
        if method.upper() == "GET":
            query = urllib.parse.urlencode(params)
            payload, body = query, None
        else:
            body_str = json.dumps(params, separators=(",", ":"))
            payload, body = body_str, body_str.encode()
        sign = hmac.new(self._secret, f"{ts}{self._key}{recv}{payload}".encode(),
                        hashlib.sha256).hexdigest()
        headers = {
            "X-BAPI-API-KEY": self._key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv,
            "X-BAPI-SIGN": sign,
        }
        return PreparedRequest(query=payload if method.upper() == "GET" else "",
                               body=body, headers=headers)

    def __repr__(self) -> str:  # secret/signature must never leak into logs/exceptions
        tail = self._key[-4:] if self._key else ""
        return f"BybitV5Signer(key=***{tail}, recv_window={self._recv_window})"

    __str__ = __repr__

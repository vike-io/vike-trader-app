"""Signed urllib transport for Binance spot order/account/userDataStream endpoints.

Builds a urllib.request.Request (method= + signed query in the URL + X-MBX-APIKEY header) since the
shared data/rest.get_json is unsigned/GET-only. A {"code":-XXXX,"msg":"..."} 4xx body becomes a
typed BinanceApiError. Unsigned exchangeInfo/time route through data/rest.get_json (shared UA).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from vike_trader_app.data.rest import get_json
from vike_trader_app.exec.crypto_client import VenueApiError


class BinanceApiError(VenueApiError):
    """A Binance {code, msg} error response (HTTP 4xx)."""

    def __init__(self, code: int, msg: str) -> None:
        super().__init__(code, msg)


def signed_request(base_url: str, path: str, method: str, params: dict, signer,
                   *, urlopen=urllib.request.urlopen, timeout: int = 30) -> dict:
    """Sign `params`, issue `method` to `base_url+path`, return parsed JSON or raise BinanceApiError."""
    query, headers = signer.prepare(params)
    url = f"{base_url}{path}?{query}"
    req = urllib.request.Request(url, method=method, headers=headers)  # noqa: S310 - host from config
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read())
        except Exception:  # noqa: BLE001 - non-JSON 4xx (WAF/HTML) -> generic
            raise BinanceApiError(int(exc.code), exc.reason or "http error") from None
        if isinstance(body, dict) and "code" in body:
            raise BinanceApiError(int(body["code"]), str(body.get("msg", ""))) from None
        raise BinanceApiError(int(exc.code), str(body)) from None
    except urllib.error.URLError as exc:
        raise BinanceApiError(0, f"network error: {exc.reason}") from None


def get_public_json(base_url: str, path: str, params: dict | None = None) -> dict:
    """UNSIGNED GET (exchangeInfo / time) via the shared rest.get_json helper."""
    from urllib.parse import urlencode

    url = f"{base_url}{path}"
    if params:
        url = f"{url}?{urlencode(params)}"
    return get_json(url)

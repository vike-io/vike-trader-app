"""Signed urllib transport for Bybit V5 spot endpoints.

GET appends the signed query to the URL; POST sends the EXACT signed JSON body bytes (from the
signer) with Content-Type: application/json — never re-serialized, or X-BAPI-SIGN won't match.
Bybit returns HTTP 200 for both success and business errors; the {retCode,retMsg,result} envelope
is unwrapped by the client's unwrap() (which raises BybitApiError on retCode!=0), NOT here.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from vike_trader_app.exec.crypto_client import VenueApiError


class BybitApiError(VenueApiError):
    """A Bybit {retCode, retMsg} error (raised by the client's unwrap(), not the transport)."""

    def __init__(self, code: int, msg: str) -> None:
        super().__init__(code, msg)


def bybit_signed_request(base_url: str, path: str, method: str, params: dict, signer,
                         *, urlopen=urllib.request.urlopen, timeout: int = 30) -> dict:
    """Sign + send; return the parsed 200 body verbatim (envelope unwrapping is the client's job)."""
    prepared = signer.prepare(params, method=method, path=path)
    if method.upper() == "GET":
        url = f"{base_url}{path}?{prepared.query}" if prepared.query else f"{base_url}{path}"
        req = urllib.request.Request(url, method="GET", headers=prepared.headers)  # noqa: S310
    else:
        headers = {**prepared.headers, "Content-Type": "application/json"}
        req = urllib.request.Request(f"{base_url}{path}", data=prepared.body, method="POST",
                                     headers=headers)  # noqa: S310 - host from config
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read())
        except Exception:  # noqa: BLE001 - non-JSON 4xx (WAF/HTML) -> generic
            raise BybitApiError(int(exc.code), exc.reason or "http error") from None
        if isinstance(body, dict) and "retCode" in body:
            raise BybitApiError(int(body["retCode"]), str(body.get("retMsg", ""))) from None
        raise BybitApiError(int(exc.code), str(body)) from None
    except urllib.error.URLError as exc:
        raise BybitApiError(0, f"network error: {exc.reason}") from None

"""Signed urllib transport for OKX V5 spot endpoints.

Every request (signed and public) carries a full browser User-Agent block to avoid Cloudflare's
error-1010 / 403 response.  x-simulated-trading: 1 is added when simulated=True (demo account).

GET appends the signed query verbatim from the signer's prepared.query (sign-then-send, never
re-encoded).  POST sends the EXACT signed JSON body bytes from the signer with Content-Type:
application/json — never re-serialized, or OK-ACCESS-SIGN won't match.

OKX returns HTTP 200 for both success and business errors; the {code,msg,data} envelope is
returned verbatim here and unwrapped by the client's unwrap() (which raises OKXApiError on
code!="0"), NOT here.  HTTP-level errors (e.g. Cloudflare 1010 / 403) become OKXApiError.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from vike_trader_app.exec.crypto_client import VenueApiError

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


class OKXApiError(VenueApiError):
    """An OKX {code, msg} error (raised by the client's unwrap() or the transport on HTTP error)."""

    def __init__(self, code: int, msg: str) -> None:
        super().__init__(code, msg)


def okx_signed_request(
    base_url: str,
    path: str,
    method: str,
    params: dict,
    signer,
    *,
    simulated: bool = True,
    urlopen=urllib.request.urlopen,
    timeout: int = 30,
) -> dict:
    """Sign + send; return the parsed 200 body verbatim (envelope unwrapping is the client's job)."""
    prepared = signer.prepare(params, method=method, path=path)
    headers: dict[str, str] = {**_BROWSER_HEADERS, **prepared.headers}
    if simulated:
        headers["x-simulated-trading"] = "1"

    if method.upper() == "GET":
        url = f"{base_url}{path}?{prepared.query}" if prepared.query else f"{base_url}{path}"
        req = urllib.request.Request(url, method="GET", headers=headers)  # noqa: S310
    else:
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(  # noqa: S310 - host from config
            f"{base_url}{path}",
            data=prepared.body,
            method="POST",
            headers=headers,
        )

    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read())
        except Exception:  # noqa: BLE001 - non-JSON 4xx (WAF/HTML) -> generic
            raise OKXApiError(int(exc.code), exc.reason or "http error") from None
        if isinstance(body, dict) and "code" in body:
            raise OKXApiError(int(body["code"]), str(body.get("msg", ""))) from None
        raise OKXApiError(int(exc.code), str(body)) from None
    except urllib.error.URLError as exc:
        raise OKXApiError(0, f"network error: {exc.reason}") from None


def okx_public_get(
    base_url: str,
    path: str,
    params: dict | None = None,
    *,
    simulated: bool = True,
    urlopen=urllib.request.urlopen,
    timeout: int = 30,
) -> dict:
    """Unsigned public GET; still carries browser UA and x-simulated-trading when simulated."""
    headers: dict[str, str] = {**_BROWSER_HEADERS}
    if simulated:
        headers["x-simulated-trading"] = "1"

    url = (
        f"{base_url}{path}?{urllib.parse.urlencode(params)}"
        if params
        else f"{base_url}{path}"
    )
    req = urllib.request.Request(url, method="GET", headers=headers)  # noqa: S310

    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read())
        except Exception:  # noqa: BLE001
            raise OKXApiError(int(exc.code), exc.reason or "http error") from None
        if isinstance(body, dict) and "code" in body:
            raise OKXApiError(int(body["code"]), str(body.get("msg", ""))) from None
        raise OKXApiError(int(exc.code), str(body)) from None
    except urllib.error.URLError as exc:
        raise OKXApiError(0, f"network error: {exc.reason}") from None

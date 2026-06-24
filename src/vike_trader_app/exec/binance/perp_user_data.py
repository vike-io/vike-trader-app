"""Binance USDS-M futures (fapi) listenKey user-data WS + make_binance_perp_run_core.

CONFIRMED LIVE: POST /fapi/v1/listenKey on demo-fapi.binance.com serves a 64-char key (spot
listenKey is 410-dead). The perp WS path is the CLASSIC listenKey: create -> connect raw
wss://<ws_base>/<listenKey> -> stream ORDER_TRADE_UPDATE (NO subscribe frame, NO auth handshake —
the listenKey in the URL IS the auth, unlike the spot WS-API subscribe.signature). A PUT keepalive
fires on a ~30-min cadence (60-min server expiry) via the run_user_data_forever ping hook.

listenKey endpoints are apiKey-HEADER-only (X-MBX-APIKEY) — NOT query-HMAC-signed: the signer is
BYPASSED here. The keepalive PUT is best-effort (log-only) with a BOUNDED timeout (timeout=2,
well under PrivateUserDataWorker.wait(2000)) so it never blocks teardown. It is also OFFLOADED via
asyncio.to_thread so the event loop can poll stop() during the blocking HTTP call — unlike every
other venue ping (_okx_ping/_bybit_ping) which are non-blocking WS sends, this is the FIRST
blocking-sync HTTP ping in exec/, hence the to_thread is mandatory.

On teardown the listenKey is NOT DELETEd (it expires server-side in 60 min). A best-effort DELETE
could be added as a no-op-safe enhancement but is omitted here to avoid adding a blocking call to
the teardown path (which is already constrained to wait(2000)=2s).

The spot binance/user_data.py is NOT edited (byte-identical).
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.request

from vike_trader_app.exec.binance.perp_mapper import map_binance_perp
from vike_trader_app.exec.user_data_core import run_user_data_forever

_LISTENKEY_PATH = "/fapi/v1/listenKey"
_KEEPALIVE_TIMEOUT = 2   # seconds — bounded well under PrivateUserDataWorker.wait(2000)
_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal: bare apiKey-header-only request builder (NOT signed)
# ---------------------------------------------------------------------------

def _listenkey_request(method: str, *, fapi_rest_url: str, api_key: str,
                       urlopen=urllib.request.urlopen, timeout: int = 10) -> dict:
    """Issue an apiKey-header-only listenKey request (NOT HMAC-signed). Returns the parsed JSON body.

    The BinanceHmacSigner MUST NOT be used here: listenKey endpoints are header-only (X-MBX-APIKEY),
    no timestamp/recvWindow/signature in the query string.
    """
    url = f"{fapi_rest_url}{_LISTENKEY_PATH}"
    req = urllib.request.Request(  # noqa: S310 — host from config
        url, method=method, headers={"X-MBX-APIKEY": api_key})
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else {}


# ---------------------------------------------------------------------------
# Public: create + keepalive
# ---------------------------------------------------------------------------

def listenkey_create(*, fapi_rest_url: str, api_key: str,
                     urlopen=urllib.request.urlopen, timeout: int = 10) -> str:
    """POST /fapi/v1/listenKey — apiKey-header-only. Returns the 64-char listenKey string."""
    body = _listenkey_request("POST", fapi_rest_url=fapi_rest_url, api_key=api_key,
                              urlopen=urlopen, timeout=timeout)
    return str(body.get("listenKey", ""))


def _listenkey_keepalive_sync(*, fapi_rest_url: str, api_key: str,
                               timeout: int = _KEEPALIVE_TIMEOUT,
                               urlopen=urllib.request.urlopen) -> None:
    """Synchronous inner body of the keepalive PUT (exposed at module level for test patching).

    This is the blocking urllib call that MUST be run via asyncio.to_thread when invoked from the
    async ping hook so it does not stall the event loop's stop()-poll during teardown.
    """
    _listenkey_request("PUT", fapi_rest_url=fapi_rest_url, api_key=api_key,
                       urlopen=urlopen, timeout=timeout)


def listenkey_keepalive(*, fapi_rest_url: str, api_key: str,
                        urlopen=urllib.request.urlopen, timeout: int = _KEEPALIVE_TIMEOUT) -> None:
    """Best-effort PUT /fapi/v1/listenKey — swallow ANY error (a transient failure self-heals on
    the next reconnect, which mints a fresh key). NEVER let it bubble into the ping hook (a raised
    ping would trigger run_user_data_forever's reconnect-with-backoff).

    timeout defaults to _KEEPALIVE_TIMEOUT (2s), NOT urllib's 10s default, so a stalled PUT cannot
    outlast PrivateUserDataWorker.wait(2000)=2s during teardown.
    """
    try:
        _listenkey_keepalive_sync(
            fapi_rest_url=fapi_rest_url, api_key=api_key, timeout=timeout, urlopen=urlopen)
    except Exception:  # noqa: BLE001 — keepalive is best-effort; log without creds
        _log.warning("binance perp listenKey keepalive failed (will self-heal on reconnect)")


# ---------------------------------------------------------------------------
# Async offload helper — module-level for test patching
# ---------------------------------------------------------------------------

async def _offload_keepalive(*, fapi_rest_url: str, api_key: str,
                              timeout: int = _KEEPALIVE_TIMEOUT) -> None:
    """Offload the blocking PUT to a thread so the event loop remains free to poll stop().

    asyncio.to_thread is used here (first use in exec/) because _listenkey_keepalive_sync is a
    BLOCKING urllib call, unlike all other venue pings (_okx_ping/_bybit_ping) which are non-blocking
    WS sends. Without to_thread the event loop would be blocked for up to `timeout` seconds, which
    exceeds PrivateUserDataWorker.wait(2000)=2s and causes 0xC0000409 teardown hangs.
    """
    try:
        await asyncio.to_thread(
            _listenkey_keepalive_sync,
            fapi_rest_url=fapi_rest_url,
            api_key=api_key,
            timeout=timeout,
        )
    except Exception:  # noqa: BLE001 — best-effort; to_thread re-raises sync exceptions
        _log.warning("binance perp listenKey keepalive offload failed (will self-heal on reconnect)")


# ---------------------------------------------------------------------------
# open_binance_perp_user_data_ws
# ---------------------------------------------------------------------------

async def open_binance_perp_user_data_ws(*, fapi_rest_url: str, ws_base_url: str, api_key: str,
                                         connect=None, open_timeout: int = 10,
                                         urlopen=urllib.request.urlopen):
    """Create the listenKey (apiKey-header POST) then connect wss://<ws_base>/<listenKey>.

    NO subscribe frame, NO ack loop — the user-data stream auto-pushes once connected. The
    listenKey in the URL IS the auth (unlike the spot WS-API which requires a signed subscribe
    frame and status==200 ack). DO NOT copy _await_subscribe_ack here: the fapi stream never
    sends an ack and it would hang forever.

    ``connect`` is an async callable (url) -> ws injected for offline testing; when None,
    lazy-imports websockets and calls websockets.connect(url, open_timeout=10) (bare —
    fapi demo is not Cloudflare-gated, so NO browser UA, unlike OKX).
    ``urlopen`` is injectable for offline testing of the listenKey create step.
    """
    listen_key = listenkey_create(fapi_rest_url=fapi_rest_url, api_key=api_key, urlopen=urlopen)
    url = f"{ws_base_url}/{listen_key}"
    if connect is None:
        import websockets  # noqa: PLC0415 — lazy so websockets is optional at import time
        return await websockets.connect(url, open_timeout=open_timeout)
    return await connect(url)


# ---------------------------------------------------------------------------
# make_binance_perp_run_core
# ---------------------------------------------------------------------------

def make_binance_perp_run_core(*, fapi_rest_url: str, ws_base_url: str, api_key: str,
                                symbol: str, now_ms, connect=None):
    """Return run_core(emit, stop) driving the fapi listenKey fill stream.

    ping = the listenKey PUT keepalive offloaded via asyncio.to_thread (ignores the ws arg — it
    is an HTTP PUT, not a WS frame); ping_ms = 30 min (under the 60-min server expiry).

    api_secret is NOT needed (listenKey endpoints are apiKey-header-only) — only api_key flows
    in here, narrowing the secret surface vs. the spot make_binance_run_core.

    Safety: the keepalive is OFFLOADED (asyncio.to_thread) + BOUNDED (timeout=2s) + BEST-EFFORT
    (swallows all exceptions), so it cannot block the event loop or outlast teardown wait(2000).
    """

    async def _keepalive(ws):  # noqa: ARG001 — ws unused; keepalive is an HTTP PUT not a WS frame
        await _offload_keepalive(fapi_rest_url=fapi_rest_url, api_key=api_key)

    def run_core(emit, stop):
        asyncio.run(
            run_user_data_forever(
                emit=emit,
                open_ws=lambda: open_binance_perp_user_data_ws(
                    fapi_rest_url=fapi_rest_url, ws_base_url=ws_base_url,
                    api_key=api_key, connect=connect),
                decode=lambda frame: map_binance_perp(frame, venue="binance", symbol=symbol),
                ping=_keepalive,
                ping_ms=1_800_000,        # ~30 min, under the 60-min listenKey expiry
                stop=stop,
                recv_timeout=1.0,
                now_ms=now_ms,
            )
        )

    return run_core

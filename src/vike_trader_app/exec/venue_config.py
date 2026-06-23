"""Testnet ladder: ONE adapter, swap base-URL + creds + signer by (venue, Environment).

REST and WS base URLs are SEPARATE, env-overridable knobs — the demo REST host is verified
(demo-api.binance.com) but the matching demo user-data WS host must be confirmed empirically
(Task 15) and is overridable via BINANCE_DEMO_WS_URL. resolve_venue_config returns None when
creds are absent (the live gate).

NOTE — Binance demo has NO listenKey user-data stream (POST /api/v3/userDataStream -> 410 Gone,
deprecated). The Phase-3b fill-stream follow-up must use Binance's WebSocket-API session-based
user-data path; do NOT default the demo WS to the mainnet host. BINANCE_DEMO_WS_DEFAULT is
intentionally empty ("") so callers that need a WS URL must supply BINANCE_DEMO_WS_URL explicitly
and a missing override is surfaced as an empty string rather than silently hitting the mainnet.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from vike_trader_app.exec.credentials import Credentials, Environment, load_credentials
from vike_trader_app.exec.signer import BinanceHmacSigner, BybitV5Signer, OKXV5Signer

BINANCE_DEMO_REST = "https://demo-api.binance.com"
BINANCE_MAINNET_REST = "https://api.binance.com"
# Demo has NO listenKey endpoint (410 Gone) — leave empty; consumers must use BINANCE_DEMO_WS_URL.
BINANCE_DEMO_WS_DEFAULT = ""
BINANCE_MAINNET_WS_DEFAULT = "wss://stream.binance.com:9443/ws"

BYBIT_DEMO_REST = "https://api-demo.bybit.com"
BYBIT_MAINNET_REST = "https://api.bybit.com"

# OKX: demo and mainnet share the same REST host; the x-simulated-trading:1 header (a transport
# concern) is the only distinction between demo and mainnet at the HTTP level.
OKX_REST = "https://www.okx.com"


@dataclass(frozen=True)
class VenueConfig:
    venue: str
    environment: Environment
    rest_base_url: str
    ws_base_url: str
    credentials: Credentials
    signer: object


def _resolve_binance(venue: str, env: Environment, creds: Credentials,
                     now_ms: Callable[[], int]) -> VenueConfig:
    if env is Environment.MAINNET:
        rest = os.environ.get("BINANCE_MAINNET_BASE_URL") or BINANCE_MAINNET_REST
        ws = os.environ.get("BINANCE_MAINNET_WS_URL") or BINANCE_MAINNET_WS_DEFAULT
    else:
        rest = os.environ.get("BINANCE_DEMO_BASE_URL") or BINANCE_DEMO_REST
        ws = os.environ.get("BINANCE_DEMO_WS_URL") or BINANCE_DEMO_WS_DEFAULT
    signer = BinanceHmacSigner(creds, now_ms=now_ms)
    return VenueConfig(venue=venue, environment=env, rest_base_url=rest, ws_base_url=ws,
                       credentials=creds, signer=signer)


def _resolve_bybit(venue: str, env: Environment, creds: Credentials,
                   now_ms: Callable[[], int]) -> VenueConfig:
    if env is Environment.MAINNET:
        rest = os.environ.get("BYBIT_MAINNET_BASE_URL") or BYBIT_MAINNET_REST
    else:
        rest = os.environ.get("BYBIT_DEMO_BASE_URL") or BYBIT_DEMO_REST
    signer = BybitV5Signer(creds, now_ms=now_ms)
    # WS user-data is DEFERRED to the cross-venue fill-stream PR — empty ws_base_url for now.
    return VenueConfig(venue=venue, environment=env, rest_base_url=rest, ws_base_url="",
                       credentials=creds, signer=signer)


def _resolve_okx(venue: str, env: Environment, creds: Credentials,
                 now_ms: Callable[[], int]) -> VenueConfig:
    # Demo and mainnet share the same REST host; env override still supported for test injection.
    if env is Environment.MAINNET:
        rest = os.environ.get("OKX_MAINNET_BASE_URL") or OKX_REST
    else:
        rest = os.environ.get("OKX_DEMO_BASE_URL") or OKX_REST
    signer = OKXV5Signer(creds, now_ms=now_ms)
    # WS user-data fill stream is DEFERRED — empty ws_base_url for now.
    return VenueConfig(venue=venue, environment=env, rest_base_url=rest, ws_base_url="",
                       credentials=creds, signer=signer)


_VENUE_BUILDERS = {"binance": _resolve_binance, "bybit": _resolve_bybit, "okx": _resolve_okx}


def resolve_venue_config(venue: str, env: Environment, *, now_ms: Callable[[], int],
                         load: Callable[[str, Environment], Credentials | None] = load_credentials
                         ) -> VenueConfig | None:
    """Build a VenueConfig, or None when creds are absent (stay paper)."""
    creds = load(venue, env)
    if creds is None:
        return None
    builder = _VENUE_BUILDERS.get(venue.lower())
    if builder is None:
        return None
    return builder(venue, env, creds, now_ms)
